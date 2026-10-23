"""1 回の実験（E1 以降）の組み立て．質問者のコンテナの中で `atrium run` から呼ばれる．"""

from __future__ import annotations

import json
import logging
import random
import resource
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import httpx

from atrium.analysis import read_results
from atrium.benchmarks import load_questions
from atrium.config import AtriumConfig
from atrium.cpu_stat import diff_tcp_counters, read_device_cpu, read_tcp_counters
from atrium.data_enronqa import QUESTION_FILES, load_gold_docs
from atrium.manifest import Placement, read_placement
from atrium.paths import dataset_paths
from atrium.requester import (
    CachedQueryEmbedder,
    CrossEncoderReranker,
    LiveMedcptEmbedder,
    QueryEmbedder,
    Requester,
    apply_pool_assign,
    apply_sniffio_lookup,
    discover_shards,
    load_rm_qrels,
    run_questions,
)
from atrium.requester_procs import (
    control_cpu_fractions,
    expert_cpu_meta,
    merge_part_files,
    read_node_cpu_seconds,
    run_questions_in_processes,
)
from atrium.routing import (
    SourceProfile,
    build_source_profiles,
    make_router,
    with_card_embeddings,
)
from atrium.routing.central import CentralIndex

logger = logging.getLogger(__name__)

HttpPool = Literal["shared", "shared_unlimited", "per_node"]

RUN_META = "run_meta.json"
RESULTS = "results.jsonl"
# 50 台規模へ同時に問い合わせても接続数で詰まらないようにする
MAX_CONNECTIONS = 512
# per_node の 1 ノードあたりの上限．parallel ≤ 30 では 1 ノードへの同時の要求が 2 × parallel（probe 1 件＋
# 本照会 高々 m = 2 件の余裕を見て 60）を超えないので，上限に当たって接続を作り直すことはない
MAX_CONNECTIONS_PER_NODE = 64
# イメージの build 時にコミットを書き込むファイル（Dockerfile の GIT_HEAD．start.sh も同じファイルを読む）
IMAGE_GIT_HEAD_FILE = Path("/etc/atrium-git-head")


def read_image_git_head() -> str:
    """このイメージを build したときの git のコミット（イメージの外で動かしているときは unknown）．"""
    if not IMAGE_GIT_HEAD_FILE.exists():
        return "unknown"
    return IMAGE_GIT_HEAD_FILE.read_text(encoding="utf-8").strip()


def build_http_client(
    http_pool: HttpPool, placement: Placement, keepalive_expiry_s: float | None = 5.0
) -> httpx.AsyncClient:
    """質問者の HTTP クライアントを作る．池の持ち方だけを変え，要求の内容と宛先は変えない．

    keepalive_expiry_s は待機中の接続を捨てるまでの秒数（既定 5.0 は httpx の既定と同じ）．ノード（uvicorn）が待機中の接続を
    閉じる 5 秒と同じなので，同じ時刻に作った接続の束が閉じた直後に再利用されて ReadError になる（p0007 C の freshness-run）．
    その測定では 5 秒より短くして，閉じられる前に質問者側で捨てる．
    """
    if http_pool == "shared":
        return httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS, keepalive_expiry=keepalive_expiry_s
            )
        )
    if http_pool == "shared_unlimited":
        return httpx.AsyncClient(
            limits=httpx.Limits(max_connections=None, keepalive_expiry=keepalive_expiry_s)
        )
    # per_node：origin（scheme・host・port）ごとの mounts で別の AsyncHTTPTransport（別の池）へ振り分ける．
    # mounts に無い宛先（質問者の Ollama など）は既定の池（shared と同じ上限）に行く
    mounts: dict[str, httpx.AsyncBaseTransport] = {
        origin: httpx.AsyncHTTPTransport(
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS_PER_NODE, keepalive_expiry=keepalive_expiry_s
            )
        )
        for origin in {
            str(httpx.URL(node.url).copy_with(path="/")).rstrip("/") for node in placement.nodes
        }
    }
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=MAX_CONNECTIONS, keepalive_expiry=keepalive_expiry_s),
        mounts=mounts,
    )


def _make_embedder(
    cfg: AtriumConfig, data_dir: Path, sources: list[SourceProfile]
) -> QueryEmbedder:
    dataset = cfg.experiment.dataset
    paths = dataset_paths(data_dir, dataset)
    if cfg.retrieval.query_embedding[dataset] == "live":
        if dataset != "medrag":
            raise ValueError("live query embedding is only supported for medrag")
        m = cfg.data.medrag
        return LiveMedcptEmbedder(m.query_encoder, m.article_encoder, m.embed_precision)
    # central_local はノードを発見せず（sources が空），EnronQA の符号化器の埋め込みだけを使う
    encoders = sorted({s.encoder for s in sources}) or [cfg.data.require_enronqa().encoder]
    return CachedQueryEmbedder(paths.query_ids, {e: paths.query_embeddings(e) for e in encoders})


async def build_requester(
    cfg: AtriumConfig,
    data_dir: Path,
    placement: Placement,
    client: httpx.AsyncClient,
    ollama_url: str,
    tolerate_node_failures: bool = False,
) -> tuple[Requester, list[SourceProfile], float]:
    """シャードを発見して質問者を組み立てる．（質問者，データ源，発見にかかった秒数）を返す．

    複数の質問者のプロセス（atrium.requester_procs）では，各プロセスがこれを自分の client で呼ぶ．
    tolerate_node_failures は，止まったノードの失敗を問の失敗にしない（p0007 I6 の failure-run．churn の指定が無くても）．
    """
    dataset = cfg.experiment.dataset
    paths = dataset_paths(data_dir, dataset)
    if cfg.experiment.routing == "central_local":
        # p0007 I4：ノードには問い合わせない（発見もしない）．質問者が全メールの索引を作る（その費用は index.load に残る）
        central = CentralIndex.from_corpus(cfg, paths)
        requester = Requester(
            cfg=cfg,
            placement=placement,
            sources=[],
            router=make_router(cfg, paths.router, paths.labels),
            embedder=_make_embedder(cfg, data_dir, []),
            client=client,
            ollama_url=ollama_url,
            central_index=central,
            # retrieval.merge=cross_encoder（p0007 A5 の C1-RRF-CE）：索引の上位を再ランクする
            reranker=CrossEncoderReranker() if cfg.retrieval.merge == "cross_encoder" else None,
        )
        return requester, [], 0.0
    start = time.perf_counter()
    shards = await discover_shards(client, placement)
    discovery_s = time.perf_counter() - start
    expected = cfg.data.sources_of(dataset)
    sources = build_source_profiles(shards, expected)
    missing = sorted(set(expected) - {s.source for s in sources})
    if missing:
        raise RuntimeError(f"no node announced sources {missing}; check deploy")
    if cfg.experiment.routing == "card_sim" and dataset == "medrag":
        # MedRAG のデータ源は広告を持たないので，自己紹介文を S4 の評価と同じ埋め込みで付ける（p0003 S5）
        from atrium.loso import embed_descriptions

        sources = with_card_embeddings(sources, embed_descriptions(cfg, sources))
    needs_cross_encoder = cfg.retrieval.merge == "cross_encoder" or (
        cfg.experiment.answer_mode == "owner_answer" and cfg.answer.aggregate == "cross_encoder"
    )
    reranker = CrossEncoderReranker() if needs_cross_encoder else None
    requester = Requester(
        cfg=cfg,
        placement=placement,
        sources=sources,
        router=make_router(cfg, paths.router, paths.labels),
        embedder=_make_embedder(cfg, data_dir, sources),
        client=client,
        ollama_url=ollama_url,
        tolerate_node_failures=tolerate_node_failures,
        reranker=reranker,
        # answer.aggregate=cross_encoder（p0007 F-6）：候補の文脈を質問者の GPU の cross-encoder で採点する
        candidate_scorer=reranker if cfg.answer.aggregate == "cross_encoder" else None,
        rm_qrels=load_rm_qrels(paths.rm_qrels) if cfg.retrieval.merge == "qrels_oracle" else {},
        gold_docs=(
            load_gold_docs(paths, [q.qid for q in load_questions(paths.questions, None)])
            if cfg.experiment.answer_mode == "gold_context"
            else {}
        ),
    )
    return requester, sources, discovery_s


def _write_meta(out_dir: Path, meta: dict[str, Any]) -> None:
    (out_dir / RUN_META).write_text(
        json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8"
    )


async def run_experiment(
    cfg: AtriumConfig, data_dir: Path, placement_path: Path, out_dir: Path, ollama_url: str
) -> int:
    """全質問を処理し，results.jsonl と run_meta.json を書く．失敗した問数を返す．"""
    dataset = cfg.experiment.dataset
    paths = dataset_paths(data_dir, dataset)
    placement = read_placement(placement_path)
    # dev は enronqa だけ（config が検査済み）．dev_questions.jsonl は test の questions.jsonl と同じ書式
    questions_path = (
        paths.questions.parent / QUESTION_FILES["dev"]
        if cfg.experiment.question_split == "dev"
        else paths.questions
    )
    all_questions = load_questions(questions_path, cfg.experiment.question_limit)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 複数のプロセスで流した前回の実行が途中で落ちていれば，プロセスごとの結果を先にまとめる
    merge_part_files(out_dir / RESULTS)
    # 同じ run_id で再開したときは，成功済みの質問を飛ばす（失敗した質問はやり直す）
    done = (
        {r["qid"] for r in read_results(out_dir / RESULTS) if r.get("error") is None}
        if (out_dir / RESULTS).exists()
        else set()
    )
    questions = [q for q in all_questions if q.qid not in done]
    churn = cfg.experiment.churn
    if churn is not None:
        # EQ7b：EQ7a と同じ順番に並べ，途中からの再開はしない（刻みと各ノードの状態の対応が崩れるため）
        from atrium.churn_live import question_order

        if cfg.experiment.question_limit is not None:
            raise ValueError(
                "experiment.churn replays the whole test set; question_limit must be null"
            )
        questions = [
            all_questions[i] for i in question_order(len(all_questions), cfg.experiment.seed)
        ]
    elif dataset == "enronqa":
        # EnronQA の質問は受信箱（＝専門家のノード）ごとに並んでいる．そのまま並列に処理すると同時の質問が
        # 1 台のノードに集まり，CPU の Ollama の待ち行列が llm.timeout_s を超えて失敗した（2026-10-09 の EQ5 の B2）．
        # 種で決まる順に並べ替えて負荷を分散する．質問ごとに独立で温度 0 なので，回答は変わらない
        random.Random(cfg.experiment.seed).shuffle(questions)
    previous: dict[str, Any] = (
        json.loads((out_dir / RUN_META).read_text(encoding="utf-8"))
        if (out_dir / RUN_META).exists()
        else {}
    )
    # httpx の部品を作る前に置く（質問者の経路の import の探索を省く．spawn の子は requester_procs が自分で置く）
    sniffio_installed = apply_sniffio_lookup(cfg.experiment.sniffio_lookup)
    httpcore_version = apply_pool_assign(cfg.experiment.pool_assign)
    async with build_http_client(cfg.experiment.http_pool, placement) as client:
        requester, sources, discovery_s = await build_requester(
            cfg, data_dir, placement, client, ollama_url
        )
        meta: dict[str, Any] = {
            "run_id": out_dir.name,
            "git_head": read_image_git_head(),
            "dataset": dataset,
            "routing": cfg.experiment.routing,
            "answer_mode": cfg.experiment.answer_mode,
            # gold_context は検索を経ない評価専用の条件で，運用できない（p0006 C0）
            "evaluation_only": cfg.experiment.answer_mode == "gold_context",
            "merge": cfg.retrieval.merge,
            # ルーティングのために事前に公開された情報の大きさ（p0004 §8.4．使わない方式は 0）
            "advert_bytes": getattr(requester.router, "advert_bytes", lambda _: 0)(sources),
            "question_split": cfg.experiment.question_split,
            "n_questions": len(all_questions),
            "n_skipped_on_resume": len(done),
            "n_nodes": len(placement.nodes),
            "discovery_s": discovery_s,
            "sources": [
                {"source": s.source, "n_docs": s.n_docs, "shards": list(s.shard_ids)}
                for s in sources
            ],
            "config": cfg.model_dump(mode="json"),
            "started_at": previous.get("started_at", time.time()),
            "http_pool": cfg.experiment.http_pool,
            "requester_procs": cfg.experiment.requester_procs,
            "probe_body": cfg.experiment.probe_body,
            "retrieve_body": cfg.experiment.retrieve_body,
            "sniffio_lookup": cfg.experiment.sniffio_lookup,
            "sniffio_installed": sniffio_installed,
            "pool_assign": cfg.experiment.pool_assign,
            "httpcore_version": httpcore_version,
        }
        if requester.central_index is not None:
            # p0007 §6.5 E1：質問者の起動のたびに払う，全メールの索引を作る（読み込む）費用（秒）
            meta["central_index"] = asdict(requester.central_index.load)
        _write_meta(out_dir, meta)
        finished_at: float | None = None
        # 質問の処理（N ≥ 2 では子のプロセスの起動も含む）の前後の，質問者のネットワークの名前空間の TCP の再送の計数（記録だけ）
        tcp_before = read_tcp_counters()
        if churn is not None:
            from atrium.churn_live import build_schedule, run_churn_questions

            schedule = build_schedule(cfg, churn, len(placement.nodes), len(questions))
            await run_churn_questions(requester, questions, out_dir / RESULTS, churn, schedule)
        elif cfg.experiment.requester_procs == 1:
            # 質問の処理の区間の質問者のプロセスの CPU 時間（1 コアの飽和 = requester_cpu_util 1.0）．
            # 専門家と質問者のデバイス全体の CPU 時間は，この区間のすぐ外側で読む
            node_before = await read_node_cpu_seconds(client, placement)
            control_before = read_device_cpu()
            cpu_before = resource.getrusage(resource.RUSAGE_SELF)
            wall_before = time.perf_counter()
            await run_questions(requester, questions, out_dir / RESULTS, cfg.experiment.parallel)
            wall_s = time.perf_counter() - wall_before
            cpu_after = resource.getrusage(resource.RUSAGE_SELF)
            control_after = read_device_cpu()
            node_after = await read_node_cpu_seconds(client, placement)
            cpu_s = (cpu_after.ru_utime - cpu_before.ru_utime) + (
                cpu_after.ru_stime - cpu_before.ru_stime
            )
            meta.update(
                {
                    "requester_cpu_user_s": cpu_after.ru_utime - cpu_before.ru_utime,
                    "requester_cpu_sys_s": cpu_after.ru_stime - cpu_before.ru_stime,
                    "requester_cpu_s": cpu_s,
                    "requester_cpu_util": cpu_s / wall_s,
                    "requester_cpu_util_per_proc": [cpu_s / wall_s],
                    "requester_parallel_per_proc": [cfg.experiment.parallel],
                    **expert_cpu_meta(node_before, node_after, wall_s),
                    **control_cpu_fractions(control_before, control_after),
                }
            )
        else:
            outcome = await run_questions_in_processes(
                cfg,
                data_dir,
                placement_path,
                placement,
                ollama_url,
                questions,
                out_dir / RESULTS,
                client,
            )
            # 処理量の区間は，全プロセスが一斉に流し始めてから最後のプロセスが終えるまで（準備の時間を含めない）
            meta["started_at"] = previous.get("started_at", outcome.started_at)
            finished_at = outcome.finished_at
            wall_s = outcome.finished_at - outcome.started_at
            user_s = sum(w.cpu_user_s for w in outcome.workers)
            sys_s = sum(w.cpu_sys_s for w in outcome.workers)
            meta.update(
                {
                    "requester_cpu_user_s": user_s,
                    "requester_cpu_sys_s": sys_s,
                    "requester_cpu_s": user_s + sys_s,
                    # 全プロセスの合計（コアの数の単位．N 個のプロセスが全て飽和すれば N）
                    "requester_cpu_util": (user_s + sys_s) / wall_s,
                    "requester_cpu_util_per_proc": [
                        (w.cpu_user_s + w.cpu_sys_s) / w.wall_s for w in outcome.workers
                    ],
                    "requester_parallel_per_proc": [w.parallel for w in outcome.workers],
                    "requester_wall_s_per_proc": [w.wall_s for w in outcome.workers],
                    **expert_cpu_meta(outcome.node_cpu_before, outcome.node_cpu_after, wall_s),
                    **control_cpu_fractions(outcome.control_cpu_before, outcome.control_cpu_after),
                }
            )
        meta["requester_tcp"] = diff_tcp_counters(tcp_before, read_tcp_counters())
        # 再開した場合も含め，各質問の最後の結果で失敗を数える
        failures = sum(r.get("error") is not None for r in read_results(out_dir / RESULTS))
        meta.update(
            {
                "finished_at": finished_at if finished_at is not None else time.time(),
                "failures": failures,
            }
        )
        _write_meta(out_dir, meta)
    logger.info("finished %d questions (%d failures)", len(questions), failures)
    return failures
