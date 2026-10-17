"""p0006 の標準の図：データごとのフロンティア（要求数と再現率）を，外部の描画ライブラリなしで 1 枚の SVG に書く．

使い方:
    uv run python scripts/tasks/p0006_figure.py docs/figures/p0006_frontier.svg
入力は results/ の frontier.json（EnronQA は P2 の 2 回分，FeB4RAG は none の正規化，MedRAG）．test・全問の点から，族ごとに
「要求数がその値以下の点の最良の再現率」の階段を描く．全員に聞く方式（flood_score）は点で示す．
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

PANEL_W, PANEL_H = 360, 280
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 56, 16, 36, 48
GAP = 24
# 色覚の差で見分けやすい 8 色（Okabe-Ito）
COLORS = {
    "flood_score": "#000000",
    "reference": "#999999",
    "body_baseline": "#0072B2",
    "term_stats": "#D55E00",
    "hybrid_summary": "#009E73",
    "sample_summary": "#CC79A7",
    "adaptive_cutoff": "#E69F00",
    "hierarchy": "#56B4E9",
    "overlay_walk": "#F0E442",
}
LABELS = {
    "reference": "device_probe など（ヘッダー込み）",
    "body_baseline": "本文の重心",
    "term_stats": "語の統計（CORI）",
    "hybrid_summary": "語の統計 + 重心",
    "sample_summary": "標本",
    "adaptive_cutoff": "問ごとの打ち切り",
    "hierarchy": "階層化",
    "overlay_walk": "近傍の転送",
}
SOURCES = {
    "EnronQA（n30．hit@2）": (("p0006_p2_frontier", "p0006_p2_iter2"), "n30", "source_recall@2", 2),
    "FeB4RAG（再現率@9）": (("p0006_p3_feb4rag",), "feb4rag_none", "source_recall@9", 9),
    "MedRAG（再現率@3．シャード）": (("p0006_p3_medrag",), "medrag", "source_recall@3", 3),
}


def load_points(
    runs: tuple[str, ...], placement: str, key: str
) -> dict[str, list[tuple[float, float]]]:
    """族 → (要求数の平均, 再現率) の点（test・全問）．"""
    seen: set[str] = set()
    out: dict[str, list[tuple[float, float]]] = {}
    for run in runs:
        for r in json.loads((Path("results") / run / "frontier.json").read_text(encoding="utf-8")):
            if r["placement"] != placement or r["split"] != "test" or r["subset"] != "all":
                continue
            ident = json.dumps([r["method"], r["params"]], sort_keys=True)
            if ident in seen:
                continue
            seen.add(ident)
            family = "flood_score" if r["method"] == "flood_score" else r["family"]
            out.setdefault(family, []).append((r["requests_per_query"]["mean"], r[key]))
    return out


def staircase(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """要求数の昇順に，ここまでの最良の再現率が更新される点だけを返す．"""
    best = -1.0
    out: list[tuple[float, float]] = []
    for x, y in sorted(points):
        if y > best:
            out.append((x, y))
            best = y
    return out


def panel(title: str, data: dict[str, list[tuple[float, float]]], x0: int) -> list[str]:
    """1 データ分のパネル．軸は要求数（0 から最大）と再現率（0 から 1）．"""
    xmax = max(x for pts in data.values() for x, _ in pts)
    inner_w, inner_h = PANEL_W - MARGIN_L - MARGIN_R, PANEL_H - MARGIN_T - MARGIN_B

    def sx(x: float) -> float:
        return x0 + MARGIN_L + inner_w * x / xmax

    def sy(y: float) -> float:
        return MARGIN_T + inner_h * (1 - y)

    parts = [
        f'<text x="{x0 + MARGIN_L}" y="20" font-size="13" font-weight="bold">{escape(title)}</text>'
    ]
    for t in range(0, 6):
        y = t / 5
        parts.append(
            f'<line x1="{sx(0)}" y1="{sy(y)}" x2="{sx(xmax)}" y2="{sy(y)}" stroke="#dddddd"/>'
        )
        parts.append(
            f'<text x="{sx(0) - 6}" y="{sy(y) + 4}" font-size="10" text-anchor="end">{y:.1f}</text>'
        )
    step = max(1, round(xmax / 5))
    for t in range(0, int(xmax) + 1, step):
        parts.append(
            f'<text x="{sx(t)}" y="{MARGIN_T + inner_h + 16}" font-size="10" text-anchor="middle">{t}</text>'
        )
    parts.append(
        f'<text x="{sx(xmax / 2)}" y="{PANEL_H - 8}" font-size="11" text-anchor="middle">1 問あたりの要求数</text>'
    )
    for family, pts in data.items():
        color = COLORS.get(family, "#666666")
        if family == "flood_score":
            for x, y in pts:
                parts.append(f'<circle cx="{sx(x)}" cy="{sy(y)}" r="5" fill="{color}"/>')
            continue
        stairs = staircase(pts)
        path = [f"M{sx(stairs[0][0]):.1f},{sy(stairs[0][1]):.1f}"]
        for (_, prev_y), (x, y) in zip(stairs, stairs[1:], strict=False):
            path.append(f"L{sx(x):.1f},{sy(prev_y):.1f} L{sx(x):.1f},{sy(y):.1f}")
        parts.append(f'<path d="{" ".join(path)}" fill="none" stroke="{color}" stroke-width="2"/>')
    return parts


def main(out: Path) -> None:
    """3 データのパネルと凡例を 1 枚の SVG に書く．"""
    panels = [(t, load_points(runs, pl, key)) for t, (runs, pl, key, _m) in SOURCES.items()]
    width = len(panels) * PANEL_W + (len(panels) - 1) * GAP
    families = [f for f in COLORS if any(f in d for _, d in panels)]
    legend_h = 24 * ((len(families) + 2) // 3)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{PANEL_H + legend_h + 8}" '
        'font-family="sans-serif">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for i, (title, data) in enumerate(panels):
        parts += panel(title, data, i * (PANEL_W + GAP))
    for i, fam in enumerate(families):
        x, y = 16 + (i % 3) * (width // 3), PANEL_H + 12 + (i // 3) * 22
        label = "全員に聞く方式（flood_score）" if fam == "flood_score" else LABELS.get(fam, fam)
        parts.append(f'<rect x="{x}" y="{y - 9}" width="14" height="10" fill="{COLORS[fam]}"/>')
        parts.append(f'<text x="{x + 20}" y="{y}" font-size="11">{escape(label)}</text>')
    parts.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
