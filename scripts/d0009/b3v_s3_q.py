"""Iteration 28 の phase 3 の記述：Q = H ∪ H2 で S3（Iteration 25 の重み）を当てはめ，R3′・V2 と対応ありで比べる．

V2 の増分のうち検証の得点 v による分（V2 − S3）が dev だけでなく Q でも残るかを見るための事後の記述で，採否には使わない．
S3 の特徴量は回答の文字列と保存済みの数値だけなので，`ce_answer_features.jsonl` は要らない（`verify_features` を渡すと読まない）．

使い方: uv run python scripts/d0009/b3v_s3_q.py  （出力は results/d0009_b3_verify/s3_q_post.json）
"""

import json
from pathlib import Path
from typing import Any

from atrium import answer_select_eval as ase

S3_SELECTOR = Path("results/d0009_b3_select/answer_selector.json")
V2_SELECTOR = Path("results/d0009_b3_verify/answer_verifier_selector.json")
# Q を作る実行と，その検証の得点（scripts/d0009/b3v_score.sh の apply と同じ組）
Q_RUNS: tuple[tuple[Path, Path], ...] = (
    (Path("results/d0009_a3_p1"), Path("results/d0009_b3v_h/verify_features.jsonl")),
    (Path("results/d0009_b3v_h2"), Path("results/d0009_b3v_h2/verify_features.jsonl")),
)
OUT_PATH = Path("results/d0009_b3_verify/s3_q_post.json")


def compare_s3_on_q() -> dict[str, Any]:
    """H・H2・Q のそれぞれで，S3 対 R3′ と V2 対 S3 の対応ありの比較を返す．"""
    s3_rule, s3 = ase.load_selector(S3_SELECTOR)
    v2_rule, v2 = ase.load_selector(V2_SELECTOR)
    if (s3_rule, v2_rule) != ("S3", "V2"):
        raise ValueError(f"unexpected selectors: {s3_rule}, {v2_rule}")
    pooled: dict[str, list[bool]] = {"S3": [], "V2": [], "R3p": []}
    result: dict[str, Any] = {
        "note": "事後の記述（Iteration 28 の phase 3）．採否に使わない．S3 は Iteration 25 の全 dev の重み",
        "runs": {},
    }
    for run_dir, verify_path in Q_RUNS:
        questions = ase.load_questions(
            run_dir, need_answer_features=False, verify_features=verify_path
        )
        s3_correct = [
            o.correct for o in ase.outcomes_of(questions, ase.choose_for("S3", questions, s3))
        ]
        v2_correct = [
            o.correct for o in ase.outcomes_of(questions, ase.choose_for("V2", questions, v2))
        ]
        r3p_correct = [o.correct for o in ase.existing_r3p_outcomes(run_dir, questions)]
        pooled["S3"] += s3_correct
        pooled["V2"] += v2_correct
        pooled["R3p"] += r3p_correct
        result["runs"][run_dir.name] = {
            "S3_vs_R3p": ase.paired_report(s3_correct, r3p_correct),
            "V2_vs_S3": ase.paired_report(v2_correct, s3_correct),
        }
    result["Q"] = {
        "S3_vs_R3p": ase.paired_report(pooled["S3"], pooled["R3p"]),
        "V2_vs_S3": ase.paired_report(pooled["V2"], pooled["S3"]),
    }
    return result


if __name__ == "__main__":
    OUT_PATH.write_text(
        json.dumps(compare_s3_on_q(), indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(OUT_PATH)
