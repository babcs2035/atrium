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
| `20261014_t4_{flood_score,multi_centroid}_p{1,4,8,16,30}_r{1,2,3}` | p0005 T4：負荷試験．EnronQA 300 問，retrieval_only，30 実行（失敗 0）．`20261014_t4_load_summary` に条件・連続実行・集計 | H-L1・H-L2 支持，H-L3 支持されない．flood_score の処理量は parallel とともに減る（12.8 → 2.5 問/秒） | T4：負荷試験 |
| `20261014_t3_newcomer` | p0005 T3：150 人での新参者の評価（30 個の新参者．再学習した素朴な中央方式との比較） | frozen 0.118 対 upper 0.282，flood_score 0.802，multi_centroid 0.442（hit@2） | T3：150 人での新参者の評価 |
| `20261014_t2_owner_eval` | p0005 T2：持ち主推定の入力（質問文・メール本文・短く切ったメール本文）を揃えた評価（`ts_diag.out` は term_sketch の採点の揺れの診断） | 埋め込みの方式は同じ長さなら持ち主の特定が質問文からの発見より低い．term_sketch は高い | T2：持ち主推定の定義の統一 |
| `20261014_t1_judge_audit` | p0005 T1：判定の補正（狭い補正）の集計．EnronQA の採点済み全 16 実行（`judge_audit.py`・`judge_audit.out`） | B2 0.297 → 0.261，B3 0.260 → 0.215（CPU の受信箱） | 判定の補正の確定と EQ8 の読み方の訂正 |
| `20261011_eq8_summary` | EQ8〜EQ10 の連続実行（`eq8_chain.sh`）と集計（`eq8_summary.py`・`eq10_summary.py`） | － | EQ8〜EQ10 |
| `20261012_eq7a_churn` | EQ7a：RQ-C のオフラインの評価（参加・離脱とメンバー表の古さ） | H-C1 支持（差は小さい） | EQ7a：RQ-C のオフラインの評価 |
| `20261013_eq7b_summary` | EQ7b の 22 回の条件（`eq7b_specs.txt`）と集計（`final/` が全 22 回，`gate/` は退行ゲートの 2 回） | H-C1 支持，EQ7a と全 22 回で一致 | EQ7b の事前登録，EQ7b：実機の参加・離脱の再生 |
| `20261013_eq7b_{multi_centroid,flood_score}_a{100,70}_r{1,1000}_s{0..4}` | EQ7b：稼働率 1（退行ゲート 2 回）と 0.7（20 回），2,940 問 | hit@2 は EQ7a と一致．H-C1 支持 | EQ7b：実機の参加・離脱の再生 |

## p0006（2026-10-16〜．汎用のルーティングと中央集約ベースライン）

| 実行 | 内容 | 主な値 | journal |
|---|---|---|---|
| `p0006_r1_med_all_ret`・`p0006_r2_feb_ragroute`・`p0006_r3_advert_eval` | P0：退行ゲート R1（MedRAG の `top_doc_ids`）・R2（FeB4RAG の `selected_sources`）・R3（`advert-eval`） | 全て既存の値と一致 | p0006 P0 |
| `p0006_c0_gold_context` | P1：C0．正解メールだけを文脈にする（評価専用）．2,940 問 | 0.8816 | p0006 P1（結果と判定） |
| `p0006_c1_all_pernode` | P1：C1．`routing: all`・`snippet_return`・`http_pool: per_node`．2,940 問（`shared` の最初の実行は PoolTimeout で止めた．制御点にだけ残る） | 0.6752 | 同上 |
| `p0006_p1_central_eval`・`p0006_p1_central_eval_v2` | P1：`central-eval`．v2 は同点を許す一致を足した再実行 | C1 の一致 0.9942（厳密）・1.0000（同点を許す）．中央 BM25 の Recall@5 0.903 | 同上 |
| `p0006_p2_r4_smoke` | P2：`frontier-eval` の R4（既存の点の再現） | 5 点とも一致 | p0006 P2（事前登録） |
| `p0006_p2_frontier` | P2 反復 1：全方式（n30・n150） | H-P2a 不成立．term_stats が最良 | p0006 P2：反復 1 の結果と判定 |
| `p0006_p2_iter2` | P2 反復 2：term_stats_large・hybrid_summary | hybrid 0.8017（18 要求），14 要求で 0.7895 | p0006 P2：反復 2 の結果と P2 のまとめ |
| `p0006_p3_feb4rag`・`p0006_p3_medrag` | P3：FeB4RAG（得点の正規化 none・zscore・rank）と MedRAG（16 シャード） | 本文の重心が FeB4RAG で，語の統計が MedRAG で最少の要求数 | p0006 P3：結果と判定 |
| `p0006_p3_verdict` | P3：`frontier-verdict`．§7.3 の汎用の判定 4 組 | 汎用に優れる組は無い | 同上 |
| `p0006_p4_build_summary` | P4：`build-summary` の要約の記録とオフラインの選択 | 語彙 198,492・30 デバイス | p0006 P4（事前登録） |
| `p0006_p4_{term,hybrid}_probe_full` | P4：オフラインとの一致．`retrieval_only`・2,940 問・parallel 30 | 一致 2,940/2,940（両方） | p0006 P4：オフラインとの一致と待ち時間の結果 |
| `p0006_p4_{term,hybrid}_probe_p{1,30}_r{1,2,3}`・`p0006_p4_flood_score_p{1,30}_r{1,2,3}` | P4：待ち時間．300 問 × 3 回 | parallel 30 の e2e p50：flood 2.2 s，term 1.9 s，hybrid 1.4 s | 同上 |
| `p0006_p4_{term,hybrid}_probe_{b1,b4}` | P4：回答まで．B1・B4，2,940 問 | B1 0.657・0.655（flood_score と差なし），B4 0.626・0.631 | p0006 P4：回答まで の結果と判定 |

- EnronQA の実行の `results.jsonl` の回答は，`scripts/tasks/sanitize_results.py` で，メール本文の引用を除いてから commit する（backlog B57）．


## p0007（2026-10-19〜．分散 P2P 型と中央集約型の多基準の比較）

| 実行 | 内容 | 主な値 | journal |
|---|---|---|---|
| `20261019_155617_p0007_b_exposure` | B：`exposure-eval`．2,940 問で外に出た文書の重複なしの数（C1・C3・P4-1・P4-3・C2）と H-B の判定 | P4-1 27.3%・P4-3 17.9%（73,772 通に対して．再構成した値）．H-B 成立 | p0007 B：露出 |
| `p0007_a1_dev_m{2,3}_{max_score,abstain,agree,llm_select}` | A1 の dev（EnronQA dev の受信箱ごとの先頭 2 問 = 300 問）．hybrid_probe D = 16・owner_answer・m × 統合の規則の 8 条件 | 正答率 0.537〜0.737（`m3_abstain` が最大で選ばれた） | p0007 A1：精度の結果と判定 |
| `p0007_a1_dev_ref_{flood_score,oracle}_m3_abstain` | A1 の参照（選択に使わない）．同じ 300 問・m = 3・abstain で Router を替えた | flood_score 0.740・oracle 0.807 | 同上 |
| `p0007_a1_test`，`p0007_a1_test_verdict_c1`，`p0007_a1_test_verdict_p41` | A1 の test 2,940 問（hybrid_probe D = 16・owner_answer・m = 3・abstain）と，C1・P4-1 に対する `verdict.json`・`sim_vs_live.json` | 0.6449．C1 に −0.0303（非劣性・優越とも不成立）．P4-1 に −0.0105 | 同上 |
| `p0007_a1_dev_sweep` | A1 の連続実行のスクリプト・条件・ログ・dev の表（`dev_table.json`） | — | 同上 |
| `20261019_p0007_a2_offline` | A2 のオフライン：`atrium fusion-eval`．受信箱の中と中央の Recall@5（密ベクトル・BM25・RRF．dev・test）とゲートの `verdict.json` | 受信箱の中 dev 0.854→0.921（+0.0665，ゲート通過）・中央 test 0.733→0.855 | p0007 A2：デバイス内の検索の改良（RRF）の結果と判定 |
| `p0007_a2_p2p_rrf_r2`，`p0007_a2_c1_rrf` | A2 の test 2,940 問．P2P-RRF（hybrid_probe D = 16・owner_answer・m = 3・abstain・`retrieval.fusion: rrf`）と C1-RRF（`central_local`・rrf）．`p0007_a2_p2p_rrf_r2` は `sim_vs_live.json` を含む（最初の `p0007_a2_p2p_rrf` は probe の 400 で全問失敗し，commit していない） | 0.6769・0.7653 | 同上 |
| `p0007_a2_verdict_rrf`，`p0007_a2_verdict_vs_c1`，`p0007_a2_verdict_vs_p2p_norrf`，`p0007_a2_verdict_c1rrf_vs_c1` | A2 の `verdict.json`（主：P2P-RRF − C1-RRF．他は RRF の効果と参考） | 主 −0.0884（−0.1054〜−0.0714）・非劣性・優越とも不成立 | 同上 |
| `p0007_a2_live` | A2 の連続実行のスクリプト・条件・ログと，ノードの BM25 の索引の費用の測定（`bm25_cost.jsonl`） | CPU の専門家 +150〜159 MB・+3.8〜4.3 秒 | 同上 |
| `20261020_p0007_a2_r3_advert_eval`，`20261020_p0007_a2_r4_frontier`，`20261020_p0007_a2_r5_selection` | A2 の退行ゲート R3〜R5（オフライン） | 全て通過 | 同上 |
| `20261020_p0007_a3_offline` | A3 のオフラインのゲート：`frontier-eval`（hybrid_probe の 1 段目 + probe の得点を BM25 にする `bm25_probe`・語の数を増やす `hybrid_xl`）と，表（`table.md`）・`verdict.json`（`verdict_original` のみ）．R4 の 5 点も同じ実行で一致 | hit@3 0.8391 → 0.9364（+0.0973，要求数 19 → 27）．ゲート通過 | p0007 A3：持ち主の特定のオフラインのゲートの結果 |
| `p0007_a4_p2p_bm25`，`p0007_a4_sel300` | A4 の実機：bm25_probe（D = 24・published の IDF・wb = 2）・owner_answer・m = 3・abstain・`retrieval.fusion: rrf` の test 2,940 問と，選択の確認の retrieval_only 300 問（m = 2）．`sim_vs_live.json` を含む | 0.7153．選択の一致 300/300・2,940/2,940 | p0007 A4：bm25_probe の実機での評価の結果と判定 |
| `p0007_a4_verdict_c1rrf`，`p0007_a4_verdict_vs_p2p_rrf` | A4 の `verdict.json`（主：P2P-BM25 − C1-RRF．記述：P2P-BM25 − P2P-RRF） | 主 −0.0500（−0.0667〜−0.0337）・非劣性・優越とも不成立．P2P-RRF に +0.0384（p = 9e-9） | 同上 |
| `p0007_a4_build_summary` | A4 の制御点での `build-summary`：`bm25_stats.npz` を足した要約と同時に作ったオフラインの選択（`offline_selection.json` の `bm25_probe`・`bm25_probe_m3`）と得点（`bm25_probe_fused.npz`） | 持ち主が候補 3 つに入る割合（オフライン）0.9320 | 同上 |
| `p0007_a4_live` | A4 の連続実行のスクリプト・条件・ログ，照合（`sim_vs_live.py`），記述の集計（`a4_report.py`・`a4_report.json`），ノードの probe 用の索引の費用（`probe_cost.jsonl`） | CPU の専門家 +54 MB・+約 3 秒 | 同上 |
| `20261020_p0007_a4_r3_advert_eval`，`20261020_p0007_a4_r4_frontier`，`20261020_p0007_a4_r5_selection` | A4 の退行ゲート R3〜R5（オフライン） | 全て通過 | 同上 |
| `p0007_d_central_p<P>_r<R>`，`p0007_d_central_norrf_p30_r<R>`，`p0007_d_semi_p<P>_r<R>`，`p0007_d_p2p_p<P>_r<R>`，`p0007_d_p2p_ref_p<P>_r<R>`（P ∈ {1, 4, 8, 16, 30}，R ∈ {1, 2, 3}．63 実行） | D の実機：中央（`central_local`・B1・rrf）・中央 RRF なし（parallel 30 のみ）・準集権（`central_generation: gpu_nodes`）・P2P（bm25_probe・owner_answer・m = 3・abstain・rrf）・P2P の参照（hybrid_probe・B1）の，生成まで含む負荷（300 問．parallel 1 は 150 問）．各実行に `energy/`（GPU 11 台の消費電力）・`gpu_apps_*.txt`（他の利用者の GPU の使用の記録．ブロック 1 の一部は無い） | parallel 30：P2P 0.581〜0.626 > 中央 0.531〜0.537（問/秒．L1 成立）．準集権 0.732〜1.414．P2P の p95 は中央より長い | p0007 D：負荷・エネルギーの結果と判定 |
| `p0007_d_verdict` | D の集計：`verdict.json`（`verdict_original`．L1・L2・p95），`d_summary.json`・`d_runs.json`・`d_tables.md`，`sim_vs_live.json`（K3・K4・選択の一致・他の利用者の GPU の使用・decode の揺れ） | L1 成立（頑健でない）．準集権は P2P より高い．選択の一致 1.0000 | 同上 |
| `p0007_dc_<条件>_p30_c<C>`（C ∈ {1, 2, 3}．9 実行），`p0007_d_verdict_clean` | D の事後の感度分析（格子の外．判定に使わない）：別の研究の GPU の使用が続く中で parallel 30 の中央・準集権・P2P を流し直した結果と，その集計（`sensitivity_posthoc`） | 中央 0.522〜0.535・準集権 0.666〜0.956・P2P 0.481〜0.497（P2P が中央より下） | 同上 |
| `p0007_d_smoke_semi_p8_r0`，`p0007_d_smoke2_semi_p30_r0`，`p0007_d_smoke2_semi_p4_r0` | 準集権の動作確認（150 問．本番の数値ではない）．`p0007_d_smoke2_semi_p30_r0` の開始前の標本は，アイドル電力の基準（DETR が始まる前） | 失敗 0 | 同上 |
| `p0007_d_live` | D の連続実行のスクリプト（`d_chain.sh`・`make_specs.py`・`make_specs_clean.py`・`gpu_apps_snapshot.sh`），予定（`specs*.txt`），集計（`d_report.py`），連続実行の記録（`chain*.log`） | 63 + 9 + 3 実行 | 同上 |
| `20261020_p0007_d_r3_advert_eval`，`20261020_p0007_d_r4_frontier`，`20261020_p0007_d_r5_selection` | D の退行ゲート R3〜R5（オフライン） | 全て通過 | 同上 |
| `p0007_c_freshness`，`p0007_c_freshness_quiet`，`p0007_c_freshness_acc` | C の実機（`freshness-run`．後から届くメール 289 通・T ∈ {0, 60, 600, ∞}）：主の実行（事前登録の規則．`retrieval_only`・hybrid_probe・繰り返しの上限 30 秒），事後の測り直し（繰り返しの上限 2 秒），新しいメールについての正答率の実行（`snippet_return`・採点済み・`sanitize_results.py` 済み．時間は使わない） | 主：P2P direct p50 3.18 秒（`verdict_original` 不成立）．測り直し：p50 0.394 秒・p95 1.43 秒（`verdict_revised` 成立）．中央 T = 0 は p50 0.040 秒，T = 60・600 は 39・534 秒．正答率 P2P 0.654・T = 0 0.664・T ≥ 60 0.19〜0.20 | p0007 C：新しいメールへの追従の結果と判定 |
| `p0007_c_verdict` | C の集計：`verdict.json`（`verdict_original`・`verdict_revised`），`c_summary.json`・`c_tables.md`，`sim_vs_live.json` | 同上 | 同上 |
| `p0007_c_live` | C の集計のスクリプト（`c_report.py`） | - | 同上 |
| `p0007_e1_failure` | E1 の実機（`failure-run`．k ∈ {1, 3, 6, 10}・止め方 availability と container・中央の再起動．down 45 秒・post 30 秒）：`failure.json`・`verdict.json`（`verdict_original`）・`sim_vs_live.json` | 窓の積分は全 8 条件で P2P > 中央（`verdict_original` 成立．ただし中央が 45 秒止まる仮定）．中央の再起動 1.44 秒（温かい）．container の停止中の割合は平常 0.74 から 0.33（k = 1）〜0.03（k = 10）．availability は 0.71〜0.49 で予測と一致 | p0007 E：障害と参加の結果と判定 |
| `p0007_e1_diag_container` | E1 の診断（container の再実行．質問ごとの記録 `questions` つき．判定に使わない） | 時間切れの割合 k = 1 で 0.52（無作為な probe 集合のモデル 0.53）．時間内に答えた質問の割合は平常と同じ | 同上 |
| `p0007_e2_join` | E2 の実機（`join-run`．3 台 = 15 受信箱が途中で加わる．L_join ∈ {0, 10, 100}）：`join.json`・`verdict.json`（`verdict_original`）・`sim_vs_live.json` | P2P の答えられるまで p50 1,283 秒（要約の作成 313〜1,499 秒）・中央 81 秒．転送量 P2P 8.26 MB・中央 15.63 MB．`verdict_original` 不成立（時間で 15.9 倍） | 同上 |
| `p0007_e_verdict` | E の集計：`e_summary.json`・`e_tables.md`・`sim_vs_live.json`（K4．E1 の予測と実測・container の時間切れのモデル） | 同上 | 同上 |
| `p0007_e_live` | E の集計のスクリプト（`e_report.py`） | - | 同上 |
