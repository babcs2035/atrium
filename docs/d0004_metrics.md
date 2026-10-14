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
