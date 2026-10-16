"""atrium のコマンドライン（`uv run atrium ...` またはコンテナ内の `atrium ...`）．

主なサブコマンド（詳細は `atrium <cmd> --help`）:

    config get KEY            config.yaml の値を出す（例: cluster.control）
    plan                      manifest.json から placement.json を作る（--tsv でシェル向けの表も出す）
    node                      専門家ノードを起動する（コンテナ内）
    run                       質問者として実験を行う（コンテナ内）
    data {medrag,feb4rag} STEP   データ中継点での準備（コンテナ内）
    embed-plan                MedRAG の埋め込みを複数の GPU で分担する表を作る（コンテナ内）
    fetch-models DATASET      データセットで使うモデルを Hugging Face のキャッシュへ取得する（コンテナ内）
    analyze / metrics / compare  結果の分析（操作端末）
    advert-eval               RQ-A のオフラインの評価（EnronQA．制御点のコンテナ）
    cascade-eval              二段ルーティング centroid_probe のオフラインの評価（EnronQA．制御点のコンテナ）
    probe-reach               centroid_probe の K ごとの届くデバイスの数と再現率（EnronQA．Iteration 4．制御点のコンテナ）
    device-probe-eval         device_probe の D ごとの hit@m と D の選択（EnronQA．Iteration 7．制御点のコンテナ）
    node-bench                専門家ノード 1 台への閉じた系の probe の負荷試験（EnronQA．Iteration 9．制御点のコンテナ）
    churn-eval                RQ-C のオフラインの評価（EnronQA．参加・離脱とメンバー表の古さ．制御点のコンテナ）
    churn-cascade-eval        二段の方式の参加・離脱への頑健さのオフラインの評価（EnronQA．Iteration 15．制御点のコンテナ）
    churn-join-eval           参加の通知の遅れ L ごとのメンバー表の見え方と hit の回復（EnronQA．Iteration 16．制御点のコンテナ）
    churn-leave-eval          離脱の通知の遅れ L_leave ごとの無駄な問い合わせと hit の回復（EnronQA．Iteration 17．制御点のコンテナ）
    churn-refill-eval         返事の無い probe の枠を次の順位で補ったときの hit と問い合わせの費用（EnronQA．Iteration 18．制御点のコンテナ）
    loso DATASET --split S    E2 の leave-one-source-out の評価（p0003．操作端末）
    newcomer-eval             150 人での新参者の評価（EnronQA．p0005 T3．制御点のコンテナ）
    owner-eval                持ち主推定の入力を揃えた評価（EnronQA．p0005 T2．制御点のコンテナ）
    header-eval               持ち主推定の入力からヘッダーを除いた測り直しと質問の人名の集計（EnronQA．Iteration 2．制御点のコンテナ）
    body-advert-eval          公開情報をヘッダーを除いた本文から作り直した RQ-A の測り直し（EnronQA．Iteration 3．制御点のコンテナ）
    churn-live-report  EQ7b の実機の実行を集めて EQ7a と比べる（操作端末）
    judge / judge-validate / judge-kappa  EnronQA の回答の採点と判定モデルの検証（制御点のコンテナ）
    e0 {faiss,medcpt,summarize}  E0 の実測と集約
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from atrium.config import DEFAULT_CONFIG_PATH, AtriumConfig, load_config
from atrium.manifest import (
    plan_placement,
    plan_placement_balanced,
    read_manifest,
    write_json_model,
)
from atrium.paths import dataset_paths

MEDRAG_STEPS = ("benchmark", "corpus", "embed", "shards", "queries", "labels", "split", "train")
FEB4RAG_STEPS = ("fetch", "beir", "shards", "queries", "split", "train")
ENRONQA_STEPS = ("fetch", "corpus", "embed", "questions", "queries", "labels", "shards", "train")
STEPS_OF = {"medrag": MEDRAG_STEPS, "feb4rag": FEB4RAG_STEPS, "enronqa": ENRONQA_STEPS}


def _config_get(cfg: AtriumConfig, key: str) -> Any:
    value: Any = cfg.model_dump(mode="json")
    for part in key.split("."):
        value = value[part]
    return value


def cmd_config(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """config.yaml の値を 1 項目出す（リストは 1 行 1 要素）．"""
    value = _config_get(cfg, args.key)
    if isinstance(value, list):
        print("\n".join(str(v) for v in value))
    elif isinstance(value, dict):
        print(json.dumps(value))
    else:
        print(value)
    return 0


def cmd_plan(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """シャードを専門家へ割り当てる．"""
    manifest = read_manifest(Path(args.manifest))
    if manifest.dataset == "enronqa":
        placement = plan_placement_balanced(
            manifest,
            cfg.cluster.expert_hosts(),
            cfg.cluster.node_port,
            cfg.experiment.seed,
            args.gpu_hosts,
        )
    else:
        placement = plan_placement(manifest, cfg.cluster.expert_hosts(), cfg.cluster.node_port)
    write_json_model(Path(args.out), placement)
    if args.tsv:
        for node in placement.nodes:
            print(f"{node.host}\t{','.join(node.shard_ids)}")
    return 0


def cmd_node(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """専門家ノードを起動する．"""
    from atrium.node import main

    shard_ids = [s for s in args.shard_ids.split(",") if s]
    main(
        Path(args.config),
        args.host,
        args.port,
        args.node_id,
        Path(args.shards_dir),
        shard_ids,
        args.ollama_url,
        args.expert_model,
    )
    return 0


def cmd_render_compose(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """compose のひな形（docker/compose.<role>.yml）を，値を埋めた compose.yml として標準出力へ出す．"""
    from atrium.render import render_compose

    print(
        render_compose(
            cfg, args.role, args.uid, args.gid, node_id=args.node_id, shard_ids=args.shard_ids
        ),
        end="",
    )
    return 0


def cmd_run(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """質問者として実験を行う．1 問でも失敗すれば終了コード 2 を返す（結果は残す）．"""
    from atrium.experiment import run_experiment

    failures = asyncio.run(
        run_experiment(
            cfg, Path(args.data_dir), Path(args.placement), Path(args.out_dir), args.ollama_url
        )
    )
    return 2 if failures else 0


def cmd_data(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """データ中継点での準備を段ごとに行う（all は全段を順に行う）．"""
    from atrium import labels, train_router

    paths = dataset_paths(Path(args.data_dir), args.dataset)
    valid = STEPS_OF[args.dataset]
    steps = valid if args.step == "all" else (args.step,)
    if args.step != "all" and args.step not in valid:
        raise SystemExit(
            f"step {args.step!r} is not defined for {args.dataset}; choose from {valid}"
        )
    sources = [args.source] if args.source else cfg.data.sources_of(args.dataset)
    for step in steps:
        logging.info("data %s: %s", args.dataset, step)
        if args.dataset == "medrag":
            from atrium import data_medrag as d

            if step == "benchmark":
                d.fetch_benchmark(cfg, paths)
            elif step == "corpus":
                for s in sources:
                    d.fetch_corpus(cfg, paths, s)
            elif step == "embed":
                only = None
                if args.only_list:
                    only = set(Path(args.only_list).read_text(encoding="utf-8").split())
                for s in sources:
                    d.embed_corpus(cfg, paths, s, only)
            elif step == "shards":
                d.build_shards(cfg, paths)
            elif step == "queries":
                d.embed_queries(cfg, paths)
            elif step == "labels":
                labels.compute_medrag_labels(cfg, paths)
        elif args.dataset == "enronqa":
            from atrium import data_enronqa as eq

            if step == "fetch":
                eq.fetch(cfg, paths)
            elif step == "corpus":
                eq.write_corpus(cfg, paths)
            elif step == "embed":
                eq.embed_corpus(cfg, paths)
            elif step == "questions":
                eq.write_questions_files(cfg, paths)
            elif step == "queries":
                eq.embed_queries(cfg, paths)
            elif step == "labels":
                eq.write_labels(cfg, paths)
            elif step == "shards":
                eq.build_shards(cfg, paths)
        else:
            from atrium import data_feb4rag as f

            if step == "fetch":
                f.fetch_repo(cfg, paths)
            elif step == "beir":
                for s in sources:
                    f.fetch_beir(cfg, paths, s)
            elif step == "shards":
                f.build_shards(cfg, paths)
            elif step == "queries":
                f.embed_queries(cfg, paths)
        if step == "split":
            labels.write_split(args.dataset, paths)
        elif step == "train" and not (paths.router / "router.pt").exists():
            report = train_router.train_router(cfg, args.dataset, paths)
            print(json.dumps(report, indent=1))
    return 0


def models_for(cfg: AtriumConfig, dataset: str) -> list[str]:
    """データセットの準備と実験で使う Hugging Face のモデルの一覧．"""
    from atrium.encoders import FEB4RAG_ENCODERS
    from atrium.requester import CrossEncoderReranker

    if dataset == "medrag":
        m = cfg.data.medrag
        return [m.query_encoder, m.article_encoder, CrossEncoderReranker.MODEL]
    if dataset == "enronqa":
        return [cfg.data.require_enronqa().encoder]
    return sorted({spec.hf_name for spec in FEB4RAG_ENCODERS.values()})


def cmd_fetch_models(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """モデルを Hugging Face のキャッシュ（HF_HOME）へ取得する．取得済みなら何もしない．"""
    from huggingface_hub import snapshot_download

    from atrium.hf_revisions import pinned_revision

    # 実行時に読む版と同じ commit を取得する（refs/main ではなく固定した版の snapshot が要る）
    for name in models_for(cfg, args.dataset):
        logging.info("fetching %s", name)
        snapshot_download(repo_id=name, revision=pinned_revision(name))
    return 0


def cmd_advert_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """RQ-A のオフラインの評価（p0004 EQ2）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import advert_eval

    metrics = advert_eval.evaluate(cfg, dataset_paths(Path(args.data_dir), "enronqa"))
    advert_eval.write_outputs(Path(args.out_dir), metrics)
    print(advert_eval.render_report(metrics))
    return 0


def cmd_cascade_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """二段ルーティング centroid_probe のオフラインの評価を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import cascade_eval

    reference = json.loads(Path(args.reference_metrics).read_text(encoding="utf-8"))
    metrics = cascade_eval.evaluate(cfg, dataset_paths(Path(args.data_dir), "enronqa"), reference)
    cascade_eval.write_outputs(Path(args.out_dir), metrics)
    print(cascade_eval.render_report(metrics))
    return 0


def cmd_probe_reach(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """centroid_probe の K ごとの届くデバイスの数と再現率（Iteration 4）を出し，metrics.json と analysis_report.md を書く．"""
    from atrium import probe_reach
    from atrium.manifest import read_placement

    metrics = probe_reach.evaluate(
        cfg, dataset_paths(Path(args.data_dir), "enronqa"), read_placement(Path(args.placement))
    )
    probe_reach.write_outputs(Path(args.out_dir), metrics)
    print(probe_reach.render_report(metrics))
    return 0


def cmd_device_probe_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """device_probe の D ごとの hit@m と D の選択（Iteration 7）を出し，metrics.json と analysis_report.md を書く．"""
    from atrium import device_probe_eval
    from atrium.manifest import read_placement

    reference = json.loads(Path(args.reference_metrics).read_text(encoding="utf-8"))
    metrics = device_probe_eval.evaluate(
        cfg,
        dataset_paths(Path(args.data_dir), "enronqa"),
        read_placement(Path(args.placement)),
        reference,
    )
    device_probe_eval.write_outputs(Path(args.out_dir), metrics)
    print(device_probe_eval.render_report(metrics))
    return 0


def cmd_node_bench(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """専門家ノード 1 台への閉じた系の probe の負荷試験（Iteration 9）を行い，node_bench.json と scores.json を書く．"""
    from atrium import node_bench
    from atrium.experiment import read_image_git_head
    from atrium.manifest import read_placement

    order = [int(c) for c in args.concurrency.split(",")]
    if not order or min(order) < 1:
        raise SystemExit(f"--concurrency must be positive integers: {args.concurrency!r}")
    bench, scores = asyncio.run(
        node_bench.run_bench(
            read_placement(Path(args.placement)),
            Path(args.data_dir),
            args.target_host,
            order,
            cfg.experiment.seed,
            args.max_procs,
            args.warmup_s,
            args.measure_s,
            read_image_git_head(),
        )
    )
    node_bench.write_outputs(Path(args.out_dir), bench, scores)
    print(node_bench.render_table(bench))
    return 0


def cmd_churn_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """RQ-C のオフラインの評価（p0004 EQ7a）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import churn_eval
    from atrium.manifest import read_placement

    reference = json.loads(Path(args.reference_metrics).read_text(encoding="utf-8"))
    metrics = churn_eval.evaluate(
        cfg,
        dataset_paths(Path(args.data_dir), "enronqa"),
        read_placement(Path(args.placement)),
        reference,
    )
    churn_eval.write_outputs(Path(args.out_dir), metrics)
    print(churn_eval.render_report(metrics))
    return 0


def cmd_churn_cascade_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """二段の方式の参加・離脱への頑健さのオフラインの評価（Iteration 15）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import churn_cascade_eval
    from atrium.manifest import read_placement

    device_probe_reference = json.loads(Path(args.device_probe_metrics).read_text(encoding="utf-8"))
    churn_reference = json.loads(Path(args.churn_metrics).read_text(encoding="utf-8"))
    metrics = churn_cascade_eval.evaluate(
        cfg,
        dataset_paths(Path(args.data_dir), "enronqa"),
        read_placement(Path(args.placement)),
        device_probe_reference,
        churn_reference,
    )
    churn_cascade_eval.write_outputs(Path(args.out_dir), metrics)
    print(churn_cascade_eval.render_report(metrics))
    return 0


def cmd_churn_join_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """参加の通知の遅れ L ごとの評価（Iteration 16）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import churn_join_eval
    from atrium.manifest import read_placement

    cascade_reference = json.loads(Path(args.cascade_metrics).read_text(encoding="utf-8"))
    metrics = churn_join_eval.evaluate(
        cfg,
        dataset_paths(Path(args.data_dir), "enronqa"),
        read_placement(Path(args.placement)),
        cascade_reference,
    )
    churn_join_eval.write_outputs(Path(args.out_dir), metrics)
    print(churn_join_eval.render_report(metrics))
    return 0


def cmd_churn_leave_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """離脱の通知の遅れ L_leave ごとの評価（Iteration 17）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import churn_leave_eval
    from atrium.manifest import read_placement

    join_reference = json.loads(Path(args.join_metrics).read_text(encoding="utf-8"))
    metrics = churn_leave_eval.evaluate(
        cfg,
        dataset_paths(Path(args.data_dir), "enronqa"),
        read_placement(Path(args.placement)),
        join_reference,
    )
    churn_leave_eval.write_outputs(Path(args.out_dir), metrics)
    print(churn_leave_eval.render_report(metrics))
    return 0


def cmd_churn_refill_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """返事の無い probe の枠を補う変種の評価（Iteration 18）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import churn_refill_eval
    from atrium.manifest import read_placement

    join_reference = json.loads(Path(args.join_metrics).read_text(encoding="utf-8"))
    metrics = churn_refill_eval.evaluate(
        cfg,
        dataset_paths(Path(args.data_dir), "enronqa"),
        read_placement(Path(args.placement)),
        join_reference,
    )
    churn_refill_eval.write_outputs(Path(args.out_dir), metrics)
    print(churn_refill_eval.render_report(metrics))
    return 0


def cmd_owner_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """持ち主推定の定義の統一（p0005 T2）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import owner_eval

    reference = json.loads(Path(args.reference_metrics).read_text(encoding="utf-8"))
    metrics = owner_eval.evaluate(cfg, dataset_paths(Path(args.data_dir), "enronqa"), reference)
    owner_eval.write_outputs(Path(args.out_dir), metrics)
    print(owner_eval.render_report(metrics))
    return 0


def cmd_header_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """T2 の入力からヘッダーを除いた持ち主推定の測り直し（Iteration 2 の R1・R2）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import header_eval

    references = {
        key: json.loads(Path(path).read_text(encoding="utf-8"))
        for key, path in (
            ("eq2", args.reference_metrics),
            ("t2", args.t2_metrics),
            ("cascade", args.cascade_metrics),
        )
    }
    metrics = header_eval.evaluate(cfg, dataset_paths(Path(args.data_dir), "enronqa"), references)
    header_eval.write_outputs(Path(args.out_dir), metrics)
    print(header_eval.render_report(metrics))
    return 0


def cmd_body_advert_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """公開情報をヘッダーを除いた本文から作り直した RQ-A の測り直し（Iteration 3）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import body_advert_eval

    references = {
        key: json.loads(Path(path).read_text(encoding="utf-8"))
        for key, path in (
            ("eq2", args.reference_metrics),
            ("header", args.header_metrics),
            ("cascade", args.cascade_metrics),
        )
    }
    metrics = body_advert_eval.evaluate(
        cfg, dataset_paths(Path(args.data_dir), "enronqa"), references
    )
    body_advert_eval.write_outputs(Path(args.out_dir), metrics)
    print(body_advert_eval.render_report(metrics))
    return 0


def cmd_newcomer_eval(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """150 人での新参者の評価（p0005 T3）を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import newcomer_eval

    reference = json.loads(Path(args.reference_metrics).read_text(encoding="utf-8"))
    metrics = newcomer_eval.evaluate(
        cfg,
        dataset_paths(Path(args.data_dir), "enronqa"),
        reference,
        Path(args.work_dir),
        args.n_newcomers,
    )
    newcomer_eval.write_outputs(Path(args.out_dir), metrics)
    print(newcomer_eval.render_report(metrics))
    return 0


def cmd_churn_live_report(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """EQ7b の実機の実行を集め，EQ7a の値と比べて metrics.json と analysis_report.md を書く（操作端末）．"""
    from atrium import churn_live

    offline = json.loads(Path(args.offline_metrics).read_text(encoding="utf-8"))
    metrics = churn_live.evaluate_live([Path(d) for d in args.runs], offline)
    churn_live.write_live_outputs(Path(args.out_dir), metrics)
    print(churn_live.render_live_report(metrics))
    return 0


def cmd_loso(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """p0003 E2 の LOSO の評価を行い，metrics.json と analysis_report.md を書く．"""
    from atrium import loso

    describe = loso.embed_descriptions if args.dataset in loso.FULL_EVALUATION_DATASETS else None
    paths = dataset_paths(Path(args.data_dir), args.dataset)
    metrics = loso.evaluate(cfg, args.dataset, paths, args.split, describe)
    loso.write_outputs(Path(args.out_dir), metrics)
    print(loso.render_report(metrics))
    return 0


def cmd_judge(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """EnronQA の回答を判定モデルで採点する（judge / judge-validate / judge-kappa．p0004 §8.6）．"""
    from atrium import judge

    e = cfg.data.require_enronqa()
    paths = dataset_paths(Path(args.data_dir), "enronqa")
    if args.cmd == "judge":
        for run_dir in args.run_dir:
            summary = judge.judge_run(cfg, paths, Path(run_dir), args.ollama_url, e.judge_model)
            print(run_dir, json.dumps(summary))
        return 0
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "judge-validate":
        result = judge.validate_judge(
            cfg, paths, args.ollama_url, e.judge_model, e.judge_validate_n
        )
    else:
        result = judge.kappa_against_reference(
            cfg,
            paths,
            [Path(r) for r in args.run_dir],
            args.ollama_url,
            e.judge_reference_model,
            e.judge_kappa_n,
        )
    (out_dir / "metrics.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


def cmd_check_data(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """今の設定が，データ準備でラベルを作ったときの設定と同じかを確かめる（deploy が呼ぶ）．"""
    from atrium.labels import check_labels_meta

    check_labels_meta(
        cfg, cfg.experiment.dataset, dataset_paths(Path(args.data_dir), cfg.experiment.dataset)
    )
    return 0


def cmd_embed_plan(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """未埋め込みの断片ファイルを担当者へ振り分け，担当者ごとの一覧を書く．"""
    from atrium import data_medrag

    plan = data_medrag.plan_embedding(
        cfg, dataset_paths(Path(args.data_dir), "medrag"), args.workers
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for worker, keys in plan.items():
        (out_dir / f"{worker}.txt").write_text("".join(f"{k}\n" for k in keys), encoding="utf-8")
        print(f"{worker}\t{len(keys)}")
    return 0


def _latest_run(results_dir: Path) -> Path:
    runs = sorted(p for p in results_dir.iterdir() if (p / "run_meta.json").exists())
    if not runs:
        raise FileNotFoundError(f"no runs under {results_dir}")
    return runs[-1]


def _analyze(run_dir: Path, artifacts_dir: Path) -> dict[str, Any]:
    from atrium.analysis import compute_metrics, read_results

    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    paths = dataset_paths(artifacts_dir, meta["dataset"])
    labels = json.loads(paths.labels.read_text(encoding="utf-8"))
    split = json.loads(paths.split.read_text(encoding="utf-8")) if paths.split.exists() else {}
    n_sources = len(meta["sources"])
    # ラベル（統合後の上位 k_rerank 件に断片を出したデータ源）と実機の結果が一致するはずなのは，
    # ラベルを作ったときと同じ条件（MedRAG・全データ源・検索スコアで統合）の実行だけ
    consistent_setting = (
        meta["dataset"] == "medrag" and meta["routing"] == "all" and meta["merge"] == "score"
    )
    rows = read_results(run_dir / "results.jsonl")
    judgements = run_dir / "judgements.jsonl"
    if judgements.exists():
        # EnronQA の自由記述の回答は，判定モデル（atrium judge）の採点で正誤を付ける
        from atrium.analysis import merge_judgements

        rows = merge_judgements(rows, judgements)
    # EnronQA は正解のメールの path が分かるので，下流の検索再現率も出す（本文は含まない gold.jsonl）
    gold_file = paths.questions.parent / "gold.jsonl"
    gold_doc = (
        {g["qid"]: g["path"] for g in map(json.loads, gold_file.read_text("utf-8").splitlines())}
        if gold_file.exists()
        else None
    )
    return compute_metrics(rows, labels, split, n_sources, consistent_setting, gold_doc)


def cmd_analyze(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """metrics.json と analysis_report.md を書く．"""
    from atrium.analysis import render_report

    run_dir = Path(args.run_dir) if args.run_dir else _latest_run(Path(args.results_dir))
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    if meta.get("kind") == "e0_measure":
        raise SystemExit(f"{run_dir} is an E0 run; use `atrium e0 summarize --dir {run_dir}/e0`")
    metrics = _analyze(run_dir, Path(args.artifacts_dir))
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (run_dir / "analysis_report.md").write_text(render_report(meta, metrics), encoding="utf-8")
    print(run_dir / "analysis_report.md")
    return 0


def cmd_metrics(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """最新（または指定）の実行の metrics.json を出す（research-cycle の metrics_cmd）．"""
    run_dir = Path(args.run_dir) if args.run_dir else _latest_run(Path(args.results_dir))
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    if meta.get("kind") == "e0_measure":
        # E0 には数値の比較指標が無いので，集約した表をそのまま返す
        summary = run_dir / "e0_summary.md"
        text = summary.read_text(encoding="utf-8") if summary.exists() else None
        print(json.dumps({"run": run_dir.name, "kind": "e0_measure", "summary_md": text}))
        return 0 if text is not None else 1
    path = run_dir / "metrics.json"
    if not path.exists():
        print(f"{path} not found; run `mise run analyze` first", file=sys.stderr)
        return 1
    metrics = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({"run": run_dir.name, **metrics}, indent=None if args.json else 1))
    return 0


def cmd_compare(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """2 つの実行の正答を McNemar 検定で比べる．"""
    from atrium.analysis import compare_runs, read_results

    result = compare_runs(
        read_results(Path(args.run_a) / "results.jsonl"),
        read_results(Path(args.run_b) / "results.jsonl"),
    )
    print(json.dumps(result, indent=1))
    return 0


def cmd_e0(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """E0 の実測（faiss / medcpt）と集約（summarize）．"""
    from atrium import e0

    if args.what == "summarize":
        if args.dir is None:
            raise SystemExit("e0 summarize requires --dir")
        text = e0.summarize(Path(args.dir))
        (Path(args.dir).parent / "e0_summary.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    if args.out is None:
        raise SystemExit(f"e0 {args.what} requires --out")
    result = (
        e0.faiss_bench(cfg.e0.faiss_vectors, cfg.e0.faiss_queries, cfg.experiment.seed)
        if args.what == "faiss"
        else e0.medcpt_bench(
            cfg.data.medrag.query_encoder, cfg.data.medrag.article_encoder, cfg.e0.faiss_queries
        )
    )
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """引数の定義．"""
    parser = argparse.ArgumentParser(prog="atrium")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("config", help="config.yaml の値を出す")
    p.add_argument("action", choices=["get"])
    p.add_argument("key")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("plan", help="placement.json を作る")
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tsv", action="store_true", help="host<TAB>shard_ids を標準出力へ出す")
    p.add_argument(
        "--gpu-hosts", nargs="*", default=[], help="GPU を持つ専門家（enronqa の B4 の委託先）"
    )
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("node", help="専門家ノードを起動する")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104 (コンテナ内で全インタフェースに公開する)
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--node-id", required=True)
    p.add_argument("--shards-dir", required=True)
    p.add_argument("--shard-ids", required=True, help="カンマ区切りのシャード ID")
    p.add_argument("--ollama-url", required=True)
    p.add_argument("--expert-model", default=None, help="宿る型の LLM（省略時は llm.expert_model）")
    p.set_defaults(func=cmd_node)

    p = sub.add_parser(
        "render-compose", help="compose のひな形の ${...} を config.yaml の値で埋めて出す"
    )
    p.add_argument(
        "--role", choices=["node", "node_gpu", "requester", "requester_llm"], required=True
    )
    p.add_argument("--uid", type=int, required=True, help="ノードの SSH のユーザーの UID")
    p.add_argument("--gid", type=int, required=True)
    p.add_argument("--node-id", default=None, help="role=node のとき必須")
    p.add_argument("--shard-ids", default=None, help="role=node のとき必須（カンマ区切り）")
    p.set_defaults(func=cmd_render_compose)

    p = sub.add_parser("run", help="質問者として実験を行う")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--placement", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--ollama-url", default="http://localhost:11434")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("data", help="データ中継点での準備")
    p.add_argument("dataset", choices=sorted(STEPS_OF))
    p.add_argument("step", choices=sorted({*MEDRAG_STEPS, *FEB4RAG_STEPS, *ENRONQA_STEPS, "all"}))
    p.add_argument("--data-dir", required=True)
    p.add_argument("--source", default=None, help="corpus / embed / beir を 1 データ源に限る")
    p.add_argument(
        "--only-list",
        default=None,
        help="embed: 分担表（1 行 1 個の source/name）にある断片だけを埋め込む",
    )
    p.set_defaults(func=cmd_data)

    p = sub.add_parser("fetch-models", help="データセットで使うモデルを HF のキャッシュへ取得する")
    p.add_argument("dataset", choices=sorted(STEPS_OF))
    p.set_defaults(func=cmd_fetch_models)

    p = sub.add_parser("advert-eval", help="RQ-A のオフラインの評価（EnronQA．p0004 EQ2）")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=cmd_advert_eval)

    p = sub.add_parser(
        "cascade-eval", help="二段ルーティング centroid_probe のオフラインの評価（EnronQA）"
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--reference-metrics", required=True, help="退行の確認の基準（EQ2 の metrics.json）"
    )
    p.set_defaults(func=cmd_cascade_eval)

    p = sub.add_parser(
        "probe-reach", help="centroid_probe の K ごとの届くデバイスの数と再現率（EnronQA）"
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.set_defaults(func=cmd_probe_reach)

    p = sub.add_parser(
        "device-probe-eval",
        help="device_probe の D ごとの hit@m と D の選択（EnronQA．Iteration 7）",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.add_argument(
        "--reference-metrics",
        required=True,
        help="centroid_probe の値の一致の基準（Iteration 1 の cascade-eval の metrics.json）",
    )
    p.set_defaults(func=cmd_device_probe_eval)

    p = sub.add_parser(
        "node-bench",
        help="専門家ノード 1 台への閉じた系の probe の負荷試験（EnronQA．Iteration 9）",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.add_argument("--target-host", required=True, help="負荷を集めるノード（配置の host）")
    p.add_argument(
        "--concurrency", required=True, help="同時の要求の数を流す順に（例: 1,2,4,8,16,32,64）"
    )
    p.add_argument("--max-procs", type=int, default=4, help="負荷生成のプロセスの数の上限")
    p.add_argument("--warmup-s", type=float, default=5.0, help="水準ごとの測定の前の慣らしの秒数")
    p.add_argument("--measure-s", type=float, default=30.0, help="水準ごとの測定の区間の秒数")
    p.set_defaults(func=cmd_node_bench)

    p = sub.add_parser("owner-eval", help="持ち主推定の入力を揃えた評価（EnronQA．p0005 T2）")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--reference-metrics", required=True, help="退行の確認の基準（EQ2 の metrics.json）"
    )
    p.set_defaults(func=cmd_owner_eval)

    p = sub.add_parser(
        "header-eval", help="持ち主推定の入力からヘッダーを除いた測り直し（EnronQA．Iteration 2）"
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--reference-metrics", required=True, help="退行の確認の基準（EQ2 の metrics.json）"
    )
    p.add_argument("--t2-metrics", required=True, help="退行の確認の基準（T2 の metrics.json）")
    p.add_argument(
        "--cascade-metrics", required=True, help="退行の確認の基準（cascade-eval の metrics.json）"
    )
    p.set_defaults(func=cmd_header_eval)

    p = sub.add_parser(
        "body-advert-eval",
        help="公開情報をヘッダーを除いた本文から作り直した RQ-A の測り直し（EnronQA．Iteration 3）",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--reference-metrics", required=True, help="退行の確認の基準（EQ2 の metrics.json）"
    )
    p.add_argument(
        "--header-metrics", required=True, help="退行の確認の基準（header-eval の metrics.json）"
    )
    p.add_argument(
        "--cascade-metrics", required=True, help="退行の確認の基準（cascade-eval の metrics.json）"
    )
    p.set_defaults(func=cmd_body_advert_eval)

    p = sub.add_parser("newcomer-eval", help="150 人での新参者の評価（EnronQA．p0005 T3）")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--reference-metrics", required=True, help="退行の確認の基準（EQ2 の metrics.json）"
    )
    p.add_argument(
        "--work-dir",
        required=True,
        help="再学習したルーターの置き場所（results/ の外．書き込める場所）",
    )
    p.add_argument("--n-newcomers", type=int, default=30)
    p.set_defaults(func=cmd_newcomer_eval)

    p = sub.add_parser("churn-live-report", help="EQ7b の実機の実行を集めて EQ7a と比べる")
    p.add_argument(
        "--runs", nargs="+", required=True, help="実行のディレクトリ（results/<run_id>）"
    )
    p.add_argument("--offline-metrics", required=True, help="EQ7a の metrics.json")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=cmd_churn_live_report)

    p = sub.add_parser("churn-eval", help="RQ-C のオフラインの評価（EnronQA．p0004 EQ7a）")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.add_argument(
        "--reference-metrics", required=True, help="退行の確認の基準（EQ2 の metrics.json）"
    )
    p.set_defaults(func=cmd_churn_eval)

    p = sub.add_parser(
        "churn-cascade-eval",
        help="二段の方式の参加・離脱への頑健さのオフラインの評価（EnronQA．Iteration 15）",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.add_argument(
        "--device-probe-metrics",
        required=True,
        help="G1 の基準（Iteration 7 の device-probe-eval の metrics.json）",
    )
    p.add_argument(
        "--churn-metrics", required=True, help="G2 の基準（EQ7a の churn-eval の metrics.json）"
    )
    p.set_defaults(func=cmd_churn_cascade_eval)

    p = sub.add_parser(
        "churn-join-eval",
        help="参加の通知の遅れ L ごとのメンバー表の見え方と hit の回復（EnronQA．Iteration 16）",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.add_argument(
        "--cascade-metrics",
        required=True,
        help="G1 の基準（Iteration 15 の churn-cascade-eval の metrics.json）",
    )
    p.set_defaults(func=cmd_churn_join_eval)

    p = sub.add_parser(
        "churn-leave-eval",
        help="離脱の通知の遅れ L_leave ごとの無駄な問い合わせと hit の回復（EnronQA．Iteration 17）",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.add_argument(
        "--join-metrics",
        required=True,
        help="G1・G2 の基準（Iteration 16 の churn-join-eval の metrics.json）",
    )
    p.set_defaults(func=cmd_churn_leave_eval)

    p = sub.add_parser(
        "churn-refill-eval",
        help="返事の無い probe の枠を次の順位で補ったときの hit と問い合わせの費用（EnronQA．Iteration 18）",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--placement", required=True, help="受信箱の配置（results/<run>/placement.json）"
    )
    p.add_argument(
        "--join-metrics",
        required=True,
        help="G1 の基準（Iteration 16 の churn-join-eval の metrics.json）",
    )
    p.set_defaults(func=cmd_churn_refill_eval)

    p = sub.add_parser("loso", help="E2 の leave-one-source-out の評価（p0003．操作端末）")
    p.add_argument("dataset", choices=["medrag", "feb4rag"])
    p.add_argument("--data-dir", default="artifacts", help="制御点から写した <dataset>/ の親")
    p.add_argument("--split", choices=["train", "val", "test"], required=True)
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=cmd_loso)

    for name, help_text in (
        ("judge", "実行の回答を判定モデルで採点し，judgements.jsonl を書く"),
        ("judge-validate", "付属の別解・誤答で判定モデルを検証する"),
        ("judge-kappa", "参照モデルで採点し直して Cohen の κ を出す"),
    ):
        p = sub.add_parser(name, help=help_text + "（EnronQA．p0004 §8.6）")
        p.add_argument("--data-dir", required=True)
        p.add_argument("--ollama-url", action="append", required=True, help="複数指定で並列に送る")
        if name != "judge-validate":
            p.add_argument("--run-dir", action="append", required=True)
        if name != "judge":
            p.add_argument("--out-dir", required=True)
        p.set_defaults(func=cmd_judge)

    p = sub.add_parser("check-data", help="設定がラベルを作ったときと同じかを確かめる")
    p.add_argument("--data-dir", required=True)
    p.set_defaults(func=cmd_check_data)

    p = sub.add_parser("embed-plan", help="MedRAG の埋め込みを複数の GPU で分担する表を作る")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--workers", nargs="+", required=True)
    p.add_argument("--out-dir", required=True, help="<worker>.txt（1 行 1 個の source/name）を書く")
    p.set_defaults(func=cmd_embed_plan)

    for name, func in (("analyze", cmd_analyze), ("metrics", cmd_metrics)):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", default=None, help="省略時は results/ の最新")
        p.add_argument("--results-dir", default="results")
        if name == "analyze":
            p.add_argument("--artifacts-dir", default="artifacts", help="labels・split の置き場所")
        else:
            p.add_argument("--json", action="store_true", help="1 行の JSON で出す")
        p.set_defaults(func=func)

    p = sub.add_parser("compare", help="2 つの実行を McNemar 検定で比べる")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("e0", help="E0 の実測と集約")
    p.add_argument("what", choices=["faiss", "medcpt", "summarize"])
    p.add_argument("--out", default=None)
    p.add_argument("--dir", default=None, help="summarize: results/<run>/e0")
    p.set_defaults(func=cmd_e0)
    return parser


def main(argv: list[str] | None = None) -> int:
    """エントリポイント．"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.config))
    code: int = args.func(args, cfg)
    return code


if __name__ == "__main__":
    sys.exit(main())
