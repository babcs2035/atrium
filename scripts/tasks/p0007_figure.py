"""p0007 の最終報告の図（matplotlib）を docs/figures/ に SVG で書く．

使い方（matplotlib は pyproject の依存に無いので，実行のときだけ足す）:
    uv run --with matplotlib python scripts/tasks/p0007_figure.py docs/figures
入力は results/ の記録（verdict.json・d_summary.json・freshness.json・failure.json・join.json）．
書く図:
  p0007_accuracy_cost.svg   A：中央と P2P の正答率（Wilson 95% 区間）と，1 問あたりのノードへの要求数との交換関係
  p0007_load.svg            D：parallel ごとの処理量と e2e の p95（3 回の中央値と最小〜最大）
  p0007_time.svg            C：答えられるまでの時間の分布，E1：窓の中の積分，E2：参加の時間
色は skill dataviz の既定のカテゴリ色（青 = P2P，橙 = 中央，青緑 = 準集権）．数値は記録から読み，ここでは決めない．
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RESULTS = Path("results")
P2P_COLOR, CENTRAL_COLOR, SEMI_COLOR, REF_COLOR = "#2a78d6", "#eb6834", "#1baf7a", "#8a8984"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e3e2dc"
WILSON_Z = 1.96
N_TEST_QUESTIONS = 2940

plt.rcParams.update(
    {
        "font.family": ["Noto Sans CJK JP", "sans-serif"],
        "font.size": 10,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "svg.fonttype": "none",
    }
)


def read_json(path: str) -> dict:
    """results/ 以下の JSON を読む．"""
    return json.loads((RESULTS / path).read_text(encoding="utf-8"))


def wilson_interval(p: float, n: int) -> tuple[float, float]:
    """正答率 p（n 問）の Wilson 95% 区間．"""
    denom = 1 + WILSON_Z**2 / n
    center = (p + WILSON_Z**2 / (2 * n)) / denom
    half = WILSON_Z * math.sqrt(p * (1 - p) / n + WILSON_Z**2 / (4 * n**2)) / denom
    return center - half, center + half


def accuracy_cost(out: Path) -> None:
    """A：正答率（区間つき）と，ノードへの要求数に対する正答率．"""
    a1 = read_json("p0007_a1_test_verdict_c1/verdict.json")["verdict_original"]
    a2 = read_json("p0007_a2_verdict_rrf/verdict.json")["verdict_original"]
    a4 = read_json("p0007_a4_verdict_c1rrf/verdict.json")["verdict_original"]
    # (表示名，正答率，ノードへの要求数/問，中央か，比べる相手の中央の条件の x 位置)
    conditions = [
        ("C1\n（中央・RRF なし）", a1["accuracy_central"], 30, True),
        ("P2P（A1）\nhybrid_probe", a1["accuracy_p2p"], 19, False),
        ("C1-RRF\n（中央・RRF）", a2["accuracy_central"], 0, True),
        ("P2P-RRF（A2）\nhybrid_probe", a2["accuracy_p2p"], 19, False),
        ("P2P-BM25（A4）\nbm25_probe", a4["accuracy_p2p"], 27, False),
    ]
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(11.5, 4.4), gridspec_kw={"width_ratios": [1.3, 1]}
    )
    for i, (_label, acc, _req, central) in enumerate(conditions):
        lo, hi = wilson_interval(acc, N_TEST_QUESTIONS)
        color = CENTRAL_COLOR if central else P2P_COLOR
        ax1.errorbar(i, acc, yerr=[[acc - lo], [hi - acc]], fmt="o", color=color, capsize=3, ms=7)
        ax1.annotate(
            f"{acc:.4f}",
            (i, hi),
            textcoords="offset points",
            xytext=(0, 5),
            ha="center",
            color=INK,
            fontsize=9,
        )
    ax1.set_xticks(range(len(conditions)), [c[0] for c in conditions], fontsize=8.5)
    ax1.set_ylabel("正答率（test 2,940 問．Wilson 95% 区間）")
    ax1.set_ylim(0.62, 0.80)
    ax1.set_title(
        "(a) 同じ改良を入れた組の差：A1 −0.0303・A2 −0.0884・A4 −0.0500", fontsize=10, loc="left"
    )
    ax1.grid(axis="x", visible=False)

    # 点が近い C1 と P2P-RRF のラベルが重ならないよう，条件ごとに置き場所を変える
    label_offsets = [(6, -12), (6, -3), (6, -3), (-8, 7), (6, -3)]
    for (label, acc, req, central), offset in zip(conditions, label_offsets, strict=True):
        ax2.scatter(
            req,
            acc,
            s=60,
            color=CENTRAL_COLOR if central else P2P_COLOR,
            marker="s" if central else "o",
            zorder=3,
        )
        ax2.annotate(
            label.replace("\n", " "),
            (req, acc),
            textcoords="offset points",
            xytext=offset,
            fontsize=8,
            color=INK,
            ha="right" if offset[0] < 0 else "left",
        )
    ax2.set_xlabel("1 問あたりのノードへの要求数（中央は索引が質問者の 1 か所）")
    ax2.set_ylabel("正答率")
    ax2.set_xlim(-2, 46)
    ax2.set_ylim(0.62, 0.80)
    ax2.set_title("(b) 要求数と正答率", fontsize=10, loc="left")
    ax2.scatter([], [], color=CENTRAL_COLOR, marker="s", label="中央")
    ax2.scatter([], [], color=P2P_COLOR, marker="o", label="P2P")
    ax2.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "p0007_accuracy_cost.svg")
    plt.close(fig)


def load_curves(out: Path) -> None:
    """D：parallel ごとの処理量と p95．3 回の中央値の線と，最小〜最大の帯．"""
    summary = read_json("p0007_d_verdict/d_summary.json")
    clean = read_json("p0007_d_verdict_clean/d_summary.json")
    series = [
        ("central", "中央（制御点 1 枚）", CENTRAL_COLOR, "-", "s"),
        ("semi", "準集権（生成を 10 枚）", SEMI_COLOR, "-", "^"),
        ("p2p", "P2P（bm25_probe・委託）", P2P_COLOR, "-", "o"),
        ("p2p_ref", "P2P 参照（hybrid_probe・B1）", REF_COLOR, "--", "D"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    for ax, key, ylabel in [
        (axes[0], "throughput_qps", "処理量（問/秒）"),
        (axes[1], "e2e_p95_s", "e2e の p95（秒）"),
    ]:
        for cond, label, color, style, marker in series:
            parallels = sorted(int(p) for p in summary[cond])
            med = [summary[cond][str(p)][key]["median"] for p in parallels]
            lo = [summary[cond][str(p)][key]["min"] for p in parallels]
            hi = [summary[cond][str(p)][key]["max"] for p in parallels]
            ax.fill_between(parallels, lo, hi, color=color, alpha=0.15, linewidth=0)
            ax.plot(parallels, med, style, color=color, marker=marker, ms=5, lw=1.8, label=label)
        # 事後の感度分析（別の研究の GPU の使用が続く中の parallel 30．判定に使わない）
        for cond, _label, color, _style, _marker in series[:3]:
            vals = clean[cond]["30"][key]
            ax.errorbar(
                31.5,
                vals["median"],
                yerr=[[vals["median"] - vals["min"]], [vals["max"] - vals["median"]]],
                fmt="o",
                mfc="white",
                color=color,
                capsize=2,
                ms=5,
            )
        ax.set_xlabel("parallel（同時に流す質問の数）")
        ax.set_ylabel(ylabel)
        ax.set_xticks([1, 4, 8, 16, 30])
        ax.set_ylim(bottom=0)
    axes[0].set_title("(a) 処理量．白抜きは parallel 30 の事後の感度分析", fontsize=10, loc="left")
    axes[1].set_title("(b) p95 は P2P が中央より長い", fontsize=10, loc="left")
    axes[0].legend(frameon=False, fontsize=8.5, loc="upper left")
    fig.tight_layout()
    fig.savefig(out / "p0007_load.svg")
    plt.close(fig)


def time_distributions(out: Path) -> None:
    """C：答えられるまでの時間の分布．E1：窓の中の積分．E2：参加の時間．"""
    fresh = read_json("p0007_c_freshness_quiet/freshness.json")
    fail = read_json("p0007_e1_failure/failure.json")
    join = read_json("p0007_e2_join/join.json")
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))

    # (a) C：メールごとの答えられるまでの時間の累積分布（答えられなかった分は 1 に届かない）
    ax = axes[0]
    n_arrivals = fresh["params"]["n_arrivals"]
    curves = [
        ("P2P（direct）", [r["direct_s"] for r in fresh["p2p"]["rows"]], P2P_COLOR, "-"),
        ("中央 T = 0", fresh["central"]["0"]["times_s"], CENTRAL_COLOR, "-"),
        ("中央 T = 60 秒", fresh["central"]["60"]["times_s"], CENTRAL_COLOR, "--"),
        ("中央 T = 600 秒", fresh["central"]["600"]["times_s"], CENTRAL_COLOR, ":"),
    ]
    for label, times, color, style in curves:
        xs = sorted(t for t in times if t is not None)
        ys = [(i + 1) / n_arrivals for i in range(len(xs))]
        ax.step(xs, ys, where="post", color=color, ls=style, lw=1.8, label=label)
    ax.set_xscale("log")
    ax.set_xlabel("新しいメールが届いてから答えられるまで（秒）")
    ax.set_ylabel("累積の割合（289 通）")
    ax.set_title("(a) C：追従の時間（測り直しの値）", fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=2)

    # (b) E1：窓の中の答えられた割合の積分（中央は停止 45 秒を仮定した値）
    ax = axes[1]
    mode_names = {"availability": "API", "container": "コンテナ"}
    labels = [f"{mode_names[r['mode']]}\nk={r['k']}" for r in fail["p2p"]]
    ax.bar(range(len(labels)), [r["integral"] for r in fail["p2p"]], color=P2P_COLOR, width=0.65)
    ax.axhline(fail["central"]["integral"], color=CENTRAL_COLOR, lw=1.8)
    ax.text(
        -0.4,
        fail["central"]["integral"] + 1.2,
        "中央 22.8（停止 45 秒の仮定）",
        color=INK,
        ha="left",
        fontsize=8.5,
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5},
    )
    ax.set_xticks(range(len(labels)), labels, fontsize=7.5)
    ax.set_ylabel("窓の中の積分（割合 × 秒）")
    ax.set_title("(b) E1：窓の中の積分（P2P の棒と中央の線）", fontsize=10, loc="left")
    ax.grid(axis="x", visible=False)

    # (c) E2：加わった 3 台の，答えられるまでの時間（対数軸）
    ax = axes[2]
    hosts = [
        d["host"].rsplit(".", 2)[-2] + "." + d["host"].rsplit(".", 1)[-1]
        for d in join["p2p"]["devices"]
    ]
    p2p_s = [d["published_s"] for d in join["p2p"]["devices"]]
    central_s = [d["indexed_s"] for d in join["central"]["devices"]]
    xs = range(len(hosts))
    ax.bar(
        [x - 0.18 for x in xs], p2p_s, width=0.36, color=P2P_COLOR, label="P2P（公開情報の作成）"
    )
    ax.bar(
        [x + 0.18 for x in xs],
        central_s,
        width=0.36,
        color=CENTRAL_COLOR,
        label="中央（転送と埋め込み）",
    )
    ax.set_yscale("log")
    ax.set_xticks(list(xs), hosts)
    ax.set_xlabel("加わったデバイス（192.168.x.y）")
    ax.set_ylabel("加わってから答えられるまで（秒）")
    ax.set_title("(c) E2：参加．P2P は CPU の埋め込みが時間の全て", fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(out / "p0007_time.svg")
    plt.close(fig)


def main(out: Path) -> None:
    """3 枚の図を out に書く．"""
    out.mkdir(parents=True, exist_ok=True)
    accuracy_cost(out)
    load_curves(out)
    time_distributions(out)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
