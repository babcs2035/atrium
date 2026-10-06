# d0002 設定：config.yaml の各項目

`config.yaml` は実験条件の唯一の情報源である．操作端末・制御点・各ノードのコンテナが同じファイルを読む．
型と既定値は `src/atrium/config.py` が定め，未知のキー（誤記）は読み込み時にエラーになる．
値の確認は `uv run atrium config get <キー>`（例: `cluster.control`）で行う．

## experiment

| キー | 値 | 意味 |
|---|---|---|
| `kind` | `e0_measure` / `e1_routing` | E0（実機の実測）か，連合 RAG の実行か |
| `dataset` | `medrag` / `feb4rag` | MIRAGE ＋ MedRAG コーパス（4 データ源）か，FeB4RAG（13 エンジン）か |
| `routing` | `ragroute` / `all` / `random` / `none` | 問い合わせ先の選び方（RAGRoute の 4 方式） |
| `answer_mode` | `retrieval_only` / `snippet_return` / `local_answer` | LLM を呼ばず検索まで／断片返却型／宿る型 |
| `question_limit` | 整数または `null` | 各問題集の先頭からこの数だけを使う（動作確認用） |
| `parallel` | 整数 | 質問者が同時に処理する質問数 |
| `seed` | 整数 | `random` のルーティングと，FeB4RAG の重心の無作為抽出の種 |

`random` は質問 ID と種から乱数を作るので，並列度や実行順によらず同じ選択になる．
ルーターの学習の分割の種は RAGRoute に合わせてコードに固定してある（medrag 12，feb4rag 42）．

## retrieval

| キー | 既定値 | 意味 |
|---|---|---|
| `k_ret` | 50 | 各データ源から取得する件数．シャードに分けたデータ源は，各シャードから `k_ret` 件ずつ取って合わせ，上位 `k_ret` 件に絞る |
| `k_rerank` | 15 | 統合後に LLM へ渡す件数 |
| `merge` | `score` | `score`：検索スコアの降順／`cross_encoder`：bge-reranker-v2-m3 で再ランク／`qrels_oracle`：FeB4RAG の結果統合用ラベルの順（RAGRoute で再ランクを無効にした場合と同じ） |
| `query_embedding.medrag` | `live` | 質問者がその場で MedCPT で埋め込む（埋め込み時間も計測に入る） |
| `query_embedding.feb4rag` | `cached` | 制御点で事前計算した埋め込みを使う（検索器が 8 種類あり，5.8B の SGPT を含むため） |

関連ラベルは `k_ret` と `k_rerank` で決まる（[d0001](d0001_architecture.md) §2.1）．
ラベルを作ったときの設定値は制御点の `labels/labels_meta.json` に残る（MedRAG は `data.medrag.sources`・`k_ret`・
`k_rerank`・検索器・`embed_precision`・`cluster.shard_budget_gb`，FeB4RAG は `data.feb4rag.sources`）．
deploy は `atrium check-data` で今の `config.yaml` と照合し，違えば止まる．その場合は制御点で該当する段
（`labels` 以降．`shard_budget_gb` や `embed_precision` なら `shards` や `embed` 以降）を作り直す．
自動では作り直さない．

## routing

| キー | 既定値 | 意味 |
|---|---|---|
| `random_k.medrag` / `random_k.feb4rag` | 3 / 9 | `random` で選ぶデータ源の数（RAGRoute と同じ） |
| `ragroute_threshold` | 0.5 | `ragroute` の判定閾値（RAGRoute の推論コードと同じ）．検証データで Youden 指数が最大になる閾値は `router/train_report.json` に記録される |

## llm

| キー | 既定値 | 意味 |
|---|---|---|
| `ollama_version` | `0.35.1` | 各ノードで使う Ollama のイメージのバージョン（registry の `mirror/ollama` を使う） |
| `requester_model` | `llama3.1:8b` | 断片返却型で質問者が使う Ollama のモデル（RAGRoute の既定と同じ系列） |
| `expert_model` | `qwen3:0.6b` | 宿る型で各専門家が使うモデル．E0 の実測で見直す |
| `num_predict` | 2048 | 生成の上限トークン数 |
| `num_ctx` | 16384 | 文脈長．15 断片 × 約 250 トークンに質問と指示を足しても収まる長さ |
| `think` | `false` | qwen3 系の思考モード |
| `temperature` | 0.0 | 生成の温度．0 で貪欲な復号（同じ入力に同じ回答．backlog B3） |
| `requester_num_parallel` | 2 | 質問者の Ollama が同時に処理する要求の数．8B・文脈長 16384 では 2 件分の KV キャッシュまでが 12 GB に収まる |
| `timeout_s` | 600 | 1 回の生成の打ち切り時間（秒） |

## local_answer

| キー | 既定値 | 意味 |
|---|---|---|
| `k_context` | 15 | 宿る型で各専門家が自分のシャードから使う断片数 |
| `aggregate` | `vote` | 複数の専門家の回答の統合（選択肢の多数決） |
| `timeout_s` | 1800 | 1 台の専門家への回答の要求の打ち切り時間（秒）．専門家の Ollama は要求を順番に処理するので，並列に送った要求の待ち時間も含む（CPU の 0.6B で 1 回 30〜190 秒を実測） |

## e0

| キー | 意味 |
|---|---|
| `hosts` | 実測するホスト（GPU なしの専門家の代表） |
| `iperf_pairs` | RTT と iperf3 のスループットを測るホストの組 |
| `gguf_models` | llama-bench で測る GGUF（Hugging Face のリポジトリとファイル名） |
| `faiss_vectors` | FAISS（fp16 平坦索引）の検索時間を測るベクトル数 |
| `faiss_queries` | FAISS と MedCPT で測る 1 クエリの試行回数 |

## cluster

| キー | 既定値 | 意味 |
|---|---|---|
| `control` | `wafl-ctrl5` | 操作端末から `ssh` で入る制御点の名前 |
| `ssh_user` | `denjo` | 制御点から各デバイスへ SSH するユーザー（デバイスごとに `ssh_user` で上書きできる） |
| `remote_dir` | `/home/denjo/atrium` | 制御点と各ノードで成果物を置くディレクトリ |
| `data_dir` | `/home/denjo/atrium-data` | 制御点のデータディレクトリ |
| `registry_port` | 5000 | 制御点のローカル registry．wafl500〜509 には制御点の 127.0.0.1:5000 への SSH 転送が既に張られており，それをそのまま使う |
| `node_port` | 8100 | 専門家ノードの HTTP ポート |
| `shard_budget_gb` | 6.0 | 1 シャードの fp16 の埋め込みの目安．変えるとシャードの切り方が変わるので，制御点で `shards` 以降の段を作り直す必要がある |

### デバイス（`requester`・`experts`・`gpu_workers`）

実験で使うデバイスは役割ごとのリストに書く．**1 台のデバイスが持つ役割は 1 つだけ**で，役割はどのリストに置くかで決まる．
同じホストを 2 か所（または同じリストに 2 回）書くと，読み込み時にエラーになる．

```yaml
cluster:
  requester: {host: "192.168.15.100"}
  experts:
    - {host: "192.168.13.100"}
    - {host: "192.168.13.101", ssh_user: alice}   # デバイスごとに SSH のユーザーを変えられる
  gpu_workers:
    - {host: "192.168.15.101"}
```

| リスト | 役割 | 現在の構成 |
|---|---|---|
| `requester`（1 台） | 質問者．質問を投げ，断片返却型では回答も生成する | 192.168.15.100（wafl500） |
| `experts` | 専門家．シャード数だけ先頭から使い，足りなければ巡回して 1 台に複数のシャードを載せる | 192.168.13.100〜109，192.168.14.100〜109（20 台） |
| `gpu_workers` | データ準備で MedCPT の埋め込みを制御点の GPU と分担する GPU PC | 192.168.15.101〜109（wafl501〜509） |

各要素のキーは `host`（必須）と `ssh_user`（省略時は `cluster.ssh_user`）である．
`e0.hosts` と `e0.iperf_pairs` のホストは `experts` に含まれていなければならない（E0 は専門家の機種を測る実験である）．

## data

`data.medrag` と `data.feb4rag` は取得元（URL とコミット）・検索器・自己紹介文を定める．
`sources` の順序はルーターの one-hot の順序でもあるため，学習後に変えるとルーターの読み込み時にエラーになる．

| キー | 意味 |
|---|---|
| `medrag.sources` | 4 データ源（RAGRoute と同じ順序） |
| `medrag.article_encoder` / `query_encoder` | MedCPT の文書側・クエリ側のモデル |
| `medrag.embed_precision` | MedCPT の埋め込みの計算精度（`fp32` / `fp16_autocast`．既定は後者．文書とクエリの両方に使う） |
| `medrag.embed_batch_size` | 埋め込みのバッチの大きさ（既定 128．RTX 3060 の 12 GB に収まる大きさ） |
| `medrag.medrag_commit` | StatPearls の断片化に使う MedRAG のスクリプトのコミット |
| `medrag.descriptions` | 各データ源の自己紹介文（Agent Card の description） |
| `feb4rag.sources` | 13 エンジン（signal1m・robust04・trec-news を除く．RAGRoute と同じ） |
| `feb4rag.feb4rag_commit` | FeB4RAG のリポジトリのコミット |
| `feb4rag.beir_hf_repo` | BEIR のコーパスを取得する Hugging Face のデータセット（`{name}` はエンジン名） |
| `feb4rag.centroid_sample` / `centroid_sample_overrides` | 重心の推定に使う文書数（5.8B の SGPT を CPU で動かす trec-covid は少なくする） |
