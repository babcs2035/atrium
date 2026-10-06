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
from atrium.manifest import plan_placement, read_manifest, write_json_model
from atrium.paths import dataset_paths

MEDRAG_STEPS = ("benchmark", "corpus", "embed", "shards", "queries", "labels", "split", "train")
FEB4RAG_STEPS = ("fetch", "beir", "shards", "queries", "split", "train")


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
    steps = (
        (MEDRAG_STEPS if args.dataset == "medrag" else FEB4RAG_STEPS)
        if args.step == "all"
        else (args.step,)
    )
    valid = MEDRAG_STEPS if args.dataset == "medrag" else FEB4RAG_STEPS
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
    return sorted({spec.hf_name for spec in FEB4RAG_ENCODERS.values()})


def cmd_fetch_models(args: argparse.Namespace, cfg: AtriumConfig) -> int:
    """モデルを Hugging Face のキャッシュ（HF_HOME）へ取得する．取得済みなら何もしない．"""
    from huggingface_hub import snapshot_download

    for name in models_for(cfg, args.dataset):
        logging.info("fetching %s", name)
        snapshot_download(repo_id=name)
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
    return compute_metrics(
        read_results(run_dir / "results.jsonl"), labels, split, n_sources, consistent_setting
    )


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
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("node", help="専門家ノードを起動する")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104 (コンテナ内で全インタフェースに公開する)
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--node-id", required=True)
    p.add_argument("--shards-dir", required=True)
    p.add_argument("--shard-ids", required=True, help="カンマ区切りのシャード ID")
    p.add_argument("--ollama-url", required=True)
    p.set_defaults(func=cmd_node)

    p = sub.add_parser(
        "render-compose", help="compose のひな形の ${...} を config.yaml の値で埋めて出す"
    )
    p.add_argument("--role", choices=["node", "requester"], required=True)
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
    p.add_argument("dataset", choices=["medrag", "feb4rag"])
    p.add_argument("step", choices=sorted({*MEDRAG_STEPS, *FEB4RAG_STEPS, "all"}))
    p.add_argument("--data-dir", required=True)
    p.add_argument("--source", default=None, help="corpus / embed / beir を 1 データ源に限る")
    p.add_argument(
        "--only-list",
        default=None,
        help="embed: 分担表（1 行 1 個の source/name）にある断片だけを埋め込む",
    )
    p.set_defaults(func=cmd_data)

    p = sub.add_parser("fetch-models", help="データセットで使うモデルを HF のキャッシュへ取得する")
    p.add_argument("dataset", choices=["medrag", "feb4rag"])
    p.set_defaults(func=cmd_fetch_models)

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
