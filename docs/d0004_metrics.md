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
| `logs/<host>.log` | start | 専門家のログ（実行の直後に集める．Git では追跡しない） |
| `placement.json` | start | この実行の配置（どのホストがどのシャードを持っていたか．委託先の対応 `delegate_of` を含む） |
| `judgements.jsonl` | `atrium judge` | EnronQA の回答の採点（§3．Git で追跡する．本文は含まない） |
| `e0/<host>/`，`e0_summary.md` | start / analyze | E0 の実測値とその表（§4） |

git のコミットは，未コミットの変更があると末尾に `-dirty` が付く．

### 質問者・専門家の CPU（`run_meta.json`．`churn` を指定しない実験）

`started_at`〜`finished_at` は問を流した区間である．`experiment.requester_procs` が 2 以上なら，全プロセスが一斉に流し始めた時刻から最後のプロセスが終えた時刻までで，各プロセスの準備の時間を含まない．
処理量は「成功した問の数 ÷（`finished_at` − `started_at`）」で求める．

| 項目 | 意味 |
|---|---|
| `requester_procs` | 質問者のプロセスの数 |
| `probe_body` | probe の本文の作り方（`experiment.probe_body`．`per_request` / `pre_encoded`） |
| `retrieve_body` | retrieve の本文の作り方（`experiment.retrieve_body`．`per_request` / `pre_encoded`） |
| `sniffio_lookup`・`sniffio_installed` | `experiment.sniffio_lookup`（`search` / `blocked`）と，その実行の質問者の環境に sniffio が入っていたか（`true` なら探索の費用は元から無い） |
| `pool_assign`・`httpcore_version` | `experiment.pool_assign`（`stock` / `lean`）と，その実行の質問者の httpcore の版 |
| `requester_tcp` | 質問の処理の前後の，質問者のコンテナのネットワークの名前空間の TCP の再送の計数の差（`RetransSegs`・`TCPSynRetrans`・`TCPTimeouts`．`/proc/net/snmp`・`/proc/net/netstat`）．N ≥ 2 では子のプロセスの起動の区間を含む．host ネットワークのコンテナではホスト全体の値になる．読めなければ `null`．記録だけで判定に使わない |
| `requester_cpu_s`（`_user_s`・`_sys_s`） | 問を流した区間の質問者の全プロセスの CPU 時間の合計（秒） |
| `requester_cpu_util` | `requester_cpu_s` ÷ 区間の長さ（コアの数の単位．N 個のプロセスが全て 1 コアに張り付けば N） |
| `requester_cpu_util_per_proc`，`requester_parallel_per_proc` | プロセスごとの CPU 使用率（そのプロセスが流していた区間で割る）と同時の問の数 |
| `requester_wall_s_per_proc` | プロセスごとの流していた区間の長さ（2 プロセス以上のときだけ） |
| `expert_cpu_util`，`expert_cpu_util_max`・`_median` | ノード（ホスト名）ごとの専門家のプロセスの CPU 使用率（`/healthz` の `process_cpu_s` の区間の前後の差 ÷ 区間の長さ．コアの数の単位）と，その最大値・中央値．`experiment.node_workers` が 2 以上のノードは `cgroup_cpu_s`（コンテナの全プロセスの合計）の差を使う．読めなかったノードは含めない |
| `control_cpu_busy`，`control_cpu_softirq` | 質問者のデバイス全体（制御点．`/proc/stat`）の，全 CPU に対する忙しい時間と softirq の時間の割合（0〜1．wafl-ctrl5 は論理 12 CPU・物理 6 コア） |

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
| `docs_exposed_by_source` | 持ち主のデバイスの外に出た本文の数（データ源ごと．p0004）．断片返却型は受け取った断片，委託（`delegate_gpu`）は委託先へ渡した断片，宿る型は空 |
| `query_recipients` | クエリ（質問文または埋め込み）を受け取ったデバイスの数（p0004）．`flood_score` の 1 段目は全ノードに届く |
| `probe_recipients`，`probe_bytes` | probe（`flood_score` の 1 段目，`centroid_probe` の 2 段目）で問い合わせたノードの数と，受け取ったスコアの大きさ |
| `probe_shards`，`probe_shards_per_device`，`probe_node_s_max` | probe したシャードの数（`flood_score` は全シャード，`centroid_probe` は K），デバイスごとのシャードの数の並び，ノード内の probe の時間（`ProbeResponse.duration_s`）の最大（`timings.probe_s` との差が待ち行列と通信の時間）．`centroid_probe` の `route_s` は 1 段目の採点と 2 段目の選択の合計で，probe の待ちは `probe_s` に入る |
| `final_answer` | EnronQA の自由記述の回答（出力の最後の "Answer:" の行） |
| `answer_overlap_lcs` | 回答文と正解のメールの最長共通部分列の比率（判定 `atrium judge` の後に付く．p0004 §6.3） |

負荷試験の連続実行（`results/*_summary/iterN_chain.sh`．Iteration 12 以降）は，`run_meta.json` の外に次の記録を残す．
- 連続実行のログの隣の `<log>.<run_id>.tcp`：問を流す `mise run start` の直前・直後の制御点の `nstat -asz TcpRetransSegs TcpExtTCPSynRetrans TcpExtTCPTimeouts`（取れなければその旨）．
  これは制御点の**ホストの**ネットワークの名前空間の値である．質問者のコンテナは既定のブリッジのネットワーク（`docker/compose.requester.yml` に `network_mode` の指定が無い）で動くので，質問者のソケットの再送はこの値に入らない．質問者の再送を見るには，コンテナの名前空間で取る（`docker exec` で `/proc/net/netstat` を読むか，`nsenter -n`）．
- 集計 `iter12_summary.py` が `results.jsonl` から数える遅れの問の数：`timings.probe_s` ≥ 0.9 秒の問の数と，そのうち `probe_node_s_max` < 0.5 秒（ノードの計算によらない遅れ）の数．
  閾値は固定の 0.9 秒なので，probe_s の中央値が 0.1 秒前後の条件（`requester_procs` = 8）でだけ遅れを数える．質問者が 1 コアに張り付く N = 1 の flood_score では probe_s の中央値が約 1 秒（CPU の待ち行列）になり，この数は遅れではなく待ち行列を数える．
- Iteration 13 の `iter13_summary.py` は上の絶対の閾値を N = 8 の実行だけで数え，全実行で「`probe_s` がその実行の中央値の 5 倍以上の問の数」（相対の閾値）も出す．再試行の例外の型ごとの数は requester.log の `retrying <url> (i/n): <型名>` から数える．

## 3. 指標（metrics.json）

全問（`all`）と，ルーターの学習に使っていない質問（`test`，`labels/split.json`）の 2 通りで集計する．
失敗した行（`error` が `null` でない行）は失敗率にだけ数え，ほかの指標からは除く．

| 指標 | 定義 |
|---|---|
| `n` | 集計した質問数（失敗を含む） |
| `n_unlabeled` | 成功した質問のうち関連ラベルが無いもの．データ源の選択とラベル一致の集計から除く（FeB4RAG の qrels に現れない要求など） |
| `failure_rate` | 失敗した質問の割合 |
| `selection.precision` / `recall` / `f1` | （質問，データ源）の組を単位にした micro 平均．正解は関連ラベル |
| `selection.mean_selected_sources` | 1 問あたりに問い合わせたデータ源の数の平均 |
| `selection.mean_relevant_sources` | 1 問あたりの関連ありのデータ源の数の平均 |
| `selection.query_reduction_vs_all` | 1 − 平均問い合わせ数 ÷ データ源の数（RAGRoute の「問い合わせ数の削減率」） |
| `selection.mean_shards_queried` | 1 問あたりに問い合わせたシャードの数の平均 |
| `label_consistency` | `contributing_sources` が関連ラベルと完全に一致した質問の割合．ラベルを作ったときと同じ条件（`dataset=medrag`・`routing=all`・`merge=score`）でだけ意味を持つので，その実行でだけ計算する（それ以外は `null`）．MedRAG の `routing=all`・`merge=score` で 1.0 に近くなければ基盤に誤りがある．FeB4RAG のラベルは検索結果ではなく qrels から作るので，この値は基盤の検査には使えない |
| `accuracy.overall` / `by_bank` | 正答率と Wilson の 95% 信頼区間 |
| `accuracy.unparsed_choice_rate` | 選択肢を抽出できなかった割合（不正解として数える） |
| `latency.<段>.p50` / `p95` | 段ごとの所要時間の中央値と 95 パーセンタイル（秒） |
| `mean_bytes_received` | 1 問あたりの受信バイト数の平均 |
| `mean_snippets_exposed` | 1 問あたりのデバイス外へ出た原文の断片数の平均 |
| `gold_doc_retrieval.recall_at_k` | EnronQA：正解のメールが，統合後に LLM へ渡した上位 `k_context` 通に入った質問の割合（検索そのものの再現率） |
| `exposure.mean_docs_exposed` / `mean_query_recipients` / `mean_answer_overlap_lcs` | p0004 の露出の指標（上の 3 つのフィールドの平均）．フィールドの無い実行では `null` |

### 関連ラベル

- MedRAG：全データ源から `k_ret` 件ずつ検索し，検索スコアで統合した上位 `k_rerank` 件に 1 件以上の断片を
  出したデータ源（RAGRoute の定義．人手の判定ではない）．
- FeB4RAG：resource selection 用の qrels（LLM による判定）でスコアが 0 より大きいエンジン．

### 統計

- 2 つの実行の正答率の比較は，同じ質問の正誤の組に対する McNemar の正確検定（両側）で行う
  （`atrium compare`）．p 値は二項分布 Bin(b + c, 0.5) の両側確率で，b と c は片方だけが正解した問数である．
- P2P と中央の精度の判定（p0007 I9．plans/p0007 §4.2）は `atrium compare <P2P の実行> <中央の実行> --out-dir <出力先>` が行い，
  `verdict.json`（`verdict_original`・`verdict_revised`）を書く．差（P2P − 中央）の 95% 信頼区間は，質問を組のまま再抽出する対応ありの
  ブートストラップ（既定は 10,000 回・種 12）で求め，下端が −δ（既定 0.02．`--margin`）より上なら非劣性（`non_inferior`），
  McNemar の正確検定で P2P が有意に高ければ優越（`superior`）である．`--revised-margin` と `--revised-reason` を付けると，事後の見直しの判定を
  `verdict_revised`（`post_hoc: true`）に足す．書いた `verdict_original` は，別の値で書き直そうとすると拒否される．
- 正答率の信頼区間は Wilson のスコア区間（z = 1.96）である．

### EnronQA の採点（`atrium judge`・`judge-validate`・`judge-kappa`）

EnronQA の回答は自由記述なので，実験の後に判定モデル（`data.enronqa.judge_model`）が，正解のメールを見て回答が正解と一致するかを判定する．
`atrium judge` は `results/<run_id>/judgements.jsonl`（質問 ID・正誤・回答と正解のメールの最長共通部分列の比率）を書き，
`atrium analyze` が `accuracy` にこの採点を使う．判定できなかった回答は不正解として数え，`accuracy.unparsed_choice_rate` に割合を出す．
メールの本文と判定モデルの出力は，制御点の `<data_dir>/enronqa/judge_raw/` にだけ残す．

| 指標 | 定義 |
|---|---|
| `judge-validate`：`alternate_judged_correct` / `incorrect_judged_incorrect` | 付属の別解を正解とした割合と，付属の誤答を不正解とした割合（それぞれ 500 件）．どちらも 0.95 以上で合格 |
| `judge-kappa`：`kappa` / `agreement` | 判定モデルと参照モデル（`judge_reference_model`）の判定の Cohen の κ と一致率（各実行から同数を抜き出した 300 件）．κ が 0.8 以上で合格 |

判定モデルは，中身の無い回答（質問の言い換えやひな形の文だけ）を一部，正解とみなすことがある（偽陽性）．この偽陽性は上の 2 つの検証では測れない．
判定の結果は，回答の中身（ひな形の文だけでないか）を併せて確認する．
補正値（`results/20261014_t1_judge_audit/judge_audit.py`）は，`final_answer` が例文 `The answer to the question` **だけ**の回答
（`re.fullmatch(r"\s*the answer to the question\s*[.:]?\s*", ..., re.IGNORECASE)`）を不正解に直した正答率である（狭い補正）．
例文を含む回答を全て不正解にする広い補正は，中身のある正しい答えまで消すので使わない．正答率の主な値は判定モデルの元の値で，補正値は併記する．

### RQ-A のオフライン評価（`atrium advert-eval`．p0004 EQ2）

実機のノードを使わず，各方式のスコアから，正解の受信箱の順位を求める．`results/<run_id>/metrics.json` の `selection`（dev．公開情報の大きさの選択に使う）と
`test`（評価）に，方式ごとの値を書く．

| 指標 | 定義 |
|---|---|
| `param` | 選んだ公開情報の大きさ（card の件名の数・語のスケッチの語数 T・中心の数 C） |
| `hit@1` / `hit@2` / `hit@3`，`hit@1_ci95` | 正解の受信箱が上位 1・2・3 個に入る割合と，ブートストラップの 95% 信頼区間．順位は，正解と同点の受信箱を正解より上に数える |
| `mrr` | 正解の受信箱の順位の逆数の平均（dev で公開情報の大きさを選ぶ指標） |
| `advert_bytes` | 事前に公開する情報の大きさ（全受信箱の合計） |
| `inboxes_receiving_query` | 1 問でクエリを受け取る受信箱の数（質問の露出．`flood_score` は全受信箱） |
| `owner_top1` / `owner_auc` | 所有者の特定：各受信箱から抜き出したメールを，公開情報だけから持ち主の受信箱に当てる正解率と AUC．`flood_score`・`ragroute` は公開情報が無く偶然の水準 |
| `curve` | 公開情報の大きさを変えたときの `hit@1`・`owner_top1`・`advert_bytes` |

`m`（問い合わせる受信箱の数）は，dev で最良の方式の hit@m が 0.9 以上になる最小の値（`m_rule`）である．

### 持ち主推定の入力を揃えた評価（`atrium owner-eval`．p0005 T2）

同じ公開情報・同じ採点で，入力だけを変えて持ち主を当てる．`results/<run_id>/metrics.json` の `methods.<方式>` に，入力ごとの `top1`・`top1_ci95`・`auc` を書く．

| 入力 | 定義 |
|---|---|
| `Q` | 質問文からの特定．test の質問の正解の受信箱を当てる．`top1` は `advert-eval` の `hit@1`（見つけてもらえる度合い）と同じ値になる |
| `E` | メール本文からの特定．各受信箱から抜き出したメール（最大 10 通）の持ち主を当てる．`top1`・`auc` は `advert-eval` の `owner_top1`・`owner_auc` と同じ値になる |
| `E_short` | 短く切ったメール本文からの特定．`E` の各メールを，test の質問のトークン数の中央値（`short_tokens`）に先頭から切ったもの |

`h_o1` は，`E_short` の `top1` の 95% 区間が `Q` の区間より上（`higher`），重なる（`overlap`），下（`lower`）かを示す．
term_sketch の採点は，語の集合を走査して重みを足す順序が Python の文字列のハッシュの種で変わるため，プロセスごとに `top1` が 0.001〜0.003 揺れる（同点の扱い）．

### 持ち主推定の入力からヘッダーを除いた測り直し（`atrium header-eval`．Iteration 2 の R1・R2）

`owner-eval` の `E_short`（ヘッダー込みの先頭トークン）を，区切り線（`=====…`）より後の本文の先頭トークン（`E_body_short`．`Subject:` を含めない）に変えて，持ち主の `top1` を比べる．
`results/<run_id>/metrics.json` の `methods.<方式>`（`Q`・`E`・`E_short` は全 1,460 通，`subset` は本文が空でないメールだけ）と，次の集計を書く．本文は出さず，集計値と語だけを書く．

| キー | 内容 |
|---|---|
| `methods.<方式>.subset.{E_short,E_body_short,E_body,E_subj_short}` | 同じ部分集合での `top1`・`top1_ci95`・`auc`．`E_body` は本文の全体，`E_subj_short` は件名の値＋本文の先頭トークン（参考） |
| `methods.<方式>.judgement` | `h_r1b`（`supported`・`partially_supported`・`rejected`・`above`．`E_body_short` の区間が `E_short` より下か）と，`E_body_short` と `Q` の `h_o1` |
| `n_empty_body`・`n_no_separator` | 本文が空のメールの通数（部分集合から除く）と，区切り線の無いメール（全体を本文とする）の通数 |
| `regression.t2`・`regression.r2` | 全体の `Q`・`E`・`E_short` が T2 の `metrics.json` と（許容 5e-4，term_sketch は 2e-3），群の重みづけ和が全 test の hit と（5e-4）一致するか |
| `feature_terms` | 受信箱ごとの term_sketch の上位 20・200 語の，ヘッダー依存の語（本文だけの文書頻度がヘッダー込みの半分未満）・ドメインの語・アドレスの名前の語の割合の平均 |
| `r2.groups.<方式>` | 質問を A（持ち主の姓を含む）・B（アドレスの名前の語を含む）・C（どちらでもない）に分けた群ごとの `n`・`hit@1`・`hit@2` と区間（`A+B` も）．`r2.named_easier` は `A+B` の `hit@2` の区間の下端が `C` の上端より上か |
| `r2.matched_words_in_b` | 群 B で一致した語の上位 30 個（人名でない語の誤検出を確かめる） |

### 公開情報をヘッダーを除いた本文から作り直した測り直し（`atrium body-advert-eval`．Iteration 3）

4 方式（card_sim・term_sketch・centroid_sim・multi_centroid）の公開情報を，ヘッダー込み（`full`．既存の chunk と埋め込み）と，区切り線（`=====…`）より後の本文だけ（`body_only`．`Subject:`・`Sender:`・`Recipients:` を含まない）の 2 通りで作り，同じ質問・同じメール・同じ採点で比べる．
本文は全メールを文書側で埋め込み直し（fp16 に丸めてから float32 に戻す），データディレクトリには保存しない．card_sim は件名だけの公開情報なので `body_only` でも変わらず，対照になる．公開情報の大きさ（件名 40，T = 200，C = 32）・m・k-means の種は固定する．
`results/<run_id>/metrics.json` に集計値だけを書く（本文・本文の埋め込みは書かない）．

| キー | 内容 |
|---|---|
| `variants.{full,body_only}.methods.<方式>.hit.{all,A,non_A}` | test の `hit@1`・`hit@2` と区間．A は持ち主の姓を含む問．`centroid_probe` は本文だけの multi_centroid を 1 段目にした参考の二段（K = `routing.probe_k.enronqa`） |
| `variants.<v>.owner.<方式>.{E,E_body_short,E_body,E_all}` | 所有者の `top1`・`top1_ci95`・`auc`．`E_all` は本文が空のメールも含む全体（退行の確認用） |
| `diff_body_only_minus_full` | 同じ問・同じメールの組で対にした，`body_only` − `full` の差の `diff`・`ci95`（復元抽出 1,000 回，種は `experiment.seed`）・`verdict`（上端 < 0 で `lower`，下端 > 0 で `higher`，他は `unclear`） |
| `curves_body_only` | term_sketch の T・multi_centroid の C を変えた H-A2 の曲線（公開情報の大きさ，`hit@1`，所有者 `top1`（E））と順位相関 |
| `surname_in_public_info` | 持ち主の姓が term_sketch の上位 20 語・全 T 語・card_sim の説明文に入る受信箱の数 |
| `n_no_separator`・`n_empty_body`・`n_emails` | 区切り線の無いメール（全体を本文とする）・本文が空のメール（本文だけの公開情報から除く）・全メールの通数 |
| `regression` | `full` が EQ2・Iteration 2（`header-eval`）・Iteration 1（`cascade-eval`）の `metrics.json` と（許容 5e-4，term_sketch は 2e-3），card_sim の `body_only` が `full` と，本文の埋め込みの行数が既存の埋め込みと一致するか |
| `judgement` | 事前登録の規則：H-B1（term_sketch）・H-B3・結論（multi_centroid．`adopt`・`partial`・`reject`） |

### centroid_probe の届くデバイスの数と再現率（`atrium probe-reach`．Iteration 4）

制御点のコンテナで流す（`scripts/remote/probe_reach.sh`．`results/<run>/placement.json` の配置を使う）．実機のノードは使わない．
実機の質問（questions.jsonl の受信箱ごとの先頭 2 問 = 300 問）と test の全問について，K ∈ {4, 8, 15, 30, 50, 75, 150} ごとに，
1 問で probe が届くデバイスの数（平均・中央値・最小・最大・度数），デバイスあたりに probe するシャードの数，上位 K の再現率（正解の受信箱が 1 段目の上位 K に入る割合），2 段目まで通した hit@m を `metrics.json` に出す．
`selected_sources` は，実機の質問の K = 30・75 での選ばれる受信箱（上位 m 個）で，実機の `selected_sources` との照合に使う．

### device_probe の D ごとの hit@m と D の選択（`atrium device-probe-eval`．Iteration 7）

制御点のコンテナで流す（`scripts/remote/device_probe_eval.sh`．`results/<run>/placement.json` の配置と，Iteration 1 の cascade-eval の `metrics.json` を使う）．実機のノードは使わない．
`curve.<dev|test>.device_probe` は D（2〜全台の偶数と全台）ごとの probe する受信箱の数の平均と hit@m（最大値でまとめたもの `max_hit@m` と，参考の和 `sum_hit@m`．test は最大値の hit@1・hit@m の 95% 区間も），
`curve.<split>.centroid_probe` は同じ採点での K ごとの届くデバイスの数の平均と hit@m である．
`choices` は比べる K（75・30）ごとの D の選択（dev の目標 hit@m，選んだ D，要求の数の比 (D ＋ m) ／（届くデバイス ＋ m），見込みあり，test の劣らないかの確認）．
`checks` は正しさの確認（centroid_probe の値が Iteration 1 と一致，D = 全台が flood_score と一致，最大値でまとめた上位 D 台が「届くデバイスが D 台以下の上位 K 個」を含まない問の数が 0）．
`live_selected_sources` は見込みありの D での，実機の質問（受信箱ごとの先頭 2 問）の選ばれる受信箱で，実機の `selected_sources` との照合に使う．

### 専門家ノード 1 台への負荷試験（`atrium node-bench`．Iteration 9）

制御点のコンテナで流す（`scripts/remote/node_bench.sh`．deploy で起動したノードのうち `--target-host` の 1 台だけへ送る）．`results/<out_name>/node_bench.json` の `levels` に，同時の要求の数 C（流した順）ごとの次の値を書く．
要求の本文は flood_score がそのノードへ送るのと同じ `/v1/probe`（EnronQA の全 2,940 問の cached の埋め込みを `atrium run` と同じ順に並べ，そのノードの全シャード）で，閉じた系（前の要求が返ったら次を送る）で送り続ける．
各水準は慣らし `warmup_s`（既定 5 秒）の後の `measure_s`（既定 30 秒）が測定の区間で，区間の内に返った要求だけを集計する．

| 項目 | 意味 |
|---|---|
| `concurrency`，`procs`，`concurrency_per_proc` | 同時の要求の数 C と，それを割った負荷生成のプロセスの数（min(C, `max_procs`)）とプロセスごとの数 |
| `throughput_rps` | 区間の内に成功した要求の数 ÷ 区間の長さ（要求/秒） |
| `latency_s`（p50・p95・p99），`latency_mean_s` | 負荷生成の側から見た 1 要求の往復の時間 |
| `node_s` | ノードの中の probe の計算時間（応答の `duration_s`） |
| `little_ratio` | 処理量 × 平均の往復の時間 ÷ C．閉じた系では 1 に近く，下回れば負荷生成の側の待ち（送れていない時間）がある |
| `node_proc_util` | 対象のノードのプロセスの CPU 使用率（`/healthz` の `process_cpu_s` の区間の前後の差 ÷ 区間の長さ．コアの数の単位）．前後の `/healthz` に別のプロセスが答えたとき（`pid` が違う．複数のプロセスのノード）は `null` |
| `node_cgroup_util`，`node_workers` | 対象のノードのコンテナの全プロセスの CPU 使用率（`/healthz` の `cgroup_cpu_s` の差 ÷ 区間の長さ．コアの数の単位．Iteration 10）と，ノードのプロセスの数（`experiment.node_workers`） |
| `node_host_busy`，`node_host_softirq`，`node_n_cpu` | 対象のノードのデバイス全体の，全 CPU に対する忙しい時間と softirq の時間の割合（`/healthz` の `host_cpu`）と論理 CPU の数 |
| `generator_cpu_util_per_proc`，`control_cpu_busy` | 負荷生成のプロセスごとの CPU 使用率と，制御点全体の忙しい時間の割合（負荷生成が律速でないことの確認） |
| `n_errors`，`error_kinds` | 区間の内に失敗した要求の数と例外の種類ごとの数（応答のノード名・シャードが対象と違うものも失敗に数える） |
| `score_mismatches`，`cross_level_score_mismatches` | 同じ問の probe の得点が水準の内・水準の間で違った回数（索引は読み取りだけなので 0 のはず） |

`scores.json` は qid → シャードごとの probe の得点（最初に見た値）で，実行の間の照合に使う．

### 150 人での新参者の評価（`atrium newcomer-eval`．p0005 T3）

`results/<run_id>/metrics.json` の `pooled.<方式>` に，新参者の問（正解の受信箱が新参者である test の質問）を合わせた `hit@1`・`hit@2` と，95% ブートストラップ信頼区間を書く．

| 方式 | 内容 |
|---|---|
| `ragroute-upper` | 全受信箱で学習済みの既存のルーター |
| `ragroute-frozen` | 新参者 1 個を除いた 149 受信箱で学習し直したルーター（新参者の one-hot は 0）．新参者ごとに 1 個学習する |
| 学習の要らない方式 | `card_sim`・`term_sketch`・`centroid_sim`・`multi_centroid`・`flood_score`・`random`．EQ2 と同じ計算 |

`judgement` に H-N1（`ragroute-frozen` < `ragroute-upper`，区間が重ならない）と H-N2（`flood_score` と，公開情報を使う最良の方式が `ragroute-frozen` より高い）を書く．
`frozen_on_existing_inboxes` は，`ragroute-frozen` の，新参者ではない受信箱の質問での `hit@2`（参考）．再学習したルーターは制御点のデータディレクトリの `enronqa/newcomer/` に置き，`results/` には書かない．

### E2 の LOSO（`atrium loso`）

`results/e2_loso_<YYYYMMDD_HHMMSS>/metrics.json` の `methods.<方式>` に，除外ごと（`by_holdout.<新参源>`）と，
全ての除外を合わせた値（`pooled`）を書く．選択の指標は上の `selection` と同じ定義（micro 平均）で，
シャード数は実機の実行にしか無いので含めない．

| 指標 | 定義 |
|---|---|
| `newcomer_recall` | 新参源が関連ありの質問のうち，新参源を選んだ割合．`pooled` は全ての除外の（質問，新参源）の組を合わせて計算する（除外ごとの値の平均ではない） |
| `n_newcomer_pairs` | 上の分母 |
| `k`，`k_selection_f1` | 上位 k 個を選ぶ方式で選んだ k と，候補ごとの N−1 源・`train`＋`val` の F1 |
| `criteria` | p0003 S4 の成功条件の判定（MedRAG だけ）．(a) `pooled.mean_selected_sources` ≤ 3.0，(b) `pooled.newcomer_recall` ≥ `ragroute-upper` の 0.8 倍．`chosen` は成功した方式のうち新参源の再現率が最大のもの |

`loso.top_doc_overlap` は，実行の `top_doc_ids` を参照の実行（s3，`routing=all`）と比べ，参照の上位文書のうち
実行にも現れた割合の平均（`mean_overlap`）と，列が完全に一致した割合（`exact_match_rate`）を返す（S5 で使う）．

### EnronQA の参加・離脱（`atrium churn-eval`．p0004 EQ7a）

`results/<run_id>/metrics.json` の `summary` に，（方式，稼働率 `availability`，メンバー表の更新間隔 `refresh`）ごとの値を，
時刻表の種をまたいだ平均・最小・最大で書く．実機のノードは使わず，EQ2 の test のスコアを，各問で質問者に見えているノードの受信箱の中で順位づけし直す．

| 指標 | 定義 |
|---|---|
| `hit` | 正解の受信箱が稼働中で，選ばれた m 個に入る割合．離脱なし（`availability` = 1）の値は EQ2 の `hit@m` と一致する（`regression`） |
| `hit_given_gold_online` | 正解の受信箱が稼働中の問に限った `hit` |
| `gold_online_rate` | 正解の受信箱が稼働中の問の割合 |
| `contacted` | 1 問で問い合わせたノードの数（flood_score は見えているノード全部） |
| `wasted` | 問い合わせたノードのうち，離脱していたノードの数 |
| `hc1` | 事前登録の判定．`availability` = 0.7 で更新間隔 1 → 1000 の `hit` の相対的な低下が，multi_centroid で flood_score より，全ての種で大きければ `supported` |

flood_score は，質問者の実装（1 台でも失敗すると問全体が失敗する）と違い，返事のあった受信箱の中から選ぶと仮定した上限である．

### 二段の方式の参加・離脱（`atrium churn-cascade-eval`．Iteration 15）

EQ7a（上の節）と同じ時刻表・メンバー表・質問の順番で，centroid_probe（K = 75・30）と device_probe（D = 24・12）を評価する．
1 段目（multi_centroid）は見えている受信箱だけを採点して候補を選び（device_probe は得点をデバイスごとに最大値でまとめた上位 D 台），
候補の載るノードへ probe を送り，返事のあった候補の中で 2 段目（flood_score の密ベクトル）の上位 m 個を選ぶ．返事の無かった分は補わない．
対照として multi_centroid・flood_score を EQ7a と同じ規則で並べ，退行の確認用に K = 受信箱の数・D = デバイスの数も流す．
`results/<run_id>/metrics.json` の `runs` は（方式，`availability`，`refresh`，`seed`）ごとに，EQ7a と同じ `hit` などに加えて次の件数を持つ．
`summary` は EQ7a と同じ形の種をまたいだ平均・最小・最大，`losses` は件数の種の和である．

| 指標 | 定義 |
|---|---|
| `contacted`・`wasted` | 二段の方式では probe を送ったノードの数と，そのうち離脱していたノードの数（本照会は数えない） |
| `n_hit` | hit の問の数 |
| `n_gold_offline` | (i) 正解の受信箱のノードが離脱している問 |
| `n_gold_invisible` | (ii) 正解のノードは稼働中だが，メンバー表で見えていない問 |
| `n_not_selected` | (iii) 見えていて稼働中だが選ばれない問．二段の方式では `n_not_candidate`（候補に入らない）と `n_not_top_m`（候補に入ったが上位 m 個に入らない）に分ける（1 段の方式は `null`） |
| `n_slot_taken` | (iv) 1 段の方式（flood_score 以外）で，離脱済みの受信箱が上位 m 個の枠を占めたために外れた問（離脱済みの受信箱を除けば m 位以内）．二段の方式と flood_score は 0 |
| `gates` | G1：`availability` = 1 の `hit` が Iteration 7 の device-probe-eval の test の値と 5e-4 以内．G2：multi_centroid・flood_score の `hit`・`contacted`・`wasted` が EQ7a の `runs` と 1/n_test 以内．G3：K = 受信箱の数・D = デバイスの数が flood_score と `n_hit`・`contacted`・`wasted` で完全に一致．G4：`n_hit` と (i)〜(iv) の和が問の数に等しい．1 つでも通らなければ `hc2`・`e1` は判定しない |
| `hc2` | 事前登録の判定．`availability` = 0.7 で更新間隔 1 → 1000 の `hit` の相対的な低下が，device_probe（D = 12）で multi_centroid より全ての種で小さければ `supported` |
| `hc3` | 記録．全方式の相対的な低下と flood_score との差（churn-eval の `hc1` と同じ計算） |
| `e1` | flood_score の `availability` = 0.7 で，更新間隔 1 → 1000 の hit の減少の件数のうち (ii) の増加の件数の割合．時刻表は更新間隔に依らないので，正解が稼働中の問に限った hit の低下に占める割合に等しい．全ての種で 0.8 以上なら `confirmed`（減少が 0 以下の種は不成立）．種の和の比 `pooled_share` は記録だけ |

二段の方式の値は，実機の質問者が probe の失敗を許して返事のあった分で続けると仮定した値である．今の `experiment.churn` は二段の方式に使えない（[d0002](d0002_configuration.md)）．

### 参加の通知の遅れ L ごとの評価（`atrium churn-join-eval`．Iteration 16）

Iteration 15（上の節）と同じ採点・時刻表・質問の順番・構成で，メンバー表に「参加の通知」を足す．
直前の更新 u(t) = ⌊t/R⌋·R より後の刻み j（u(t) < j ≤ t − L）に参加した（`online[j]` かつ `¬online[j−1]`）ノードを表に足す．
離脱は通知せず，更新のときにだけ表から消える．通知の遅れ L ∈ {0, 1, 10, 100} と通知なし（`delay` = `null`．EQ7a・Iteration 15 と同じ表）を流す．
L ≥ R では次の更新の方が先に来るので，通知は効かない．
`runs` は Iteration 15 の項目に `delay` を足したもので，`summary`・`losses` は（方式，`availability`，`refresh`，`delay`）ごとにまとめる．

| 指標 | 定義 |
|---|---|
| `gates` | G1：`delay` = `null` の全ての実行が Iteration 15 の `runs` と 1/n_test 以内（件数は n_test で割った差）で一致し，どちらにも欠けが無い．G2：L ≥ R の実行が同じ（方式，a，R，種）の通知なしと全ての項目で完全に一致．G3：flood_score の L = 0 の `n_hit` が同じ（a，種）の R = 1 と全ての R で完全に一致．G4：Iteration 15 と同じ．1 つでも通らなければ `hj1` は判定しない |
| `recovery` | `availability` = 0.7 で，方式・L ごとの回復の割合 ρ = (hit(R=1000, L) − hit(R=1000, 通知なし)) / (hit(R=1) − hit(R=1000, 通知なし))（種ごと．分母が 0 以下の種は `null`）と，R = 1000 の `hit`（種ごと）・`contacted`・`wasted`（種の平均） |
| `hj1` | 事前登録の判定．device_probe（D = 12）の L = 100 の ρ が全ての種で 0.5 以上なら `supported` |
| `e2` | 記録．device_probe（D = 12）の `availability` = 0.7 で，R = 1 → 1000・L = 0 にしても残る hit の低下（問）と，(i)〜(iv)・候補外・上位 m 外の件数の増分（種ごと） |
| `transitions` | 記録．`availability` = 0.7 の（方式，L，種）ごとに，R = 1 の区分（`loss_classes` の順：hit・(i)〜(iv)）から R = 1000 の区分への 5 × 5 の件数 |

1 刻みは 1 問である．L を秒へ換算するには処理量の仮定が要る．

### 離脱の通知の遅れ L_leave ごとの評価（`atrium churn-leave-eval`．Iteration 17）

Iteration 16（上の節）と同じ採点・時刻表・質問の順番・構成で，メンバー表に「離脱の通知」も足す．
直前の更新 u(t) = ⌊t/R⌋·R の時点の表に，区間 (u(t), t] の事象のうち届いたもの（参加は j ≤ t − L_join，離脱（`¬online[j]` かつ `online[j−1]`）は j ≤ t − L_leave）を重ね，最も新しい事象に従う（参加なら表に入れ，離脱なら消す）．
届いた事象が無いノードは u(t) の時点の状態のままである．離脱の通知の後に再び参加したノードは，参加の通知が届いた時点から見える．
L_join ∈ {0, 100} × L_leave ∈ {0, 10, 100} と離脱の通知なし（`leave_delay` = `null`．Iteration 16 の同じ L の表）を流す．L_join = 0 が判定の条件で，L_join = 100 は記録だけの感度の格子である．
L_leave ≥ R では次の更新の方が先に来るので，離脱の通知は効かない．(L_join, L_leave) = (0, 0) では表が稼働中の集合そのものになる．
`runs` は Iteration 15 の項目に `join_delay`・`leave_delay` を足したもので，`summary`・`losses` は（方式，`availability`，`refresh`，`join_delay`，`leave_delay`）ごとにまとめる．

| 指標 | 定義 |
|---|---|
| `gates` | G1：L_join = 0・`leave_delay` = `null` の全ての実行が Iteration 16 の `delay` = 0 の `runs` と 1/n_test 以内（churn-join-eval の G1 と同じ照合）で一致し，どちらにも欠けが無い．G2：L_join = 100 で同じ照合．G3：L_leave ≥ R の実行が同じ（方式，a，R，種，L_join）の離脱の通知なしと全ての項目で完全に一致．G4：Iteration 15 と同じ．G5：(0, 0) の実行が同じ（方式，a，種）の R = 1 と `n_hit` と全ての件数で完全に一致．1 つでも通らなければ `hl1` は判定しない |
| `recovery` | `availability` = 0.7 で，方式・L_join・L_leave ごとの ρ_leave = (hit(R=1000, L_join, L_leave) − hit(R=1000, L_join, 通知なし)) / (hit(R=1) − hit(R=1000, L_join, 通知なし))（種ごと．分母が 0 以下の種は `null`）と，R = 1000 の `hit`・`n_gold_invisible`（種ごと）・`contacted`・`wasted`（種の平均）．`recovery_other_availabilities` は device_probe（D = 12）の a = 0.9・0.5 の同じ値 |
| `hl1` | 事前登録の判定．device_probe（D = 12）の L_join = 0・L_leave = 10 の ρ_leave が全ての種で 0.75 以上なら `supported` |
| `e2` | 記録．device_probe（D = 12）の `availability` = 0.7・L_join = 0 で，R = 1 → 1000・L_leave = 10 にしても残る hit の低下（問）と，(i)〜(iv)・候補外・上位 m 外の件数の増分（種ごと） |
| `transitions` | 記録．`availability` = 0.7・L_join = 0 の（方式，L_leave，種）ごとに，R = 1 の区分から R = 1000 の区分への 5 × 5 の件数 |

### 返事の無い probe の枠を補う変種の評価（`atrium churn-refill-eval`．Iteration 18）

Iteration 16 と同じ採点・時刻表・質問の順番・構成・メンバー表（`join_notified_view`，L_join ∈ {∞, 0, 100}．離脱は通知しない）で，二段の方式の probe の補い方 `refill` を変える．
見えている単位（centroid_probe は受信箱，device_probe はデバイス）を 1 段目の得点で並べ，先頭 S 個（S は K または D）に probe を送る．返事の無いことは送った時点で分かるとする．
none は補わない（Iteration 15・16 と同じ）．once は 1 往復目で返事の無かった f 個の枠を見えている順位の S + 1〜S + f 番目で 1 回だけ補う（最大 2 往復）．until_full は返事のある単位が S 個に揃うか見えている単位が尽きるまで補う．
2 段目の上位 m 個の選び方と hit・(i)〜(iv) の分け方は変えない．`contacted` は全ての往復の probe の先のノードの和集合，`wasted` はそのうち離脱していたノードの数である．
1 段だけの方式と全数の構成には補う相手が無いので，none と同じ値になる．記録だけの対照 `device_probe_d15`（none のみ）を足す．
`runs` は Iteration 15 の項目に `join_delay`・`refill`・`n_contacted` と往復の情報（`second_round_rate`：2 往復目の要る問の割合，`rounds_mean`・`rounds_max`：1 問あたりの往復の数，`probe_requests`・`n_probe_requests`：1 問あたりの probe の要求の数とその和．往復ごとに新たに候補に入った単位の載るノードの数の和．1 段だけの方式は `null`）を足したもので，`summary`・`losses` は（方式，`availability`，`refresh`，`join_delay`，`refill`）ごとにまとめる．

| 指標 | 定義 |
|---|---|
| `gates` | G1：none の全ての実行（対照を除く）が Iteration 16 の同じ `delay` の `runs` と 1/n_test 以内（churn-join-eval の G1 と同じ照合）で一致し，どちらにも欠けが無い．G2：補う相手の無い構成の none 以外が同じ条件の none と全ての項目で完全に一致．G3：a = 1.0 または R = 1 の none 以外（対照を除く）が同じ条件の none と完全に一致．G4：Iteration 15 と同じ．G5：L_join = 0・until_full の二段の 4 構成が同じ（構成，a，種）の R = 1 と全ての件数で完全に一致．G6：二段の構成の全ての問で返事のある候補が none ⊆ once ⊆ until_full．1 つでも通らなければ `hp1` は判定しない |
| `recovery` | `availability` = 0.7・R = 1000 で，二段の 4 構成・L_join ごとの ρ_refill = (hit(L_join, refill) − hit(L_join, none)) / (hit(R=1) − hit(L_join, none))（種ごと．hit(R=1) は none・L_join = ∞ の R = 1．分母が 0 以下の種は `null`）と，補い方ごとの `hit`（種ごと）・`contacted`・`wasted`・`probe_requests`・`second_round_rate`・`rounds_mean`（種の平均）・`rounds_max`（種の最大）．`recovery_other_availabilities` は device_probe（D = 12）の a = 0.9・0.5 の同じ値 |
| `hp1` | 事前登録の判定．device_probe（D = 12）の L_join = 100・once の ρ_refill が全ての種で 0.20 以上なら `supported` |
| `control` | 記録．同じ条件で device_probe（D = 12）の once と `device_probe_d15` の none の hit の差・`contacted`・`wasted`・`probe_requests` と，none に対する回復した問の数 / 増えた問い合わせの数（種ごと） |
| `e2` | 記録．device_probe（D = 12）の同じ条件の once で残る hit の低下と，R = 1 および同じ条件の none に対する (i)〜(iv)・候補外・上位 m 外の件数の増分（種ごと） |
| `transitions` | 記録．`availability` = 0.7・L_join = 100 の（二段の 4 構成，`refill`，種）ごとに，R = 1（none・L_join = ∞）の区分から R = 1000 の区分への 5 × 5 の件数 |

1 刻みは 1 問である．2 往復目の待ち時間はオフラインでは測らない．

### device_probe の D ごとに補う変種と補わない構成を同じ問い合わせの数で比べる評価（`atrium churn-budget-eval`．Iteration 19）

Iteration 18 と同じ採点・時刻表・質問の順番・メンバー表（`join_notified_view`，L_join ∈ {∞, 0, 100}．離脱は通知しない）で，device_probe の D ∈ {9, …, 18} の全てに `refill` ∈ {none, once, until_full} を流す（構成の名前は `device_probe_d{D}`）．
補う規則・2 段目・hit と (i)〜(iv)・`contacted`・`wasted`・往復の情報は churn-refill-eval の `route_cascade_with_refill` をそのまま使う．
同じ（`availability`，`refresh`，`join_delay`，種）の none の D ごとの点（`contacted`，`n_hit`）を `contacted` の昇順に並べた折れ線 Î（`contacted` の等しい点は `n_hit` の最大値）を作り，補う変種の点（c，n）を同じ問い合わせの数で比べる．
`runs` は churn-refill-eval の項目に `budget`・`contacted_max`（問ごとの contacted の最大）・`contacted_p95`（問ごとの contacted の 95 パーセンタイル．`numpy.percentile` の既定の線形補間）を足したもの．`summary`・`losses` は churn-refill-eval と同じまとめ方である．

| 指標 | 定義 |
|---|---|
| `gates` | G1：D = 12 の全ての補い方と D = 15 の none（960 件）が Iteration 18 の `runs` の同じキー（`method`，`availability`，`refresh`，`join_delay`，`refill`，`seed`）の実行と churn-refill-eval の `EXACT_FIELDS` で完全に一致し，両側に欠けが無く，`m`・`n_test` も一致．G2：a = 1.0 または R = 1 の once・until_full が同じ D・条件の none と `EXACT_FIELDS` で完全に一致．G3：全ての実行で hit と (i)〜(iv) の件数の和が問の数に等しい．G4：同じ条件の none で `n_contacted` が D について減らず，返事のある候補が隣り合う D の間で入れ子．G5：全ての D の全ての問で返事のある候補が none ⊆ once ⊆ until_full．G6：L_join = 0・until_full が同じ（D，a，種）の R = 1（none・L_join = ∞）と COUNT_FIELDS で完全に一致．1 つでも通らなければ `hb` は判定しない |
| `comparisons` | once・until_full の全ての実行の `interpolated_n_hit` = Î(c)（c が none の範囲の外なら `null`．外挿しない），`delta` = n − Î(c)，`saved_contacts` = Î⁻¹(n) − c（解がただ 1 つのときだけ．他は `null`），`diff_vs_d15_none`（同じ条件の `device_probe_d15` の none の `n_hit` との差） |
| `hb` | 事前登録の判定．D = 12・once・`availability` = 0.7・R = 1000・L_join = 100 の種ごとの Δ で，H-B1（全ての種で Δ > 0 かつ平均 ≥ 15），H-B3（全ての種で Δ < 0 かつ平均 ≤ −15），H-B2（全ての種で \|Δ\| ≤ 25）の順に最初に成り立つもの．None の種があるか，どれも成り立たなければ `undetermined` |
| `none_curves` | 記録．a = 0.7・R = 1000 の L_join ∈ {100, ∞, 0} と，a ∈ {0.9, 0.5}・R = 1000・L_join = 100 の none の D ごとの `n_hit`・`contacted`（種ごと）と `contacted`・`wasted` の種の平均 |
| `variants` | 記録．`none_curves` と同じ条件の once・until_full の D ごとの Δ・s・d15 との差（種ごと），`second_round_rate`・`rounds_mean`・`contacted_p95`（種の平均），`rounds_max`・`contacted_max`（種の最大） |

1 刻みは 1 問である．2 往復目の待ち時間はオフラインでは測らない．

### 中央集約ベースライン（`atrium central-eval`．p0006 P1）

制御点のコンテナで流す（`scripts/remote/central_eval.sh <out_name> <c1_run> [<name>=<run> ...]`）．実機のノードは使わない．
`central_dense`・`central_bm25` は，全メールを 1 つの索引にした場合（密ベクトルの内積と，全体で IDF と平均長を共有した BM25）の Recall@1・Recall@K（K = `data.enronqa.k_context`）と 95% 区間．
`c1_equivalence` は，C1（`routing: all`・`snippet_return`）の `top_doc_ids` と 1 索引の上位 K 通の一致で，`set_agreement`（集合として）・`ordered_agreement`（順序も）・`tie_aware_agreement`（同点の重複メールの入れ替えを許す）・欠け・失敗の数と，閾値 0.999 での判定を持つ．
`runs` は実行ごとの Recall@K と，1 問あたりの質問を受け取ったデバイスの数・外へ出た文書の数・問い合わせたシャードの数．`central_one_time_docs` は中央に集める場合に一度きり外へ出る文書の数．

### 露出（`atrium exposure-eval`．p0007 B）

制御点のコンテナで流す（`scripts/remote/exposure_eval.sh <out_name> <p2p,...> <name>=<run>[:<1 問あたりの値>] ...`）．実機のノードは使わない．
「外に出た文書」は，持ち主のデバイスが受信箱のメール本文をそのデバイス以外へ渡した文書である．B1 は選ばれた各受信箱が返した断片（統合で落ちたものを含む）が質問者へ，B4 は GPU の無いノードが委託先の GPU へ渡した文脈が出る（GPU のあるノードが自分で答えれば 0）．
`results.jsonl` の `docs_exposed_by_source` は数だけで ID を持たない（B1 の `top_doc_ids` は統合後の 5 通）ので，受信箱ごとの密ベクトルの内積の上位 5 通を埋め込みから再計算して ID を復元する．
出力は次の 4 つ．

- `exposure.json`：条件ごとに，重複なしの文書の数・全メールに対する割合（`distinct_docs`・`fraction`），受け手別の数（`distinct_docs_by_receiver`），1 問あたりの平均（外に出た文書・質問を受け取ったデバイス・probe の受け手・問い合わせた受信箱・委託先 GPU），公開情報の大きさ（`advert_bytes`）と持ち主の特定率（`owner_attribution`），下限・上限（`bounds`），再構成の妥当性（`validation`）．中央に集める場合は `central`（最初に 100%）．
- `verdict.json`：H-B（P2P の全ての条件で割合が 0.5 未満）の `verdict_original`．再構成の妥当性（件数の一致と，記録の `top_doc_ids` の包含 ≥ 0.999）が崩れた条件があれば，下限・上限で判定する．事後に基準を見直した場合だけ `verdict_revised` を出す（見直さなければ null）．
- `sim_vs_live.json`：再構成した 1 問あたりの文書の数と，実機の記録・journal の値の照合．
- `analysis_report.md`：上の表．

### 汎用のルーティング方式のフロンティア（`atrium frontier-eval`・`atrium frontier-verdict`．p0006 P2・P3）

`frontier-eval` は制御点のコンテナで流す（`scripts/remote/frontier_eval.sh <out_name> <placement_run|-> [methods] [--dataset ...]`）．
方式は `src/atrium/frontier_methods.py` の `BUILDERS` に足すだけで加わる（CLI は増やさない）．出力は次の 3 つ．

- `frontier.json`：方式×設定×split（dev・test）×subset（`all`・`no_owner_name`）ごとに 1 行．`requests_per_query`（平均・p95・最大．probe したデバイス + 方式固有の追加 + 本照会の m），`round_trips`（平均・最大），`advert_bytes`（float32 で数えた公開情報），`hit@1`・`hit@m`・`source_recall@m`（test は 95% 区間），`attribution_top1`・`attribution_auc`（公開情報から正解でないメールの出どころのデバイスを当てる．test のみ），`family`・`method`・`params`．
- `metrics.json`：EnronQA は R4（既存の点の再現），dev の hit@m が目標以上で平均の要求数が最小の設定（族ごと．test では選ばない），H-P2a・H-P2b・H-P2c の集計，test のフロンティア．FeB4RAG・MedRAG は族ごとの「目標に届く最小の要求数」と面積，全員に聞く方式の再現率（dev・test）．
- `analysis_report.md`：上の表．

hit は既存の評価と同じ規則（正解より得点が高いか同点のデータ源の数を順位とする．同点は不利な側）で数える．複数ラベルのデータ（FeB4RAG・MedRAG）の主の指標は，選んだ m 個に入る関連データ源の割合 `source_recall@m` である．
`frontier-verdict` は複数のデータの `frontier.json` から，p0006 §7.3 の汎用の判定（3 データのうち 2 つ以上で優れ，残りで逆転の差が区間の外にない）を `X:Y` の組ごとに出す（操作端末）．

### term_probe・hybrid_probe の要約とオフラインの選択（`atrium build-summary`．p0006 P4）

制御点のコンテナで流す（`scripts/remote/build_summary.sh <out_name> <placement_run>`）．配置ごとの要約（語の統計の CORI 重みと本文の重心）を `<data_dir>/enronqa/summary/` に書き（既存の成果物とは別の新しいサブディレクトリ），
`results/<out_name>/offline_selection.json` に，test の質問ごとの「選ばれるデータ源」（`frontier-eval` と同じ関数で求めたもの）を書く．
実機の `retrieval_only` の `selected_sources` との一致率（集合として．閾値 0.999）で，質問者の選択がオフラインの評価と同じかを確かめる．

### EnronQA の参加・離脱の実機の再生（`atrium churn-live-report`．p0004 EQ7b）

`experiment.churn` を指定した実行（[d0002](d0002_configuration.md)）の `results.jsonl` は，問ごとに `churn: {step, up, n_visible}`
（刻み，その刻みの各ノードの稼働状態を並べた 0/1 の文字列，メンバー表に載っているノードの数）と，`contacted_nodes`・`failed_nodes`
（本照会で問い合わせたノードと返事の無かったノード．`flood_score` は `probe_recipients`・`probe_failed`）を持つ．
`atrium churn-live-report --runs <run_dir>... --offline-metrics <EQ7a の metrics.json> --out-dir <dir>` が，上の表と同じ定義で
`hit` などを数え（失敗した問は hit = 0，分母は全問），EQ7a の同じ条件の値と比べて `metrics.json` の `equivalence`・`regression`・`hc1` と
`analysis_report.md` に書く．`equivalence.equivalent` は，全ての実行で |実機の hit − EQ7a の hit| が 2 問分（2 / n_test）以内かを示す（H-C2）．

## 4. E0 の実測値

| ファイル | 内容 |
|---|---|
| `dimm.txt` | 装着されたメモリ（スロット・容量・速度）．`sudo dmidecode -t memory` から抽出 |
| `host.json`，`host.raw` | メモリの合計，`/` の空き，ディスクの一覧 |
| `llama-bench-<model>.json` | llama-bench の JSON 出力（pp512・pp4096・tg128，4 スレッド） |
| `faiss.json` | fp16 平坦索引（専門家と同じ `IndexScalarQuantizer`）で 1 クエリずつ上位 50 件を検索する時間．ベクトルは乱数 |
| `medcpt.json` | MedCPT のクエリ側モデルで 1 件を埋め込む時間（トークン数 16〜128．所要時間は内容ではなく長さで決まるため，入力は長さだけをそろえた文字列） |
| `../net/<a>_<b>.json` | ping 20 回の RTT の平均と，iperf3（10 秒）のスループット |
