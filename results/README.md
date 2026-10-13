# results の索引

`results/` の各ディレクトリが，どの実験のどの条件の実行で，判定と考察が journal（[.claude/research/journal.md](../.claude/research/journal.md)）のどの節にあるかを引くための一覧である．
数値と考察は journal の節にあり，ここには重複して書かない（主な値は，節を開かずに見分けるための目印である）．
ファイルの書式と指標の定義は [docs/d0004_metrics.md](../docs/d0004_metrics.md) にある．実行を足したら，この表にも 1 行を足す．

- 条件の略記：データセット・`routing`・`answer_mode`・`merge`．B1〜B4 は p0004 の RQ-B の回答方式（B1 断片返却型，B2 宿る型，B2' は B2 の専門家のモデルを qwen3:1.7b にしたもの，B3 は質問者が小型のモデルで答える，B4 は GPU の専門家へ委託）．
- 「journal」の列は節の見出しの先頭である．

## 準備段階（2026-10-05〜06．p0001）

| 実行 | 内容 | 主な値 | journal |
|---|---|---|---|
| `20261006_052434` | E0：専門家の機種の実測（メモリ・llama-bench・FAISS・MedCPT・ネットワーク） | `e0_summary.md` | 準備段階の記録 › E0 |
| `20261006_043855`，`20261006_044022` | FeB4RAG all・ragroute，retrieval_only，qrels_oracle，790 問（ルーターの作り直しの前） | ragroute 9.19 エンジン／問 | 準備段階の記録 › 実機での検証 |
| `20261006_050758` | FeB4RAG ragroute，local_answer，5 問（宿る型の動作確認） | 失敗 1 | 同上 |
| `20261006_094749` | MedRAG all，retrieval_only，score，100 問 | ラベル一致 1.000 | 同上 |
| `20261006_100032` | MedRAG ragroute，snippet_return，25 問 | 正答率 0.72 | 同上 |
| `20261006_214950` | FeB4RAG ragroute，40 問（ルーターの作り直しの後） | 失敗 0 | 同上 |
| `20261006_205154`，`20261006_224015`，`20261006_230206`，`20261007_005901`，`20261007_010910` | MedRAG all，retrieval_only，score，25〜50 問．スクリプト・配置先・設定の書式を直すたびに流した動作確認 | 全て失敗 0，ラベル一致 1.000 | 同上 |

## E1：RAGRoute の再現（2026-10-07〜13．p0003 S1・S2）

| 実行 | 条件 | 主な値（test） | journal |
|---|---|---|---|
| `20261007_s1_feb_all` | FeB4RAG all，retrieval_only，qrels_oracle | 再現率 1.000 | E1 の判定 |
| `20261007_s2_feb_ragroute` | FeB4RAG ragroute | 9.44 エンジン／問 | 同上 |
| `20261007_s3_med_all_ret` | MedRAG all，retrieval_only，score | ラベル一致 0.999 | 同上 |
| `20261007_s4_med_all_snip` | MedRAG all，snippet_return，score | 正答率 0.659（全問） | 同上 |
| `20261007_s5_med_ragroute` | MedRAG ragroute，snippet_return | 0.657，削減率 58.7% | 同上 |
| `20261007_s6_med_random` | MedRAG random（3 源），snippet_return | 0.654 | 同上 |
| `20261007_s7_med_none` | MedRAG none（RAG なし） | 0.640 | 同上 |
| `20261009_s8_smoke` | MedRAG all，snippet_return，cross_encoder，10 問 | 失敗 0 | E1 の判定 › s8 について |
| `20261007_s8_med_all_ce` | MedRAG all，snippet_return，cross_encoder，7,663 問 | 0.689（s4 より高い．p = 1.2e-8） | s8：MedRAG の cross_encoder |
| `20261007_e1_extra` | E1 の全問実行のスクリプト（`run_full.sh`・`run_s8.sh`）と再集計（`e1_extra.py`） | － | E1 の判定 › 再集計で分かったこと |

## p0004 の退行の確認（2026-10-09）

| 実行 | 条件 | 主な値 | journal |
|---|---|---|---|
| `20261009_r1_medrag_regression` | MedRAG all，retrieval_only，10 問 | ラベル一致 1.000 | p0004 の実装と退行の確認 |
| `20261009_r2_feb4rag_regression` | FeB4RAG ragroute，2 問 | 失敗 0 | 同上 |

## E2：後から加わったデータ源（2026-10-11〜12．p0003 S3〜S5）

| 実行 | 内容 | 主な値 | journal |
|---|---|---|---|
| `e2_loso_20261011_120539` | MedRAG・FeB4RAG の leave-one-source-out（実機を使わない） | desc_sim の新参源の再現率 0.830（上限の 1.04 倍） | E2：LOSO の判定 |
| `20261012_e2_desc_sim` | MedRAG card_sim（desc_sim，3 源），retrieval_only，7,663 問 | F1 0.634，失敗 0 | E2 の下流確認 |

## E3：MedRAG の宿る型のパイロット（2026-10-12．p0003 S6）

| 実行 | 条件 | 主な値 | journal |
|---|---|---|---|
| `20261012_e3_smoke` | MedRAG all，local_answer，score，10 問（`llm.timeout_s` 600） | 失敗 2（打ち切り） | S6：MedRAG の宿る型のパイロット |
| `20261012b_e3_smoke` | 同上（`llm.timeout_s` 1800） | 失敗 0 | 同上 |
| `20261012b_e3_pilot` | 同上，100 問 | 失敗 64（専門家のメモリ不足）．不成功として閉じた（backlog B6） | 同上 |

## EnronQA の拡張（2026-10-09〜．p0004）

| 実行 | 内容 | 主な値 | journal |
|---|---|---|---|
| `20261009_eq0_data_check` | EQ0：データ検証（V1〜V5）とゲート G0 | G0 通過 | p0004 の着手前の確認 › EnronQA のデータ検証 |
| `20261009_eq1_oracle_{retrieval,b1,b2}` | EQ1：実機の健全性．oracle，150 問 | 失敗 0 | EQ1：実機の健全性 |
| `20261009_eq2_advert_eval` | EQ2：RQ-A のオフラインの評価（実機を使わない） | hit@1：flood_score 0.722，multi_centroid 0.349 | EQ2：RQ-A のオフラインの評価 |
| `20261009_eq3_{term_sketch,multi_centroid,flood_score}` | EQ3：RQ-A の実機．retrieval_only，2,940 問 | － | EQ3〜EQ5 の事前登録，EQ5：RQ-B の判定 |
| `20261009_eq4_judge_validate` | EQ4 の検証 1：判定モデルの別解・誤答の判定 | 合格 | EQ5：RQ-B の判定 |
| `20261010_eq4_judge_kappa` | EQ4 の検証 2：参照モデル（qwen3:14b）との一致 | κ = 0.828 | EQ4 の検証 2 |
| `20261009_eq5_{b1,b1_flood,b2,b2_1p7b,b3,b4}` | EQ5：RQ-B．oracle（b1_flood は flood_score），2,940 問 | B1 0.777，B2 0.451，B2' 0.684，B3 0.258，B4 0.775 | EQ5：RQ-B の判定 |
| `20261010_eq5_summary` | EQ5 の集計（`eq5_rqb.py`）と H-B1 の診断（`hb1_diag.py`） | － | EQ5：RQ-B の判定 |
| `20261010_eq6_{b1,b1_flood,b2,b2_1p7b,b3,b4}_r{1,2,3}` | EQ6：待ち時間．300 問 × 3 回 | － | EQ6：待ち時間 |
| `20261010_eq6_invalid_warmcache` | EQ6 の B2 の 2・3 回目のうち，KV キャッシュに当たって無効にした実行 | 集計から除外 | EQ6：待ち時間，EQ6 の事前登録 |
| `20261010_eq6_summary` | EQ6 の集計（`eq6_breakdown.py`・`eq6_b2_vs_b2p.py`・`eq6_r1r2.py`） | － | EQ6：待ち時間 |
| `20261011_eq8_{b1,b2,b2_1p7b}_k{1,3,5}` | EQ8：文脈の通数 k．oracle，2,940 問 | B2' は k = 3 と 5 で同じ 0.685 | EQ8〜EQ10 |
| `20261011_eq9_b3_k5` | EQ9：B3 の流し直し（EQ5 の B3 との揺れ） | 0.256（EQ5 は 0.258） | EQ8〜EQ10 |
| `20261011_eq10_{b1_p1,b4_p1,b2_k1,b2_k3,b2_1p7b_k1,b2_1p7b_k3}_r{1,2,3}` | EQ10：待ち時間（parallel 1）．300 問 × 3 回 | － | EQ8〜EQ10 |
| `20261011_eq8_summary` | EQ8〜EQ10 の連続実行（`eq8_chain.sh`）と集計（`eq8_summary.py`・`eq10_summary.py`） | － | EQ8〜EQ10 |
| `20261012_eq7a_churn` | EQ7a：RQ-C のオフラインの評価（参加・離脱とメンバー表の古さ） | H-C1 支持（差は小さい） | EQ7a：RQ-C のオフラインの評価 |
| `20261013_eq7b_summary` | EQ7b の 22 回の条件（`eq7b_specs.txt`）と集計（`gate/` は退行ゲートの 2 回） | － | EQ7b の事前登録，EQ7b：実機の参加・離脱の再生 |
| `20261013_eq7b_{multi_centroid,flood_score}_a100_r1_s0` | EQ7b の退行ゲート：稼働率 1，2,940 問 | hit@2 0.4401・0.8041（EQ7a と一致） | EQ7b：実機の参加・離脱の再生 |
