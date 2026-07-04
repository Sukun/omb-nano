"""
Constants mirrored from omb-nano/src/params.h and wave_analyzer.cpp.

Every value here must trace back to the firmware source. Do not "round" or
"simplify" anything -- the whole point of the validation gate (T3) is to
catch silent mismatches between this file and the actual firmware constants.

Source: omb-nano/src/params.h (as of the wave_analyzer.cpp fix for issues 1/6).
"""

import numpy as np

# ---------------------------------------------------------------------------
# Welch / FFT parameters (params.h)
# ---------------------------------------------------------------------------
FS = 10.0                      # Hz, IMU wave-analysis sample rate
N = 2048                       # fft_length
FFT_STEP = 512                 # fft_overlap in the firmware (misleadingly named: it's the
                                # step between consecutive segment starts, NOT the overlap
                                # amount). True overlap = N - FFT_STEP = 1536 (75%).
K = 21                         # number_welch_segments
SEGMENT_START_OFFSET = 50      # s0: first segment starts at sample index 50 in the
                                # "core" acquisition window (matches wave_analyzer.cpp)

DF = FS / N                    # welch_frequency_resolution ~= 0.0048828125 Hz

WELCH_BIN_MIN = 9              # retained band low edge (current firmware)
WELCH_BIN_MAX = 64             # retained band high edge, exclusive (current firmware)
WELCH_BIN_MAX_EXT = 103        # extended high edge for problem 8 (bin 102 ~= 0.498 Hz ~= 2.0s)

NBR_WAVE_PACKET_FREQS = WELCH_BIN_MAX - WELCH_BIN_MIN  # 55

TOTAL_NUMBER_OF_SAMPLES = 2048 * 6     # 12288
CORE_LENGTH = TOTAL_NUMBER_OF_SAMPLES + 99  # 12387, matches gather_imu_data() acquisition length

GRAVITY = 9.81                 # m/s^2, subtracted as a fixed DC constant (NOT a per-segment mean)

# Hann window energy correction factor used in wave_analyzer.cpp (fixed empirical constant,
# this is exactly what problem 5 questions).
HANN_ENERGY_SCALING_FACTOR = 1.63

# ---------------------------------------------------------------------------
# Derived: exact sample index of each Welch segment start, matching the
# firmware loop in perform_welch_analysis_imu_data():
#   start_current_segment = 50; end = start + fft_length
#   for each of K segments: use [start, end); start += fft_overlap
# ---------------------------------------------------------------------------
def segment_starts():
    return np.array([SEGMENT_START_OFFSET + k * FFT_STEP for k in range(K)], dtype=int)


MIN_CORE_LENGTH_REQUIRED = SEGMENT_START_OFFSET + (K - 1) * FFT_STEP + N  # 12338
assert CORE_LENGTH >= MIN_CORE_LENGTH_REQUIRED, "CORE_LENGTH too short for K segments"

# ---------------------------------------------------------------------------
# EF1 padding (from /2-review-plan): generate PAD_SAMPLES extra samples on
# each side of the "core" acquisition window so that:
#   - the baseline (mirrors firmware exactly) operates on the core window only
#   - the SWIFT-reference variant (problem 2), which needs to run a filtfilt
#     over a longer series and then drop 500 samples (50s) of transient at
#     each end, still has its remaining valid data fully cover the same
#     core window used by the baseline (so both are compared on identical
#     underlying wave content -- this is the fairness fix EF1).
# 600 > 500 (the filtfilt transient drop) with a comfortable margin.
# ---------------------------------------------------------------------------
PAD_SAMPLES = 600
FILTFILT_TRANSIENT_DROP = 500  # 50 s at 10 Hz, matches SWIFT's rawdisplacements.m convention

PADDED_LENGTH = PAD_SAMPLES + CORE_LENGTH + PAD_SAMPLES

# ---------------------------------------------------------------------------
# Validation-gate signal (T3): a sinusoid placed EXACTLY on a retained bin
# center, so that the discrete Welch spectrum reduces to a single non-zero
# bin analytically (no spectral leakage to argue about).
# ---------------------------------------------------------------------------
ON_BIN_INDEX = 46                      # inside [WELCH_BIN_MIN, WELCH_BIN_MAX)
ON_BIN_FREQ = ON_BIN_INDEX * DF        # ~0.22461 Hz, period ~4.452 s

# Off-bin frequency for leakage characterisation (matches the firmware's own
# dummy_imu_accel_frequency comment: "in the middle of two frequency bins").
OFF_BIN_FREQ = (ON_BIN_INDEX + 0.5) * DF

# ---------------------------------------------------------------------------
# Materiality thresholds (pre-registered in doc/readme.md, EF3).
# A deviation >= threshold is judged "material" (worth fixing the firmware
# for); < threshold is judged negligible.
# ---------------------------------------------------------------------------
THRESH_HS_REL = 0.05           # 5%
THRESH_HS_ABS_M = 0.10         # 0.10 m (materiality = whichever is larger in absolute terms)
THRESH_T_S = 0.5               # seconds, for Tz/Tc
THRESH_TP_BINS = 1             # frequency bins, for Tp


def hs_is_material(hs_true, hs_est):
    """EF3 materiality rule for Hs: |dHs| > 5% or > 0.10 m (whichever is larger)."""
    dhs = abs(hs_est - hs_true)
    thresh = max(THRESH_HS_REL * hs_true, THRESH_HS_ABS_M)
    return dhs > thresh, dhs, thresh


def period_is_material(t_true, t_est):
    """EF3 materiality rule for Tz/Tc: |dT| > 0.5 s."""
    dt = abs(t_est - t_true)
    return dt > THRESH_T_S, dt


def tp_is_material(tp_true, tp_est):
    """EF3 materiality rule for Tp: |dTp| in bins > 1."""
    f_true = 1.0 / tp_true
    f_est = 1.0 / tp_est
    d_bins = abs(f_est - f_true) / DF
    return d_bins > THRESH_TP_BINS, d_bins
