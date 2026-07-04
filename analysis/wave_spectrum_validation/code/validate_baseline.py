"""
T3 -- Scaling validation gate (BLOCKING, per /2-review-plan).

Must pass before any of the problem 2/3/4/5/8 experiments are trusted:
the Python baseline's Hs on a pure on-bin tone must match the analytic
truth to within 2%. If this fails, the CMSIS-rfft vs numpy-rfft scaling
convention differs and must be fixed before proceeding (see doc/readme.md
methodology B).
"""

import numpy as np

import params as P
import signal_gen as SG
import wave_welch_baseline as WB

TOLERANCE = 0.02  # 2%


def relative_error(est, true):
    return abs(est - true) / abs(true) if true != 0 else np.nan


def run_on_bin_gate():
    r = SG.make_on_bin_realization(eta_amplitude=1.0, seed=0)
    accel_core = r.accel_raw[P.PAD_SAMPLES: P.PAD_SAMPLES + P.CORE_LENGTH]
    res = WB.baseline_analyze(accel_core, bin_hi=P.WELCH_BIN_MAX)
    truth = r.truth["current"]

    errs = {k: relative_error(res[k], truth[k]) for k in ("Hs", "Tz", "Tc", "Tp")}
    passed = errs["Hs"] <= TOLERANCE
    return res, truth, errs, passed


def run_off_bin_leakage_check():
    """Not a pass/fail gate -- just characterises how much energy a strictly
    off-bin tone leaks into neighbouring bins under the Hann window, and
    confirms the leakage stays within the retained band (so m0 integrated
    over the WHOLE band is still accurate even though individual bins are
    smeared)."""
    r = SG.make_off_bin_realization(eta_amplitude=1.0, seed=0)
    accel_core = r.accel_raw[P.PAD_SAMPLES: P.PAD_SAMPLES + P.CORE_LENGTH]
    res = WB.baseline_analyze(accel_core, bin_hi=P.WELCH_BIN_MAX)
    truth = r.truth["current"]
    err_hs = relative_error(res["Hs"], truth["Hs"])
    return res, truth, err_hs


def run_jonswap_sanity(hs_target=2.0, tp_target=8.0, seed=1):
    """Non-blocking sanity check: a realistic sea state under zero perturbation
    should also reproduce the current-band truth reasonably closely (residual
    here is the baseline's intrinsic bias from windowing/leakage near the band
    edges -- expected to be small but non-zero, and NOT to be confused with
    the problem 2/3/5 effects studied later)."""
    r = SG.make_realization(hs_target, tp_target, seed)
    accel_core = r.accel_raw[P.PAD_SAMPLES: P.PAD_SAMPLES + P.CORE_LENGTH]
    res = WB.baseline_analyze(accel_core, bin_hi=P.WELCH_BIN_MAX)
    truth = r.truth["current"]
    errs = {k: relative_error(res[k], truth[k]) for k in ("Hs", "Tz", "Tc")}
    return res, truth, errs


if __name__ == "__main__":
    print("=" * 70)
    print("T3 VALIDATION GATE -- on-bin tone, Hann window, current band")
    print("=" * 70)
    res, truth, errs, passed = run_on_bin_gate()
    for k in ("Hs", "Tz", "Tc", "Tp"):
        print(f"  {k:3s}  baseline={res[k]:.6f}  truth={truth[k]:.6f}  "
              f"rel_err={errs[k]*100:.3f}%")
    verdict = "PASS" if passed else "FAIL"
    print(f"\n  Hs relative error {errs['Hs']*100:.3f}% "
          f"(tolerance {TOLERANCE*100:.0f}%) -> {verdict}")

    if not passed:
        raise SystemExit(
            "T3 GATE FAILED: baseline scaling does not match analytic truth. "
            "STOP -- diagnose CMSIS/numpy rfft scaling mismatch before "
            "proceeding to T4/variants (see doc/readme.md risks section)."
        )

    print("\n" + "=" * 70)
    print("Off-bin leakage characterisation (informational, not a gate)")
    print("=" * 70)
    res_ob, truth_ob, err_ob = run_off_bin_leakage_check()
    print(f"  Hs relative error for off-bin tone: {err_ob*100:.3f}% "
          f"(expected non-zero due to Hann main-lobe leakage; band-integrated "
          f"m0 should still be close since leakage stays within the retained band)")

    print("\n" + "=" * 70)
    print("JONSWAP zero-perturbation sanity check (informational)")
    print("=" * 70)
    res_j, truth_j, errs_j = run_jonswap_sanity()
    for k in ("Hs", "Tz", "Tc"):
        print(f"  {k:3s}  baseline={res_j[k]:.4f}  truth(current band)={truth_j[k]:.4f}  "
              f"rel_err={errs_j[k]*100:.2f}%")
    print("\nNote: full-band Tc/m4 truth is not used as a reference anywhere -- "
          "JONSWAP's f^-5 high-frequency tail makes the m4 integral log-divergent "
          "in the unbounded case; only current-band and extended-band (both bounded "
          "by the algorithm's own retained bins) truths are physically meaningful "
          "here. See doc/readme.md notes.")

    print("\n" + "=" * 70)
    print("T3 GATE: PASSED. Safe to proceed to T4 (variants).")
    print("=" * 70)
