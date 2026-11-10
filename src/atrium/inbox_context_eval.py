"""d0009 B3（Iteration 41）：候補の受信箱の中で生成へ渡す文脈 5 通の選び方を，今の RRF の上位 5 通から dev で選んだ規則 X へ替える評価の部品．

`atrium inbox-context-eval --stage ...` が呼ぶ（journal の「Iteration 41」の事前登録．config.yaml の実験の経路は変えず，追加だけ）．
質問者・ノードの実機の経路は流さず，保存した候補（`results/d0009_a3_p1`＝H，`results/d0009_m1_h2_base`＝H2．Q = H ∪ H2）の受信箱・質問から
文脈を計算し直し，文脈の並びが変わる候補だけを GPU の PC の node の `POST /v1/generate` で作り直す．

規則 X の候補（事前に固定．費用の小さい順）：
- `bm25`：持ち主の受信箱の BM25（その受信箱だけの統計）の上位 k 通．文脈の順は BM25 の降順．
- `ce10`・`ce20`・`ce50`：RRF の上位 N 通を cross-encoder（`atrium score-server`．fp16・最大長 1,024）の得点の降順に並べ替えた上位 k 通．
  並べ方は `CrossEncoderReranker.rerank` と同じ `np.argsort(-得点)`．文は `<題>\\n<本文>`（再ランク・`select-features` と同じ）．
- 今の RRF（基準）：密ベクトルと BM25 の各上位 `retrieval.fusion_depth` 通の RRF の上位 k 通を，内積の降順に並べたもの
  （`atrium.store.FaissShardStore._search_rrf` と同じ．RRF が変えるのは集合だけで，文脈の順は内積の降順）．

段階（`--stage`）：
- `dev-ce`：dev の持ち主の受信箱の RRF の上位 50 通を採点して `ce_scores.jsonl` に書く（`--limit 30` が G-CE）．
- `dev-gate`：dev の Recall@k（密・BM25・RRF・ce10/20/50）から X を選び，G-D を判定する（`dev_gate.json`．不通過なら `verdict.json`）．
- `q-rrf`：Q の候補の RRF の上位 k 通が保存した `context_doc_ids` と並びまで一致する割合（G-RRF．`g_rrf.json`）．
- `q-ce`：Q の候補の RRF の上位 N 通を採点する（X が ce のとき）．
- `q-plan`：Q の候補ごとの新しい文脈と，作り直す候補の一覧（`plan.jsonl`・`plan.json`）．
- `q-ce-features`：new 腕の作り直す候補の `ce_features`（新しい文脈の 5 通から．`select-features` と同じ関数）を求める（`ce_features_new.jsonl`）．
- `q-generate`：作り直す候補を base・new の両方で node へ送って回答を作る（`gen_records.jsonl`．`--limit`・`--arm` は G-GEN 用）．
- `g-gen`：base 腕の作り直す候補の先頭 30 件の失敗の数と `prompt_tokens` の一致（`g_gen.json`）．
- `q-build`：base・new の実行のディレクトリ（`results.jsonl`・`ce_features.jsonl`・`verify_features.jsonl` の種）を書く（採点の server は使わない）．
  採点（`atrium judge`）・検証（`atrium verify-features`）・選択（`atrium answer-select-eval`）は既存の CLI がこのディレクトリを読む．

回答の本文を含む出力は `gen_records.jsonl` と実行のディレクトリの `results.jsonl` だけで，commit の前に `scripts/tasks/sanitize_results.py` を通す．
統計は既存の実装：対応ありのブートストラップ `atrium.verdict.paired_bootstrap_ci`（10,000 回・種 41）．
"""

from __future__ import annotations

import copy
import json
import logging
import queue
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from atrium import prompts
from atrium.advert_eval import load_inboxes, load_query_set
from atrium.analysis import read_results
from atrium.arrays import F32Array, F64Array
from atrium.benchmarks import load_questions
from atrium.config import AtriumConfig
from atrium.data_enronqa import QUESTION_FILES, read_gold
from atrium.hybrid import Bm25Index, ranked_indices, rrf_top_k
from atrium.paths import DatasetPaths
from atrium.protocol import DocOut, GenerateRequest
from atrium.requester import RemoteCandidateScorer
from atrium.routing.advert import tokenize
from atrium.select_eval import CE_FEATURES, cross_encoder_features
from atrium.verdict import BOOTSTRAP_SAMPLES, paired_bootstrap_ci, write_verdict

logger = logging.getLogger(__name__)

# X の候補（費用の小さい順．同点の選び方もこの順）
CONTEXT_RULES = ("bm25", "ce10", "ce20", "ce50")
CE_DEPTH_OF = {"ce10": 10, "ce20": 20, "ce50": 50}
MAX_CE_DEPTH = max(CE_DEPTH_OF.values())
# G-D：選んだ X の Recall@k − RRF の Recall@k がこの値以上で，対応ありの 95% 区間の下端 > 0
G_D_MIN_GAIN = 0.010
# X の選び方：最大との差がこの値以内の X のうち，費用の小さいものを選ぶ
SELECT_TIE_TOLERANCE = 0.003
# 割合は問数で割った値なので，閾値との境界は浮動小数の誤差の幅だけ緩めて比べる
FLOAT_EPS = 1e-12
# G-RRF：保存した context_doc_ids と並びまで一致する候補の割合の下限
G_RRF_MIN_AGREEMENT = 0.98
# G-CE・G-GEN：先頭から確かめる件数
GATE_SAMPLE = 30
BOOTSTRAP_SEED = 41
# 採点の要求を 1 つの server あたり同時に送る数（server の lock が直列にするので，待ち時間の隠しに 3 本）
SCORE_STREAMS_PER_SERVER = 3
SCORE_RETRIES = 2
# 生成の要求（1 つの node あたり 1 本．node の Ollama は OLLAMA_NUM_PARALLEL=1）
GEN_TIMEOUT_S = 600.0
GEN_MAX_ATTEMPTS = 3
# 1 つの node が続けて失敗したら，その node は止まったとみなして流れを閉じる
GEN_NODE_MAX_CONSECUTIVE_FAILURES = 5
GEN_ERROR_BACKOFF_S = 5.0
CE_CACHE = "ce_scores.jsonl"
GEN_RECORDS = "gen_records.jsonl"
PLAN = "plan.jsonl"
CE_FEATURES_NEW = "ce_features_new.jsonl"
VERIFY_FEATURES = "verify_features.jsonl"
# 質問の集合 Q：（実行のディレクトリ，質問の分割の名前，検証の得点）．H・H2 の順
Q_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("results/d0009_a3_p1", "holdout", "results/d0009_b3v_h/verify_features.jsonl"),
    ("results/d0009_m1_h2_base", "holdout2", "results/d0009_b3v_h2/verify_features.jsonl"),
)
ARMS = ("base", "new")


# ── 受信箱の中の検索（ノードと同じ得点の作り方） ───────────────────────────────


@dataclass(frozen=True)
class InboxView:
    """受信箱 1 個の，文脈の選び方の評価に要るデータ（埋め込みは受信箱の chunk と同じ fp16 からの float32）．"""

    paths: list[str]
    titles: list[str]
    contents: list[str]
    emb: F32Array
    bm25: Bm25Index
    row_of: dict[str, int]

    def ce_text(self, row: int) -> str:
        """cross-encoder に渡す文（再ランク・`select-features` と同じ「題 + 改行 + 本文」）．"""
        return f"{self.titles[row]}\n{self.contents[row]}".strip()


def load_inbox_views(cfg: AtriumConfig, paths: DatasetPaths) -> dict[str, InboxView]:
    """全受信箱を読み，受信箱ごとの BM25 の索引（その受信箱だけの統計．ノードの `retrieval.fusion: rrf` と同じ）を作る．"""
    views: dict[str, InboxView] = {}
    for ib in load_inboxes(cfg, paths):
        views[ib.name] = InboxView(
            ib.paths,
            ib.titles,
            ib.texts,
            ib.emb,
            Bm25Index(tokenize(t) for t in ib.texts),
            {p: i for i, p in enumerate(ib.paths)},
        )
    return views


@dataclass(frozen=True)
class Retrieval:
    """1 問・1 受信箱の内積・BM25 の得点（行の番号に対応）と，RRF の上位 MAX_CE_DEPTH 通の行の番号（RRF の得点順）．"""

    dense: F64Array
    bm25: F64Array
    rrf_order: list[int]


def retrieve(view: InboxView, query_emb: F32Array, query_text: str, fusion_depth: int) -> Retrieval:
    """質問に対する受信箱の中の得点を作る（`atrium.store.FaissShardStore._dense_and_bm25` と同じ）．"""
    dense = (view.emb @ np.asarray(query_emb, dtype=np.float32)).astype(np.float64)
    bm25 = view.bm25.scores(tokenize(query_text))
    return Retrieval(dense, bm25, rrf_top_k(dense, bm25, MAX_CE_DEPTH, fusion_depth))


def rrf_context(r: Retrieval, k: int) -> list[int]:
    """今の文脈：RRF の上位 k 通を内積の降順（同点は行の番号）に並べる（`FaissShardStore._search_rrf` と同じ）．"""
    return sorted(r.rrf_order[:k], key=lambda i: (-r.dense[i], i))


def dense_context(r: Retrieval, k: int) -> list[int]:
    """密ベクトルだけの上位 k 通（Recall の記述用）．"""
    return [int(i) for i in ranked_indices(r.dense, k)]


def bm25_context(r: Retrieval, k: int) -> list[int]:
    """X = bm25：BM25 の得点の降順の上位 k 通（同点は行の番号の小さい方．A2 の BM25 と同じ）．"""
    return [int(i) for i in ranked_indices(r.bm25, k)]


def ce_context(r: Retrieval, scores: Sequence[float], depth: int, k: int) -> list[int]:
    """X = ce{depth}：RRF の上位 depth 通を，得点（RRF の順の並び）の降順に並べ替えた上位 k 通．"""
    pool = r.rrf_order[:depth]
    if len(scores) < len(pool):
        raise ValueError(f"{len(scores)} scores for {len(pool)} documents")
    order = np.argsort(-np.asarray(scores[: len(pool)], dtype=np.float64))[:k]
    return [pool[int(i)] for i in order]


def context_for_rule(
    rule: str, r: Retrieval, ce_scores: Sequence[float] | None, k: int
) -> list[int]:
    """規則 X（CONTEXT_RULES）の文脈の行の番号．ce は RRF の上位 50 通の得点（RRF の順）が要る．"""
    if rule == "bm25":
        return bm25_context(r, k)
    if ce_scores is None:
        raise ValueError(f"{rule} needs cross-encoder scores")
    return ce_context(r, ce_scores, CE_DEPTH_OF[rule], k)


# ── 採点の cache と採点の実行 ────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoreJob:
    """採点の要求 1 つ：質問 ID・受信箱・質問文・文書 ID と文（RRF の上位 N 通．RRF の順）．"""

    qid: str
    source: str
    query: str
    doc_ids: list[str]
    texts: list[str]


def read_ce_cache(path: Path) -> dict[tuple[str, str], tuple[list[str], list[float]]]:
    """ce_scores.jsonl → (質問 ID，受信箱) → (文書 ID，得点)．無ければ空．"""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {
            (j["qid"], j["source"]): (j["doc_ids"], j["scores"]) for j in map(json.loads, f) if j
        }


def run_score_jobs(
    jobs: Sequence[ScoreJob], urls: Sequence[str], cache_path: Path
) -> dict[str, Any]:
    """未採点の要求を全 server へ振り分けて採点し，1 件ずつ cache_path へ追記する．失敗・非有限の得点の数を返す．

    失敗した要求は SCORE_RETRIES 回まで送り直し，なお失敗したものは cache に書かず n_failed に数える（再実行で続きから）．
    """
    done = read_ce_cache(cache_path)
    todo = [j for j in jobs if (j.qid, j.source) not in done]
    scorer = RemoteCandidateScorer(urls)
    lock = threading.Lock()
    stats = {
        "n_jobs": len(jobs),
        "n_already": len(jobs) - len(todo),
        "n_failed": 0,
        "n_nonfinite": 0,
    }
    gpu_s = 0.0
    n_pairs = 0
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with cache_path.open("a", encoding="utf-8") as out:

        def one(job: ScoreJob) -> None:
            nonlocal gpu_s, n_pairs
            for attempt in range(1 + SCORE_RETRIES):
                try:
                    scores, _, duration = scorer.scores_with_target(job.query, job.texts)
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("score failed for %s (attempt %d): %s", job.qid, attempt, exc)
            else:
                with lock:
                    stats["n_failed"] += 1
                return
            nonfinite = sum(not np.isfinite(s) for s in scores)
            with lock:
                stats["n_nonfinite"] += nonfinite
                gpu_s += duration
                n_pairs += len(scores)
                if not nonfinite:
                    out.write(
                        json.dumps(
                            {
                                "qid": job.qid,
                                "source": job.source,
                                "doc_ids": job.doc_ids,
                                "scores": scores,
                            }
                        )
                        + "\n"
                    )
                    out.flush()

        with ThreadPoolExecutor(max_workers=SCORE_STREAMS_PER_SERVER * len(urls)) as pool:
            list(pool.map(one, todo))
    return {
        **stats,
        "n_pairs_now": n_pairs,
        "gpu_time_s_sum": gpu_s,
        "wall_s": time.perf_counter() - start,
        "counts_by_server": scorer.counts(),
    }


def scorer_conditions(urls: Sequence[str]) -> list[dict[str, Any]]:
    """各採点の server の /healthz（dtype・最大長など）．G-CE の条件の確認用（読めない server は error に残す）．"""
    out: list[dict[str, Any]] = []
    for url in urls:
        try:
            body = httpx.get(f"{url.rstrip('/')}/healthz", timeout=10.0).json()
            out.append({"url": url, **body})
        except (httpx.HTTPError, ValueError) as exc:
            out.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
    return out


# ── dev：Recall@k・X の選択・G-D ─────────────────────────────────────────────


def select_context_rule(recall: Mapping[str, float]) -> dict[str, Any]:
    """事前登録の選び方：dev の Recall@k が最大の X．最大との差が SELECT_TIE_TOLERANCE 以内の X が複数あれば，費用の小さい順で最初のもの．"""
    best = max(recall[r] for r in CONTEXT_RULES)
    near = [r for r in CONTEXT_RULES if recall[r] >= best - SELECT_TIE_TOLERANCE - FLOAT_EPS]
    return {"chosen": near[0], "best_recall": best, "within_tolerance": near}


def gate_d(flags_x: F64Array, flags_rrf: F64Array) -> dict[str, Any]:
    """G-D：選んだ X − RRF の Recall の差が G_D_MIN_GAIN 以上で，対応ありの bootstrap の 95% 区間の下端 > 0 なら通過．"""
    diff = flags_x - flags_rrf
    low, high = paired_bootstrap_ci(diff, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED)
    delta = float(diff.mean())
    return {
        "delta": delta,
        "bootstrap_ci95": [low, high],
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "paired": True},
        "min_gain": G_D_MIN_GAIN,
        "passed": bool(delta >= G_D_MIN_GAIN - FLOAT_EPS and low > 0),
    }


@dataclass(frozen=True)
class DevQuestion:
    """dev の 1 問：質問 ID・質問文・持ち主の受信箱・クエリ埋め込み・正解のメールの path."""

    qid: str
    text: str
    owner: str
    emb: F32Array
    gold_path: str


def load_dev_questions(cfg: AtriumConfig, paths: DatasetPaths) -> list[DevQuestion]:
    """dev の質問（`QuestionSet` の順）．"""
    e = cfg.data.require_enronqa()
    queries = load_query_set(cfg, paths, "dev")
    gold = read_gold(paths)
    return [
        DevQuestion(qid, text, e.sources[owner], emb, gold[qid]["path"])
        for qid, text, owner, emb in zip(
            queries.qids, queries.texts, queries.gold, queries.emb, strict=True
        )
    ]


def score_jobs_for_dev(
    questions: Sequence[DevQuestion], views: Mapping[str, InboxView], fusion_depth: int
) -> list[ScoreJob]:
    """dev の持ち主の受信箱の RRF の上位 MAX_CE_DEPTH 通を採点する要求（質問の順）．"""
    jobs = []
    for q in questions:
        view = views[q.owner]
        r = retrieve(view, q.emb, q.text, fusion_depth)
        rows = r.rrf_order
        jobs.append(
            ScoreJob(
                q.qid,
                q.owner,
                q.text,
                [view.paths[i] for i in rows],
                [view.ce_text(i) for i in rows],
            )
        )
    return jobs


def dev_recall_flags(
    questions: Sequence[DevQuestion],
    views: Mapping[str, InboxView],
    cache: Mapping[tuple[str, str], tuple[list[str], list[float]]],
    k: int,
    fusion_depth: int,
) -> dict[str, F64Array]:
    """方式（dense・bm25・rrf・ce10・ce20・ce50）ごとの，持ち主の受信箱の上位 k 通に正解が入るか（問ごとに 1.0 / 0.0）．"""
    flags: dict[str, list[float]] = {m: [] for m in ("dense", "bm25", "rrf", *CE_DEPTH_OF)}
    for q in questions:
        view = views[q.owner]
        r = retrieve(view, q.emb, q.text, fusion_depth)
        gold_row = view.row_of[q.gold_path]
        cached_ids, scores = cache[(q.qid, q.owner)]
        if cached_ids != [view.paths[i] for i in r.rrf_order]:
            raise ValueError(f"{q.qid}: cached documents differ from the RRF top list")
        contexts = {
            "dense": dense_context(r, k),
            "bm25": bm25_context(r, k),
            "rrf": r.rrf_order[:k],
            **{name: ce_context(r, scores, depth, k) for name, depth in CE_DEPTH_OF.items()},
        }
        for name, rows in contexts.items():
            flags[name].append(float(gold_row in rows))
    return {m: np.asarray(v, dtype=np.float64) for m, v in flags.items()}


def evaluate_dev_gate(flags: Mapping[str, F64Array]) -> dict[str, Any]:
    """dev の Recall・X の選択・G-D をまとめる（dev_gate.json の中身）．"""
    recall = {m: float(v.mean()) for m, v in flags.items()}
    selection = select_context_rule(recall)
    chosen = selection["chosen"]
    return {
        "n": int(len(flags["rrf"])),
        "recall": recall,
        "selection": selection,
        "G_D": {
            "chosen": chosen,
            "rrf_recall": recall["rrf"],
            "chosen_recall": recall[chosen],
            **gate_d(flags[chosen], flags["rrf"]),
        },
        "paired_vs_rrf": {
            m: {
                "delta": float((flags[m] - flags["rrf"]).mean()),
                "bootstrap_ci95": list(
                    paired_bootstrap_ci(flags[m] - flags["rrf"], BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED)
                ),
            }
            for m in flags
            if m != "rrf"
        },
    }


def gate_d_verdict(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """G-D 不通過なら verdict_original（棄却．Q の生成を流さない）を返す．通過なら None．"""
    g_d = result["G_D"]
    if g_d["passed"]:
        return None
    return {
        "hypothesis": "H-IC（候補の受信箱の中の文脈 5 通の選び方を dev で選んだ規則 X に替えると，最終回答の正答率が上がる）．"
        "Q の生成に進むかは dev の Recall の G-D で決める",
        "verdict": "rejected_gate_not_passed",
        "live_run": False,
        "reason": f"G-D 不通過（選んだ {g_d['chosen']} の Recall の差 {g_d['delta']:+.4f}，"
        f"95% 区間 {g_d['bootstrap_ci95']}．閾値 +{g_d['min_gain']}・区間の下端 > 0）．Q の生成は流さない．",
        "G_D": g_d,
        "recall": result["recall"],
    }


# ── Q：保存した候補・G-RRF・新しい文脈・作り直す候補 ──────────────────────────


@dataclass(frozen=True)
class QCandidate:
    """Q の候補 1 つ（保存した候補の文脈と，その受信箱の中の検索）．"""

    qid: str
    index: int
    source: str
    question: str
    saved_ids: list[str]
    saved_top_score: float | None
    retrieval: Retrieval


def read_q_rows(sources: Sequence[tuple[str, str, str]] = Q_SOURCES) -> list[dict[str, Any]]:
    """Q の全問の保存した行（H の次に H2．失敗した行は含めない．候補が 3 つ無い行はそのまま）．"""
    rows: list[dict[str, Any]] = []
    for run, _, _ in sources:
        rows += [r for r in read_results(Path(run) / "results.jsonl") if r.get("error") is None]
    return rows


def iter_q_candidates(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    views: Mapping[str, InboxView],
    rows: Sequence[Mapping[str, Any]],
) -> Iterator[QCandidate]:
    """Q の候補を（質問の順，候補の順に）1 つずつ作る．質問文・クエリ埋め込みは質問の分割ごとに読む．"""
    e = cfg.data.require_enronqa()
    text_of: dict[str, str] = {}
    emb_of: dict[str, F32Array] = {}
    for _, split, _ in Q_SOURCES:
        questions = load_questions(paths.questions.parent / QUESTION_FILES[split])
        ids: list[str] = json.loads(paths.split_query_ids(split).read_text(encoding="utf-8"))
        matrix = np.load(paths.split_query_embeddings(split, e.encoder), mmap_mode="r")
        row_of = {q: i for i, q in enumerate(ids)}
        for q in questions:
            text_of[q.qid] = q.question
            emb_of[q.qid] = np.asarray(matrix[row_of[q.qid]], dtype=np.float32)
    for row in rows:
        qid = str(row["qid"])
        for index, c in enumerate((row.get("owner_answer") or {}).get("candidates") or []):
            source = str(c["source"])
            yield QCandidate(
                qid,
                index,
                source,
                text_of[qid],
                list(c["context_doc_ids"]),
                c.get("top_score"),
                retrieve(views[source], emb_of[qid], text_of[qid], cfg.retrieval.fusion_depth),
            )


def g_rrf(cfg: AtriumConfig, paths: DatasetPaths, views: Mapping[str, InboxView]) -> dict[str, Any]:
    """G-RRF：Q の候補の RRF の上位 k 通（内積の降順）が，保存した context_doc_ids と並びまで一致する割合．"""
    k = cfg.data.require_enronqa().k_context
    rows = read_q_rows()
    n = same = same_set = 0
    top_score_diffs: list[float] = []
    mismatched: list[str] = []
    for cand in iter_q_candidates(cfg, paths, views, rows):
        view = views[cand.source]
        mine = [view.paths[i] for i in rrf_context(cand.retrieval, k)]
        n += 1
        same += mine == cand.saved_ids
        same_set += set(mine) == set(cand.saved_ids)
        if mine == cand.saved_ids:
            if cand.saved_top_score is not None:
                top_score_diffs.append(
                    abs(float(cand.retrieval.dense[view.row_of[mine[0]]]) - cand.saved_top_score)
                )
        elif len(mismatched) < 20:
            mismatched.append(f"{cand.qid}#{cand.index}")
    agreement = same / n
    return {
        "n_candidates": n,
        "n_identical_order": same,
        "n_identical_set": same_set,
        "agreement": agreement,
        "threshold": G_RRF_MIN_AGREEMENT,
        "passed": agreement >= G_RRF_MIN_AGREEMENT - FLOAT_EPS,
        "top_score_abs_diff_max": max(top_score_diffs) if top_score_diffs else None,
        "mismatched_examples": mismatched,
    }


def score_jobs_for_q(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    views: Mapping[str, InboxView],
    depth: int,
) -> list[ScoreJob]:
    """Q の候補の受信箱の RRF の上位 depth 通を採点する要求（候補の順）．cache には上位 depth 通だけを入れる．"""
    jobs = []
    for cand in iter_q_candidates(cfg, paths, views, read_q_rows()):
        view = views[cand.source]
        rows = cand.retrieval.rrf_order[:depth]
        jobs.append(
            ScoreJob(
                cand.qid,
                cand.source,
                cand.question,
                [view.paths[i] for i in rows],
                [view.ce_text(i) for i in rows],
            )
        )
    return jobs


def plan_q(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    views: Mapping[str, InboxView],
    rule: str,
    cache: Mapping[tuple[str, str], tuple[list[str], list[float]]],
) -> list[dict[str, Any]]:
    """Q の候補ごとの，保存した文脈・新しい文脈（規則 rule）・並びが変わるか・新しい文脈の先頭の内積．

    ce は RRF の上位 depth 通の得点の cache が要る（得点の無い候補は例外．黙って補わない）．
    """
    k = cfg.data.require_enronqa().k_context
    plan = []
    for cand in iter_q_candidates(cfg, paths, views, read_q_rows()):
        view = views[cand.source]
        r = cand.retrieval
        scores: list[float] | None = None
        if rule != "bm25":
            cached_ids, scores = cache[(cand.qid, cand.source)]
            depth = CE_DEPTH_OF[rule]
            if cached_ids != [view.paths[i] for i in r.rrf_order[:depth]]:
                raise ValueError(f"{cand.qid}#{cand.index}: cached documents differ")
        new_rows = context_for_rule(rule, r, scores, k)
        new_ids = [view.paths[i] for i in new_rows]
        plan.append(
            {
                "qid": cand.qid,
                "index": cand.index,
                "source": cand.source,
                "base_ids": cand.saved_ids,
                "new_ids": new_ids,
                "changed": new_ids != cand.saved_ids,
                "set_changed": set(new_ids) != set(cand.saved_ids),
                "new_top_score": float(r.dense[new_rows[0]]) if new_rows else None,
            }
        )
    return plan


def summarize_plan(plan: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """作り直す候補の数・割合（問の単位でも）．"""
    changed = [p for p in plan if p["changed"]]
    questions = {p["qid"] for p in plan}
    return {
        "n_candidates": len(plan),
        "n_changed": len(changed),
        "share_changed": len(changed) / len(plan) if plan else None,
        "n_set_changed": sum(bool(p["set_changed"]) for p in plan),
        "n_questions": len(questions),
        "n_questions_with_changed": len({p["qid"] for p in changed}),
    }


# ── 生成（node の POST /v1/generate．委託と同じ経路） ─────────────────────────


@dataclass(frozen=True)
class GenItem:
    """作り直す生成 1 件：腕・質問 ID・候補の番号・質問文・文脈の文書."""

    arm: str
    qid: str
    index: int
    question: str
    docs: list[DocOut]

    @property
    def key(self) -> str:
        """記録の key（腕・質問 ID・候補の番号）．"""
        return f"{self.arm}|{self.qid}|{self.index}"


def generation_items(
    plan: Sequence[Mapping[str, Any]],
    views: Mapping[str, InboxView],
    question_of: Mapping[str, str],
) -> list[GenItem]:
    """作り直す候補（changed）を，候補ごとに base・new の順に並べた生成の入力．"""
    items: list[GenItem] = []
    for p in plan:
        if not p["changed"]:
            continue
        view = views[p["source"]]
        for arm, ids in (("base", p["base_ids"]), ("new", p["new_ids"])):
            docs = [
                DocOut(
                    doc_id=d,
                    title=view.titles[view.row_of[d]],
                    content=view.contents[view.row_of[d]],
                    # prompts.format_context は score を使わない
                    score=0.0,
                )
                for d in ids
            ]
            items.append(GenItem(arm, p["qid"], p["index"], question_of[p["qid"]], docs))
    return items


def read_gen_records(path: Path) -> dict[str, dict[str, Any]]:
    """gen_records.jsonl → key → 記録（同じ key は後の行が優先．失敗の行も含む）．"""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {j["key"]: j for j in map(json.loads, f) if j}


def _post_generation(client: httpx.Client, url: str, item: GenItem) -> dict[str, Any]:
    """1 件を node へ送り，記録（応答の計測値・回答・対数確率．失敗は error）を返す．"""
    body = GenerateRequest(
        dataset="enronqa",
        question=item.question,
        options={},
        docs=item.docs,
        allow_unknown=False,
        logprobs=True,
    ).model_dump()
    base = {"key": item.key, "arm": item.arm, "qid": item.qid, "index": item.index, "node": url}
    sent_at = time.time()
    try:
        response = client.post(f"{url.rstrip('/')}/v1/generate", json=body)
        response.raise_for_status()
        payload = response.json()
        llm = payload["llm"]
        return {
            **base,
            "error": None,
            "answer": str(payload["answer"]),
            "prompt_tokens": int(llm["prompt_tokens"]),
            "output_tokens": int(llm["output_tokens"]),
            "prefill_s": float(llm["prefill_s"]),
            "decode_s": float(llm["decode_s"]),
            "avg_logprob": payload.get("avg_logprob"),
            "n_logprob_tokens": int(payload.get("n_logprob_tokens", 0)),
            "sent_at": sent_at,
            "done_at": time.time(),
        }
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {
            **base,
            "error": f"{type(exc).__name__}: {exc}",
            "sent_at": sent_at,
            "done_at": time.time(),
        }


def run_generation(
    items: Sequence[GenItem], node_urls: Sequence[str], out_path: Path
) -> dict[str, Any]:
    """未生成の件を，node ごとに 1 本の流れ（共有の待ち行列から取る）で送り，1 件ずつ out_path へ追記する．

    失敗した件は GEN_MAX_ATTEMPTS 回まで待ち行列へ戻す（別の node が取りうる）．node が続けて失敗したらその流れは閉じる．
    戻り値の n_missing が 0 でなければ呼び出し側は再実行する（記録の済んだ件は飛ばす）．
    """
    done = {k for k, v in read_gen_records(out_path).items() if v["error"] is None}
    pending: queue.Queue[tuple[GenItem, int]] = queue.Queue()
    for item in items:
        if item.key not in done:
            pending.put((item, 1))
    n_todo = pending.qsize()
    lock = threading.Lock()
    counts = {"ok": 0, "failed_attempts": 0}
    closed_nodes: list[str] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as out:

        def stream(url: str) -> None:
            consecutive = 0
            with httpx.Client(timeout=GEN_TIMEOUT_S) as client:
                while True:
                    try:
                        item, attempt = pending.get_nowait()
                    except queue.Empty:
                        return
                    record = _post_generation(client, url, item)
                    with lock:
                        out.write(json.dumps(record, ensure_ascii=False) + "\n")
                        out.flush()
                        counts["ok" if record["error"] is None else "failed_attempts"] += 1
                    if record["error"] is None:
                        consecutive = 0
                        continue
                    consecutive += 1
                    if attempt < GEN_MAX_ATTEMPTS:
                        pending.put((item, attempt + 1))
                    if consecutive >= GEN_NODE_MAX_CONSECUTIVE_FAILURES:
                        with lock:
                            closed_nodes.append(url)
                        logger.error("closing the stream of %s after repeated failures", url)
                        return
                    time.sleep(GEN_ERROR_BACKOFF_S)

        threads = [threading.Thread(target=stream, args=(u,), name=f"gen-{u}") for u in node_urls]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    final = read_gen_records(out_path)
    return {
        "n_todo": n_todo,
        **counts,
        "n_missing": sum(1 for i in items if final.get(i.key, {}).get("error", "x") is not None),
        "closed_nodes": closed_nodes,
    }


def g_gen(
    plan: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    q_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """G-GEN：base 腕の作り直す候補の先頭 GATE_SAMPLE 件で，失敗 0・`prompt_tokens` が保存値（node_answers）と一致．

    回答の文字列が保存値と一致する割合は記述（生成の揺れの目安）．
    """
    row_of = {r["qid"]: r for r in q_rows}
    first = [p for p in plan if p["changed"]][:GATE_SAMPLE]
    failures = missing = token_match = answer_match = 0
    mismatched: list[dict[str, Any]] = []
    for p in first:
        rec = records.get(f"base|{p['qid']}|{p['index']}")
        if rec is None:
            missing += 1
            continue
        if rec["error"] is not None:
            failures += 1
            continue
        row = row_of[p["qid"]]
        saved_tokens = next(
            na["prompt_tokens"] for na in row["node_answers"] if na["source"] == p["source"]
        )
        saved_answer = row["owner_answer"]["candidates"][p["index"]]["answer"]
        token_match += rec["prompt_tokens"] == saved_tokens
        if rec["prompt_tokens"] != saved_tokens and len(mismatched) < 10:
            mismatched.append(
                {"key": rec["key"], "saved": saved_tokens, "now": rec["prompt_tokens"]}
            )
        answer_match += prompts.extract_free_answer(rec["answer"]) == saved_answer
    n = len(first)
    return {
        "n_checked": n,
        "n_missing": missing,
        "n_failures": failures,
        "n_prompt_tokens_match": token_match,
        "n_answer_identical": answer_match,
        "mismatched_prompt_tokens": mismatched,
        "passed": bool(n == GATE_SAMPLE and missing == 0 and failures == 0 and token_match == n),
    }


# ── 実行のディレクトリ（採点・検証・選択の既存の CLI が読む） ─────────────────────


def apply_regenerated_candidate(
    cand: dict[str, Any], record: Mapping[str, Any], new_context: Mapping[str, Any] | None
) -> None:
    """保存した候補に，作り直した回答（と new 腕なら新しい文脈）を上書きする．

    answer・unknown は `requester._owner_answer` と同じ（`extract_free_answer`・`is_unknown_answer`）．avg_logprob・n_logprob_tokens は応答の値．
    top_score はノードの `answer` と同じく文脈の先頭の内積．probe_*・bm25_max・rrf_max は受信箱・質問だけで決まる値なので変えない．
    """
    free = prompts.extract_free_answer(record["answer"])
    cand["answer"] = free
    cand["unknown"] = prompts.is_unknown_answer(free)
    cand["avg_logprob"] = record["avg_logprob"]
    cand["n_logprob_tokens"] = record["n_logprob_tokens"]
    if new_context is not None:
        cand["context_doc_ids"] = list(new_context["new_ids"])
        cand["top_score"] = new_context["new_top_score"]


def failed_generation_keys(
    plan: Sequence[Mapping[str, Any]], records: Mapping[str, Mapping[str, Any]]
) -> set[tuple[str, int]]:
    """作り直す候補のうち，どちらかの腕の生成が成功していない候補（質問 ID，候補の番号）．両腕とも不正解として数える（事前登録の成功条件 (2)）．"""
    return {
        (p["qid"], p["index"])
        for p in plan
        if p["changed"]
        and any(
            records.get(f"{arm}|{p['qid']}|{p['index']}", {"error": "missing"})["error"] is not None
            for arm in ARMS
        )
    }


def arm_rows(
    saved_rows: Sequence[Mapping[str, Any]],
    plan: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    arm: str,
    failed: frozenset[tuple[str, int]] = frozenset(),
) -> list[dict[str, Any]]:
    """腕（base・new）の results.jsonl の行．作り直す候補だけ回答（new 腕は文脈も）を上書きする．

    行の final_answer・node_answers・answer は保存した値のまま（選択は candidates から行い，使わない）．上書きした候補の番号を `b3ic_rebuilt` に残す．
    failed の候補は，両腕とも回答を空文字（判定は不正解になる）にして番号を `b3ic_failed` に残す（片方の腕だけが失敗した候補を非対称に扱わないため）．
    """
    changed = {(p["qid"], p["index"]): p for p in plan if p["changed"]}
    out: list[dict[str, Any]] = []
    for saved in saved_rows:
        row = copy.deepcopy(dict(saved))
        rebuilt: list[int] = []
        failed_here: list[int] = []
        for index, cand in enumerate(row["owner_answer"]["candidates"]):
            p = changed.get((row["qid"], index))
            if p is None:
                continue
            if (row["qid"], index) in failed:
                cand["answer"] = ""
                cand["unknown"] = False
                cand["avg_logprob"] = None
                cand["n_logprob_tokens"] = 0
                if arm == "new":
                    cand["context_doc_ids"] = list(p["new_ids"])
                    cand["top_score"] = p["new_top_score"]
                failed_here.append(index)
                rebuilt.append(index)
                continue
            record = records[f"{arm}|{row['qid']}|{index}"]
            if record["error"] is not None:
                raise ValueError(f"{record['key']} has no successful generation")
            apply_regenerated_candidate(cand, record, p if arm == "new" else None)
            rebuilt.append(index)
        row["b3ic_failed"] = failed_here
        row["b3ic_rebuilt"] = rebuilt
        row["b3ic_arm"] = arm
        out.append(row)
    return out


def new_context_rows(
    saved_rows: Sequence[Mapping[str, Any]], plan: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """new 腕の文脈だけを反映した行（回答は保存のまま．生成の前に ce_features を求めるため）．作り直す候補の番号を `b3ic_rebuilt` に残す．"""
    changed = {(p["qid"], p["index"]): p for p in plan if p["changed"]}
    out: list[dict[str, Any]] = []
    for saved in saved_rows:
        row = copy.deepcopy(dict(saved))
        row["b3ic_rebuilt"] = []
        for index, cand in enumerate(row["owner_answer"]["candidates"]):
            p = changed.get((row["qid"], index))
            if p is not None:
                cand["context_doc_ids"] = list(p["new_ids"])
                row["b3ic_rebuilt"].append(index)
        out.append(row)
    return out


def seed_verify_rows(
    arm_rows_: Sequence[Mapping[str, Any]],
    saved_rows: Sequence[Mapping[str, Any]],
    saved_verify: Mapping[tuple[str, int], Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """保存した検証の得点のうち，（文脈，回答）が保存した候補と同じ候補の行．`verify-features --resume` が飛ばす．

    検証の入力は文脈・質問・回答だけなので，どれも変わらない候補の得点は温度 0 の同じ入力の結果として使い回せる．
    """
    seeded: list[Mapping[str, Any]] = []
    for new_row, old_row in zip(arm_rows_, saved_rows, strict=True):
        for index, (nc, oc) in enumerate(
            zip(
                new_row["owner_answer"]["candidates"],
                old_row["owner_answer"]["candidates"],
                strict=True,
            )
        ):
            same = nc["context_doc_ids"] == oc["context_doc_ids"] and nc["answer"] == oc["answer"]
            saved = saved_verify.get((new_row["qid"], index))
            if same and saved is not None:
                seeded.append(saved)
    return seeded


def write_arm_dir(
    out_dir: Path,
    rows: Sequence[Mapping[str, Any]],
    ce_rows: Sequence[Mapping[str, Any]],
    verify_rows: Sequence[Mapping[str, Any]],
) -> None:
    """腕の実行のディレクトリ：results.jsonl・ce_features.jsonl・verify_features.jsonl（種）を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, items in (
        ("results.jsonl", rows),
        (CE_FEATURES, ce_rows),
        (VERIFY_FEATURES, verify_rows),
    ):
        (out_dir / name).write_text(
            "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8"
        )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """jsonl を行ごとに読む（空行は飛ばす）．"""
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def recompute_ce_features(
    new_rows: Sequence[Mapping[str, Any]],
    saved_ce: Sequence[Mapping[str, Any]],
    plan: Sequence[Mapping[str, Any]],
    views: Mapping[str, InboxView],
    question_of: Mapping[str, str],
    scorer: RemoteCandidateScorer,
) -> list[dict[str, Any]]:
    """new 腕の ce_features：作り直す候補は新しい文脈の 5 通から `cross_encoder_features`（`select-features` と同じ関数）で計算し直し，他は保存した値．"""
    changed = {(p["qid"], p["index"]) for p in plan if p["changed"]}
    text_of = {d: view.ce_text(view.row_of[d]) for view in views.values() for d in view.paths}
    touched = [r for r in new_rows if r["b3ic_rebuilt"]]
    # 1 回目：`cross_encoder_features` が出す採点の呼び出し（質問，文の列）を集める．2 回目の前に並列で採点し，同じ呼び出しに得点を返す
    calls: dict[tuple[str, tuple[str, ...]], list[float]] = {}

    def record(query: str, texts: Sequence[str]) -> list[float]:
        calls.setdefault((query, tuple(texts)), [])
        return [0.0] * len(texts)

    cross_encoder_features(touched, question_of, text_of, record)
    with ThreadPoolExecutor(max_workers=SCORE_STREAMS_PER_SERVER * len(scorer.urls)) as pool:
        for key, scores in zip(
            calls, pool.map(lambda k: scorer.scores(k[0], list(k[1])), calls), strict=True
        ):
            calls[key] = scores
    fresh = {
        (f["qid"], f["index"]): f
        for f in cross_encoder_features(
            touched, question_of, text_of, lambda q, t: calls[(q, tuple(t))]
        )
        if (f["qid"], f["index"]) in changed
    }
    return [fresh.get((s["qid"], int(s["index"])), dict(s)) for s in saved_ce]


def write_stage_json(path: Path, doc: Mapping[str, Any]) -> None:
    """段階の結果の JSON を書く（インデント付き）．"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")


def write_rejection(out_dir: Path, verdict: Mapping[str, Any]) -> None:
    """G-D 不通過の verdict.json（`atrium.verdict.write_verdict` の書式）．"""
    write_verdict(out_dir, dict(verdict))


# ── 段階の実行（`atrium inbox-context-eval`） ───────────────────────────────────

STAGES = (
    "dev-ce",
    "dev-gate",
    "q-rrf",
    "q-ce",
    "q-plan",
    "q-ce-features",
    "q-generate",
    "g-gen",
    "q-build",
)
G_CE_EXPECTED_DTYPE = "float16"
G_CE_EXPECTED_MAX_LENGTH = 1024


def _require_chosen_rule(dev_dir: Path) -> str:
    """dev_gate.json から選んだ X を読む．G-D 不通過なら例外（Q の生成を流さない）．"""
    gate = json.loads((dev_dir / "dev_gate.json").read_text(encoding="utf-8"))
    if not gate["G_D"]["passed"]:
        raise SystemExit("G-D was not passed; the Q stages must not run (see verdict.json)")
    return str(gate["selection"]["chosen"])


def _read_plan(plan_dir: Path) -> list[dict[str, Any]]:
    """plan.jsonl を読む（q-plan の後）．"""
    path = plan_dir / PLAN
    if not path.exists():
        raise SystemExit(f"{path} is missing; run --stage q-plan first")
    return read_jsonl(path)


def run_stage(
    stage: str,
    cfg: AtriumConfig,
    paths: DatasetPaths,
    dev_dir: Path,
    plan_dir: Path,
    arm_prefix: str,
    verdict_dir: Path,
    scorer_urls: Sequence[str],
    node_urls: Sequence[str],
    limit: int | None,
    arm: str | None,
    max_failed: int = 0,
) -> dict[str, Any]:
    """段階 stage を実行し，要約（標準出力に出す JSON）を返す．詳しい出力は各ディレクトリのファイルに書く．

    verdict_dir は dev-gate が G-D 不通過の verdict.json（棄却）を書く先（最終の判定の置き場所）．
    max_failed は q-build が許す，生成が失敗した候補の数（既定 0．失敗した候補は両腕とも不正解として数える）．
    """
    from atrium.judge import question_texts

    k = cfg.data.require_enronqa().k_context
    depth = cfg.retrieval.fusion_depth
    if stage in ("dev-ce", "q-ce", "q-ce-features") and not scorer_urls:
        raise SystemExit(f"--stage {stage} requires --scorer-url")
    if stage in ("q-generate",) and not node_urls:
        raise SystemExit("--stage q-generate requires --node-url")
    if stage == "g-gen":
        plan = _read_plan(plan_dir)
        result = g_gen(plan, read_gen_records(plan_dir / GEN_RECORDS), read_q_rows())
        write_stage_json(plan_dir / "g_gen.json", result)
        return result
    views = load_inbox_views(cfg, paths)
    if stage == "dev-ce":
        questions = load_dev_questions(cfg, paths)
        jobs = score_jobs_for_dev(questions, views, depth)
        jobs = jobs if limit is None else jobs[:limit]
        conditions = scorer_conditions(scorer_urls)
        run = run_score_jobs(jobs, scorer_urls, dev_dir / CE_CACHE)
        cached = read_ce_cache(dev_dir / CE_CACHE)
        n_cached = sum((j.qid, j.source) in cached for j in jobs)
        finite = all(
            np.isfinite(s) for j in jobs for s in cached.get((j.qid, j.source), ([], [np.nan]))[1]
        )
        result = {
            "n_questions": len(jobs),
            "n_cached": n_cached,
            "conditions": conditions,
            "run": run,
            "all_finite": bool(finite),
            "passed": bool(
                n_cached == len(jobs)
                and run["n_failed"] == 0
                and run["n_nonfinite"] == 0
                and finite
                and all(
                    c.get("dtype") == G_CE_EXPECTED_DTYPE
                    and c.get("max_length") == G_CE_EXPECTED_MAX_LENGTH
                    for c in conditions
                )
            ),
        }
        name = "g_ce.json" if limit == GATE_SAMPLE else "dev_ce_run.json"
        write_stage_json(dev_dir / name, result)
        return result
    if stage == "dev-gate":
        questions = load_dev_questions(cfg, paths)
        cache = read_ce_cache(dev_dir / CE_CACHE)
        missing = [q.qid for q in questions if (q.qid, q.owner) not in cache]
        if missing:
            raise SystemExit(f"{len(missing)} dev questions have no cross-encoder scores")
        result = {
            "k": k,
            "fusion_depth": depth,
            **evaluate_dev_gate(dev_recall_flags(questions, views, cache, k, depth)),
        }
        write_stage_json(dev_dir / "dev_gate.json", result)
        rejection = gate_d_verdict(result)
        if rejection is not None:
            write_rejection(verdict_dir, rejection)
        return {key: result[key] for key in ("n", "recall", "selection", "G_D")}
    if stage == "q-rrf":
        result = g_rrf(cfg, paths, views)
        write_stage_json(plan_dir / "g_rrf.json", result)
        return result
    rule = _require_chosen_rule(dev_dir)
    if stage == "q-ce":
        if rule == "bm25":
            return {"rule": rule, "note": "bm25 needs no cross-encoder scores"}
        jobs = score_jobs_for_q(cfg, paths, views, CE_DEPTH_OF[rule])
        jobs = jobs if limit is None else jobs[:limit]
        result = run_score_jobs(jobs, scorer_urls, plan_dir / CE_CACHE)
        write_stage_json(plan_dir / "q_ce_run.json", result)
        return result
    g_rrf_path = plan_dir / "g_rrf.json"
    if not g_rrf_path.exists() or not json.loads(g_rrf_path.read_text("utf-8"))["passed"]:
        raise SystemExit("G-RRF is not passed; the later Q stages must not run")
    if stage == "q-plan":
        cache = read_ce_cache(plan_dir / CE_CACHE) if rule != "bm25" else {}
        plan = plan_q(cfg, paths, views, rule, cache)
        (plan_dir / PLAN).write_text("".join(json.dumps(p) + "\n" for p in plan), encoding="utf-8")
        result = {"rule": rule, **summarize_plan(plan)}
        write_stage_json(plan_dir / "plan.json", result)
        return result
    plan = _read_plan(plan_dir)
    questions_of = question_texts(paths)
    saved_rows = read_q_rows()
    saved_ce = [r for run, _, _ in Q_SOURCES for r in read_jsonl(Path(run) / CE_FEATURES)]
    if stage == "q-ce-features":
        new_ce = recompute_ce_features(
            new_context_rows(saved_rows, plan),
            saved_ce,
            plan,
            views,
            questions_of,
            RemoteCandidateScorer(scorer_urls),
        )
        (plan_dir / CE_FEATURES_NEW).write_text(
            "".join(json.dumps(r) + "\n" for r in new_ce), encoding="utf-8"
        )
        n_changed = sum(
            a["ce_max"] != b["ce_max"] or a["ce_top1"] != b["ce_top1"]
            for a, b in zip(new_ce, saved_ce, strict=True)
        )
        return {"n_candidates": len(new_ce), "n_ce_features_changed": n_changed}
    if stage == "q-generate":
        items = generation_items(plan, views, questions_of)
        if arm is not None:
            items = [i for i in items if i.arm == arm]
        items = items if limit is None else items[:limit]
        return run_generation(items, node_urls, plan_dir / GEN_RECORDS)
    if stage == "q-build":
        records = read_gen_records(plan_dir / GEN_RECORDS)
        failed = failed_generation_keys(plan, records)
        if len(failed) > max_failed:
            raise SystemExit(f"{len(failed)} candidates have failed generations (max {max_failed})")
        if not (plan_dir / CE_FEATURES_NEW).exists():
            raise SystemExit(f"{plan_dir / CE_FEATURES_NEW} is missing; run --stage q-ce-features")
        new_ce = read_jsonl(plan_dir / CE_FEATURES_NEW)
        saved_verify = {
            (j["qid"], int(j["index"])): j for _, _, v in Q_SOURCES for j in read_jsonl(Path(v))
        }
        summary: dict[str, Any] = {}
        for name in ARMS:
            rows = arm_rows(saved_rows, plan, records, name, frozenset(failed))
            ce_rows = saved_ce if name == "base" else new_ce
            seeded = seed_verify_rows(rows, saved_rows, saved_verify)
            write_arm_dir(Path(f"{arm_prefix}{name}"), rows, ce_rows, seeded)
            summary[name] = {
                "n_rows": len(rows),
                "n_rebuilt_candidates": sum(len(r["b3ic_rebuilt"]) for r in rows),
                "n_failed_generation_candidates": len(failed),
                "n_verify_seeded": len(seeded),
                "n_candidates": sum(len(r["owner_answer"]["candidates"]) for r in rows),
            }
        write_stage_json(plan_dir / "build.json", summary)
        return summary
    raise SystemExit(f"unknown stage {stage}")
