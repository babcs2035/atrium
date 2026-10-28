"""d0008 の報告書の図（matplotlib）を docs/figures/ に SVG で書く．

使い方（matplotlib は pyproject の依存に無いので，実行のときだけ足す）:
    uv run --with matplotlib python scripts/tasks/d0008_figure.py docs/figures
入力は results/ の記録だけで，数値はここでは決めない．
書く図（報告書の図番号）:
  d0008_survey.svg           図 1  文献検索：分散 RAG 86 件の年ごとの件数と，3 つの観点の分類
  d0008_prior.svg            図 2  先行研究の中央との差と，隠す量と精度の変化（値は各論文の表から写す）
  d0008_system.svg           図 3  実験系と P2P で 1 問に答える流れ
  d0008_accuracy_gap.svg     図 4  (a) 3 つの構成の正答率，(b) 改良の段階ごとの中央との差
  d0008_gap_breakdown.svg    図 5  P2P の差の内訳（区分 a・b・c の寄与）
  d0008_search_exposure.svg  図 6  (a) 公開情報の種類ごとの探す費用と見つけやすさ，(b) 外に出た文書の割合
色は skill dataviz の既定のカテゴリ色（青 = P2P，橙 = 中央，青緑 = 3 つ目の系列）と，参照の灰．
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
P2P_COLOR, CENTRAL_COLOR, THIRD_COLOR, REF_COLOR = "#2a78d6", "#eb6834", "#1baf7a", "#8a8984"
LIGHT_BAR = "#c3c2b7"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e1e0d9"
WILSON_Z = 1.96
NON_INFERIORITY_MARGIN = 0.02

plt.rcParams.update(
    {
        "font.family": ["Noto Sans CJK JP", "sans-serif"],
        "font.size": 9,
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


def verdict(run: str) -> dict:
    """実行の事前登録の判定（verdict_original）を読む．"""
    return read_json(f"{run}/verdict.json")["verdict_original"]


def wilson_interval(p: float, n: int) -> tuple[float, float]:
    """正答率 p（n 問）の Wilson 95% 区間．"""
    denom = 1 + WILSON_Z**2 / n
    center = (p + WILSON_Z**2 / (2 * n)) / denom
    half = WILSON_Z * math.sqrt(p * (1 - p) / n + WILSON_Z**2 / (4 * n**2)) / denom
    return center - half, center + half


def signed(x: float, digits: int = 4) -> str:
    """符号つきの小数．本文の表記に合わせて負号は「−」にする．"""
    return f"{x:+.{digits}f}".replace("-", "−")


def survey(out: Path) -> None:
    """文献検索の結果：年ごとの件数と，3 つの観点での分類（本研究に当たる区分を青で強調）．"""
    stats = read_json("20261026_survey_search/stats.json")
    per_year = dict(stats["included_per_year"])  # 年不明（unknown）も含めて合計を 86 にそろえる
    coding = stats["coding"]
    # 観点ごとに，(記録の分類コード，図の語) の並び．本研究に当たる区分を強調する．
    facets = [
        (
            "データの分け方",
            coding["data_partition"],
            [("TOPIC", "話題・分野ごと"), ("SPLIT", "公開データの人工的な分割"), ("TIER", "端末とクラウドの 2 層"),
             ("OWNER", "持ち主ごと"), ("NA", "該当しない"), ("NS", "記載なし")],
            "OWNER",
        ),
        (
            "評価の形",
            coding["evaluation_form"],
            [("SIM", "模擬・1 台"), ("PHYS", "複数の物理的な機器"), ("NONE", "実験なし"), ("NS", "記載なし")],
            "PHYS",
        ),
        (
            "中央との比べ方",
            coding["comparison_to_centralized"],
            [("NONE", "比べない"), ("ACC", "正解に対する正誤"), ("WINRATE", "LLM の相対勝率"), ("OTHER", "そのほか"),
             ("NS", "記載なし")],
            "ACC",
        ),
    ]
    assert sum(per_year.values()) == stats["included"], "年ごとの合計が 86 件と合わない"
    for _title, _counts, _labels, _ in facets:
        assert sum(_counts.get(c, 0) for c, _ in _labels) == stats["included"], f"{_title} の合計が 86 件と合わない"
    fig, axes = plt.subplots(1, 4, figsize=(13.5, 2.9), gridspec_kw={"width_ratios": [1.25, 1.2, 1.1, 1.1]})
    ax = axes[0]
    years = sorted(per_year, key=lambda y: (y == "unknown", y))
    ax.bar([("年不明" if y == "unknown" else y) for y in years], [per_year[y] for y in years], color=P2P_COLOR, width=0.6)
    for i, y in enumerate(years):
        ax.text(i, per_year[y] + 0.8, str(per_year[y]), ha="center", va="bottom", color=INK)
    ax.set_title(f"(a) 年ごとの件数（計 {stats['included']} 件）", fontsize=9, loc="left")
    ax.tick_params(axis="x", labelsize=7.5)
    ax.set_ylabel("件数")
    ax.grid(axis="x", visible=False)
    for letter, ax, (title, counts, labels, highlight) in zip("bcd", axes[1:], facets):
        names = [name for _, name in labels][::-1]
        values = [counts.get(code, 0) for code, _ in labels][::-1]
        colors = [P2P_COLOR if code == highlight else LIGHT_BAR for code, _ in labels][::-1]
        ax.barh(names, values, color=colors, height=0.6)
        for i, v in enumerate(values):
            ax.text(v + 0.8, i, str(v), va="center", color=INK)
        ax.set_title(f"({letter}) {title}", fontsize=9, loc="left")
        ax.set_xlim(0, max(values) * 1.25)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("件数")
    fig.text(0.01, -0.04, "青：本研究の条件に当たる区分．持ち主ごとの分割かつ複数の物理的な機器の研究は 0 件．"
             "分類は題目と要旨による（results/20261026_survey_search）．", color=MUTED, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "d0008_survey.svg", bbox_inches="tight")
    plt.close(fig)


def accuracy(out: Path, ax=None) -> None:
    """中央・P2P・持ち主だけに聞く上限の正答率を，Wilson 95% 区間つきで並べる．"""
    a6 = verdict("p0007_a6_verdict")
    oracle = verdict("p0007_a5_verdict_oracle_vs_c1rrf_ce")
    rows = [
        ("中央（全メールを集める）", a6["accuracy_central"], a6["n_shared"], CENTRAL_COLOR),
        ("分散（P2P．主の構成）", a6["accuracy_p2p"], a6["n_shared"], P2P_COLOR),
        ("持ち主だけに聞く（評価専用の上限）", oracle["accuracy_p2p"], oracle["n_shared"], REF_COLOR),
    ]
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(5.6, 2.2))
    for i, (name, p, n, color) in enumerate(rows):
        lo, hi = wilson_interval(p, n)
        ax.plot([lo, hi], [i, i], color=color, linewidth=2.5)
        ax.plot(p, i, "o", color=color, markersize=8)
        ax.text(hi + 0.003, i, f"{p:.4f}", va="center", color=INK)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.set_xlim(0.74, 0.85)
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.set_xlabel("正答率（test 2,940 問．点は値，横線は 95% 区間）")
    ax.grid(axis="y", visible=False)
    ax.set_title(
        f"P2P は中央の {a6['accuracy_p2p'] / a6['accuracy_central']:.1%}（差 {signed(a6['difference'])}）．"
        "持ち主が分かれば中央と同程度以上",
        fontsize=9,
        loc="left",
    )
    if own:
        fig.tight_layout()
        fig.savefig(out / "d0008_accuracy.svg", bbox_inches="tight")
        plt.close(fig)


def gap_trend(out: Path, ax=None) -> None:
    """改良の段階ごとの中央との差と 95% 区間．各点に比べた中央の名前を添え，非劣性の幅の線を引く．"""
    stages = [
        ("RRF なし", "p0007_a1_test_verdict_c1", "中央：密ベクトル"),
        ("受信箱の中の\nRRF", "p0007_a2_verdict_rrf", "中央：RRF"),
        ("持ち主の特定\nに BM25", "p0007_a4_verdict_c1rrf", "中央：RRF"),
        ("cross-encoder\nで回答を選ぶ", "p0007_a5_verdict", "中央：RRF＋\n再ランク"),
        ("通常のプロンプト\n（主の構成）", "p0007_a6_verdict", "中央：RRF＋\n再ランク"),
        ("候補を\n4 つにする", "p0007_f7_verdict", "中央：RRF＋\n再ランク"),
    ]
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(8.6, 3.3))
    for i, (name, run, central) in enumerate(stages):
        v = verdict(run)
        lo, hi = v["ci95"]
        ax.plot([i, i], [lo, hi], color=P2P_COLOR, linewidth=2)
        ax.plot(i, v["difference"], "o", color=P2P_COLOR, markersize=7)
        ax.text(i + 0.12, v["difference"], signed(v["difference"]), va="center", color=INK, fontsize=8)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.axhline(-NON_INFERIORITY_MARGIN, color=CENTRAL_COLOR, linewidth=1, linestyle="--")
    ax.text(1.5, -NON_INFERIORITY_MARGIN + 0.003, "非劣性の幅（−0.02）", ha="center", color=CENTRAL_COLOR,
            fontsize=8)
    ax.set_xticks(range(len(stages)), [f"{s[0]}\n［{s[2]}］" for s in stages], fontsize=7)
    ax.set_ylabel("正答率の差（P2P − 中央）")
    ax.set_ylim(-0.12, 0.015)
    ax.grid(axis="x", visible=False)
    ax.set_title("差は比べる中央の強さで変わる．区間が −0.02 の線より上に入った段階は無い", fontsize=9, loc="left")
    if own:
        fig.tight_layout()
        fig.savefig(out / "d0008_gap_trend.svg", bbox_inches="tight")
        plt.close(fig)


def gap_breakdown(out: Path) -> None:
    """主の構成の差を区分 a・b・c の寄与に分け，中央との差と上限との差を並べる．"""
    labels = {
        "a_owner_out": "(a) 持ち主が候補に入らない",
        "b_owner_chosen": "(b) 持ち主の回答を採った",
        "c_owner_in_not_chosen": "(c) 持ち主が候補にいるのに別の回答を採った",
    }
    reports = [
        ("中央との差", read_json("p0007_a6_live/a6_report_vs_c1_rrf_ce.json")["segments_rule"], CENTRAL_COLOR),
        ("持ち主だけに聞く上限との差", read_json("p0007_a6_live/a6_report_vs_oracle.json")["segments_rule"], REF_COLOR),
    ]
    fig, ax = plt.subplots(figsize=(7.8, 3.0))
    height = 0.36
    keys = list(labels)
    for j, (name, seg, color) in enumerate(reports):
        for i, key in enumerate(keys):
            y = i + (j - 0.5) * height
            c = seg[key]["contribution_to_diff"]
            ax.barh(y, c, height=height * 0.92, color=color, label=name if i == 0 else None)
            ax.text(c + (0.001 if c >= 0 else -0.001), y, f"{signed(c, 3)}（{seg[key]['share']:.1%}）",
                    va="center", ha="left" if c >= 0 else "right", color=INK, fontsize=8)
    ax.set_yticks(range(len(keys)), [labels[k] for k in keys])
    ax.invert_yaxis()
    ax.axvline(0, color=MUTED, linewidth=0.8)
    ax.set_xlim(-0.07, 0.035)
    ax.set_xlabel("差への寄与（割合 ×（P2P の正答率 − 相手の正答率））．括弧は問の割合")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=2, frameon=False, fontsize=8)
    ax.set_title("代償は持ち主の特定 (a) と回答の選び方 (c) から来る．(b) は中央より上", fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(out / "d0008_gap_breakdown.svg", bbox_inches="tight")
    plt.close(fig)


def search_cost(out: Path, ax=None) -> None:
    """公開情報の種類ごとに，1 問あたりの要求数と hit@2 の関係を線で示す（30 台・test）．"""
    frontier = json.loads((RESULTS / "p0006_p2_iter2/frontier.json").read_text(encoding="utf-8"))
    metrics = json.loads((RESULTS / "p0006_p2_iter2/metrics.json").read_text(encoding="utf-8"))
    # 公開情報からの持ち主の特定（top-1）は，dev で選んだ設定の記録から読む．
    attribution = {}
    for entry in _walk_dicts(metrics):
        if entry.get("placement") == "n30" and entry.get("subset") == "all" and "attribution_top1" in entry:
            attribution[entry["method"]] = entry["attribution_top1"]
    # (方式，記録の固定の設定，図の語，色)．hybrid と語の統計は dev で選んだ V・w に固定する．
    series = [
        ("device_probe_body", {}, "埋め込みの重心", REF_COLOR),
        ("term_stats", {"V": 64000}, "語の統計", THIRD_COLOR),
        ("hybrid_summary", {"V": 64000, "w": 0.5}, "語の統計＋重心", P2P_COLOR),
    ]
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(5.6, 3.0))
    for method, fixed, name, color in series:
        pts = sorted(
            (p["requests_per_query"]["mean"], p["hit@2"])
            for p in frontier
            if p["placement"] == "n30" and p["split"] == "test" and p["subset"] == "all" and p["method"] == method
            and all(p["params"].get(k) == v for k, v in fixed.items())
        )
        xs, ys = zip(*pts)
        label = f"{name}（持ち主の特定 {attribution.get(method, float('nan')):.3f}）"
        ax.plot(xs, ys, "-o", color=color, linewidth=2, markersize=4, label=label)
    flood = next(p for p in frontier if p["placement"] == "n30" and p["split"] == "test" and p["method"] == "flood_score"
                 and p["subset"] == "all")
    ax.plot(flood["requests_per_query"]["mean"], flood["hit@2"], "s", color=CENTRAL_COLOR, markersize=8,
            label="何も公開せず全員に聞く")
    ax.set_xlabel("1 問あたりの要求数（問い合わせた機器の数 ＋ 本照会 2）")
    ax.set_ylabel("正解の受信箱が上位 2 に入る割合")
    ax.set_ylim(0.25, 0.85)
    ax.legend(loc="lower right", frameon=False, fontsize=8)
    ax.set_title("詳しい要約ほど少ない要求で見つかるが，持ち主も当てられやすい", fontsize=9, loc="left")
    if own:
        fig.tight_layout()
        fig.savefig(out / "d0008_search_cost.svg", bbox_inches="tight")
        plt.close(fig)


def _walk_dicts(x: object):
    """入れ子の JSON の中の dict を全て返す．"""
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _walk_dicts(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk_dicts(v)


def exposure(out: Path, ax=None) -> None:
    """構成ごとの，外に出た重複なしの文書の割合（73,772 通に対する割合）．"""
    e = read_json("20261019_155617_p0007_b_exposure/exposure.json")
    f6 = read_json("p0007_f6_verdict/f6_summary.json")
    rows = [
        ("中央（最初に全メールを集める）", e["central"]["fraction"], CENTRAL_COLOR),
        ("集めずに全員から上位 5 通ずつ取る", e["conditions"]["c1"]["fraction"], CENTRAL_COLOR),
        ("P2P 主の構成（回答の選び方を実機で行う）", f6["exposure_to_requester"]["a6_test_share"], P2P_COLOR),
        ("P2P 軽い構成（質問者が答える）", e["conditions"]["p4_1"]["fraction"], P2P_COLOR),
        ("P2P 軽い構成（GPU を持つ PC に委託）", e["conditions"]["p4_3"]["fraction"], P2P_COLOR),
        ("持ち主だけに聞く（評価専用）", e["conditions"]["c2"]["fraction"], REF_COLOR),
    ]
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(5.6, 2.6))
    for i, (name, frac, color) in enumerate(rows):
        ax.barh(i, frac, color=color, height=0.6)
        ax.text(frac + 0.01, i, f"{frac:.1%}", va="center", color=INK)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 1.12)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_xlabel(f"持ち主の機器の外に出た文書（全 {e['n_total_docs']:,} 通に対する割合．test 2,940 問）")
    ax.grid(axis="y", visible=False)
    ax.set_title("「集めない」は 0 か 1 かではなく，精度を上げる工夫ほど多くを出す", fontsize=9, loc="left")
    if own:
        fig.tight_layout()
        fig.savefig(out / "d0008_exposure.svg", bbox_inches="tight")
        plt.close(fig)


# 先行研究の値は論文の表から写す（出どころを各行のコメントに書く）．指標は研究ごとに違うので，
# 各研究の中の対（分散 − 中央，隠す前後）の差だけを示し，本研究の値とは並べない．
PRIOR_GAPS = [
    # (研究と指標，分散の値，中央の値)
    ("C-FedRAG  BioASQ 正答率", 79.61, 74.60),  # arXiv:2412.13163 Table 1（再ランクあり 対 MedCorp）
    ("C-FedRAG  平均 正答率", 72.51, 70.90),  # 同上
    ("C-FedRAG  PubMedQA 正答率", 65.40, 67.20),  # 同上
    ("DRAG  News F1（20 ピア）", 74.73, 76.66),  # arXiv:2505.00443 Table 1（TARW 対 CRAG）
    ("DRAG  MMLU F1（20 ピア）", 83.92, 85.75),  # 同上
    ("DRAG  Medical F1（20 ピア）", 90.58, 94.46),  # 同上
    ("FedRepRAG  ScienceQA 正答率", 88.14, 89.22),  # arXiv:2609.22162 Table 3（β = 0.5．対 Centralized IC-RAG）
    ("FedRepRAG  SLAKE 正答率", 60.70, 71.25),  # 同 Table 2
    ("FedRepRAG  KILT-NQ EM", 21.92, 35.11),  # 同 Table 5
    ("FedRepRAG  PubMedQA 正答率", 58.40, 82.20),  # 同 Table 4
]
PRIOR_HIDE = [
    # (研究，出す量の変化，正答率の変化 pt)
    ("FD-RAG  復元の成功率 92.4% → 8.8%", 53.0 - 53.9),  # arXiv:2605.27432 Table 5（局所差分プライバシー ε = 1.0．3 データの平均）
    ("FedRepRAG  文脈 406.98 → 5.00 トークン（ScienceQA）", 88.14 - 89.22),  # arXiv:2609.22162 Table 3
    ("FedRepRAG  文脈 92.93 → 5.00 トークン（SLAKE）", 60.70 - 71.25),  # 同 Table 2
    ("FedRepRAG  文脈 1,194 → 3 トークン（PubMedQA）", 58.40 - 82.20),  # 同 Table 4
]


def prior(out: Path) -> None:
    """先行研究の，中央との差（左）と，出す量を減らしたときの正答率の変化（右）．"""
    fig, (a, b) = plt.subplots(1, 2, figsize=(11.5, 3.6), gridspec_kw={"width_ratios": [1.1, 1.0]})
    rows = sorted(((n, d - c) for n, d, c in PRIOR_GAPS), key=lambda r: r[1])
    for i, (name, diff) in enumerate(rows):
        a.barh(i, diff, color=P2P_COLOR if diff >= 0 else REF_COLOR, height=0.6)
        a.text(diff + (0.5 if diff >= 0 else -0.5), i, signed(diff, 2), va="center",
               ha="left" if diff >= 0 else "right", color=INK, fontsize=8)
    a.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=8)
    a.axvline(0, color=MUTED, linewidth=0.8)
    a.set_xlim(-30, 10)
    a.set_xlabel("分散 − 中央（各論文の指標．pt）")
    a.set_title("(a) 先行研究の中央との差：正にも負にも散らばる", fontsize=9, loc="left")
    a.grid(axis="y", visible=False)
    rows = sorted(PRIOR_HIDE, key=lambda r: r[1])
    for i, (name, diff) in enumerate(rows):
        b.barh(i, diff, color=REF_COLOR, height=0.6)
        b.text(diff - 0.5, i, signed(diff, 2), va="center", ha="right", color=INK, fontsize=8)
    b.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=8)
    b.axvline(0, color=MUTED, linewidth=0.8)
    b.set_xlim(-30, 2)
    b.set_xlabel("出す量を減らす前後の正答率の変化（pt）")
    b.set_title("(b) 出す量を減らすと，正答率は落ちる（減り方は研究ごとに違う）", fontsize=9, loc="left")
    b.grid(axis="y", visible=False)
    fig.text(0.01, -0.04, "値は各論文の表から写した．指標・データ・比べた中央が研究ごとに違うので，研究どうし，本研究の値とは並べて比べない．",
             color=MUTED, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "d0008_prior.svg", bbox_inches="tight")
    plt.close(fig)


def _box(ax, x: float, y: float, w: float, h: float, text: str, color: str = "#ffffff", edge: str = MUTED,
         fontsize: float = 8.5) -> None:
    """文字つきの角丸の箱を描く．"""
    from matplotlib.patches import FancyBboxPatch

    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.04", fc=color, ec=edge, lw=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize, color=INK)


def _arrow(ax, x0: float, y0: float, x1: float, y1: float, label: str = "", dashed: bool = False) -> None:
    """矢印と，あれば中点の近くの説明を描く．"""
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops={"arrowstyle": "->", "color": MUTED, "lw": 1.2, "linestyle": "--" if dashed else "-"})
    if label:
        ax.text((x0 + x1) / 2, (y0 + y1) / 2 + 0.035, label, ha="center", va="bottom", fontsize=7.5, color=MUTED)


def question(out: Path) -> None:
    """§1 の問い：中央に集める構成と，集めない構成の対比．"""
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.grid(False)
    ax.text(0.02, 0.97, "中央：集める", fontsize=9.5, color=CENTRAL_COLOR, va="top", fontweight="bold")
    _box(ax, 0.02, 0.66, 0.14, 0.2, "質問者")
    _box(ax, 0.38, 0.66, 0.28, 0.2, "全員のメールの索引\n（1 台に集約）", color="#fdeee8", edge=CENTRAL_COLOR)
    _box(ax, 0.78, 0.66, 0.2, 0.2, "LLM が回答")
    _arrow(ax, 0.16, 0.76, 0.38, 0.76)
    _arrow(ax, 0.66, 0.76, 0.78, 0.76)
    ax.text(0.38, 0.58, "外に出る文書：全 73,772 通", fontsize=7.5, color=MUTED)
    ax.text(0.02, 0.46, "分散：集めない", fontsize=9.5, color=P2P_COLOR, va="top", fontweight="bold")
    _box(ax, 0.02, 0.14, 0.14, 0.2, "質問者")
    _box(ax, 0.38, 0.14, 0.28, 0.2, "各 PC の受信箱\n（メールは置いたまま）", color="#e8f0fb", edge=P2P_COLOR)
    _box(ax, 0.78, 0.14, 0.2, 0.2, "回答を 1 つ選ぶ")
    _arrow(ax, 0.16, 0.24, 0.38, 0.24, "公開情報で絞る")
    _arrow(ax, 0.66, 0.24, 0.78, 0.24, "回答")
    ax.text(0.02, 0.05, "失うもの：精度・時間・計算　　差し出すもの：公開情報・文書の一部", fontsize=7.5, color=MUTED)
    fig.savefig(out / "d0008_question.svg", bbox_inches="tight")
    plt.close(fig)


def system(out: Path) -> None:
    """§4 の実験系：質問者，30 台の PC，P2P で 1 問に答える 4 つの手順（番号は矢印に付け，手順は凡例にする）．"""
    from matplotlib.patches import FancyBboxPatch

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.grid(False)
    _box(ax, 0.02, 0.55, 0.22, 0.26, "質問者・中央の 1 台\nRTX 3060")
    ax.add_patch(FancyBboxPatch((0.5, 0.2), 0.48, 0.74, boxstyle="round,pad=0.02,rounding_size=0.04", fc="#f4f8fe",
                                ec=P2P_COLOR, lw=1))
    ax.text(0.52, 0.91, "30 台の PC（各 5 受信箱）", fontsize=8.5, color=P2P_COLOR, va="top")
    _box(ax, 0.54, 0.58, 0.4, 0.2, "GPU を持つ PC 10 台\n8B のモデルで回答")
    _box(ax, 0.54, 0.26, 0.4, 0.2, "CPU だけの PC 20 台\n回答は GPU の PC に委託")
    _arrow(ax, 0.74, 0.46, 0.74, 0.58, dashed=True)
    _arrow(ax, 0.24, 0.74, 0.50, 0.74)
    _arrow(ax, 0.50, 0.64, 0.24, 0.64)
    _arrow(ax, 0.24, 0.58, 0.50, 0.40)
    for x, y, num in ((0.37, 0.77, "1"), (0.37, 0.60, "2"), (0.31, 0.45, "3")):
        ax.text(x, y, num, ha="center", va="center", fontsize=8, color="#ffffff",
                bbox={"boxstyle": "circle,pad=0.25", "fc": P2P_COLOR, "ec": P2P_COLOR})
    ax.text(0.02, 0.40, "1  公開情報で問い合わせ先を 24 台に絞る\n2  probe で得点を集め，候補の受信箱を 3 つ選ぶ\n"
            "3  候補に回答を依頼（委託は点線）\n4  候補の回答を cross-encoder で採点し，最大の 1 つを採る",
            fontsize=7.5, color=INK, va="top", linespacing=1.6)
    fig.savefig(out / "d0008_system.svg", bbox_inches="tight")
    plt.close(fig)


def accuracy_gap(out: Path) -> None:
    """図 4：(a) 3 つの構成の正答率，(b) 改良の段階ごとの中央との差．"""
    fig, (a, b) = plt.subplots(1, 2, figsize=(12.5, 3.5), gridspec_kw={"width_ratios": [1.0, 1.7]})
    accuracy(out, a)
    gap_trend(out, b)
    a.set_title("(a) 3 つの構成の正答率", fontsize=9, loc="left")
    b.set_title("(b) 差は比べる中央の強さで変わる（区間が −0.02 の線より上に入った段階は無い）", fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(out / "d0008_accuracy_gap.svg", bbox_inches="tight")
    plt.close(fig)


def search_exposure(out: Path) -> None:
    """図 6：(a) 公開情報の種類ごとの探す費用と見つけやすさ，(b) 外に出た文書の割合．"""
    fig, (a, b) = plt.subplots(1, 2, figsize=(12.0, 3.2), gridspec_kw={"width_ratios": [1.0, 1.1]})
    search_cost(out, a)
    exposure(out, b)
    for ax, letter in ((a, "(a)"), (b, "(b)")):
        ax.set_title(f"{letter} {ax.get_title(loc='left')}", fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(out / "d0008_search_exposure.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    """引数の出力先に，報告書で使う図を書く．"""
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/figures")
    out.mkdir(parents=True, exist_ok=True)
    survey(out)
    prior(out)
    system(out)
    accuracy_gap(out)
    gap_breakdown(out)
    search_exposure(out)


if __name__ == "__main__":
    main()
