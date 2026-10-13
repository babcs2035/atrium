"""RQ-C の実機での再生（p0004 EQ7b）：専門家の参加・離脱を実機のノードに起こし，質問者のメンバー表が古いときの検索を測る．

`atrium run` が experiment.churn を指定された設定で呼ぶ（run_experiment が run_churn_questions に分岐する）．
EQ7a（`atrium churn-eval`．実機を使わない）と同じ時刻表・質問の順番・メンバー表の更新規則で，実機の質問者とノードを動かす．

- 質問は EQ2 の test を，EQ7a と同じ順（experiment.seed の置換）で 1 問ずつ順に流す．1 問 = 1 刻み．
- 各ノードの稼働状態は churn_eval.simulate_availability（2 状態のマルコフ連鎖）で決め，刻みごとに状態が変わったノードへ
  `/v1/admin/availability` で参加・離脱を伝える．離脱中のノードは検索・probe・プロファイルの要求を 503 で拒否する．
- 質問者は refresh_steps 刻みごとにだけ，全ノードの `/v1/profile` を引き，返事のあったノードをメンバー表にする．
- 質問者は離脱したノードへの要求の失敗を問の失敗にせず，返事のあった分で続ける（Requester の churn 対応）．

結果は通常の results.jsonl（問ごとに `churn: {step, up, n_visible}` を追加）で，`summarize_run` が hit などを数える．
`atrium churn-live-report` が複数の実行を集め，EQ7a の値と比べる（`compare_with_offline`）．
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from atrium.benchmarks import Question
from atrium.churn_eval import BoolArray, simulate_availability
from atrium.config import AtriumConfig, ChurnConfig
from atrium.manifest import Placement
from atrium.requester import RETRIEVE_TIMEOUT_S, Requester

logger = logging.getLogger(__name__)

# EQ7a の hit との一致の許容（問数）．スコアの同点の扱いの違いで 1 問ずれうるので 2 問まで許す
EQUIVALENCE_TOLERANCE_QUESTIONS = 2
# flood_score は 1 段目で全ノードに問い合わせるので，問い合わせたノードの数は probe の数で数える
FLOOD_ROUTING = "flood_score"


def question_order(n_questions: int, seed: int) -> list[int]:
    """EQ7a（churn_eval.evaluate）と同じ質問の順番．test の質問の並びに対する，experiment.seed の置換．"""
    return [int(i) for i in np.random.default_rng(seed).permutation(n_questions)]


def build_schedule(cfg: AtriumConfig, churn: ChurnConfig, n_nodes: int, n_steps: int) -> BoolArray:
    """[刻み, ノード] の稼働状態．churn_eval.evaluate と同じ種 [experiment.seed, schedule_seed, 稼働率×100] で引く．"""
    rng = np.random.default_rng(
        [cfg.experiment.seed, churn.schedule_seed, round(churn.availability * 100)]
    )
    return simulate_availability(n_nodes, n_steps, churn.availability, churn.mean_up_steps, rng)


async def _set_node_states(
    client: httpx.AsyncClient, placement: Placement, indices: Sequence[int], online: BoolArray
) -> None:
    """indices のノードの参加・離脱を伝える（online はその刻みの各ノードの稼働状態）．"""

    async def one(i: int) -> None:
        url = f"{placement.nodes[i].url}/v1/admin/availability"
        response = await client.post(url, json={"up": bool(online[i])}, timeout=RETRIEVE_TIMEOUT_S)
        response.raise_for_status()

    await asyncio.gather(*(one(i) for i in indices))


async def _refresh_member_table(client: httpx.AsyncClient, placement: Placement) -> frozenset[str]:
    """全ノードの /v1/profile を引き，返事のあったノードの URL をメンバー表とする（離脱中のノードは 503 で返事をしない）．"""

    async def alive(url: str) -> str | None:
        try:
            response = await client.get(f"{url}/v1/profile", timeout=RETRIEVE_TIMEOUT_S)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        return url

    urls = await asyncio.gather(*(alive(n.url) for n in placement.nodes))
    return frozenset(u for u in urls if u is not None)


async def run_churn_questions(
    requester: Requester,
    questions: Sequence[Question],
    out_path: Path,
    churn: ChurnConfig,
    schedule: BoolArray,
) -> int:
    """questions（EQ7a と同じ順）を 1 問ずつ流し，刻みごとにノードの参加・離脱とメンバー表の更新を行う．失敗した問数を返す．

    out_path は作り直す（途中からの再開は，刻みと状態の対応が崩れるのでしない）．終わるとき（失敗でも）は全ノードを稼働に戻す．
    """
    client = requester.client
    placement = requester.placement
    n_nodes = len(placement.nodes)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    # 前の実行が途中で止まって離脱したままのノードがあっても，最初の刻みの状態をまず全ノードへ伝える
    previous = schedule[0].copy()
    try:
        await _set_node_states(client, placement, list(range(n_nodes)), previous)
        with out_path.open("w", encoding="utf-8") as out:
            for step, question in enumerate(questions):
                online = schedule[step]
                changed = np.flatnonzero(online != previous)
                if changed.size:
                    await _set_node_states(client, placement, changed.tolist(), online)
                    previous = online.copy()
                if step % churn.refresh_steps == 0:
                    requester.visible_urls = await _refresh_member_table(client, placement)
                record = await requester.process(question)
                visible = requester.visible_urls
                record["churn"] = {
                    "step": step,
                    "up": "".join("1" if u else "0" for u in online),
                    "n_visible": len(visible) if visible is not None else n_nodes,
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                failures += record["error"] is not None
    finally:
        await _set_node_states(client, placement, list(range(n_nodes)), np.ones(n_nodes, np.bool_))
    return failures


def summarize_run(
    records: Sequence[dict[str, Any]], routing: str, node_of_source: dict[str, int]
) -> dict[str, float]:
    """1 回の実行の指標（churn_eval.summarize_runs の STAT_FIELDS と同じ定義）．

    hit は「正解の受信箱が稼働中で，選ばれたデータ源に入る」割合．flood_score が問い合わせたノードの数は 1 段目（probe）の数で，
    ほかの方式は本照会のノードの数である．失敗した問は hit = 0 として数える（分母は全問）．
    """
    n = len(records)
    hits = 0
    gold_online = 0
    hits_online = 0
    contacted = 0
    wasted = 0
    for r in records:
        up = r["churn"]["up"]
        gold_up = up[node_of_source[r["bank"]]] == "1"
        hit = gold_up and r["error"] is None and r["bank"] in r.get("selected_sources", [])
        hits += hit
        gold_online += gold_up
        hits_online += hit and gold_up
        if routing == FLOOD_ROUTING:
            contacted += r.get("probe_recipients", 0)
            wasted += r.get("probe_failed", 0)
        else:
            contacted += r.get("contacted_nodes", 0)
            wasted += r.get("failed_nodes", 0)
    return {
        "hit": hits / n,
        "hit_given_gold_online": hits_online / gold_online if gold_online else 0.0,
        "gold_online_rate": gold_online / n,
        "contacted": contacted / n,
        "wasted": wasted / n,
    }


def compare_with_offline(
    live_runs: Sequence[dict[str, Any]], offline_runs: Sequence[dict[str, Any]], n_test: int
) -> dict[str, Any]:
    """実機の各実行（method, availability, refresh, seed, hit）を EQ7a の同じ条件の hit と比べる．

    H-C2（事前登録）：全ての実行で |実機 − EQ7a| ≤ EQUIVALENCE_TOLERANCE_QUESTIONS / n_test なら「EQ7a の仕組みは実機で再現した」．
    """
    offline = {(r["method"], r["availability"], r["refresh"], r["seed"]): r for r in offline_runs}
    tolerance = EQUIVALENCE_TOLERANCE_QUESTIONS / n_test
    rows = []
    for r in live_runs:
        ref = offline[(r["method"], r["availability"], r["refresh"], r["seed"])]
        rows.append(
            {
                "method": r["method"],
                "availability": r["availability"],
                "refresh": r["refresh"],
                "seed": r["seed"],
                "live_hit": r["hit"],
                "offline_hit": ref["hit"],
                "diff": r["hit"] - ref["hit"],
            }
        )
    return {
        "tolerance": tolerance,
        "rows": rows,
        "max_abs_diff": max((abs(x["diff"]) for x in rows), default=0.0),
        # 比べる実行が無いとき（ゲートだけ）は判定しない（空の all() が真になって「成立」と読めるのを防ぐ）
        "equivalent": all(abs(x["diff"]) <= tolerance + 1e-12 for x in rows) if rows else None,
    }


def _hc1_complete(runs: Sequence[dict[str, Any]]) -> bool:
    """H-C1 の判定に要る実行（判定の稼働率で，R = 1 と最大の R，全ての種，判定の方式と flood_score）が揃っているか．"""
    from atrium.churn_eval import (
        JUDGED_AVAILABILITY,
        JUDGED_METHOD,
        N_SCHEDULE_SEEDS,
        REFRESH_STEPS,
    )

    have = {(r["method"], r["availability"], r["refresh"], r["seed"]) for r in runs}
    return all(
        (method, JUDGED_AVAILABILITY, refresh, seed) in have
        for method in (JUDGED_METHOD, FLOOD_ROUTING)
        for refresh in (1, max(REFRESH_STEPS))
        for seed in range(N_SCHEDULE_SEEDS)
    )


def evaluate_live(run_dirs: Sequence[Path], offline: dict[str, Any]) -> dict[str, Any]:
    """EQ7b の複数の実行（results/<run_id>/）を集め，EQ7a の metrics.json（offline）と比べる．"""
    from atrium.analysis import read_results
    from atrium.churn_eval import STAT_FIELDS, judge_hc1, summarize_runs

    runs: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        churn = meta["config"]["experiment"]["churn"]
        placement = json.loads((run_dir / "placement.json").read_text(encoding="utf-8"))
        node_of_source = {
            sid: i for i, node in enumerate(placement["nodes"]) for sid in node["shard_ids"]
        }
        records = read_results(run_dir / "results.jsonl")
        failed = sum(r["error"] is not None for r in records)
        stats = summarize_run(records, meta["routing"], node_of_source)
        runs.append(
            {
                "run_id": run_dir.name,
                "method": meta["routing"],
                "availability": churn["availability"],
                "refresh": churn["refresh_steps"],
                "seed": churn["schedule_seed"],
                "n": len(records),
                "failed": failed,
                "e2e_p50_s": float(np.median([r["timings"]["e2e_s"] for r in records])),
                **stats,
            }
        )
    n_test = offline["n_test"]
    churned = [r for r in runs if r["availability"] < 1.0]
    methods = sorted({r["method"] for r in runs})
    # 離脱なし（a = 1）の実行は，EQ7a の離脱なしの hit（= EQ2 の hit@m）との一致で，退行の確認にする
    tolerance = 1.0 / n_test
    no_churn = {
        r["method"]: r["hit"] - offline["no_churn_hit"][r["method"]]
        for r in runs
        if r["availability"] >= 1.0
    }
    return {
        "n_test": n_test,
        "m": offline["m"],
        "regression": {
            "tolerance": tolerance,
            "diff": no_churn,
            "passed": all(abs(d) <= tolerance + 1e-12 for d in no_churn.values()),
        },
        "summary": summarize_runs(runs) if runs else [],
        "stat_fields": list(STAT_FIELDS),
        # 22 回が揃う前（ゲートだけなど）に集計しても止まらないよう，判定に要る組が揃ったときだけ判定する
        "hc1": judge_hc1(churned, methods) if _hc1_complete(churned) else None,
        "equivalence": compare_with_offline(churned, offline["runs"], n_test),
        "runs": runs,
    }


def render_live_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    reg = metrics["regression"]
    eq = metrics["equivalence"]
    h = metrics["hc1"]
    diffs = ", ".join(f"{k} {v:+.4f}" for k, v in reg["diff"].items())
    lines = [
        "# EQ7b：RQ-C の実機での再生（専門家の参加・離脱とメンバー表の古さ）",
        "",
        f"- test {metrics['n_test']} 問，m = {m}．実行 {len(metrics['runs'])} 回（実機のノード 30 台．1 問 = 1 刻み）",
        f"- 退行の確認（離脱なしの hit@{m} と EQ7a の離脱なしの値の差が 1 問以内）：{'通過' if reg['passed'] else '不一致'}（{diffs}）",
        f"- H-C2（EQ7a の再現）：全ての実行で |実機 − EQ7a| ≤ {eq['tolerance']:.5f}（{EQUIVALENCE_TOLERANCE_QUESTIONS} 問）："
        f"{ {True: '成立', False: '不成立', None: '比べる実行が無いので未判定'}[eq['equivalent']] }"
        f"（最大の差 {eq['max_abs_diff']:.5f}）",
        "",
        f"| 方式 | 稼働率 a | 更新間隔 R | 種 | 失敗 | hit@{m} | EQ7a の hit@{m} | 問い合わせたノード | うち離脱していたノード | e2e p50 (s) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    offline = {(x["method"], x["availability"], x["refresh"], x["seed"]): x for x in eq["rows"]}
    for r in sorted(
        metrics["runs"], key=lambda x: (x["method"], -x["availability"], x["refresh"], x["seed"])
    ):
        ref = offline.get((r["method"], r["availability"], r["refresh"], r["seed"]))
        ref_hit = f"{ref['offline_hit']:.4f}" if ref else "-"
        lines.append(
            f"| {r['method']} | {r['availability']} | {r['refresh']} | {r['seed']} | {r['failed']} | "
            f"{r['hit']:.4f} | {ref_hit} | {r['contacted']:.2f} | {r['wasted']:.2f} | {r['e2e_p50_s']:.2f} |"
        )
    if h is None:
        lines += ["", "H-C1：判定に要る実行が揃っていないので判定していない"]
        return "\n".join(lines) + "\n"
    flood = ", ".join(f"{x:.3f}" for x in h["flood_rel_drop"])
    lines += [
        "",
        f"## H-C1（実機．a = {h['availability']}，R = 1 → {h['max_refresh']} の hit@{m} の相対的な低下）",
        "",
        f"flood_score の相対的な低下（種ごと）：{flood}",
        "",
        "| 方式 | 相対的な低下（種ごと） | flood_score との差（種ごと） | 全ての種で flood_score より大きい |",
        "|---|---|---|---|",
    ]
    for name, p in h["per_method"].items():
        drops = ", ".join(f"{x:.3f}" for x in p["rel_drop"])
        minus = ", ".join(f"{x:+.3f}" for x in p["minus_flood"])
        lines.append(
            f"| {name} | {drops} | {minus} | {'はい' if p['larger_in_all_seeds'] else 'いいえ'} |"
        )
    lines += ["", f"判定（{h['judged_method']}）：{'支持' if h['supported'] else '支持されない'}"]
    return "\n".join(lines) + "\n"


def write_live_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_live_report(metrics), encoding="utf-8")
