"""p0007 I5〜I7（新しいメール・障害・参加の実機での測定）の純粋な計算：選択・スケジュール・時間の集計・時間積分・検定・判定．

実機にも DB にも触れない（`atrium freshness-run`・`failure-run`・`join-run` が使い，tests/test_dynamics.py が検証する）．
判定の規則と定数は事前登録（plans/p0007 §6.4・§6.5）で，実験の結果を見て変えない．変えるときは verdict_revised に別の基準で残す．

- 選択：後から届くメール（test の正解メールの一部）・止めるノード・途中で加わるノードを，種つきで再現できるように選ぶ．
- スケジュール：中央が間隔 T ごとにまとめて転送するときの，メールごとの転送の時刻．
- 集計：答えられるまでの時間（答えられなかったものは打ち切りとして無限大）の中央値・p95．
- 時間積分：答えられた割合の時間変化を階段関数にし，窓の中で積分する（障害の比較の主の指標）．
- 判定：freshness（P2P の p50 < 1 秒かつ中央の T ≥ 60 秒より有意に短い），failure（時間積分が中央より大きい），join（短い，または同等で転送量が小さい）．
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

# ── 事前登録の定数 ────────────────────────────────────────────────────────────

# 後から届くメールの割合と選択の種（plans/p0007 §6.4．test の正解メールから）
HELD_BACK_FRACTION = 0.10
DEFAULT_SEED = 12
# 「答えられるまでの時間」の p50 の上限（秒）と，中央の「まとめて転送する間隔 T」でこの値以上を比べる（§6.4 の判定）
FRESHNESS_P50_LIMIT_S = 1.0
FRESHNESS_MIN_CENTRAL_T_S = 60.0
# 有意水準（片側の符号検定．対にした各メールの P2P の時間が中央より短いか）
SIGNIFICANCE_ALPHA = 0.05
# 参加の判定で「同等の時間」とみなす，P2P の時間の中央値の上限（中央の時間の中央値に対する比．§6.5 E2．事前登録）
JOIN_EQUIVALENCE_RATIO = 1.25
# 障害の「答えられた割合」の時間変化を求める区間の幅（秒）
DEFAULT_BIN_S = 5.0
# 打ち切り（答えられなかった）の時間．JSON に書けるよう None で持ち，集計では無限大として扱う
CENSORED = math.inf


# ── 選択 ──────────────────────────────────────────────────────────────────────


def choose_held_back(
    doc_ids: Sequence[str], fraction: float = HELD_BACK_FRACTION, seed: int = DEFAULT_SEED
) -> list[str]:
    """後から届くメールを選ぶ．重複を除いて並べた文書 ID から，種つきの置換の先頭 round(fraction × 件数) 件を返す（届く順を兼ねる）．"""
    if not 0 < fraction <= 1:
        raise ValueError(f"fraction {fraction} must be in (0, 1]")
    distinct = sorted(set(doc_ids))
    order = np.random.default_rng(seed).permutation(len(distinct))
    n = max(1, round(fraction * len(distinct))) if distinct else 0
    return [distinct[int(i)] for i in order[:n]]


def choose_names(names: Sequence[str], k: int, seed: int = DEFAULT_SEED) -> list[str]:
    """名前の一覧（ノードのホストなど）から k 個を種つきで選ぶ．入力の並びに依らず，同じ集合と種なら同じ結果を返す（並びは名前順）．"""
    distinct = sorted(set(names))
    if not 0 <= k <= len(distinct):
        raise ValueError(f"cannot choose {k} of {len(distinct)} names")
    order = np.random.default_rng(seed).permutation(len(distinct))
    return sorted(distinct[int(i)] for i in order[:k])


# ── スケジュール ──────────────────────────────────────────────────────────────


def central_flush_offsets(arrival_offsets: Sequence[float], interval_s: float) -> list[float]:
    """中央が間隔 interval_s ごとにまとめて転送するとき，各メールが転送される時刻（実験の開始からの秒）．

    interval_s = 0 は届いた時刻に即時，無限大は転送しない（無限大を返す），それ以外は届いた時刻以降で最初の，
    開始時刻を 0 とする interval_s の倍数の時刻．
    """
    if interval_s < 0:
        raise ValueError(f"interval_s {interval_s} must be >= 0")
    if interval_s == 0:
        return [float(t) for t in arrival_offsets]
    if math.isinf(interval_s):
        return [math.inf for _ in arrival_offsets]
    return [math.ceil(t / interval_s) * interval_s for t in arrival_offsets]


def parse_interval(text: str) -> float:
    """ "0"・"60"・"inf"（または "∞"）を秒の数にする．"""
    return math.inf if text.strip().lower() in ("inf", "∞", "infinity") else float(text)


# ── 集計 ──────────────────────────────────────────────────────────────────────


def times_or_censored(
    arrival_s: Sequence[float], answered_s: Sequence[float | None]
) -> list[float]:
    """届いた時刻と答えられた時刻（答えられなければ None）から，答えられるまでの時間を作る（答えられなければ無限大）．"""
    if len(arrival_s) != len(answered_s):
        raise ValueError("arrival_s and answered_s must have the same length")
    return [CENSORED if a is None else a - t for t, a in zip(arrival_s, answered_s, strict=True)]


def durations_or_censored(durations: Sequence[float | None]) -> list[float]:
    """答えられるまでの時間（答えられなければ None）を，None を無限大にした一覧にする．"""
    return [CENSORED if d is None else d for d in durations]


def summarize_times(times: Sequence[float]) -> dict[str, Any]:
    """答えられるまでの時間の件数・打ち切りの数・中央値・p95・最大（打ち切りは無限大．無限大の統計は null で書く）．"""
    if not times:
        return {"n": 0, "n_censored": 0, "p50": None, "p95": None, "max": None}
    arr = np.asarray(times, dtype=np.float64)

    def finite(x: float) -> float | None:
        return None if math.isinf(x) else float(x)

    return {
        "n": len(times),
        "n_censored": int(np.isinf(arr).sum()),
        "p50": finite(float(np.percentile(arr, 50, method="higher"))),
        "p95": finite(float(np.percentile(arr, 95, method="higher"))),
        "max": finite(float(arr.max())),
    }


def sign_test_less(a: Sequence[float], b: Sequence[float]) -> dict[str, float | int]:
    """対にした a[i] < b[i] の片側の正確な符号検定（帰無仮説：a[i] < b[i] と a[i] > b[i] が同じ確率．同値の対は除く）．

    a が小さい対の数を n_less，大きい対の数を n_greater として，p = P(X ≥ n_less)（X ~ Binomial(n_less + n_greater, 1/2)）．
    対が 1 つも無ければ p = 1．
    """
    if len(a) != len(b):
        raise ValueError("a and b must be paired")
    n_less = sum(x < y for x, y in zip(a, b, strict=True))
    n_greater = sum(x > y for x, y in zip(a, b, strict=True))
    n = n_less + n_greater
    p = 1.0 if n == 0 else sum(math.comb(n, i) for i in range(n_less, n + 1)) / 2**n
    return {"n_less": n_less, "n_greater": n_greater, "n_tied": len(a) - n, "p": p}


# ── 時間積分 ──────────────────────────────────────────────────────────────────


def binned_rates(
    times: Sequence[float], flags: Sequence[bool], start: float, end: float, bin_s: float
) -> list[float | None]:
    """時刻 times の各質問の結果 flags を，[start, end) を bin_s ごとに区切った区間の平均にする（質問が 1 つも無い区間は None）．"""
    if bin_s <= 0 or end <= start:
        raise ValueError("need bin_s > 0 and end > start")
    n_bins = math.ceil((end - start) / bin_s)
    hits = [0] * n_bins
    counts = [0] * n_bins
    for t, ok in zip(times, flags, strict=True):
        if start <= t < end:
            b = min(int((t - start) // bin_s), n_bins - 1)
            counts[b] += 1
            hits[b] += bool(ok)
    return [h / c if c else None for h, c in zip(hits, counts, strict=True)]


def fill_forward(rates: Sequence[float | None], initial: float = 0.0) -> list[float]:
    """質問が無かった区間（None）を直前の区間の値で埋める（先頭が None なら initial）．区間の幅より質問の間隔が長いときの欠測の扱い．"""
    out: list[float] = []
    last = initial
    for r in rates:
        last = last if r is None else r
        out.append(last)
    return out


def integrate_rates(
    rates: Sequence[float], start: float, bin_s: float, t0: float, t1: float
) -> float:
    """階段関数（区間 i = [start + i·bin_s, start + (i+1)·bin_s) で値 rates[i]）の [t0, t1] の積分（割合 × 秒）．区間の外は 0．"""
    if t1 < t0:
        raise ValueError("t1 must be >= t0")
    total = 0.0
    for i, rate in enumerate(rates):
        lo = max(t0, start + i * bin_s)
        hi = min(t1, start + (i + 1) * bin_s)
        if hi > lo:
            total += rate * (hi - lo)
    return total


def central_rate_integral(restart_s: float, rate_after: float, window_s: float) -> float:
    """中央の答えられた割合の時間積分（窓 [0, window_s]．障害の発生を 0 とする）．再起動が終わる restart_s までは 0，その後は rate_after．"""
    return rate_after * max(0.0, window_s - restart_s)


# ── 判定 ──────────────────────────────────────────────────────────────────────


def judge_freshness(
    p2p_times: Sequence[float], central_times: Mapping[float, Sequence[float]]
) -> dict[str, Any]:
    """plans/p0007 §6.4 の判定：P2P の答えられるまでの時間の p50 が 1 秒未満，かつ中央の T ≥ 60 秒の全ての場合より有意に短い．

    central_times は T（秒）→ 同じメールの順に並べた中央の時間．有意は，対にした片側の符号検定で p < SIGNIFICANCE_ALPHA．
    T ≥ 60 秒の条件が 1 つも無ければ判定しない（holds は None）．
    """
    p50 = summarize_times(p2p_times)["p50"]
    p50_ok = p50 is not None and p50 < FRESHNESS_P50_LIMIT_S
    comparisons: dict[str, Any] = {}
    for interval, times in sorted(central_times.items()):
        if interval < FRESHNESS_MIN_CENTRAL_T_S:
            continue
        test = sign_test_less(p2p_times, times)
        comparisons[_interval_key(interval)] = {
            **test,
            "central": summarize_times(times),
            "significantly_shorter": bool(test["p"] < SIGNIFICANCE_ALPHA),
        }
    holds = (
        None
        if not comparisons
        else bool(p50_ok and all(c["significantly_shorter"] for c in comparisons.values()))
    )
    return {
        "hypothesis": "P2P の答えられるまでの時間の p50 < 1 秒，かつ中央の T >= 60 秒より有意に短い（対にした片側の符号検定）",
        "p2p": summarize_times(p2p_times),
        "p50_limit_s": FRESHNESS_P50_LIMIT_S,
        "p50_below_limit": bool(p50_ok),
        "alpha": SIGNIFICANCE_ALPHA,
        "comparisons": comparisons,
        "holds": holds,
    }


def judge_failure(per_k: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """障害の判定：測った全ての k で，障害の窓の中の答えられた割合の時間積分が，P2P が中央より大きい．

    per_k の各要素は k・p2p_integral・central_integral（同じ窓の積分．割合 × 秒）を持つ．k が 1 つも無ければ判定しない．
    """
    rows = [
        {
            "k": row["k"],
            "p2p_integral": row["p2p_integral"],
            "central_integral": row["central_integral"],
            "p2p_larger": bool(row["p2p_integral"] > row["central_integral"]),
        }
        for row in per_k
    ]
    return {
        "hypothesis": "全ての k で，同じ窓の答えられた割合の時間積分が P2P > 中央",
        "rows": rows,
        "holds": None if not rows else all(r["p2p_larger"] for r in rows),
    }


def judge_join(
    p2p_times: Sequence[float],
    central_times: Sequence[float],
    p2p_bytes: int,
    central_bytes: int,
) -> dict[str, Any]:
    """plans/p0007 §6.5 E2 の判定：P2P が「答えられるまでの時間」の中央値で中央より短い，または同等（中央の JOIN_EQUIVALENCE_RATIO 倍以内）
    で転送量が小さい．

    p2p_times・central_times は加わった受信箱ごとの，参加から答えられるまでの時間（打ち切りは無限大）．転送量はバイト数の合計．
    """
    p2p = summarize_times(p2p_times)
    central = summarize_times(central_times)
    p2p_p50 = p2p["p50"]
    central_p50 = central["p50"]
    faster = bool(p2p_p50 is not None and (central_p50 is None or p2p_p50 < central_p50))
    equivalent = bool(
        p2p_p50 is not None
        and central_p50 is not None
        and p2p_p50 <= JOIN_EQUIVALENCE_RATIO * central_p50
    )
    less_transfer = p2p_bytes < central_bytes
    if faster:
        branch = "faster"
    elif equivalent and less_transfer:
        branch = "equivalent_less_transfer"
    else:
        branch = None
    return {
        "hypothesis": "P2P が答えられるまでの時間の中央値で中央より短い，または同等（中央の 1.25 倍以内）で転送量が小さい",
        "p2p": p2p,
        "central": central,
        "equivalence_ratio": JOIN_EQUIVALENCE_RATIO,
        "p2p_bytes": p2p_bytes,
        "central_bytes": central_bytes,
        "faster": faster,
        "equivalent": equivalent,
        "less_transfer": bool(less_transfer),
        "branch": branch,
        "holds": branch is not None,
    }


def _interval_key(interval_s: float) -> str:
    """T を JSON のキーにする（無限大は "inf"，整数の秒は "60"）．"""
    return "inf" if math.isinf(interval_s) else f"{interval_s:g}"
