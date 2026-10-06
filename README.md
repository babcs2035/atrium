# atrium

組織内の既存 PC が「自分の文書＋小さな LLM」を持つ専門家になり，中央の管理者や中央の学習なしに
「誰に聞き，誰が答えるか」を決める分散協調（連合 RAG）を調べるための実験基盤である．
研究の目的・仕組み・理論の要約は [docs/d0006_research_overview.md](docs/d0006_research_overview.md)，
研究計画の全文は [plans/p0001_initialization.md](plans/p0001_initialization.md) にある．

## 現状（2026-10-06）

- データ準備は完了している．MedRAG の 4 コーパス（約 5,400 万断片）を MedCPT で埋め込み，16 シャードに分けた．
  FeB4RAG の 13 エンジンのシャードも作った．どちらもルーター（RAGRoute の MLP）を学習済みである．
- **E0**（実機の性能実測）は完了した（`results/20261006_052434/e0_summary.md`）．
- **E1**（RAGRoute の 4 方式 ragroute / all / random / none の再現）は，実機での基盤の検証まで終えた
  （MedRAG の 100 問で，実機の分散検索の結果と関連ラベルの一致が 1.000）．全問での比較は research-cycle で行う．
- 回答方式は，断片返却型（`snippet_return`）と宿る型（`local_answer`）の両方が動く．

検証の記録は [.claude/research/journal.md](.claude/research/journal.md) の「準備段階の記録」にある．

## 構成

```
gpu2（操作端末）── mise のタスク・イメージの build・外部資材の取得・分析
  └─ wafl-ctrl5（制御点．RTX 3060）── registry・データ準備・各ノードの操作．質問者も兼ねる
       │                                  （クエリ埋め込み・ルーティング・統合・回答生成）
       ├─ 192.168.13.100〜109, 14.100〜109（専門家 20 台．GPU なし）── シャードの検索・宿る型の回答
       └─ 192.168.15.100〜109（wafl500〜509．RTX 3060）── データ準備のときだけ埋め込みを分担する
```

各ノードはインターネットに出ず，イメージとモデルは制御点の registry と rsync で受け取る．
詳しくは [docs/d0001_architecture.md](docs/d0001_architecture.md) を参照．

## 使い方

前提：
- 操作端末に `mise`，`uv`，`docker`，`jq` があること．
- 操作端末から `ssh wafl-ctrl5` で制御点に入れること．
- 制御点から各ノードへ，ユーザー `denjo` で SSH できること．

実験条件も環境の構成も全て [config.yaml](config.yaml) で決める（環境変数は使わない）．

```bash
mise run setup         # 環境構築．イメージを build して配り，制御点でデータ準備をバックグラウンドで始める
mise run data-status   # データ準備の進み具合を見る
mise run deploy        # 実験準備．シャード・設定を配り，各ノードのコンテナを起動する
mise run start         # 実験実行．結果は results/<YYYYMMDD_HHMMSS>/ に回収される
mise run analyze       # 結果の分析．metrics.json と analysis_report.md を作る
mise run stop          # 実験の後に，各ノードのコンテナを止めてメモリと VRAM を解放する
mise run check         # テスト・lint・型検査
```

- 条件やコードを変えて実験するときは，`config.yaml` を編集し，`mise run deploy` から実行し直す．
  deploy はイメージを毎回 build して配る．start は，各ノードの設定とイメージが今の作業ツリーと同じかを確かめてから始める．
- データ準備の各段は冪等であり，`mise run setup` を再び実行しても，終わっている段は飛ばされる．
- 2 つの実行の正答率は `uv run atrium compare results/<A> results/<B>`（McNemar の正確検定）で比べる．

手順の詳細・所要時間・失敗時の確認先は [docs/d0003_experiment_workflow.md](docs/d0003_experiment_workflow.md) にある．

研究サイクルを自動で回すときは，research-cycle skill を使う（[docs/d0005_research_cycle.md](docs/d0005_research_cycle.md)）．

```bash
~/workspace/start_research_cycle.sh atrium
```

## ディレクトリ

| パス | 役割 |
|---|---|
| `config.yaml` | 実験設定（唯一の情報源）．項目は [docs/d0002_configuration.md](docs/d0002_configuration.md) |
| `src/atrium/` | Python の実装（専門家ノード・質問者・ルーティング・データ準備・分析．CLI は `atrium`） |
| `src/atrium/routing/` | ルーティング方式．新しい方式はここに `Router` を追加する |
| `scripts/tasks/` | mise のタスクの本体（操作端末で動く） |
| `scripts/remote/` | 制御点で動くスクリプト（データ準備と各ノードの操作） |
| `docker/` | 専門家・質問者の compose のひな形（deploy が `config.yaml` の値で埋める） |
| `Dockerfile` | `node`（専門家）と `full`（質問者・データ準備）の 2 種類のイメージ |
| `tests/` | テスト（`mise run check`） |
| `plans/` | 研究計画書（p0001）と実装計画（p0002） |
| `docs/` | 技術仕様と研究の概要 |
| `results/` | 実験の結果（実行ごとのディレクトリ．ログは Git で追跡しない） |
| `artifacts/` | 操作端末の作業用の生成物（Git で追跡しない）．`cache/` は外部から取得したモデルの写しで，setup の再実行で取り直さないために残す |
| `.claude/research/` | research-cycle の設定と記録（journal・backlog） |

## 文書

| 文書 | 内容 |
|---|---|
| [docs/d0006_research_overview.md](docs/d0006_research_overview.md) | 研究の目的・実験系の仕組み・理論（検索・ラベル・ルーター・待ち時間）・評価の方針・現状 |
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
