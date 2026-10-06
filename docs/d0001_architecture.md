# d0001 構成：役割の分担・データの流れ・専門家ノードの API

この文書は，atrium の実験基盤がどのホストで何をし，データがどう流れるかをまとめる．
実装の判断の経緯と RAGRoute との差分は [plans/p0002_implementation.md](../plans/p0002_implementation.md) にある．

## 1. 役割

| 役割 | ホスト | すること | 主なコード |
|---|---|---|---|
| 操作端末 | gpu2 | mise のタスク・イメージの build・外部の資材の取得・分析・テスト | `scripts/tasks/`，`atrium.analysis` |
| 制御点（データ中継点） | wafl-ctrl5（RTX 3060） | registry・データ準備・各ノードの操作 | `scripts/remote/`，`atrium.data_*`，`atrium.labels`，`atrium.train_router` |
| 埋め込みの分担 | 192.168.15.101〜109 = wafl501〜509（RTX 3060） | データ準備の間だけ，MedCPT の埋め込みを制御点と分担する | `scripts/remote/prepare_data.sh` |
| 質問者 | 192.168.15.100 = wafl500（RTX 3060） | クエリ埋め込み・ルーティング・統合・断片返却型の回答 | `atrium.requester`，`atrium.experiment` |
| 専門家 | 192.168.13.100〜109，192.168.14.100〜109（GPU なし） | シャードの検索・宿る型の回答 | `atrium.node`，`atrium.store` |

操作端末は各ノードへ直接 SSH しない．操作端末はリポジトリを制御点の `/home/denjo/atrium` へ rsync し，
`ssh wafl-ctrl5 "bash scripts/remote/<task>.sh"` を実行する．制御点は `denjo@<IP>` で各ノードへ SSH する．

自前のイメージ（`atrium-node`，`atrium-full`）は操作端末で build し，SSH の転送（操作端末の 15000 番 → 制御点の
5000 番）を通して制御点の registry（`127.0.0.1:5000`）へ push する．
各ノードは `localhost:5000` から取得する．wafl500〜509 には制御点の 127.0.0.1:5000 への SSH 転送が既に張られており，
それ以外のノードには制御点から SSH の逆トンネル（`ssh -R 5000:localhost:5000`）を張る
（docker は localhost の registry だけを TLS なしで使えるため）．

各ノードはインターネットに出ない．研究室側の回線は不安定で，ノードからの取得が 700 KB/s 程度しか出ないことや，
制御点がインターネットに出られなくなることがあった（2026-10-06）．そこで外部の資材は全て操作端末で取得する．

| 資材 | 操作端末での取得 | ノードへの配り方 |
|---|---|---|
| 自前のイメージ（`atrium-node`・`atrium-full`） | build | registry |
| 外部のイメージ（ollama・llama.cpp・iperf3） | pull（`scripts/tasks/fetch_assets.sh`） | registry の `mirror/` |
| Ollama のモデル（`llm.expert_model`・`llm.requester_model`） | ollama pull | 制御点の `$DATA_DIR/ollama` から rsync |
| E0 の GGUF | Hugging Face | 制御点の `$DATA_DIR/gguf` から rsync |
| Hugging Face のモデル（MedCPT・再ランク・FeB4RAG の検索器） | 制御点の `fetch-models`，または操作端末で取得して送る | 制御点の HF のキャッシュから rsync（ノードでは `HF_HUB_OFFLINE=1`） |

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
   fp16 で保存する．制御点と `cluster.gpu_workers`（wafl501〜509）の GPU 10 枚で分担する：未埋め込みの
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

## 3. 専門家ノードの API

| メソッド | パス | 要求 | 応答 |
|---|---|---|---|
| GET | `/healthz` | － | `{"status", "node_id", "shards"}` |
| GET | `/.well-known/agent-card.json` | － | A2A の Agent Card．シャード 1 個を 1 個の skill として名乗る |
| GET | `/v1/profile` | － | シャードごとの `source`・`n_docs`・`dim`・`encoder`・`centroid`・`description` |
| POST | `/v1/retrieve` | `{"shard_id", "k", "embedding"?, "query_id"?}` | `{"shard_id", "docs": [{"doc_id","title","content","score"}], "duration_s"}` |
| POST | `/v1/answer` | `{"dataset", "question", "options", "shard_ids", "k", "embedding"?, "query_id"?}` | `{"node_id", "answer", "choice", "n_context_docs", "top_score", "retrieve_s", "llm", "duration_s"}` |

- MedRAG 型のシャードは `embedding`，FeB4RAG 型は `query_id`（元のデータセットの要求 ID）で検索する．
- `/v1/answer`（宿る型）は，指定したシャードからそれぞれ上位 `k` 件を取り，スコアで統合した上位 `k` 件を
  同じホストの Ollama（`llm.expert_model`）に渡す．応答には原文の断片を含めない．
- エラーは RFC 9457 の `application/problem+json`（`type`・`title`・`status`・`detail`）で返す．
  未知のシャードは 404，次元の不一致・未知の要求 ID は 400，Ollama の失敗は 502 である．

要求の例:

```bash
curl -s http://192.168.13.100:8100/v1/retrieve -H 'content-type: application/json' \
  -d '{"shard_id": "pubmed-00", "k": 3, "embedding": [0.1, ...]}'
```

## 4. 質問者の 1 問の処理

1. クエリ埋め込み（medrag は MedCPT でその場で計算，feb4rag は事前計算を引く）
2. ルーティング：各ノードの `/v1/profile` をデータ源ごとにまとめた `SourceProfile`（重心はシャードの重心を
   文書数で重み付けした平均）を入力に，`Router.select()` が問い合わせるデータ源を返す
3. 回答方式ごとの処理
   - `retrieval_only`・`snippet_return`：選んだデータ源の全シャードへ `/v1/retrieve` を並列に送る．
     データ源ごとに上位 `k_ret` 件へ絞ってから（シャードに分けない場合と同じ結果になる），
     `retrieval.merge` の方法で上位 `k_rerank` 件に統合する．`snippet_return` はこれを質問者の LLM に渡す．
   - `local_answer`：選んだシャードを持つノードへ `/v1/answer` を送り，選択肢を多数決で統合する
     （同数なら，その選択肢を出したノードの最高検索スコアが高い方を採る）．
4. 結果を `results.jsonl` に 1 行で追記する（書式は [d0004_metrics.md](d0004_metrics.md)）．

## 5. ルーティング方式の追加（E2 以降）

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

## 6. データの配置

制御点の `/home/denjo/atrium-data/<dataset>/` の構成は `src/atrium/paths.py` の冒頭に記した．
各ノードの `/home/denjo/atrium/` には次を置く．

| ホスト | パス | 内容 |
|---|---|---|
| 専門家 | `shards/<shard_id>/` | シャード（`*.offsets.npy` は起動時に作る行の先頭位置のキャッシュ） |
| 専門家 | `compose.yml`，`.env`，`config.yaml` | compose（`docker/compose.node.yml`）と設定 |
| 質問者 | `data/<dataset>/` | 質問・manifest・クエリ埋め込み・ルーター・qrels |
| 質問者 | `placement.json`，`results/<run_id>/` | 配置と実験の結果 |
| E0 の対象 | `gguf/`，`results/<run_id>/` | llama-bench の GGUF と実測の結果 |

## 7. メモリと容量

| 項目 | 値 |
|---|---|
| MedRAG の 4 コーパスのシャード数（6 GiB ごと） | PubMed 6，Wikipedia 8，StatPearls 1，Textbooks 1 の計 16（1 シャード最大約 419 万断片）．StatPearls は 384,050 断片で，研究計画書 §7.1 の約 30.1 万より多い（2026-10 時点の NCBI の配布物を MedRAG のスクリプトで断片化した結果） |
| 専門家 1 台のメモリ | fp16 索引 約 6 GB ＋ 行の先頭位置（1 断片 8 バイト）＋ Ollama（宿る型のみ） |
| 専門家 1 台のディスク | MedRAG のシャード 1 個で 12〜18 GB（埋め込み約 6 GB ＋ 本文）．FeB4RAG のシャードは数十 MB．使用率は 41〜83%（2026-10-06．他の用途のファイルを含む） |
| 制御点のデータディレクトリ | MedRAG 約 186 GB（本文と fp16 の埋め込み），FeB4RAG 約 16 GB，HF のモデル約 73 GB，Ollama のモデルと GGUF 約 14 GB．ディスクの空きは約 500 GB（2026-10-06） |

制御点では，断片化を終えた StatPearls の tarball と展開物，BEIR の取得に使った一時ファイルは削除してある．
コーパスの取得の段は `chunk/.complete` があれば飛ばされるので，削除しても setup の再実行には影響しない．
