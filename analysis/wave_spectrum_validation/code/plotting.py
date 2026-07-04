"""
T8 -- Figures for problems 2, 3, 5, 8 (problem 4 is reported as a table, not
a figure -- see report). shared_plotting is not installed on this machine
(checked at T0), so this uses plain matplotlib with a consistent minimal style.

Every Hs figure draws the EF3 materiality band (+-max(5%*Hs_true, 0.10m))
around the truth line so "material vs negligible" is visible at a glance.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import params as P

# CJK font support (macOS: PingFang HK / Heiti TC / Songti SC are available;
# without this all Chinese labels render as missing-glyph boxes).
matplotlib.rcParams["font.sans-serif"] = ["PingFang HK", "Heiti TC", "Arial Unicode MS"]
matplotlib.rcParams["axes.unicode_minus"] = False

CODE_DIR = Path(__file__).resolve().parent
OUT_CSV = CODE_DIR.parent / "outputs" / "csv"
OUT_FIG = CODE_DIR.parent / "outputs" / "figures"
OUT_FIG.mkdir(parents=True, exist_ok=True)

COLORS = {"baseline": "#666666", "lightweight": "#1f77b4", "reference": "#d62728",
          "baseline_fixed": "#666666", "adaptive": "#2ca02c",
          "current": "#666666", "extended": "#2ca02c"}
MARKERS = {"baseline": "o", "lightweight": "s", "reference": "^",
           "baseline_fixed": "o", "adaptive": "s",
           "current": "o", "extended": "s"}


def _materiality_band(ax, x, hs_true):
    thresh = max(P.THRESH_HS_REL * hs_true, P.THRESH_HS_ABS_M)
    ax.axhspan(hs_true - thresh, hs_true + thresh, color="green", alpha=0.08,
               label=f"materiality band (±{thresh:.3f}m)")
    ax.axhline(hs_true, color="black", linestyle="--", linewidth=1, label="truth Hs")


def plot_problem2():
    df = pd.read_csv(OUT_CSV / "problem2_results.csv")
    methods = ["baseline", "lightweight", "reference"]
    for sea_state in df["sea_state"].unique():
        sub_sea = df[df["sea_state"] == sea_state]
        pert_types = sub_sea["perturbation"].unique()
        fig, axes = plt.subplots(1, len(pert_types), figsize=(6 * len(pert_types), 4.5))
        if len(pert_types) == 1:
            axes = [axes]
        for ax, pert in zip(axes, pert_types):
            sub = sub_sea[sub_sea["perturbation"] == pert].sort_values("perturbation_value")
            hs_true = sub["truth_Hs"].iloc[0]
            _materiality_band(ax, sub["perturbation_value"], hs_true)
            for m in methods:
                ax.errorbar(sub["perturbation_value"], sub[f"{m}_Hs_mean"],
                            yerr=sub[f"{m}_Hs_bias_ci95"], label=m,
                            color=COLORS[m], marker=MARKERS[m], capsize=3)
            xlabel = {"dc_offset": "残余重力DC偏置 [m/s²]",
                      "edge_drift_0.03Hz": "0.03Hz正弦漂移幅值 [m/s²]",
                      "drift_freq_sweep": "漂移频率 [Hz] (幅值固定0.15 m/s²)"}.get(pert, pert)
            ax.set_xlabel(xlabel)
            ax.set_ylabel("Hs [m]")
            ax.set_title(f"问题2: {sea_state} — {pert}")
            ax.legend(fontsize=8)
            ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(OUT_FIG / f"problem2_{sea_state}.png", dpi=140)
        plt.close(fig)
    print("problem2 figures written")


def plot_problem3():
    df = pd.read_csv(OUT_CSV / "problem3_results.csv")
    methods = ["baseline", "lightweight", "reference"]
    n_spikes_values = sorted(v for v in df["n_spikes"].unique() if v > 0)
    cmap = plt.get_cmap("viridis")
    hs_true = df["truth_Hs"].iloc[0]

    fig, axes = plt.subplots(1, len(methods), figsize=(6 * len(methods), 4.5), sharey=True)
    for ax, m in zip(axes, methods):
        _materiality_band(ax, df["spike_amp_sigma"], hs_true)
        for i, n_spikes in enumerate(n_spikes_values):
            sub = df[df["n_spikes"] == n_spikes].sort_values("spike_amp_sigma")
            color = cmap(i / max(1, len(n_spikes_values) - 1))
            ax.plot(sub["spike_amp_sigma"], sub[f"{m}_Hs_mean"],
                    color=color, marker="o", label=f"n_spikes={n_spikes}")
        ax.set_xlabel("尖峰幅值 [× 信号标准差 σ]")
        ax.set_title(f"问题3: {m}")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("Hs [m]")
    axes[-1].legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT_FIG / "problem3_despiking.png", dpi=140)
    plt.close(fig)
    print("problem3 figure written")


def plot_problem5():
    df = pd.read_csv(OUT_CSV / "problem5_results.csv")
    methods = ["baseline_fixed", "adaptive"]

    # (a) sea-state sweep
    sub = df[df["experiment"] == "sea_state_sweep"].copy()
    sub["label"] = sub["hs_target"].astype(str) + "m/" + sub["tp_target"].astype(str) + "s"
    fig, ax = plt.subplots(figsize=(7, 5))
    x = np.arange(len(sub))
    width = 0.35
    for i, m in enumerate(methods):
        rel_err = 100 * (sub[f"{m}_Hs_mean"] - sub["truth_Hs"]) / sub["truth_Hs"]
        ax.bar(x + (i - 0.5) * width, rel_err, width, label=m, color=COLORS[m])
    ax.axhline(0, color="black", linewidth=1)
    ax.axhline(P.THRESH_HS_REL * 100, color="green", linestyle="--", alpha=0.5, label="±5% materiality")
    ax.axhline(-P.THRESH_HS_REL * 100, color="green", linestyle="--", alpha=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(sub["label"])
    ax.set_ylabel("Hs 相对误差 [%]")
    ax.set_xlabel("海况 (Hs/Tp)")
    ax.set_title("问题5a: 固定窗修正(1.63) vs 自适应 — 不同海况")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(OUT_FIG / "problem5a_sea_states.png", dpi=140)
    plt.close(fig)

    # (b) spectral shape (narrowband vs broadband)
    sub = df[df["experiment"] == "spectral_shape"].copy()
    fig, ax = plt.subplots(figsize=(6, 5))
    x = np.arange(len(sub))
    for i, m in enumerate(methods):
        rel_err = 100 * (sub[f"{m}_Hs_mean"] - sub["truth_Hs"]) / sub["truth_Hs"]
        ax.bar(x + (i - 0.5) * width, rel_err, width, label=m, color=COLORS[m])
    ax.axhline(0, color="black", linewidth=1)
    ax.set_xticks(x)
    ax.set_xticklabels(sub["shape_label"])
    ax.set_ylabel("Hs 相对误差 [%]")
    ax.set_title("问题5b: 窄带(非高斯代理) vs 宽带谱")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(OUT_FIG / "problem5b_spectral_shape.png", dpi=140)
    plt.close(fig)

    # (c) short record
    sub = df[df["experiment"] == "short_record"].sort_values("n_segments")
    fig, ax = plt.subplots(figsize=(6, 5))
    hs_true = sub["truth_Hs"].iloc[0]
    _materiality_band(ax, sub["n_segments"], hs_true)
    for m in methods:
        ax.errorbar(sub["n_segments"], sub[f"{m}_Hs_mean"],
                     yerr=sub[f"{m}_Hs_bias_ci95"], label=m,
                     color=COLORS[m], marker=MARKERS[m], capsize=3)
    ax.set_xlabel("Welch 段数 (固件默认=21)")
    ax.set_ylabel("Hs [m]")
    ax.set_title("问题5c: 短记录 (段数减少) — 固定 vs 自适应窗修正")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_FIG / "problem5c_short_record.png", dpi=140)
    plt.close(fig)
    print("problem5 figures written")


def plot_problem8():
    df = pd.read_csv(OUT_CSV / "problem8_results.csv")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    ax = axes[0]
    x = np.arange(len(df))
    width = 0.35
    ax.bar(x - width / 2, df["missed_energy_current_pct"], width, label="现带(bin9-63, 3.25-22.8s)漏能%")
    ax.bar(x + width / 2, df["missed_energy_extended_pct"], width, label="扩带(bin9-102, 2-22.8s)漏能%")
    ax.axhline(5.0, color="green", linestyle="--", alpha=0.6, label="5% 材料性阈值")
    ax.set_xticks(x)
    ax.set_xticklabels(df["sea_state"])
    ax.set_ylabel("相对全频段真值漏掉的能量 [%] (以Hs² ~ m0计)")
    ax.set_title("问题8a: 频段截断漏掉的能量占比")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")

    ax = axes[1]
    for i, m in enumerate(["current", "extended"]):
        rel_err = 100 * (df[f"{m}_Hs_mean"] - df["truth_full_Hs"]) / df["truth_full_Hs"]
        ax.bar(x + (i - 0.5) * width, rel_err, width, label=f"{m} band", color=COLORS[m])
    ax.axhline(0, color="black", linewidth=1)
    ax.axhline(-P.THRESH_HS_REL * 100, color="green", linestyle="--", alpha=0.5, label="-5% materiality")
    ax.set_xticks(x)
    ax.set_xticklabels(df["sea_state"])
    ax.set_ylabel("Hs 相对全频段真值的误差 [%]")
    ax.set_title("问题8b: 现带 vs 扩带 Hs 相对全频段真值的偏差")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")

    fig.tight_layout()
    fig.savefig(OUT_FIG / "problem8_band_extension.png", dpi=140)
    plt.close(fig)
    print("problem8 figure written")


if __name__ == "__main__":
    plot_problem2()
    plot_problem3()
    plot_problem5()
    plot_problem8()
    print("\nAll figures written to", OUT_FIG)
