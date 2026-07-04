"""
Supplementary analysis (beyond the original report) answering the user's
follow-up questions about problem 2 (low-frequency drift):

  Q1. What frequency/period range does the drift actually corrupt? Does it
      bleed into other bands? Does it affect Hs / Tz / Tc / Tp?
  Q2. Since the buoy transmits the full per-bin spectrum, can onshore
      processing just drop the drift-corrupted low bins and recompute clean
      parameters?

Worst-case drift from the report: sinusoid at 0.0439 Hz (= bin 9, the current
low band edge, period 22.8 s), amplitude 0.15 m/s^2, on a typical wind-sea
(Hs=2 m, Tp=8 s). Averaged over N=20 phase realisations (paired, same seeds).
"""

from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import params as P
import signal_gen as SG
import wave_welch_baseline as WB

matplotlib.rcParams["font.sans-serif"] = ["PingFang HK", "Heiti TC", "Arial Unicode MS"]
matplotlib.rcParams["axes.unicode_minus"] = False
OUT_FIG = Path(__file__).resolve().parent.parent / "outputs" / "figures"

N_REAL = 20
DRIFT_FREQ = 0.0439   # Hz, worst case (= bin 9)
DRIFT_AMP = 0.15      # m/s^2
HS, TP = 2.0, 8.0
BIN_HI = P.WELCH_BIN_MAX  # 64, current band


def accel_core_with_drift(realization, add_drift):
    a = realization.accel_raw.copy()
    if add_drift:
        a = a + DRIFT_AMP * np.sin(2 * np.pi * DRIFT_FREQ * realization.t)
    return a[P.PAD_SAMPLES: P.PAD_SAMPLES + P.CORE_LENGTH]


def analyze_full(accel_core):
    """Return the acceleration PSD (what is transmitted) plus Hs/Tz/Tc/Tp."""
    res = WB.baseline_analyze(accel_core, bin_hi=BIN_HI)
    return res


def moments_over_kept_bins(S_acc_full, keep_from_bin):
    """Recompute m0/m2/m4 from a transmitted accel spectrum, EXCLUDING bins
    below keep_from_bin (the onshore band-exclusion step). S_acc_full is
    indexed from WELCH_BIN_MIN."""
    start = keep_from_bin - P.WELCH_BIN_MIN
    S_sub = S_acc_full[start:]
    bins = np.arange(keep_from_bin, BIN_HI)
    f = bins * P.DF
    omega4 = (2 * np.pi * f) ** 4
    S_eta = S_sub / omega4
    m0 = np.sum(S_eta) * P.DF
    m2 = np.sum(f ** 2 * S_eta) * P.DF
    m4 = np.sum(f ** 4 * S_eta) * P.DF
    hs, tz, tc = WB.bulk_params_from_moments(m0, m2, m4)
    # Tp on the accel spectrum over kept bins (firmware convention)
    tp = 1.0 / ((keep_from_bin + int(np.argmax(S_sub))) * P.DF)
    return dict(Hs=hs, Tz=tz, Tc=tc, Tp=tp)


def main():
    bins = np.arange(P.WELCH_BIN_MIN, BIN_HI)
    freqs = bins * P.DF
    omega4 = (2 * np.pi * freqs) ** 4

    S_acc_clean = np.zeros(len(bins))
    S_acc_drift = np.zeros(len(bins))
    clean_params = {k: [] for k in ("Hs", "Tz", "Tc", "Tp")}
    drift_params = {k: [] for k in ("Hs", "Tz", "Tc", "Tp")}
    # onshore recompute after excluding low bins, several cutoffs
    cutoffs = [P.WELCH_BIN_MIN, 12, 13, 14]  # 9=keep all, 12/13/14 = drop 9..(c-1)
    drift_excl = {c: {k: [] for k in ("Hs", "Tz", "Tc", "Tp")} for c in cutoffs}
    truth_current = {k: [] for k in ("Hs", "Tz", "Tc", "Tp")}
    truth_kept = {c: {"Hs": []} for c in cutoffs}

    for seed in range(N_REAL):
        r = SG.make_realization(HS, TP, seed)
        t_cur = SG.truth_bulk_params(r.amplitudes, P.WELCH_BIN_MIN, BIN_HI)
        for k in truth_current:
            truth_current[k].append(t_cur[k])
        for c in cutoffs:
            truth_kept[c]["Hs"].append(SG.truth_bulk_params(r.amplitudes, c, BIN_HI)["Hs"])

        res_clean = analyze_full(accel_core_with_drift(r, add_drift=False))
        res_drift = analyze_full(accel_core_with_drift(r, add_drift=True))
        S_acc_clean += res_clean["S_acc"] / N_REAL
        S_acc_drift += res_drift["S_acc"] / N_REAL
        for k in clean_params:
            clean_params[k].append(res_clean[k])
            drift_params[k].append(res_drift[k])
        for c in cutoffs:
            rc = moments_over_kept_bins(res_drift["S_acc"], c)
            for k in drift_excl[c]:
                drift_excl[c][k].append(rc[k])

    # --- PART A: per-bin corruption localisation ---
    Seta_clean = S_acc_clean / omega4
    Seta_drift = S_acc_drift / omega4
    print("=" * 78)
    print("PART A — 逐bin污染定位 (worst-case drift 0.0439Hz/0.15, avg N=20)")
    print("=" * 78)
    print(f"{'bin':>3} {'f[Hz]':>7} {'T[s]':>6} | {'accel谱 drift/clean':>18} | {'elev谱 drift/clean':>18}")
    for i, (b, f) in enumerate(zip(bins, freqs)):
        ra = S_acc_drift[i] / S_acc_clean[i] if S_acc_clean[i] > 0 else np.nan
        re = Seta_drift[i] / Seta_clean[i] if Seta_clean[i] > 0 else np.nan
        # print low bins densely, then a few representative high bins
        if b <= 20 or b in (25, 30, 40, 50, 63):
            print(f"{b:>3} {f:>7.4f} {1/f:>6.1f} | {ra:>18.2f} | {re:>18.2f}")

    # --- PART B: parameter impact ---
    def stat(d, k):
        return float(np.mean(d[k]))
    print()
    print("=" * 78)
    print("PART B — 对海浪参数的影响 (truth vs 干净 vs 有漂移, 全部现带 bin9-63)")
    print("=" * 78)
    print(f"{'参数':>4} {'truth':>10} {'clean基线':>12} {'drift基线':>12} {'drift偏差':>12}")
    for k, unit in (("Hs", "m"), ("Tz", "s"), ("Tc", "s"), ("Tp", "s")):
        tv = stat(truth_current, k)
        cv = stat(clean_params, k)
        dv = stat(drift_params, k)
        rel = (dv - tv) / tv * 100
        print(f"{k:>4} {tv:>10.3f} {cv:>12.3f} {dv:>12.3f} {rel:>+11.1f}%")

    # --- PART C: onshore band-exclusion recovery ---
    print()
    print("=" * 78)
    print("PART C — 岸上去掉低频端后重算 (从有漂移的传输谱恢复)")
    print("=" * 78)
    print("说明: keep_from=9 即不排除(=PART B的drift基线); 12/13/14 = 丢掉bin9..(keep-1)")
    print(f"{'keep_from':>9} {'起始周期':>9} {'Hs':>9} {'Hs vs truth_kept':>18} {'Tz':>8} {'Tc':>8} {'Tp':>8}")
    for c in cutoffs:
        hs = np.mean(drift_excl[c]["Hs"])
        tz = np.mean(drift_excl[c]["Tz"])
        tc = np.mean(drift_excl[c]["Tc"])
        tp = np.mean(drift_excl[c]["Tp"])
        tk = np.mean(truth_kept[c]["Hs"])
        rel = (hs - tk) / tk * 100
        start_T = 1.0 / (c * P.DF)
        print(f"{c:>9} {start_T:>8.1f}s {hs:>9.3f} {rel:>+16.1f}% {tz:>8.3f} {tc:>8.3f} {tp:>8.2f}")
    print()
    print("truth (current band 9-63) 参考: Hs=%.3f Tz=%.3f Tc=%.3f Tp=%.2f"
          % (stat(truth_current, "Hs"), stat(truth_current, "Tz"),
             stat(truth_current, "Tc"), stat(truth_current, "Tp")))

    # --- figure: (a) per-bin corruption localisation, (b) onshore recovery ---
    periods = 1.0 / freqs
    ratio_acc = S_acc_drift / S_acc_clean

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    ax = axes[0]
    ax.semilogy(periods, ratio_acc, "o-", color="#d62728", markersize=4)
    ax.axhline(1.0, color="black", lw=1, ls="--", label="无污染 (比值=1)")
    ax.axvline(16.7, color="green", lw=1.2, ls=":", label="拟议截断线 16.7s")
    ax.set_xlabel("周期 [s]  (每个点 = 一个频格 bin)")
    ax.set_ylabel("该频格被抬高的倍数  drift / clean")
    ax.set_title("(a) 漂移污染是局部的：只糊住最低3-4格\n"
                 "0.0439Hz(22.8s)漂移, 中高频完全不受影响(比值=1)")
    ax.invert_xaxis()  # long period (low freq, corrupted) on the left
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.3, which="both")
    ax.annotate("bin13(15.8s)起\n比值≈1.0", xy=(15.8, 1.05), xytext=(11, 30),
                fontsize=8, arrowprops=dict(arrowstyle="->", color="gray"))

    ax = axes[1]
    keep_periods = [1.0 / (c * P.DF) for c in cutoffs]
    hs_recovered = [np.mean(drift_excl[c]["Hs"]) for c in cutoffs]
    hs_true = stat(truth_current, "Hs")
    thresh = max(P.THRESH_HS_REL * hs_true, P.THRESH_HS_ABS_M)
    ax.axhspan(hs_true - thresh, hs_true + thresh, color="green", alpha=0.1,
               label=f"材料性阈值带 (±{thresh:.2f}m)")
    ax.axhline(hs_true, color="black", ls="--", lw=1, label=f"真值 Hs={hs_true:.2f}m")
    xlabels = [f"{p:.1f}s\n(bin{c})" for p, c in zip(keep_periods, cutoffs)]
    xpos = np.arange(len(cutoffs))
    ax.bar(xpos, hs_recovered, color="#1f77b4", width=0.55)
    for x, h in zip(xpos, hs_recovered):
        ax.text(x, h + 0.1, f"{h:.2f}", ha="center", fontsize=9)
    ax.set_xticks(xpos)
    ax.set_xticklabels(xlabels)
    ax.set_xlabel("岸上重算时保留的最低频格 (丢掉更低的)")
    ax.set_ylabel("重算出的 Hs [m]")
    ax.set_title("(b) 岸上剔除低频端即恢复：\n丢掉最低3格(→17.1s起) Hs 就回到真值")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.3, axis="y")

    fig.tight_layout()
    OUT_FIG.mkdir(parents=True, exist_ok=True)
    out = OUT_FIG / "problem2_supp_drift_locality.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"\nfigure written: {out}")


if __name__ == "__main__":
    main()
