# d0002 設定：config.yaml の各項目

`config.yaml` は実験条件の唯一の情報源である．操作端末・制御点・各ノードのコンテナが同じファイルを読む．
型と既定値は `src/atrium/config.py` が定め，未知のキー（誤記）は読み込み時にエラーになる．
値の確認は `uv run atrium config get <キー>`（例: `cluster.control`）で行う．

## 設定の経路（環境変数を使わない）

実験・環境の制御は `config.yaml` の編集だけで行い，環境変数や `.env` ファイルでは何も制御しない．

| 値を使うもの | 経路 |
|---|---|
| Python のコード（専門家ノード・質問者・分析など） | `load_config()` が `config.yaml` を読む．コードは `os.environ` を読まない（テストで検査している） |
| 専門家ノードのシャード・ノード ID | deploy が `atrium render-compose` で，`docker/compose.node.yml` のひな形を埋めた `compose.yml` を作り，`atrium node` の引数として渡す |
| compose（ポート・Ollama のバージョン・並列数・UID など） | 同上．ひな形の変数は `render-compose` が `config.yaml` から埋める．埋まらない変数があればエラーになる |
| シェルスクリプト | 起動のたびに `src/atrium/shell_config.py`（Python と PyYAML だけで動く）が `config.yaml` を読んでシェルの変数にする（`eval "$(python3 src/atrium/shell_config.py config.yaml)"`）．設定を書き出すファイルは無い．操作端末のタスクは，先に `atrium.config` で検証してから読む．操作端末だけが持つ git のコミットは，start の引数で制御点へ渡す |
| 実験の記録に残すコミット | イメージの build 時に `/etc/atrium-git-head` へ書き込み，実験の記録（`run_meta.json`）と start の照合が同じファイルを読む |

Ollama・Hugging Face・uv など外部のツールが読む環境変数（`OLLAMA_MODELS`，`HF_HUB_OFFLINE`，`HF_HOME` など）は，
そのツールの設定に必要なので compose とイメージに書いてある．これらは `config.yaml` から変えられる条件ではなく，
基盤の固定の構成である（`OLLAMA_NUM_PARALLEL` だけは `llm.requester_num_parallel` から埋める）．

## experiment

| キー | 値 | 意味 |
|---|---|---|
| `kind` | `e0_measure` / `e1_routing` | E0（実機の実測）か，連合 RAG の実行か |
| `dataset` | `medrag` / `feb4rag` / `enronqa` | MIRAGE ＋ MedRAG コーパス（4 データ源）か，FeB4RAG（13 エンジン）か，EnronQA（150 人の受信箱．p0004） |
| `routing` | `ragroute` / `all` / `random` / `none`，p0004 で `oracle` / `flood_score` / `card_sim` / `term_sketch` / `centroid_sim` / `multi_centroid`，Iteration 1 で `centroid_probe`，Iteration 7 で `device_probe` | 問い合わせ先の選び方（RAGRoute の 4 方式と，公開情報を使う方式．[d0001](d0001_architecture.md) §5） |
| `answer_mode` | `retrieval_only` / `snippet_return` / `local_answer` / `snippet_return_small` / `delegate_gpu` | LLM を呼ばず検索まで／断片返却型／宿る型／断片返却型で質問者が専門家と同じ小型のモデルを使う（B3．p0004）／GPU の無い専門家が対応づけた GPU の専門家に断片を渡して答えさせる（B4．p0004） |
| `question_limit` | 整数または `null` | 各問題集の先頭からこの数だけを使う（動作確認用） |
| `parallel` | 整数 | 質問者が同時に処理する質問数 |
| `seed` | 整数 | `random` のルーティングと，FeB4RAG の重心の無作為抽出の種 |
| `http_pool` | `shared`（既定）/ `shared_unlimited` / `per_node` | 質問者の HTTP 接続の持ち方（Iteration 5）．`shared`：全ノードと Ollama で 1 つの池（`max_connections` = 512）．`shared_unlimited`：1 つの池のまま上限なし．`per_node`：専門家のノードの origin ごとに別の池（各 64 本）を持ち，ノード以外の宛先（質問者の Ollama など）は上限 512 の既定の池を使う．要求の内容・宛先・選択は変わらない．`run_meta.json` に `http_pool` と質問処理中の質問者の CPU 時間（`requester_cpu_s`・`requester_cpu_util`）が残る |
| `requester_procs` | 整数（既定 1） | 質問者のデバイスの上で問を分けて同時に流すプロセスの数（Iteration 8．`atrium.requester_procs`）．`parallel` は全プロセスの合計の同時の問の数で，各プロセスへほぼ均等に割る（30 を 4 個なら 8・8・7・7）．問は並べ替えた後の順に 1 問ずつ配る．各プロセスは自分で HTTP クライアント（`http_pool` に従う）とルーターを持ち，全プロセスの準備が済んでから一斉に流し始める．`parallel` ≥ `requester_procs` でなければならず，`churn` とは併用できない．1 は従来どおり 1 プロセス．`run_meta.json` の項目は [d0004](d0004_metrics.md) の「質問者・専門家の CPU」 |
| `node_workers` | 整数（既定 1） | 専門家ノード 1 台を動かすプロセスの数（Iteration 10．`atrium.node`）．2 以上では各プロセスが全シャードを読み込み（メモリはシャードの約 W 倍），同じポートを `SO_REUSEPORT` で開いて，カーネルが新しい接続を各プロセスへ振り分ける．親のプロセスは要求を受けず，子のどれかが終われば全てを止めて異常終了する（compose の `restart` で起き直す）．各ノードは deploy が配る `config.yaml` から読むので，変えたら deploy し直す．離脱の状態はプロセスごとに持つので `churn` とは併用できない．1 は従来どおり 1 プロセス |
| `probe_body` | `per_request`（既定）/ `pre_encoded` | 質問者が `/v1/probe` に送る本文の作り方（Iteration 11．`atrium.requester`）．`per_request`：ノードごとに `ProbeRequest` → `model_dump` → httpx の `json=` で作る．`pre_encoded`：問ごと（検索器ごと）に埋め込みの JSON を 1 回だけ作り，ノードごとに `shard_ids` だけを差し替えた bytes を `content=` で送る．ノードが受け取る本文はバイト単位で同じ（`tests/test_probe_body.py`）で，NaN・inf は両方の経路で例外になる．`/v1/retrieve`・`/v1/answer` の本文は変わらない．`run_meta.json` に `probe_body` が残る．負荷の実験では `pre_encoded` を明示する（質問者の 1 要求の CPU が約 0.57 ms 減り，flood_score の質問者 1 プロセスの処理量が約 1.33 倍になる．Iteration 11） |
| `sniffio_lookup` | `search`（既定）/ `blocked` | 質問者のプロセスでの sniffio の import の扱い（Iteration 12．`atrium.requester.apply_sniffio_lookup`）．httpcore 1.0.9 は要求のたびに `import sniffio` を試み，sniffio が無い環境では失敗した import が残らず毎回 `sys.path` を探す．`search`：従来どおり．`blocked`：sniffio が入っていないときだけ，質問者のプロセス（`atrium run` と `requester_procs` の spawn の子）の始めに `sys.modules["sniffio"] = None` を置いて探索を省く（httpcore は同じ `ImportError` の枝で asyncio とみなすので，要求の内容と選択は変わらない）．入っている環境では何もしない．専門家ノード・依存は変えない．`run_meta.json` に `sniffio_lookup` と `sniffio_installed` が残る．負荷の実験では `probe_body: pre_encoded` と併せて `blocked` を明示する（質問者の 1 要求の CPU が flood_score で約 0.57 ms 減り，質問者 1 プロセスの処理量が約 1.49 倍になる．Iteration 12） |
| `churn` | `null` または `{availability, refresh_steps, schedule_seed, mean_up_steps}` | EQ7b（p0004）．指定すると，EnronQA の質問を 1 問ずつ順に流し（1 問 = 1 刻み），専門家の参加・離脱を実機に起こす．`dataset: enronqa`・`answer_mode: retrieval_only`・`parallel: 1`・`routing` が公開情報の方式か `flood_score` か `oracle` のときだけ使える．`availability` は稼働率，`refresh_steps` は質問者のメンバー表の更新間隔（刻み），`schedule_seed` は時刻表の種，`mean_up_steps`（既定 200）は稼働中の平均の長さ．時刻表と質問の順番は `atrium churn-eval`（EQ7a）と同じで，離脱は `/v1/admin/availability` で起こす（[d0001](d0001_architecture.md) §3）．結果は途中から再開せず，毎回作り直す |

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
| `random_k.medrag` / `random_k.feb4rag` / `random_k.enronqa` | 3 / 9 / 2 | `random` で選ぶデータ源の数（medrag・feb4rag は RAGRoute と同じ．enronqa は `top_m.enronqa` と同じにして比べる） |
| `ragroute_threshold` | 0.5 | `ragroute` の判定閾値（RAGRoute の推論コードと同じ）．検証データで Youden 指数が最大になる閾値は `router/train_report.json` に記録される |
| `top_m.enronqa` | 2（dev で選んだ値） | 公開情報を使う方式と `flood_score` が問い合わせるデータ源の数 m（p0004 の RQ-A） |
| `probe_k.enronqa` | 評価前は 75（仮置き） | `centroid_probe` が probe を送る候補の数 K（`multi_centroid` の上位 K 個．`top_m.enronqa` 以上）．`atrium cascade-eval` の dev で，hit@2 が `flood_score` の 0.95 倍以上になる最小の K（候補 {4, 8, 15, 30, 50, 75}．届かなければ 75）を選ぶ．実機（`experiment.routing: centroid_probe`）では，質問者が 1 段目の候補（既存のヘッダー込みの `multi_centroid`）を 1 問で 1 回だけ求め，候補のシャードを持つノードにだけ，候補の `shard_ids` で `/v1/probe` を送って上位 m 個を選ぶ．届くデバイスの数・シャードの数は `atrium probe-reach`（オフライン）と結果の `probe_*` で確かめる |
| `probe_d.enronqa` | 評価前は 30（仮置き．全台で `flood_score` と同じ選択） | `device_probe` が probe を送るデバイスの数 D．`multi_centroid` の受信箱の得点をデバイス（`placement.json` のノード）ごとに最大値でまとめた上位 D 台を選び，その D 台に載る全ての受信箱へ `/v1/probe` を送って上位 m 個を選ぶ．1 問あたりの要求の数は D ＋ m に決まる．`atrium device-probe-eval` の dev で，Iteration 7 の事前登録の規則（比べる centroid_probe の K の dev の hit@2 以上になる最小の D）で選ぶ．1 データ源 1 シャード（EnronQA）でだけ使える |
| `top_m.medrag` | 3 | MedRAG で `card_sim`（E2 の `desc_sim`）が問い合わせるデータ源の数（p0003 S5）．4 源の `train`・`val` で F1 が最大の k（候補 {1, 2, 3}）．MedRAG のデータ源は広告を持たないので，質問者が起動時に自己紹介文を MedCPT の文書側で埋め込んで付ける |

## llm

| キー | 既定値 | 意味 |
|---|---|---|
| `ollama_version` | `0.35.1` | 各ノードで使う Ollama のイメージのバージョン（registry の `mirror/ollama` を使う） |
| `requester_model` | `llama3.1:8b` | 断片返却型で質問者が使う Ollama のモデル（RAGRoute の既定と同じ系列） |
| `expert_model` | `qwen3:0.6b` | 宿る型で各専門家が使うモデル．E0 の実測で見直す |
| `expert_model_gpu` | `llama3.1:8b` | GPU を持つ専門家が宿る型で使うモデル（p0004）．省略すると `expert_model` と同じ |
| `num_predict` | 2048 | 生成の上限トークン数 |
| `num_ctx` | 16384 | 文脈長．15 断片 × 約 250 トークンに質問と指示を足しても収まる長さ |
| `think` | `false` | qwen3 系の思考モード |
| `temperature` | 0.0 | 生成の温度．0 で貪欲な復号（同じ入力に同じ回答．backlog B3） |
| `requester_num_parallel` | 2 | 質問者の Ollama が同時に処理する要求の数．8B・文脈長 16384 では 2 件分の KV キャッシュまでが 12 GB に収まる |
| `timeout_s` | 1800 | 1 回の生成の打ち切り時間（秒） |

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
| `remote_dir` | `/home/denjo/workspace/ktakahashi/atrium` | 制御点と全ノードで成果物（リポジトリの写し・シャード・compose・結果）を置く絶対パス．全ホスト共通で，デプロイ先を変えるときはこの 1 か所だけを変える |
| `data_dir` | `/home/denjo/atrium-data` | 制御点のデータディレクトリ（絶対パス） |
| `registry_port` | 5000 | 制御点のローカル registry．wafl500〜509 には制御点の 127.0.0.1:5000 への SSH 転送が既に張られており，それをそのまま使う |
| `node_port` | 8100 | 専門家ノードの HTTP ポート |
| `shard_budget_gb` | 6.0 | 1 シャードの fp16 の埋め込みの目安．変えるとシャードの切り方が変わるので，制御点で `shards` 以降の段を作り直す必要がある |

### デバイス（`requester`・`experts`・`gpu_workers`）

実験で使うデバイスは役割ごとのリストに書く．**1 台のデバイスが同時に持つ役割は 1 つだけ**で，役割はどのリストに置くかで決まる．
同じホストを 2 か所（または同じリストに 2 回）書くと，読み込み時にエラーになる．
ただし `experts` と `gpu_workers` の両方に置くことだけは許す．GPU PC は，データ準備（`gpu_workers`）→ 実験（`experts`）→ 採点の順に
時間を分けて使い，同時には持たないためである（p0004 §8.1．ユーザーの承認 U2）．
`requester_llm` を省略すると，質問者の LLM は質問者と同じホストで動く（制御点が質問者と質問者の LLM を兼ねる）．

```yaml
cluster:
  requester: {host: "wafl-ctrl5"}
  experts:
    - {host: "192.168.13.100"}
    - {host: "192.168.13.101", ssh_user: alice}   # デバイスごとに SSH のユーザーを変えられる
  gpu_workers:
    - {host: "192.168.15.100"}
```

| リスト | 役割 | 現在の構成 |
|---|---|---|
| `requester`（1 台） | 質問者．質問を投げ，断片返却型では回答も生成する | wafl-ctrl5（制御点が兼ねる） |
| `requester_llm`（省略可．1 台） | 質問者の LLM（Ollama，`llm.requester_model`）を動かす GPU PC．省略すると質問者と同じホストで動かす．質問者の GPU で再ランク（`merge=cross_encoder`）を動かすと 8B の Ollama と VRAM を取り合い，一部の層が CPU で動いて生成が約 4 倍遅くなるため，別の GPU に分ける | 省略（制御点が兼ねる）．MedRAG の `merge=cross_encoder` を流すときだけ，`{host: "192.168.15.100"}`（wafl500）にし，`experts` と `gpu_workers` から wafl500 を外す（役割は 1 つだけ） |
| `experts` | 専門家．シャード数だけ先頭から使い，足りなければ巡回して 1 台に複数のシャードを載せる（EnronQA は 150 個の受信箱を全台に均等に配る）．GPU を持つ専門家は Ollama を GPU で動かし，`llm.expert_model_gpu` を使う | CPU の 192.168.13.100〜109，192.168.14.100〜109 の後ろに，GPU の 192.168.15.100〜109（計 30 台） |
| `gpu_workers` | データ準備の埋め込みを制御点の GPU と分担する GPU PC | 192.168.15.100〜109（wafl500〜509） |

各要素のキーは `host`（必須）と `ssh_user`（省略時は `cluster.ssh_user`）である．
質問者には制御点（`cluster.control` と同じホスト）を指定できる．その場合の `REMOTE_DIR` の扱いは [d0001](d0001_architecture.md) §1 にある．
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
| `medrag.mirage_url` / `statpearls_url` | MIRAGE のベンチマークと StatPearls の tarball の取得元 |
| `medrag.descriptions` | 各データ源の自己紹介文（Agent Card の description） |
| `feb4rag.sources` | 13 エンジン（signal1m・robust04・trec-news を除く．RAGRoute と同じ） |
| `feb4rag.feb4rag_commit` | FeB4RAG のリポジトリのコミット |
| `feb4rag.beir_hf_repo` | BEIR のコーパスを取得する Hugging Face のデータセット（`{name}` はエンジン名） |
| `feb4rag.centroid_sample` / `centroid_sample_overrides` | 重心の推定に使う文書数（5.8B の SGPT を CPU で動かす trec-covid は少なくする） |

### data.enronqa（p0004．省略できる）

EnronQA（Enron の 150 人の受信箱）を使う実験の設定である．省略すると `dataset: enronqa` は使えないが，
既存の `medrag`・`feb4rag` の実験には影響しない．受信箱 1 個が 1 個のデータ源で，1 個のシャードになる．

| キー | 意味 |
|---|---|
| `enronqa.sources` | 150 個の受信箱の ID（順序はルーターの one-hot の順序にもなる） |
| `enronqa.hf_repo` / `hf_revision` | 取得元の Hugging Face のデータセットと，固定するリビジョン |
| `enronqa.encoder` / `embed_batch_size` | メールとクエリの埋め込みの符号化器（Snowflake arctic-embed-m-v1.5）とバッチの大きさ |
| `enronqa.test_per_inbox` / `dev_per_inbox` / `train_per_inbox` | 受信箱あたりに抜き出す質問の数（評価／公開情報の大きさの選択／中央の分類器の学習） |
| `enronqa.duplicate_jaccard` | 他の受信箱にほぼ同じメールがある質問を主評価から除く Jaccard 類似度の閾値 |
| `enronqa.k_context` | 1 問の回答に使うメールの数（受信箱からの取得数と統合後の数の両方．原論文と同じ 5） |
| `enronqa.owner_probe_per_inbox` | 所有者特定の評価で受信箱ごとに抜き出すメールの数 |
| `enronqa.card_subjects` / `term_sketch_size` / `n_centroids` | 公開情報の大きさ（Agent Card に載せる件名の数，語のスケッチの語数 T，重心の数 C）．dev で選ぶ |
| `enronqa.ragroute_negatives` | 中央の分類器の学習で 1 問あたりに使う関連の無い受信箱の数 |
| `enronqa.judge_model` / `judge_reference_model` | 自由記述の回答を採点する判定モデル（既定は qwen3:8b．質問者の llama3.1:8b と別系統にして自己贔屓を避ける）と，判定モデルとの一致（Cohen の κ）を測る参照モデル（qwen3:14b） |
| `enronqa.judge_validate_n` / `judge_kappa_n` | 判定モデルの検証に使う件数（付属の別解・誤答それぞれ／κ の測定） |

`retrieval.query_embedding.enronqa` は `cached`（制御点で事前計算する）である．`retrieval.k_ret`・`k_rerank` は MedRAG・FeB4RAG の
値のまま変えず，EnronQA では `enronqa.k_context` を使う．
