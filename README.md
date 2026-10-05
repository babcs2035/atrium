# atrium

組織内の PC が「自分のデータ＋小さな LLM」を持つ専門家になり，中央の管理者や中央の学習なしに
「誰に聞き，誰が答えるか」を決める分散協調（連合 RAG）の実験コードである．
研究の目的・問い・実験計画は [plans/p0001_initialization.md](plans/p0001_initialization.md) にある．

現在の実装で行えること:

- **E0**：実機の性能実測（メモリ構成・llama-bench・FAISS・MedCPT・ネットワーク）
- **E1**：RAGRoute（EuroMLSys 2025）の 4 方式（ragroute / all / random / none）を，
  専門家ノードが別々の PC に分かれた構成で再現する（MIRAGE ＋ MedRAG コーパス，FeB4RAG）
- 回答方式の比較：断片返却型（snippet_return）と宿る型（local_answer）

## 構成

```
gpu2（操作端末）── mise のタスク・分析
  └─ wafl-ctrl5（制御点．データ中継点を兼ねる）── build・registry・データ準備・各ノードの操作
       ├─ 192.168.15.100（質問者．GPU）── クエリ埋め込み・ルーティング・統合・回答生成
       └─ 192.168.13.100〜109, 14.100〜109（専門家 20 台．GPU なし）── シャードの検索・宿る型の回答
```

詳しくは [docs/d0001_architecture.md](docs/d0001_architecture.md) を参照．

## 使い方

前提：操作端末に `mise` と `uv` があり，`ssh wafl-ctrl5` で制御点に入れること．制御点から各ノードへは
ユーザー `denjo` で SSH できること．実験条件は全て [config.yaml](config.yaml) で決める．

```bash
mise run setup         # 環境構築．制御点でデータ準備をバックグラウンドで始める（数日かかる）
mise run data-status   # データ準備の進み具合を見る
mise run deploy        # 実験準備．シャード・設定を配り，各ノードのコンテナを起動する
mise run start         # 実験実行．結果は results/<YYYYMMDD_HHMMSS>/ に回収される
mise run analyze       # 結果の分析．metrics.json と analysis_report.md を作る
mise run stop          # 実験の後に，各ノードのコンテナを止めてメモリと VRAM を解放する
mise run check         # テスト・lint・型検査
```

条件を変えて実験するときは config.yaml を編集し，`mise run deploy` から実行し直す．
コードを変えたときは，イメージを作り直すために `mise run setup` から実行し直す（データ準備は冪等なので，
終わっている段は飛ばされる）．手順の詳細と所要時間の目安は
[docs/d0003_experiment_workflow.md](docs/d0003_experiment_workflow.md) にある．

研究サイクルを自動で回すときは，research-cycle skill を使う（[docs/d0005_research_cycle.md](docs/d0005_research_cycle.md)）．

```bash
~/workspace/start_research_cycle.sh atrium
```

## ディレクトリ

| パス | 役割 |
|---|---|
| `config.yaml` | 実験設定（唯一の情報源）．項目は [docs/d0002_configuration.md](docs/d0002_configuration.md) |
| `src/atrium/` | Python の実装（専門家ノード・質問者・ルーティング・データ準備・分析） |
| `src/atrium/routing/` | ルーティング方式．新しい方式はここに `Router` を追加する |
| `scripts/tasks/` | mise のタスクの本体（操作端末で動く） |
| `scripts/remote/` | 制御点で動くスクリプト（各ノードの操作） |
| `docker/` | 専門家・質問者の compose ファイル |
| `Dockerfile` | `node`（専門家）と `full`（質問者・データ準備）の 2 種類のイメージ |
| `tests/` | テスト（`mise run check`） |
| `plans/` | 研究計画書と実装計画 |
| `docs/` | 技術仕様 |
| `results/` | 実験の結果（実行ごとのディレクトリ） |
| `.claude/research/` | research-cycle の設定と記録（journal・backlog・state） |

## 文書

| 文書 | 内容 |
|---|---|
| [docs/d0001_architecture.md](docs/d0001_architecture.md) | 役割の分担・データの流れ・専門家ノードの API・データの配置 |
| [docs/d0002_configuration.md](docs/d0002_configuration.md) | config.yaml の各項目 |
| [docs/d0003_experiment_workflow.md](docs/d0003_experiment_workflow.md) | mise タスクの手順・所要時間・失敗時の確認先 |
| [docs/d0004_metrics.md](docs/d0004_metrics.md) | 結果ファイルの書式と指標の定義 |
| [docs/d0005_research_cycle.md](docs/d0005_research_cycle.md) | research-cycle での研究の進め方 |
| [plans/p0002_implementation.md](plans/p0002_implementation.md) | 実装の判断と RAGRoute との差分 |

## 出典とライセンス

ルーターの MLP・学習手順・プロンプト・回答の抽出規則は RAGRoute
（https://github.com/sacs-epfl/ragroute ，MIT License，Copyright (c) 2025 SaCS-EPFL）から移植した．
評価データは MIRAGE / MedRAG（Xiong et al., Findings of ACL 2024）と FeB4RAG（Wang et al., SIGIR 2024）を使う．
