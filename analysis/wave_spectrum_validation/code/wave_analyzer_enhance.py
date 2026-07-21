"""
Onshore post-processing enhancement for omb-nano wave spectra (problems 2 & 4).

wave_analyzer.cpp on the buoy is algorithm/power constrained: it computes Hs/Tz/
Tc/Tp once, on the full (possibly drift-corrupted) retained band, and transmits
them alongside the complete per-bin acceleration spectrum (`array_pwelch` +
`max_value` in WAVES.CSV / the Iridium Wave_Packet). Because the FULL spectrum
is available, an onshore tool with no power/RAM constraint can do strictly
better:

  - Problem 2 (low-frequency drift, see docs/2.问题与结论.md §4.1/4.1.5):
    drift corrupts only the lowest few retained bins and is otherwise a smooth,
    reconstructible spectrum. This module detects that corruption pattern and
    adaptively re-derives Hs/Tz/Tc/Tp from the clean remainder of the spectrum.

  - Problem 4 (QC blind spots, §4.3): the two failure modes identified there
    (severely out-of-band long swell; sensor saturation/clipping) are handled
    here as ADVISORY FLAGS, not corrections:
      * long-swell truncation IS detectable from the transmitted spectrum
        shape (the true peak sits at/below the retained band's low edge) --
        flagged, not corrected (energy below the band is simply not there).
      * saturation/clipping is NOT reliably detectable from the spectrum alone
        (its diagnostic signature is in the raw time series, which is never
        transmitted) -- documented as a known limitation, not flagged, because
        it also requires a hardware fault (the ISM330DHCX's +-2g range is
        ~65x the vertical acceleration of a typical sea state) and is
        therefore low-probability.

Firmware constants (params.py) already reflect the current retained band
(problems 3 despiking + 8 band-to-2s are firmware changes, not part of this
module's job -- this module reads whatever band the firmware currently uses).
"""

from pathlib import Path

import numpy as np
import pandas as pd

import params as P
import wave_welch_baseline as WB
import wave_welch_variants as WV

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "csv"

# WAVES.CSV has NO header row (sd_manager.cpp::log_latest_wave_packet):
#   posix_timestamp, spectrum_number, Hs, Tz, Tc, Tp, max_value, <spectrum columns>
FIXED_COLS = ["posix_timestamp", "spectrum_number", "Hs_onboard", "Tz_onboard",
              "Tc_onboard", "Tp_onboard", "max_value"]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_waves_csv(path, spectrum_kind="accel"):
    """
    Parse an omb-nano WAVES.CSV into a list of records, one per transmitted/
    logged Wave_Packet.

    spectrum_kind: "accel" (default, matches the firmware -- array_pwelch is
        the ACCELERATION power spectrum, see docs/1.算法说明.md) or
        "elevation" (for a future source that already provides a displacement
        spectrum, e.g. a re-derived OMB product -- NOT implemented, raises).

    The number of spectrum columns is read from the file itself (NOT
    hardcoded to 55 or 94), so this loader works unchanged whether the
    firmware used the pre- or post-problem-8 band width.

    Returns: list of dicts, each with:
        posix_timestamp, spectrum_number,
        Hs_onboard, Tz_onboard, Tc_onboard, Tp_onboard  (firmware's own values,
            kept only for reference/comparison -- NOT the authoritative output
            of this tool),
        S_acc (np.ndarray, dequantized acceleration PSD, one entry per
            retained bin, index i corresponds to bin = bin_lo + i),
        bin_lo, bin_hi (int, [bin_lo, bin_hi) is the retained band actually
            present in this file -- derived from the column count, so this
            adapts automatically to the firmware's current welch_bin_max).
    """
    if spectrum_kind != "accel":
        raise NotImplementedError(
            "spectrum_kind='elevation' is not implemented -- WAVES.CSV always "
            "carries the acceleration spectrum in this firmware. Add a branch "
            "here if a future source provides displacement spectra directly."
        )

    df = pd.read_csv(path, header=None)
    n_spectrum_cols = df.shape[1] - len(FIXED_COLS)
    if n_spectrum_cols <= 0:
        raise ValueError(
            f"{path}: expected at least {len(FIXED_COLS)+1} columns "
            f"(7 fixed + >=1 spectrum bin), found {df.shape[1]}"
        )
    df.columns = FIXED_COLS + [f"pw{i}" for i in range(n_spectrum_cols)]

    bin_lo = P.WELCH_BIN_MIN
    bin_hi = bin_lo + n_spectrum_cols

    records = []
    pw_cols = [f"pw{i}" for i in range(n_spectrum_cols)]
    for _, row in df.iterrows():
        pw = row[pw_cols].to_numpy(dtype=float)
        max_value = row["max_value"]
        s_acc = pw * max_value / 65000.0 if max_value > 0 else pw * 0.0
        records.append(dict(
            posix_timestamp=int(row["posix_timestamp"]),
            spectrum_number=int(row["spectrum_number"]),
            Hs_onboard=float(row["Hs_onboard"]),
            Tz_onboard=float(row["Tz_onboard"]),
            Tc_onboard=float(row["Tc_onboard"]),
            Tp_onboard=float(row["Tp_onboard"]),
            S_acc=s_acc,
            bin_lo=bin_lo,
            bin_hi=bin_hi,
        ))
    return records


def load_omb_netcdf(path):
    """
    STUB -- intended design for a future Phase 2.

    Real OpenMetBuoy deployment data is published (not raw buoy telemetry) on
    Zenodo, e.g. SvalMIZ-24 (34 buoys, 2024, https://zenodo.org/records/15312810,
    file 2024_KVS_deployment_flagged.nc) and SvalMIZ-25
    (https://zenodo.org/records/17087019). This is post-processed scientific
    data, NOT the raw per-bin acceleration spectrum this module expects --
    before using it here, inspect the netCDF variables (xarray.open_dataset)
    to confirm whether it exposes a per-frequency spectrum at all, and if so
    whether it is elevation or acceleration, and its frequency grid (likely
    does not line up with omb-nano's fixed Δf=10/2048 Hz bins, requiring
    interpolation onto this module's bin grid before reuse of
    detect_and_exclude_drift / qc_flags below).

    Also: this published data has NO analytic ground truth (unlike the
    synthetic validation in wave_analyzer_enhance_validate.py) -- any
    comparison against it can only sanity-check flag plausibility, not verify
    numerical accuracy.

    Not implemented in this round (synthetic validation + WAVES.CSV only).
    """
    raise NotImplementedError(
        "OMB netCDF loader is a Phase-2 stub -- see docstring for the intended "
        "design and why it isn't a drop-in ground-truth source. Not executed "
        "in this round."
    )


# ---------------------------------------------------------------------------
# Problem 2: adaptive drift detection + exclusion
# ---------------------------------------------------------------------------
def detect_and_exclude_drift(S_acc, bin_lo, max_check=6, lookahead=4, ratio_threshold=50.0):
    """
    Adaptively find how many of the lowest retained bins are drift-corrupted.

    Rationale (empirically confirmed in supp_drift_locality.py, and re-confirmed
    while tuning this detector): a low-frequency drift near the band edge dumps
    enormous energy into the acceleration spectrum, spread across the Hann
    window's ~4-bin-wide main lobe (so the drift's own bin and its immediate
    neighbour can be COMPARABLE in size -- an immediate-neighbour ratio check
    misses this and stops one step too early), then decays sharply through the
    sidelobes over the next couple of bins. A genuine sea state's spectrum at
    the low edge is smooth and monotonically approaches the real peak, so it is
    always much SMALLER than a reference bin a few steps further up -- while a
    drift-corrupted bin is orders of magnitude LARGER than that same reference.

    Algorithm: compare S_acc[k] against S_acc[k + lookahead] (a reference far
    enough past the main-lobe width to be unaffected by the drift, but not so
    far that it starts to reflect the real spectral peak too strongly at low
    Hs/Tp). If S_acc[k] / S_acc[k+lookahead] exceeds ratio_threshold, bin k is
    judged corrupted; keep scanning upward until the ratio falls below
    threshold. Using a lookahead rather than an immediate neighbour also avoids
    false positives from the near-machine-epsilon numerical noise floor in
    genuinely near-zero low bins (see wave_welch_variants.py's __main__ note on
    why bin-to-bin ratios there are meaningless): the lookahead reference is
    guaranteed to have grown by orders of magnitude past that floor in a clean
    spectrum, so the ratio stays reliably tiny.

    Returns: (keep_from_bin, n_excluded, drift_detected)
    """
    keep_from = bin_lo
    n_excluded = 0
    for _ in range(max_check):
        idx = keep_from - bin_lo
        ref_idx = idx + lookahead
        if ref_idx >= len(S_acc):
            break
        numer, denom = S_acc[idx], S_acc[ref_idx]
        if denom > 0:
            ratio = numer / denom
        elif numer > 0:
            # reference underflowed to zero (quantization) but the candidate bin
            # is genuinely non-zero -- that IS the contamination signature.
            ratio = np.inf
        else:
            # both bins are at/below quantization resolution: a clean sea state
            # legitimately has negligible energy there too, so 0-vs-0 is not
            # evidence of drift (avoids false positives on clean quantized data).
            ratio = 0.0
        if ratio > ratio_threshold:
            keep_from += 1
            n_excluded += 1
        else:
            break
    return keep_from, n_excluded, (n_excluded > 0)


def recompute_bulk_params(S_acc, bin_lo, bin_hi, keep_from_bin):
    """
    Re-derive Hs/Tz/Tc/Tp from the acceleration spectrum restricted to
    [keep_from_bin, bin_hi) -- i.e. after excluding any drift-corrupted low
    bins found by detect_and_exclude_drift. Reuses the exact same
    accel->moments->bulk-params chain as the firmware/baseline
    (wave_welch_baseline.py), just over a narrower bin range.
    """
    start = keep_from_bin - bin_lo
    s_sub = S_acc[start:]
    m0, m2, m4 = WB.moments_from_accel_psd(s_sub, keep_from_bin, bin_hi)
    hs, tz, tc = WB.bulk_params_from_moments(m0, m2, m4)
    tp = WB.peak_period_from_psd(s_sub, keep_from_bin)
    return dict(Hs=hs, Tz=tz, Tc=tc, Tp=tp)


# ---------------------------------------------------------------------------
# Problem 4: QC flags (advisory only -- see module docstring)
# ---------------------------------------------------------------------------
def check_long_swell_truncation(S_acc, bin_lo, keep_from_bin, bin_hi,
                                 concentration_threshold=0.30):
    """
    Detects the 'severely out-of-band long swell' blind spot (§4.3): if the
    true spectral peak sits at or below the retained band's low edge, the
    ELEVATION spectrum (S_acc/omega^4) will still be rising (or sharply
    peaked) at the very first surviving bin, with a disproportionate share of
    the total retained energy concentrated there -- unlike a genuine in-band
    peak, which has real spectral width around it.

    This is a real, detectable signature (unlike saturation/clipping, which
    is not -- see module docstring), so it is safe to only flag, not correct:
    the energy below the band is genuinely not observable in this data.
    """
    start = keep_from_bin - bin_lo
    s_sub = S_acc[start:]
    bins = np.arange(keep_from_bin, bin_hi)
    f = bins * P.DF
    omega4 = (2.0 * np.pi * f) ** 4
    s_eta = s_sub / omega4

    total_m0 = np.sum(s_eta) * P.DF
    if total_m0 <= 0:
        return False
    first_bin_share = (s_eta[0] * P.DF) / total_m0
    peak_at_edge = int(np.argmax(s_eta)) == 0
    return bool(peak_at_edge and first_bin_share > concentration_threshold)


def qc_flags_for_record(hs, tp, S_acc, bin_lo, keep_from_bin, bin_hi):
    """Combines the existing physical-range checks (wave_welch_variants.qc_flags,
    tp_range already covers down to 2s) with the long-swell-truncation check."""
    flags = list(WV.qc_flags(hs, tp))
    if check_long_swell_truncation(S_acc, bin_lo, keep_from_bin, bin_hi):
        flags.append("possible_long_swell_truncation")
    return flags


# ---------------------------------------------------------------------------
# Per-record analysis + batch driver
# ---------------------------------------------------------------------------
def analyze_record(record):
    s_acc = record["S_acc"]
    bin_lo, bin_hi = record["bin_lo"], record["bin_hi"]

    keep_from_bin, n_excluded, drift_detected = detect_and_exclude_drift(s_acc, bin_lo)
    onshore = recompute_bulk_params(s_acc, bin_lo, bin_hi, keep_from_bin)
    flags = qc_flags_for_record(onshore["Hs"], onshore["Tp"], s_acc, bin_lo,
                                 keep_from_bin, bin_hi)

    return dict(
        posix_timestamp=record["posix_timestamp"],
        spectrum_number=record["spectrum_number"],
        Hs_onboard=record["Hs_onboard"], Tz_onboard=record["Tz_onboard"],
        Tc_onboard=record["Tc_onboard"], Tp_onboard=record["Tp_onboard"],
        Hs_onshore=onshore["Hs"], Tz_onshore=onshore["Tz"],
        Tc_onshore=onshore["Tc"], Tp_onshore=onshore["Tp"],
        n_bins_excluded=n_excluded, drift_detected=drift_detected,
        qc_flags=";".join(flags) if flags else "none",
    )


def analyze_waves_csv(path, out_csv=None):
    records = load_waves_csv(path)
    rows = [analyze_record(r) for r in records]
    df = pd.DataFrame(rows)
    if out_csv is not None:
        Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_csv, index=False)
    return df


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python3 wave_analyzer_enhance.py path/to/WAVES.CSV [out.csv]")
        raise SystemExit(1)
    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else str(OUT_DIR / "enhanced_waves.csv")
    result = analyze_waves_csv(in_path, out_csv=out_path)
    pd.set_option("display.width", 200)
    print(result.to_string(index=False))
    print(f"\nwritten: {out_path}")
