"""p0007 I9：verdict.json（verdict_original・verdict_revised）の共通の書式と，精度の判定（非劣性・優越）．

- `make_verdict`・`write_verdict`：各 CLI が使う verdict.json の書式．`verdict_original`（最初の基準による判定）は一度書いたら
  書き換えない（plans/p0007 §4.4）．結果を見た後に基準を見直したときだけ `verdict_revised` を足す．
- `judge_accuracy`：P2P（A）と中央（B）の正答率の差（A − B）の判定（plans/p0007 §4.2）．差の 95% 信頼区間（対応ありのブートストラップ．
  既定は 10,000 回・種 12）の下端が −δ より上なら非劣性，McNemar の正確検定（`atrium.analysis.compare_runs`）で A が有意に高ければ優越．

使い方（2 つの実行の results.jsonl は `atrium judge` で採点済みであること）:
    uv run atrium compare <P2P の実行> <中央の実行> --out-dir <出力先> [--margin 0.02]
      [--revised-margin 0.03 --revised-reason "<独立の根拠>"]   # 結果を見た後の見直し（verdict_revised．事後と明記される）
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import compare_runs, paired_correctness
from atrium.arrays import F64Array

# plans/p0007 §4.2 の最初の基準
NON_INFERIORITY_MARGIN = 0.02
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 12
# 優越・信頼区間の有意水準（両側 5%．信頼区間は 2.5・97.5 パーセンタイル）
ALPHA = 0.05
# ブートストラップの再標本を 1 度に作る回数（質問数 × 回数の添字の行列をメモリに収めるため）
BOOTSTRAP_CHUNK = 500
VERDICT_FILENAME = "verdict.json"

VERDICT_SUPERIOR = "superior"
VERDICT_NON_INFERIOR = "non_inferior"
VERDICT_NOT_NON_INFERIOR = "not_non_inferior"


def make_verdict(
    original: Mapping[str, Any] | None, revised: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """verdict.json の中身．original は最初の基準による判定，revised は事後の見直しによる判定（無ければ None）．"""
    return {"verdict_original": original, "verdict_revised": revised}


def write_verdict(
    out_dir: Path, original: Mapping[str, Any] | None, revised: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """out_dir/verdict.json を書く．既に verdict_original があれば，それと違う original は拒否して書き換えない．"""
    path = out_dir / VERDICT_FILENAME
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))["verdict_original"]
        if previous is not None:
            if original is not None and dict(original) != previous:
                raise ValueError(f"{path} already has a different verdict_original; keep it as is")
            original = previous
    verdict = make_verdict(original, revised)
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(verdict, indent=1, ensure_ascii=False), encoding="utf-8")
    return verdict


def paired_bootstrap_ci(
    diff: F64Array, samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float]:
    """質問ごとの差（A の正誤 − B の正誤）の平均の，対応ありのブートストラップによる 95% 信頼区間（質問を組のまま再抽出する）．"""
    if len(diff) == 0:
        raise ValueError("no paired questions")
    rng = np.random.default_rng(seed)
    means: list[F64Array] = []
    for start in range(0, samples, BOOTSTRAP_CHUNK):
        size = min(BOOTSTRAP_CHUNK, samples - start)
        idx = rng.integers(0, len(diff), size=(size, len(diff)))
        means.append(diff[idx].mean(axis=1))
    all_means = np.concatenate(means)
    return (
        float(np.percentile(all_means, 100 * ALPHA / 2)),
        float(np.percentile(all_means, 100 * (1 - ALPHA / 2))),
    )


def judge_accuracy(
    rows_p2p: Sequence[dict[str, Any]],
    rows_central: Sequence[dict[str, Any]],
    margin: float = NON_INFERIORITY_MARGIN,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """P2P と中央の正答率の差（P2P − 中央）を判定する．非劣性は信頼区間の下端 > −margin，優越は McNemar の正確検定で P2P が有意に高いこと．

    優越を満たせば非劣性も満たす（優越の判定が先）．両方の実行が採点済み・成功の質問だけを対にして比べる．
    """
    shared, p2p, central = paired_correctness(rows_p2p, rows_central)
    diff = np.asarray(p2p, dtype=np.float64) - np.asarray(central, dtype=np.float64)
    low, high = paired_bootstrap_ci(diff, samples, seed)
    mcnemar = compare_runs(rows_p2p, rows_central)
    non_inferior = low > -margin
    superior = (
        mcnemar["mcnemar_p"] < ALPHA and mcnemar["only_a_correct"] > mcnemar["only_b_correct"]
    )
    verdict = (
        VERDICT_SUPERIOR
        if superior
        else VERDICT_NON_INFERIOR
        if non_inferior
        else VERDICT_NOT_NON_INFERIOR
    )
    return {
        "hypothesis": "H-A1 非劣性（差の 95% 信頼区間の下端 > -margin）・H-A2 優越（McNemar の正確検定）",
        "n_shared": len(shared),
        "accuracy_p2p": mcnemar["accuracy_a"],
        "accuracy_central": mcnemar["accuracy_b"],
        "difference": float(diff.mean()),
        "ci95": [low, high],
        "bootstrap": {"samples": samples, "seed": seed, "paired": True},
        "non_inferiority": {"margin": margin, "holds": bool(non_inferior)},
        "superiority": {
            "mcnemar_p": mcnemar["mcnemar_p"],
            "alpha": ALPHA,
            "only_p2p_correct": mcnemar["only_a_correct"],
            "only_central_correct": mcnemar["only_b_correct"],
            "holds": bool(superior),
        },
        "verdict": verdict,
    }
