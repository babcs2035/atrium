# d0001 構成：役割の分担・データの流れ・専門家ノードの API

この文書は，atrium の実験基盤がどのホストで何をし，データがどう流れるかをまとめる．
実装の判断の経緯と RAGRoute との差分は [plans/p0002_implementation.md](../plans/p0002_implementation.md) にある．

## 1. 役割

| 役割 | ホスト | すること | 主なコード |
|---|---|---|---|
| 操作端末 | gpu2 | mise のタスク・イメージの build・外部の資材の取得・分析・テスト | `scripts/tasks/`，`atrium.analysis` |
| 制御点（データ中継点）・質問者 | wafl-ctrl5（RTX 3060） | registry・データ準備・各ノードの操作．質問者として，クエリ埋め込み・ルーティング・統合・断片返却型の回答も行う（`cluster.requester_llm` を省略したとき．省略しないと質問者の LLM は別の GPU PC で動く） | `scripts/remote/`，`atrium.data_*`，`atrium.labels`，`atrium.train_router`，`atrium.requester`，`atrium.experiment`，`atrium.requester_procs`（`experiment.requester_procs` で質問者を複数のプロセスに分ける），`atrium.node_bench`（専門家ノード 1 台への負荷試験） |
| 埋め込み・採点の分担 | 192.168.15.100〜109 = wafl500〜509（RTX 3060．`cluster.gpu_workers`） | データ準備の間は MedCPT・EnronQA の埋め込みを制御点と分担し，実験の後は判定モデルでの採点を分担する．実験の間は専門家（GPU）になる（時間を分けて使う．[d0002](d0002_configuration.md)） | `scripts/remote/prepare_data.sh`，`scripts/remote/judge.sh` |
| 質問者の LLM（省略可） | `cluster.requester_llm` に書いた GPU PC（MedRAG の `merge=cross_encoder` を流すときは wafl500） | 断片返却型の回答を生成する Ollama（llama3.1:8b）．質問者（制御点）の GPU で再ランクを動かすと VRAM を取り合うので，別の GPU に分ける | `docker/compose.requester_llm.yml` |
| 専門家（CPU） | 192.168.13.100〜109，192.168.14.100〜109（GPU なし） | シャードの検索・宿る型の回答 | `atrium.node`，`atrium.store` |
| 専門家（GPU） | 192.168.15.100〜109 = wafl500〜509 | 同上．Ollama を GPU で動かし（`llm.expert_model_gpu`），`delegate_gpu` では対応づけた CPU の専門家 2 台の回答を引き受ける | `atrium.node`，`docker/compose.node_gpu.yml` |

操作端末は各ノードへ直接 SSH しない．操作端末はリポジトリを制御点の `cluster.remote_dir`（既定は `/home/denjo/workspace/ktakahashi/atrium`）へ rsync し，
`ssh wafl-ctrl5 "bash scripts/remote/<task>.sh"` を実行する．制御点は `denjo@<IP>` で各ノードへ SSH する．

自前のイメージ（`atrium-node`，`atrium-full`）は操作端末で build し，SSH の転送（操作端末の 15000 番 → 制御点の
5000 番）を通して制御点の registry（`127.0.0.1:5000`）へ push する．
各ノードは `localhost:5000` から取得する．wafl500〜509 には制御点の 127.0.0.1:5000 への SSH 転送が既に張られており，
それ以外のノードには制御点から SSH の逆トンネル（`ssh -R 5000:localhost:5000`）を張る
（docker は localhost の registry だけを TLS なしで使えるため）．質問者は制御点自身なので，トンネルは要らない．

制御点は質問者を兼ねるので，制御点の `REMOTE_DIR`（`cluster.remote_dir`）はリポジトリの作業ディレクトリと質問者の作業
ディレクトリを兼ねる．同期（`sync_to_control`）は質問者の資材（`data/`・`hf-cache/`・`ollama/`・`compose.yml`・
`placement.json`）を消さないよう除外してあり，`mise run clean -- --full` もこれらだけを消す（リポジトリは残る）．

各ノードはインターネットに出ない．研究室側の回線は不安定で，ノードからの取得が 700 KB/s 程度しか出ないことや，
制御点がインターネットに出られなくなることがある．そこで外部の資材は全て操作端末で取得する（EnronQA のデータだけは，操作端末のディスクの空きが少ないため，制御点で取得する）．

| 資材 | 操作端末での取得 | ノードへの配り方 |
|---|---|---|
| 自前のイメージ（`atrium-node`・`atrium-full`） | build | registry |
| 外部のイメージ（ollama・llama.cpp・iperf3） | pull（`scripts/tasks/fetch_assets.sh`） | registry の `mirror/` |
| Ollama のモデル（`llm.expert_model`・`llm.requester_model`） | ollama pull | 制御点の `$DATA_DIR/ollama` から rsync |
| E0 の GGUF | Hugging Face | 制御点の `$DATA_DIR/gguf` から rsync |
| Hugging Face のモデル（MedCPT・再ランク・FeB4RAG の検索器・EnronQA の符号化器．リビジョンは `atrium.hf_revisions` で固定） | 制御点の `fetch-models`，または操作端末で取得して送る | 制御点の HF のキャッシュから rsync（ノードでは `HF_HUB_OFFLINE=1`） |

## 2. データの流れ

```
[制御点] scripts/remote/prepare_data.sh（setup がバックグラウンドで起動する）
   benchmark → corpus → embed（制御点＋wafl500〜509 で分担）→ shards → queries → labels → split → train
        │ manifest.json（シャードの一覧）
        ▼
[制御点] atrium plan（deploy）── placement.json（どのホストがどのシャードを持つか）
        │ rsync -L（シャード）           │ rsync（質問・クエリ埋め込み・ルーター・配置）
        ▼                               ▼
[専門家] atrium node  ◀── HTTP ──  [質問者] atrium run（start）── results.jsonl
                                        │ rsync
                                        ▼
                               [操作端末] atrium analyze（analyze）── metrics.json, analysis_report.md
```

### 2.1 MedRAG（MIRAGE）

1. MIRAGE の 7,663 問を `questions.jsonl` へ変換する．質問 ID は `medqa/0000` のように問題集の名前を前に付ける．
2. MedRAG の断片を Hugging Face（`MedRAG/pubmed` 等）から取得する．StatPearls は NCBI の tarball を
   取得し，MedRAG の `src/data/statpearls.py`（コミット固定）で断片化する．
3. 断片ファイル（`chunk/<name>.jsonl`）ごとに MedCPT-Article-Encoder で埋め込み，`emb/<name>.f16.npy` に
   fp16 で保存する．制御点と `cluster.gpu_workers`（wafl500〜509）の GPU 11 枚で分担する：未埋め込みの
   ファイルをバイト数が均等になるよう振り分け（`atrium embed-plan`，LPT 法），各 GPU PC へ rsync で送り，
   埋め込みを制御点へ回収する．失敗した GPU PC の分は最後に制御点で補う．入力は `[title, content]` の組，512 トークンで切り詰め，CLS の出力を使う．
   MedRAG の sentence-transformers を使う手順と出力が一致することを確認してある（最大絶対誤差 0.0）．
4. 断片ファイルを名前順のまま，fp16 の埋め込みが `cluster.shard_budget_gb`（既定 6 GB）に収まるように束ねて
   シャードにする．シャードのディレクトリには `shard.json` と，corpus への相対 symlink を置く．
5. 全質問のクエリ埋め込み（MedCPT-Query-Encoder）を計算する．
6. 関連ラベルを作る：全シャードを GPU で総当たりに内積検索し，データ源ごとの上位 `k_ret`（50）件を
   スコアで統合した上位 `k_rerank`（15）件に断片を出したデータ源を「関連あり」とする（RAGRoute の定義）．
7. 学習・評価の分割（問題集ごとに 40% を学習，60% を評価．学習の 10% を検証）を作り，ルーターを学習する．

### 2.2 FeB4RAG

1. FeB4RAG のリポジトリ（コミット固定）から `dataset/` を取得する．790 件の要求を `questions.jsonl` にし，
   resource selection 用の qrels でスコアが 0 より大きいエンジンを関連ありとする．
2. 13 エンジンの BEIR コーパスを Hugging Face の `BeIR/<name>`（parquet）から取得し，`corpus.jsonl` に変換する
   （BEIR の配布元の zip と同じ内容であることを nfcorpus で確認した．配布元は転送が遅くなることがあるため）．
3. エンジンごとに 1 シャードを作る．シャードは配布された検索結果（要求ごとの上位 100 件）と，そこに現れる
   文書の本文だけを持つ（`kind="search_results"`）．重心は，コーパスから無作為に抽出した文書
   （既定 2,000 件．trec-covid は 500 件）をそのエンジンの検索器で埋め込んだ平均で近似する．
4. 検索器（8 種類）ごとに全要求のクエリ埋め込みを計算する．質問者はこれを引いて使う（`cached`）．

### 2.3 EnronQA

1. Hugging Face の `MichaelR207/enron_qa_0922`（リビジョンを固定）の parquet を取得し，受信箱 150 個ごとにメールを `corpus/<inbox>/chunk/<inbox>.jsonl` へ書く．
   メール本文の "File:" の行（受信箱の名前がそのまま入っている付属情報）は，検索・回答・公開情報の全てから除く．
2. メールを arctic-embed-m-v1.5 で埋め込み，fp16 で保存する（制御点と `cluster.gpu_workers` で分担する）．
3. 質問を評価用（test．受信箱あたり最大 20 問）・公開情報の大きさの選択用（dev）・中央の分類器の学習用（train）に抜き出し，全質問のクエリ埋め込みを計算する．
   他の受信箱にほぼ同じメールがある質問は主評価から除く（Jaccard 類似度の閾値 `data.enronqa.duplicate_jaccard`）．
4. 関連ラベル（質問 → 正解のメールの受信箱）と分割を作り，受信箱ごとに 1 シャードを作る．シャードには，持ち主のメールだけから計算した
   公開情報（`Advert`．語のスケッチ・k-means の中心・Agent Card の説明文の埋め込み）を付ける．
5. deploy が，150 個の受信箱を専門家 30 台に 5 個ずつ配置する（`placement.json`．`experiment.seed` で並べ替えた受信箱を，メールの数がおおよそ均等になるよう同数ずつ配る．CPU の専門家に GPU の専門家を対応づける `delegate_of` も入る）．
6. 実験の後に，判定モデル（qwen3:8b）が自由記述の回答を採点する（`judgements.jsonl`．[d0003](d0003_experiment_workflow.md) §5）．

本文は制御点のデータディレクトリにだけ置き，`results/` と Git には入れない．

## 3. 専門家ノードの API

| メソッド | パス | 要求 | 応答 |
|---|---|---|---|
| GET | `/healthz` | － | `{"status", "node_id", "shards", "process_cpu_s", "cgroup_cpu_s", "pid", "workers", "host_cpu"}`．`process_cpu_s` はノードのプロセスの CPU 時間の累計（秒）で，質問者が測定の区間の前後に読んで専門家の側の CPU 使用率を求める（Iteration 8）．`host_cpu` はデバイス全体の `/proc/stat` の累計 `{"busy", "total", "softirq"}`（jiffies）と論理 CPU の数 `n_cpu`（Linux 以外では `null`．負荷試験 `atrium node-bench` が使う．Iteration 9）．`cgroup_cpu_s` はコンテナの cgroup（v2）の全プロセスの CPU 時間の累計（秒．v2 でなければ `null`），`pid` は答えたプロセスの番号，`workers` は `experiment.node_workers`（Iteration 10．複数のプロセスのノードでは答えるプロセスが要求ごとに違いうるので，ノードの全体の CPU は `cgroup_cpu_s` で測る） |
| GET | `/.well-known/agent-card.json` | － | A2A の Agent Card．シャード 1 個を 1 個の skill として名乗る |
| GET | `/v1/profile` | － | シャードごとの `source`・`n_docs`・`dim`・`encoder`・`centroid`・`description` |
| POST | `/v1/retrieve` | `{"shard_id", "k", "embedding"?, "query_id"?}` | `{"shard_id", "docs": [{"doc_id","title","content","score"}], "duration_s"}` |
| POST | `/v1/probe` | `{"shard_ids", "embedding"}` | `{"node_id", "scores": {shard_id: 最高スコア}, "duration_s"}`．本文は返さない（flood_score の 1 段目．p0004） |
| POST | `/v1/generate` | `{"dataset", "question", "options", "docs"}` | `{"node_id", "answer", "llm"}`．B4（`delegate_gpu`）で GPU の専門家が受け取った断片から答える（p0004） |
| POST | `/v1/answer` | `{"dataset", "question", "options", "shard_ids", "k", "embedding"?, "query_id"?}` | `{"node_id", "answer", "choice", "n_context_docs", "top_score", "retrieve_s", "llm", "duration_s"}` |
| POST | `/v1/admin/availability` | `{"up": bool}` | `{"up": bool}`．`experiment.churn` を指定した実験でだけ存在する（EQ7b）．`false` の間，ノードは `/healthz` 以外の要求を 503 で拒否する（プロセスは生きている） |

- MedRAG 型のシャードは `embedding`，FeB4RAG 型は `query_id`（元のデータセットの要求 ID）で検索する．
- `/v1/answer`（宿る型）は，指定したシャードからそれぞれ上位 `k` 件を取り，スコアで統合した上位 `k` 件を
  同じホストの Ollama（`llm.expert_model`）に渡す．応答には原文の断片を含めない．
- エラーは RFC 9457 の `application/problem+json`（`type`・`title`・`status`・`detail`）で返す．
  未知のシャードは 404，次元の不一致・未知の要求 ID は 400，Ollama の失敗は 502，離脱中（EQ7b）は 503 である．

要求の例:

```bash
curl -s http://192.168.13.100:8100/v1/retrieve -H 'content-type: application/json' \
  -d '{"shard_id": "pubmed-00", "k": 3, "embedding": [0.1, ...]}'
```

## 4. 質問者の 1 問の処理

1. クエリ埋め込み（medrag は MedCPT でその場で計算，feb4rag は事前計算を引く）
2. ルーティング：各ノードの `/v1/profile` をデータ源ごとにまとめた `SourceProfile`（重心はシャードの重心を
   文書数で重み付けした平均）を入力に，`Router.select()` が問い合わせるデータ源を返す．`flood_score` は先に全ノードの `/v1/probe` へ
   クエリ埋め込みを送り，返事のあったデータ源の最高スコアを `RoutingQuery.probe_scores` として渡す．
   `experiment.churn` を指定した実験では，質問者のメンバー表（`Requester.visible_urls`）に載っているノードのデータ源だけを `Router.select()` に渡し，
   離脱したノードの失敗（HTTP のエラー・接続の失敗）は問の失敗にせず，返事のあった分で続ける
3. 回答方式ごとの処理
   - `retrieval_only`・`snippet_return`：選んだデータ源の全シャードへ `/v1/retrieve` を並列に送る．
     データ源ごとに上位 `k_ret` 件へ絞ってから（シャードに分けない場合と同じ結果になる），
     `retrieval.merge` の方法で上位 `k_rerank` 件に統合する．`snippet_return` はこれを質問者の LLM に渡す．
   - `local_answer`・`delegate_gpu`：選んだシャードを持つノードへ `/v1/answer` を送る．MedRAG は選択肢を多数決で統合する
     （同数なら，その選択肢を出したノードの最高検索スコアが高い方を採る）．EnronQA は最高の検索スコアのノードの回答を採る．
     `delegate_gpu` では，対応づけた GPU の専門家（`placement.json` の `delegate_of`）が，CPU の専門家の断片を受け取って答える．
   - `snippet_return_small`：`snippet_return` と同じ流れで，質問者が `llm.expert_model` で答える．
4. 結果を `results.jsonl` に 1 行で追記する（書式は [d0004_metrics.md](d0004_metrics.md)）．

## 5. ルーティング方式

`src/atrium/routing/` に次のプロトコルを満たすクラスを作り，`make_router()` に名前を登録し，
`config.py` の `RoutingName` に名前を加える．

```python
class Router(Protocol):
    name: str
    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]: ...
```

`SourceProfile` には自己紹介文（`description`）・重心・検索器名・文書数が入っている．研究計画書 RQ1 の
「自己紹介文の類似度」「代表文書の要約」は，この入力だけで実装できる．新参者を学習から除外する
（leave-one-source-out）評価では，中央で学習した情報を使う方式かどうかを方式の側で明示すること．

E2 の LOSO の評価（p0003）は，実機を使わずに `atrium loso <dataset> --split <split>` が操作端末で行う
（`src/atrium/loso.py`．入力は制御点から写した `artifacts/<dataset>/` の manifest・クエリ埋め込み・ラベル・分割・ルーター）．
方式は次のとおりで，新しい `Router` は足さずに既存のものを使う．

| 方式 | 実装 | 新参源の扱い |
|---|---|---|
| `desc_sim` | `CardSimRouter`．自己紹介文を MedCPT の文書側で埋め込み，`Advert.card_embedding` に入れる | 学習しない（MedRAG だけ） |
| `centroid_sim` | `CentroidSimRouter` | 学習しない |
| `ragroute-frozen` | `train_router(exclude=[新参源], out_dir=artifacts/<dataset>/loso/<新参源>)` で N−1 源を学習し，`RagrouteRouter(allow_unseen=True)` で新参源の one-hot を 0 にして判定する | 再学習しない（MedRAG だけ） |
| `ragroute-upper` | 全データ源で学習済みの `router/` | 再学習した場合の上限 |

上位 k 個を選ぶ方式の k は，設定に置かず，除外ごとに新参源を候補とラベルから外した N−1 源で，
`train`・`val` の質問の F1 が最大のものを選ぶ（候補は `loso.K_CANDIDATES`）．

EnronQA（p0004 の RQ-A）のために，公開情報を使う方式を `src/atrium/routing/advert.py` に置いた．
各受信箱は，自分のメールだけから計算した公開情報（`Advert`．語のスケッチ・k-means の中心・Agent Card の説明文の埋め込み）を
`/v1/profile` で名乗り，質問者はそれだけを使って上位 `routing.top_m` 個に問い合わせる．

| 方式 | 事前に公開するもの | 選び方 |
|---|---|---|
| `oracle` | － | 正解の受信箱だけ（評価用の上限） |
| `flood_score` | なし | 1 段目に全ノードの `/v1/probe` へクエリ埋め込みを送り，最高スコアの上位 m 個に本照会する |
| `card_sim` | Agent Card の説明文（多い件名の一覧）の埋め込み | 内積の上位 m 個 |
| `term_sketch` | 多く使う語と，その語を含むメールの割合 | クエリの語と重なる語の割合の和の上位 m 個 |
| `centroid_sim` | 埋め込みの平均 | 内積の上位 m 個 |
| `multi_centroid` | k-means の C 個の中心 | 中心との内積の最大の上位 m 個 |
| `device_probe` | `multi_centroid` と同じ | 1 段目に `multi_centroid` の得点をノードごとに最大値でまとめた上位 D 台を選び，その D 台の全ての受信箱へ `/v1/probe` を送って上位 m 個に本照会する（D は `routing.probe_d`．Iteration 7） |

これらの評価は，実機を使わないもの（`atrium advert-eval`．RQ-A のオフライン評価，`atrium owner-eval`．持ち主推定の入力を揃えた評価，`atrium newcomer-eval`．150 人での新参者の評価，`atrium churn-eval`．参加・離脱のオフライン評価，`atrium churn-cascade-eval`．二段の方式の参加・離脱のオフライン評価，`atrium churn-join-eval`．参加の通知の遅れごとのオフライン評価，`atrium churn-leave-eval`．離脱の通知の遅れごとのオフライン評価，`atrium churn-refill-eval`．返事の無い probe の枠を補う変種のオフライン評価，`atrium churn-budget-eval`．device_probe の D ごとに補う変種と補わない構成を同じ問い合わせの数で比べるオフライン評価）と，
実機に流すもの（`experiment.routing` に名前を書く．参加・離脱の再生は `experiment.churn`）がある．指標は [d0004](d0004_metrics.md) にある．

## 6. データの配置

制御点の `cluster.data_dir`（既定は `/home/denjo/atrium-data`）の `<dataset>/` の構成は `src/atrium/paths.py` の冒頭に記した．
各ノードの `cluster.remote_dir` には次を置く．

| ホスト | パス | 内容 |
|---|---|---|
| 専門家 | `shards/<shard_id>/` | シャード（`*.offsets.npy` は起動時に作る行の先頭位置のキャッシュ） |
| 専門家 | `compose.yml`，`config.yaml` | compose（`docker/compose.node.yml` のひな形を `atrium render-compose` で埋めたもの）と設定 |
| 質問者（制御点） | `data/<dataset>/` | 質問・manifest・クエリ埋め込み・ルーター・qrels |
| 質問者（制御点） | `placement.json`，`results/<run_id>/` | 配置と実験の結果 |
| E0 の対象 | `gguf/`，`results/<run_id>/` | llama-bench の GGUF と実測の結果 |

## 7. メモリと容量

| 項目 | 値 |
|---|---|
| MedRAG の 4 コーパスのシャード数（6 GiB ごと） | PubMed 6，Wikipedia 8，StatPearls 1，Textbooks 1 の計 16（1 シャード最大約 419 万断片）．StatPearls は 384,050 断片で，研究計画書 §7.1 の約 30.1 万より多い（NCBI の配布物を MedRAG のスクリプトで断片化した結果） |
| 専門家 1 台のメモリ | fp16 索引 約 6 GB ＋ 行の先頭位置（1 断片 8 バイト）＋ Ollama（宿る型のみ） |
| 専門家 1 台のディスク | MedRAG のシャード 1 個で 12〜18 GB（埋め込み約 6 GB ＋ 本文）．FeB4RAG のシャードは数十 MB．使用率は専門家によって違う（他の用途のファイルを含む）．実験の前に確かめる |
| 制御点のデータディレクトリ | MedRAG 約 187 GB（本文と fp16 の埋め込み），FeB4RAG 約 16 GB，EnronQA 約 1.2 GB，Ollama のモデル約 20 GB，GGUF 約 8.5 GB．HF のモデルのキャッシュは質問者の作業ディレクトリの `hf-cache/`（約 4 GB） |

制御点では，断片化を終えた StatPearls の tarball と展開物，BEIR の取得に使った一時ファイルは削除してある．
コーパスの取得の段は `chunk/.complete` があれば飛ばされるので，削除しても setup の再実行には影響しない．
