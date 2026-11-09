"""採点と生成を同じ GPU に載せたときの閉じた系の負荷試験（`atrium colocate-bench`．d0009 B2 の同居の測定）．

採点の流れは `atrium score-bench` と同じ（score_bench.run_bench を再利用する）．生成の流れは，H の 288 問の 864 候補（3 候補 × 288 問）の入力
（`results/d0009_a3_p1/results.jsonl` の `owner_answer.candidates[].context_doc_ids` の 5 通．題と本文）を，問の順・候補の順に，
GPU の PC の node の `POST /v1/generate`（委託と同じ経路．質問・文書・`allow_unknown: false`・`logprobs: true`）へ，同時の要求 C_g 本の閉じた系で送る．
- `--mode score`：採点だけ．`--mode gen`：生成だけ．`--mode both`：両方を 1 つの process で動かし，慣らし・測定の区間を同じ時刻に揃える．
- 通常：[t0 + warmup_s, t0 + warmup_s + measure_s) が測定の区間．区間の終わりで新しい要求を止め，送信中の要求は返るのを待つ．
- `--one-pass`（score・both）：採点 288 問の 1 周が終わったら，採点も生成も止める（得点の一致の確認用）．
- `--gen-limit N`（gen）：864 候補の先頭 N 件を 1 回だけ送って終える（生成の経路の確認用）．
出力（out_dir）: score_bench.json・requests.jsonl・scores.jsonl（採点を流したとき．score-bench と同じ），
gen_bench.json・gen_requests.jsonl（生成を流したとき．要求ごとの候補の番号・時刻・トークン数・prefill_s・decode_s・回答・失敗）．
負荷の生成は制御点の CPU（GPU は使わない）．使い方: scripts/d0009/b2co_chain.sh が制御点の full イメージのコンテナから呼ぶ．
"""

from __future__ import annotations

import json
import logging
import statistics
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np

from atrium import score_bench
from atrium.analysis import read_results
from atrium.benchmarks import load_questions
from atrium.data_enronqa import QUESTION_FILES, read_inbox
from atrium.paths import dataset_paths
from atrium.protocol import DocOut, GenerateRequest
from atrium.requester import RemoteCandidateScorer

logger = logging.getLogger(__name__)

# node の応答を待つ上限（LlmConfig.timeout_s の既定と同じ．主構成の委託の要求と同じ条件にする）
GEN_TIMEOUT_S = 600.0
# 失敗した要求の後に次の要求へ進むまでの待ち（node が落ちているときに失敗の要求を大量に作らないため）
GEN_ERROR_BACKOFF_S = 1.0
GEN_START_DELAY_S = score_bench.START_DELAY_S
# 生成の要求の score 欄は prompts.format_context が使わないので 0 を入れる
UNUSED_DOC_SCORE = 0.0


@dataclass(frozen=True)
class GenInput:
    """生成の要求 1 つ（1 候補分）：通し番号・質問 ID・候補の番号・質問文・文脈の文書（題・本文）．"""

    index: int
    qid: str
    candidate: int
    question: str
    docs: list[DocOut]


@dataclass(frozen=True)
class GenRecord:
    """生成の要求 1 つの結果．失敗のとき error 以外の計測値は None．"""

    index: int
    qid: str
    candidate: int
    sent_at: float
    done_at: float
    error: str | None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    prefill_s: float | None = None
    decode_s: float | None = None
    answer: str | None = None


def load_generation_inputs(data_dir: Path, run_results: Path) -> list[GenInput]:
    """H の問と，保存済みの候補の文脈の文書 ID から，生成の入力 864 件を作る（問の順は questions.jsonl の順，候補の順は保存の順）．"""
    paths = dataset_paths(data_dir, "enronqa")
    questions = load_questions(
        paths.questions.parent / QUESTION_FILES["holdout"], score_bench.QUESTIONS_PER_INBOX
    )
    rows = {r["qid"]: r for r in read_results(run_results) if r.get("error") is None}
    missing = [q.qid for q in questions if q.qid not in rows]
    if missing:
        raise RuntimeError(f"{len(missing)} questions have no successful row in {run_results}")
    ids_of = {
        q.qid: [list(c["context_doc_ids"]) for c in rows[q.qid]["owner_answer"]["candidates"]]
        for q in questions
    }
    inboxes = {d.split("/")[0] for lists in ids_of.values() for ids in lists for d in ids}
    doc_of = {d["id"]: d for inbox in sorted(inboxes) for d in read_inbox(paths, inbox)}
    inputs: list[GenInput] = []
    for q in questions:
        for ci, ids in enumerate(ids_of[q.qid]):
            docs = [
                DocOut(
                    doc_id=d,
                    title=doc_of[d]["title"],
                    content=doc_of[d]["content"],
                    score=UNUSED_DOC_SCORE,
                )
                for d in ids
            ]
            inputs.append(GenInput(len(inputs), q.qid, ci, q.question, docs))
    return inputs


class GenLoop:
    """同時の要求 C_g 本の閉じた系を，スレッドで流す（要求は I/O 待ちが大半なので，制御点の CPU の負担は小さい）．"""

    def __init__(
        self,
        node_url: str,
        inputs: Sequence[GenInput],
        concurrency: int,
        limit: int | None,
        stop: threading.Event,
    ) -> None:
        """送り先の node の URL・入力・同時の要求の数・先頭から送る件数の上限（None なら区間の終わりまで周回）・止める合図．"""
        self._url = f"{node_url.rstrip('/')}/v1/generate"
        self._inputs = inputs
        self._concurrency = concurrency
        self._limit = limit
        self._stop = stop
        self._lock = threading.Lock()
        self._next = 0
        self.records: list[GenRecord] = []

    def _take(self, end_at: float | None) -> GenInput | None:
        """次の入力を取る（件数の上限か区間の終わりか止める合図で None）．区間の中では先頭から周回する．"""
        with self._lock:
            if self._stop.is_set():
                return None
            if self._limit is not None:
                if self._next >= min(self._limit, len(self._inputs)):
                    return None
            elif end_at is not None and time.time() >= end_at:
                return None
            item = self._inputs[self._next % len(self._inputs)]
            self._next += 1
            return item

    def _post(self, client: httpx.Client, item: GenInput) -> GenRecord:
        """1 要求を送り，応答の計測値を記録にする（HTTP・JSON の形の失敗は error に入れ，流れは止めない）．"""
        body = GenerateRequest(
            dataset="enronqa",
            question=item.question,
            options={},
            docs=item.docs,
            allow_unknown=False,
            logprobs=True,
        ).model_dump()
        sent_at = time.time()
        try:
            response = client.post(self._url, json=body)
            response.raise_for_status()
            payload = response.json()
            llm = payload["llm"]
            return GenRecord(
                item.index,
                item.qid,
                item.candidate,
                sent_at,
                time.time(),
                None,
                int(llm["prompt_tokens"]),
                int(llm["output_tokens"]),
                float(llm["prefill_s"]),
                float(llm["decode_s"]),
                str(payload["answer"]),
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return GenRecord(
                item.index,
                item.qid,
                item.candidate,
                sent_at,
                time.time(),
                f"{type(exc).__name__}: {exc}",
            )

    def _stream(self, t0: float, end_at: float | None) -> None:
        """1 本の流れ：t0 から，入力を取って送り続ける．"""
        time.sleep(max(0.0, t0 - time.time()))
        with httpx.Client(timeout=GEN_TIMEOUT_S) as client:
            while (item := self._take(end_at)) is not None:
                record = self._post(client, item)
                with self._lock:
                    self.records.append(record)
                if record.error is not None:
                    time.sleep(GEN_ERROR_BACKOFF_S)

    def run(self, t0: float, end_at: float | None) -> None:
        """全ての流れを t0 から流し，終わるまで待つ．"""
        threads = [
            threading.Thread(target=self._stream, args=(t0, end_at), name=f"gen-bench-{i}")
            for i in range(self._concurrency)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


def _median_or_none(values: list[float]) -> float | None:
    """中央値（空なら None）．"""
    return statistics.median(values) if values else None


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    """待ち時間の p50・p95（空なら None）．"""
    if not values:
        return {f"p{p}": None for p in score_bench.PERCENTILES}
    arr = np.asarray(values, dtype=np.float64)
    return {f"p{p}": float(np.percentile(arr, p)) for p in score_bench.PERCENTILES}


def summarize_generation(
    records: Sequence[GenRecord],
    concurrency: int,
    window: tuple[float, float] | None,
    git_head: str,
    node_url: str,
) -> dict[str, object]:
    """gen_bench.json の中身．window があれば区間に終わった要求だけで X_gen・トークンの速さ・待ち時間を数える（無ければ全要求）．"""
    scoped = (
        list(records)
        if window is None
        else [r for r in records if window[0] <= r.done_at < window[1]]
    )
    ok = [r for r in scoped if r.error is None]
    decode_tps = [
        r.output_tokens / r.decode_s
        for r in ok
        if r.output_tokens and r.decode_s and r.output_tokens > 0 and r.decode_s > 0
    ]
    prefill_tps = [
        r.prompt_tokens / r.prefill_s
        for r in ok
        if r.prompt_tokens and r.prefill_s and r.prefill_s > 0
    ]
    latencies = [r.done_at - r.sent_at for r in ok]
    bench: dict[str, object] = {
        "kind": "gen_bench",
        "git_head": git_head,
        "gen_url": node_url,
        "gen_concurrency": concurrency,
        "n_requests_total": len(records),
        "n_request_errors_total": sum(r.error is not None for r in records),
        "error_examples": sorted({r.error for r in records if r.error})[:3],
        "n_ok": len(ok),
        "n_errors": sum(r.error is not None for r in scoped),
        "decode_tps_median": _median_or_none(decode_tps),
        "prefill_tps_median": _median_or_none(prefill_tps),
        "latency_s": _percentiles(latencies),
        "gpu_time_s_sum": sum((r.prefill_s or 0.0) + (r.decode_s or 0.0) for r in ok),
    }
    if window is not None:
        measure_s = window[1] - window[0]
        bench.update(
            {
                "window_start": window[0],
                "window_end": window[1],
                "measure_s": measure_s,
                "x_gen_cps": len(ok) / measure_s,
            }
        )
    return bench


def write_generation_outputs(
    out_dir: Path, bench: dict[str, object], records: Sequence[GenRecord]
) -> None:
    """gen_bench.json・gen_requests.jsonl を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gen_bench.json").write_text(
        json.dumps(bench, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    with (out_dir / "gen_requests.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def run_colocated(
    mode: str,
    scorer: RemoteCandidateScorer | None,
    score_inputs: Sequence[score_bench.QuestionInput],
    score_concurrency: int,
    node_url: str | None,
    gen_inputs: Sequence[GenInput],
    gen_concurrency: int,
    gen_limit: int | None,
    warmup_s: float,
    measure_s: float,
    one_pass: bool,
    git_head: str,
) -> tuple[
    tuple[dict[str, object], score_bench.ClosedLoop] | None,
    tuple[dict[str, object], list[GenRecord]] | None,
]:
    """mode（score・gen・both）に従って流し，（採点の（bench，流れ），生成の（bench，記録））を返す（流さなかった側は None）．"""
    t0 = time.time() + GEN_START_DELAY_S
    window = (
        None if (one_pass or gen_limit is not None) else (t0 + warmup_s, t0 + warmup_s + measure_s)
    )
    end_at = None if window is None else window[1]
    stop = threading.Event()
    gen_loop: GenLoop | None = None
    gen_thread: threading.Thread | None = None
    if mode in ("gen", "both"):
        assert node_url is not None
        gen_loop = GenLoop(node_url, gen_inputs, gen_concurrency, gen_limit, stop)
        gen_thread = threading.Thread(target=gen_loop.run, args=(t0, end_at), name="gen-bench")
        gen_thread.start()
    score_result = None
    if mode in ("score", "both"):
        assert scorer is not None
        score_result = score_bench.run_bench(
            scorer, score_inputs, score_concurrency, warmup_s, measure_s, one_pass, git_head, t0
        )
        # 1 周で終える採点が済んだら，生成の流れも止める（区間の実行では生成の流れは区間の終わりで自分で止まる）
        stop.set()
    if gen_thread is not None:
        gen_thread.join()
    gen_result = None
    if gen_loop is not None:
        assert node_url is not None
        gen_result = (
            summarize_generation(gen_loop.records, gen_concurrency, window, git_head, node_url),
            gen_loop.records,
        )
    return score_result, gen_result
