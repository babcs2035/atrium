# d0004 結果の書式と指標の定義

## 1. 実行のディレクトリ

`results/<run_id>/` に次のファイルができる．

| ファイル | 作るもの | 内容 |
|---|---|---|
| `run_meta.json` | start | 実行の条件（`config.yaml` の全体・git のコミット・データ源とシャード・開始と終了の時刻・失敗数） |
| `results.jsonl` | start | 1 問 1 行の結果（§2） |
| `requester.log` | start | 質問者のログ（Git では追跡しない） |
| `metrics.json` | analyze | 指標（§3） |
| `analysis_report.md` | analyze | 指標の表 |
| `logs/<host>.log` | analyze | 専門家のログ（Git では追跡しない） |
| `e0/<host>/`，`e0_summary.md` | start / analyze | E0 の実測値とその表（§4） |

git のコミットは，未コミットの変更があると末尾に `-dirty` が付く．

## 2. results.jsonl の各行

| フィールド | 意味 |
|---|---|
| `qid`，`bank`，`gold` | 質問 ID（`medqa/0000` など），問題集，正解の選択肢（FeB4RAG は `null`） |
| `routing`，`answer_mode` | ルーティング方式と回答方式 |
| `selected_sources` | ルーターが選んだデータ源 |
| `n_shards_queried` | 問い合わせたシャードの数 |
| `contributing_sources` | 統合後の上位 `k_rerank` 件に断片を出したデータ源（`retrieval_only`・`snippet_return`） |
| `top_doc_ids` | 統合後の断片の ID |
| `shard_stats` | シャードごとの `bytes`（応答の大きさ）・`n_docs`・`node_s`（ノード内の検索時間）・`rtt_s`（往復時間） |
| `bytes_received` | 専門家から受け取った応答の合計バイト数 |
| `snippets_exposed` | デバイスの外へ出た原文の断片の数（宿る型では 0） |
| `answer`，`choice`，`correct` | LLM の出力，抽出した選択肢，正誤（`retrieval_only` では無い） |
| `prompt_tokens`，`output_tokens`，`llm` | 断片返却型の生成の計測値（プレフィル・デコードの秒数） |
| `node_answers` | 宿る型の各ノードの選択肢・検索時間・生成の計測値 |
| `timings` | `embed_s`・`route_s`・`retrieve_s`・`merge_s`・`generate_s`・`e2e_s`（秒） |
| `error` | 失敗したときの例外（成功なら `null`） |

## 3. 指標（metrics.json）

全問（`all`）と，ルーターの学習に使っていない質問（`test`，`labels/split.json`）の 2 通りで集計する．
失敗した行（`error` が `null` でない行）は失敗率にだけ数え，ほかの指標からは除く．

| 指標 | 定義 |
|---|---|
| `selection.precision` / `recall` / `f1` | （質問，データ源）の組を単位にした micro 平均．正解は関連ラベル |
| `selection.mean_selected_sources` | 1 問あたりに問い合わせたデータ源の数の平均 |
| `selection.mean_relevant_sources` | 1 問あたりの関連ありのデータ源の数の平均 |
| `selection.query_reduction_vs_all` | 1 − 平均問い合わせ数 ÷ データ源の数（RAGRoute の「問い合わせ数の削減率」） |
| `selection.mean_shards_queried` | 1 問あたりに問い合わせたシャードの数の平均 |
| `label_consistency` | `contributing_sources` が関連ラベルと完全に一致した質問の割合．MedRAG の `routing=all`・`merge=score` で 1.0 に近くなければ基盤に誤りがある．FeB4RAG のラベルは検索結果ではなく qrels から作るので，この値は基盤の検査には使えない |
| `accuracy.overall` / `by_bank` | 正答率と Wilson の 95% 信頼区間 |
| `accuracy.unparsed_choice_rate` | 選択肢を抽出できなかった割合（不正解として数える） |
| `latency.<段>.p50` / `p95` | 段ごとの所要時間の中央値と 95 パーセンタイル（秒） |
| `mean_bytes_received` | 1 問あたりの受信バイト数の平均 |
| `mean_snippets_exposed` | 1 問あたりのデバイス外へ出た原文の断片数の平均 |

### 関連ラベル

- MedRAG：全データ源から `k_ret` 件ずつ検索し，検索スコアで統合した上位 `k_rerank` 件に 1 件以上の断片を
  出したデータ源（RAGRoute の定義．人手の判定ではない）．
- FeB4RAG：resource selection 用の qrels（LLM による判定）でスコアが 0 より大きいエンジン．

### 統計

- 2 つの実行の正答率の比較は，同じ質問の正誤の組に対する McNemar の正確検定（両側）で行う
  （`atrium compare`）．p 値は二項分布 Bin(b + c, 0.5) の両側確率で，b と c は片方だけが正解した問数である．
- 正答率の信頼区間は Wilson のスコア区間（z = 1.96）である．

## 4. E0 の実測値

| ファイル | 内容 |
|---|---|
| `dimm.txt` | 装着されたメモリ（スロット・容量・速度）．`sudo dmidecode -t memory` から抽出 |
| `host.json`，`host.raw` | メモリの合計，`/` の空き，ディスクの一覧 |
| `llama-bench-<model>.json` | llama-bench の JSON 出力（pp512・pp4096・tg128，4 スレッド） |
| `faiss.json` | fp16 平坦索引（専門家と同じ `IndexScalarQuantizer`）で 1 クエリずつ上位 50 件を検索する時間．ベクトルは乱数 |
| `medcpt.json` | MedCPT のクエリ側モデルで 1 件を埋め込む時間（トークン数 16〜128．所要時間は内容ではなく長さで決まるため，入力は長さだけをそろえた文字列） |
| `../net/<a>_<b>.json` | ping 20 回の RTT の平均と，iperf3（10 秒）のスループット |
