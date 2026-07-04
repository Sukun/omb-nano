"""
T1 -- Synthetic "IMU-measured" wave signal generator with analytic ground truth.

Design (per doc/readme.md methodology A, and /2-review-plan EF1/EF4):

  - eta(t) = sum_i a_i * cos(2*pi*f_i*t + phi_i)
    Amplitudes a_i are DETERMINISTIC (set by the target JONSWAP spectrum);
    only phases phi_i are randomised per realisation (EF4: "amplitude fixed,
    phase random" -- this keeps the ground-truth spectral moments exact for
    every realisation, and lets us do paired comparisons across realisations).

  - Frequency components sit exactly on the Welch algorithm's own frequency
    grid f_k = k * DF (k = 1 .. FULL_BAND_MAX_BIN). This makes the "true"
    spectral moments over the current/extended/full retained bands a simple
    sum over the matching component subset -- no interpolation error between
    the synthetic truth and what the Welch algorithm could in principle
    recover.

  - The synthetic raw accelerometer signal is a(t) = GRAVITY + eta''(t),
    i.e. exactly what an ideal (noise-free) IMU vertical channel would
    report: gravity DC plus the wave-induced acceleration. eta'' is taken
    analytically (closed form for a sum of cosines), not by finite
    differencing, to avoid introducing any numerical differentiation error
    into the "ideal" baseline signal (perturbations like drift/spikes are
    added later, in run_experiments.py, on top of this ideal signal).

  - EF1 padding: PAD_SAMPLES extra samples are generated on each side of the
    "core" acquisition window (see params.py) so that the SWIFT-reference
    variant (problem 2) can run its own filtfilt + 50s-transient-drop
    pipeline and still end up analysing the exact same core window as the
    baseline.
"""

from dataclasses import dataclass, field

import numpy as np

import params as P


# ---------------------------------------------------------------------------
# JONSWAP spectrum
# ---------------------------------------------------------------------------
def _jonswap_shape(f, fp, gamma=3.3):
    """Unnormalised JONSWAP shape (alpha=1, g=9.81). f in Hz, fp in Hz."""
    f = np.asarray(f, dtype=float)
    out = np.zeros_like(f)
    valid = f > 0
    fv = f[valid]
    g = P.GRAVITY
    pm = (fv ** -5) * np.exp(-1.25 * (fv / fp) ** -4)
    sigma = np.where(fv <= fp, 0.07, 0.09)
    r = np.exp(-((fv - fp) ** 2) / (2.0 * sigma ** 2 * fp ** 2))
    out[valid] = g ** 2 * (2 * np.pi) ** -4 * pm * gamma ** r
    return out


# Frequency grid used for ALL synthetic-truth bookkeeping: exactly the
# Welch algorithm's own bin frequencies, extended well past the retained
# band so "full-band" truth captures essentially all real wave energy.
FULL_BAND_MAX_BIN = 900          # f up to ~4.39 Hz, safely below Nyquist (5 Hz)
GRID_BINS = np.arange(1, FULL_BAND_MAX_BIN + 1)   # exclude k=0 (DC)
GRID_FREQS = GRID_BINS * P.DF


def jonswap_amplitudes(hs_target, tp_target, gamma=3.3):
    """
    Return per-component amplitudes a_i on GRID_FREQS such that the
    discretised spectrum has EXACTLY variance (Hs_target/4)^2 over the full
    grid (i.e. Hs computed via the moments machinery on GRID_FREQS reproduces
    hs_target exactly, removing the usual JONSWAP alpha-parameter bias).

    a_i^2 / 2 = S_eta(f_i) * DF   =>   a_i = sqrt(2 * S_eta(f_i) * DF)
    """
    fp = 1.0 / tp_target
    shape = _jonswap_shape(GRID_FREQS, fp, gamma=gamma)
    m0_raw = np.sum(shape) * P.DF
    if m0_raw <= 0:
        raise ValueError("degenerate JONSWAP shape (check tp_target)")
    target_m0 = (hs_target / 4.0) ** 2
    scale = target_m0 / m0_raw
    S_eta = shape * scale
    amplitudes = np.sqrt(2.0 * S_eta * P.DF)
    return amplitudes, S_eta


def on_bin_amplitude(eta_amplitude):
    """Single on-bin tone (T3 validation gate): amplitude array with all
    zeros except at ON_BIN_INDEX."""
    amplitudes = np.zeros_like(GRID_FREQS)
    idx = np.where(GRID_BINS == P.ON_BIN_INDEX)[0]
    assert len(idx) == 1, "ON_BIN_INDEX must lie within the truth grid"
    amplitudes[idx[0]] = eta_amplitude
    return amplitudes


def off_bin_amplitude(eta_amplitude):
    """Single off-bin tone (spectral-leakage characterisation only)."""
    # Not on the GRID_FREQS lattice by construction; handled as a special
    # case directly in `synthesize` via explicit (freq, amplitude) override.
    return None


# ---------------------------------------------------------------------------
# Truth spectral moments over arbitrary bin ranges
# ---------------------------------------------------------------------------
def truth_moments(amplitudes, bin_lo, bin_hi):
    """
    Analytic spectral moments m0, m2, m4 of the ELEVATION spectrum, restricted
    to GRID_BINS in [bin_lo, bin_hi) (Python half-open, matching WELCH_BIN_MAX
    convention). Each component contributes a_i^2/2 to m0 at its frequency.
    """
    mask = (GRID_BINS >= bin_lo) & (GRID_BINS < bin_hi)
    f = GRID_FREQS[mask]
    a = amplitudes[mask]
    energy = 0.5 * a ** 2  # m^2, this component's contribution to m0
    m0 = np.sum(energy)
    m2 = np.sum(energy * f ** 2)
    m4 = np.sum(energy * f ** 4)
    return m0, m2, m4


def truth_bulk_params(amplitudes, bin_lo, bin_hi):
    """Hs, Tz, Tc, Tp (from the ELEVATION spectrum peak) over [bin_lo, bin_hi)."""
    m0, m2, m4 = truth_moments(amplitudes, bin_lo, bin_hi)
    hs = 4.0 * np.sqrt(m0) if m0 > 0 else 0.0
    tz = np.sqrt(m0 / m2) if m2 > 0 else np.nan
    tc = np.sqrt(m2 / m4) if m4 > 0 else np.nan
    mask = (GRID_BINS >= bin_lo) & (GRID_BINS < bin_hi)
    f = GRID_FREQS[mask]
    a = amplitudes[mask]
    if len(a) == 0 or np.all(a == 0):
        tp = np.nan
    else:
        tp = 1.0 / f[np.argmax(a ** 2)]
    return dict(Hs=hs, Tz=tz, Tc=tc, Tp=tp, m0=m0, m2=m2, m4=m4)


# ---------------------------------------------------------------------------
# Time-domain synthesis
# ---------------------------------------------------------------------------
@dataclass
class Realization:
    seed: int
    t: np.ndarray               # padded time array, t=0 at core window start
    accel_raw: np.ndarray       # GRAVITY + eta''(t), padded, "ideal IMU" signal
    eta: np.ndarray              # sea-surface elevation, padded (diagnostics only)
    amplitudes: np.ndarray       # per-grid-bin amplitude used (fixed across realisations)
    truth: dict = field(default_factory=dict)  # {'full':..., 'current':..., 'extended':...}


def _padded_time_array():
    n = P.PADDED_LENGTH
    # index 0 .. PADDED_LENGTH-1; t=0 corresponds to padded index PAD_SAMPLES
    # (i.e. the start of the "core" 12387-sample acquisition window).
    idx = np.arange(n)
    return (idx - P.PAD_SAMPLES) / P.FS


def synthesize(amplitudes, seed, extra_component=None):
    """
    Build one realisation: random phases per component, analytic eta(t) and
    a(t) = GRAVITY + eta''(t) over the padded time window.

    extra_component: optional (freq_hz, amplitude) tuple for a single
    additional tone NOT on the GRID_FREQS lattice (used for the off-bin
    leakage-characterisation signal). Its phase is also randomised.
    """
    rng = np.random.default_rng(seed)
    t = _padded_time_array()

    freqs = GRID_FREQS
    amps = amplitudes
    if extra_component is not None:
        f_extra, a_extra = extra_component
        freqs = np.concatenate([freqs, [f_extra]])
        amps = np.concatenate([amps, [a_extra]])

    nonzero = amps > 0
    f_nz = freqs[nonzero]
    a_nz = amps[nonzero]
    phi = rng.uniform(0.0, 2 * np.pi, size=f_nz.shape)
    omega = 2 * np.pi * f_nz

    # eta(t) = sum a_i cos(w_i t + phi_i)
    # eta''(t) = -sum a_i w_i^2 cos(w_i t + phi_i)
    phase_matrix = np.outer(t, omega) + phi[None, :]   # (len(t), n_components)
    cos_terms = np.cos(phase_matrix)
    eta = cos_terms @ a_nz
    accel_wave = -(cos_terms @ (a_nz * omega ** 2))
    accel_raw = P.GRAVITY + accel_wave

    return t, accel_raw, eta


def make_realization(hs_target, tp_target, seed, gamma=3.3, extra_component=None):
    amplitudes, _S_eta = jonswap_amplitudes(hs_target, tp_target, gamma=gamma)
    t, accel_raw, eta = synthesize(amplitudes, seed, extra_component=extra_component)
    truth = {
        "full": truth_bulk_params(amplitudes, 1, FULL_BAND_MAX_BIN + 1),
        "current": truth_bulk_params(amplitudes, P.WELCH_BIN_MIN, P.WELCH_BIN_MAX),
        "extended": truth_bulk_params(amplitudes, P.WELCH_BIN_MIN, P.WELCH_BIN_MAX_EXT),
    }
    return Realization(seed=seed, t=t, accel_raw=accel_raw, eta=eta,
                        amplitudes=amplitudes, truth=truth)


def make_on_bin_realization(eta_amplitude=1.0, seed=0):
    """T3 validation-gate signal: pure tone exactly on ON_BIN_INDEX."""
    amplitudes = on_bin_amplitude(eta_amplitude)
    t, accel_raw, eta = synthesize(amplitudes, seed)
    truth = {
        "full": truth_bulk_params(amplitudes, 1, FULL_BAND_MAX_BIN + 1),
        "current": truth_bulk_params(amplitudes, P.WELCH_BIN_MIN, P.WELCH_BIN_MAX),
        "extended": truth_bulk_params(amplitudes, P.WELCH_BIN_MIN, P.WELCH_BIN_MAX_EXT),
    }
    return Realization(seed=seed, t=t, accel_raw=accel_raw, eta=eta,
                        amplitudes=amplitudes, truth=truth)


def make_off_bin_realization(eta_amplitude=1.0, seed=0):
    """Leakage-characterisation signal: pure tone at OFF_BIN_FREQ (between
    two bin centers), used only to illustrate spectral leakage -- not part
    of the T3 pass/fail gate."""
    amplitudes = np.zeros_like(GRID_FREQS)
    t, accel_raw, eta = synthesize(amplitudes, seed,
                                    extra_component=(P.OFF_BIN_FREQ, eta_amplitude))
    # truth for the off-bin tone: build a tiny amplitude array manually since
    # it's not on GRID_FREQS.
    m0 = 0.5 * eta_amplitude ** 2
    f = P.OFF_BIN_FREQ
    truth_single = dict(Hs=4 * np.sqrt(m0), Tz=1 / f, Tc=1 / f, Tp=1 / f, m0=m0,
                         m2=m0 * f ** 2, m4=m0 * f ** 4)
    truth = {"full": truth_single, "current": truth_single, "extended": truth_single}
    return Realization(seed=seed, t=t, accel_raw=accel_raw, eta=eta,
                        amplitudes=amplitudes, truth=truth)


if __name__ == "__main__":
    # quick smoke test
    r = make_on_bin_realization(eta_amplitude=1.0, seed=0)
    print("on-bin truth (current band):", r.truth["current"])
    print("padded length:", len(r.t), "expected:", P.PADDED_LENGTH)

    r2 = make_realization(hs_target=2.0, tp_target=8.0, seed=1)
    print("JONSWAP Hs=2.0 Tp=8.0 truth (full):", r2.truth["full"])
    print("JONSWAP Hs=2.0 Tp=8.0 truth (current band):", r2.truth["current"])
