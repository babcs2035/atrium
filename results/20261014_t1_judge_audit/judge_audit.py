"""p0005 T1：判定モデルの偽陽性（例文だけの回答）を狭い補正で数え直す．既存の結果ファイルを読むだけで，何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261014_t1_judge_audit/judge_audit.py > results/20261014_t1_judge_audit/judge_audit.out

定義（p0005 §3.2．固定）:
- 例文だけの回答：final_answer が "The answer to the question" だけ（前後の空白と末尾の "." ":" を除く）．
- 狭い補正の正答率 =（判定が正解とした数 − 例文だけの回答で正解とされた数）／ 問の数．判定できなかった出力は不正解．
- CPU の受信箱：placement.json で，ホストが 192.168.13. か 192.168.14. で始まるノードが持つ受信箱．質問の受信箱は正解の受信箱（行の bank）で，
  routing=oracle の実行では p0005 §3.2 の selected_sources[0] と同じ（flood_score の実行でも質問の置き場所で分けられるよう bank を使う）．
- 同じ qid が複数行あるときは最後の行を使う（atrium.analysis.read_results と同じ）．
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from atrium.analysis import merge_judgements, read_results

RESULTS = Path("results")
# 判定の対象の実行（EQ5 の 6 条件，EQ8 の 9 条件，EQ9）
RUNS = (
    "20261009_eq5_b1",
    "20261009_eq5_b1_flood",
    "20261009_eq5_b2",
    "20261009_eq5_b2_1p7b",
    "20261009_eq5_b3",
    "20261009_eq5_b4",
    *(f"20261011_eq8_{c}_k{k}" for c in ("b1", "b2", "b2_1p7b") for k in (1, 3, 5)),
    "20261011_eq9_b3_k5",
)
CPU_HOST_PREFIXES = ("192.168.13.", "192.168.14.")
TEMPLATE = re.compile(r"\s*the answer to the question\s*[.:]?\s*", re.IGNORECASE)
PROMPT_ECHO_PREFIX = "Emails:"


def cpu_inboxes(run_dir: Path) -> set[str]:
    """その実行で CPU の専門家が持っていた受信箱．"""
    placement = json.loads((run_dir / "placement.json").read_text(encoding="utf-8"))
    return {
        sid
        for node in placement["nodes"]
        if node["host"].startswith(CPU_HOST_PREFIXES)
        for sid in node["shard_ids"]
    }


def audit(run: str) -> dict[str, Any] | None:
    """1 回の実行の，CPU の受信箱の問についての集計．採点が無い実行は None．"""
    run_dir = RESULTS / run
    if not (run_dir / "judgements.jsonl").exists():
        return None
    rows = merge_judgements(read_results(run_dir / "results.jsonl"), run_dir / "judgements.jsonl")
    inboxes = cpu_inboxes(run_dir)
    all_echo = [
        r for r in rows if (r.get("final_answer") or "").lstrip().startswith(PROMPT_ECHO_PREFIX)
    ]
    rows = [r for r in rows if r["bank"] in inboxes]
    template = [r for r in rows if TEMPLATE.fullmatch(r.get("final_answer") or "")]
    echo = [
        r for r in rows if (r.get("final_answer") or "").lstrip().startswith(PROMPT_ECHO_PREFIX)
    ]
    correct = sum(bool(r.get("correct")) for r in rows)
    template_correct = sum(bool(r.get("correct")) for r in template)
    n = len(rows)
    return {
        "run": run,
        "n": n,
        "template_only": len(template),
        "template_only_correct": template_correct,
        "accuracy": correct / n,
        "accuracy_narrow": (correct - template_correct) / n,
        "prompt_echo": len(echo),
        "prompt_echo_correct": sum(bool(r.get("correct")) for r in echo),
        "prompt_echo_all": len(all_echo),
        "prompt_echo_all_correct": sum(bool(r.get("correct")) for r in all_echo),
    }


def main() -> None:
    """全実行を集計して表で出す．"""
    print(
        "run | n(CPU の受信箱) | 例文だけ(うち正解) | 元の正答率 | 狭い補正 | プロンプトのまま(うち正解) | 全問でプロンプトのまま(うち正解)"
    )
    for run in RUNS:
        a = audit(run)
        if a is None:
            print(f"{run} | 採点なし", file=sys.stderr)
            continue
        print(
            f"{a['run']} | {a['n']} | {a['template_only']}({a['template_only_correct']}) | "
            f"{a['accuracy']:.3f} | {a['accuracy_narrow']:.3f} | {a['prompt_echo']}({a['prompt_echo_correct']}) | "
            f"{a['prompt_echo_all']}({a['prompt_echo_all_correct']})"
        )


if __name__ == "__main__":
    main()
