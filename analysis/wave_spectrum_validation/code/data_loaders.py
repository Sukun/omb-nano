"""
T7 -- Generic input interface for the Welch pipeline, plus Phase 2 stubs for
real SWIFT / NIWA WII data (per doc/readme.md methodology E).

`load_accel_series(source)` is the single entry point that baseline_analyze /
the problem 2-5-8 variants consume: they only ever see a padded 10 Hz vertical
acceleration array (P.PADDED_LENGTH samples) plus a metadata dict. This means
swapping in real data later (Phase 2) requires no changes to
wave_welch_baseline.py or wave_welch_variants.py -- only a new `source["type"]`
branch here.

Phase 1 (this study): only "synthetic_jonswap" / "synthetic_on_bin" /
"synthetic_off_bin" are implemented and used.
Phase 2 (future, NOT executed in this study): "swift_raw" / "niwa_heave" are
documented stubs that raise NotImplementedError with the intended design.
"""

import numpy as np

import params as P
import signal_gen as SG


def load_accel_series(source):
    """
    source: dict with a "type" key selecting the loader, e.g.
        {"type": "synthetic_jonswap", "hs": 2.0, "tp": 8.0, "seed": 1}
        {"type": "synthetic_on_bin", "eta_amplitude": 1.0, "seed": 0}
        {"type": "synthetic_off_bin", "eta_amplitude": 1.0, "seed": 0}
        {"type": "swift_raw", "path": "...", "burst_id": "..."}     # Phase 2 stub
        {"type": "niwa_heave", "path": "...", "record_id": "..."}   # Phase 2 stub

    Returns:
        accel_padded : np.ndarray, 10 Hz vertical acceleration, length
                       P.PADDED_LENGTH for synthetic sources (real sources
                       would need their own padding/framing, TBD in Phase 2).
        meta : dict with provenance (source type, params used, and -- for
               synthetic sources -- the analytic ground truth for
               full/current/extended bands).
    """
    kind = source["type"]
    if kind == "synthetic_jonswap":
        r = SG.make_realization(
            hs_target=source["hs"], tp_target=source["tp"], seed=source["seed"],
            gamma=source.get("gamma", 3.3),
        )
        meta = dict(type=kind, hs=source["hs"], tp=source["tp"], seed=source["seed"],
                    fs=P.FS, truth=r.truth)
        return r.accel_raw, meta

    if kind == "synthetic_on_bin":
        r = SG.make_on_bin_realization(eta_amplitude=source.get("eta_amplitude", 1.0),
                                        seed=source.get("seed", 0))
        meta = dict(type=kind, seed=source.get("seed", 0), fs=P.FS, truth=r.truth)
        return r.accel_raw, meta

    if kind == "synthetic_off_bin":
        r = SG.make_off_bin_realization(eta_amplitude=source.get("eta_amplitude", 1.0),
                                         seed=source.get("seed", 0))
        meta = dict(type=kind, seed=source.get("seed", 0), fs=P.FS, truth=r.truth)
        return r.accel_raw, meta

    if kind == "swift_raw":
        return _load_swift_raw(source)

    if kind == "niwa_heave":
        return _load_niwa_heave(source)

    raise ValueError(f"unknown source type: {kind!r}")


# ---------------------------------------------------------------------------
# Phase 2 stubs (NOT implemented / NOT executed in this study)
# ---------------------------------------------------------------------------
def _load_swift_raw(source):
    """
    STUB -- intended design (see doc/readme.md 'Phase 2 真实数据'):

    SWIFT's raw IMU vertical acceleration is sampled at 25 Hz (see
    rawdisplacements.m: `a = quat_rotate_vector(AHRS.Accel, AHRS.Quat)`,
    dt implied ~0.04s). To feed this 10 Hz Welch pipeline it must be:
      1. Anti-alias low-pass filtered (e.g. 4th-order Butterworth, cutoff
         ~4 Hz, comfortably below the new 5 Hz Nyquist at 10 Hz).
      2. Resampled 25 Hz -> 10 Hz. This is NOT an integer decimation ratio;
         use `scipy.signal.resample_poly(x, up=2, down=5)` (25*2/5 = 10)
         rather than naive decimation.

    Real SWIFT data has NO analytic ground truth (unlike the synthetic
    signals here). The intended pseudo-reference is SWIFT's own onboard/
    post-processed output (Hs = SWIFT.sigwaveheight, Tp = SWIFT.peakwaveperiod,
    computed by GPSwaves_v2.m / rawdisplacements.m) -- treated as a PSEUDO
    ground truth with its own uncertainty, not a true reference (see
    doc/readme.md risks section: "真实数据无解析真值 -> 伪参照对比有不确定性").

    Reference files (read-only, outside this repo):
      .../SeaState project_VEWS Parameter Calibration/Data analysis/SWIFT/
        2015 arctic cruise/SKQ201512S_shared/my processed wave data from swift/
        {rawdisplacements.m, GPSwaves_v2.m}

    Not implemented in this study (Phase 1 is synthetic-only per scope).
    """
    raise NotImplementedError(
        "swift_raw loader is a Phase 2 stub (see docstring for the intended "
        "25Hz->10Hz anti-alias + resample_poly design and the pseudo-reference "
        "approach). Not implemented or executed in this study."
    )


def _load_niwa_heave(source):
    """
    STUB -- intended design (see doc/readme.md 'Phase 2 真实数据'):

    NIWA WII provides HEAVE (displacement, not acceleration) directly at 2 Hz
    (run_wii.m). Nyquist = 1 Hz, so:
      - It cannot resolve the problem-8 extended band (0.31-0.5 Hz) AT ALL.
      - It only marginally resolves the top of the CURRENT band (up to
        ~0.31 Hz is close to the 1 Hz Nyquist, so should be usable there,
        but with less margin than the 25 Hz SWIFT source).
      - To feed the acceleration-based pipeline studied here, heave would
        need to be double-DIFFERENTIATED (the opposite numerical problem
        from double integration: differentiation amplifies high-frequency
        noise rather than low-frequency drift) and upsampled 2 Hz -> 10 Hz,
        which cannot recover content the 2 Hz stream never resolved.

    NIWA is therefore a poor match for validating THIS acceleration-based
    algorithm and is kept only as a secondary, documented option -- SWIFT
    (25 Hz acceleration) is the intended primary Phase 2 source.

    Reference files (read-only, outside this repo):
      .../Data sources_Buoy data/NIWA_WII_buoy/NIWA WII buoy-201704/NIWA WII buoy/
        {run_wii.m, process.m, get_NIWA.m}

    Not implemented in this study (Phase 1 is synthetic-only per scope).
    """
    raise NotImplementedError(
        "niwa_heave loader is a Phase 2 stub (see docstring: 2Hz heave requires "
        "double differentiation + upsampling, cannot resolve problem-8 band at "
        "all, kept as a secondary/documented option only). Not implemented or "
        "executed in this study."
    )


if __name__ == "__main__":
    accel, meta = load_accel_series({"type": "synthetic_jonswap", "hs": 2.0, "tp": 8.0, "seed": 1})
    print("loaded:", meta["type"], "len=", len(accel), "truth Hs (current)=",
          meta["truth"]["current"]["Hs"])

    for stub_source in (
        {"type": "swift_raw", "path": "dummy", "burst_id": "dummy"},
        {"type": "niwa_heave", "path": "dummy", "record_id": "dummy"},
    ):
        try:
            load_accel_series(stub_source)
        except NotImplementedError as e:
            print(f"{stub_source['type']}: correctly raises NotImplementedError ({e.args[0][:60]}...)")
