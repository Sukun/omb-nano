"""
T6 -- Experiment matrix for problems 2, 3, 4, 5, 8 (per doc/readme.md methodology F).

EF4 (from /2-review-plan): every perturbation configuration is evaluated over
N_REAL >= 20 realisations sharing the SAME seed set 0..N_REAL-1. Amplitudes
are fixed by the target JONSWAP spectrum (signal_gen.py); only phases vary
across realisations, so the analytic ground truth is identical for every
realisation at a given (hs, tp) config -- this lets us report a genuine
PAIRED bias (estimate - truth) mean +/- 95% CI per method per configuration,
rather than conflating estimation bias with Welch/realisation variance.

Materiality (EF3, params.py): each row also records whether the mean bias
crosses the pre-registered materiality threshold for that parameter.

Outputs: one CSV per problem in ../outputs/csv/.
"""

from pathlib import Path

import numpy as np
import pandas as pd

import params as P
import signal_gen as SG
import wave_welch_baseline as WB
import wave_welch_variants as WV

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "csv"
OUT_DIR.mkdir(parents=True, exist_ok=True)

N_REAL = 20  # EF4 minimum


def _accel_core(accel_padded):
    return accel_padded[P.PAD_SAMPLES: P.PAD_SAMPLES + P.CORE_LENGTH]


def _ci95(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return np.nan, np.nan, np.nan
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    ci = 1.96 * std / np.sqrt(len(values))
    return mean, std, ci


def _bias_row(prefix, estimates, truth_value):
    bias = np.asarray(estimates, dtype=float) - truth_value
    mean, std, ci = _ci95(bias)
    return {
        f"{prefix}_mean": float(np.mean(estimates)),
        f"{prefix}_bias_mean": mean,
        f"{prefix}_bias_std": std,
        f"{prefix}_bias_ci95": ci,
    }


# ===========================================================================
# Problem 2: acceleration -> displacement conversion (omega^4 vs time-domain)
# ===========================================================================
def run_problem2(n_real=N_REAL):
    rows = []
    sea_states = [
        ("typical", 2.0, 8.0),
        ("long_swell_stress", 2.0, 22.0),   # Tp=22s per user confirmation
    ]
    dc_offsets = [0.0, 0.05, 0.1, 0.2, 0.3, 0.5]     # m/s^2 residual gravity bias
    drift_amps = [0.0, 0.02, 0.05, 0.1, 0.2]          # m/s^2, near-band-edge sine drift
    drift_freq = 0.03  # Hz, deliberately near the low band edge (bin9=0.0439Hz)
    # Frequency sweep at a fixed representative amplitude: a 0.03Hz drift turns out
    # to be far enough from bin9 (0.0439Hz, ~2.85 bins away) that Hann's sidelobe
    # rejection already suppresses most of it -- this sweep locates where the real
    # danger zone is (drift frequencies landing IN or adjacent to the retained band).
    drift_freq_sweep = [0.02, 0.03, 0.035, 0.04, 0.0439, 0.05, 0.06]
    drift_freq_sweep_amp = 0.15  # m/s^2, comparable to typical wave-induced accel RMS

    methods = {
        "baseline": lambda ap: WB.baseline_analyze(_accel_core(ap), bin_hi=P.WELCH_BIN_MAX),
        "lightweight": lambda ap: WV.problem2_lightweight_analyze(_accel_core(ap), bin_hi=P.WELCH_BIN_MAX),
        "reference": lambda ap: WV.problem2_reference_analyze(ap, bin_hi=P.WELCH_BIN_MAX),
    }

    for label, hs, tp in sea_states:
        for pert_type, pert_values in (("dc_offset", dc_offsets),
                                        ("edge_drift_0.03Hz", drift_amps)):
            for val in pert_values:
                results = {name: {"Hs": [], "Tz": []} for name in methods}
                truth_hs = truth_tz = None
                for seed in range(n_real):
                    r = SG.make_realization(hs, tp, seed)
                    if pert_type == "dc_offset":
                        accel_p = r.accel_raw + val
                    else:
                        drift = val * np.sin(2 * np.pi * drift_freq * r.t)
                        accel_p = r.accel_raw + drift
                    truth = r.truth["current"]
                    truth_hs, truth_tz = truth["Hs"], truth["Tz"]
                    for name, fn in methods.items():
                        res = fn(accel_p)
                        results[name]["Hs"].append(res["Hs"])
                        results[name]["Tz"].append(res["Tz"])

                row = dict(sea_state=label, hs_target=hs, tp_target=tp,
                           perturbation=pert_type, perturbation_value=val,
                           truth_Hs=truth_hs, truth_Tz=truth_tz, n_real=n_real)
                for name in methods:
                    row.update({f"{name}_Hs{k}": v for k, v in
                                _bias_row("", results[name]["Hs"], truth_hs).items()})
                    row.update({f"{name}_Tz{k}": v for k, v in
                                _bias_row("", results[name]["Tz"], truth_tz).items()})
                    mat_hs, _, _ = P.hs_is_material(truth_hs, row[f"{name}_Hs_mean"])
                    mat_tz, _ = P.period_is_material(truth_tz, row[f"{name}_Tz_mean"])
                    row[f"{name}_Hs_material"] = mat_hs
                    row[f"{name}_Tz_material"] = mat_tz
                rows.append(row)

        # --- drift frequency sweep (locate the danger zone) ---
        for freq in drift_freq_sweep:
            results = {name: {"Hs": []} for name in methods}
            truth_hs = None
            for seed in range(n_real):
                r = SG.make_realization(hs, tp, seed)
                drift = drift_freq_sweep_amp * np.sin(2 * np.pi * freq * r.t)
                accel_p = r.accel_raw + drift
                truth_hs = r.truth["current"]["Hs"]
                for name, fn in methods.items():
                    results[name]["Hs"].append(fn(accel_p)["Hs"])
            row = dict(sea_state=label, hs_target=hs, tp_target=tp,
                       perturbation="drift_freq_sweep", perturbation_value=freq,
                       truth_Hs=truth_hs, n_real=n_real)
            for name in methods:
                row.update({f"{name}_Hs{k}": v for k, v in
                            _bias_row("", results[name]["Hs"], truth_hs).items()})
                mat_hs, _, _ = P.hs_is_material(truth_hs, row[f"{name}_Hs_mean"])
                row[f"{name}_Hs_material"] = mat_hs
            rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "problem2_results.csv", index=False)
    print(f"problem2: {len(df)} configurations -> {OUT_DIR / 'problem2_results.csv'}")
    return df


# ===========================================================================
# Problem 3: despiking
# ===========================================================================
def run_problem3(n_real=N_REAL):
    rows = []
    hs, tp = 2.0, 8.0
    spike_counts = [0, 1, 3, 5, 10]
    spike_amp_sigmas = [3.0, 6.0, 10.0, 20.0]  # multiples of core-signal std

    methods = {
        "baseline": lambda ac: WB.baseline_analyze(ac, bin_hi=P.WELCH_BIN_MAX),
        "lightweight": lambda ac: WV.problem3_lightweight_analyze(ac, bin_hi=P.WELCH_BIN_MAX),
        "reference": lambda ac: WV.problem3_reference_analyze(ac, bin_hi=P.WELCH_BIN_MAX),
    }

    for n_spikes in spike_counts:
        for amp_sigma in ([spike_amp_sigmas[0]] if n_spikes == 0 else spike_amp_sigmas):
            results = {name: {"Hs": []} for name in methods}
            truth_hs = None
            for seed in range(n_real):
                r = SG.make_realization(hs, tp, seed)
                accel_core = _accel_core(r.accel_raw).copy()
                truth_hs = r.truth["current"]["Hs"]

                if n_spikes > 0:
                    rng = np.random.default_rng(seed * 1009 + 7)
                    sigma = np.std(accel_core)
                    positions = rng.integers(0, len(accel_core), size=n_spikes)
                    spike_signs = rng.choice([-1, 1], size=n_spikes)
                    accel_core[positions] += spike_signs * amp_sigma * sigma

                for name, fn in methods.items():
                    res = fn(accel_core)
                    results[name]["Hs"].append(res["Hs"])

            row = dict(hs_target=hs, tp_target=tp, n_spikes=n_spikes,
                       spike_amp_sigma=amp_sigma, truth_Hs=truth_hs, n_real=n_real)
            for name in methods:
                row.update({f"{name}_Hs{k}": v for k, v in
                            _bias_row("", results[name]["Hs"], truth_hs).items()})
                mat_hs, _, _ = P.hs_is_material(truth_hs, row[f"{name}_Hs_mean"])
                row[f"{name}_Hs_material"] = mat_hs
            rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "problem3_results.csv", index=False)
    print(f"problem3: {len(df)} configurations -> {OUT_DIR / 'problem3_results.csv'}")
    return df


# ===========================================================================
# Problem 4: QC thresholds on pathological inputs
# ===========================================================================
def run_problem4(n_real=5):
    rows = []
    cases = [
        ("extreme_hs", dict(hs=12.0, tp=9.0)),
        ("super_long_swell_out_of_band", dict(hs=2.0, tp=30.0)),
    ]

    for name, kwargs in cases:
        for seed in range(n_real):
            r = SG.make_realization(kwargs["hs"], kwargs["tp"], seed)
            b = WB.baseline_analyze(_accel_core(r.accel_raw), bin_hi=P.WELCH_BIN_MAX)
            flags = WV.qc_flags(b["Hs"], b["Tp"])
            rows.append(dict(case=name, seed=seed, Hs_est=b["Hs"], Tz_est=b["Tz"],
                              Tp_est=b["Tp"], truth_Hs=r.truth["current"]["Hs"],
                              qc_flags=";".join(flags) if flags else "none"))

    # flat / DC-only signal: no wave content at all
    flat_accel = np.full(P.PADDED_LENGTH, P.GRAVITY)
    b = WB.baseline_analyze(_accel_core(flat_accel), bin_hi=P.WELCH_BIN_MAX)
    flags = WV.qc_flags(b["Hs"], b["Tp"])
    rows.append(dict(case="flat_signal", seed=-1, Hs_est=b["Hs"], Tz_est=b["Tz"],
                      Tp_est=b["Tp"], truth_Hs=0.0,
                      qc_flags=";".join(flags) if flags else "none"))

    # saturated / clipped signal: typical sea state clipped at an artificially
    # low level to emulate an ADC-saturation-style hardware fault
    r = SG.make_realization(hs_target=2.0, tp_target=8.0, seed=0)
    clip_level = 1.0  # m/s^2 around gravity -- deliberately tight to force clipping
    accel_clipped = np.clip(r.accel_raw, P.GRAVITY - clip_level, P.GRAVITY + clip_level)
    b = WB.baseline_analyze(_accel_core(accel_clipped), bin_hi=P.WELCH_BIN_MAX)
    flags = WV.qc_flags(b["Hs"], b["Tp"])
    rows.append(dict(case="saturated_clipped", seed=0, Hs_est=b["Hs"], Tz_est=b["Tz"],
                      Tp_est=b["Tp"], truth_Hs=r.truth["current"]["Hs"],
                      qc_flags=";".join(flags) if flags else "none"))

    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "problem4_results.csv", index=False)
    print(f"problem4: {len(df)} rows -> {OUT_DIR / 'problem4_results.csv'}")
    return df


# ===========================================================================
# Problem 5: window energy-correction factor (fixed 1.63 vs adaptive)
# ===========================================================================
def run_problem5(n_real=N_REAL):
    rows = []
    methods = {
        "baseline_fixed": lambda ac, **kw: WB.baseline_analyze(ac, bin_hi=P.WELCH_BIN_MAX, **kw),
        "adaptive": lambda ac, **kw: WV.problem5_adaptive_analyze(ac, bin_hi=P.WELCH_BIN_MAX, **kw),
    }

    # (a) sea-state sweep
    sea_states = [(1.0, 5.0), (2.0, 8.0), (4.0, 10.0), (2.0, 15.0)]
    for hs, tp in sea_states:
        results = {name: {"Hs": []} for name in methods}
        truth_hs = None
        for seed in range(n_real):
            r = SG.make_realization(hs, tp, seed)
            ac = _accel_core(r.accel_raw)
            truth_hs = r.truth["current"]["Hs"]
            for name, fn in methods.items():
                results[name]["Hs"].append(fn(ac)["Hs"])
        row = dict(experiment="sea_state_sweep", hs_target=hs, tp_target=tp,
                   truth_Hs=truth_hs, n_real=n_real, n_segments=P.K)
        for name in methods:
            row.update({f"{name}_Hs{k}": v for k, v in
                        _bias_row("", results[name]["Hs"], truth_hs).items()})
            mat_hs, _, _ = P.hs_is_material(truth_hs, row[f"{name}_Hs_mean"])
            row[f"{name}_Hs_material"] = mat_hs
        rows.append(row)

    # (b) narrowband (gamma=20, near-monochromatic / non-Gaussian proxy)
    #     vs broadband (gamma=1, Pierson-Moskowitz-like) at the same Hs/Tp
    hs, tp = 2.0, 8.0
    for label, gamma in (("broadband_gamma1", 1.0), ("narrowband_gamma20", 20.0)):
        results = {name: {"Hs": []} for name in methods}
        truth_hs = None
        for seed in range(n_real):
            amplitudes, _ = SG.jonswap_amplitudes(hs, tp, gamma=gamma)
            t, accel_raw, eta = SG.synthesize(amplitudes, seed)
            truth = SG.truth_bulk_params(amplitudes, P.WELCH_BIN_MIN, P.WELCH_BIN_MAX)
            truth_hs = truth["Hs"]
            ac = _accel_core(accel_raw)
            for name, fn in methods.items():
                results[name]["Hs"].append(fn(ac)["Hs"])
        row = dict(experiment="spectral_shape", hs_target=hs, tp_target=tp,
                   shape_label=label, gamma=gamma, truth_Hs=truth_hs, n_real=n_real,
                   n_segments=P.K)
        for name in methods:
            row.update({f"{name}_Hs{k}": v for k, v in
                        _bias_row("", results[name]["Hs"], truth_hs).items()})
            mat_hs, _, _ = P.hs_is_material(truth_hs, row[f"{name}_Hs_mean"])
            row[f"{name}_Hs_material"] = mat_hs
        rows.append(row)

    # (c) short record: fewer averaged Welch segments (5 instead of firmware's 21)
    hs, tp = 2.0, 8.0
    for n_seg in (21, 10, 5, 3):
        results = {name: {"Hs": []} for name in methods}
        truth_hs = None
        for seed in range(n_real):
            r = SG.make_realization(hs, tp, seed)
            ac = _accel_core(r.accel_raw)
            truth_hs = r.truth["current"]["Hs"]
            for name, fn in methods.items():
                results[name]["Hs"].append(fn(ac, n_segments=n_seg)["Hs"])
        row = dict(experiment="short_record", hs_target=hs, tp_target=tp,
                   n_segments=n_seg, truth_Hs=truth_hs, n_real=n_real)
        for name in methods:
            row.update({f"{name}_Hs{k}": v for k, v in
                        _bias_row("", results[name]["Hs"], truth_hs).items()})
            mat_hs, _, _ = P.hs_is_material(truth_hs, row[f"{name}_Hs_mean"])
            row[f"{name}_Hs_material"] = mat_hs
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "problem5_results.csv", index=False)
    print(f"problem5: {len(df)} configurations -> {OUT_DIR / 'problem5_results.csv'}")
    return df


# ===========================================================================
# Problem 8: high-frequency band coverage (current 3.25-22.8s vs extended 2-22.8s)
# ===========================================================================
def run_problem8(n_real=N_REAL):
    rows = []
    # sea states with varying short-wave content: pure long swell (little/no
    # energy near the current high-frequency cutoff) through wind-sea/mixed
    # conditions (significant energy pushing toward and past the 3.25s cutoff)
    sea_states = [
        ("pure_swell", 2.0, 14.0),
        ("mixed_sea", 1.5, 6.0),
        ("wind_sea_short_period", 1.0, 4.0),
    ]

    for label, hs, tp in sea_states:
        results = {"current": {"Hs": [], "Tz": []}, "extended": {"Hs": [], "Tz": []}}
        truth_current = truth_extended = truth_full = None
        for seed in range(n_real):
            r = SG.make_realization(hs, tp, seed)
            ac = _accel_core(r.accel_raw)
            truth_current = r.truth["current"]
            truth_extended = r.truth["extended"]
            truth_full = r.truth["full"]

            res_cur = WB.baseline_analyze(ac, bin_hi=P.WELCH_BIN_MAX)
            res_ext = WB.baseline_analyze(ac, bin_hi=P.WELCH_BIN_MAX_EXT)
            results["current"]["Hs"].append(res_cur["Hs"])
            results["current"]["Tz"].append(res_cur["Tz"])
            results["extended"]["Hs"].append(res_ext["Hs"])
            results["extended"]["Tz"].append(res_ext["Tz"])

        row = dict(sea_state=label, hs_target=hs, tp_target=tp,
                   truth_full_Hs=truth_full["Hs"], truth_current_Hs=truth_current["Hs"],
                   truth_extended_Hs=truth_extended["Hs"], n_real=n_real)
        # missed energy fraction (relative to full-band truth) that widening the
        # band from current->extended would recover
        row["missed_energy_current_pct"] = 100.0 * (1 - (truth_current["Hs"] / truth_full["Hs"]) ** 2)
        row["missed_energy_extended_pct"] = 100.0 * (1 - (truth_extended["Hs"] / truth_full["Hs"]) ** 2)
        for name, truth in (("current", truth_current), ("extended", truth_extended)):
            row.update({f"{name}_Hs{k}": v for k, v in
                        _bias_row("", results[name]["Hs"], truth["Hs"]).items()})
            row.update({f"{name}_Tz{k}": v for k, v in
                        _bias_row("", results[name]["Tz"], truth["Tz"]).items()})
            mat_hs, _, _ = P.hs_is_material(truth_full["Hs"], row[f"{name}_Hs_mean"])
            row[f"{name}_Hs_material_vs_full"] = mat_hs
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "problem8_results.csv", index=False)
    print(f"problem8: {len(df)} configurations -> {OUT_DIR / 'problem8_results.csv'}")
    return df


if __name__ == "__main__":
    print("Running experiment matrix (T6)...")
    df2 = run_problem2()
    df3 = run_problem3()
    df4 = run_problem4()
    df5 = run_problem5()
    df8 = run_problem8()
    print("\nAll experiments complete. CSVs written to", OUT_DIR)
