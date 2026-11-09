"""cross-encoder の採点だけの閉じた系の負荷試験（`atrium score-bench`．d0009 B2）．採点の server を G 個に分けたときの処理量を測る．

入力は H の 288 問（`question_split: holdout`・受信箱ごとの先頭 2 問）の，`results/d0009_a3_p1/results.jsonl` の
`owner_answer.candidates[].context_doc_ids`（3 候補 × 上位 5 通）と，corpus の本文（採点する文は live と同じ「題 + 改行 + 本文」）．
同時の問の数 C の流れが，1 問ごとに 3 候補の採点の要求を順に `RemoteCandidateScorer` で送り，3 つ目が返ったら次の問へ進む（問は同じ順に回す）．
- 通常：[t0 + warmup_s, t0 + warmup_s + measure_s) が測定の区間．区間の終わりで新しい問を止め，送信中の問は返るのを待つ（区間の外の問は集計に入れない）．
- `--one-pass`：288 問を 1 周だけ採点して終わる（得点の一致の確認 G-S 用．区間は無く，得点だけを `--dump-scores` で書き出す）．
出力（out_dir）: score_bench.json（集計と条件），requests.jsonl（要求ごとの送り先・時刻・失敗），scores.jsonl（--dump-scores．1 行 1 組）．
負荷の生成は制御点の CPU（GPU は使わない）．使い方: scripts/d0009/b2_chain.sh が制御点の full イメージのコンテナから呼ぶ．
"""

from __future__ import annotations

import json
import logging
import resource
import statistics
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np

from atrium.analysis import read_results
from atrium.benchmarks import load_questions
from atrium.cpu_stat import CpuSample, busy_fractions, read_device_cpu
from atrium.data_enronqa import QUESTION_FILES, read_inbox
from atrium.paths import dataset_paths
from atrium.requester import RemoteCandidateScorer

logger = logging.getLogger(__name__)

QUESTIONS_PER_INBOX = 2
START_DELAY_S = 2.0
# 失敗した要求の後に次の問へ進むまでの待ち（server が落ちているときに失敗の要求を大量に作らないため）
ERROR_BACKOFF_S = 1.0
PERCENTILES = (50, 95)


@dataclass(frozen=True)
class QuestionInput:
    """1 問の採点の入力：質問文と，候補ごとの（文書 ID，文）の並び．"""

    qid: str
    query: str
    candidates: list[list[tuple[str, str]]]


@dataclass(frozen=True)
class RequestRecord:
    """採点の要求 1 つ（1 候補分）．target は送り先の番号．"""

    qid: str
    candidate: int
    target: int
    sent_at: float
    done_at: float
    error: str | None
    # server の返した採点の時間（秒．GPU の上の 5 組．lock の待ちを含む．失敗は None）
    server_duration_s: float | None = None


@dataclass(frozen=True)
class QuestionRecord:
    """1 問（3 候補）の完了．"""

    qid: str
    sent_at: float
    done_at: float
    error: str | None


@dataclass(frozen=True)
class ScoredPair:
    """書き出す得点 1 組（候補の番号・文書 ID・得点・採点した送り先）．"""

    qid: str
    candidate: int
    doc_id: str
    score: float
    target: int


def load_inputs(
    data_dir: Path, run_results: Path, per_inbox: int = QUESTIONS_PER_INBOX
) -> list[QuestionInput]:
    """H の問と，保存済みの候補の文脈の文書 ID・本文から採点の入力を作る（問の順は questions.jsonl の順）．"""
    paths = dataset_paths(data_dir, "enronqa")
    questions = load_questions(paths.questions.parent / QUESTION_FILES["holdout"], per_inbox)
    rows = {r["qid"]: r for r in read_results(run_results) if r.get("error") is None}
    missing = [q.qid for q in questions if q.qid not in rows]
    if missing:
        raise RuntimeError(f"{len(missing)} questions have no successful row in {run_results}")
    ids_of = {
        q.qid: [list(c["context_doc_ids"]) for c in rows[q.qid]["owner_answer"]["candidates"]]
        for q in questions
    }
    inboxes = {d.split("/")[0] for lists in ids_of.values() for ids in lists for d in ids}
    text_of = {
        d["id"]: f"{d['title']}\n{d['content']}".strip()
        for inbox in sorted(inboxes)
        for d in read_inbox(paths, inbox)
    }
    return [
        QuestionInput(q.qid, q.question, [[(d, text_of[d]) for d in ids] for ids in ids_of[q.qid]])
        for q in questions
    ]


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {f"p{p}": None for p in PERCENTILES}
    arr = np.asarray(values, dtype=np.float64)
    return {f"p{p}": float(np.percentile(arr, p)) for p in PERCENTILES}


class ClosedLoop:
    """同時の問の数 C の閉じた系を，スレッドで流す（採点の要求は I/O 待ちが大半なので，制御点の CPU の負担は小さい）．"""

    def __init__(
        self,
        scorer: RemoteCandidateScorer,
        inputs: Sequence[QuestionInput],
        concurrency: int,
        one_pass: bool,
    ) -> None:
        """scorer・入力・同時の問の数・1 周だけで終えるか．"""
        self._scorer = scorer
        self._inputs = inputs
        self._concurrency = concurrency
        self._one_pass = one_pass
        self._lock = threading.Lock()
        self._next = 0
        self.requests: list[RequestRecord] = []
        self.questions: list[QuestionRecord] = []
        self.pairs: dict[tuple[str, int], list[ScoredPair]] = {}

    def _take(self, end_at: float | None) -> QuestionInput | None:
        """次の問を取る（1 周だけなら尽きたら None．そうでなければ区間の終わりで None）．"""
        with self._lock:
            if self._one_pass:
                if self._next >= len(self._inputs):
                    return None
            elif end_at is not None and time.time() >= end_at:
                return None
            item = self._inputs[self._next % len(self._inputs)]
            self._next += 1
            return item

    def _stream(self, t0: float, end_at: float | None) -> None:
        """1 本の流れ：t0 から，問を取って 3 候補を順に採点し続ける．"""
        time.sleep(max(0.0, t0 - time.time()))
        while (item := self._take(end_at)) is not None:
            sent_at = time.time()
            error: str | None = None
            for ci, candidate in enumerate(item.candidates):
                req_sent = time.time()
                try:
                    values, target, server_s = self._scorer.scores_with_target(
                        item.query, [text for _, text in candidate]
                    )
                # 応答の形が壊れていたとき（pydantic の ValidationError は ValueError）も 1 要求の失敗として数え，流れは止めない
                except (httpx.HTTPError, ValueError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    with self._lock:
                        self.requests.append(
                            RequestRecord(item.qid, ci, -1, req_sent, time.time(), error)
                        )
                    break
                with self._lock:
                    self.requests.append(
                        RequestRecord(item.qid, ci, target, req_sent, time.time(), None, server_s)
                    )
                    self.pairs.setdefault(
                        (item.qid, ci),
                        [
                            ScoredPair(item.qid, ci, doc_id, score, target)
                            for (doc_id, _), score in zip(candidate, values, strict=True)
                        ],
                    )
            done_at = time.time()
            with self._lock:
                self.questions.append(QuestionRecord(item.qid, sent_at, done_at, error))
            if error is not None:
                time.sleep(ERROR_BACKOFF_S)

    def run(self, t0: float, end_at: float | None) -> None:
        """全ての流れを t0 から流し，終わるまで待つ．"""
        threads = [
            threading.Thread(target=self._stream, args=(t0, end_at), name=f"score-bench-{i}")
            for i in range(self._concurrency)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


def _rusage_cpu_s() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


@dataclass
class WindowSamples:
    """測定の区間の始まりと終わりに読んだ CPU 時間（この PC 全体の /proc/stat と，このプロセスの getrusage）．"""

    cpu_before: CpuSample | None = None
    cpu_after: CpuSample | None = None
    proc_before_s: float = 0.0
    proc_after_s: float = 0.0


def run_bench(
    scorer: RemoteCandidateScorer,
    inputs: Sequence[QuestionInput],
    concurrency: int,
    warmup_s: float,
    measure_s: float,
    one_pass: bool,
    git_head: str,
) -> tuple[dict[str, object], ClosedLoop]:
    """1 回の実行（通常は慣らし＋測定の区間，one_pass は 1 周）を流し，（score_bench.json の中身，流れの記録）を返す．"""
    loop = ClosedLoop(scorer, inputs, concurrency, one_pass)
    t0 = time.time() + START_DELAY_S
    window_start = t0 + warmup_s
    window_end = window_start + measure_s
    samples = WindowSamples()

    def sample() -> None:
        """区間の始まりと終わりの，この PC の CPU 時間とこのプロセスの CPU 時間を読む．"""
        time.sleep(max(0.0, window_start - time.time()))
        samples.cpu_before = read_device_cpu()
        samples.proc_before_s = _rusage_cpu_s()
        time.sleep(max(0.0, window_end - time.time()))
        samples.cpu_after = read_device_cpu()
        samples.proc_after_s = _rusage_cpu_s()

    sampler = threading.Thread(target=sample, name="score-bench-sampler")
    if not one_pass:
        sampler.start()
    loop.run(t0, None if one_pass else window_end)
    finished_at = time.time()
    if not one_pass:
        sampler.join()
    targets = len(scorer.urls)
    bench: dict[str, object] = {
        "kind": "score_bench",
        "git_head": git_head,
        "scorers": scorer.urls,
        "n_scorers": targets,
        "concurrency": concurrency,
        "one_pass": one_pass,
        "n_questions_input": len(inputs),
        "scorer_counts_total": scorer.counts(),
        "n_request_errors_total": sum(r.error is not None for r in loop.requests),
    }
    if one_pass:
        bench["duration_s"] = finished_at - t0
        bench["n_questions_done"] = sum(q.error is None for q in loop.questions)
        bench["n_errors"] = sum(q.error is not None for q in loop.questions)
        return bench, loop
    in_window = [q for q in loop.questions if window_start <= q.done_at < window_end]
    ok = [q for q in in_window if q.error is None]
    req_window = [r for r in loop.requests if window_start <= r.done_at < window_end]
    latencies = [q.done_at - q.sent_at for q in ok]
    control = busy_fractions(samples.cpu_before, samples.cpu_after)
    proc_cpu = samples.proc_after_s - samples.proc_before_s
    bench.update(
        {
            "warmup_s": warmup_s,
            "measure_s": measure_s,
            "window_start": window_start,
            "window_end": window_end,
            "n_ok": len(ok),
            "n_errors": sum(q.error is not None for q in in_window),
            "error_examples": sorted({r.error for r in loop.requests if r.error})[:3],
            "throughput_qps": len(ok) / measure_s,
            "latency_s": _percentiles(latencies),
            "latency_mean_s": statistics.fmean(latencies) if latencies else None,
            # 閉じた系の Little の法則：処理量 × 平均の待ち時間 ≈ 同時の問の数（1 から離れれば負荷生成の側の待ちがある）
            "little_ratio": (
                len(ok) / measure_s * statistics.fmean(latencies) / concurrency
                if latencies
                else None
            ),
            "requests_per_target_in_window": [
                sum(r.target == i and r.error is None for r in req_window) for i in range(targets)
            ],
            "control_cpu_busy": None if control is None else control[0],
            "generator_process_cpu_util": proc_cpu / measure_s,
        }
    )
    return bench, loop


def write_outputs(
    out_dir: Path, bench: dict[str, object], loop: ClosedLoop, dump_scores: bool
) -> None:
    """score_bench.json・requests.jsonl（・scores.jsonl）を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "score_bench.json").write_text(
        json.dumps(bench, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    with (out_dir / "requests.jsonl").open("w", encoding="utf-8") as f:
        for r in loop.requests:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    if dump_scores:
        with (out_dir / "scores.jsonl").open("w", encoding="utf-8") as f:
            for pairs in loop.pairs.values():
                for p in pairs:
                    f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
