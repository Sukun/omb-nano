"""
Synthetic validation for wave_analyzer_enhance.py, against KNOWN ground truth
(unlike real OMB Zenodo data, which has none -- see wave_analyzer_enhance.py's
load_omb_netcdf docstring). Also round-trips a synthetic WAVES.CSV through
load_waves_csv to confirm the dequantization/parsing matches the source
spectrum exactly.

BIN_HI here is P.WELCH_BIN_MAX_EXT (103) -- the band the firmware now uses
after the problem-8 fix (welch_bin_max 64->103 in params.h), NOT the old
P.WELCH_BIN_MAX (64), which stays fixed at 64 so the historical problem-8
comparison experiments (run_experiments.py) remain reproducible.
"""

from pathlib import Path

import numpy as np

import params as P
import signal_gen as SG
import wave_welch_baseline as WB
import wave_analyzer_enhance as E

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "csv"
BIN_HI = P.WELCH_BIN_MAX_EXT  # 103, matches the firmware post problem-8 fix


def _accel_core(accel_padded):
    return accel_padded[P.PAD_SAMPLES: P.PAD_SAMPLES + P.CORE_LENGTH]


def quantize_spectrum(s_acc):
    """Mirrors the firmware's uint16 quantization exactly (wave_analyzer.cpp
    perform_welch_analysis_imu_data, the array_pwelch fill loop)."""
    max_value = float(np.max(s_acc))
    if max_value <= 0:
        return np.zeros(len(s_acc), dtype=np.uint16), 0.0
    pw = np.floor(s_acc / max_value * 65000.0).astype(np.uint16)
    return pw, max_value


def _record_from_accel(accel_padded, seed_label):
    accel_core = _accel_core(accel_padded)
    res = WB.baseline_analyze(accel_core, bin_hi=BIN_HI)
    pw, max_value = quantize_spectrum(res["S_acc"])
    s_acc_dequant = pw.astype(float) * max_value / 65000.0
    record = dict(posix_timestamp=1700000000 + seed_label, spectrum_number=seed_label,
                  Hs_onboard=res["Hs"], Tz_onboard=res["Tz"], Tc_onboard=res["Tc"],
                  Tp_onboard=res["Tp"], S_acc=s_acc_dequant,
                  bin_lo=P.WELCH_BIN_MIN, bin_hi=BIN_HI)
    return record, res


def make_waves_csv_row(accel_padded, seed_label=0):
    record, res = _record_from_accel(accel_padded, seed_label)
    pw, max_value = quantize_spectrum(res["S_acc"])
    return [record["posix_timestamp"], record["spectrum_number"], res["Hs"],
            res["Tz"], res["Tc"], res["Tp"], max_value] + list(pw)


def write_waves_csv(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def test_clean_sea_state(n_real=10):
    print("=" * 70)
    print("TEST 1: clean sea state -> onshore Hs matches truth, no drift/long-swell flags")
    print("=" * 70)
    hs_target, tp_target = 2.0, 8.0
    errs = []
    false_positives = 0
    for seed in range(n_real):
        r = SG.make_realization(hs_target, tp_target, seed)
        truth = SG.truth_bulk_params(r.amplitudes, P.WELCH_BIN_MIN, BIN_HI)
        record, _ = _record_from_accel(r.accel_raw, seed)
        out = E.analyze_record(record)
        errs.append(abs(out["Hs_onshore"] - truth["Hs"]) / truth["Hs"])
        false_positives += int(out["drift_detected"])
        if seed == 0:
            print(f"  seed0: truth Hs={truth['Hs']:.4f} onshore Hs={out['Hs_onshore']:.4f} "
                  f"drift_detected={out['drift_detected']} n_excluded={out['n_bins_excluded']} "
                  f"flags={out['qc_flags']}")
    mean_err = 100 * np.mean(errs)
    print(f"  mean |Hs relative error| over {n_real} seeds: {mean_err:.3f}%")
    print(f"  false-positive drift detections: {false_positives}/{n_real}")
    assert mean_err < 3.0, "clean case should reproduce truth closely"
    assert false_positives == 0, "clean data must not trigger drift_detected"
    print("  PASS")


def test_drift_detection_and_recovery(n_real=10):
    print()
    print("=" * 70)
    print("TEST 2: worst-case drift (0.0439Hz, 0.15 m/s^2) -> detected + Hs recovered")
    print("=" * 70)
    hs_target, tp_target = 2.0, 8.0
    drift_freq, drift_amp = 0.0439, 0.15

    baseline_hs, onshore_hs, detected_count, excluded_counts = [], [], 0, []
    truth_hs = None
    for seed in range(n_real):
        r = SG.make_realization(hs_target, tp_target, seed)
        truth = SG.truth_bulk_params(r.amplitudes, P.WELCH_BIN_MIN, BIN_HI)
        truth_hs = truth["Hs"]
        accel_p = r.accel_raw + drift_amp * np.sin(2 * np.pi * drift_freq * r.t)
        record, res = _record_from_accel(accel_p, seed)
        out = E.analyze_record(record)
        baseline_hs.append(res["Hs"])
        onshore_hs.append(out["Hs_onshore"])
        detected_count += int(out["drift_detected"])
        excluded_counts.append(out["n_bins_excluded"])

    print(f"  truth Hs = {truth_hs:.4f}")
    print(f"  baseline (onboard, contaminated) Hs mean = {np.mean(baseline_hs):.4f}  "
          f"(+{100 * (np.mean(baseline_hs) - truth_hs) / truth_hs:.1f}%)")
    print(f"  onshore (recovered) Hs mean         = {np.mean(onshore_hs):.4f}  "
          f"({100 * (np.mean(onshore_hs) - truth_hs) / truth_hs:+.1f}%)")
    print(f"  drift detected in {detected_count}/{n_real} realisations, "
          f"mean bins excluded = {np.mean(excluded_counts):.1f}")

    mat, _, _ = P.hs_is_material(truth_hs, float(np.mean(onshore_hs)))
    assert detected_count == n_real, "drift should be detected in every realisation"
    assert not mat, "recovered Hs should be within materiality threshold of truth"
    print("  PASS")


def test_long_swell_truncation_flag(n_real=5):
    print()
    print("=" * 70)
    print("TEST 3: super-long swell (Tp=30s, out of band) -> long-swell truncation flagged")
    print("=" * 70)
    hs_target, tp_target = 2.0, 30.0
    flagged = 0
    for seed in range(n_real):
        r = SG.make_realization(hs_target, tp_target, seed)
        record, res = _record_from_accel(r.accel_raw, seed)
        out = E.analyze_record(record)
        is_flagged = "possible_long_swell_truncation" in out["qc_flags"]
        flagged += int(is_flagged)
        if seed == 0:
            print(f"  seed0: onboard Hs={res['Hs']:.4f} flags={out['qc_flags']}")
    print(f"  flagged in {flagged}/{n_real} realisations")
    assert flagged == n_real, "super-long swell should be flagged in every realisation"
    print("  PASS")


def test_waves_csv_roundtrip():
    print()
    print("=" * 70)
    print("TEST 4: WAVES.CSV format round-trip (dequantization matches source)")
    print("=" * 70)
    r = SG.make_realization(2.0, 8.0, seed=123)
    accel_core = _accel_core(r.accel_raw)
    res = WB.baseline_analyze(accel_core, bin_hi=BIN_HI)
    row = make_waves_csv_row(r.accel_raw, seed_label=42)
    path = OUT_DIR / "sample_waves.csv"
    write_waves_csv(path, [row])

    records = E.load_waves_csv(str(path))
    assert len(records) == 1
    rec = records[0]
    assert rec["bin_lo"] == P.WELCH_BIN_MIN
    n_bins_parsed = rec["bin_hi"] - rec["bin_lo"]
    n_bins_expected = BIN_HI - P.WELCH_BIN_MIN
    assert rec["bin_hi"] == BIN_HI, (
        f"expected bin_hi={BIN_HI} ({n_bins_expected} bins), got {rec['bin_hi']} "
        f"({n_bins_parsed} bins) -- loader should read column count dynamically"
    )

    pw, max_value = quantize_spectrum(res["S_acc"])
    s_acc_expected = pw.astype(float) * max_value / 65000.0
    max_rel_err = np.max(np.abs(rec["S_acc"] - s_acc_expected) / (np.max(s_acc_expected) + 1e-30))
    print(f"  n_bins parsed = {n_bins_parsed} (expected {n_bins_expected})")
    print(f"  max relative dequantization error vs source spectrum: {max_rel_err * 100:.6f}%")
    assert max_rel_err < 1e-6, "dequantized spectrum should exactly match the source"
    print(f"  written sample file: {path}")
    print("  PASS")


if __name__ == "__main__":
    test_clean_sea_state()
    test_drift_detection_and_recovery()
    test_long_swell_truncation_flag()
    test_waves_csv_roundtrip()
    print()
    print("=" * 70)
    print("ALL VALIDATION TESTS PASSED")
    print("=" * 70)
