"""
T2 -- Python replica of WaveAnalyzer::perform_welch_analysis_imu_data()
      (omb-nano/src/wave_analyzer.cpp, post issue-1/6 fix).

This module provides a generic Welch engine (`welch_psd_core`) that is reused
verbatim by wave_welch_variants.py for the problem-2/3/5 variants -- only the
per-segment preprocessing step and/or the windowing function differ between
baseline and variants. This is deliberate (per /2-review-plan EF2): all
methods must share the exact same segmentation, FFT, and bin-averaging
machinery so that comparisons isolate ONLY the thing each problem is about.

Firmware correspondence (line references are to the fixed wave_analyzer.cpp):
  - segment loop, Hann window, FFT, Welch accumulation : perform_welch_analysis_imu_data(), ~114-199
  - CMSIS arm_rfft_fast_f32 is an UNNORMALISED forward DFT (same convention as
    numpy.fft.rfft), which is why the firmware applies the 1/N^2 PSD scaling
    manually afterwards -- see docs/1.算法说明.md 三、 4.2.
  - m0/m2/m4 + Hs/Tz/Tc : ~208-258 (Tz/Tc now sqrt(m0/m2), sqrt(m2/m4) after the fix)
  - Tp : new field added for issue 6, computed from the ACCELERATION spectrum
    peak (not the displacement spectrum peak) -- this is a documented,
    deliberate simplification (reuses the same argmax already computed for
    uint16 quantization), see docs/1.算法说明.md 三、 6.4.
"""

import numpy as np

import params as P


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------
def raw_hann_shape(n):
    """sin^2(pi*m/N), m=0..N-1 -- the UNSCALED window shape used by the firmware."""
    m = np.arange(n)
    return np.sin(np.pi * m / n) ** 2


def fixed_hann_window(x_seg):
    """Firmware's actual windowing: fixed empirical energy-correction factor 1.63."""
    n = len(x_seg)
    return x_seg * (P.HANN_ENERGY_SCALING_FACTOR * raw_hann_shape(n))


def adaptive_hann_window(x_seg):
    """Problem-5 'improved' variant: per-segment variance-preserving correction,
    factor = sqrt(var(x_seg) / var(x_seg * raw_window)), matching SWIFT/NIWA
    process.m (see docs/2.问题与结论.md 问题5)."""
    n = len(x_seg)
    raw_w = raw_hann_shape(n)
    tapered = x_seg * raw_w
    var_orig = np.var(x_seg)
    var_tapered = np.var(tapered)
    if var_tapered <= 0:
        factor = 1.0
    else:
        factor = np.sqrt(var_orig / var_tapered)
    return x_seg * (factor * raw_w)


# ---------------------------------------------------------------------------
# Generic Welch engine (shared by baseline and all variants)
# ---------------------------------------------------------------------------
def welch_psd_core(x_core, window_fn, bin_hi, segment_preprocess=None, n_segments=None):
    """
    Replicates the firmware's segment loop + FFT + Welch accumulation exactly.

    x_core : 1D array, the series to be segmented (length >= MIN_CORE_LENGTH_REQUIRED
             for the default n_segments=P.K; shorter is fine if n_segments is reduced
             accordingly -- see the problem-5 "short record" experiment).
             For the baseline / problem-3 / problem-5 variants this is raw
             acceleration (gravity still included; segment_preprocess removes it).
             For the problem-2 SWIFT-reference variant this is an
             already-converted displacement series (segment_preprocess=None).
    window_fn : callable(x_seg) -> windowed x_seg (fixed_hann_window or
             adaptive_hann_window or a custom callable for other experiments).
    bin_hi : upper bin index (exclusive) to accumulate up to. Always accumulate
             from WELCH_BIN_MIN up to bin_hi so a single call can serve both the
             current-band (bin_hi=WELCH_BIN_MAX) and extended-band
             (bin_hi=WELCH_BIN_MAX_EXT) analyses from ONE spectral estimate
             (problem 8 needs both from a shared underlying computation).
    segment_preprocess : callable(x_seg_raw) -> x_seg, applied BEFORE windowing.
             None means identity (used for the displacement-based problem-2
             reference variant, which has nothing left to subtract).
    n_segments : override for the number of Welch segments (default P.K=21,
             matching the firmware exactly). Only used by the problem-5
             "short record" experiment to emulate a shorter acquisition
             window with fewer averaged segments; NOT part of the firmware
             itself.

    Returns: S[k] for k in [WELCH_BIN_MIN, bin_hi), i.e. array of length
             (bin_hi - WELCH_BIN_MIN), units m^2/s^4/Hz (acceleration PSD) or
             m^2/Hz (if x_core was already displacement).
    """
    k = P.K if n_segments is None else n_segments
    starts = [P.SEGMENT_START_OFFSET + i * P.FFT_STEP for i in range(k)]
    n_bins = bin_hi - P.WELCH_BIN_MIN
    S = np.zeros(n_bins)

    for start in starts:
        seg_raw = x_core[start:start + P.N]
        assert len(seg_raw) == P.N, "segment out of bounds -- x_core too short"
        seg = seg_raw if segment_preprocess is None else segment_preprocess(seg_raw)
        seg_windowed = window_fn(seg)

        X = np.fft.rfft(seg_windowed)  # unnormalised, matches CMSIS convention
        # |X[k]|^2 for k in [WELCH_BIN_MIN, bin_hi)
        mag2 = np.abs(X[P.WELCH_BIN_MIN:bin_hi]) ** 2
        crrt_energy = mag2 / k
        S += 2.0 * crrt_energy / (P.N ** 2) / P.DF

    return S


def moments_from_accel_psd(S_acc, bin_lo, bin_hi):
    """m0, m2, m4 of the ELEVATION spectrum, converting S_acc -> S_eta via /omega^4,
    exactly mirroring wave_analyzer.cpp lines ~217-225."""
    bins = np.arange(bin_lo, bin_hi)
    f = bins * P.DF
    omega4 = (2.0 * np.pi * f) ** 4
    S_eta = S_acc / omega4
    m0 = np.sum(S_eta) * P.DF
    m2 = np.sum(f ** 2 * S_eta) * P.DF
    m4 = np.sum(f ** 4 * S_eta) * P.DF
    return m0, m2, m4


def moments_from_disp_psd(S_disp, bin_lo, bin_hi):
    """m0, m2, m4 directly from an already-displacement PSD (problem-2 reference
    variant -- no omega^4 conversion needed)."""
    bins = np.arange(bin_lo, bin_hi)
    f = bins * P.DF
    m0 = np.sum(S_disp) * P.DF
    m2 = np.sum(f ** 2 * S_disp) * P.DF
    m4 = np.sum(f ** 4 * S_disp) * P.DF
    return m0, m2, m4


def bulk_params_from_moments(m0, m2, m4):
    hs = 4.0 * np.sqrt(m0) if m0 > 0 else 0.0
    tz = np.sqrt(m0 / m2) if m2 > 0 else np.nan   # FIXED (issue 1): Tm02
    tc = np.sqrt(m2 / m4) if m4 > 0 else np.nan   # FIXED (issue 1): Tm24
    return hs, tz, tc


def peak_period_from_psd(S, bin_lo):
    """Tp = 1/f_peak, argmax over the supplied PSD array (whatever spectrum it is)."""
    k_peak = bin_lo + int(np.argmax(S))
    f_peak = k_peak * P.DF
    return 1.0 / f_peak


def baseline_analyze(accel_core, bin_hi=P.WELCH_BIN_MAX, n_segments=None):
    """
    Full baseline pipeline, exactly mirroring the (fixed) firmware:
      x[n] = accel[n] - GRAVITY (fixed constant, NOT per-segment mean/detrend)
      -> Hann(1.63) -> rfft -> Welch-average -> /omega^4 -> moments -> Hs/Tz/Tc
      -> Tp from the ACCELERATION spectrum peak.

    n_segments: override for number of averaged Welch segments (default P.K=21,
      firmware-exact). Only used by the problem-5 "short record" experiment.
    """
    S_acc = welch_psd_core(
        accel_core, fixed_hann_window, bin_hi,
        segment_preprocess=lambda seg: seg - P.GRAVITY,
        n_segments=n_segments,
    )
    m0, m2, m4 = moments_from_accel_psd(S_acc, P.WELCH_BIN_MIN, bin_hi)
    hs, tz, tc = bulk_params_from_moments(m0, m2, m4)
    tp = peak_period_from_psd(S_acc, P.WELCH_BIN_MIN)
    return dict(Hs=hs, Tz=tz, Tc=tc, Tp=tp, m0=m0, m2=m2, m4=m4, S_acc=S_acc)


if __name__ == "__main__":
    import signal_gen as SG

    r = SG.make_on_bin_realization(eta_amplitude=1.0, seed=0)
    accel_core = r.accel_raw[P.PAD_SAMPLES: P.PAD_SAMPLES + P.CORE_LENGTH]
    res = baseline_analyze(accel_core, bin_hi=P.WELCH_BIN_MAX)
    truth = r.truth["current"]
    print("baseline  :", {k: res[k] for k in ("Hs", "Tz", "Tc", "Tp")})
    print("truth     :", {k: truth[k] for k in ("Hs", "Tz", "Tc", "Tp")})
