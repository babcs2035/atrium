# atrium 研究ジャーナル

研究サイクル（research-cycle）の各イテレーションの記録．新しいイテレーションを先頭に追記する（逆時系列）．
末尾の「準備段階の記録」は，サイクルを始める前に行った環境構築・データ準備・実機での検証の要約である．

## p0006 P3：結果と判定（2026-10-17．`results/p0006_p3_feb4rag`・`results/p0006_p3_medrag`・`results/p0006_p3_verdict`）

**結論**：事前登録の 4 組（X が Y より汎用的に優れるか）のうち，「汎用に優れる」と書ける組は**無い**．EnronQA で最良だった hybrid_summary の優位は，FeB4RAG で逆転する（区間の外）．EnronQA 固有の可能性が残る．一致の確認（MedRAG のシャードのラベルと labels.json）は 1.0000 で，止まる条件には当たらない．

**FeB4RAG**（13 エンジン．test 474 問．m = 9．全員に聞く方式の再現率 0.7814・22 要求．目標 0.742）：
- 目標に届く最小の要求数：body_baseline（本文の重心で probe なしに直接選ぶ）**9**，term_stats・hybrid_summary 17，sample_summary 21，flood_score 22．
- 要求数が m = 9 を下回る probe 型は，D < 9 では選べる数が足りず再現率が頭打ち（D = 4：0.42）．probe 型は D ≥ 9 が要るので 18 要求近くになる．
- 関連エンジンが 1 問あたり 11.9/13 と多く，ルーティングで選ぶ余地が小さい（ラベルが飽和）．直接選ぶ方式が最少の要求数になるのは，この構造による．
- 得点の比べ方：flood_score の再現率は none 0.7814・zscore 0.7499・rank 0.7393．dev も none が最大で，none を判定に使った．z 値・順位の正規化は，本研究の範囲（検索器の違いが大きいデータ）では全員に聞く方式をむしろ下げた（原因は未確認．train の分布が test の分布と違う可能性）．
- 限界：配布の評価スクリプトの資源選択の指標は実装が無く出していない．

**MedRAG**（16 シャード．test 4,599 問．m = 3．全員に聞く方式の再現率 0.5740・19 要求．目標 0.545）：目標に届く最小の要求数は term_stats **11**，hybrid_summary 15，sample_summary 15，body_baseline 19，flood_score 19．打ち切りは届かない．要約は各シャードの標本 50,000 通（約 1.2%）から作った近似で，語の統計もその標本から作る（限界）．

**判定**（`atrium frontier-verdict`．EnronQA は P2 反復 2 の n30，FeB4RAG は none）

| 組 | 勝ったデータ | 逆転が区間の外 | 判定 |
|---|---|---|---|
| hybrid_summary > body_baseline | EnronQA・MedRAG（2/3） | FeB4RAG（予算 11：0.216 対 0.786） | 汎用でない |
| hybrid_summary > term_stats | EnronQA（1/3） | MedRAG（予算 9.5：0.516 対 0.530） | 汎用でない |
| term_stats > body_baseline | EnronQA・MedRAG（2/3） | FeB4RAG（0.215 対 0.786） | 汎用でない |
| hybrid_summary > sample_summary | 判定不能（EnronQA の P2 反復 2 に sample_summary の行が無い．反復 1 の値は hybrid が上） | — | 判定不能 |

- 4 組を見た多重性があるが，どの組も「汎用」でないので，結論は変わらない．
- 読み方：probe 型（語の統計・併用）は「持ち主が少数のデバイスに偏る」EnronQA と MedRAG で，直接選ぶ重心に勝つ．ラベルが飽和し，直接選ぶ方が要求数の少ない FeB4RAG では勝てない．汎用の方式は無く，データの性質（関連データ源の数・検索器の違い）に依る．
- 露出：term_stats と hybrid_summary の出どころの top-1 は EnronQA で 0.635・0.726（FeB4RAG のエンジンでは 0.515）．要約の種類による交換関係は P2 と同じ向き．

**P4 の最終候補（最大 2 つ）**：hybrid_summary（V = 64,000・w = 0.5・dev が選んだ D = 16）と term_stats（V = 64,000・dev が選んだ D = 24）．P3 で「汎用に優れる」と言えないため，P4 は EnronQA の実機の確認として位置づけ，汎用性の主張はしない．

## p0006 P3：汎用性の検証（FeB4RAG・MedRAG．事前登録．2026-10-17）

レバー `generality.dataset`・`generality.score_norm`．評価の前に，条件・判定の規則を固定する．結果を見て変えない．

**データと単位**（`atrium frontier-eval --dataset feb4rag|medrag`．dev = train + val の質問，test = test の質問．方式は学習しない）
- FeB4RAG：13 エンジン（1 エンジン = 1 デバイス）．790 問（dev 316・test 474）．2 段目の得点はエンジンの配布済み検索結果の最高の得点．関連ラベルは LLM 判定（1 問平均 11.9 エンジンが関連）．m = 9（config の random_k.feb4rag と同じ）．主の指標は関連エンジンの再現率 `source_recall@9`（選んだ 9 エンジンに入る関連エンジンの割合．上限は 9 / 11.9）．要約は，エンジンが返した文書（title と text．13 エンジンで 512,806 通）を共通の符号化器（arctic-embed-m-v1.5）で埋め込んだもの．配布の評価スクリプトの資源選択の指標（nDCG など）は，このリポジトリに実装が無いので出さない（限界）．
- MedRAG：16 シャード（1 シャード = 1 デバイス）．7,663 問．2 段目の得点は，全シャードを総当たりで検索した上位 15 件（保存されていないので GPU で計算し直す）の最高の内積．関連ラベルは「統合後の上位 15 件に 1 件以上を出したシャード」．m = 3（config の top_m.medrag と同じ）．主の指標は `source_recall@3`（シャード単位．データ源単位の集計は出さない）．要約は，各シャードから無作為に取った文書 50,000 通（25 ファイル × 2,000 通．シャードの約 1.2%．MedCPT の埋め込みと本文の語の統計）で，語の統計はこの標本から作る（近似．限界）．
- 一致の確認（止まる条件の 1）：シャードのラベルから求めたデータ源の集合が `labels.json` と一致する割合 ≥ 0.999．届かなければ止まる．

**得点の比べ方**（`generality.score_norm`．FeB4RAG のみ）：none・zscore・rank を全て流す（z 値と順位は，train の質問のエンジンごとの得点の分布から）．dev の全員に聞く方式の再現率が最大の正規化を，判定に使う．MedRAG は 1 つの検索器（MedCPT）なので none だけ．

**方式**：flood_score・body_baseline（本文の重心）・sample_summary・term_stats・term_stats_large・hybrid_summary・adaptive_cutoff．設定の格子は P2 と同じ（`frontier_methods.py` の定数）．hierarchy・overlay_walk は P2 で目標に届かず，P3 から外す．ヘッダー込みの既存の基準（device_probe など）は EnronQA 固有なので使わない．

**目標**：EnronQA は hit@2 ≥ 0.78．FeB4RAG・MedRAG は全員に聞く方式（flood_score）の test の再現率の 0.95 倍．方式の族ごとに，目標に届く最小の要求数（test の最小値．選ぶ変数は設定の格子だけ）で順位づけし，届かない族は，要求数 [m, n_devices + m] を 1 に正規化した，フロンティアの階段の下の面積で順位づける（p0006 §7.3）．

**判定**（`atrium frontier-verdict`．EnronQA は `results/p0006_p2_iter2` の n30 を使う）
- 「X は Y より汎用的に優れる」：X が Y より優れることが 3 つのデータのうち 2 つ以上で成り立ち，残りで逆転の差が区間の外にない（要求数の予算が全員に聞く方式の半分のとき，Y の再現率の 95% 区間の下端が X の上端より高いデータが無い）．予算内に点が無い方式は再現率 0 とみなす．
- 比べる組：(hybrid_summary, body_baseline)・(hybrid_summary, term_stats)・(term_stats, body_baseline)・(hybrid_summary, sample_summary)．4 つの組を見る多重性があるが，閾値は変えない（組の数を結論に併記する）．
- 結論が「汎用に優れる」にならない組は，EnronQA 固有の可能性があると書く．露出（出どころの top-1・AUC）と公開情報の大きさは記述のみ．

**実行**：`bash scripts/tasks/remote.sh frontier_eval p0006_p3_feb4rag - flood_score,body_baseline,sample_summary,term_stats,term_stats_large,hybrid_summary,adaptive_cutoff --dataset feb4rag`（MedRAG も同じ形）．反復の上限は P3 全体で 4．

## p0006 P2：反復 2 の結果と P2 のまとめ（2026-10-17．`results/p0006_p2_iter2`）

**判定**：H-P2a′ は規則どおり**不成立**．ただし near-miss である．dev で hit@2 ≥ 0.78 に届く最小の設定が選ばれ，hybrid_summary（V = 64,000・w = 0.5）は dev の D = 12 が 0.7778（目標に 0.002 届かない）で，D = 16（18 要求・dev 0.7863）が選ばれた．test は D = 16 が 0.8017（95% 区間 [0.787, 0.816]，18 要求）で，要求数の上限 16 を 2 超える．規則は結果を見て変えない．

**探索的な値**（test．選択に使っていない．n30・全問．hit@2）

| 設定 | 要求数 | hit@2 [95% 区間] | A 群を除く hit@2 | 出どころ top-1・AUC |
|---|---|---|---|---|
| flood_score | 32 | 0.8041 | 0.7595 | （公開情報なし） |
| device_probe（既存．ヘッダー込み） | 14 | 0.6830 | 0.6047 | — |
| device_probe_body（本文の重心） | 14 | 0.6510 [0.633, 0.669] | 0.5955 | 0.526・— |
| term_stats（V = 64,000） | 14 | 0.7684 [0.753, 0.783] | — | 0.635 |
| **hybrid_summary（V = 64,000，w = 0.5，D = 12）** | **14** | **0.7895 [0.775, 0.803]** | **0.7352** | **0.726・0.975** |
| hybrid_summary（同上，D = 16．dev が選んだ設定） | 18 | 0.8017 [0.787, 0.816] | 0.7526 | 0.726・0.975 |
| hybrid_summary（D = 8） | 10 | 0.7701 | 0.7077 | 0.726 |

- hybrid_summary は，同じ 14 要求の device_probe_body に対して hit@2 が相対で +21.3%，既存の device_probe に対して +15.6%．D = 16（18 要求）で flood_score（32 要求）の hit@2（0.8041）と区間の中で並び，要求数は 44% 少ない．重みは 0.5（重心側を弱く）が最良（w = 1：0.7847，w = 2：0.7619）．V は 64,000 が 16,000（0.7806）より高い．
- n150（1 受信箱 = 1 デバイス）：hybrid_summary は 14 要求で 0.7833，26 要求で 0.7935（全員に聞く方式は 152 要求）．device_probe_body は 26 要求で 0.620．要求数の伸びは緩やかで，hit も保たれる．
- **露出の代償**：出どころの top-1 は 0.726（AUC 0.975）で，本文の重心だけ（0.526）や標本の要約（0.073・S = 4）より高い．公開情報は 1 デバイスあたり約 1.0 MB（語の統計 64,000 語 × 8 bytes に重心 32 個 × 768 × 4 bytes を足した値の合計は，30 台で 29.7 MB）．語の統計は持ち主の特定に使われやすい．これは要求数と露出の交換関係であり，「汎用」の主張には露出の代償を併記する．
- 重みを変えたとき，hit の最良（w = 0.5）と出どころの top-1（w = 1 が最大 0.762）は同じ向きに動かない．露出を抑えた設定は未検討．

**P2 のまとめ**（反復 2 / 6 回を使用）
- RQ-A′ への答え（EnronQA・本文だけの要約）：同じ hit@2（≈ 0.80）に，全員に聞く方式の 32 要求を，語の統計と重心の併用で 18 要求（−44%）まで減らせる．n150 では 152 要求を 26〜32 要求（−79〜83%）にできる．hit@2 ≥ 0.78 を 16 要求以下にする事前登録の目標は，規則では未達（14 要求の点は探索的に達する）．
- 汎用の主張：A 群を除いても順位は変わらない（hybrid 0.7352 ＞ term_stats ＞ device_probe_body）．
- 失敗した方式：sample_summary（無作為の標本）は重心に劣る，adaptive_cutoff は同じ要求数の固定の D と差が無い（Iteration 19 と同じ），hierarchy は要求数が固定のため n150 で hit が落ちる，overlay_walk は往復が多く要求数が同じ device_probe_body に劣る．
- **P4 の最終候補（暫定．最大 2 つ．P3 の結果で確定）**：hybrid_summary（V = 64,000・w = 0.5・D は dev が選んだ 16）と term_stats（dev が選んだ設定）．
- 次：P3（FeB4RAG・MedRAG で同じ枠組みの汎用性を確かめる）．

## p0006 P2：反復 2 の事前登録（語の統計の拡張と併用．2026-10-17）

反復 1 の結果（term_stats が最も近い：V = 16,000・D = 12 で 14 要求・hit@2 0.7558）を受け，term_stats を伸ばす．評価の前に固定する（結果を見て変えない）．

**変えるもの**（反復 1 と同じ `frontier-eval`．方式の追加だけ）
- `term_stats_large`：公開する語の数 V = 64,000（上位 V 語の文書頻度．反復 1 の V ≤ 16,000 の続き）．
- `hybrid_summary`：語の統計（CORI．V ∈ {16,000, 64,000}）と本文の重心（multi_centroid_body のデバイス最大）を，質問ごとにデバイス間で標準化して重み w ∈ {0.5, 1, 2}（重心側）で足す．どちらも本文だけから作る（§3.2）．
- 比べる相手（同じ実行に入れる）：flood_score・device_probe（既存）・body_baseline・term_stats（反復 1 の再現）．n30・n150．

**仮説**（反復 1 の H-P2a と同じ規則．族は term_stats（V ≤ 64,000 を含む）と hybrid_summary．dev で選んだ設定の test・n30・全問）
- H-P2a′：いずれかの族が hit@2 ≥ 0.78 かつ平均の要求数 ≤ 16．反復 1 と合わせて 8 つの族を見ているので，点推定が通っても，hit@2 の 95% 区間の下端が 0.77 未満なら「弱い成立」と書く．
- 採用（P3 の最終候補）：H-P2a′ を通り，かつ要求数が同じ以下の device_probe_body に対して hit@2 が相対で 5% 以上高い方式．
- 記述のみ：露出（出どころの top-1・AUC．公開情報の大きさ）と，n150 での要求数・hit，A 群を除いた hit．V を増やす・併用するときの，hit の上昇と露出の増加の交換関係．
- 判定の流れ：H-P2a′ が成立すれば，反復 3 以降は露出を抑える方向（V の削減・語の絞り込み）を試す．成立しなければ，term_stats の限界（同じ要求数で 0.78 に届く最小の要求数）を記録して P2 を閉じ，P3 へ進む（最終候補は term_stats を暫定とし，採用の規則を満たすかを書く）．

**実行**：`bash scripts/tasks/remote.sh frontier_eval p0006_p2_iter2 p0006_c1_all_pernode flood_score,device_probe,body_baseline,term_stats,term_stats_large,hybrid_summary`

## p0006 P2：反復 1 の結果と判定（2026-10-17．`results/p0006_p2_frontier`）

**判定**：H-P2a **不成立**．H-P2b **不成立**．H-P2c は規則どおり成立だが意味が薄い．adoption（採用）する方式は無い．

**H-P2a**（dev が選んだ設定の test．n30・全問）：body_baseline 0.8041（32 要求），sample_summary 0.8041（32），term_stats 0.7952（26），overlay_walk 0.7830（28.8），adaptive_cutoff 0.6963（17.5），hierarchy 0.5810（13）．要求数 ≤ 16 で hit@2 ≥ 0.78 を満たす族は無い．要求数 ≤ 16 の最良の点（test．選択に使っていない参考）は term_stats（V = 16,000，D = 12）の 0.7558（14 要求）で，0.78 に届くのは D = 16〜24（18〜26 要求）．

**要求数ごとの最良点**（test・n30・全問．hit@2）

| 要求数 ≤ | term_stats | device_probe（既存．ヘッダー込み） | device_probe_body | overlay_walk | adaptive_cutoff | sample_summary | hierarchy |
|---|---|---|---|---|---|---|---|
| 8 | 0.6990 | 0.5949 | 0.5245 | 0.3673 | 0.5173 | 0.3524 | 0.3820 |
| 12 | 0.7211 | 0.6303 | 0.5810 | 0.5415 | 0.5942 | 0.4116 | 0.5245 |
| 16 | 0.7558 | 0.6830 | 0.6510 | 0.6347 | 0.6864 | 0.5078 | 0.5810 |
| 20 | 0.7755 | 0.7296 | 0.6980 | 0.6435 | 0.6963 | 0.5850 | 0.5810 |
| 26 | 0.7952 | 0.7837 | 0.7752 | 0.7435 | 0.6963 | 0.7143 | 0.5810 |

**term_stats**（本文の語の文書頻度の上位 V 語を公開し，CORI で順位づける）：同じ要求数で，本文だけの multi_centroid（device_probe_body）より D = 12（14 要求）で hit@2 が相対 +16.1%（0.7558 対 0.6510．95% 区間 [0.741, 0.770] 対 [0.633, 0.669]），ヘッダー込みの既存の device_probe より +10.7%（対 0.6830）．A 群を除いても順位は保たれる（D = 12：0.6913 対 0.5955・0.6047）．n150 でも D = 24（26 要求）で 0.7735・D = 48（50 要求）で 0.7895（全員に聞く方式は 152 要求）．V が大きいほど hit は上がる（V = 1,000・4,000・16,000 の D = 12：0.591・0.692・0.756）．**露出**：公開情報の大きさは V = 16,000 で 3.84 MB（float32 の語の番号と df の組として数えた値）．出どころの top-1 は 0.589・AUC 0.94（V = 4,000：0.437・0.875，V = 1,000：0.323・0.812）で，multi_centroid_body の 0.526 とほぼ同程度，標本の要約の 0.073（S = 4）より高い．hit と露出の間に交換関係がある．

**H-P2b**（打ち切りが固定の D より 25% 以上要求数を減らし，hit を 0.01 より下げない）：不成立．打ち切り（softmax_mass，p = 0.9，Dmax = 16，τ = 0.05）は multi_centroid_body の上で要求数を 17.5（固定の D = 30 の 32 より 45% 少ない）にしたが，hit@2 は 0.696 に下がった（dev が目標に届かない族なので，dev で hit が最大の設定が選ばれた）．同じ要求数での比較（固定の D = 16：18 要求で 0.698）では打ち切りの 0.696 と差が無い．問ごとに台数を変えても，同じ要求数の固定の D の曲線は上回らない（Iteration 19 と同じ結論）．

**H-P2c**：成立（F3 の要求数 13 は n30・n150 で同じ（伸び 1.0）で，flood_score の 4.75 より小さい）．ただし hit@2 は n30 の 0.581 から n150 の 0.382 に下がり，F3 は目標の hit に届かない（dev が目標に届かない族）．要求数が伸びないのは，G′ + D2 + m が固定だからであり，探索の質は保たれていない．

**その他**：sample_summary（標本の埋め込み）は低く，S = 64・D = 12 で 0.508（device_probe_body の 0.651 より低い）．無作為の標本は，持ち主の話題をまとめた重心に劣る．overlay_walk（近傍だけを知る転送）は往復 14 回で，15 要求の 0.635 と，要求数が同じ device_probe_body（0.651）より低く，n150 では 40.7 要求で 0.574 に下がる．
- **A 群の点検**：族の順位（all：body・sample 0.8041 ＞ term 0.7952 ＞ overlay 0.7830 ＞ adaptive 0.6963 ＞ hierarchy 0.5810）は `no_owner_name`（0.7595・0.7595・0.7462・0.7334・0.6381・0.5268）で変わらない．
- **反復の使用**：P2 の 6 回のうち 1 回．次は，term_stats が H-P2a に最も近いので，これを伸ばす（反復 2）．

## p0006 P2：汎用のルーティング方式（EnronQA．事前登録．2026-10-17）

レバー `routing.frontier_eval`（R4）・`routing.sample_summary`・`routing.term_stats_summary`・`routing.adaptive_cutoff`・`routing.hierarchy`・`routing.overlay_walk`．評価の前に，条件・仮説・判定の規則・閾値を固定する．結果を見て変えない．

**先行研究の確認**（tavily-search．§10 の 5）：連合 RAG の調査論文（arXiv:2505.18906）は選択的なルーティング（RAGRoute）・FeB4RAG・暗号化 kNN（FRAG）を挙げ，DRAG（arXiv:2505.00443）は TARW による P2P の探索，Rank-S・Taily（Kulkarni ら 2012，Aly ら 2013）は選択的検索の資源選択である．デバイス単位の要求数・往復・露出を同じ軸で測って汎用の方式を比べる研究は見つからず，§10 の 5 には当たらない．ただし F1〜F4 は既存の資源選択（ReDDE・CSI・CORI・Rank-S・Taily・DNS・DRAG）の再実装であり，新規性の主張は「分散 RAG の設定で同じ軸の上で比べ直した」範囲にとどめる．

**R4**：`results/p0006_p2_r4_smoke`．flood_score 0.8041（32 要求），device_probe d24 0.7837（26）・d12 0.6830（14），centroid_probe K=75 0.7612（31.1 台に届く），multi_centroid 0.4401（2）．5 点とも一致（許容 6e-4．要求数は厳密）．

**条件**：`atrium frontier-eval`．n30（既存の 30 台の配置）と n150（1 受信箱 = 1 デバイス）．要約は**ヘッダーを除いた本文**（区切り線より後）から作る．質問は dev 2,948 問・test 2,940 問．m = 2．部分集合は `all` と `no_owner_name`（質問が持ち主の姓を含む A 群を除く）．種は 12．

**設定の選び方**（族ごと．dev だけで選ぶ）：dev の hit@2 が 0.78 以上の設定のうち，dev の平均の要求数が最小のもの．届かなければ dev の hit@2 が最大のもの．test の値は，その設定の値として報告する．

**仮説と判定**
- H-P2a（主）：§3.2 を守る族（body_baseline・sample_summary・term_stats・adaptive_cutoff・hierarchy・overlay_walk）のいずれかが，上の規則で選んだ設定の test（n30・`all`）で，hit@2 ≥ 0.78 かつ平均の要求数 ≤ 16．6 つの族から選ぶ多重性があるので，余裕（hit@2 − 0.78）が 0.01 未満なら「弱い成立」と書く．**採用**（P3 に進める最終候補）は，H-P2a を通り，かつ同じ要求数以下の body_baseline（device_probe_body）の最良の点に対して hit@2 が相対で 5% 以上高い方式だけ（p0006 §7.2）．
- H-P2b：打ち切り（F2）が，同じ 1 段目（multi_centroid_body と標本 S = 64 の max）で dev が選んだ固定の D より，test の平均の要求数を 25% 以上減らし，hit@2 を 0.01 より下げない（2 つの 1 段目のどちらかで成立すれば成立）．
- H-P2c：n150 で，dev が選んだ F3 の要求数の伸び（n150 / n30）が，flood_score の伸び（n150 / n30 = 152 / 32）より小さい．
- 記述のみ：F4 の hit@2・要求数・往復（最大）．公開情報の大きさ（float32 で数えた bytes）と，出どころの特定（top-1・AUC．n30 の test のメール以外の最大 10 通 / 受信箱）．
- A 群の点検：族ごとの dev が選んだ設定の hit@2 の順位が，`all` と `no_owner_name` で入れ替わる場合は，その方式を「汎用」と書かない（§3.2）．

**実行**：`bash scripts/tasks/remote.sh frontier_eval p0006_p2_frontier p0006_c1_all_pernode all`（本文の埋め込みは制御点の `<data>/enronqa/frontier/` に保存済み）．方式の設定の格子は `src/atrium/frontier_methods.py` の定数（SAMPLE_SIZES など）に固定している．

**止まる条件**：§3.2 の「主の候補にしない信号」を必要とする方式が見つかった，R4 が崩れた，ディスク使用率 90% 超（§10）．反復の上限は P2 全体で 6．

## p0006 P1：中央集約ベースライン（反復 1 回目．結果と判定．2026-10-17）

**結論**：事前登録の厳密な一致は 0.9942 で閾値 0.999 に届かず，p0006 §10 の 1 に従って止まった（B56）．ユーザーの「残りを全て自律で遂行」の委任を受け，B56 の推奨 A1 を採用して，同点を許す一致（`central-eval` の追加指標．`results/p0006_p1_central_eval_v2`）で再確認した：**1.0000（等価）**．したがって C1 は「厳密な一致の事前登録には届かないが，差は重複メールの同点の順序だけで，同点を許すと精度の上で中央集約と等価」と書く（事後に基準を足した変更である）．B57（結果へのメール本文の混入）は `sanitize_results.py` で C0・C1 を整形した．仮説の判定は事前登録の規則で出したとおりで，変わらない．

**実行**（回答は llama3.1:8b・温度 0，判定は qwen3:8b．2,940 問，失敗 0，判定不能 0）
- `results/p0006_c0_gold_context`（C0）、`results/p0006_c1_all_pernode`（C1）、`results/p0006_p1_central_eval`（`central-eval`）．C2 は `20261009_eq5_b1`，C3 は `20261009_eq5_b1_flood`．
- C1 の最初の実行（`http_pool: shared`，run_id `p0006_c1_all_global_merge`）は，1 問が 30 台へ並行に送る要求が共有の池の上限 512 を超え，82 問中 68 問が `PoolTimeout` で失敗したため止めた（結果は制御点にだけあり，根拠に使わない）．`http_pool: per_node`（接続の持ち方だけが違い，検索と選択は同じ）で新しい run_id から流し直した．事前登録の「条件」には書いていない変更である．

**正答率**（元の値．qwen3:8b）

| 条件 | 正答率 | 検索の Recall@5 |
|---|---|---|
| C0 正解メールのみ | 0.8816 | 1.000（定義） |
| C2 oracle（持ち主の受信箱） | 0.7769 | 0.8629 |
| C1 中央・全受信箱の統合 | 0.6752 | 0.7333 |
| C3 flood_score 上位 2 | 0.6616 | 0.7119 |
| 参考：中央・BM25（全体で統計共有．回答なし） | — | 0.9031 |
| 参考：中央・密ベクトル（1 索引．オフライン） | — | 0.7330 |

**等価性**：C1 の `top_doc_ids` が全 73,772 通の 1 索引の上位 5 通と集合として一致する割合 0.9942（17 問の不一致．順序も合う割合 0.9711）．閾値 0.999 に届かない．診断（制御点の別の 1 回の実行．同点を安定な並べ替えで見ると 23 問）では，不一致の全問で C1 だけが選んだ文書と中央だけが選んだ文書の得点が完全に同じ（差 0.0）で，5 位と 6 位の得点が同点だった．重複するメールの採り方の違いであり，検索の誤りではない（Recall@5：C1 0.7333・1 索引 0.7330）．

**仮説の判定**（McNemar の正確検定，同じ 2,940 問．Holm 補正は H-P1a・H-P1b の 2 つ）
- H-P1a（C2 > C1）：C2 のみ正解 428・C1 のみ正解 129，p < 1e-39（補正後も同じ），相対差 +15.1% → **支持**．
- H-P1b（C1 ≥ C3）：C3 のみ 135・C1 のみ 175，p = 0.0266（Holm 補正後 0.0266）．C3 は C1 を上回らず棄却されない → **支持**．C1 > C3 の差は有意（相対差 +2.1%，5% 未満）．
- 記述：C0 − C2 = +0.105（検索の取りこぼし），C2 − C1 = +0.102（持ち主の絞り込みの価値）．C0 は C1 より 0.206 高い．
- 補足：C1′（BM25）の Recall@5 0.9031 は密ベクトルの 0.7330 より 0.170 高い．EnronQA の質問は固有名詞や語を含む問が多く，本研究の埋め込みの検索より語の一致が強い（測っていない説明）．

**露出**（1 問あたり．C1 は 30 台すべてが質問を受け取り，750 通がデバイスの外へ出る．中央に集める場合は一度きり 73,772 通．oracle は 1 台・5 通，flood_score は 30 台・10 通．**C1 は分散のまま最も多くの文書を外に出す**）．

**原論文との位置づけ**（参考．直接比べない）：原論文は 103,638 通の索引・Llama-3.1-70B の判定で，BM25 の Recall@5 87.5・Llama-3.1-8B の正答率 72.1%．本研究は 73,772 通・qwen3:8b の判定で，BM25 の Recall@5 0.9031，C1 の正答率 0.6752（密ベクトル）．索引の通数・評価の問・判定モデル・プロンプトが違う．

**道具の注記**：`atrium compare` は EnronQA の判定結果を合流しない（`correct` を持たない）ので，比較は `merge_judgements` で合流して `compare_runs` に渡した（EQ5 と同じ方法）．

## p0006 P1：中央集約ベースライン（反復 1 回目．事前登録．2026-10-16）

レバー `baseline.central`．評価の前に，条件・仮説・判定の規則・閾値を固定する．結果を見て変えない．

**条件**（回答は llama3.1:8b，温度 0，判定は qwen3:8b．EnronQA test 2,940 問．EQ5 の B1 と同じ設定：`parallel: 30`，`merge: score`，`k_context: 5`）

| ID | 内容 | 実行 |
|---|---|---|
| C0 | 正解メールだけを文脈にする（検索なし．評価専用．`run_meta.json` に `evaluation_only: true`） | 新規．`answer_mode: gold_context`．`routing` は何でもよい（使わない）．`flood_score` のまま流す |
| C1 | 全受信箱の上位 5 通を集め，全体の得点で上位 5 通にして質問者が回答 | 新規．`routing: all`・`merge: score`・`answer_mode: snippet_return` |
| C1′ | 全体で統計を共有した BM25 の上位 5 通 | オフラインの Recall@5 のみ．**回答までは行わない**（質問者の経路に BM25 の検索がなく，C1′ の目的は検索の精度の比較だから） |
| C2 | oracle（持ち主の受信箱だけ） | 既存 `20261009_eq5_b1`（0.777） |
| C3 | flood_score の上位 2 受信箱 | 既存 `20261009_eq5_b1_flood`（0.662） |

C1 の評価の統計は 1 回の実行で，C2・C3 は既存の実行を再利用する（同じ 2,940 問）．質問の並べ替えは `experiment.seed = 12` で全条件が同じ．判定は元の値（qwen3:8b）を主とする．0.6B が絡まないので狭い補正は使わない．

**等価性の確認**（`atrium central-eval`）：全 73,772 通を 1 つの索引にした上位 5 通の文書 ID が，C1 の実機の `top_doc_ids` と一致する問の割合（集合として）が **≥ 0.999**．届かなければ p0006 §10 の 1 により止まる．順序も合う割合は記録する．C1 が 1 問でも失敗した問は，比べる問から除き，欠けた数を書く．

**仮説と判定の規則**（正答 = judgements.jsonl の `correct`．同じ 2,940 問．McNemar の正確検定，両側．Holm の補正を H-P1a・H-P1b の 2 つにかける，α = 0.05）

- H-P1a：C2 > C1．支持の条件：Holm 補正後の p < 0.05，かつ C2 の正答率が C1 より高く，相対差 (C2 − C1) / C1 ≥ 5%（p0006 §7.2 の採用の規則）．
- H-P1b：C1 ≥ C3（中央が flood_score の 2 受信箱より下回らない）．**棄却の条件**：C3 の正答率が C1 より高く，Holm 補正後の p < 0.05．棄却されなければ支持とし，C1 > C3 が有意かどうかは別に書く．
- 記述のみ：C0 の正答率（読解と判定の上限），C1 と C0・C2 の差の分解（検索の損失 = C0 − C2 相当ではなく，C2 − C1 を「持ち主の絞り込みの価値」，C0 − C2 を「検索の取りこぼし」と読む），C1′ と C1 の密ベクトルの Recall@5．

**露出の勘定**：条件ごとに，デバイスの外に出る文書の数を「一度きり（集約）」と「1 問あたり」に分けて表にする．C1 を物理的に 1 台へ集める場合の一度きりは全 73,772 通，分散の各方式は 1 問あたり実行の `snippets_exposed` の平均．待ち時間は，ノードが 150 受信箱を持てると確かめられないので測らない（p0006 P1 の条件どおり．判定に使わない）．

**原論文（Ryan et al. 2025，Table 4）との位置づけ**：表では「参考」として並べ，索引の通数（103,638 通対 73,772 通）・評価の問・判定モデル（70B 対 qwen3:8b）・プロンプトが違うことを列挙する．数値を直接比べない．

**止まる条件**：R1〜R3 に当たる退行，等価性が 0.999 に届かない，C1 の失敗率が 1% を超えて再開しても解消しない，ディスク使用率 90% 超（§10）．

**実行の手順**：deploy → start（C0 と C1 を 1 つずつ．C0 は deploy が chunk を配る）→ 判定（`judge.sh`）→ analyze → `central-eval`（C1 の `results/` を使う）→ `compare`．連続実行の間は commit しない．

## p0006 P0：片付けと退行ゲート（2026-10-16）

- 計画：`plans/p0006_generic_routing_and_central_baseline.md`（ユーザーの指示で P0〜P5 を実行．B55）．`git tag pre-p0006`（8670895）を push した．
- 凍結：`churn_eval.*` の 6 レバーを `frozen_levers:`（config.yml のトップレベルの新キー）へ移した．skill のスクリプトは `slack.*` しか読まないため，コメント化の代替案は使っていない．B54 の Iteration 20（`device_probe_cutoff`）は凍結の対象なので実行せず，同じ考えは P2-2 の `routing.adaptive_cutoff` で行う．state.json の iteration_name を P1 に差し替えた．
- 退行ゲート（イメージは bf912a2 から build）：
  - R1（`results/p0006_r1_med_all_ret`）：MedRAG，routing=all，retrieval_only，question_limit=2（5 問題集 × 2 = 10 問）．s3 と `top_doc_ids` が 10/10 一致．
  - R2（`results/p0006_r2_feb_ragroute`）：FeB4RAG，routing=ragroute，question_limit=2．s2 と `selected_sources` が 2/2 一致．
  - R3（`results/p0006_r3_advert_eval`）：flood_score hit@1 0.7224・hit@2 0.8041，multi_centroid hit@1 0.3486・hit@2 0.4401 が計画の値と一致（許容 5e-4）．centroid_probe（K = 75）は `advert-eval` の出力に無く（`cascade-eval` の値），P2-0 の R4 で確かめる．
- 副次的な観察：`advert-eval` の dev の MRR は flood_bm25_shared_idf 0.904，flood_score 0.781，flood_bm25_local_idf 0.681．全体で統計を共有した BM25 による持ち主の特定が最も高い．P1 の C1′ の見込みの材料になる（未検証．test の値は P1 の事前登録の後に読む）．

## Iteration 19: 返事の無い枠を補う方式と最初から多めに送る方式の，同じ問い合わせの数での hit の比較

### 調査・計画（2026-10-16．research-cycle の phase 1）

**調べて分かったこと**

- Iteration 18 の比較の偏り（journal の Iteration 18 の表から計算した．`metrics.json` は読めていない）：
  - none で D を 12 から 15 へ上げたときの限界の回収は，1 種あたり 1 台あたり約 25.5 問である．
  - once（D = 12）の contacted は 15.15〜15.64 で，d15（15.00）より 0.15〜0.64 台多い．限界の回収で換算すると約 4〜16 問になり，観測された差（平均 6.6 問．種ごとに 0〜+22 問）と同じ大きさである．最も近い D との単純な比較は，once に有利な側に偏っている．
  - none の d12 と d15 の間を折れ線で補間すると，contacted 15.35 での none の n_hit の種の和は約 7,429 で，once の 7,417 との差は約 −12 問（1 種あたり約 −2.4 問）になる（推測．種ごとの対応は確かめていない）．
- コードで確かめたこと：`visible_top_devices` は安定な並べ替えで上位 d 台を取る．そのため，none の候補は D について入れ子になり，contacted は D について減らない（G4 の根拠）．
- Dean & Barroso, "The Tail at Scale", Communications of the ACM 56(2), 2013, pp. 74–80：
  - fan-out の大きい要求では，最も遅い少数の要求が全体の待ち時間を決める．Google の測定では，1 台への要求の 99 パーセンタイルが 10 ms のとき，全ての要求の完了を待つ 99 パーセンタイルは 140 ms で，最も遅い 5% の要求を待つ分がその約半分を占める．
  - tied request（同じ要求を 2 つの複製に送り，片方が処理を始めたら他方を取り消す）で，送る前に 1 ms 遅らせると，中央値が 16%，99.9 パーセンタイルが約 40% 短くなった．
  - 送る前に遠隔の待ち行列を probe して最も空いた server を選ぶ方式は，複製へ同時に送る方式より効果が小さいとされる．理由は，probe と要求の間に負荷が変わること，処理時間を見積もりにくいこと，全ての client が同じ server を選んで偏りが生じうることである．
  - atrium への含意（推測）：hedge は，遅い少数の要求にだけ追加を送る．once は 96.4% の問に 2 往復目を送るので，hedge のようには働かず，ほぼ全ての問の待ち時間に 2 往復目が加わる．hit が同じなら，往復の少ない構成が有利と読める．
- Kulkarni & Callan, "Selective Search: Efficient and Effective Search of Large Textual Collections", ACM Transactions on Information Systems 33(4), 2015：
  - 資源の順位付けに ReDDE（0.5% の標本からなる CSI）を使い，上位の固定の T 個のシャードだけを検索して結果を統合する．D はこの T に当たる．
  - 返事の無いシャードの扱いの記述は見つからなかった．問ごとに打ち切りの数を変える Rank-S・Taily の書誌と内容は確かめていない．
- B50 の要レビュー (a) への答え：2 つの文献の書誌と上の内容を確かめた．Iteration 18 の「hedge は複製に重ねて送る方式で，atrium の補う変種とは異なる」という記述は，本文と矛盾しない．

**単一レバー**：`churn_eval.device_probe_budget`（device_probe の 1 段目の台数 D）を変える．Iteration 18 では D は {12, 15} で，15 は none だけだった．これを D ∈ {9, 10, 11, 12, 13, 14, 15, 16, 17, 18} に広げ，全ての D で refill ∈ {none, once, until_full} を流す．
- 新しい CLI `atrium churn-budget-eval`（`src/atrium/churn_budget_eval.py`）で実行する．
- 次の規則は，Iteration 18 の `route_cascade_with_refill` と同じにする（import して再利用する）．補う規則，2 段目，hit と (i)〜(iv) の分け方，contacted・wasted，往復の情報．
- none の D = 9〜18 から，同じ条件・同じ種の「contacted と n_hit の曲線」を作る．once と until_full の点を，この曲線と同じ contacted で比べる．

**固定する構成**
- 変えないもの：config.yaml，データ，シャード，配置，m = 2，C = 32，`AVAILABILITIES`・`REFRESH_STEPS`・`MEAN_UP_STEPS`・`N_SCHEDULE_SEEDS`・`experiment.seed` = 12，採点・質問の順番．
- メンバー表は Iteration 16 の `join_notified_view` とし，離脱は通知しない．返事の無いことは，送った時点で分かるとする．
- 方式は device_probe だけにする．centroid_probe・multi_centroid・flood_score は流さない．
- 条件は a ∈ {1.0, 0.9, 0.7, 0.5}，R ∈ {1, 10, 100, 1000}，L_join ∈ {∞, 0, 100}，種 5 個である．
- 実行の数は，10（D）× 3（refill）× 4 × 4 × 3 × 5 = 7,200 件である．

**比較の規則**
- 同じ（a, R, L_join, 種）の none の点 (x_D, y_D) を作る．x_D は contacted の問あたりの平均，y_D は n_hit である（D = 9〜18）．
- 点を x の昇順に並べ，折れ線で補間した関数 Î を作る．x の等しい点が複数あれば，その中の n_hit の最大値を使う（none に有利な側に寄せ，H-B1 を厳しくする）．
- 補う変種の点 (c, n) に対し，Δ = n − Î(c) とする．c が none の x の範囲の外なら，Δ は None とする．
- 記録として，s = Î⁻¹(n) − c も出す．これは，同じ hit に要る none の contacted との差で，補う変種が節約した問い合わせの数に当たる．Î が単調でない区間では None とする．

**仮説と判定**
- 主の点は，device_probe，D = 12，once，a = 0.7，R = 1000，L_join = 100 である．種ごとの Δ_s で判定する．
- H-B1（補う変種が有利）：5 個の種の全てで Δ_s > 0，かつ平均 ≥ +15 問．
- H-B3（補う変種が不利）：5 個の種の全てで Δ_s < 0，かつ平均 ≤ −15 問．
- H-B2（差が無い）：5 個の種の全てで |Δ_s| ≤ 25 問．
- 判定は H-B1 → H-B3 → H-B2 の順に調べ，最初に成り立ったものを結論とする．どれも成り立たない場合と，Δ_s が None の種がある場合は「判定できない」とする．
- 成功条件（採用）：退行ゲートを全て通過し，結論が H-B1・H-B2・H-B3 のいずれかに決まること．「判定できない」なら棄却とし，D の範囲か種の数を見直す．
- 閾値の根拠：
  - 15 問は 2,940 問の約 0.51 ポイントで，限界の回収（約 25.5 問 / 台）の約 0.6 台分に当たる．25 問は約 1 台分（約 0.85 ポイント）である．
  - Iteration 18 の once と d15 の差の，種の間の標準偏差は 9.3 問である．真の差が 0 なら，5 個の種の全てが ±25 問に入る確率は約 0.96 と見込む（正規分布で近似した推測）．
- 期待：H-B2（Δ の平均は約 −2 問の見込み）．

**期待効果（推測）**
- once の D = 12 の contacted は 15.35 で，none の D = 15〜16 の間に入る．Δ は 1 種あたり −10〜+10 問の範囲と見込む．
- once の D = 9・10・11 の contacted は，それぞれ約 11.5・12.8・14.1 と見込む．根拠は，Iteration 18 の補った枠の約 72% が稼働中だったことである．いずれも none の曲線の範囲（約 9〜18）に入る．
- until_full の D = 12（contacted 16.70）は，none の D = 16〜17 の間に入る．

**退行ゲート**（1 つでも通らなければ判定しない）
- G1：D = 12 の none・once・until_full と，D = 15 の none の計 960 件を照合する．全ての条件で，`results/20261016_iter18_churn_refill_eval/metrics.json` の `runs` の同じキー（method，availability，refresh，join_delay，refill，seed）の実行と完全に一致する．照合する項目は，Iteration 18 の EXACT_FIELDS（STAT_FIELDS・COUNT_FIELDS・`n_contacted`・往復の情報）である．
- G2：a = 1.0 または R = 1 の once・until_full の 2,100 件が，同じ D・条件の none と全ての項目で完全に一致する．
- G3：全ての実行（7,200 件）で，n_hit と (i)〜(iv) の件数の和が問の数に等しい（`churn_cascade_eval.gate_g4`）．
- G4：同じ（a, R, L_join, 種）の none で，contacted が D について減らない．かつ，全ての問で返事のある候補の集合が D について入れ子になる（違反 0）．
- G5：全ての D で，全ての問の返事のある候補について none ⊆ once ⊆ until_full が成り立つ（違反 0 問．Iteration 18 の G6）．
- G6：L_join = 0・until_full の 800 件（10 D × 4 a × 4 R × 5 種）が，同じ（D, a, 種）の R = 1 の none と COUNT_FIELDS で完全に一致する（Iteration 18 の G5）．

**記録**（判定に使わない）
- none の曲線：D ごとの n_hit・contacted・wasted．主の条件に加えて，L_join ∈ {∞, 0} と a ∈ {0.9, 0.5} でも出す．
- once・until_full の D = 9〜18 の Δ と s（種ごと）．特に once の D = 9・10・11 と，until_full の D = 12．
- 問ごとの contacted の分布（once は問ごとに深さが変わる．最大値と 95 パーセンタイル），second_round_rate，往復の数の平均と最大．
- d15 との単純な差（Iteration 18 の対照の形）と，補間した Δ の両方．

**実機**：使わない．補う変種の 2 往復目の待ち時間は測れない．H-B1 なら，実機の再生（B49 A3・B51 A2）を行う理由ができる．H-B2・H-B3 なら，1 往復で D を大きくする構成を推奨し，実機の再生の優先度を下げる．

**記録の注記**：phase 1 の最後に，`Bash`・`Read` が `Unknown tool` で失敗した．`metrics.json` から主の条件の値（種ごとの contacted）を取り出す前に失敗したので，見込みは journal の Iteration 18 の表から計算した．この節・backlog の B52・config.yml の変更は，オーケストレータが戻り値から書き写した．「調べて分かったこと」の前半（B50 の要レビュー (a) への答えより上）は，戻り値の途中が省略されて届き，再送も得られなかったため，オーケストレータが戻り値に残っていた調査の要点から書き起こした．state.json は，指示により rc-researcher が触れていない．

### 事前登録（2026-10-16．実装の後，実行の前に commit・push する．結果を見て変えない．実装は commit `7a1a3fe`）

調査・計画の節の規則（単一レバー，固定する構成，比較の規則，H-B1〜H-B3 と判定の順，退行ゲート G1〜G6，記録の項目）を，実行の前にそのまま固定する．計画と実装の間に，規則の食い違いは無い．

**評価**：`atrium churn-budget-eval`（`src/atrium/churn_budget_eval.py`）を制御点のコンテナで 1 回だけ流す．出力は `results/20261016_iter19_churn_budget_eval/`（`metrics.json`・`analysis_report.md`・`run.log`）である．実機のノードは使わない．失敗しても，規則を変えて再実行しない．評価の前のインフラの失敗（制御点の `docker pull` の `crc32 mismatch` 等）は B48 と同じ扱いとし，registry の blob の健全性を確かめたうえで，同じコマンドを 1 回だけ流し直す．

```bash
ssh wafl-ctrl5 'pgrep -af "[c]hain.sh" || echo NO_CHAIN'   # 連続実行が動いていないことを確かめる
bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'
bash scripts/tasks/remote.sh churn_budget_eval 20261016_iter19_churn_budget_eval 20261009_eq5_b1 20261016_iter18_churn_refill_eval
rsync -az wafl-ctrl5:<REMOTE_DIR>/results/20261016_iter19_churn_budget_eval/ results/20261016_iter19_churn_budget_eval/
```

**照合の件数**：実行 7,200 件，G1 960 件（D = 12 の 3 つの補い方の 720 件と D = 15 の none の 240 件），G2 2,100 件，G3 7,200 件，G4 2,160 組（240 条件 × 隣り合う D の 9 組），G5 2,400 組（10 D × 240 条件），G6 800 件．

**実装の細部**（規則の読み方を固定するもの）
- 構成の名前は `device_probe_d{D}` とする．G1 は，この名前と（availability，refresh，join_delay，refill，seed）で Iteration 18 の `runs` と照合する．照合する項目は，Iteration 18 の `EXACT_FIELDS`（STAT_FIELDS・COUNT_FIELDS・`n_contacted`・往復の情報）である．加えて，`m`・`n_test` の一致と，両側の欠けが 0 件であることを求める．
- G2 は `churn_refill_eval.gate_g3` を使わず，新しく書いた．理由は，`gate_g3` が `device_probe_d15` を対照として照合から外すためである．今回の d15 は格子の 1 点なので，照合に含める．
- G3 は `churn_cascade_eval.gate_g4` をそのまま使う．
- G4 の contacted は `n_contacted`（問の和）で比べる．入れ子は，none の返事のある候補（受信箱の印）で，昇順に隣り合う D の間で調べる．
- G5 は，Iteration 18 の `gate_g6` に D ごとの照合を渡す．G6 の基準は，同じ（D, a, 種）の R = 1・L_join = ∞・none の実行で，COUNT_FIELDS を照合する（contacted・wasted は照合しない）．
- 折れ線の x は，none の `contacted`（問あたりの平均）である．x の等しい点は，n_hit の最大値を使う．Δ は外挿しない（c が範囲の外なら None）．s は，Î(x) = n の解がただ 1 つのときだけ出す．頂点で等しい点と区間の内側の交点を数え，平らな区間で等しい場合と解が 2 つ以上の場合は None とする．
- 問ごとの contacted の 95 パーセンタイルは，`numpy.percentile` の既定（線形補間）で計算する．記録の表では，最大は種の最大，95 パーセンタイルは種の平均で示す．d15 との単純な差は，同じ（a, R, L_join, 種）の `device_probe_d15` の none の n_hit との差である．

**実装の検査**（commit `7a1a3fe`）
- `tests/test_churn_budget_eval.py` の 23 件が通過した．全体の pytest は 512 件が全て通過した．`ruff check src tests`・`ruff format --check src tests`・`mypy` は通過した．
- テストで，折れ線（等しい x の最大値，範囲の外の None，逆関数の一意性・頂点・平らな区間），判定の順（H-B1 → H-B3 → H-B2，None の種），ゲートが不通過を拾うこと（G1 の値・欠け・n_test，G2，G4 の減少と入れ子，G6）を確かめた．
- 合成のデータ（18 ノード・18 デバイス・36 受信箱，300 刻み，乱数の得点）で，集計・ゲート・報告の流れを最後まで通した．実行 7,200 件，G1 960 件，G2 2,100 件，G4 2,160 組，G5 2,400 組，G6 800 件で，G1〜G6 は全て通過した．

### 実装・実験（2026-10-16．research-cycle の phase 2．数値の規則の機械的な出力のみ．解釈・判定は phase 3）

- 実装：commit `7a1a3fe`（`src/atrium/churn_budget_eval.py`，`tests/test_churn_budget_eval.py`，`scripts/remote/churn_budget_eval.sh`，`cli.py` の `churn-budget-eval`，README・d0001・d0003・d0004）．事前登録：commit `bbf33a4`（実行の前に push 済み）．結果：この節と同じ commit（`results/20261016_iter19_churn_budget_eval/`．`metrics.json`・`analysis_report.md`・`run.log`）．
- 検査：事前登録の「実装の検査」のとおり（新しいテスト 23 件と全体の pytest 512 件が全て通過，`ruff check src tests`・`ruff format --check src tests`・`mypy` が通過）．実装の途中で mypy の strict の型エラーが 3 件出たので，commit の前に直した（比較の結果を `bool()` で包む 2 件と，`judge_hb` の Δ の平均で None を除いた list を `np.mean` に渡す 1 件．値は変わらない）．
- 実行：制御点に G1 の基準（`results/20261016_iter18_churn_refill_eval/metrics.json`）と `results/20261009_eq5_b1/placement.json` があること，`NO_CHAIN` を確かめてから publish した（242 秒．イメージの版は `bbf33a47f362-dirty`．dirty は `state.json` の変更と未追跡のファイルによる）．`remote.sh churn_budget_eval` は終了コード 0，pull を含めて 288 秒，評価の本体（`duration_s`）は 51.2 秒である．1 回で終わり，流し直しはしていない．実行は 7,200 件で，失敗は 0 件．`run.log` は 380 行で，WARNING は 128 行，全て faiss の k-means の「学習点が少ない」の警告である（Iteration 16〜18 と同じ件数）．冒頭の `ModuleNotFoundError("No module named 'faiss.swigfaiss_avx2'")` は faiss の loader の INFO の行で，AVX2 無しの版を読み込んで続いている．
- `metrics_cmd`（`uv run atrium metrics --json`）は，Iteration 16〜18 と同じく `run_meta.json` のある最新の実行（`20261016_iter14_smoke_dp12_pe_n8`）を読んだ．数値は `metrics.json` から取った．
- 退行ゲート：全て通過した．
  - G1：960 件 / 期待 960 件を照合し，不一致 0 件，こちらの欠け 0 件，基準の欠け 0 件，`m`・`n_test` は一致．
  - G2：2,100 件 / 期待 2,100 件を照合し，不一致 0 件．
  - G3：7,200 件を照合し，違反 0 件．
  - G4：条件 240 個，入れ子の照合 2,160 組 / 期待 2,160 組，contacted の減少 0 件，入れ子の違反 0 問．
  - G5：2,400 組 / 期待 2,400 組を照合し，違反 0 問．
  - G6：800 件 / 期待 800 件を照合し，不一致 0 件．
- 主の点（`device_probe_d12`，once，a = 0.7，R = 1000，L_join = 100）：機械的な判定は H-B2．Δ の平均は −1.49 問．

| 種 | n_hit | contacted | Î(c) | Δ（問） | s（問あたりの問い合わせ） | d15 の none との差（問） |
|---|---|---|---|---|---|---|
| 0 | 1,485 | 15.1463 | 1,487.49 | −2.49 | −0.1463 | +0 |
| 1 | 1,479 | 15.2289 | 1,482.35 | −3.35 | −0.1763 | +1 |
| 2 | 1,482 | 15.1735 | 1,484.47 | −2.47 | −0.1235 | +1 |
| 3 | 1,503 | 15.6408 | 1,510.02 | −7.02 | −0.2808 | +9 |
| 4 | 1,468 | 15.5439 | 1,460.14 | +7.86 | +0.3023 | +22 |

- 記録：a = 0.7・R = 1000・L_join = 100 の Δ（種 0〜4．括弧は種の平均）．
  - once の D = 9：+2.22・−11.02・−6.86・−3.28・−3.58（−4.50）．contacted の平均 11.52．
  - once の D = 10：−3.84・−4.89・−0.82・−15.70・+6.89（−3.67）．contacted の平均 12.79．
  - once の D = 11：−1.06・+3.03・+2.28・−11.67・+3.92（−0.70）．contacted の平均 14.07．
  - once の D = 13：+2.40・−0.53・+6.89・−1.09・−2.03（+1.13）．D = 14 は種 3・4 が None，D = 15〜18 は全ての種で None（contacted が none の範囲の外）．
  - until_full の D = 12：+5.57・−10.01・+7.46・−1.06・+1.59（+0.71）．contacted の平均 16.70．s は +0.3976・−0.6728・+0.6197・−0.0486・+0.0610．d15 の none との差は +27・+15・+32・+45・+46．
  - until_full の D = 9〜11 の Δ の平均は −7.46・−4.51・−4.60．D = 13 は種 3・4 が None，D = 14〜18 は全ての種で None．
- 記録：none の曲線（a = 0.7，R = 1000，L_join = 100）の contacted は，D = 9〜17 で全ての種が D に等しく，D = 18 では 17.9656・18.0000・18.0000・17.9646・18.0000（見えているデバイスが 18 台に満たない問がある）．n_hit の種の範囲は D = 9 で 1,278〜1,325，D = 12 で 1,371〜1,422，D = 15 で 1,446〜1,494，D = 18 で 1,515〜1,556．wasted の平均は D = 9 で 2.52，D = 12 で 3.35，D = 18 で 5.01．
- 記録：問ごとの contacted と往復（a = 0.7，R = 1000，L_join = 100，D = 12）．once は最大 23（種の最大），95 パーセンタイル 18.20（種の平均），2 往復目の要る問の割合 0.9644，往復の数の平均 1.964・最大 2．until_full は最大 30，95 パーセンタイル 21.40，往復の数の平均 2.932・最大 10．
- 記録：他の条件の主の点の Δ の平均（D = 12，once）．L_join = ∞ は +1.76，L_join = 0 は −1.14，a = 0.9（L_join = 100）は −2.62．a = 0.5（L_join = 100）は種 3 が None（Δ は −0.16・−1.22・+1.34・None・−2.18）．
- 計画からの逸脱：規則・閾値・ゲート・記録の項目の変更は無い．流し直しは無い．

### Iteration 19 実行済み：分析・考察（2026-10-16．research-cycle の phase 3．判定：採用．結論は H-B2）

**変更**：新しいオフラインの CLI `atrium churn-budget-eval` で，device_probe の 1 段目の台数 D を 9〜18 に広げ，全ての D で refill ∈ {none, once, until_full} を流した（実装 `7a1a3fe`，事前登録 `bbf33a4`，結果 `a48b1ef`）．config.yaml・データ・シャード・既存の routing の挙動は変えていない．事前登録の規則・閾値は，結果を見て変えていない．

**照合**（rc-evaluator が `metrics.json` を直接読んだ．Iteration 18 では行えなかった照合である）
- `hb` の種ごとの Δ・平均・結論は，上の「実装・実験」の表と一致した．
- `runs` から，none の D = 9〜18 の折れ線を作り直した（x の等しい点は n_hit の最大値を使い，`numpy.interp` で補間した）．主の点の Δ を独立に計算すると，−2.49・−3.35・−2.47・−7.02・+7.86（平均 −1.493）で，`hb` と一致した．
- `gates` の G1〜G6 は全て `passed: true` で，不一致・違反の list は空である．`runs` は 7,200 件で，キー（method，availability，refresh，join_delay，refill，seed）の重複は無い．

**結果と判定**
- 事前登録の順に当てはめた．
  - H-B1：4 個の種で Δ が負なので，成り立たない．
  - H-B3：種 4 の Δ が +7.86 なので，成り立たない．
  - H-B2：|Δ| の最大は 7.86 問で，25 問以下なので成り立つ．None の種は無い．
- **このイテレーションの結論：採用（結論は H-B2「差が無い」）**．退行ゲートを全て通過し，結論が決まったので，事前登録の成功条件を満たす．
- 採用の意味（B52 の要レビュー (b)）：採用は，「同じ問い合わせの数では，返事の無い枠を補う変種と，補わない構成の hit に差が無い」という結論が決まったことを表す．「補う変種が有効」という意味ではない．

**分析**

事実（`metrics.json` から確かめたもの）
1. **主の点の Δ はノイズの範囲にある**．Δ の種の間の標準偏差は 5.55 問で，平均 −1.49 問の標準誤差は約 2.48 問（t ≈ −0.60）である．平均は 0 から標準誤差の 1 倍も離れていない．H-B1・H-B3 の閾値（±15 問）とは 1 桁離れている．
2. **主の点だけの結果ではない**．a < 1 かつ R > 1 の 135 組（3 a × 3 R × 3 L_join × 5 種）で，once の D = 12 の Δ は，平均 −0.51 問・標準偏差 3.83 問（範囲 −11.6〜+12.2．None は 1 組）である．条件ごとの種の平均は −2.77〜+1.76 問に収まる．135 組をまとめた D ごとの平均は，once（D = 9〜17）で −1.25〜+0.09 問，until_full（D = 9〜17）で −2.02〜−0.01 問である．主の条件の once の D = 9・10（−4.50・−3.67 問）のように，1 つの条件の種の平均が 3 問を超える点もあるが，閾値の 15 問には届かない．
3. **単純な比較は，contacted の差の分だけ補う変種に有利に偏っていた**．until_full の D = 12 は，d15 の none との単純な差が +27〜+46 問だが，補間した Δ の平均は +0.71 問である．once の D = 12 の単純な差（+0〜+22 問．Iteration 18 の対照の形）も，補間した Δ では平均 −1.49 問になる．
4. **費用**：none は全ての問で 1 往復である．once は 96.4% の問に 2 往復目を送る（往復の数の平均 1.964）．until_full の往復の数は，平均 2.932・最大 10 である．同じ hit は，D を once の contacted（約 15.35）程度にした none で，1 往復で得られる．
5. **none の曲線**（a = 0.7，R = 1000，L_join = 100）：D を 1 台増やしたときの n_hit の増分は，D = 9 → 10 で平均 31.0 問，D = 15 → 18 で平均 19.9 問と減る．隣り合う D の差は，種ごとに 11〜38 問で揺れる．
6. 調査・計画の見込みと一致した．Δ の平均は約 −2 問の見込みに対し −1.49 問だった．once の D = 9・10・11 の contacted は，見込み 11.5・12.8・14.1 に対し，実測は 11.52・12.79・14.07 だった．

推測（未確認）
- 曲線はやや凹なので，折れ線の補間は真の曲線より低く出る．そのため Δ は，補う変種に有利な側へわずかに偏る（1 区間で約 1 問以下と見積もる）．偏りを除いても，結論は変わらない．
- 返事のある候補の数が同じなら，hit は，候補を 1 往復で得たか 2 往復で得たかに依らないと読める（G5 の入れ子と Δ ≈ 0 による．問ごとの候補の集合の一致率は測っていない）．

**想定外の挙動**：無し（オフラインの評価のため，言語崩れ・発散・OOM は該当しない）．`run.log` の WARNING 128 行は，faiss の k-means の警告で，Iteration 16〜18 と同じ件数である．

**学び**
- 問い合わせの数が違う構成の hit は，同じ contacted にそろえてから比べる．Iteration 18 で観測した d15 との差は，contacted の差でほぼ説明でき，補間した Δ では 0 に近い．
- 古いメンバー表の下で返事の無い枠を補っても，最初から同じ数のデバイスへ送る構成に対して，hit の利点は無い．補う変種は約 96% の問で 2 往復目を要するので，待ち時間の点では，1 往復で D を大きくする構成が有利である（待ち時間は測っていない）．
- 論文には，補う変種を勧めないと書く．D は，none の (contacted, hit) の曲線から選ぶ．
- 固定の D の曲線は，D の全域で 1 台あたり約 20〜31 問の回収がある．この曲線を同じ contacted で上回るには，候補の数ではなく，問ごとの候補の選び方を変える必要がある（次の一手の根拠）．

**限界**
- オフラインの評価で，待ち時間は測っていない．返事の無いことは，送った時点で分かると仮定した．時間切れを待つ場合，補う変種の費用はさらに大きい．
- 判定は，device_probe の a = 0.7・R = 1000・L_join = 100 の 1 点と，種 5 個による．centroid_probe では評価していない．
- once の D ≥ 14 は，contacted が none の範囲（最大 18）を超え，Δ を出せない種がある．

**次の一手**（backlog B54 [auto-decided]）：config.yml の levers を使い切ったので，新しいレバー `churn_eval.device_probe_cutoff`（仮称）を末尾に足した．問ごとに probe の台数を，1 段目の得点の分布から決める方式（selective search の Rank-S・Taily に当たる）を，固定の D の none の曲線と同じ contacted で比べる．

## Iteration 18: 返事の無いデバイスの枠を次の順位で補ったときの hit と問い合わせの費用

### 調査・計画（2026-10-16．research-cycle の phase 1）

**直近の判定**：Iteration 17 は採用である（G1〜G5 を通過し，H-L1 を支持した．`device_probe_d12` の ρ_leave は 0.839〜0.883．commit `96c4516`）．config.yml の levers は使い切っている．B49 [auto-decided] で，次のレバーを「probe に返事の無かったデバイスの枠を，1 段目の次の順位で補う変種」に決めた．キー名・補う回数・主の条件・判定の規則は，今回に決めた（B50 [auto-decided]）．

**調べて分かったこと**
- コードから確かめた事実は，次のとおりである（`churn_cascade_eval.py` の 120〜213 行，`churn_join_eval.py` の 95〜117 行，`churn_eval.py` の 54〜126 行，`device_probe_eval.py` の 64〜96 行を Read で確かめた）．
  - `visible_top_devices` は，見えていない受信箱を −inf にしてから，デバイスごとの最大値（`device_scores`）でまとめる．その後，`argsort(kind="stable")` で上位 d 台を取る．
  - 同点は，デバイスの番号の小さい方を先にする．デバイスの番号は，受信箱の名前の初出順である（`device_index_of_inbox`）．
  - `visible_top_k` も，−inf にした stable な並べ替えで，同点は受信箱の番号の小さい方を先にする．
  - `route_cascade_under_churn` は，返事のあった候補（候補 ∧ 稼働中）の中で，2 段目の上位 m 個を選ぶ．contacted は probe を送ったノードの数，wasted はそのうち離脱していたノードの数である．
- G5 は構造上成り立つ．
  - L_join = 0 の `join_notified_view` は，時刻 t に稼働中の全ノードを含む．t に稼働中のノードは，u(t) に稼働中だったか，区間 (u(t), t] に参加したかのどちらかだからである．
  - このため，「見えていて稼働中」は「稼働中」と同じ集合になり，R = 1 の表と一致する．
  - −inf で隠しても，残る候補の間の前後は変わらない（stable な並べ替え）．よって，until_full で返事のある候補は，R = 1 の上位 D 台（または上位 K 個）と一致する．
  - 正解が稼働中なら，正解は見えている．このため，hit と (i)〜(iv)・候補外・上位 m 外の件数は一致する．contacted と wasted は一致しないので，G5 の照合から外す．
- G6 も構造上成り立つ．once で返事のある候補は，見えている順位の 1〜D + f 番目のうち稼働中のものである．その数は D 以下で，順位の上から取っている．よって，until_full の「稼働中の上位 D 個」に含まれる．
- 先行研究は，記憶によるもので，Web の検索では確かめていない．
  - Dean & Barroso, "The Tail at Scale", CACM 56(2), 2013 は，複製へ重ねて送る hedged request を述べる．
  - atrium の受信箱は複製を持たない．このため，補う変種は「次の順位の別のデータ源を選び直す」ことになり，hedge とは異なる．
  - Kulkarni & Callan, "Selective Search", ACM TOIS 33(4), 2015 は，資源選択による分散検索の研究である．返事の無いシャードの扱いは，確かめていない．

**単一レバー**：`churn_eval.probe_refill` を，none（今の挙動）から {once, until_full} へ変える．新しい CLI `atrium churn-refill-eval`（`src/atrium/churn_refill_eval.py`）で実行する．
- once は，1 段目の上位 D 台（centroid_probe では上位 K 個の受信箱）のうち，返事の無かった f 個の枠を補う．補う相手は，見えている順位の D + 1〜D + f 番目で，補うのは 1 回だけである（最大 2 往復）．
- until_full は，返事のある候補が D 個（または K 個）に揃うか，見えている候補が尽きるまで補う．実装は，「見えていて稼働中の受信箱」での `visible_top_*` と同じ集合にする．
- contacted は 2 往復の probe の先の和集合，wasted はそのうち稼働していないノードの数とする．
- 返事の無いことは，probe を送った時点で分かるとする（接続の拒否に当たる．時間切れの待ちは模さない）．2 段目と，hit・(i)〜(iv) の規則は変えない．
- 1 段目だけの方式（multi_centroid），flood_score，全数の構成（`centroid_probe_k150`・`device_probe_d30`）には補う相手が無いので，none と同じ値になる（G2）．
- 1 刻みは 1 問である．秒への換算と往復の待ち時間の換算は行わない．

**固定する構成**
- 変えないもの：config.yaml，データ，シャード，配置，m = 2，C = 32，`AVAILABILITIES`・`REFRESH_STEPS`・`MEAN_UP_STEPS`・`N_SCHEDULE_SEEDS`・`experiment.seed` = 12，採点・質問の順番．
- メンバー表は Iteration 16 の `join_notified_view(online, R, L_join)` とし，離脱は通知しない．
- 構成は Iteration 16 と同じ 8 個とする（multi_centroid，flood_score，`centroid_probe_k75`，`centroid_probe_k30`，`device_probe_d24`，`device_probe_d12`，全数の `centroid_probe_k150`・`device_probe_d30`）．
- 条件は a ∈ {1.0, 0.9, 0.7, 0.5}，R ∈ {1, 10, 100, 1000}，L_join ∈ {∞, 0, 100}，refill ∈ {none, once, until_full}，種 5 個である．
- 記録だけの対照として，`device_probe_d15`（none のみ）を足す．once と同じ程度の問い合わせの数で 1 往復にした場合に当たる．
- 実行の数は，8 × 4 × 4 × 3 × 3 × 5 = 5,760 件と対照の 4 × 4 × 3 × 5 = 240 件で，計 6,000 件である．

**仮説 H-P1**：L_join = 100 の表の上で once にすると，`device_probe_d12` の a = 0.7・R = 1000 で，hit の低下の 20% 以上が 5 個の種の全てで戻る．
- ρ_refill = (n_hit(once) − n_hit(none)) / (n_hit(R=1) − n_hit(none)) とする．分子と分母の n_hit(once)・n_hit(none) は，R = 1000・L_join = 100 の値である．
- n_hit(R = 1) には，none・L_join = ∞ の R = 1 の実行を使う（R = 1 では表が稼働中の集合そのものになり，どの条件でも同じになる）．
- 分母は，種ごとに 164・139・144・155・166 問になる見込みである（G1 で確かめる）．

**期待効果（推測）**
- ρ_refill(once) ≈ 0.72 × 392 / 768 ≈ 0.37 と見込む．until_full の上限は約 392 / 768 ≈ 0.51 である．(ii)（稼働中なのに見えない）の 376 問は，補っても戻らない．
- L_join = 0 では，ρ_refill(once) ≈ 0.72，ρ_refill(until_full) = 1 と見込む．後者は構造上そうなる（G5）．
- contacted は 12.00 から約 15.35 に，wasted は 3.35 から約 4.29 に増える見込みである．
- 2 往復目が要る問の割合は，枠ごとに独立と仮定すると 1 − 0.72^12 ≈ 0.98 と見込む．この値は時刻表の試算で確かめていない．
- 対照の `device_probe_d15`（1 往復）は，返事のある候補が約 10.8 台の見込みで，once との hit の差は小さい可能性がある．
- 枠の取り合いと上位 m の入れ替わりは線形でないので，見込みは外れうる．

**退行ゲート**（1 つでも通らなければ H-P1 を判定しない）
- G1：none の L_join ∈ {∞, 0, 100} の 1,920 件が，`results/20261016_iter16_churn_join_eval/metrics.json` の `runs` の delay = ∞・0・100 の実行と，hit・hit_given_gold_online・gold_online_rate・contacted・wasted と全ての件数（n_test で割った差）で 1/2940 以内に一致する．どちらにも欠けが無く，件数の None の有無も一致する．m と n_test も一致する．
- G2：補う相手の無い 4 構成（multi_centroid・flood_score・`centroid_probe_k150`・`device_probe_d30`）の none 以外の 1,920 件が，同じ条件の none と全ての項目で完全に一致する．
- G3：a = 1.0 または R = 1 の none 以外の 1,680 件（8 構成）が，同じ条件の none と全ての項目で完全に一致する．
- G4：全ての実行（6,000 件）で，`n_hit` と (i)〜(iv) の件数の和が問の数に等しい（`churn_cascade_eval.gate_g4`）．
- G5：L_join = 0・until_full の二段の 4 構成の 320 件が，同じ（構成，a，種）の R = 1 の実行と，`n_hit`・(i)〜(iv)・候補外・上位 m 外の件数で完全に一致する．主の判定には使わない．
- G6：二段の構成の全ての実行の全ての問で，返事のある候補の集合について「none ⊆ once ⊆ until_full」が成り立つ（違反 0 問）．

**判定の規則と成功条件**
- G1〜G6 を全て通過し，かつ `device_probe_d12` の a = 0.7・R = 1000・L_join = 100・once の ρ_refill が 5 個の種の全てで 0.20 以上なら，H-P1 を支持し，採用とする．分母が 0 以下の種は不成立とする．問の数は実行によらず等しいので，件数の比は hit の比に等しい．
- 閾値の 0.20 は，見込みの 0.37 から，種の間の標準偏差の推定（約 0.07）の約 2.4 倍を引いた値である．標準偏差の推定は，Iteration 16 の 0.045 を分母の大きさの比 √(400/150) で広げたものである．

**記録**（判定に使わない）
- 二段の 4 構成の a = 0.7・R = 1000・L_join ∈ {∞, 0, 100} の ρ_refill（once・until_full．種ごと），hit（種ごと），contacted・wasted（種の平均）．
- 往復の情報：second_round_rate，1 問あたりの往復の数の平均と最大，1 問あたりの probe の要求の数．
- 対照：`device_probe_d15`（none）と `device_probe_d12`（once）の hit の種ごとの差と contacted，回復した問の数 / 増えた問い合わせの数．
- E-2 相当：主の条件の once で，(iii) の候補外・上位 m 外の件数の増分（種ごと）．
- a = 0.9・0.5 の `device_probe_d12` の ρ_refill（R = 1000，L_join = 100）．
- 遷移表：a = 0.7・R = 1000・L_join = 100 の（二段の 4 構成，refill，種）ごとに，R = 1 の区分（hit・(i)〜(iv)）から R = 1000 の区分への 5 × 5 の件数（60 個）．

**実機**：使わない．制御点のコンテナで実行するオフライン評価である．2 往復目の待ち時間の費用はオフラインでは測れないので，実機の再生（B49 A3）は次の候補に残す．

**記録の注記**：phase 1 では，1 度目に `Read`，再開した 2 度目に `Bash` と `Write` が `Unknown tool` で失敗した．そのため，tavily の検索，`/tmp` での試算，ファイルへの書き込みは行えていない．この節と backlog の B50・config.yml の note は，オーケストレータが戻り値から書き写した．コードの確認は `Read` で行った．文献の書誌と，見込みの数値（約 28%・約 0.98）は未確認である．state.json は指示により触れていない．

### 事前登録（2026-10-16．実装の後，実行の前に commit・push する．結果を見て変えない．実装は commit `a65e41b`）

調査・計画の節の規則（単一レバー，固定する構成，H-P1，退行ゲート G1〜G6，判定の規則，記録の項目）を，実行の前にそのまま固定する．計画と実装の間に規則の食い違いは無い（backlog への新しい記録は無い）．

**評価**：`atrium churn-refill-eval`（`src/atrium/churn_refill_eval.py`）を制御点のコンテナで 1 回だけ流す．出力は `results/20261016_iter18_churn_refill_eval/`（`metrics.json`・`analysis_report.md`・`run.log`）．実機のノードは使わない．失敗しても規則を変えて再実行しない．評価の前のインフラの失敗（制御点の `docker pull` の `crc32 mismatch` 等）は B48 と同じ扱いとし，registry の blob の健全性を確かめたうえで同じコマンドを 1 回だけ流し直す．

```bash
ssh wafl-ctrl5 'pgrep -af "[c]hain.sh" || echo NO_CHAIN'   # 連続実行が動いていないことを確かめる
bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'
bash scripts/tasks/remote.sh churn_refill_eval 20261016_iter18_churn_refill_eval 20261009_eq5_b1 20261016_iter16_churn_join_eval
rsync -az wafl-ctrl5:<REMOTE_DIR>/results/20261016_iter18_churn_refill_eval/ results/20261016_iter18_churn_refill_eval/
```

**照合の件数**：実行 6,000 件（8 構成 5,760 件 + 対照 240 件），G1 1,920 件（L_join = ∞・0・100 の各 640 件），G2 1,920 件，G3 1,680 件，G4 6,000 件，G5 320 件，G6 1,440 実行（二段の 6 構成．全数の 2 構成を含む），遷移表 60 個．

**実装の細部**（規則の読み方を固定するもの）
- ρ_refill の分母の n_hit(R=1) は，L_join に依らず none・L_join = ∞ の R = 1 の実行を使う．G5 の基準・E-2 相当の R = 1・遷移表の基準も同じ実行である．
- G1 は L_join ごとに Iteration 16 の同じ `delay` の `runs` だけと照合する（`churn_join_eval.gate_g1` を L_join ごとに呼ぶ）．G2・G3 の「全ての項目」は，STAT_FIELDS・COUNT_FIELDS・`n_contacted`・往復の情報（`second_round_rate`・`rounds_mean`・`rounds_max`・`probe_requests`・`n_probe_requests`）である．G3 は対照を除く．G5 は COUNT_FIELDS（`n`・`n_hit`・(i)〜(iv)・候補外・上位 m 外）を照合し，contacted・wasted は照合しない．
- 補う相手は見えている順位だけで決め，前の往復で返事の無かったノードに載る受信箱も飛ばさない（centroid_probe だけで起きる）．probe の要求の数は，往復ごとに新たに候補に入った単位の載るノードの数の和とする．
- 対照の「回復した問の数 / 増えた問い合わせの数」は，`device_probe_d12` の none に対する `n_hit` の増分 / `n_contacted`（問の和）の増分で，分母が 0 以下なら `null` とする．E-2 相当は，(iii) の候補外・上位 m 外に加えて，(i)〜(iv) の件数の増分を R = 1 と同じ条件の none の両方に対して記録する．
- `analysis_report.md` の表では，G2・G3 で none と同じことを確かめる行（補う相手の無い構成，a = 1.0，R = 1 の none 以外）を省く．`metrics.json` には全ての実行を残す．構成の対照の名前は `metrics.json` の `control_cascade`，対照の記録は `control` に置く．

**実装の検査**（commit `a65e41b`）
- `tests/test_churn_refill_eval.py` の 48 件が通過した．全体の pytest は 489 件が全て通過した．`ruff check src tests`・`ruff format --check src tests`・`mypy` は通過した．
- テストで，none が `route_cascade_under_churn` と一致すること（G1），until_full が「見えていて稼働中」の上位と一致すること，L_join = 0・until_full が R = 1 と件数で一致すること（G5），none ⊆ once ⊆ until_full（G6），離脱が無ければ補い方に依らず同じこと（G3），手で作った返事の有無での往復の境目，ゲートと判定が不通過を拾うことを確かめた．
- 合成のデータ（16 ノード・16 デバイス・32 受信箱，300 刻み，乱数の得点）で，集計・ゲート・報告の流れを最後まで通した．実行 6,000 件，G1 1,920 件，G2 1,920 件，G3 1,680 件，G5 320 件，G6 1,440 実行，遷移表 60 個で，G1〜G6 は全て通過した．

### 実装・実験（2026-10-16．research-cycle の phase 2．数値の規則の機械的な出力のみ．解釈・判定は phase 3）

- 実装：commit `a65e41b`（`src/atrium/churn_refill_eval.py`，`tests/test_churn_refill_eval.py`，`scripts/remote/churn_refill_eval.sh`，`cli.py` の `churn-refill-eval`，README・d0001・d0003・d0004）．事前登録：commit `1da9320`（実行の前に push 済み）．結果：commit `ddf999a`（`results/20261016_iter18_churn_refill_eval/`．`metrics.json`・`analysis_report.md`・`run.log`．push 済み）．
- 検査：事前登録の「実装の検査」のとおり（pytest 489 件が全て通過，`ruff check src tests`・`ruff format --check src tests`・`mypy` が通過）．実装の途中で，構成の対照の名前と対照の記録が同じキー `control` で上書きされる不具合を見つけ，commit の前に構成の側を `control_cascade` に直した（事前登録の「実装の細部」に記載）．
- 実行：`NO_CHAIN` を確かめてから publish した（234 秒．イメージの版は `1da9320c7ce2-dirty`．dirty は未追跡のファイルによる）．`remote.sh churn_refill_eval` は終了コード 0，pull を含めて 297 秒，評価の本体（`duration_s`）は 61.2 秒である．1 回で終わり，流し直しはしていない．実行は 6,000 件で，失敗は 0 件．遷移表は 60 個．`run.log` は 1,162 行で，WARNING は 128 行，全て faiss の k-means の「学習点が少ない」の警告である（Iteration 16・17 と同じ件数）．
- `metrics_cmd`（`uv run atrium metrics --json`）は，Iteration 16・17 と同じく `run_meta.json` のある最新の実行（`20261016_iter14_smoke_dp12_pe_n8`）を読んだ．数値は `metrics.json` から取った．
- 退行ゲート：全て通過した．
  - G1：1,920 件（L_join = ∞・0・100 の各 640 件）を照合し，こちらの欠け 0 件，基準の欠け 0 件，None の不一致 0 件，差の最大 0.0（許容 1/2940）．
  - G2：1,920 件 / 期待 1,920 件を照合し，不一致 0 件．
  - G3：1,680 件 / 期待 1,680 件を照合し，不一致 0 件．
  - G4：違反 0 件．
  - G5：320 件 / 期待 320 件を照合し，不一致 0 件．
  - G6：1,440 実行 / 期待 1,440 実行を照合し，違反 0 問．
- H-P1（`device_probe_d12`，a = 0.7，R = 1000，L_join = 100，once）：機械的な判定は「支持」．

| 種 | n_hit（R = 1） | n_hit（none） | n_hit（once） | 分母（問） | 回復（問） | ρ_refill |
|---|---|---|---|---|---|---|
| 0 | 1,561 | 1,397 | 1,485 | 164 | 88 | 0.5366 |
| 1 | 1,542 | 1,403 | 1,479 | 139 | 76 | 0.5468 |
| 2 | 1,552 | 1,408 | 1,482 | 144 | 74 | 0.5139 |
| 3 | 1,577 | 1,422 | 1,503 | 155 | 81 | 0.5226 |
| 4 | 1,537 | 1,371 | 1,468 | 166 | 97 | 0.5843 |

- 記録：二段の 4 構成の a = 0.7・R = 1000 の ρ_refill（種の最小〜最大．基準は同じ L_join の none）と，probe の要求の数（種の平均．none / once / until_full）．

| 構成 | L_join | ρ_refill（once） | ρ_refill（until_full） | probe の要求 | 2 往復目の要る問の割合 |
|---|---|---|---|---|---|
| centroid_probe_k75 | ∞ | 0.033〜0.105 | 0.039〜0.126 | 20.77 / 33.33 / 37.05 | 0.984 |
| centroid_probe_k75 | 0 | 0.660〜0.844 | 1.000 | 28.40 / 43.63 / 52.05 | 0.989 |
| centroid_probe_k75 | 100 | 0.280〜0.425 | 0.406〜0.529 | 27.55 / 42.65 / 50.84 | 0.986 |
| centroid_probe_k30 | ∞ | 0.061〜0.107 | 0.089〜0.137 | 16.90 / 23.90 / 27.66 | 0.981 |
| centroid_probe_k30 | 0 | 0.623〜0.804 | 1.000 | 19.62 / 27.00 / 30.71 | 0.985 |
| centroid_probe_k30 | 100 | 0.301〜0.455 | 0.425〜0.581 | 19.31 / 26.70 / 30.48 | 0.983 |
| device_probe_d24 | ∞ | 0.000〜0.019 | 0.000〜0.019 | 20.63 / 20.83 / 20.83 | 0.068 |
| device_probe_d24 | 0 | 0.919〜1.022 | 1.000 | 23.91 / 28.79 / 29.01 | 0.951 |
| device_probe_d24 | 100 | 0.299〜0.420 | 0.299〜0.455 | 23.57 / 27.92 / 28.11 | 0.856 |
| device_probe_d12 | ∞ | 0.099〜0.164 | 0.118〜0.220 | 12.00 / 15.31 / 16.41 | 0.965 |
| device_probe_d12 | 0 | 0.686〜0.830 | 1.000 | 12.00 / 15.32 / 16.66 | 0.964 |
| device_probe_d12 | 100 | 0.514〜0.584 | 0.647〜0.755 | 12.00 / 15.35 / 16.70 | 0.964 |

- 記録：`device_probe_d12` の probe の要求の数は contacted と同じ値である（デバイスごとに 1 要求）．L_join = 100 の wasted は none / once / until_full で 3.35 / 4.31 / 4.70．往復の数の平均は once 1.964・until_full 2.932，最大は 2・10．centroid_probe では contacted より probe の要求の数が大きい（k75・L_join = 100 の once で contacted 28.11，要求 42.65）．
- 記録：`device_probe_d12` の他の a（R = 1000，L_join = 100，once．種 0〜4）．a = 0.9 は 0.717・0.767・0.731・0.630・0.741（分母 53・60・52・54・54 問），a = 0.5 は 0.353・0.355・0.381・0.373・0.463（分母 187・220・194・217・205 問）．until_full は a = 0.9 で 0.667〜0.817，a = 0.5 で 0.532〜0.659．
- 対照（a = 0.7，R = 1000，L_join = 100．種 0〜4）：`device_probe_d12` の once と `device_probe_d15` の none の n_hit の差は 0・+1・+1・+9・+22 問．contacted は once 15.15〜15.64，対照 15.00．回復した問の数 / 増えた問い合わせの数は once 0.0076〜0.0095，対照 0.0082〜0.0100．
- E-2 相当（`device_probe_d12`，once，a = 0.7，R = 1 → 1000，L_join = 100．種 0〜4）：残る低下は 76・63・70・74・69 問，回復は 88・76・74・81・97 問．R = 1 に対する (ii) の増分は +83・+94・+60・+69・+70．none に対する増分は，候補外が −133・−108・−121・−125・−145，上位 m 外が +45・+32・+47・+44・+48 で，(i)・(ii)・(iv) は 0．
- 遷移表（a = 0.7，R = 1（none・L_join = ∞）→ R = 1000・L_join = 100，種の和，各 14,700 問．R = 1 の (ii)・(iv) は 0 件．(i) → (i) は全てで 4,398 件．hit → (ii) と (iii) → (ii) は補い方に依らない）：
  - `device_probe_d12`：none は hit → hit 6,911・→ (ii) 281・→ (iii) 577，(iii) → hit 90・→ (ii) 95・→ (iii) 2,348．once は hit → hit 7,354・→ (iii) 134，(iii) → hit 63・→ (iii) 2,375．until_full は hit → hit 7,482・→ (iii) 6，(iii) → hit 67・→ (iii) 2,371．
  - hit → (iii) / (iii) → hit（none / once / until_full）：`centroid_probe_k75` は 306・121 / 79・58 / 3・45，`centroid_probe_k30` は 293・55 / 85・38 / 6・36，`device_probe_d24` は 248・89 / 7・24 / 0・21．
- 計画からの逸脱：規則・閾値・ゲート・記録の項目の変更は無い．流し直しは無い．
- 記録の注記：この節は，rc-executor への指示で journal には事前登録の節だけを書かせたため，オーケストレータが戻り値と `analysis_report.md` から書き写した．戻り値の E-2 相当の「none に対する候補外の増分」の範囲（−133〜−108）は誤りで，`analysis_report.md` の値（−145〜−108）に直した．

### Iteration 18 実行済み：分析・考察（2026-10-16．research-cycle の phase 3．判定：採用）

**変更**：新しいオフラインの CLI `atrium churn-refill-eval` で，probe に返事の無かった枠を 1 段目の次の順位で補う変種（once・until_full）を評価した（実装 `a65e41b`，事前登録 `1da9320`，結果 `ddf999a`）．config.yaml・データ・シャード・既存の routing の挙動は変えていない．規則・閾値は結果を見て変えていない．流し直しは無い．

**正しさの確認**：G1〜G6 は全て通過した（G1 1,920 件で差の最大 0.0，G2 1,920 件・G3 1,680 件・G5 320 件で不一致 0 件，G4 違反 0 件，G6 1,440 実行で違反 0 問）．

**結果と判定**
- H-P1：ρ_refill は 0.5366・0.5468・0.5139・0.5226・0.5843（平均 0.541，標準偏差 0.027．種の和 416 / 768 = 0.542）．最小値は閾値 0.20 を 0.31 上回り，種の間の標準偏差の約 11 倍である．事前に推定した標準偏差（約 0.07）より実測の揺れは小さい．
- **このイテレーションの結論：採用**（事前登録の規則による）．ただし，同じ程度の問い合わせの数の 1 往復の対照に対する利点は示せていない（分析の 3）．

**分析**

事実（記録とコードの規則から確かめたもの）
1. **費用の見込みは当たった**．contacted は 12.00 → 15.35（見込み 15.35），wasted は 3.35 → 4.31（見込み 4.29），2 往復目の要る問の割合は 0.964（見込み 0.98）である．補った枠のうち稼働中の割合は 1 − 0.96 / 3.35 ≈ 0.71 で，見込みの 0.72 と合う．
2. **ρ の見込み（0.37）と until_full の上限の見込み（0.51）が外れた理由は，損失の割り当て方にある**（遷移表，`device_probe_d12`，種の和）．
   - 計画は，(ii) の増分 376 問を戻らない損失とした．そのうち 95 問は (iii) → (ii) で，R = 1 でも hit ではなかった．hit を失わせた (ii) は hit → (ii) の 281 問だけである．
   - 損失 768 問は，hit → (ii) 281 問 + hit → (iii) 577 問 − (iii) → hit 90 問である（R = 1 の hit 7,769 問，none の hit 7,001 問）．
   - 補うと，hit → (iii) は 577 → 134（once）→ 6（until_full）と減り，(iii) → hit は 90 → 63 → 67 と減った．
   - 補う変種の上限は，hit → (iii) を 0 にし，(iii) → hit を 90 に保つ場合で，577 / 768 ≈ 0.75 である．until_full の 0.714（548 / 768）はこの上限より下にある．
   - 正しい上限 0.75 に 0.72 を掛けると ≈ 0.54 で，once の実測 0.542 と一致する．
3. **同じ程度の問い合わせの数の対照との差は，種の間の揺れの中にある**．
   - `device_probe_d15`（none，1 往復，contacted 15.00）と d12 の once の n_hit の差は，種ごとに 0・+1・+1・+9・+22 問（平均 6.6，標準偏差 9.3．種の和で +33 問 / 14,700 問，0.22 ポイント）である．
   - 回復した問の数 / 増えた問い合わせの数は，5 個の種のうち 4 個で対照の方が高い．
   - once で返事のある候補は，見えている順位の上位 12 + f 台（f の平均 3.35）のうち稼働中のものである．d15 は上位 15 台のうち稼働中のものなので，平均ではほぼ同じ集合になる．違いは問ごとに補う深さが変わることだけで，その効果はこの評価では 33 問以下である．2 往復目の待ち時間の費用は once にだけかかる．
4. **L_join = ∞ の d24 で ρ がほぼ 0 なのは，補う相手が無いからである**．none の contacted は 20.63 で D = 24 に届かず，見えているデバイスを既にほぼ全て送っている（2 往復目の要る問の割合 0.068）．
5. **a が小さいほど ρ が下がる**．a = 0.9 で 0.630〜0.767，0.7 で 0.514〜0.584，0.5 で 0.353〜0.463 である．補った先も離脱済みである割合が上がるためである．
6. **E-2 相当**：none に対して，候補外は種の和で −632，上位 m 外は +216 である．補って候補に入った正解の約 34% は，2 段目の上位 m の外になった．

推測（未確認）
- centroid_probe で probe の要求の数が contacted より大きく増える（k75・L_join = 100 の once で 27.55 → 42.65，contacted は 28.11）．事前登録の「前の往復で返事の無かったノードに載る受信箱も飛ばさない」規則で，同じノードに要求を重ねるためと読める．問ごとには確かめていない．
- d15 より大きい D（16〜17）の none は，until_full（contacted 16.70，n_hit 7,549）と同じ程度の hit を 1 往復で得る可能性がある．未測定である．

**想定外の挙動**：無し（オフラインの評価のため，言語崩れ・発散・OOM は該当しない）．

**学び**
- 区分の正味の増減で損失を割り当てると，R = 1 で既に失っていた問（(iii) → (ii)）を損失に数え，回復の上限を過小に見積もる．上限は遷移表の hit → 各区分の総量で見積もる．
- 補う変種の回復は，「補った枠のうち稼働中の割合」×「hit → (iii) の総量」でおおむね予測できる．
- 返事の無い枠を補うことは，平均では「D を f の平均だけ大きくして 1 往復で送る」ことに近く，このオフラインの評価では hit の差がほとんど無い．補う変種の価値は，同じ問い合わせの数での比較と往復の待ち時間とで決まる．

**限界**
- オフラインの評価で，2 往復目の待ち時間の費用は測っていない．返事の無さは送った時点で分かると仮定した（時間切れの待ちは模していない）．
- 判定は a = 0.7・R = 1000・L_join = 100 の 1 点，種 5 個による．対照は D = 15 の 1 点だけで，同じ問い合わせの数での hit の曲線は無い．

**次の一手**（backlog B51 [auto-decided]）：同じ問い合わせの数で，補う変種と最初から多めに送る構成の hit を比べる（`churn_eval.device_probe_budget`．仮称）．

**記録の注記**：rc-evaluator は `Bash` が `Unknown tool` で失敗し，ファイルへ書き込めなかった．この節・backlog の B51・config.yml の追記・ローテーション・commit は，オーケストレータが戻り値から書き写して行った．数値は `analysis_report.md` から取り，`metrics.json` を直接読む照合は行っていない．損失の分解・上限・対照の差の平均と標準偏差・E-2 の和は，オーケストレータが遷移表と報告の値から検算した．

## Iteration 17: 離脱の通知の遅れごとの無駄な問い合わせと hit の回復

### 調査・計画（2026-10-16．research-cycle の phase 1）

**直近の判定**：Iteration 16 は採用（G1〜G4 通過，H-J1 支持．device_probe_d12 の ρ は 0.581〜0.695）．L = 0 でも device_probe_d12 の低下の 27%（種の和で 556 問．種ごとに 111・94・113・118・120 問）が残り，全て (iii) の増分だった．原因は，構造上，離脱済みのデバイスが表に残り，12 枠のうち 3.32 枠を取ることである．config.yml の levers は使い切っており，B46 [auto-decided] でレバーを離脱の通知の遅れ L_leave に決めた．レバーの形は B47 [auto-decided] に記録した．

**調べて分かったこと**
- SWIM（Das, Gupta, Motivala, DSN 2002, doi:10.1109/DSN.2002.1028914．一次資料）は，周期 T' ごとに無作為な 1 台へ ping を送り，返事が無ければ k 台を介して ping-req を送る．故障の最初の検出までの時間の期待値は e/(e − 1) ≈ 1.58 周期である．suspicion の機構では，疑われたノードは時間切れの後に故障と宣言され，本人は incarnation 番号を上げて反駁できる．
- Lifeguard（Dadgar, Phillips, Currey, arXiv:1707.00788, 2017．一次資料）は，SWIM の誤検出を減らすために，自ノードの健康度に応じて probe の時間切れを延ばし，suspicion の時間切れを独立な確認の数に応じて最大値から最小値へ縮める．
- HashiCorp memberlist の `DefaultLANConfig`（`config.go`．一次資料のコード）は，ProbeInterval 1 秒，ProbeTimeout 500 ms，SuspicionMult 4，SuspicionMaxTimeoutMult 6，GossipInterval 200 ms である．suspicion の時間切れの最小値は SuspicionMult × log10(n) × ProbeInterval で，n = 30 なら約 5.9 秒，最大値はその 6 倍の約 35 秒になる．意図的な離脱（`Leave()`）は自分の dead の通知を gossip で流すので，故障より速く伝わる．
- 収束の時間は，Consul の講演で約 2 秒（Iteration 16．条件は未確認），gossip で O(log n) 回の round とされる（二次資料）．
- 以上から，故障による離脱の伝播は「検出の約 1.6 周期 ＋ suspicion の時間切れ 6〜35 秒 ＋ gossip の伝播」で，参加や意図的な離脱の伝播（gossip だけ）より遅い．現実には L_leave ≥ L_join である（推測を含む）．1 刻み ＝ 1 問なので，秒への換算は処理量の仮定に依る．1 問/秒なら L_leave = 10 は故障の検出に当たる．数十問/秒なら L_leave = 10 は意図的な離脱に当たり，L_leave = 100 でも故障の検出より短い．
- 時刻表だけの試算（計画のための試算で，コードにはしていない）．`/tmp/iter17_estimate.py`・`/tmp/iter17_estimate2.py` を手元で `uv run` した．条件は，`churn_eval.simulate_availability` と同じ種（`default_rng([12, s, round(a·100)])`），ノード 30 台，2,940 刻み，5 個の種である．
  - 検算：R = 1000・a = 0.7 の「見えているのに離脱済み」の割合は，L_join = ∞ で 0.276，L_join = 100 で 0.281 で，Iteration 16 の概算と一致した．1 刻みあたりの見えているのに離脱済みのノードの数は，L_join = ∞・100・0 で 5.77・7.92・8.10 で，Iteration 16 の flood_score の wasted（5.77・7.92・8.10）と小数 2 桁まで一致した．
  - L_join = 0 に固定したときの，見えているのに離脱済みのノードの数（L_leave = ∞ に対する比，5 個の種の最小〜最大）と，D = 12 の枠の換算（12 × 表の中の割合）は次のとおりである．稼働中なのに見えないノードは全ての L_leave で 0 である．

| a | R | L_leave = ∞ | L_leave = 100 | L_leave = 30 | L_leave = 10 | L_leave = 1 |
|---|---|---|---|---|---|---|
| 0.7 | 1000 | 8.10（3.33 枠） | 5.93（0.733，0.692〜0.762．2.64 枠） | 2.64（0.327） | 1.01（0.125，0.118〜0.133．0.55 枠） | 0.11（0.013） |
| 0.7 | 100 | 3.66（1.78 枠） | 効果なし（L ≥ R） | 2.28（0.623） | 0.95（0.261） | 0.11（0.029） |
| 0.9 | 1000 | 2.90（1.16 枠） | 2.86（0.988） | 2.14（0.740） | 1.06（0.367） | 0.13（0.045） |
| 0.5 | 1000 | 11.99（5.31 枠） | 5.59（0.466） | 2.03（0.170） | 0.72（0.060） | 0.07（0.006） |

  - B46 の案（L_join = 100 に固定）では，a = 0.7・R = 1000 の稼働中なのに見えない割合が，L_leave = ∞・100・30・10 で 0.036・0.234・0.364・0.378 に増え，通知なし（0.285）より大きくなる．稼働中の平均は 200 問，停止中の平均は 85.7 問なので，離脱の通知の後に 100 問以内に再び参加するノードが多く，参加の通知が届くまで見えないためである．L_join = 10 なら，L_leave = 10 で 0.047 である．

**単一レバー**：`churn_eval.leave_notice_delay` を，通知なし（L_leave = ∞．Iteration 16 と同じ）から {0, 10, 100} へ変える．参加の通知は L_join = 0 に固定する（B47 の A1．B46 の L_join = 100 から変えた理由は上の試算）．新しい CLI `atrium churn-leave-eval`（`src/atrium/churn_leave_eval.py`）で実行する．`churn-join-eval` の挙動と Iteration 16 の結果の再現には触れない．
- メンバー表は，直前の更新 u(t) = ⌊t/R⌋·R の時点の状態に，区間 (u(t), t] の事象のうち通知の届いたものを重ねる．参加は刻み j ≤ t − L_join のときに届き，離脱は刻み j ≤ t − L_leave のときに届く．届いた事象のうち最も新しいものに従う（参加なら表に入れ，離脱なら表から消す）．届いた事象が無ければ u(t) の時点の状態のままである．
- 参加は `online[j]` かつ ¬`online[j−1]`，離脱は ¬`online[j]` かつ `online[j−1]` とする（刻み 0 は事象として数えない）．
- L_leave = ∞ は Iteration 16 の `join_notified_view(online, R, L_join)` と同じになる．L_leave ≥ R では区間 (u(t), t − L_leave] が空になるので，離脱の通知は効かない．
- 離脱の通知の後に再び参加したノードは，参加の事象の方が新しいので，参加の通知が届いた時点から見える．参加の通知の後に離脱し，その離脱がまだ届いていないノードは，見えているのに離脱済みとして表に残る．SWIM の incarnation 番号による反駁は模さない．
- 感度の格子として，L_join = 100 でも同じ L_leave を流す（記録だけ．B47 の E1）．
- 1 刻みは 1 問である．秒への換算は行わない．

**固定する構成**：config.yaml，データ，シャード，配置，m = 2，C = 32，`AVAILABILITIES`・`REFRESH_STEPS`・`MEAN_UP_STEPS`・`N_SCHEDULE_SEEDS`・`experiment.seed` = 12，採点・質問の順番・経路の選択（`churn_cascade_eval` の関数をそのまま呼ぶ）．構成は Iteration 16 と同じ 8 個（multi_centroid，flood_score，`centroid_probe_k75`，`centroid_probe_k30`，`device_probe_d24`，`device_probe_d12`，全数の `centroid_probe_k150`・`device_probe_d30`）である．条件は a ∈ {1.0, 0.9, 0.7, 0.5}，R ∈ {1, 10, 100, 1000}，L_join ∈ {0, 100}，L_leave ∈ {∞, 0, 10, 100}，種 5 個で，実行の数は 8 × 4 × 4 × 2 × 4 × 5 = 5,120 件である．

**仮説 H-L1**：参加を L_join = 0 で通知した上で，離脱を L_leave = 10 で通知すると，device_probe（D = 12）の a = 0.7・R = 1000 で，L_join = 0 でも残る低下の 75% 以上が，5 個の種の全てで戻る．種ごとの回復の割合は ρ_leave = (n_hit(R=1000, L_join=0, L_leave=10) − n_hit(R=1000, L_join=0, L_leave=∞)) / (n_hit(R=1) − n_hit(R=1000, L_join=0, L_leave=∞)) とする．分母は Iteration 16 の E-2 の残る低下（種ごとに 111・94・113・118・120 問）と同じ値になる．

**期待効果（推測）**
- 回復の割合を，見えているのに離脱済みの数に比例すると見て，ρ_leave(L) ≈ 1 − (L の数)/(∞ の数) と見積もる．device_probe_d12 の a = 0.7・R = 1000 では，ρ_leave(10) ≈ 0.875（種の間で 0.867〜0.882），ρ_leave(100) ≈ 0.267（0.238〜0.308）である．ρ_leave(0) は構造上 1 である．
- 閾値は 0.75 にする（B47 の C1）．ρ_leave(0) = 1 が構造上決まるので，Iteration 15・16 の 0.5 では，見積もりよりずっと小さい回復でも支持になる．0.75 は，見積もり 0.875 から，Iteration 16 の ρ の種の間の標準偏差（0.045）の約 2.8 倍を引いた値である．今回は分母が 94〜120 問で，Iteration 16（約 400 問）より小さいので，種の間の揺れは大きくなりうる．Iteration 16 の E-2 では候補外が +851，上位 m 外が −295 で，枠と上位 m の入れ替わりは線形でないので，見積もりは外れうる．
- flood_score は L_join = 0 で既に hit が R = 1 と同じなので（Iteration 16 の G3），ρ_leave の分母が 0 になり，hit は変わらない．contacted は 29.17 から，約 22.1（L_leave = 10）と約 27.0（L_leave = 100）に減ると見込む．wasted は 8.10 から，約 1.01 と約 5.93 に減ると見込む．device_probe_d12 は contacted が 12.00 のまま，wasted が 3.32 から約 0.55（L_leave = 10）に減ると見込む．
- 感度の格子（L_join = 100）では，L_leave を短くすると見えなさが増える．このため，device_probe_d12 の hit は，L_leave = 10 で Iteration 16 の L = 100 より下がると見込む．

**退行ゲート**（1 つでも通らなければ H-L1 を判定しない）
- G1：L_join = 0・L_leave = ∞ の 640 件が，`results/20261016_iter16_churn_join_eval/metrics.json` の `runs` の delay = 0 の実行と，hit・hit_given_gold_online・gold_online_rate・contacted・wasted と全ての件数（n_test で割った差）で 1/2940 以内に一致する．どちらにも欠けが無く，件数の None の有無も一致する．m と n_test も一致する．
- G2：L_join = 100・L_leave = ∞ の 640 件が，同じ `metrics.json` の delay = 100 の実行と，G1 と同じ項目・同じ許容で全件一致する．
- G3：L_leave ≥ R の実行（構成・a・種・L_join ごとに 5 個．計 1,600 件）が，同じ（構成，a，R，種，L_join）の L_leave = ∞ と，全ての項目で完全に一致する．
- G4：全ての実行で，`n_hit` と (i)〜(iv) の件数の和が問の数に等しい（`churn_cascade_eval.gate_g4`）．
- G5：(L_join, L_leave) = (0, 0) の実行が，全ての構成・a・R・種で，同じ（構成，a，種）の R = 1 の実行と，`n_hit` と全ての件数で完全に一致する（640 件）．このとき表は稼働中の集合そのものになるので，構造上そうなる．主の判定には使わない（B46 の要レビュー (c)）．

**判定の規則と成功条件**
- G1〜G5 を全て通過し，`device_probe_d12` の a = 0.7・R = 1000・L_join = 0・L_leave = 10 の ρ_leave が 5 個の種の全てで 0.75 以上なら，H-L1 を支持し，採用とする．分母が 0 以下の種は不成立とする．問の数は実行によらず等しいので，件数の比は hit の比に等しい．
- 判定は Iteration 15・16 と同じく，5 個の種の全てで閾値を超える形にする．ρ_leave は種ごとに対で計算する．

**記録**（判定に使わない）
- 全ての構成の a = 0.7 の L_join・L_leave ごとの ρ_leave（種ごと．分母が 0 の構成は「定義できない」と書く），R = 1000 の hit（種ごと）・contacted・wasted（種の平均）．
- a = 0.9・0.5 の `device_probe_d12` の ρ_leave（試算の見積もりは，L_leave = 10 で約 0.63・約 0.94）．
- E-2 相当：`device_probe_d12` の a = 0.7・R = 1000・L_join = 0 の L_leave = 10 で残る低下（問）と，(i)・(ii)・(iii)・候補外・上位 m 外・(iv) の件数の増分（種ごと）．
- 感度の格子：L_join = 100 の L_leave ごとの hit・(ii) の件数・contacted・wasted（B46 の要レビュー (a) への答え）．
- 遷移表：a = 0.7・L_join = 0 の（構成，L_leave，種）ごとに，R = 1 の区分（hit・(i)〜(iv)）から R = 1000 の区分への 5 × 5 の件数（160 個）．

**実機**：使わない．制御点のコンテナで実行するオフライン評価である（B46）．実機の再生は次の候補に残す（B43 の A2・B46 の A3）．

**記録の注記**：phase 1 の途中で 2 度，ツールが `Unknown tool` で失敗した（1 度目は Bash・Read，最後は Edit・Bash）．backlog の B47 と config.yml の note は rc-researcher が書き込んだ．この節は，journal への書き込みが失敗したので，オーケストレータが戻り値から書き写した．config.yml の YAML の構文は，オーケストレータが `yaml.safe_load` で確かめた．state.json は指示により触れていない．

### 事前登録（2026-10-16．実装の後，実行の前に commit・push する．結果を見て変えない．実装は commit `f4ffac5`）

調査・計画の節の規則（単一レバー，固定する構成，H-L1，退行ゲート G1〜G5，判定の規則，記録の項目）を，実行の前にそのまま固定する．計画と実装の間に規則の食い違いは無い（backlog への新しい記録は無い）．

**評価**：`atrium churn-leave-eval`（`src/atrium/churn_leave_eval.py`）を制御点のコンテナで 1 回だけ流す．出力は `results/20261016_iter17_churn_leave_eval/`（`metrics.json`・`analysis_report.md`・`run.log`）．実機のノードは使わない．失敗しても規則を変えて再実行しない．

```bash
ssh wafl-ctrl5 'pgrep -af "[c]hain.sh" || echo NO_CHAIN'   # 連続実行が動いていないことを確かめる
bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'
bash scripts/tasks/remote.sh churn_leave_eval 20261016_iter17_churn_leave_eval 20261009_eq5_b1 20261016_iter16_churn_join_eval
rsync -az wafl-ctrl5:<REMOTE_DIR>/results/20261016_iter17_churn_leave_eval/ results/20261016_iter17_churn_leave_eval/
```

**照合の件数**：実行 5,120 件，G1 640 件，G2 640 件，G3 1,600 件，G5 640 件，遷移表 160 個．

**実装の細部**（規則の読み方を固定するもの）
- ρ_leave の分母の n_hit(R=1) は，L_join に依らず L_join = 0・L_leave = ∞ の R = 1 の実行を使う（R = 1 では通知の窓が常に空なので，どの遅れでも表は同じになる）．G5 の基準も同じ実行である．
- `analysis_report.md` の表では L_leave ≥ R の行を省く（Iteration 16 で L ≥ R の行を省いたのと同じ扱い）．`metrics.json` には全ての実行を残す．

**実装の検査**（commit `f4ffac5`）
- `tests/test_churn_leave_eval.py` の 41 件が通過した．全体の pytest は 441 件が全て通過した．`ruff check src tests`・`ruff format --check src tests`・`mypy` は通過した．
- テストで G1・G2（L_leave = ∞ の表が `join_notified_view` と一致），G3（L_leave ≥ R の表が L_leave = ∞ と一致），G5（(0, 0) の表が稼働中の集合と一致），B47 (d)（手で作った時刻表で，再び参加したノードが参加の通知の届いた刻みから見える）の構造上の性質を確かめた．
- 合成のデータ（4 構成，ノード 4 台，300 刻み，乱数の得点）で，集計・ゲート・報告の流れを最後まで通した．実行 2,560 件，G1・G2 は各 320 件，G3 は 800 件，G5 は 320 件を照合し，遷移表は 80 個で，G1〜G5 は全て通過した（8 構成の件数のちょうど半分）．

### 実装・実験（2026-10-16．research-cycle の phase 2．数値の規則の機械的な出力のみ．解釈・判定は phase 3）

- 実装：commit `f4ffac5`（`src/atrium/churn_leave_eval.py`，`tests/test_churn_leave_eval.py`，`scripts/remote/churn_leave_eval.sh`，`cli.py` の `churn-leave-eval`，README・d0001・d0003・d0004）．事前登録：commit `2b043ca`（実行の前に push 済み）．結果：この節と同じ commit（`results/20261016_iter17_churn_leave_eval/`．`metrics.json`・`analysis_report.md`・`run.log`）．
- 検査：事前登録の「実装の検査」のとおり（pytest 441 件が全て通過，`ruff check src tests`・`ruff format --check src tests`・`mypy` が通過）．
- 1 度目の実行（失敗．backlog B48）：`NO_CHAIN` を確かめてから publish した（235 秒．イメージの版は `2b043caeec1d-dirty`．dirty は未追跡のファイルによる．制御点の registry の manifest の digest はローカルと一致）．`remote.sh churn_leave_eval` は，制御点の `docker pull` の層の展開で `unpigz: corrupted -- crc32 mismatch`（層 `sha256:02e7dc83…`）となり，終了コード 1（125 秒）で止まった．評価は起動しておらず，数値は出ていない．
- B48（A1）：制御点の registry の blob を読み取りだけで調べ，`sha256sum` が digest と一致し（423,719,366 バイト），`gzip -t` も通った．registry の中の blob は壊れていなかったので，publish はやり直さずに，同じコマンドを 1 回だけ流した．
- 2 度目の実行：終了コード 0，pull を含めて 298 秒，評価の本体（`duration_s`）は 56.9 秒である．実行は 5,120 件で，失敗は 0 件．遷移表は 160 個．`run.log` は `remote.sh` の標準出力（1,324 行）で，WARNING は 128 行，全て faiss の k-means の「学習点が少ない」の警告である（Iteration 16 の `run.log` も 128 行）．`ModuleNotFoundError("No module named 'faiss.swigfaiss_avx2'")` は faiss の loader の INFO の行である．
- `metrics_cmd`（`uv run atrium metrics --json`）は，Iteration 16 と同じく `run_meta.json` のある最新の実行（`20261016_iter14_smoke_dp12_pe_n8`）を読んだ．今回のオフラインの評価は `run_meta.json` を作らないので，この命令の対象外である．数値は `metrics.json` から取った．
- 退行ゲート：全て通過した．
  - G1：640 件を照合し，こちらの欠け 0 件，基準の欠け 0 件，None の不一致 0 件，差の最大 0.0（許容 1/2940）．
  - G2：640 件を照合し，こちらの欠け 0 件，基準の欠け 0 件，None の不一致 0 件，差の最大 0.0．
  - G3：1,600 件 / 期待 1,600 件を照合し，不一致 0 件．
  - G4：違反 0 件．
  - G5：640 件 / 期待 640 件を照合し，不一致 0 件．
- H-L1（`device_probe_d12`，a = 0.7，R = 1000，L_join = 0，L_leave = 10）：機械的な判定は「支持」．

| 種 | n_hit（R = 1） | n_hit（R = 1000，L_leave = ∞） | n_hit（R = 1000，L_leave = 10） | 分母（問） | 回復（問） | ρ_leave |
|---|---|---|---|---|---|---|
| 0 | 1,561 | 1,450 | 1,545 | 111 | 95 | 0.8559 |
| 1 | 1,542 | 1,448 | 1,529 | 94 | 81 | 0.8617 |
| 2 | 1,552 | 1,439 | 1,535 | 113 | 96 | 0.8496 |
| 3 | 1,577 | 1,459 | 1,558 | 118 | 99 | 0.8390 |
| 4 | 1,537 | 1,417 | 1,523 | 120 | 106 | 0.8833 |

- 記録：全ての構成の a = 0.7・R = 1000・L_join = 0 の ρ_leave（種の最小〜最大．L_leave = 0 は全ての構成で 1.000）と，contacted・wasted（種の平均．L_leave = ∞ / 0 / 10 / 100 の順）．`centroid_probe_k150`・`device_probe_d30` は全ての値が flood_score と同じ．

| 構成 | ρ_leave（L_leave = 10） | ρ_leave（L_leave = 100） | contacted | wasted |
|---|---|---|---|---|
| multi_centroid | 0.817〜0.875 | 0.155〜0.250 | 1.96 / 1.94 / 1.95 / 1.95 | 0.55 / 0.00 / 0.09 / 0.43 |
| flood_score | 定義できない（分母 0．hit は全ての L_leave で同じ） | 定義できない | 29.17 / 21.08 / 22.08 / 27.00 | 8.10 / 0.00 / 1.01 / 5.93 |
| centroid_probe_k75 | 0.744〜0.978 | 0.170〜0.333 | 28.40 / 21.01 / 21.99 / 26.55 | 7.88 / 0.00 / 1.00 / 5.83 |
| centroid_probe_k30 | 0.804〜0.904 | 0.065〜0.212 | 19.62 / 16.99 / 17.41 / 19.07 | 5.45 / 0.00 / 0.80 / 4.19 |
| device_probe_d24 | 0.919〜1.022 | 0.270〜0.667 | 23.91 / 20.96 / 21.82 / 23.88 | 6.61 / 0.00 / 1.00 / 5.22 |
| device_probe_d12 | 0.839〜0.883 | 0.204〜0.340 | 12.00 / 12.00 / 12.00 / 12.00 | 3.32 / 0.00 / 0.56 / 2.63 |

- 記録：`device_probe_d12` の他の a の ρ_leave（R = 1000，L_join = 0，種の最小〜最大）．a = 0.9 は L_leave = 10 で 0.605〜0.800，100 で 0.000〜0.025．a = 0.5 は L_leave = 10 で 0.909〜0.972，100 で 0.504〜0.559．
- E-2 相当（`device_probe_d12`，a = 0.7，R = 1 → 1000，L_join = 0，L_leave = 10．種 0〜4）：残る低下は 16・13・17・19・14 問，回復は 95・81・96・99・106 問．件数の増分は (iii) が残る低下と同じ値で，そのうち候補外が +28・+23・+25・+22・+26，上位 m 外が −12・−10・−8・−3・−12．(i)・(ii)・(iv) の増分は全ての種で 0．
- 感度の格子（a = 0.7，R = 1000，L_join = 100．L_leave = ∞ / 0 / 10 / 100 の順）：(ii) の件数（種の和．全ての構成で同じ）は 376 / 3,931 / 3,921 / 2,399．ρ_leave は全ての構成・全ての種で負である．
  - `device_probe_d12`：hit 0.466〜0.484 / 0.352〜0.380 / 0.352〜0.377 / 0.399〜0.422，contacted 12.00 / 11.32 / 11.52 / 12.00，wasted 3.35 / 0.00 / 0.54 / 2.77．
  - flood_score：hit 0.554〜0.565 / 0.359〜0.387 / 0.360〜0.387 / 0.441〜0.464，contacted 28.24 / 13.07 / 13.75 / 21.00，wasted 7.92 / 0.00 / 0.64 / 4.85．
- 遷移表（a = 0.7，L_join = 0，種の和，各 14,700 問．行は R = 1 の区分，列は R = 1000 の区分．R = 1 の (ii)・(iv) は 0 件．(i) → (i) は全てで 4,398 件）：
  - `device_probe_d12`：L_leave = ∞ は hit → hit 7,140・→ (iii) 629，(iii) → hit 73・→ (iii) 2,460．L_leave = 0 は hit → hit 7,769，(iii) → (iii) 2,533．L_leave = 10 は hit → hit 7,679・→ (iii) 90，(iii) → hit 11・→ (iii) 2,522．L_leave = 100 は hit → hit 7,294・→ (iii) 475，(iii) → hit 61・→ (iii) 2,472．
  - `centroid_probe_k75`：L_leave = ∞ は hit → (iii) 336・(iii) → hit 112，0 は 0・0，10 は 50・12，100 は 252・81．
  - multi_centroid：hit → (iv) が L_leave = ∞ / 0 / 10 / 100 で 339 / 0 / 52 / 270．(iii) → (iii) は全てで 5,391．
  - flood_score：全ての L_leave で hit → hit 8,529，(iii) → (iii) 1,773．
- 計画からの逸脱：規則・閾値・ゲート・記録の項目の変更は無い．評価の前のインフラの失敗による再実行を 1 度行った（backlog B48）．

### Iteration 17 実行済み：分析・考察（2026-10-16．research-cycle の phase 3．判定：採用）

**変更**：新しいオフラインの CLI `atrium churn-leave-eval` で，Iteration 16 のメンバー表（参加の通知）に離脱の通知（遅れ L_leave）を足した．参加の通知は L_join = 0 に固定した（実装 `f4ffac5`，事前登録 `2b043ca`，結果 `4ed7fbb`）．config.yaml・データ・シャード・既存の routing の挙動は変えていない．事前登録の規則・閾値は，結果を見て変えていない．評価の前のインフラの失敗で 1 度流し直した（B48．1 度目には評価の数値は 1 つも出ていない）．

**正しさの確認**：G1〜G5 は全て通過した（G1・G2 は各 640 件で差の最大 0.0，G3 は 1,600 件で不一致 0 件，G4 は違反 0 件，G5 は 640 件で不一致 0 件）．G1・G2 により，L_leave = ∞ の実行は Iteration 16 の L = 0・100 の実行を完全に再現している．

**結果と判定**
- H-L1：機械的な判定は「支持」．ρ_leave は 0.8559・0.8617・0.8496・0.8390・0.8833（平均 0.858，標準偏差 0.017）．最小値は閾値 0.75 を 0.089 上回り，これは今回の種の間の標準偏差の約 5 倍である．種の和の ρ_leave は 477 / 556 = 0.858 である．
- 計画の見込み 0.875（種の間で 0.867〜0.882）と比べると，平均は 0.017 低い．見込みの範囲の下限より下だが，差は今回の種の間の標準偏差と同じ程度である．
- **このイテレーションの結論：採用**．

**分析**

事実（記録とコードの規則から確かめたもの）
1. **回復は種の間の揺れより大きい**．device_probe_d12 の L_leave = 10 と ∞ の差は，種ごとに 95・81・96・99・106 問（hit で 0.028〜0.036）で，全ての種で正である．L_leave = ∞ の hit の種の間の幅（0.4820〜0.4963，0.0143．42 問）の 1.9〜2.5 倍ある．
2. **費用は時刻表の試算どおりに減った**．flood_score の contacted は 29.17 → 22.08（見込み約 22.1），wasted は 8.10 → 1.01（見込み約 1.01）で，hit は全ての L_leave で同じである．flood_score では，離脱の通知（L_leave = 10）は hit を変えずに問い合わせを 24% 減らし，無駄な問い合わせを 87.5% 減らす．
3. **device_probe_d12 の無駄な枠は，表の中の離脱済みの割合に比例する**．12 × (flood_score の wasted / contacted) は L_leave = ∞・10・100 で 3.33・0.55・2.64 で，d12 の実測の wasted 3.32・0.56・2.63 と一致する．Iteration 16 の「1 段目の得点は，稼働の有無と無関係に，離脱済みのデバイスを表の中の割合のまま選ぶ」という読みが，L_leave の 3 点で再び成り立った．
4. **ρ_leave は，時刻表だけから求めた見込みで，a と L_leave をまたいでおおむね予測できる**．見込みは 1 − (L_leave での見えているのに離脱済みの数) / (∞ での数) である．device_probe_d12 の種の平均の実測 / 見込みは次のとおりである．
   - a = 0.7：L_leave = 10 で 0.858 / 0.875，100 で 0.258 / 0.267
   - a = 0.5：10 で 0.936 / 0.940，100 で 0.523 / 0.534
   - a = 0.9：10 で 0.701 / 0.633，100 で 0.009 / 0.012
   - a = 0.9 の L_leave = 10 は，5 個の種のうち 3 個で 0.75 を下回る（0.605〜0.800）．分母は種あたり 40〜46 問と小さい．
5. **残る低下は (iii) だけである**．device_probe_d12 の L_leave = 10 で残る低下は，種の和で 79 問（556 問の 14.2%）である．内訳は候補外が +124，上位 m 外が −45 である．遷移表では hit → (iii) が 90 件，(iii) → hit が 11 件である．d12 の R = 1 → 1000 の低下は，Iteration 16 の通知なし（L = ∞）の 2,081 問から，(L_join, L_leave) = (0, 10) の 79 問になった（96% 減）．

感度の格子（L_join = 100）の解釈（phase 2 では解釈していない分）

6. **事実**：正解が稼働中の 10,302 問に対する (ii) の割合は，L_leave = ∞・0・10・100 で 0.036・0.382・0.381・0.233 である．計画の試算（0.036・—・0.378・0.234）と一致した．どの構成でも，hit は通知なし（L_leave = ∞）より下がり，ρ_leave は全ての構成・種で負である．flood_score の contacted は 28.24 → 13.75（L_leave = 10）に減り，見える稼働中のノードが減っている．device_probe_d12 の contacted も L_leave = 0 で 11.32 になり，見えるデバイスが 12 台に満たない問がある．
7. **仕組み（コードの規則から導いた読み）**：
   - L_leave = ∞ では，u(t) の時点で稼働していたノードは，その後に離脱して再び参加しても，表の古い記載のまま見え続ける．古い記載が，再び参加したノードの見えなさを隠していた．
   - 離脱の通知はこの記載を消す．そのため，再び参加したノードは，参加の通知が届くまで見えない．
   - 刻み s に離脱し，停止の長さ d の後に再び参加したノードが，稼働中なのに見えない長さは max(0, L_join − max(0, L_leave − d)) である．L_leave が平均の停止の長さ（a = 0.7 で 85.7 問）より短いと，再参加のたびにほぼ L_join 刻み（次の離脱が先なら，そこまで）見えない．
   - 推測の概算：再参加は 1 台あたり 1 / 285.7 刻みに 1 回で，見えない長さの期待値は E[min(100, 稼働の長さ)] ≈ 200 × (1 − e^{−0.5}) ≈ 78.7 問（稼働の長さを指数分布と仮定）である．このため，稼働中のうち見えない割合は約 78.7 / 285.7 / 0.7 ≈ 0.39 になる（更新の直後の分を無視した概算．実測は 0.38）．
8. **示唆**：
   - 離脱の通知は，参加の通知が平均の停止の長さより十分に速いとき（L_join ≪ 85.7 問）にだけ効く．L_join = 100 では，どの L_leave でも通知なしより悪い．
   - 参加と離脱を同じ gossip で伝える構成（L_join ≈ L_leave）に当たる (100, 100) では，device_probe_d12 の hit は種の和で 6,041 問である．これは (100, ∞) の 7,001 問より 960 問少なく，両方とも通知しない場合（Iteration 16 の L = ∞，5,688 問）より 353 問多い．flood_score は，(100, ∞) → (100, 100) で wasted が 7.92 → 4.85 に減る代わりに，hit が 8,244 → 6,663 問に下がる．
   - 遅れが対称な現実の構成では，メンバー表の通知で「離脱済みなのに見える」ことを除くと「稼働中なのに見えない」ことが増え，2 つが打ち消し合う．離脱済みのデバイスの枠は，メンバー表の通知ではなく probe の返事で扱う方が見込みがある（次の一手の根拠）．

推測（未確認）
- 見込みの比（L_leave での離脱済みの数 / ∞ での数）は，停止の長さを指数分布と見ると，およそ 1 − exp(−L_leave / 平均の停止の長さ) で近似できる．平均の停止の長さは a = 0.9・0.7・0.5 で 22.2・85.7・200 問である．L_leave = 10 の近似は 0.36・0.11・0.05 で，試算は 0.367・0.125・0.060 である．したがって効き目は，L_leave の絶対値ではなく，平均の停止の長さとの比で決まると読める．
- device_probe_d24 の種 0 の ρ_leave = 1.0222 は，hit が R = 1 より 1 問多いことによる．離脱済みのデバイスが 1 段目の枠を取ると，稼働中の別のデバイスが候補から外れ，そのデバイスが 2 段目で正解を上位 m の外へ押し出さなくなる問がありうる（上位 m 外の増分が負であることと整合する．問ごとには確かめていない）．
- 故障による離脱の伝播（memberlist の既定で suspicion の時間切れ 6〜35 秒）は，数十問/秒では数百問以上に当たる．このとき a = 0.7 でも ρ_leave は L_leave = 100 の 0.26 より小さく，離脱の通知だけでは故障の分はほとんど戻らない．

**想定外の挙動**：無し（オフラインの評価のため，言語崩れ・発散・OOM は該当しない）．1 度目の実行の docker pull の crc32 の不一致は，評価の前のインフラの失敗である（B48．原因は確かめていない）．

**学び**
- 離脱済みなのに見えるノードによる損失は，時刻表だけから求めた「表に残る離脱済みのノードの数」の比で，方式・a・L_leave をまたいでおおむね予測できる．device_probe の無駄な枠は，D × 表の中の離脱済みの割合に等しい．
- 離脱の通知の効き目は，L_leave と平均の停止の長さの比で決まる（推測）．停止の短い a = 0.9 では，L_leave = 10 でも 7 割しか戻らない．
- 古いメンバー表の記載には，再び参加したノードを見えるままにする働きもある．参加の通知が遅いと，離脱の通知はこの働きを消して見えなさを増やす．通知の遅れは参加と離脱の組で決める必要があり，片方だけを短くすると悪くなりうる．
- Iteration 16 では，2 つの因子の片方を除くと他方が現れた．今回は，通知の仕方によっては，片方を減らすと他方が増えることが分かった．

**限界**
- オフラインの評価で，実機の再生は行っていない．
- 判定は a = 0.7・R = 1000 の 1 点，種 5 個による．分母は種あたり 94〜120 問と小さい．
- L_join = 0 は参加の通知が理想的な場合で，現実の値ではない（B47 (a)）．
- SWIM の incarnation 番号による反駁と suspicion の段階は模していない（B47 (d)）．
- 刻みは問の数で，秒への換算は推測である．

**次の一手**（backlog B49 [auto-decided]）：返事の無いデバイスの枠を 1 段目の次の順位のデバイスで補う変種（`churn_eval.probe_refill`．仮称）を，オフラインで評価する．メンバー表は Iteration 16 の形（参加の通知だけ）にする．

## p0005 の完了のまとめ（2026-10-14．T1〜T4．各節は下にある）

| 仮説・項目 | 判定 | 要点 |
|---|---|---|
| T1 判定の補正 | 検算が一致 | 狭い補正：B2 0.297 → 0.261，B3 0.260 → 0.215．EQ8 の「差はほぼ消える」は誤った広い補正に基づくので訂正（EQ8 の判定は不変） |
| T2 H-O1（持ち主推定の入力を揃える） | 方式ごと．検算は term_sketch だけ揺れの範囲で不一致（B8） | 埋め込みの 3 方式は，同じ長さ（28 トークン）なら持ち主の特定が質問文からの発見より低い．term_sketch は同じ長さでも高い．EQ2 の「持ち主を当てることの方に役立つ」は方式ごとに書き直した．**Iteration 2 で訂正**：term_sketch の高さはヘッダーによるもので，ヘッダーを除くと 4 方式とも Q より低い |
| T3 H-N1 | 支持 | 再学習しない素朴な中央方式の hit@2 0.118，全受信箱で学習した場合 0.282（区間が重ならない） |
| T3 H-N2 | 支持（2 つとも） | flood_score 0.802，multi_centroid 0.442 は，新参者でも ragroute-frozen を大きく上回る |
| T4 H-L1 | 支持 | parallel 30 の e2e p95：flood_score 19.1 秒，multi_centroid 4.95 秒（3 回の幅が重ならない） |
| T4 H-L2 | 支持 | p50 の p = 1 → 30 の倍率：flood_score 149 倍，multi_centroid 29 倍 |
| T4 H-L3 | 支持されない（定義が両方式を同じ値にした．下の T4 の節） | 処理量は flood_score が parallel とともに減り（12.8 → 2.5 問/秒），multi_centroid は一定（約 8.4 問/秒） |

事前登録した退行の確認・基準は変えていない．計画からの差は，T1 の CPU の受信箱の決め方（`bank`），T2 の term_sketch の退行の許容（B8）の 2 つで，どちらも該当の節に書いた．

## T4：負荷試験（確定．2026-10-14 18:24．`results/20261014_t4_*`，集計は `results/20261014_t4_load_summary/`）

事前登録（下の「T4 の事前登録」）のとおり，30 実行（flood_score と multi_centroid × parallel 1・4・8・16・30 × 3 回）を `t4_chain.sh` で連続実行した（15:55〜18:24．1 回あたり約 5 分，deploy を含む）．
**失敗は全 30 実行で 0**（失敗率が 10% を超えた実行は無く，打ち切りの規則は動かなかった）．結果を見て条件・基準は変えていない．イメージは `ffe0880`．

**結果**（3 回の中央値．300 問．e2e は成功した問，単位は秒．処理量は問／秒）

| routing | parallel | e2e p50 | e2e p95 | probe p95 | 処理量 |
|---|---|---|---|---|---|
| flood_score | 1 | 0.076 | 0.085 | 0.071 | 12.83 |
| flood_score | 4 | 0.598 | 0.802 | 0.504 | 6.48 |
| flood_score | 8 | 1.53 | 2.50 | 1.48 | 4.72 |
| flood_score | 16 | 5.11 | 8.10 | 4.87 | 2.95 |
| flood_score | 30 | 11.4 | 19.1 | 13.5 | 2.50 |
| multi_centroid | 1 | 0.120 | 0.123 | － | 8.30 |
| multi_centroid | 4 | 0.470 | 0.591 | － | 8.49 |
| multi_centroid | 8 | 0.938 | 1.30 | － | 8.45 |
| multi_centroid | 16 | 1.80 | 2.58 | － | 8.43 |
| multi_centroid | 30 | 3.49 | 4.95 | － | 8.39 |

1 問あたりにクエリを受け取ったデバイスは，flood_score が 30 台（全台），multi_centroid が 1.96 台．

**判定**（EQ6・EQ10 と同じ規則：3 回の幅が重ならないときだけ差がある）
- **H-L1：支持**．parallel 30 の e2e p95 は，flood_score が [18.9, 20.6]，multi_centroid が [4.93, 5.02] で，重ならない（約 4 倍）．
- **H-L2：支持**．e2e p50 の p = 1 → 30 の倍率は，flood_score が 149 倍，multi_centroid が 29 倍．
- **H-L3：支持されない**．定義（処理量が前の段より 10% 以上増えなくなる最小の parallel）では，両方式とも 4 になった（flood_score は 12.83 → 6.48 と減り，multi_centroid は 8.30 → 8.49 と +2%）．
  定義が「増えなくなる」だけを見て，flood_score のように**減る**場合と，multi_centroid のように最初から頭打ちの場合を区別しないため，差が出ない．事前登録の定義どおりに判定し，下に実測の形を書く．

**読み取れること**
- multi_centroid の処理量は parallel 1 から 30 までほぼ一定（約 8.4 問/秒）で，e2e p50 は parallel にほぼ比例して延びる（30 ÷ 8.4 ≈ 3.6 秒．同時に流した問の待ち行列）．質問者の側（クエリ埋め込みのスコアの計算など）が律速で，専門家は余裕がある．
- flood_score の処理量は，parallel が上がるほど**減る**（12.8 → 2.5 問/秒．約 1/5）．1 問が 30 台へ probe を送るので，parallel 30 では 900 件の要求が同時に飛び，専門家の CPU か質問者の接続が競合して，全体が遅くなると考えられる（原因は未確認）．
  （2026-10-15 追記．Iteration 4 の分析）ノードの計算は probe の時間の 0.1% 未満で，専門家の CPU ではない．待ちは質問者の側（HTTP クライアントの要求の扱いと接続の池）にあると考えられる．また，parallel 16 以上では接続のエラーが 1 実行で 100 回以上起き，`post_with_retry` の再試行が吸収していた（下の「`ReadError` は出なかった」は再試行を使い切った問が無かったという意味）．
  parallel 1 では flood_score の方が速い（12.8 対 8.3 問/秒）が，parallel 4 で逆転する．
- 事前登録の予想（parallel が上がると p95 が急に伸び，`ReadError` が出る）のうち，p95 の伸びは確認できたが，**`ReadError` は出なかった**．EQ3 の 175 問の `ReadError` は，300 問ではなく 2,940 問を parallel 30 で続けて流したときに起きたもので，この規模（300 問）では再現しなかった．
- parallel 1 の e2e は EQ7b のゲート（0.089 秒．2,940 問）と同じ程度（0.076 秒．300 問）で，条件の違い（質問集合）の範囲に収まる．
- 限界：有線 LAN・300 問・検索だけ．回答の生成を含む待ち時間は EQ6・EQ10 にある．処理量は `run_meta.json` の開始・終了から求めた（質問ごとの壁時計時間は記録していない）．

## T4 の事前登録（2026-10-14．実行の前に push する．p0005 §6）

flood_score は公開情報なしで最も見つけてもらいやすい（EQ2 の hit@1 0.722）が，1 問を全 30 台へ問い合わせる．1 問ずつ流すと e2e の中央値は 0.089 秒（EQ7b のゲート），30 問を同時に流すと 92.2 秒（EQ3）だった．
同時に流す質問の数に対して，待ち時間と処理量がどう変わるかを，公開情報を使う方式（multi_centroid）と比べて測る．

**条件（固定）**：`dataset: enronqa`，`answer_mode: retrieval_only`，`question_limit: 2`（受信箱ごとの先頭 2 問．300 問．EQ6・EQ10 と同じ集合），m = 2．
`routing` は flood_score と multi_centroid，`experiment.parallel` は 1・4・8・16・30，各条件 3 回の 30 実行（`results/20261014_t4_load_summary/t4_specs.txt`）．
run_id は `20261014_t4_<routing>_p<parallel>_r<1|2|3>`．各回の前に deploy し直す（`t4_chain.sh`．eq8_chain.sh と同じ形．deploy の再試行は 5 分おきに 3 回まで）．
失敗した問は流し直さない（待ち時間の測定．EQ10 と同じ）．連続実行の間，制御点で他のコンテナ（T2・T3）を動かさず，commit もしない．

**指標**：e2e_s の p50・p95（成功した問），probe_s（flood_score）の p50・p95，処理量（成功した問の数 ÷ `run_meta.json` の finished_at − started_at），失敗率とエラーの種類，1 問あたりにクエリを受け取ったデバイスの数．
質問ごとの壁時計時間は記録に無いので，処理量は `run_meta.json` の開始・終了から求める（質問の処理の開始から終了まで）．

**判定**（EQ6・EQ10 と同じ規則：比べる 2 条件の 3 回の幅が重ならないときだけ「差がある」）
- **H-L1**：parallel = 30 で，flood_score の e2e p95 が multi_centroid より大きい（3 回の幅が重ならない）．
- **H-L2**：parallel を 1 から 30 に上げたときの e2e p50 の増加の倍率（3 回の中央値どうしの比）が，flood_score の方が multi_centroid より大きい．
- **H-L3**：処理量が前の段より 10% 以上増えなくなる最小の parallel（無ければ「30 より上」）が，flood_score の方が小さい．
- 失敗率が 1% を超えた実行があっても止まらない（負荷による破綻は結果である）．ただし，同じ routing・parallel の 3 回とも失敗率が 10% を超えたら，その routing のより大きい parallel は飛ばして記録する（p0004 §12 の 4 の代わり．この実験に限る）．

**予想**（判定に使わない）：flood_score は 1 問ごとに 30 台へ probe を送るので，parallel が上がると専門家と質問者の接続の待ち行列で p95 が急に伸び，EQ3 と同じ `ReadError` が出る．
**限界**：有線 LAN・1 種類の質問集合．検索だけで，回答の生成は含まない（回答の生成を含む待ち時間は EQ6・EQ10）．parallel 1 の e2e は EQ7b のゲートと同じ条件のはずだが，質問集合（300 問）が違う．

**集計**：`results/20261014_t4_load_summary/t4_summary.py` → `t4_summary.out`．

## T3：150 人での新参者の評価（確定．2026-10-14 15:54．`results/20261014_t3_newcomer`）

事前登録（下の「T3 の事前登録」）のとおり，制御点のコンテナで 1 回だけ実行した（所要 1.6 時間．イメージは `b89d289`，`src` は実装の commit `d00d350` と同じ）．結果を見て条件・基準は変えていない．
ragroute-frozen の学習は 1 個あたり平均 169 秒（30 個の合計 1.4 時間）で，所要時間の上限（4 時間）に当たらず，新参者は 30 個すべてを使った．再学習したルーターは制御点の `atrium-data/enronqa/newcomer/` にあり，`results/` には入れていない．

**退行の確認：通過**．ragroute-upper と学習の要らない方式の全 test の hit@1・hit@2 が EQ2 と一致した．card_sim・centroid_sim・multi_centroid・flood_score・ragroute は差 0，term_sketch だけ hit@1 −0.0007・hit@2 −0.0010 で，事前登録した許容（2e-3）の中だった（採点の揺れ．backlog B8）．

新参者（名前の昇順に並べた 150 受信箱から `default_rng(12)` で非復元抽出）：taylor-m, king-j, lewis-a, baughman-d, lokay-m, gilbertsmith-d, mccarty-d, cash-m, holst-k, campbell-l, stclair-c, hain-m, geaccone-t, haedicke-m, kitchen-l, ring-a, benson-r, richey-c, shively-h, badeer-r, salisbury-h, dickson-s, perlingiere-d, cuilla-m, hyvl-d, arnold-j, white-s, maggi-m, harris-s, lay-k．

**結果**（新参者の問 600 問を合わせた pooled．m = 2）

| 方式 | hit@1 | hit@2 [95%CI] |
|---|---|---|
| flood_score | 0.705 | 0.802 [0.768, 0.832] |
| multi_centroid | 0.363 | 0.442 [0.403, 0.482] |
| term_sketch | 0.310 | 0.377 [0.338, 0.415] |
| centroid_sim | 0.263 | 0.328 [0.293, 0.365] |
| ragroute-upper（150 受信箱で学習済み） | 0.233 | 0.282 [0.248, 0.317] |
| card_sim | 0.118 | 0.155 [0.127, 0.185] |
| **ragroute-frozen**（新参者を除いて学習し直した） | **0.070** | **0.118 [0.092, 0.143]** |
| random | 0.010 | 0.017 [0.007, 0.027] |

**判定**
- **H-N1：支持**．ragroute-frozen（0.118）は ragroute-upper（0.282）より低く，区間が重ならない．再学習しない素朴な中央方式は，新参者を見つけにくい．新参者ごとでは，30 個のうち 22 個で frozen が upper より低い（平均 0.118 対 0.282）．
- **H-N2：支持**（2 つとも）．flood_score（0.802）と，公開情報を使う最良の方式 multi_centroid（0.442）は，どちらも ragroute-frozen より高く，区間が重ならない．
  学習の要らない方式は，4 方式のうち card_sim（0.155）以外が，新参者の発見で ragroute-frozen の 2 倍以上である．card_sim（0.155）も frozen を上回る（区間は [0.127, 0.185] と [0.092, 0.143] で重なる）．
- 参考：新参者ではない受信箱の質問での ragroute-frozen の hit@2 は 0.284（n = 87,600）で，全受信箱で学習した ragroute-upper の全体の値（0.279．EQ2）と同じである．
  再学習なしの素朴な中央方式が失うのは，新参者の分（0.284 → 0.118）に限られる．
- 公開情報を使う方式と flood_score は，新参者でも他の受信箱と同じ値（flood_score 0.802，multi_centroid 0.442，全 test では 0.804・0.440）で，新参者であることによる低下が無い．

**書き方の制約（D2）**：ragroute は，関連の無い受信箱を 1 問あたり 10 個だけ抜き出して学習した素朴な中央方式であり，この結果は「中央方式一般」を代表しない．
**限界**：公開情報の大きさ（件名 40，T = 200，C = 32）と m = 2 は，新参者を含む dev で選んだ値である．新参者の評価は 150 受信箱の 1 回の抽出（30 個）で，抽出を変えた繰り返しはしていない．

## T3 の事前登録（2026-10-14．実行の前に push する．p0005 §5）

150 人の受信箱で，学習のときにいなかった受信箱（新参者）を再学習なしで見つけられるかを測る．コードは `atrium newcomer-eval`（`src/atrium/newcomer_eval.py`．実装の commit は下の「実行」に書く）に固定し，結果を見て条件・基準を変えない．実行は 1 回だけ．

**問い**：公開情報を使う方式と flood_score は学習が要らないので，新参者でも挙動は同じである．比べる中心は，**素朴な中央方式（ragroute．D2）の再学習の有無**である．

**設計（固定）**
- 新参者：150 受信箱を名前の昇順に並べ，`numpy.random.default_rng(12)` で 30 個を非復元抽出する．選ばれた 30 個は結果の `metrics.json`（`newcomers_sampled`）に記録する．
- 各新参者 n：ragroute-upper（既存のルーター．150 受信箱で学習済み），ragroute-frozen（n を除いた 149 受信箱で学習し直したルーター．`train_router(exclude=[n])`．負例の数・種・ハイパーパラメータは既存と同じ．判定のとき n の one-hot を 0 にする），
  学習の要らない方式（card_sim・term_sketch・centroid_sim・multi_centroid は EQ2 で選んだ大きさ，flood_score は密ベクトル，random）．
- 評価する質問：test のうち正解の受信箱が n の問（受信箱あたり最大 20 問．最大 600 問）を，新参者を合わせて集計する（pooled）．m = 2（EQ2）．
- 再学習したルーターは制御点の `/home/denjo/atrium-data/enronqa/newcomer/` に置き（既存の `router/` とは別），`results/` には評価の値だけを書く．
- **所要時間の上限**：最初の 1 個の学習の時間 × 新参者の数 が 4 時間を超えるなら，新参者を先頭の半数（15 個．種と並びは同じ）に減らす．

**退行の確認**（一致しなければ判定に進まず，原因を調べて記録する）：全 test の hit@1・hit@2 を，ragroute-upper と学習の要らない方式について，EQ2 の `metrics.json` の値と 5e-4 以内で一致させる．
**term_sketch だけは 2e-3 以内**とする（採点が文字列のハッシュの種で揺れる．T2 の診断，journal の「T2」と backlog B8）．

**仮説**
- **H-N1**：新参者の問の pooled hit@2 で，ragroute-frozen < ragroute-upper かつ 95% ブートストラップ信頼区間が重ならなければ，「再学習しない素朴な中央方式は，新参者を見つけにくい」を支持．
- **H-N2**：flood_score と，公開情報を使う方式のうち pooled hit@2 が最良のものが，それぞれ ragroute-frozen より高く（区間が重ならない），「学習の要らない方式は，新参者の発見で再学習しない素朴な中央方式に勝る」を支持．
- 書き方の制約（D2）：ragroute は素朴な中央方式（関連の無い受信箱を 1 問あたり 10 個だけ抜き出して学習）なので，「中央方式一般」についての主張はしない．
- 参考（判定に使わない）：新参者ごとの値，新参者の問以外（既存の受信箱の問）での ragroute-frozen の hit@2．
- 限界：公開情報の大きさ（件名 40，T = 200，C = 32）と m = 2 は，新参者を含む dev で選んだ値である．

**実行**：制御点のコンテナで 1 回だけ．`results/20261014_t3_newcomer/`（`metrics.json`，`analysis_report.md`）に書く．

```bash
bash scripts/tasks/remote.sh newcomer_eval 20261014_t3_newcomer 20261009_eq2_advert_eval
```

## T2：持ち主推定の定義の統一（確定．2026-10-14 14:04．`results/20261014_t2_owner_eval`）

事前登録（下の「T2 の事前登録」）のとおり，制御点のコンテナで 1 回だけ実行した（所要 44 秒．イメージは `01e8908`．`src` は固定した `5b6c190` と同じ）．結果を見て条件・基準は変えていない．

**退行の確認：term_sketch だけ不一致．原因を特定した（p0005 §9 の 1 に当たるが，判定は続けた．下の「計画からの差」）**
- card_sim・centroid_sim・multi_centroid は，Q の top-1・E の top-1・E の AUC が EQ2 と**完全に一致**した（差 0）．
- term_sketch は Q の top-1 が 0.2922 対 EQ2 の hit@1 0.2929（差 −0.0007．2 問），E の top-1 が 0.4274 対 0.4253（差 +0.0021．3 通）で，基準（5e-4）を超えた．AUC は一致した．
- 原因：term_sketch の採点は，クエリに含まれる語の重みの和を，語の**集合**を走査して足す（`score_terms`）．足す順序が Python の文字列のハッシュの種（`PYTHONHASHSEED`）で変わり，
  浮動小数の末尾の桁がずれて，正解の受信箱との同点（Q で 88〜89 問）の扱いが 1〜3 件変わる．同じ制御点のコンテナで `PYTHONHASHSEED` だけを変えて 4 回採点すると，
  Q の top-1 は 0.2915〜0.2922，E の top-1 は 0.4240〜0.4274 に動いた（`ts_diag.out`）．EQ2 の値（0.2929・0.4253）も，同じ揺れの中にある．
  実装の誤りではなく，term_sketch の採点が持つプロセスごとの揺れである．ほかの 3 方式は和を取る順序に依存しない（内積と最大値）ので一致する．
- 影響：揺れは Q で ±0.001，E で ±0.002 程度で，下の H-O1 の差（term_sketch の 0.25 以上）に比べて十分に小さい．

**結果**（持ち主の top-1 [95%CI]．E_short は 28 トークンに切ったメール）

| 方式 | Q（質問文） | E（メール本文） | E_short（28 トークン） | Q の AUC | E の AUC | E_short の AUC | H-O1（E_short と Q） |
|---|---|---|---|---|---|---|---|
| card_sim | 0.112 [0.100, 0.123] | 0.168 [0.149, 0.188] | 0.074 [0.060, 0.088] | 0.704 | 0.760 | 0.659 | E_short の方が低い |
| term_sketch | 0.292 [0.276, 0.311] | 0.427 [0.401, 0.452] | 0.543 [0.515, 0.568] | 0.793 | 0.939 | 0.888 | 同じ長さでも，メール本文の方が持ち主を当てやすい |
| centroid_sim | 0.264 [0.248, 0.280] | 0.284 [0.260, 0.308] | 0.165 [0.146, 0.186] | 0.804 | 0.853 | 0.754 | E_short の方が低い |
| multi_centroid | 0.349 [0.332, 0.366] | 0.527 [0.502, 0.552] | 0.211 [0.190, 0.232] | 0.866 | 0.950 | 0.830 | E_short の方が低い |

**読み取れること**（EQ2 の「公開する情報は，見つけてもらうことより持ち主を当てることの方に役立つ」の訂正）
- 埋め込みを使う 3 方式（card_sim・centroid_sim・multi_centroid）は，メール本文からの持ち主の特定（E）が質問文からの発見（Q）より高いが，メール本文を質問と同じ長さ（28 トークン）に切ると，
  持ち主の特定（E_short）は質問文からの発見（Q）より**低い**．EQ2 の差は，入力の長さ（情報量）の違いで説明でき，同じ長さなら「持ち主を当てる」より「見つけてもらう」の方が高い．
  card_sim と centroid_sim では，長いメール本文でも持ち主の特定は Q と同程度にとどまる（0.168 対 0.112，0.284 対 0.264）．
- term_sketch は，28 トークンでも持ち主の特定が 0.543 で，Q（0.292）より高く，長いメール本文（0.427）よりも高い．メールの先頭の送信者・宛先・件名などの固有名詞が，語のスケッチと一致しやすいためと考えられる（未確認）．
  語のスケッチは，同じ入力の長さでも，見つけてもらうことより持ち主を当てることに役立つ方式である．
- したがって EQ2 の読み取りは，方式ごとに分けて書く：語のスケッチ（term_sketch）は持ち主の特定に強く，埋め込みを使う方式は入力の長さを揃えると持ち主の特定が弱い．公開情報の量を増やすと両方が単調に増える（H-A2）という結論は，この評価の対象ではない．
- 限界：E_short は先頭の 28 トークンで，メールの署名・引用など末尾の情報を含まない．Q はクエリ側，E_short は文書側の接頭辞なしの埋め込みで，モードが違う．
  E_short の先頭 28 トークンにメールのヘッダが多く含まれるかは確かめていない．
- **訂正（Iteration 2．値は変えない）**：ヘッダーを除いた本文の先頭 28 トークン（E_body_short）では，term_sketch の持ち主の top-1 は 0.219 で Q（0.292）より低い．上の term_sketch の「同じ長さでも，メール本文の方が持ち主を当てやすい」はヘッダー（持ち主のアドレス）によるもので，主張しない．4 方式とも，同じ長さなら持ち主の特定は Q より低い（journal の「Iteration 2 実行済み」）．

**計画からの差**：p0005 §9 の 1 は「退行の確認が一致しなければ止まる」である．term_sketch の不一致は，原因（採点の揺れ）を診断で特定し，影響（±0.002）が判定の差より十分に小さいので，止まらずに記録して続けた．
この判断は，計画の止まる条件を自動で緩めたものであり，ユーザーの確認を求める（backlog B8）．T3 の事前登録には，term_sketch の揺れを許す基準を書く．

## T2 の事前登録（2026-10-14．実行の前に push する．p0005 §4）

持ち主推定の定義を揃える．コードは `5b6c190`（`atrium owner-eval`，`src/atrium/owner_eval.py`）に固定し，結果を見て入力・大きさ・切り詰めの長さを変えない．実行は 1 回だけ．

**問い**：EQ2 の「公開する情報は，見つけてもらうことより持ち主を当てることの方に役立つ」は，入力の長さの違い（質問文は短く，メール本文は長い）で説明できるか．

**手順**：公開情報を使う 4 方式（card_sim・term_sketch・centroid_sim・multi_centroid．EQ2 で選んだ大きさ：件名 40，T = 200，C = 32）について，同じ採点の関数で 3 つの入力の top-1 と AUC を出す．
- Q：test の質問文（EQ2 と同じ 2,940 問）．E：EQ2 と同じ所有者推定用のメール（受信箱あたり最大 10 通，種 12．1,460 通）．
- E_short：E の各メールを，test の質問のトークン数の中央値（arctic-embed-m-v1.5 のトークナイザ．特殊トークンを除く）に先頭から切ったもの．E_short は文書側（接頭辞なし），Q はクエリ側（接頭辞あり）で埋め込む．
- flood_score・ragroute・random は受信箱ごとの公開情報が無いので対象外（EQ2 と同じく偶然の水準）．

**退行の確認**（一致しなければ判定に進まず，原因を調べて記録する）：Q の top-1 が EQ2 の hit@1（card_sim 0.112，term_sketch 0.293，centroid_sim 0.264，multi_centroid 0.349），
E の top-1・AUC が EQ2 の所有者 top-1・AUC（0.168/0.760，0.425/0.939，0.284/0.853，0.527/0.950）と，小数 3 桁（差 5e-4 以内）で一致すること．

**H-O1**（方式ごとに記録．全体の判定は付けない）：95% ブートストラップ信頼区間で，E_short の top-1 が Q の top-1 より高く，区間が重ならなければ「同じ長さでも，メール本文の方が持ち主を当てやすい」，
区間が重なれば「EQ2 の差は入力の長さで説明できる」，E_short の区間が Q より下なら「E_short の方が低い」と書く．

**予想**（判定に使わない）：term_sketch は送信者の名前などの固有名詞で，短くてもメール本文の方が当てやすい．

**限界**：E_short は先頭を切るので，署名・引用など末尾の情報は含まない．Q と E_short の埋め込みのモード（クエリ側と文書側）が違う．

**実行**：制御点のコンテナで 1 回だけ．`results/20261014_t2_owner_eval/`（`metrics.json`，`analysis_report.md`）に書く．

```bash
bash scripts/tasks/remote.sh owner_eval 20261014_t2_owner_eval 20261009_eq2_advert_eval
```

## 判定の補正の確定と EQ8 の読み方の訂正（2026-10-14．p0005 T1．`results/20261014_t1_judge_audit/`）

既存の `results.jsonl`・`judgements.jsonl` を読み直しただけで，実験は流していない．訂正する節は「EQ5：RQ-B の判定」（判定モデルの偽陽性）と「EQ8〜EQ10」の EQ8（判定の読み方の注意）である．過去の節は書き換えていない．

**決定（ユーザー．p0005 §1）**
- D3：0.6B の正答率の主な値は，事前登録どおり**判定モデル（qwen3:8b）の元の値**とし，補正値を併記する．
- D4：補正の定義は**狭い補正**（`final_answer` が例文 `The answer to the question` **だけ**の回答を不正解に直す）．
  広い補正（例文を**含む**回答を全て不正解に直す）は，EQ5 の B2（CPU の受信箱）で例文を含み中身がある回答のうち判定が正解とした 213 件から 30 件を目視したところ，27 件が正解と照らして正しい答えだったので，誤りである．
- D2：中央の分類器（ragroute．EnronQA の test で hit@1 0.215）は学習し直さない．文書・表では「素朴な中央方式（naive central classifier）」と呼び，中央方式一般についての主張はしない．

**定義**：例文だけの回答 = `re.fullmatch(r"\s*the answer to the question\s*[.:]?\s*", final_answer, re.IGNORECASE)`．
狭い補正の正答率 =（判定が正解とした数 − 例文だけの回答で正解とされた数）／ 問の数．判定できなかった出力は不正解．CPU の受信箱 = CPU の専門家（13.x・14.x）が持つ受信箱の質問 1,940 問．

**検算：一致した**（plan の値．小数 3 桁．B2・B3 のプロンプトのまま返した回答の数は全 2,940 問で数えた値）

| 実行 | 例文だけ（うち正解） | 元の正答率 | 狭い補正 | プロンプトのまま（うち正解．全問） |
|---|---|---|---|---|
| EQ5 B2（`20261009_eq5_b2`） | 696（71） | 0.297 | 0.261 | 40（6） |
| EQ5 B3 | 877（88） | 0.260 | 0.215 | 113（22） |
| EQ8 B2 k = 1 | 1,021（85） | 0.249 | 0.205 | 48（14） |
| EQ8 B2 k = 3 | 878（63） | 0.258 | 0.226 | 94（16） |
| EQ8 B2 k = 5 | 697（70） | 0.297 | 0.261 | 40（7） |
| EQ9 B3 k = 5 | 916（95） | 0.254 | 0.205 | 108（18） |
| EQ5 B1・B1 flood_score・B2'・B4，EQ8 B1・B2' の全 k | 0 | 不変 | 不変 | 0〜9（正解 0〜2） |

全 16 実行の表は `judge_audit.out` にある．例文だけの回答は B2（0.6B）と B3（0.6B）にしか無い．

**訂正**
- EQ8 の「B2 で k を増やして上がった分の多くは偽陽性で，補正すると差はほぼ消える」という読み方（副指標 0.147・0.142・0.154）は，**誤った広い補正に基づく**．
  狭い補正では 0.205 → 0.226 → 0.261 と，k とともに上がる．文脈が増えると，例文だけの回答が減る（1,021 → 878 → 697）．
  EQ8 の判定（Holm 補正後の 6 組）は元の値によるもので，変わらない．
- EQ5 の「結論の向きは変わらない」は，狭い補正でも成り立つ：B2 対 B3（CPU の受信箱）は 0.261 対 0.215，B2 対 B1 は 0.261 対 0.789．0.6B の条件の偽陽性による上乗せは 0.032〜0.049 である．
- 限界：プロンプトをそのまま返した回答（`Emails:` で始まる）は，末尾に正しい答えを含むもの（回答の抽出の失敗）と，メールの本文を吐き出したもの（偽陽性の疑い）が混ざる．
  正解とされた数は最大で B3 の 22 / 2,940（約 0.7 pt）なので補正せず，限界として記録する．
- 計画からの差：p0005 §3.2 は CPU の受信箱を `selected_sources[0]` で決めるが，flood_score の実行では選んだ先が正解の受信箱と限らないので，質問の置き場所（正解の受信箱．`bank`）で分けた．
  routing=oracle の実行では同じ値になる（検算の全項目が一致した）．

## EQ7b：実機の参加・離脱の再生（確定．2026-10-14 03:53 に全 22 回が終了．`results/20261013_eq7b_*`，集計は `results/20261013_eq7b_summary/final/`）

事前登録（下の「EQ7b の事前登録」）のとおりに 22 回を流した．結果を見て条件・基準は変えていない．失敗した問は全 22 回で 0（HTTP のエラー以外の失敗も無い）．
1 回の所要は 2.6〜42 分（平均 20 分）．

**判定**
- **退行ゲート：通過**．a = 1 の hit@2 が，multi_centroid 0.4401・flood_score 0.8041 で EQ7a（＝ EQ2）と 0 問の差で一致した．
- **H-C2（EQ7a の再現）：成立**．本番の 20 回すべてで，実機の hit@2 が EQ7a の同じ条件の値と**小数 4 桁まで一致した**（最大の差 0.00000．基準は 2 問分 = 0.00068 以内）．
  EQ7a の規則（メンバー表・離脱したノードの扱い・返事のあった受信箱の中から選ぶ規則）が，実機の質問者とノード 30 台の振る舞いと同じであることを示す．
- **H-C1（実機）：支持**．a = 0.7 で R = 1 → 1000 の hit@2 の相対的な低下は，multi_centroid（0.314，0.321，0.262，0.255，0.278）が flood_score（0.298，0.310，0.240，0.213，0.255）より，
  5 個の種のすべてで大きかった（差は +0.011〜+0.041）．数値は EQ7a と同じなので，EQ7a の限界（差が小さい）もそのまま当てはまる．

**a = 0.7 の平均（5 個の種）**

| 方式 | R | hit@2 | 正解が稼働中のときの hit@2 | 問い合わせたノード／問 | うち離脱していたノード |
|---|---|---|---|---|---|
| multi_centroid | 1 | 0.334 | 0.477 | 1.94 | 0 |
| multi_centroid | 1000 | 0.238 | 0.340 | 1.94 | 0.55 |
| flood_score | 1 | 0.580 | 0.828 | 21.08 | 0 |
| flood_score | 1000 | 0.427 | 0.610 | 20.84 | 5.77 |

**考察**
- 絶対値では，どの条件でも flood_score の hit@2 が multi_centroid を大きく上回る（R = 1000 で 0.427 対 0.238）．ただし flood_score は 1 問に約 21 台へ問い合わせる（multi_centroid は 1.9 台）．
  メンバー表が古くなったときの低下の割合は，2 方式で似ている（0.21〜0.32）．「公開情報を使う方式は，表が古いと大きく hit を失う」とは言えず，EQ7a の考察と同じである．
- 無駄な問い合わせの割合は，R = 1000 で方式によらず約 28%（multi_centroid 0.55 / 1.94，flood_score 5.77 / 20.84）．離脱の検出が遅れる影響は，稼働率にほぼ比例する．
- 質問者の変更（離脱を許す．`experiment.churn` を指定した実験だけ）は，離脱の無い条件の結果を変えなかった（退行ゲート）．
- **EQ7a の未検証の項目**：「R が大きいと，正解が稼働中でも hit が 3 割前後下がる原因は，表の更新の後に参加したノードが見えないこと」は，EQ7b でも分けて数えていないので未検証のままである
  （実機と EQ7a が完全に一致するため，EQ7b はこの点について新しい情報を与えない）．

**限界**（事前登録のとおり）：離脱は「プロセスが生きたまま 503 を返す」ことで起こしたので，ホストの停止・接続拒否・タイムアウトによる検出の遅れは測っていない．
刻みは質問の番号で，質問は 1 問ずつ流したため，同時の質問による待ち行列は含まない（e2e は 0.05〜0.12 秒．EQ3 の待ち時間とは比べられない）．メールの時間的な到着は再現していない．

**退行ゲートの 2 回（先に確定した記録）**

- 事前登録（下の「EQ7b の事前登録」）の 22 回のうち，退行ゲートの 2 回（稼働率 1）を先に流した．本番の 20 回はこの後に流す．
- イメージは `d59123a-dirty`（事前登録のコミット．`src` は固定した `5613f8c` と同じ．dirty は連続実行が書き換える `config.yaml` だけ）．

| 方式 | 失敗 | hit@2（実機） | EQ7a の離脱なしの hit@2 | 差 | 問い合わせたノード／問 | e2e p50 / p95 (s) | 所要 |
|---|---|---|---|---|---|---|---|
| multi_centroid | 0 / 2,940 | 0.4401 | 0.4401 | 0 問 | 1.96 | 0.124 / 0.153 | 42 分 |
| flood_score | 0 / 2,940 | 0.8041 | 0.8041 | 0 問 | 30.00 | 0.089 / 0.100 | 40 分 |

- **退行ゲート：通過**．2 方式とも，実機の hit@2 が EQ7a（＝ EQ2）の値と完全に一致した（基準は 1 問以内）．離脱を許す質問者の変更は，離脱の無い条件の結果を変えていない．
- 観察（判定には使わない）：parallel 1 の flood_score の e2e の中央値は 0.089 秒で，EQ3（parallel 30）の 92.2 秒の約 1/1000 である．
  EQ3 の待ち時間のほとんどは，30 問が同時に 30 台へ probe を送ったときの待ち行列だったことになる．
  multi_centroid（0.124 秒）より速いのは，multi_centroid が 1 問ごとに 150 受信箱 × 32 重心のスコアを質問者の CPU で計算するためと考えられる（未検証）．
- 集計のコマンドの修正（`atrium churn-live-report`．集計だけで，実行の経路は変えていない）：22 回が揃う前に集計すると H-C1 の判定で止まった（`KeyError`）ので，
  判定に要る実行が揃うまで H-C1 を判定しないようにした．また，比べる実行が 0 回のときに H-C2 が「成立」と出ていたので，「未判定」と出すようにした．

## EQ7b の事前登録（2026-10-13．実行の前に push する．p0004 §6.4）

EQ7a（実機を使わない）の仕組みを，実機のノード 30 台と実機の質問者で再生して確かめる．コードは `5613f8c`（`experiment.churn`，`atrium/churn_live.py`）に固定し，
結果を見て条件・基準を変えない．

**問い**：EQ7a の（メンバー表が古いときの）hit@2 は，実機で再現するか．H-C1 は実機でも支持されるか．

**手順**
- 質問は EQ2 の test の 2,940 問を，EQ7a と同じ順（`experiment.seed` = 12 の置換）で **1 問ずつ順に**流す（`parallel: 1`．1 問 = 1 刻み）．m = 2．
- 各ノードの稼働状態は，EQ7a と同じ時刻表（2 状態のマルコフ連鎖．稼働中の平均 200 刻み．種は `[12, schedule_seed, 稼働率×100]`）を，刻みごとに `/v1/admin/availability` でノードへ伝える．
  離脱中のノードは検索・probe・プロファイルの要求を 503 で拒否する．
- 質問者は，`refresh_steps` 刻みごとにだけ，全ノードの `/v1/profile` を引いて返事のあったノードをメンバー表にする．
  公開情報を使う方式は，表に載っているデータ源の中から上位 m 個を選び，離脱していても問い合わせる．`flood_score` は表のノード全部の `/v1/probe` に問い合わせ，返事のあった受信箱の中から選ぶ．
  質問者は，離脱による要求の失敗（HTTP のエラー・接続の失敗）を問の失敗にせず，返事のあった分で続ける．
- 実行は 22 回（`results/20261013_eq7b_summary/eq7b_specs.txt`．`eq8_chain.sh` で 1 回ずつ deploy → start → analyze）：
  - 退行ゲート 2 回：a = 1，R = 1，種 0，multi_centroid と flood_score．
  - 本番 20 回：a = 0.7，R ∈ {1, 1000}，種 0〜4，multi_centroid と flood_score．
- 集計：`atrium churn-live-report`．hit@2・正解が稼働中の割合・問い合わせたノード・うち離脱していたノードを，EQ7a と同じ定義で数える（失敗した問は hit = 0）．

**判定**
- **退行ゲート**：a = 1 の hit@2 が，EQ7a の離脱なしの値（multi_centroid 0.440，flood_score 0.804．EQ2 と同じ）と 1 問（1/2940）以内で一致すること．
  一致しなければ本番の判定に進まず，原因を調べて記録する．
- **H-C2（EQ7a の再現）**：本番の全 20 回で，|実機の hit@2 − EQ7a の同じ条件の hit@2| が 2 問分（2/2940）以内なら「EQ7a の仕組みは実機で再現した」．超える回があれば，どの条件か・原因の候補を記録する．
- **H-C1（実機）**：EQ7a と同じ規則．a = 0.7 で R = 1 → 1000 の hit@2 の相対的な低下が，multi_centroid では flood_score より，5 個の種のすべてで大きければ支持．
- 記述のみ（判定に使わない）：e2e の中央値，問い合わせたノードの数と離脱していたノードの数，失敗した問の数（HTTP のエラー以外の失敗は 0 であるはず）．

**予想**（判定に使わない）：実機の質問者の振る舞いは EQ7a の仮定と同じ規則なので，hit@2 は EQ7a と（ほぼ）一致する．H-C1 の判定も EQ7a と同じになる．

**限界**
- ノードの離脱は「プロセスが生きたまま 503 を返す」ことで起こす．ホストの停止や接続拒否（TCP の RST）・タイムアウトは起こさないので，離脱の検出の遅さは測れない．
- 刻みは質問の番号で，壁時計時間ではない．質問は 1 問ずつ流すので，同時に流したときの待ち行列の影響は含まない（e2e は EQ3 の値と比べられない）．
- 表の更新の後に参加したノードの見えなさは，EQ7a と同じく分けて数えない．メールの時間的な到着は再現しない．

## s8：MedRAG の cross_encoder（確定．2026-10-13 12:29．`results/20261007_s8_med_all_ce`）

- B4 の撤回（2026-10-12）を受け，同じ run_id で再開して全 7,663 問を終えた．**失敗 0**．
  質問者の LLM は wafl500（`cluster.requester_llm`），専門家は先頭 16 台である．
- **正答率 0.689**［0.678, 0.699］（test 4,599 問は 0.690）．routing=all・merge=score の s4（0.659）との McNemar の正確検定は，
  共通 7,606 問で s8 のみ正解 879 問・s4 のみ正解 655 問，p = 1.2e-8．RAG なしの s7（0.640）との差は p = 1.8e-17 である．
  問題集ごとの s8：bioasq 0.875，medmcqa 0.634，medqa 0.667，mmlu 0.792，pubmedqa 0.742（pubmedqa だけ s4 の 0.766 より低い）．
- 判定：**cross_encoder の再ランクは，score の統合より正答率が +3.0 pt 高い（p < 0.05）**．ただし s4 との差には，統合方式だけでなく
  質問者の LLM の置き場所（wafl-ctrl5 → wafl500）と，s4 の失敗 57 問の除外による共通問の違いが混ざる．
  生成の待ち時間は p50 6.9 秒（s4 は 21.0 秒）と大きく違うので，LLM の置き場所を揃えた比較は未実施である．
- 待ち時間：merge_s の p50 22.1 秒（cross_encoder の再ランク），e2e p50 31.5 秒・p95 54.5 秒．
- 実行の経緯：最初の 1,191 問は 2026-10-07〜09 のコード（`9f115ae-dirty`，`llm.timeout_s` = 600）で，残りの 6,472 問は
  `16febdc-dirty`（`llm.timeout_s` = 1800）で流した．`run_meta.json` の `git_head` は再開時の値である．
  再開時，2026-10-09 の手動停止が残した `.exit`（値 0）を，start が「完了済み」と見なしたので，制御点で削除した．
- ここでも，MedRAG の正答率はルーティングではなく統合方式と生成側の条件に反応する（E1 の判定と整合する）．

## S6：MedRAG の宿る型のパイロット（確定．2026-10-12 19:45．`results/20261012b_e3_{smoke,pilot}`）

- B4 の保留を 2026-10-12 にユーザーが撤回し，p0003 S6 を実行した（`s6_run.sh`．routing=all，answer_mode=local_answer，merge=score）．
- **1 回目の smoke（`20261012_e3_smoke`）**：10 問中 2 問が失敗した（専門家 13.103 が 502）．原因は専門家側の Ollama 呼び出しが
  `llm.timeout_s`（600 秒）で切れたこと（e2e 602.9 秒）．`llm.timeout_s` を 1800 にして（`f3e50e3`）やり直した．
- **2 回目の smoke（`20261012b_e3_smoke`）**：10 問すべて成功（1,779 秒）．
- **pilot（`20261012b_e3_pilot`，100 問）**：**失敗 64 問**（成功 36 問，所要 13,090 秒）．p0003 S6 の基準（失敗 0・3 時間以内）を満たさないので，
  pilot50 は実行していない．判定：**不成功（基盤の限界）**．
  - 失敗は問題集に偏る．medmcqa 20/20，pubmedqa 20/20，mmlu 9/20，medqa 10/20，bioasq 5/20 が失敗した．
  - 原因の確認：専門家 13.103（wafl303，メモリ 16 GB）のカーネルログに，`llama-server`（qwen3:0.6b）の OOM kill がある
    （anon-rss 9.2 GB．19:36:58）．Ollama は 500 を返し，専門家は 502，質問者は失敗として数えた．7 問は質問者側の ReadTimeout である．
  - 推定される機序（未検証）：`llm.num_ctx` = 16384 に，k_context = 15 の断片（約 4,800〜6,700 トークン）を渡し，
    同じ専門家に `experiment.parallel` = 4 の要求が重なって，KV キャッシュと検索のシャード（`cluster.shard_budget_gb` = 6）が
    16 GB のメモリを超える．
- 成功した 36 問の e2e の中央値は約 534 秒である．この条件で 7,663 問を流すのは現実的でない．
- 参考（主張には使わない）：成功した問の正答率は smoke 0.50（10 問），pilot 0.58 [0.42, 0.73]（36 問）．失敗が問題集に偏るので，
  s4（0.659）などとは比べられない．
- 次の候補は (1) `experiment.parallel` を 1 に下げる，(2) `llm.num_ctx` または `local_answer.k_context` を下げる，
  (3) 宿る型の主評価は EnronQA の RQ-B に任せ，MedRAG の宿る型は不成功として閉じる，の 3 つだった．
  **2026-10-13 にユーザーが (3) を選び，MedRAG の宿る型は不成功として閉じた**（backlog B6）．
  (1) は 1 問約 9 分で全問に現実的でなく，(2) は E1 と文脈の条件が変わって比較が崩れるためである．

## EQ7a：RQ-C のオフラインの評価（確定．2026-10-12 12:19．`results/20261012_eq7a_churn`）

- 事前登録（下の「EQ7a の事前登録」．`f5e663d` で評価の前に push 済み）のとおり，制御点のコンテナで **1 回だけ**実行した（所要 50 秒）．
  実行したコードは `03698c3` で，`src`・`tests`・`scripts` は事前登録の `34258f7` と同じ．結果を見て条件・基準は変えていない．
- **退行ゲート：通過**．離脱なし（a = 1）の hit@2 が，6 方式すべてで EQ2 の test の値と一致した（差は +0.0000．許容は 1/2940）．
  card_sim 0.153，term_sketch 0.359，centroid_sim 0.323，multi_centroid 0.440，flood_score 0.804，ragroute 0.279．
- **H-C1：支持**（a = 0.7，R = 1 → 1000 の hit@2 の相対的な低下）．multi_centroid の低下は 5 個の種のすべてで flood_score より大きかった（差は +0.011〜+0.041）．
  平均では multi_centroid が 0.286，flood_score が 0.263 で，差は 0.023 にとどまる．

| 方式（a = 0.7） | 相対的な低下の平均 | flood_score との差（種ごとの最小〜最大） |
|---|---|---|
| multi_centroid（判定の対象） | 0.286 | +0.011〜+0.041 |
| card_sim | 0.292 | −0.020〜+0.081 |
| term_sketch | 0.279 | −0.027〜+0.044 |
| centroid_sim | 0.267 | −0.024〜+0.045 |
| ragroute | 0.268 | −0.023〜+0.037 |
| flood_score | 0.263 | － |

- **判定の読み方の注意**（判定は書き換えない）：
  - 事前登録した判定は，multi_centroid 1 方式についてである．他の 4 方式は，どれも全ての種で flood_score を上回るとは言えない（参考の表）．
    「公開情報を使う方式は，メンバー表が古いと flood_score より大きく hit を失う」と一般化することはできない．
  - 差が小さい（平均 0.023）うえに，方式ごとの低下の範囲は flood_score と重なる（multi_centroid 0.255〜0.321，flood_score 0.213〜0.310）．支持は，5 個の種で向きがそろったという意味に限る．
  - **絶対値では，どの条件でも flood_score の hit が公開情報を使う方式を大きく上回る**．a = 0.7，R = 1000 で flood_score 0.427 に対し multi_centroid 0.238，
    a = 0.5，R = 1000 で 0.239 に対し 0.136．ただし flood_score は 1 問に平均 20.8 台（a = 0.7，R = 1000）〜15.3 台（a = 0.5）に問い合わせ，multi_centroid は 1.9 台である．
  - 事前登録の予想は「離脱済みの受信箱が上位 m 個の枠を占めて hit を失う（公開情報を使う方式）」だった．データは，この効果が小さいことを示す．
    メンバー表が古いときの低下は，全ての方式で同じ程度（平均 0.26〜0.29）に起きた．
    正解が稼働中の問に限った hit@2 も，どの方式でも R = 1 から R = 1000 で 3 割前後下がる（flood_score 0.828 → 0.610，multi_centroid 0.477 → 0.340）．
    考えられる原因は，表の更新の後に参加したノードが見えず，正解の受信箱が稼働中でも問い合わせられないこと（**未検証**．見えていたかどうかを分けて数えていない）．
- 無駄な問い合わせ（問い合わせたノードのうち，離脱していたノード）の割合は，方式によらず同じ程度である（a = 0.7，R = 1000 で multi_centroid 0.55 / 1.94，flood_score 5.77 / 20.84．どちらも約 28%）．
  絶対数は flood_score の方が約 10 倍多い．
- 参考：a = 0.7 で R = 1 のとき，正解が稼働中の問に限った hit@2 は，離脱なしの値より高い（multi_centroid 0.477 対 0.440，flood_score 0.828 対 0.804）．
  稼働中の受信箱が減って，競合が減るためと考えられる（未検証）．
- **限界**（事前登録のとおり）：flood_score は「返事のあった分で続ける」仮定の上限で，今の質問者の実装の挙動ではない（1 台でも失敗すると問全体が失敗する）．
  メールの時間的な到着は再現していない．回答の正答率と待ち時間は測っていない（実機の EQ7b は 2026-10-14 に行い，hit@2 は全 22 回で EQ7a と一致した．上の「EQ7b」）．
- 次に調べる候補（未登録）：表の更新の後に参加したノードの見えなさを分けて数える．質問者が部分的な失敗を許す実装にして，実機で EQ7b を行う（後者は実施済み．上の「EQ7b」）．

## E2 の下流確認（確定．2026-10-12 11:44．p0003 S5．`results/20261012_e2_desc_sim`）

- 条件：`dataset: medrag`，`routing: card_sim`（S4 の `desc_sim`．`routing.top_m.medrag` = 3），`answer_mode: retrieval_only`，`merge: score`，全 7,663 問．
  イメージは `ac63bc6`．`-dirty` は `config.yaml` の `routing` の書き換えによる（`src` の差分は無い）．質問者の起動時に，自己紹介文を S4 と同じ埋め込みで広告に付けた（`244b4b4`）．
- **失敗は 0 / 7,663**（p0003 S5 の完了条件「失敗率 1% 以下」を満たす）．所要時間は 09:31〜11:44 の 2.2 時間（見込み 2.5 時間以下）．
  実行前のディスク使用率は，専門家 16 台と制御点のすべてで 70% 未満だった．実行後に `mise run stop` し，`config.yaml` は元に戻っている．
- 全問で，3 源（シャード 11.86 個／問．16 個のうち）に問い合わせた．e2e は p50 4.63 秒・p95 4.67 秒（`retrieval_only`．埋め込み 0.009 秒）．事前に公開した情報は 238 バイト．

**データ源の選択（test 4,599 問．E1 の同じ質問の値と並べる）**

| 方式 | 適合率 | 再現率 | F1 | 平均問い合わせ源 | 削減率 |
|---|---|---|---|---|---|
| all（s3） | 0.445 | 1.000 | 0.616 | 4.00 | 0 |
| ragroute（s5） | 0.865 | 0.799 | 0.830 | 1.65 | 0.589 |
| random（s6．3 源） | 0.445 | 0.749 | 0.558 | 3.00 | 0.250 |
| **desc_sim（今回．3 源）** | 0.505 | 0.850 | 0.634 | 3.00 | 0.250 |

- 同じ 3 源に問い合わせる random と比べて，desc_sim は再現率（0.850 対 0.749）と F1（0.634 対 0.558）が高い．差の検定は事前登録していないので，記述にとどめる．
- 学習済みの ragroute は，より少ない問い合わせ（1.65 源）で F1 が高い（0.830）．全データ源で学習済みの通常の条件では，desc_sim は ragroute に及ばない．
  desc_sim が使えるのは，S4 で測った「再学習なしで新参源を見つける」場面である（新参源の再現率 0.830 対 ragroute-frozen 0.354）．
- S4 のオフラインの値（4 源がそろった世界で k = 3 の `train`・`val` の F1 = 0.637）と，実機の test の F1（0.634）はほぼ同じである．実機の実装は，オフラインの評価と一致した．

**`top_doc_ids` と s3（routing=all）の比較**

- 上位の文書のうち s3 にも現れた割合は，全 7,663 問で 0.908（上位の列が完全に一致したのは 73.5%），test 4,599 問で 0.906（完全一致 73.3%）．
- この差は，落とした源の文書が抜けた分だけで説明できる．s3 の上位 15 件のうち，選ばれた源の文書の割合の平均は 0.9061（test）で，実測の 0.906 と同じである．
  4,599 問のうち 4,597 問で，s3 との共通の文書の数が「s3 の上位のうち選ばれた源の文書の数」と一致した．残りの 2 問は一致しなかった（原因は未確認）．
  したがって，検索そのものは s3 と同じで，違いは問い合わせ先を絞ったことだけから生じている．
- 文書の源は文書 ID の接頭辞で判定した（`pubmed`・`wiki`・`article` はそれぞれ PubMed・Wikipedia・StatPearls，それ以外は Textbooks）．
  最初は接頭辞の対応表が不完全で，615 問が不一致になった．教科書の接頭辞を直して，2 問になった．

**p0003 の完了の条件**：S1〜S5 を実行した．**S6（E3 のパイロット）は保留のままで，実行していない**（backlog B4．p0004 §10・U1．2026-10-12 にユーザーが保留の維持を選んだ）．
s8（cross_encoder の全問）も同じ理由で保留である．途中の結果（1,191 問）は制御点の `results/20261007_s8_med_all_ce/` に残してあり，再開する場合は B4 の手順に従う．
（2026-10-13 の追記：保留は 2026-10-12 に解除され，S6 は不成功（「S6」．B6 で閉じた），s8 は完了した（「s8」）．これで p0003 の S1〜S6 は全て実行済みである．）

## EQ7a の事前登録（2026-10-12．評価の前に push する．p0004 §6.4・§8.7）

RQ-C（専門家の参加・離脱）のうち，実機を使わずに測れる部分だけを先に測る（EQ7a）．実機の時刻表の再生（EQ7b）は行わない（下の「限界」）．
コードは `34258f7`（branch `eq7-churn`．`atrium churn-eval`，`src/atrium/churn_eval.py`）に固定する．結果を見て条件・基準を変えない．

**問い**：質問者のメンバー表が古くなると，公開情報を使う方式は，公開情報を使わない flood_score より大きく hit を失うか．

**手順**
- 質問は EQ2 の test の 2,940 問．方式のスコアは 1 回だけ計算する（公開情報の大きさは EQ2 で選んだ値：件名 40，T = 200，C = 32．m = 2）．
- 受信箱は EQ5 の配置（`results/20261009_eq5_b1/placement.json`．30 台に 5 受信箱ずつ）に従い，ノード単位で参加・離脱する．
- 各ノードの状態は 2 状態のマルコフ連鎖（1 問 = 1 刻み）．稼働率 a ∈ {1.0, 0.9, 0.7, 0.5}，稼働中の平均の長さ 200 問，初期値は定常分布．
  質問の順番は `experiment.seed`（12）で並べ替える．時刻表の種は 5 個．
- 質問者はメンバー表を R 問ごとにだけ更新する．R ∈ {1, 10, 100, 1000}．各問で見えているのは，直前の更新の時点で稼働中だったノードである．
- 方式：card_sim，term_sketch，centroid_sim，multi_centroid，ragroute は，見えている受信箱の中でスコアの上位 m 個に問い合わせる（離脱済みのノードも枠を占める）．
  flood_score は，見えているノード全部に問い合わせ，返事のあった受信箱の中で上位 m 個を選ぶ．
- 指標：hit@2（正解の受信箱が稼働中で，選ばれた 2 個に入る割合），正解が稼働中の問に限った hit@2，正解が稼働中の割合，問い合わせたノードの数，うち離脱していたノードの数．
  値は時刻表の種をまたいだ平均 [最小, 最大] で出す．

**退行ゲート**：a = 1 の hit@2 が，どの方式でも EQ2 の test の hit@2 と 1 問（1/2940）以内で一致すること．
一致しなければ判定に進まず，原因を調べて記録する（card_sim は埋め込みを計算し直すので，末尾の桁が揺れうる）．

**仮説 H-C1**：a = 0.7 で，R = 1 から R = 1000 にしたときの hit@2 の相対的な低下（1 − hit(R=1000) / hit(R=1)）が，
multi_centroid では flood_score より，**5 個の時刻表の種のすべてで**大きければ「支持」，そうでなければ「支持されない」とする．
multi_centroid を選んだのは，EQ2 で公開情報を使う方式の中で hit が最も高かったためである．他の方式は参考として同じ表に出し，判定には使わない．

**予想**（判定には使わない）：公開情報を使う方式は，古い表に残った離脱済みの受信箱が上位 m 個の枠を占めるので，R が大きいほど hit を失う．
flood_score は返事のあった受信箱から選ぶので枠を奪われず，失うのは無駄な問い合わせ（離脱したノードへの送信）の方である．

**限界**
- 実機のノードは止めない．質問者は 1 台でも失敗すると問全体を失敗にする（`gather_or_cancel`）ので，ノードを止めると flood_score の全問が失敗する．
  この評価の flood_score は「返事のあった分で続ける」と仮定した上限であり，現在の実装の挙動ではない．
- メールの時間的な到着・削除は再現しない（EnronQA に日付のフィールドが無い）．離脱はノード単位で，受信箱の内容は変わらない．
- 回答の正答率と待ち時間は測らない（EQ7b．実機の質問者を部分的な失敗を許す形に直す変更が要るので，この事前登録には含めない）．

**実行**：制御点のコンテナで 1 回だけ．`results/20261012_eq7a_churn/`（`metrics.json`，`analysis_report.md`）に書く．

```bash
bash scripts/tasks/remote.sh churn_eval 20261012_eq7a_churn 20261009_eq5_b1 20261009_eq2_advert_eval
```

## EQ8〜EQ10：文脈の通数・流し直しの揺れ・待ち時間（確定．2026-10-12 04:17 に全 28 実行が終了．`results/20261011_eq{8,9,10}_*`，集計は `results/20261011_eq8_summary/`）

- 事前登録（下の「EQ8〜EQ10 の事前登録」，実行の前に push 済み）のとおりに流した．結果を見て基準や比べる組は変えていない．
- 28 実行はすべて `b9e25ba26bcf-dirty` で動いた（dirty は連続実行が書き換える `config.yaml` と `eq8_chain.sh` だけで，`src` に差分は無い）．受信箱の配置は EQ5 と同じ（ハッシュ `1d99505d5a17`）．
- 失敗した問は全 28 実行で 0．EQ8 の B2 k = 5 と B2' k = 5 は，start が終了コード 2 で終わったので，同じ run_id で 1 回だけ再開した（流し直したのはそれぞれ 1 問と 4 問）．重複した 5 問は，最後の行（成功）を集計に使った．
- 集計：`eq8_summary.py`（EQ8・EQ9）と `eq10_summary.py`（EQ10）．出力は同じ場所の `eq8_summary.out` と `eq10_summary.out`．実行名の `b2_1p7b` が B2' である．

**EQ8（文脈の通数と正答率．CPU の受信箱 1,940 問）**

| 条件 | k | 正答率 [95%CI] | ひな形を不正解に直した正答率 | 入力トークン p50 |
|---|---|---|---|---|
| B1 | 1 | 0.687 [0.666, 0.707] | 0.687 | 572 |
| B1 | 3 | 0.765 [0.746, 0.784] | 0.762 | 1528 |
| B1 | 5 | 0.786 [0.767, 0.804] | 0.782 | 2475 |
| B2 | 1 | 0.249 [0.230, 0.269] | 0.147 | 599 |
| B2 | 3 | 0.258 [0.239, 0.278] | 0.142 | 1600 |
| B2 | 5 | 0.297 [0.277, 0.318] | 0.154 | 2596 |
| B2' | 1 | 0.625 [0.603, 0.646] | 0.625 | 599 |
| B2' | 3 | 0.647 [0.626, 0.668] | 0.647 | 1600 |
| B2' | 5 | 0.651 [0.630, 0.672] | 0.650 | 2596 |

| 条件 | 組 | 差 | 片方だけ正解 | 補正後の p（Holm，6 組） | 判定 |
|---|---|---|---|---|---|
| B1 | k=1 対 k=5 | −0.099 | 133 対 325 | 7.73e-19 | 差がある |
| B1 | k=3 対 k=5 | −0.021 | 103 対 143 | 3.82e-02 | 差がある |
| B2 | k=1 対 k=5 | −0.048 | 213 対 306 | 2.58e-04 | 差がある |
| B2 | k=3 対 k=5 | −0.039 | 201 対 276 | 2.75e-03 | 差がある |
| B2' | k=1 対 k=5 | −0.026 | 193 対 244 | 3.82e-02 | 差がある |
| B2' | k=3 対 k=5 | −0.004 | 159 対 166 | 7.39e-01 | 差があるとは言えない |

- **判定**：6 組のうち 5 組で差があり，どれも k = 5 のほうが高い．B2' の k = 3 と k = 5 だけは差があるとは言えない．予想（k を減らすと下がる）と合う．
- 外に出た本文は B1 で 1 問あたり k 件ちょうど（1.00・3.00・5.00），B2・B2' は 0．
- 参考（GPU の受信箱 1,000 問．補正しない）：3 条件とも k = 1 対 k = 5 は p < 1e-7 で k = 5 が高い．k = 3 対 k = 5 はどれも p > 0.4 で，k = 3 のほうがわずかに高い．
- **判定の読み方の注意**（判定は書き換えない）：B2 で k を増やして上がった分の多くは，判定モデルがひな形の文を正解とみなした分である．副指標（ひな形を不正解に直した正答率）では 0.147・0.142・0.154 で，k による違いはほぼ消える．B2 の CPU の専門家の答えでは，文脈を増やしても正しく答えられる問はほとんど増えていない．

**EQ9（流し直しによる揺れ．CPU の受信箱 1,940 問）**

| 条件 | final_answer の一致 | 正答率 今回 | 正答率 EQ5 | 差 | 今回だけ正解 対 EQ5 だけ正解 | p |
|---|---|---|---|---|---|---|
| B1 | 0.812 | 0.786 | 0.789 | −0.003 | 41 対 46 | 0.668 |
| B2 | 0.921 | 0.297 | 0.297 | −0.001 | 58 対 59 | 1.000 |
| B2' | 0.998 | 0.651 | 0.650 | +0.001 | 22 対 20 | 0.878 |
| B3 | 0.602 | 0.254 | 0.260 | −0.006 | 134 対 146 | 0.511 |

- **流し直しの幅は 0.006**（B3）．EQ5 の条件間の差（15 組）のうち，これより小さいのは B1 と B4（0.001）だけで，この組は「流し方の揺れと区別できない」．
  B1 flood_score と B2'（0.009）は幅をわずかに超えるだけなので，差として読むには弱い．他の 13 組は 0.037 以上．
- `final_answer` は B3 で 4 割，B1 で 2 割の問が変わったが，正誤が両方向にほぼ同じ数だけ入れ替わり，正答率はほとんど動かなかった．
  p0004 §8.7 の「正答率の実行は各条件 1 回」は，差が 0.01 を大きく超える比較に限れば保てる．
- **H-B1 の再判定**：今回は B2 0.297 対 B3 0.254（B2 だけ正解 264，B3 だけ正解 181），p = 9.72e-05 で同等とは言えない．EQ5（p = 3.73e-04）と同じ結論．EQ5 の判定は書き換えない．

**EQ10（待ち時間．300 問 × 3 回．p50 は 3 回の中央値 [最小〜最大]，単位は秒）**

| 条件 | 置き場所 | e2e p50 | e2e p95 | generate p50 |
|---|---|---|---|---|
| B2 k=1 | cpu | 6.16 [6.14〜6.21] | 10.47 | 6.16 |
| B2 k=3 | cpu | 13.62 [13.59〜13.65] | 26.78 | 13.62 |
| B2 k=5（EQ6） | cpu | 23.73 [23.73〜23.76] | 50.12 | 23.73 |
| B2' k=1 | cpu | 7.71 [7.69〜7.74] | 16.56 | 7.71 |
| B2' k=3 | cpu | 21.59 [21.57〜21.59] | 46.82 | 21.59 |
| B2' k=5（EQ6） | cpu | 37.36 [37.29〜37.63] | 81.52 | 37.36 |
| B1 p1 | all | 1.62 [1.62〜1.62] | 2.97 | 1.61 |
| B4 p1 | all | 1.55 [1.54〜1.55] | 2.63 | 1.55 |
| B1 p4（EQ6） | all | 5.70 [5.67〜5.84] | 8.59 | 5.69 |

- **判定**：事前登録の 5 組はすべて，e2e_s と generate_s の両方で「差がある」．B2・B2' は k が小さいほど速い．B1 と B4（parallel 1）では B4 が速い．
- B1 と B4 の差は 0.07 秒（p50）で，3 回の幅が狭いので判定上は差があるが，実用上は小さい．
- 参考：質問者での順番待ち（B1 の parallel 4 − parallel 1）は p50 で +4.08 秒（判定はしない）．

**合わせて読むと**
- B2' は k を 5 から 3 に減らすと，CPU の受信箱で待ち時間が約 42% 短くなり（37.4 → 21.6 秒），正答率の差は −0.004 で差があるとは言えない．k = 1 まで減らすと −0.026 で差が出る．
- B1 は k = 3 でも −0.021 の差があり，流し直しの幅（0.006）より大きい．B1 では k = 5 を保つ理由がある．
- 次に調べる候補（未登録）：B2' の既定を k = 3 にした場合の H-B2 の読み直し．GPU の受信箱で k = 3 が k = 5 と並ぶ理由（長い文脈での 8B の劣化か）．

**連続実行で起きたこと**
1. 2026-10-11 04:42〜04:47 に gpu2 の DNS が引けず，続く 25 回の deploy が 1 秒ずつで失敗した（`ALL DONE` 04:46:48）．
   `eq8_chain.sh` に `retry`（deploy と judge up を 5 分おいて 3 回まで試す）を足し，失敗したログを `failed_20261011_0446/` に移して 10:39 に再開した．結果のある実行は飛ばした．
2. 21:10:12 から，連続実行のプロセスが `/var/run/docker.sock` に触れなくなった（プロセスが docker グループを持っていなかった）．judge up が docker build で失敗し，`retry` の 3 回も尽きる前に止めた．
   21:56 に `sg docker` の下で再開した（最初の起動はスクリプトのパスを誤り，21:56:25〜21:56:53 は何も動いていない）．採点は 21:57〜23:43．
- 所要時間：EQ8・EQ9 は障害の 6 時間を除いて約 14.5 時間（見込み 12 時間），採点は約 1.8 時間（見込み 2 時間），EQ10 は約 4.5 時間（見込み 4 時間）．
- `eq8.log*`・`*.nohup*`・`failed_20261011_0446/`（連続実行のログと，失敗した最初の試行）は，2026-10-14 に `results/20261011_eq8_summary/` へ commit した．

## E2 の下流確認の準備（2026-10-11．p0003 S5．`244b4b4`）

- S4 の `desc_sim` は，計算が同じ既存の `CardSimRouter`（`routing: card_sim`）を使っている．実機の質問者で動かすには，次の 2 点が足りなかった．
  - MedRAG のデータ源は複数のシャードからなるので，広告（`card_embedding`）を持たない（`build_source_profiles` は 1 シャードのデータ源にしか広告を渡さない）．
  - `routing.top_m` に `medrag` のキーが無い．
- したこと（`244b4b4`．`mise run check` と同じ検査が全て通った．pytest 151 件・ruff・format・mypy）：
  - `routing.with_card_embeddings` を足し，LOSO の評価（`loso.load_loso_data`）と質問者（`experiment.run_experiment`）の両方がこれを使うようにした．
    質問者は `routing: card_sim` かつ `dataset: medrag` のとき，起動時に自己紹介文を S4 と同じ `loso.embed_descriptions`（MedCPT の文書側，fp32）で埋め込む．ノードと manifest は変えていない．
  - `routing.top_m.medrag: 3`．k は S4 と同じ規則で，4 源がそろった世界の `train`・`val`（3,064 問）の F1 が最大のものを選んだ（test は見ていない）．
    F1 は k = 1 が 0.438，k = 2 が 0.589，k = 3 が 0.637．
- 制御点の HF キャッシュの MedCPT は，固定した revision と一致している（Query `d83a36c`，Article `d05a736`．`refs/main` と `snapshots/`）．
- 実行の条件：`dataset: medrag`，`routing: card_sim`，`answer_mode: retrieval_only`，`merge: score`，`question_limit: null`．run_id は `20261011_e2_desc_sim`（日付は実行日に合わせる）．
  main の作業ツリーで deploy するので，EQ8〜EQ10 の連続実行の `ALL DONE` の後に行う．参照の実行は `results/20261007_s3_med_all_ret`．

## E2：LOSO の判定（2026-10-11．p0003 S4．`results/e2_loso_20261011_120539`）

- 事前登録（`ce97d7c`，評価の前に push 済み）のとおり，`atrium loso --split test` を MedRAG（4,599 問）・FeB4RAG（474 問）で 1 回だけ実行した．
  コードは `ce97d7c`（`eb6c12c` から journal だけが進んだもの）．結果を見て成功条件や k は変えていない．
- MedRAG の test（4 通りの除外を合わせた値）：

| 方式 | 平均問い合わせ源 | F1 | 新参源の再現率（合計） | pubmed | statpearls | textbooks | wikipedia |
|---|---|---|---|---|---|---|---|
| desc_sim | 2.75 | 0.625 | 0.830 | 0.893 | 0.776 | 0.858 | 0.704 |
| centroid_sim | 3.00 | 0.403 | 0.541 | 0.169 | 0.981 | 1.000 | 0.981 |
| ragroute-frozen | 1.62 | 0.731 | 0.354 | 0.258 | 0.798 | 0.431 | 0.308 |
| ragroute-upper | 1.64 | 0.830 | 0.799 | 1.000 | 0.365 | 0.601 | 0.644 |
| random（3 源） | 3.00 | 0.558 | 0.749 | 0.747 | 0.760 | 0.756 | 0.745 |
| all | 4.00 | 0.616 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

- 選んだ k：`desc_sim` は statpearls を除いたときだけ 2，他は 3．`centroid_sim` はすべて 3．
- **判定：成功**．`desc_sim` が (a) 2.75 ≤ 3.0，(b) 0.830 ≥ 0.8 × 0.799 = 0.639（上限との比 1.040）を同時に満たした．
  `centroid_sim` は (b) を満たさない（比 0.677）．分岐に従い，S5 は `desc_sim` で行う．
- **判定の読み方の注意**（判定は書き換えない）：
  - 判定の対象外の `random`（3 源）も (a)（3.00）と (b)（比 0.937）を満たす．この基準は「中央の分類器に頼らない」方式の下限としては弱い．
    `desc_sim` は `random` より少ない問い合わせ（2.75 対 3.00）で，新参源の再現率（0.830 対 0.749）と F1（0.625 対 0.558）が高い．
    ただし両者の差の検定は事前登録していないので，記述にとどめる．
  - `desc_sim` の新参源の再現率が上限を超えるのは，問い合わせる源が多い（2.75 対 1.64）ためで，同じ問い合わせ数での比較ではない．
    適合率と F1 は `ragroute-upper` が高い（F1 0.830 対 0.625）．
  - `ragroute-frozen`（新参源の one-hot を 0 にする）は，新参源の再現率が 0.354 で，再学習しない中央の分類器は新参源をほとんど選ばない．
  - `centroid_sim` は PubMed を除いたときだけ再現率が 0.169 と低い．PubMed は断片数が多く話題が広いので，重心がどの質問からも遠くなると考えられる（未検証）．
  - データ源が 4 つしかなく，除外は 4 通りなので，除外ごとの値の揺れは大きい（p0003 B1）．
- FeB4RAG の test（参考．判定には使わない）：`centroid_sim` は 13 通りすべてで k = 13（N−1 = 12 源なので全源）を選び，`all` と同じ値（平均 13.00 源，新参源の再現率 1.000）になった．
  `train`・`val` の F1 が k とともに単調に増えたためで，重心の類似度では削減できなかった．`ragroute-upper` は平均 9.44 源，再現率 0.874．
- 次：S5（`routing: desc_sim`，`answer_mode: retrieval_only` の実機での下流確認）．main の作業ツリーで deploy するので，EQ8〜EQ10 の連続実行が終わってから行う．
  `desc_sim` を実機で動かすには，各データ源の広告に自己紹介文の埋め込みが要る．S5 の前に，deploy の経路でそれが作られるかを確かめる．

## E2 の成功条件の事前登録（2026-10-11．評価の前に push する．p0003 S4）

p0003 S4 で固定した成功条件を写す．E1 の結果と，S3 の `train`・`val` の値を見ても変えていない．

**成功条件**：MedRAG の 4 通りの除外について，次の 2 つを**同時に**満たす方式が 1 つ以上あること．

- (a) 1 問あたりの平均問い合わせ数が 3.0 以下（`all` の 4.00 に対する削減率 25% 以上）
- (b) 新参源の再現率が，`ragroute-upper` の新参源の再現率の 80% 以上

- 判定の対象とする方式は `desc_sim` と `centroid_sim`（`loso.CANDIDATE_METHODS`）．
- 新参源の再現率は，4 通りの除外の（質問，新参源）の組を合わせて計算する（`pooled.newcomer_recall`）．4 通りそれぞれの値も併記する．
- k は除外ごとに，新参源を除いた N−1 源で，`train`・`val` の F1 が最大のものを選ぶ（MedRAG は {1, 2, 3}，FeB4RAG は {5, 7, 9, 11, 13}）．
- FeB4RAG の結果は参考にとどめ，判定には使わない（もともと関連源が多く，削減の余地が小さいため）．
- 80% は，研究計画書 §8 E2 の「上限の一定割合以上」を数値にした暫定の基準である．

**手順**

- `atrium loso --split test` を，MedRAG・FeB4RAG で **1 回だけ** 実行し，`results/e2_loso_<YYYYMMDD_HHMMSS>/` に書く（`metrics.json` と `analysis_report.md`）．
- 実行するコードは `eb6c12c`（branch `e2-loso`）とする．

**分岐**

- どちらかが成功したとき → S5（実機での下流確認）に進む．成功した方式が複数あれば，新参源の再現率が最大のものを選ぶ（同点なら平均問い合わせ数が小さい方）．
  S5 は main の作業ツリーで deploy するので，EQ8〜EQ10 の連続実行が終わってから行う．
- どちらも不成功のとき → S5 を飛ばして S6 に進み，原因の候補を記録する．

## E2 の実装（2026-10-11．p0003 S3．branch `e2-loso`）

- EQ8〜EQ10 の連続実行が main の作業ツリーで動いているので，`git worktree` の branch `e2-loso` で作業した．
  main の HEAD を進めると，連続実行の deploy と start の版が合わなくなるためである．
  push は `git push origin e2-loso:main`（早送り）で行い，ローカルの main は連続実行の後に `git merge --ff-only` で取り込む．
- 3 つの commit を作った．各 commit で ruff，ruff format，mypy，pytest（150 件）が通った．
  1. `ed06913`：`train_router` に，除外するデータ源（`exclude`）と出力先（`out_dir`）を足した．既定値は今までの動作と同じ．
  2. `8f6a557`：`RagrouteRouter(allow_unseen=True)` で，学習に無いデータ源の one-hot を 0 にして判定する（`ragroute-frozen`）．
  3. `6c1e9bc`：`src/atrium/loso.py` と `atrium loso`，`top_doc_ids` の重なりの関数（S5 用），テスト，`d0001` §5 と `d0004` の追記．
- **計画からの逸脱**：
  - p0003 S3 の 1・2（新しい `desc_sim`・`centroid_sim` の `Router` と，`RoutingConfig.similarity_top_k`）は行わなかった．
    代わりに p0004 で入れた `CardSimRouter`・`CentroidSimRouter` を使った．方式の中身（自己紹介文または重心とクエリ埋め込みの内積で，上位 k 個を選ぶ）は計画と同じである．
    同じ処理の `Router` を二重に持たないためである．
  - k は設定に置かず，除外ごとに `loso.K_CANDIDATES`（計画と同じ．MedRAG は {1, 2, 3}，FeB4RAG は {5, 7, 9, 11, 13}）から選ぶ．
    計画の「新参源の関連ラベルを k の選択に使わない」を守るには，除外ごとに N−1 源で選ぶ必要がある．
  - このため commit の分け方が計画の 1〜3 と違う（2 つめが `ragroute-frozen`）．`config.yaml` と `d0002` は変えていない．
- データ：制御点から写した `artifacts/{medrag,feb4rag}/`（manifest・queries・router）．N−1 源のルーターは `artifacts/medrag/loso/<新参源>/`（計 3.4 MB）．
  `ncbi/MedCPT-Article-Encoder` は固定した版をオフラインで読んだ．
- `train`・`val` での動作確認（S3 の完了条件．S4 の判定には使わない）．MedRAG の値は次のとおり．
  `train` はルーターの学習に使い，k の選択にも `train`・`val` を使うので，どちらも評価用の値ではない．

| 方式 | val：平均問い合わせ源 | val：新参源の再現率 | train：平均問い合わせ源 | train：新参源の再現率 |
|---|---|---|---|---|
| desc_sim | 2.75 | 0.810 | 2.75 | 0.835 |
| centroid_sim | 3.00 | 0.533 | 3.00 | 0.538 |
| ragroute-frozen | 1.69 | 0.401 | 1.63 | 0.360 |
| ragroute-upper | 1.71 | 0.812 | 1.65 | 0.845 |
| random（3 源） | 3.00 | 0.742 | 3.00 | 0.744 |

- FeB4RAG の `val`（79 問）では，`centroid_sim` が 13 通りの除外すべてで k = 13 を選んだ．候補の中で F1 が k とともに単調に増えるためである．
  N−1 = 12 源で k = 13 は全源を選ぶので，評価では `all` と同じ値（平均 13.00 源，新参源の再現率 1.000）になる．FeB4RAG は判定に使わない（p0003 S4）．
- 気づいた点（判定には使わない）：
  - `ragroute-frozen` は，新参源の再現率が 0.36〜0.40 と低い．one-hot を 0 にすると，新参源をあまり選ばない．
  - `centroid_sim` は，PubMed を除いたときだけ新参源の再現率が 0.13〜0.16 と低い．

## EQ8〜EQ10 の事前登録（2026-10-11．実行の前に push する．p0004 §8.7）

EQ6 の「次に調べる候補」のうち，文脈の通数（1 つめ）と，流し方による答えの揺れ（3 つめ）を調べる．
3 つめは，キャッシュを空にして採点し直す代わりに，同じ条件を今の HEAD で流し直して EQ5 と比べる．2 つめ（裾を縮める）は今回は行わない．

**共通**
- ルーティングは oracle．判定モデルは qwen3:8b（EQ4 で合格したもの）．他の設定は EQ5・EQ6 と同じ．
- 文脈の通数は `data.enronqa.k_context` で変える．質問者の取得数・統合後の数・専門家が使う数は，すべてこの値になる（`requester.py` の `_k_ret`・`_k_rerank`・`_k_local`）．
  `local_answer.k_context` と `retrieval.k_ret` は EnronQA では使われない．k はその場の処理だけで使うので，データの作り直しは要らない．
- 各回の前に deploy し直し，KV キャッシュが空の状態から始める（EQ6 の手順の変更と同じ）．
- 連続実行は `results/20261011_eq8_summary/eq8_chain.sh`，実行の一覧は同じ場所の `eq8_specs.txt`．EQ8・EQ9 → 採点 → EQ10 の順に流す．
- 失敗した問が 1% を超えた実行は集計から除き，記録する（§12 の 4）．EQ8・EQ9 は，失敗した問が残ったら同じ run_id で 1 回だけ再開する（EQ5 と同じ）．
  EQ10 は待ち時間を測るので再開しない．
- 連続実行の途中では commit しない（HEAD が進むと，次の deploy と start の版が合わなくなるため）．

**EQ8（文脈の通数と正答率．test の 2,940 問，parallel 30，各 1 回）**
- 条件：B1・B2・B2' のそれぞれで k = 1, 3, 5．計 9 実行．
- k = 5 も今の HEAD で流し直す．EQ5 の B1 は，質問を並べ替える前の版（`ae5b099`）で動いた．EQ6 の 7 のとおり，答えは同じ専門家で前に何を処理したかで変わる．
  そのため EQ5 を k = 5 として使うと，k の効果と流し方の違いが混ざる．
- 比較：各条件の中で，k = 1 と k = 5，k = 3 と k = 5 を McNemar の正確検定で比べる（計 6 組）．Holm 法で 6 組の多重性を補正し，補正後の p < 0.05 のときだけ差があるとする．
  対象は CPU の専門家に置かれた受信箱の 1,940 問（EQ5 と同じ．B2 では GPU に置かれた受信箱には 8B が答えるため）．GPU の受信箱の値は参考として出す．
- 副指標：
  - ひな形の文だけの回答を不正解に直した正答率．`final_answer` に，`prompts.py` の回答形式の例の文 `The answer to the question` を含む回答を指す（EQ5・EQ6 と同じ文字列）．
  - 外に出た本文の数（B1）と，入力トークン数の中央値．
- 予想（判定には使わない）：k を減らすと正答率は下がる．EQ1 では，正解のメールが上位 5 通に入る割合が 0.967 だった．上位 1 通に限れば，これより低い．

**EQ9（流し直しによる揺れ．test の 2,940 問，parallel 30）**
- 追加の実行は B3（k = 5）の 1 回だけ．EQ8 の k = 5 の B1・B2・B2' と合わせた 4 条件を，EQ5 の同じ条件と比べる．
- 指標：`final_answer` が一致した問の割合，正答率の差，McNemar の p（CPU の受信箱の 1,940 問）．
- 判定：同じ条件の 2 回の正答率の差の絶対値の最大を「流し直しの幅」とする．EQ5 の条件間の差のうち，この幅より小さいものは「流し方の揺れと区別できない」と書く．
- H-B1 の再判定：B2（k = 5）と B3（k = 5）を，CPU の受信箱で McNemar の正確検定で比べる．基準は EQ5 と同じ（p ≥ 0.05 で同等）．EQ5 の判定は書き換えず，両方を並べて記録する．
- p0004 §8.7 は「温度 0 で正誤は決定的なので，正答率の実行は各条件 1 回」としていた．EQ6 の 7 でこの前提が崩れたので，その大きさを測る．

**EQ10（待ち時間．各受信箱の先頭 2 問の 300 問，各 3 回）**
- 条件：B2 と B2' の k = 1, 3（parallel 4）．B1 と B4 の k = 5（parallel 1）．計 6 条件 × 3 回 = 18 回．
- k = 5・parallel 4 の値には EQ6 の 3 回を使う．EQ6 から後の `src` の差分は Hugging Face の版の固定だけで，固定した版は EQ6 で使った版と同じである．
- 指標と判定は EQ6 と同じ：e2e_s と generate_s の p50・p95．3 回の p50 の幅が重ならないときだけ「差がある」とする．比べる組は次の 5 つに限る．
  - B2 の k = 1 と k = 5，B2 の k = 3 と k = 5，B2' の k = 1 と k = 5，B2' の k = 3 と k = 5．B2・B2' は CPU の受信箱に限った値で判定する．
  - B1 と B4（parallel 1）．質問者の Ollama での順番待ち（EQ6 の内訳の 3）を除いて，答える場所の違いだけを見る．
- 参考：B1 の parallel 1 と parallel 4（EQ6）の差を，質問者での順番待ちの大きさとして出す（判定はしない）．
- 正答率は EQ10 では採点しない（EQ8 で測る）．

**所要時間の見込み**：EQ8・EQ9 で約 12 時間，採点で約 2 時間，EQ10 で約 4 時間．EQ5 と EQ6 の実測（`run_meta.json` の時刻と `eq6.log`）から見積もった．

## Hugging Face のモデルの版の固定（2026-10-11．`4d4c6ad`）

- EQ6 が終わったので，`pin-hf-revisions` の commit を main に取り込んだ（cherry-pick．main は直線の履歴なので merge commit は作らない）．取り込み済みの branch は消した．
  - `src/atrium/hf_revisions.py` に 12 モデルの commit を固定した．読み込む箇所（`encoders.py`，`requester.py` の reranker，`cli.py` の `fetch-models`）は，すべて `pinned_revision()` を通す．表に無いモデルは `KeyError` で止まる．
  - 固定した commit は，2026-10-10 に制御点のキャッシュの `refs/main` が指していた版である．EQ0〜EQ6 を含むこれまでの実験は，すべてこの版で動いた．したがって，固定によって結果は変わらない．
- 確認：
  - ruff，ruff format，mypy，pytest（137 件）が通った．
  - 制御点の `$DATA_DIR/.cache/huggingface/hub` に，12 モデルすべての固定した snapshot があり，`refs/main` も一致した．
  - `atrium-full`（`4d4c6add0cd7`）を `--network none`・`HF_HUB_OFFLINE=1` で起動し，12 モデルすべてを固定した版から解決できた．
    SGPT（5.8B）は config と tokenizer まで，他の 11 個は重みまで読み込んで推論した．埋め込みの次元は 768 または 1024，reranker のスコアは 0.995．
  - medrag の既定の設定で再配備した．配置先の専門家 16 台はすべて `4d4c6add0cd7` のイメージで起動し，`/healthz` が応答した．残りの 14 台は配置に入っていない．
- 運用上の注意：
  - 制御点が pull するのは `atrium-full` だけである．そのため，制御点に古い `atrium-node:latest`（`c2574bf1f3d9-dirty`）が残っていた．専門家は deploy で最新を pull するので，実験には影響しない．制御点のものも pull し直した．
  - この記録の commit で HEAD が進む．次の実験の前に `mise run deploy` が要る（start.sh は，イメージの版が HEAD と一致しないと止まる）．
- gpu2 の一時ディレクトリ `/mnt/data-raid/ktakahashi/tmp_atrium_e2e`（約 110 GB）は，2026-10-11 の時点で既に無かった．

## EQ6：待ち時間（確定．2026-10-10 23:17 に全 18 回が終了．`results/20261010_eq6_*`，集計は `results/20261010_eq6_summary/`）

- 終わった回：6 条件（B1・B1 flood_score・B2・B2'・B3・B4）を 3 回ずつ，計 18 回．どれも失敗が 0（300 / 300 問）．
- 受信箱の配置は 18 回すべてで同じだった（`placement.json` の専門家と受信箱の対応が一致）．
  専門家ごとの受信箱を並べたハッシュは `c16d1686361f`（18 回で計算）．途中経過で書いた `cbbcb93c9a52` は別の並べ方で計算した値で，その計算では先の 15 回が一致していた（B2' の 3 回にはこの計算を使っていない）．
- コードの版（`run_meta.json` の `git_head`）：
  - `193ac3b`：B2・B3 の 1 回目．`f540690`：B1 の 3 回，B2・B3 の 2・3 回目，B4 の 3 回．
  - `844cc46`：B1 flood_score の 1 回目．`0b2386b`：B1 flood_score の 2・3 回目，B2' の 1 回目．`86b208d`：B2' の 2・3 回目．
  - `git diff 193ac3b 86b208d -- src` は空で，18 回はすべて同じ `src` で動いた（版の違いは journal と集計スクリプトだけ）．
- 集計：`eq6_summary.py`（事前登録の判定），`eq6_breakdown.py`（時間の内訳），`eq6_b2_vs_b2p.py`（B2 と B2' の比，裾，再現性，EQ5 との答えの一致，KV キャッシュの影響）．出力は同じディレクトリの `.out`．

| 条件 | 有効な回 | e2e p50 [3 回の幅] (s) | e2e p95 (s) | 内訳（CPU の受信箱．prefill / decode / その他，中央値） | 出力トークン |
|---|---|---|---|---|---|
| B1（質問者の llama3.1:8b） | 3 | 5.70 [5.67〜5.84] | 8.59 | 1.05 / 1.03 / 3.25 | 28 |
| B1 flood_score（2 源へ問い合わせ，スコアで統合） | 3 | 5.73 [5.71〜5.83] | 10.58 | 1.11 / 1.17 / 3.32 | 30 |
| B2（CPU は qwen3:0.6b） | 3 | 18.82 [18.79〜18.82]（CPU の受信箱 23.73 [23.73〜23.76]） | 44.60（CPU 50.12） | 13.5 / 8.6 / 0.18 | 102 |
| B2'（CPU は qwen3:1.7b） | 3 | 30.08 [30.03〜30.22]（CPU の受信箱 37.36 [37.29〜37.63]） | 75.62（CPU 81.52） | 30.4 / 5.3 / 0.18 | 37 |
| B3（質問者の qwen3:0.6b） | 3 | 2.49 [2.44〜2.51] | 3.54 | 0.16 / 0.85 / 1.39 | 100 |
| B4（GPU の専門家へ委託） | 3 | 1.72 [1.72〜1.72] | 3.72 | 0.92 / 0.46 / 0.16 | 29 |

「その他」は generate_s − prefill − decode で，Ollama の中での順番待ち・通信・委託の往復を含む．B2 の GPU の受信箱（llama3.1:8b）は e2e p50 1.60 秒で，B4 と同じ程度．B2' の GPU の受信箱も同じモデルで 1.60 秒（B2 と同じ）．

**判定（事前登録の 4 組．3 回の p50 の幅が重ならないときだけ差がある）**
- B2 と B3：**差がある（B3 が速い）**．幅は [18.79, 18.82] と [2.44, 2.51] で重ならない．同じ 0.6B でも，答える場所（データの持ち主の CPU か，質問者の GPU か）で待ち時間が約 7.6 倍違う．
- B1 と B4：**差がある（B4 が速い）**．幅は [5.67, 5.84] と [1.72, 1.72] で重ならない（約 3.3 倍）．
  ただしこの差は `parallel: 4`・`llm.requester_num_parallel: 2` の組での値で，主に質問者の Ollama での順番待ち（下の 3）による．計算だけなら約 2.1 秒と約 1.4 秒．
- B1 と B1 flood_score：**差があるとは言えない**．幅は [5.67, 5.84] と [5.71, 5.83] で重なる．
  - p95 は flood_score が約 2 秒長い（8.59 → 10.58）．e2e と generate の差は約 0.1 秒で，問い合わせ（probe_s の p95 0.14〜0.15 秒）ではなく生成が長くなったためである．
    2 源の断片をスコアで混ぜるので入力が長くなり（入力トークンの p95 3,257 → 3,465，最大 5,062 → 6,555），出力も伸びた（出力トークンの p95 66〜74 → 87〜105）．
  - 代価：待ち時間は縮まない一方で，クエリを受け取る源は 2 倍（1.00 → 2.00），外に出る断片も 2 倍（5 → 10 件），受信量も 2 倍（8.3 → 17.9 KB）になる．再現率は 0.920 で，8% の問で関連する源を取りこぼす．
- B2 と B2'：**差がある（B2 が速い）**．幅は [18.79, 18.82] と [30.03, 30.22] で重ならない（約 1.6 倍）．CPU の受信箱に限っても [23.73, 23.76] と [37.29, 37.63] で重ならない．
  同じ問・同じ専門家・同じ入力トークン数で比べると，CPU の受信箱の 200 問すべてで B2' の方が遅かった（e2e の比の中央値 1.62）．
- 別件：B1 の 1 回目に，出力が上限の 2,048 トークンに達した問が 1 問あった（e2e 92 秒）．p50・p95 には影響しないが，生成が止まらなくなる例として残す．

**内訳から分かったこと**
1. B2 の CPU の受信箱の待ち時間は，CPU での計算そのものでほぼ決まる．
   - prefill 13.5 秒（約 2,138 トークン），decode 8.6 秒（約 102 トークン）．
   - CPU の専門家 20 台（i5-8350U，8 スレッド）の速さはそろっていた：prefill 5.7〜7.0 ms/入力トークン，decode 76〜96 ms/出力トークン．
   - qwen3:0.6b は llama3.1:8b（約 30 トークン）の約 3.5 倍の長さで答えるので，decode の時間も延びる．B3 も 100 トークンなので，これはモデルの性質である．
2. B2 の裾（p95 50 秒，最大 145 秒）は，専門家の Ollama の順番待ちによるもの．
   - 専門家の Ollama は要求を 1 件ずつ処理する（`OLLAMA_NUM_PARALLEL` が 1）．
   - 最も遅い問（192.168.13.101 の `merriss-s` 61-0，145 秒）は，計算が約 24 秒，残りの約 121 秒が順番待ちだった．
     直前に同じ専門家へ，入力の長い `slinger-r` の 2 問（5,082・6,080 トークン．300 問の最大は 6,080）が届いており，その prefill（49.5・71.3 秒）の後ろで待った．
   - EQ6 の 300 問では，CPU の専門家はどれも受信箱を 5 個ずつ持つ．このため，専門家ごとの差は受信箱の数ではなく，入力の長い問がどの専門家に重なったかで決まる．
3. B1・B3 の「その他」（3.25 秒・1.39 秒）は，質問者の Ollama の中での順番待ちと考えられる．
   - 質問者は 4 問を同時に送る（`parallel: 4`）が，質問者の Ollama は 2 件までしか同時に処理しない（`llm.requester_num_parallel: 2`）．
   - B1 の計算（prefill ＋ decode）は約 2.1 秒で，B4 の約 1.4 秒との差は小さい．B4 が速い主な理由は，生成を GPU の専門家 10 台に分けて，順番待ちがほぼ無いこと（その他 0.16 秒）にある．
   - したがって B1 と B4 の差は，「答える場所」よりも「生成に使える GPU の数」の差を強く反映する．B1 の値は `requester_num_parallel` と `parallel` の組に依存するので，判定を書くときはこの条件を付ける．
4. B2' が遅いのは prefill による．出力が短くなる分では取り返せない．
   - CPU の受信箱の中央値で，prefill 13.5 → 30.4 秒，decode 8.6 → 5.3 秒，出力 102 → 37 トークン．入力のトークン数は同じ（中央値 2,138）．
   - 同じ問どうしの比（1 回目，200 問）：prefill 2.24 倍［1.75〜2.57］，decode の 1 トークンあたり 1.72 倍，出力トークン 0.37 倍．
   - prefill の 1 トークンあたりの時間は，B2 6.29 ms（p5〜p95 5.24〜7.89），B2' 14.12 ms（12.26〜16.68）．B2' でも専門家 20 台の中央値は 12.85〜15.17 ms でそろっていた．
   - RAG のように入力が長く（約 2,100 トークン）出力が短い場面では，CPU での待ち時間は入力側の計算でほぼ決まり，モデルが大きくなるとその分だけ延びる．
     「大きいモデルは答えが短いので速いかもしれない」という見込みは，この入力の長さでは成り立たなかった．
5. B2' の裾は B2 と同じ問・同じ専門家で起き，順番待ちが約 2 倍に延びた．
   - 最も遅い 3 問は，B2 でも B2' でも 192.168.13.101 の `merriss-s` 61-0，`slinger-r` 80-0，`linder-e` 41-0 で，6 回とも同じ順だった．
   - 61-0：B2 145 秒（計算 24 秒，順番待ち 121 秒）→ B2' 278〜281 秒（計算 43〜46 秒，順番待ち 233〜235 秒）．
     80-0（入力 6,080 トークン）：prefill 71 秒 → 125〜128 秒．41-0：順番待ち 42 秒 → 84〜93 秒．
   - 専門家の Ollama は 1 件ずつ処理する（`OLLAMA_NUM_PARALLEL` が 1）ので，前の問の計算が長くなるほど，同じ並び順でも後ろの問の順番待ちがそれに比例して延びる．p95 は 44.60 → 75.62 秒．
6. temperature 0 の再現性：B2 も B2' も，3 回の答え（`final_answer`）と出力トークン数が 300 / 300 問で一致した．
   - 各回は deploy の直後（KV キャッシュが空）から，同じ問を同じ順で同じ専門家へ送るので，生成は完全に再現する．回ごとの待ち時間の差は計算の揺らぎだけによる．
   - 出力が上限の 2,048 トークンに達した問は，B2・B2' とも 0．
   - `The answer to the question` を含む答え（ひな形の繰り返し）は，CPU の受信箱で B2 133 / 200，B2' 0 / 200．1.7B ではひな形の繰り返しが起きない．
     （EQ5 の 35.9% は CPU の受信箱の 1,940 問でひな形の文をそのまま答えにした割合で，数え方も対象の問も違う．）
7. **KV キャッシュの状態で答えが変わる**（EQ5 の正答率の解釈に関わる）．
   - KV キャッシュに当たった無効な B2 の 2・3 回目（`results/20261010_eq6_invalid_warmcache/`）は，有効な 1 回目と答えが一致したのが 2・3 回目とも 221 / 300 問だった（CPU の受信箱 132 / 200，GPU の受信箱 89 / 100）．
     temperature 0・同じプロンプト・同じ重み・同じ専門家でも，プロンプトの KV を計算し直すか再利用するかで生成が分かれる．GPU の 8B でも起きる．
   - EQ5（同じ設定で parallel 30．2,940 問を続けて流した）の同じ 300 問と比べると，届いた専門家は 300 / 300 で同じだが，答えが一致したのは
     B2 278 / 300（CPU 182 / 200，GPU 96 / 100），B2' 278 / 300（CPU 181 / 200，GPU 97 / 100）だった．
     EQ5 では，前の問とプロンプトの先頭（システムの指示など）が共通なので KV を再利用した可能性があり，上と同じ仕組みで答えが分かれたと考えられる（確かめてはいない）．
   - 含意：EQ5 の各問の答えは，その問の前に同じ専門家で何が処理されたかに依存する．EQ5 の正答率の差のうち小さいもの（H-B1 の 0.297 vs 0.260 など）は，この揺れを含む．
     EQ5 の H-B1 の原因を調べたときに挙げた「CPU と GPU の計算の違い」に加えて，KV キャッシュの再利用の有無も生成を分ける要因である．
     正答率への影響（向きと大きさ）は，EQ6 では採点しないので分からない．

**考察（RQ-B．EQ5 の正答率と合わせる．暫定）**
- CPU の受信箱で比べると：B4 は正答率 0.789・p50 1.70 秒で，露出は 1 問あたり 5 通（相手は対応づけた GPU の専門家 1 台）．
  B1 は 0.789・5.58 秒で，露出は 5 通（相手は質問者）．B2 は露出 0 だが，0.297・23.73 秒．B2' も露出 0 で，0.650・37.36 秒．B3 は 2.51 秒だが 0.260．
- 露出を 0 にする（B2）代価は，正答率（0.789 → 0.297）と待ち時間（1.70 → 23.73 秒，約 14 倍）の両方に出る．その待ち時間の大半は，CPU での長い入力の prefill である．
- CPU の専門家のモデルを大きくすると（B2 → B2'），露出 0 のまま正答率は 0.297 → 0.650 に上がる．一方で待ち時間は 23.73 → 37.36 秒（約 1.6 倍．B4 の約 22 倍）に延びる．
  それでも正答率は B1・B4 の 0.789 に届かない．露出 0 の条件では，正答率と待ち時間が引き換えになる．
- 次に調べる候補（事前登録はまだしていない）：
  - B2・B2' の文脈の通数や長さを減らしたときの待ち時間と正答率．prefill は入力のトークン数にほぼ比例するので待ち時間は縮むはずで，prefill の割合が大きい B2' ほど効く．正答率がどれだけ下がるかは分からない．
  - 裾を縮める方法（入力の長い問を専門家の間で散らす，専門家の Ollama の同時処理数を増やす）．
  - KV キャッシュの状態による答えの揺れが正答率に与える影響（EQ5 の条件を，各問の前にキャッシュを空にして採点し直す）．
- 注意：parallel 4 で測った値である．各受信箱の先頭 2 問だけを使っている．各回は「重みは読み込み済み，KV キャッシュは空」の状態から測っている．

## EQ6 の事前登録（2026-10-10．実行の前に push する．p0004 §8.7）

- 質問：test の抜き出しの各受信箱の先頭 2 問（`experiment.question_limit: 2`．150 受信箱 × 2 = 300 問．全ての受信箱が 5 問以上ある）．
- 条件：EQ5 の 6 条件（B1，B1 flood_score，B2，B2'，B3，B4）．`experiment.parallel` は既定の 4．他の設定は EQ5 と同じ．
- 手順：条件ごとに 1 回 deploy し，同じ設定で 3 回続けて実行する（run_id `20261010_eq6_<条件>_r<1〜3>`）．
  deploy は，その条件で答える Ollama のモデルを実験と同じ num_ctx で読み込んでおく．
  EQ5 の宿る型では，各専門家の最初の回答に読み込みの時間が上乗せされた（中央値 3〜5 秒，最大 59 秒）．
  300 問では約 1 割の問と 1 回目の実行だけに入るため，deploy で読み込むように直した．
- 指標：e2e_s と generate_s の p50・p95（300 問）．3 回の中央値を代表値とし，最小〜最大を幅として出す．
  回ごとの値もすべて記録する（順序の影響を見るため）．B2・B2' は CPU の専門家に置かれた受信箱の質問に限った値も出す．
- 判定：2 条件の待ち時間は，3 回の p50 の幅（最小〜最大）が重ならないときに限り「差がある」とする．比べる組は次の 4 つに限る．
  B2 と B3（同じ 0.6B．答える場所だけが違う．H-B1 の待ち時間），B1 と B4（同じ 8B．答える場所が違う），
  B2 と B2'（専門家のモデルの大きさ），B1 と B1 flood_score（ルーティング）．
- 失敗した問が 1% を超えた実行は集計から除き，記録する（§12 の 4）．
- 正答率は EQ6 では主張に使わず，採点もしない（正答率は EQ5 の 2,940 問で測った）．
- **手順の変更（2026-10-10 18:35．B2 の 3 回と B3 の 1 回目の途中で変更．判定の規則と比べる組は変えない）**：
  各回の前に deploy し直す（コンテナを作り直す）．条件ごとに 1 回だけ deploy すると，2・3 回目は 1 回目と同じ問が
  同じ専門家に届くため，Ollama のプロンプトの KV キャッシュに当たる．B2 では，CPU の専門家の prefill が
  1 回目 14〜17 秒から 2・3 回目 0.08 秒に縮み（入力のトークン数は同じ 2107.5，decode は比 1.00），
  e2e の p50 は 18.8 → 7.4 秒になった．これは同じ条件を 3 回測ったものではないので，B2 の 2・3 回目は無効とし，
  集計の対象から外した（`results/20261010_eq6_invalid_warmcache/` に残す．ログ類は除く）．条件をまたいだ持ち越しは無い
  （deploy は `docker compose up -d --force-recreate` で Ollama のプロセスを作り直し，KV キャッシュはその中にだけある）．
  B2 の 1 回目と B3 の 1 回目は deploy の直後に始まったので有効．この変更で各回は「重みは読み込み済み，KV キャッシュは空」から測る．

## EQ4 の検証 2：参照モデルとの一致（2026-10-10．`results/20261010_eq4_judge_kappa`）

- EQ5 の 6 実行から 50 件ずつ（計 300 件）を qwen3:14b（制御点と wafl500〜509）で採点し直した．
  **κ = 0.828**（一致率 0.915，n = 293．参照モデルが判定できなかった 7 件を除く）．基準（κ ≥ 0.8）を満たし，検証 1 と合わせて**合格**．
  主指標は判定モデル（qwen3:8b）の採点のままとし，選択式への切り替えはしない．
- 実行の途中で 2 回止まった．原因は Ollama 0.35 が返す 500 `prediction aborted, token repeat limit reached` で，qwen3:14b が
  300 件中 2 件（`b2:610-1`，`b2_1p7b:54698-0`）で同じトークンを繰り返して打ち切られたもの．温度 0 なのでどの Ollama でも同じく打ち切られる．
  `judge_items` を直し，この 500 は判定できなかった出力として扱い，他の失敗は別の Ollama へ振り替えて送り直すようにした（f9925cb，c2574bf）．
- 診断用に同じ 300 件を採点したときは κ = 0.835（n = 294）で，判定できなかった件の数が 1 件違った．
  温度 0 でも GPU が違うと生成が揺れるため（EQ5 の H-B1 と同じ現象）．どちらも基準を満たす．
- 注意：検証 1・2 はどちらも，中身の無い回答（ひな形の文だけ）を正解とする偽陽性を測れない．EQ5 の 0.6B の条件の正答率は
  この偽陽性で約 0.04 高い（下の EQ5 の記録）．

## EQ5：RQ-B の判定（2026-10-10．`results/20261009_eq5_*`，判定モデル qwen3:8b）

受信箱の置き場所で分けた結果（CPU の専門家に置かれた受信箱 1,940 問，GPU の専門家に置かれた受信箱 1,000 問）．
B2 と B2' の `results.jsonl` には再試行の前の失敗の行（35 行・4 行）が残るが，`read_results` は同じ qid の最後の行を採るので集計には入らない．
待ち時間は parallel 30 のもので主張には使わない（EQ6 で測る）．

| 条件 | 置き場所 | 正答率 [95%CI] | e2e p50 / p95 (s) | 外に出た本文（件/問） |
|---|---|---|---|---|
| B1（質問者の llama3.1:8b） | cpu / gpu | 0.789 [0.770, 0.806] / 0.754 [0.726, 0.780] | 57.9 / 78.9 | 5 |
| B1 flood_score | cpu / gpu | 0.659 / 0.666 | 59.4 / 78.0 | 10 |
| B2（CPU は qwen3:0.6b，GPU は llama3.1:8b） | cpu / gpu | 0.297 [0.277, 0.318] / 0.750 [0.722, 0.776] | 75.7 / 394.8（cpu） | 0 |
| B2'（CPU は qwen3:1.7b） | cpu / gpu | 0.650 [0.628, 0.671] / 0.749 | 101.9 / 374.5（cpu） | 0 |
| B3（質問者の qwen3:0.6b） | cpu / gpu | 0.260 [0.241, 0.280] / 0.253 | 23.2 / 36.4 | 5 |
| B4（CPU の専門家が GPU の専門家 1 台に委託） | cpu / gpu | 0.789 [0.770, 0.807] / 0.747 | 17.4 / 45.4（cpu） | 5 / 0（全問の平均 3.30） |

| 比較（McNemar の正確検定） | 置き場所 | 正答率 A vs B | A だけ正解 / B だけ正解 | p |
|---|---|---|---|---|
| B2 vs B3（H-B1） | cpu | 0.297 vs 0.260 | 236 / 164 | 3.7e-4 |
| B2 vs B1（H-B2 (a)） | cpu | 0.297 vs 0.789 | 48 / 1001 | 9e-233 |
| B4 vs B1 | cpu | 0.789 vs 0.789 | 46 / 45 | 1.00 |
| B2' vs B1 | cpu | 0.650 vs 0.789 | 99 / 368 | 2e-37 |
| B1 vs B3（モデルの大きさ） | cpu | 0.789 vs 0.260 | 1067 / 42 | 7e-258 |
| B2 vs B1（同じ 8B，答える場所だけが違う） | gpu | 0.750 vs 0.754 | 20 / 24 | 0.65 |
| B1 flood_score vs B1 oracle | 全問 | 0.662 vs 0.777 | 120 / 459 | 1e-47 |

**判定**（事前登録の基準）
- H-B1：**支持されない**（p = 3.7e-4 < 0.05）．事前登録どおり原因を調べた．
  - プロンプト長・切り詰め：B2 と B3 のプロンプトは 1,940 問すべてで同じトークン数（中央値 2,596，最大 12,458．num_ctx 16,384 の内）で，文脈はどちらも 5 通．
  - 打ち切り：num_predict（2,048）に達したのは B2 4 問・B3 6 問だけ．
  - 差の出どころ：qwen3:0.6b はプロンプトのひな形の文 `The answer to the question` をそのまま答えにすることが多く，その割合が
    B2（CPU で動く）35.9%，B3（制御点の GPU で動く）45.2% と違った．両方とも中身のある回答を返した 918 問に限ると 0.411 vs 0.403（p = 0.63）で差は無い．
    temperature 0・同じプロンプトでも最終の回答が一致したのは 736 / 1,940 問だった．qwen3:0.6b の manifest の digest は制御点・CPU の専門家・GPU の専門家で同じ（`7df6b6e09427a769`）なので，同じ重みでも CPU と GPU の計算の違い（浮動小数の演算順など）で生成が分かれたと考えられる．
  - 解釈：「誰が答えるか」そのものの効果ではなく，0.6B の出力の不安定さ（ひな形の繰り返し）が計算機器の違いで揺れたもの．
- H-B2：**支持**．(a) CPU の受信箱で B2 0.297 < B1 0.789（p = 9e-233）．(b) 全問で B1 − B4 = 0.002 < B1 − B2 = 0.326．
  (c) B4 の外に出た本文は 1 問あたり 3.30 件で B1 の 5 件より少ない（CPU の受信箱では 5 件のまま．ただし受け取るのは対応づけた GPU の専門家 1 台）．
- 参考：GPU の受信箱では，同じ 8B で答える場所だけが違う B2 と B1 は同等（0.750 vs 0.754，p = 0.65）．CPU の専門家を 1.7B にすると（B2'）0.650 まで上がるが B1 には届かない．

**判定モデルの偽陽性（EQ4 の検証 1 では見つからなかったもの）**
- ひな形の文だけの回答（中身が無い）を，qwen3:8b は約 1 割正解とした（B2 cpu 71 / 696，B3 cpu 88 / 877，B3 gpu 36 / 443．他の条件にこの回答は無い）．
  検証 1 は付属の別解と誤答だけを使うので，中身の無い回答の偽陽性を測れない．
- これらを不正解に直しても結論の向きは変わらない：B2 vs B3（cpu）0.261 vs 0.215（p = 1e-6），B2 vs B1（cpu）0.261 vs 0.789．
  影響を受けるのは 0.6B の条件（B2 の cpu・B3）だけで，正答率を約 0.04 高く見積もっている．

## EQ3：RQ-A の実機の確認と EQ4 の検証 1（2026-10-09〜10．`results/20261009_eq3_*`，`results/20261009_eq4_judge_validate`．2026-10-13 に journal へ追記）

結果はコミット（`ae5b099`，`343f0f5`，`6ddbe65`）にだけ書いてあり，journal に判定が無かったので，2026-10-13 に同じ値で追記した．判定は事前登録（下の「EQ3〜EQ5 の事前登録」）の基準による．

**EQ3**（retrieval_only，test の 2,940 問，m = 2，parallel 30）

| 方式 | 実機の再現率（正解の受信箱が選ばれた割合） | EQ2 の hit@2 | 差 | 失敗 | e2e p50 / p95 (s) | 事前の公開情報 |
|---|---|---|---|---|---|---|
| term_sketch | 0.3588 | 0.359 | 0.000 | 0 | 0.47 / 0.96 | 0.83 MB |
| multi_centroid | 0.4401 | 0.440 | 0.000 | 0 | 3.52 / 4.83 | 73.6 MB |
| flood_score | 0.8041 | 0.804 | 0.000 | 0（下記） | 92.2 / 107.2 | 0 |

- 判定：**一致**．3 方式とも，実機の再現率が EQ2 のオフラインの hit@2 と 0.01 以内（小数 3 桁で同じ）．実機の実装はオフラインの評価と一致している．
- flood_score は，最初の実行で 175 問が `ReadError`（30 台への同時の probe で，待機中の接続を閉じられたもの）で失敗した．`.exit` に終了コード 2 を残す修正（`719a0bc`）の後に，
  同じ run_id で失敗した問だけを流し直して，失敗 0 になった（`6ddbe65`）．`results.jsonl` には最初の失敗の行が残る（3,115 行）が，集計は同じ qid の最後の行を使う．
- 1 問あたりにクエリを受け取ったデバイスは，公開情報の 2 方式で約 2 台，flood_score で 30 台（全台）．外に出た本文は全方式で 10 件／問（2 受信箱 × 5 通）．
- 待ち時間は parallel 30 のもので，主張には使わない（flood_score の 92 秒は 30 台への probe の待ち行列による）．

**EQ4 の検証 1**（判定モデル qwen3:8b，EnronQA の原論文の付録 B.6 のプロンプト）
- 付属の別解 500 件を正解とした割合 **0.956**，誤答 500 件を不正解とした割合 **0.984**，判定できなかった出力 0．基準（どちらも 0.95 以上）を満たし，**合格**．
- この判定モデルで EQ1 の回答を採点した：B1 0.813，B2 0.420（各 150 問）．
- 検証 1 では，中身の無い回答（ひな形の文だけ）の偽陽性は測れない（EQ5 の記録を参照）．

## EQ1：実機の健全性（2026-10-09．受信箱ごとに 1 問の計 150 問，routing=oracle）

| run | 条件 | 失敗 | e2e p50 / p95 (s) | 外に出た本文（件/問） | 備考 |
|---|---|---|---|---|---|
| `20261009_eq1_oracle_retrieval` | retrieval_only | 0 | 0.01 / 0.02 | 5 | 正解のメールが上位 5 通に入った割合 0.967 |
| `20261009_eq1_oracle_b1` | B1 snippet_return（質問者の llama3.1:8b） | 0 | 5.6 / 9.4 | 5 | プロンプトの中央値 2,035 トークン．"Answer:" の行が無い出力 29 問（出力の全体を採点に回す） |
| `20261009_eq1_oracle_b2` | B2 local_answer | 0 | 20.6 / 45.5 | 0 | GPU の専門家は llama3.1:8b を GPU（100%），生成の中央値 2.0 秒．CPU の専門家は qwen3:0.6b を CPU（100%），24 秒 |

- 30 台の専門家に 5 個ずつ受信箱を配った．GPU の専門家として wafl500〜509 の 10 台を検出し，CPU の 20 台を 2 台ずつ対応づけた（B4 の委託先）．
- 制御点の GPU には質問者の llama3.1:8b だけが載る（クエリ埋め込みは事前計算なので，§8.1 の VRAM の懸念は起きない）．
- 所要時間の見積もりの更新：B2 は experiment.parallel 4 では 150 問に約 25 分かかり，全 2,940 問では 8 時間を超える．EQ5 では parallel を 30 にする（下の事前登録）．

## EQ3〜EQ5 の事前登録（2026-10-09．評価の前に push する．p0004 §8.6・§8.7）

**共通**
- 評価の質問は test の抜き出し 2,940 問（受信箱あたり最大 20 問．他の受信箱にほぼ同じメールがある質問は除いた．V3）．各条件 1 回．
- `experiment.parallel` は EQ3・EQ5 の全ての条件で 30 にする（既定の 4 では CPU の専門家 20 台のほとんどが待つだけになり，
  宿る型の全問に 8 時間以上かかるため）．待ち時間の主張には使わず，待ち時間は EQ6（300 問，既定の 4，各条件 3 回）で測る．

**EQ3（RQ-A の実機の確認．retrieval_only）**
- 条件：EQ2 の hit@1 が高い公開情報の 2 方式（multi_centroid，term_sketch）と flood_score．m = 2．
- 基準：実機のデータ源の選択の再現率（正解の受信箱が選ばれた割合）が，EQ2 のオフラインの hit@2 と 0.01 以内で一致すれば，
  実機の実装がオフラインの評価と一致しているとする．一致しなければ原因を調べて記録する．

**EQ4（判定モデルの検証．§8.6）**
- 判定モデルは qwen3:8b，プロンプトは EnronQA の原論文の付録 B.6．判定できない出力は不正解として数え，割合を記録する．
- 検証 1（EQ5 の前）：test の付属の別解と誤答を 500 件ずつ採点する．別解を正解とした割合と誤答を不正解とした割合が，どちらも 0.95 以上で合格．
- 検証 2（EQ5 の後）：EQ5 の各条件から同じ数ずつ抜き出した 300 件を，参照モデル qwen3:14b で採点し直す．Cohen の κ が 0.8 以上で合格．
  gpu2 はディスクの空きが少なく，データとモデルは制御点に置く指示があるので，参照モデルは制御点と wafl500〜509 で動かす（§8.6 の規定どおり 14B 級）．
- 切り替え規則：検証 1 か 2 が不合格なら，主指標を選択式（正解 1 個と誤答 2 個の 3 択．原論文に無い派生指標）に切り替え，判定モデルの結果は副指標にする．
  選択式は追加の回答の実行を要するので，切り替えたときにだけ実装して実行する．

**EQ5（RQ-B．ルーティングは oracle に固定）**
- 条件：B1 snippet_return（質問者の llama3.1:8b），B2 local_answer（CPU の専門家は qwen3:0.6b，GPU の専門家は llama3.1:8b），
  B2' local_answer（CPU の専門家を qwen3:1.7b にする．`llm.expert_model` をこの実行だけ変える），B3 snippet_return_small（質問者が qwen3:0.6b），
  B4 delegate_gpu．加えて，実機に実装した非オラクルの方式で EQ2 の hit@1 が最も高い flood_score で B1 を 1 回．
- 正答率は判定モデルの採点で，Wilson の 95% 信頼区間を付ける．方式間の比較は同じ質問の正誤の組の McNemar の正確検定．
- 誰が答えるかの比較（H-B1・H-B2 の (a)）は，CPU の専門家に置かれた受信箱の質問に限る（B2 では GPU に置かれた受信箱は 8B が答えるため）．
  GPU に置かれた受信箱の質問は，同じ 8B で B1 と B2 を比べる（答える場所だけが違う）．
- H-B1：CPU の受信箱の質問で，B2（0.6B）と B3（0.6B）の McNemar の p が 0.05 以上なら支持（同等）．0.05 未満なら，原因（プロンプト長・切り詰め・
  打ち切り）を調べて記録する．
- H-B2：(a) CPU の受信箱の質問で，B2（0.6B）の正答率が B1 より低く p < 0.05．(b) 全問で B1 と B4 の差が B1 と B2 の差より小さい．
  (c) B4 の 1 問あたりの外に出た本文の数が B1 より少ない．3 つとも成り立てば支持，どれも成り立たなければ反証，それ以外は部分的に支持．

## EQ2：RQ-A のオフラインの評価（2026-10-09．`results/20261009_eq2_advert_eval`）

事前登録（下の「EQ2 の事前登録」）の手順で 1 回だけ評価した．受信箱 150 個，dev 2,948 問，test 2,940 問，所有者の特定に使うメール 1,460 通．
dev で選んだ値は，件名 40，T = 200，C = 32．m は dev の MRR が最大の flood_bm25_shared_idf の hit@m が 0.9 に届く最小の値で，m = 2 になった．

| 方式 | 公開情報（KB） | hit@1 [95%CI] | hit@2 | 質問を受け取る受信箱 | 所有者 top-1 | 所有者 AUC |
|---|---|---|---|---|---|---|
| card_sim | 169 | 0.112 [0.100, 0.123] | 0.153 | 2 | 0.168 | 0.760 |
| term_sketch | 870 | 0.293 [0.276, 0.311] | 0.359 | 2 | 0.425 | 0.939 |
| centroid_sim | 2,509 | 0.264 [0.248, 0.280] | 0.323 | 2 | 0.284 | 0.853 |
| multi_centroid | 75,386 | 0.349 [0.332, 0.366] | 0.440 | 2 | 0.527 | 0.950 |
| flood_score（密ベクトル） | 0 | 0.722 [0.707, 0.737] | 0.804 | 150 | 偶然（0.007） | 0.5 |
| flood（BM25，受信箱ごとの IDF） | 0 | 0.603 [0.585, 0.621] | 0.702 | 150 | 偶然 | 0.5 |
| flood（BM25，共有した IDF） | 0（＋文書頻度の共有） | 0.866 [0.855, 0.878] | 0.928 | 150 | 偶然 | 0.5 |
| ragroute（中央の MLP） | 0（＋学習データの中央への集約） | 0.215 [0.201, 0.229] | 0.279 | 2 | － | － |
| random | 0 | 0.007 | 0.013 | 2 | 0.007 | 0.5 |

**判定**（事前登録の基準）
- H-A1：**支持**．flood_score の hit@1（0.722）は，公開情報を使う 4 方式のどれ（最大は multi_centroid の 0.349．信頼区間は重ならない）よりも高い．
  代わりに質問（クエリ埋め込み）は 150 個の受信箱すべてに届く（他は 2 個）．
- H-A2：**支持**．card_sim・term_sketch・multi_centroid の 3 方式すべてで，公開情報の大きさと hit@1 の順位相関（1.00・1.00・1.00）と，
  大きさと所有者 top-1 の順位相関（1.00・0.40・1.00）がどちらも正だった．term_sketch の所有者の特定は語数 10 ですでに 0.415 と高く，
  語数を増やしてもあまり変わらない（送信者の名前などが少ない語数でも入るためと考えられる．未確認）．
- H-A3：**支持**．密ベクトル（0.722）≥ 受信箱ごとの IDF の BM25（0.603）で，IDF を共有すると BM25 は 0.866 まで上がった．
  ただし共有した IDF の BM25 は密ベクトルより高く，「密ベクトルの方が良い」は，統計を共有しない条件に限って成り立つ．

**読み取れること**
- 公開情報を使う方式は，所有者の特定の精度（0.17〜0.53）が，見つけてもらえる度合い（hit@1 0.11〜0.35）と同程度か，それ以上だった．
  公開する情報は，見つけてもらうことより持ち主を当てることの方に役立っている．
  **訂正（T2・Iteration 2．値は変えない）**：この読み取りは入力の長さの違いと，メールのヘッダー（持ち主のアドレス）の名前の一致によるもので，そのまま主張しない（journal の「T2」と「Iteration 2 実行済み」）．
  **注記（Iteration 3．値は変えない）**：この表の公開情報はヘッダー込みのメールから作った．本文だけから作り直すと hit@2 は multi_centroid 0.347，term_sketch 0.328，centroid_sim 0.227 に下がるが，H-A2（単調な兼ね合い）は 3 方式とも成り立つ（journal の「Iteration 3 実行済み」）．
- 公開情報なしで全員に聞く flood は，見つけてもらえる度合いが最も高く，所有者は特定されないが，質問の露出が最大になる．
  検索の統計を共有する BM25 は最も高い hit を得るが，文書頻度の共有そのものが露出になる（H-A3 の仮説の前提）．
- 中央の MLP（ragroute）は 150 個の受信箱では hit@1 0.215 にとどまり，公開情報を使う方式より低い．1 問 1 個の正解に対して，
  関連の無い受信箱を 10 個だけ抜き出した学習（150 個の全ての組は大きすぎる）が十分でない可能性がある．
- m = 2 は，最良の方式（共有 IDF の BM25）に合わせて決まったので，公開情報を使う方式には小さい（hit@2 は 0.15〜0.44）．
  事前登録の規則なので変えない．EQ3 の実機の確認は m = 2 で行う．

**設定への反映**：`data.enronqa` の card_subjects 40，term_sketch_size 200，n_centroids 32，`routing.top_m.enronqa` 2，
比較を揃えるため `routing.random_k.enronqa` も 2 にした．シャードの公開情報はこの値で作り直す．

## p0004 の実装と退行の確認（2026-10-09）

- I1〜I9 を実装した（`308db94`〜`b35a170`，`81ff7cb`）．既存の設定キーは消さず，値を変えたのは `cluster` の 3 か所
  （`requester_llm` の削除，`experts` の末尾への wafl500〜509 の追加，`gpu_workers` への wafl500 の追加．p0004 §8.1）だけである．
- 退行ゲート：**退行なし**．
  - R1（`results/20261009_r1_medrag_regression`．MedRAG，all，retrieval_only，10 問）：ラベル一致 1.000，`top_doc_ids` は s3 と 10 問中 10 問で一致．
  - R2（`results/20261009_r2_feb4rag_regression`．FeB4RAG，ragroute，2 問）：`selected_sources` は s2 と 2 問中 2 問で一致．
  - 専門家の配置は MedRAG 16 台，FeB4RAG 13 台のまま（wafl500〜509 は末尾に足したので使われない）．
- 計画からの逸脱と理由：
  - データ準備（メールの埋め込み）は制御点の GPU 1 枚で行った．約 7.4 万通を約 20 分で埋め込めたので，11 台への分担（§8.1）は要らなかった．
  - 受信箱ごとの BM25 の索引は専門家のノードには持たせず，H-A3 の確認に使うオフラインの評価（`advert-eval`）でだけ計算する．
    実機の受信箱をまたぐ統合は密ベクトルのスコアで行う（§8.2）ので，ノードの BM25 は実験に使わない．
  - 回答と正解のメールの最長共通部分列の比率（§8.4 の `answer_overlap_lcs`）は，正解のメールの本文を質問者に配らないよう，
    採点（`atrium judge`）の段で計算し，`judgements.jsonl` から分析に取り込む．
  - Agent Card の説明文は，受信箱で多い件名の上位 N 個とした（N は dev で選ぶ）．持ち主が自分のメールだけから作れる自己紹介として定めた．
  - 中央の分類器（ragroute）は，150 個の受信箱の全ての組を作るとメモリが足りないので，1 問あたり関連の無い受信箱を 10 個だけ抜き出して学習する．
  - 判定の参照モデル（§8.6 の 2）は，gpu2 のディスクの空きが少なく，データとモデルを制御点に置く指示があるので，制御点・GPU の PC で qwen3:14b を使う．

## EQ2 の事前登録（2026-10-09．評価の前に push する．p0004 §8.7）

EQ2（RQ-A のオフラインの評価．`atrium advert-eval`）の手順と判定の基準を，test の評価の前に固定する．

**手順**（`src/atrium/advert_eval.py` に固定）
- 受信箱は 150 個．質問は，dev（受信箱あたり最大 20 問）と test（受信箱あたり最大 20 問）．他の受信箱にほぼ同じメール（Jaccard 0.9 以上）が
  ある質問は除く（V3）．
- 公開情報の大きさの候補：Agent Card の件名の数 {5, 10, 20, 40}，語のスケッチの語数 T {10, 20, 50, 100, 200}，k-means の中心の数 C {2, 4, 8, 16, 32}．
  方式ごとに，dev の MRR が最大の値を選ぶ．
- 問い合わせる数 m：dev で MRR が最大の方式（flood_score・ragroute を含む非オラクルの全方式）の dev の hit@m が 0.9 以上になる最小の m
  （候補 {1, 2, 3, 5, 10, 20}．届かなければ 20）．
- test は 1 回だけ評価する．結果を見て候補・選択の規則・m を変えない．選んだ T・C・件名の数・m は，EQ3 の実機の確認と，
  シャードの公開情報（`data.enronqa`・`routing.top_m`）に使う．
- 所有者の特定：各受信箱から test の正解のメール以外を最大 10 通（種 12）抜き出し，公開情報だけから持ち主の受信箱を当てる．
  flood_score と ragroute は受信箱ごとの公開情報が無いので偶然の水準（1/150，AUC 0.5）とする．
- 公開情報の大きさと所有者の特定の兼ね合い（H-A2）を見るため，test では選んだ値に加えて全ての候補の値の hit@1・所有者 top-1 も出す
  （報告用の曲線であり，値の選択には使わない）．

**仮説の判定の基準**
- H-A1（flood_score は公開情報なしで高い hit を得るが，質問の露出は全台）：test の flood_score の hit@1 が，公開情報を使う 4 方式
  （card_sim・term_sketch・centroid_sim・multi_centroid）のどれの hit@1 以上なら支持する．質問を受け取る受信箱の数（flood は 150，他は m）を併記する．
- H-A2（公開情報を増やすほど hit も所有者の特定も上がる）：大きさの候補を持つ 3 方式（card_sim・term_sketch・multi_centroid）それぞれで，
  test の大きさと hit@1 のスピアマンの順位相関と，大きさと所有者 top-1 の順位相関が，どちらも正なら，その方式で単調な兼ね合いがあるとする．
  3 方式の全てで成り立てば支持，どれでも成り立たなければ反証，それ以外は部分的に支持とする．
- H-A3（BM25 は受信箱ごとの IDF では比べにくく，統計を共有すると改善する．密ベクトルの方が良い）：test の hit@1 が
  「flood_score（密ベクトル）≥ flood_bm25_local_idf」かつ「flood_bm25_shared_idf > flood_bm25_local_idf」なら支持する．
- 差の主張には hit@1 の 95% ブートストラップ信頼区間を併記し，区間が重なる差は「差がはっきりしない」と書く．

## p0004 の着手前の確認（2026-10-09）

### 新規性の確認（p0004 §5.3．N1〜N3）

C1〜C4 と実質的に重なる先行研究は見つからなかった（§12 の 5 には当たらない）．本文の読み取りは要約を経由しているので，
引用するときは原文で確かめ直す．

| 研究 | 設定 | 測っているもの | 重なり |
|---|---|---|---|
| DRAG（arXiv:2505.00443） | NetworkX による 1 プロセスのシミュレーション（20〜100 ピア）．公開データ（MMLU・合成の医療 QA・ニュース） | メッセージ数と精度．漏洩は定性的に触れるだけ | C1 の一部（探索のコスト）．C2〜C4 は無し |
| FedRepRAG（arXiv:2609.22162） | 1 枚の A100 で 10〜20 クライアントを模擬 | 表現の inversion と membership inference．クエリ埋め込みは全員へ送る（ルーティング無し） | C1 の一部（共有物の漏洩）．公開の段階・見つけてもらえる度合い・質問の露出は無し |
| Semord（arXiv:2609.25514） | 分散ベクトル検索．実機 32 ピアとシミュレーション．RAG ではない | recall・接触ピア数・待ち時間 | C1・C4 の一部 |
| A Wolf in Sheep's Clothing（arXiv:2605.28112） | 申告する profile（重心・k-means・説明文など）を偽造してルーティングを乗っ取る攻撃．シミュレーション | 完全性（改ざん）．profile からの漏洩は測っていない | C1 の一部（公開する記述の種類が近い）．関連研究として引用する |
| FD-RAG（arXiv:2605.27432），DRAGON（arXiv:2504.11197） | エッジの連合 RAG／端末とクラウドの分散 RAG | 共有物の復元攻撃／実機の待ち時間 | C1・C4 の一部 |

EnronQA を連合・分散の設定で使った研究は見つからなかった．C2 型の比較（質問者の大きいモデル，専門家の小さいモデル，同じ小さいモデルの対照，
GPU への委託）も見つからなかった．C1 の差別化は，FedRepRAG と A Wolf in Sheep's Clothing との対比で明確に書く．

### EnronQA のデータ検証とゲート G0（`results/20261009_eq0_data_check/`）

- V1：1 行は 1 通のメールで，train・dev・test は同じ 73,772 通を持ち，質問だけを分割している（333,473／105,515／89,316 問．論文と一致．分割をまたぐ重複は 0）．
  論文のメール数 103,638 通に対し，配布物は質問の付いた 73,772 通なので，検索の絶対値は論文と直接比べない．
- V2：論文に「CC BY 4.0」と明記されている．Hugging Face のメタデータには記載が無い．
- V3：テストのメールの 3.0%（2,250 通，テスト質問 2,722 問）に，他の受信箱に Jaccard 0.9 以上のメールがある．これらの質問は主評価から除く．
- V4：受信箱は 150 個．20 問に満たない受信箱は 5 個．受信箱あたり最大 20 問の抽出で 2,965 問（除外の前）．
- V5：全てのテスト質問に別解 1 個・誤答 2 個がある．判定の検証と 3 択の補助指標に使える．
- G0 を通過した．判定と回答のプロンプトは論文の付録 B.5・B.6 にある（判定モデルの実装で使う）．
- 制御点の取得：期限切れの Hugging Face のトークンが gpu2 に残っていたため，匿名で取得した．データは制御点だけに置く（ユーザーの指示）．

## E1 の判定（2026-10-09．p0003 S2）

E1 の全問実行は research-cycle を通さず，`run_full.sh` で 2026-10-07〜09 に行った．条件は全て temperature 0，
`k_ret` 50，`k_rerank` 15，質問者の LLM は llama3.1:8b．

| run | 条件 | n | 失敗率 | 選択できない率 | 正答率 [95% CI] | 適合率 / 再現率（test） | 問い合わせ（削減率） |
|---|---|---|---|---|---|---|---|
| s1 | FeB4RAG all，retrieval_only，qrels_oracle | 790 | 0 | － | － | 0.724 / 1.000 | 13.00（0） |
| s2 | FeB4RAG ragroute | 790 | 0 | － | － | 0.872 / 0.874 | 9.44（27.4%） |
| s3 | MedRAG all，retrieval_only，score | 7,663 | 0 | － | － | 0.445 / 1.000 | 4.00（0） |
| s4 | MedRAG all，snippet_return | 7,663 | 0.74%（57） | 1.6% | 0.659 [0.648, 0.670] | 0.445 / 1.000 | 4.00（0） |
| s5 | MedRAG ragroute | 7,663 | 0 | 1.4% | 0.657 [0.647, 0.668] | 0.865 / 0.799 | 1.65（58.7%） |
| s6 | MedRAG random（3 源） | 7,663 | 0 | 1.4% | 0.654 [0.643, 0.665] | 0.445 / 0.749 | 3.00（25.0%） |
| s7 | MedRAG none | 7,663 | 0 | 1.2% | 0.640 [0.629, 0.651] | － | 0（100%） |
| s8 | MedRAG all，cross_encoder | 7,663 | 0 | 1.4% | 0.689 [0.678, 0.699] | 0.445 / 1.000 | 4.00（0） |

**判定**（`.claude/research/config.yml` の `success_criteria`）
- ラベル一致：s3 で 0.999（7,663 問中 6 問が，上位 15 件の境界で 1 データ源だけ違う）．基準 0.99 以上を満たす．
- 失敗率：全実行で 1% 以下．s4 の 57 問は専門家への接続切れ（ReadError 51，RemoteProtocolError 6）で，
  再試行の追加（`0d7ed6b`）より前のイメージで動いていた．s5 以降は失敗 0．
- s3 と s4 の `top_doc_ids` は，s4 で成功した 7,606 問の全てで一致した（検索は同じで，回答方式だけが違う）．
- RAGRoute の報告値との比較：FeB4RAG の ragroute の問い合わせ数 9.44 は，論文の 9.20 とほぼ同じ．MedRAG の再現率・削減率は，
  リポジトリ内に論文の報告値が無いので比較できない．差の原因の候補は p0002 §7（fp16 の埋め込み，温度 0，ラベルの再計算）．
- 方式間の正答率（s4 を基準に，s4 で成功した 7,606 問で McNemar の正確検定）：
  - all と ragroute：0.659 と 0.658，p = 0.71．差は無い
  - all と random：0.659 と 0.655，p = 0.33．差は無い
  - all と none：0.659 と 0.640（+1.9 pt），p = 0.00094．差がある
- 実行条件の揃い：`git_head` は s1・s2・s4 が `d238f5fdf1ac-dirty`，s3 が `d238f5fdf1ac`，s5〜s7 が `0d7ed6ba587c-dirty`．
  間の差は `src/` で analysis・cli（ラベル一致を出す条件．分析だけ）と requester（検索要求の接続エラーの再試行）の 3 ファイル．
  再試行は接続エラーのときだけ働くので，成功した質問の結果の値は変わらない．
- 待ち時間は各条件 1 回の実測なので，主張に使わない．

**再集計で分かったこと**（p0004 の動機）
- 問題集ごとの all − none（McNemar）：PubMedQA +18.4 pt（p = 1.9×10⁻¹⁹），BioASQ +6.9 pt（p = 1.8×10⁻⁴），MedMCQA +0.7 pt（p = 0.37），
  MedQA −1.7 pt（p = 0.22），MMLU-Med −0.1 pt（p = 1）．RAG の効果は PubMedQA と BioASQ にほぼ限られる．
- ragroute は全 7,663 問で PubMed を選んでいた．関連ラベル（統合後の上位 15 件への寄与）は，専門性よりコーパスの大きさを反映している．
- random が PubMed を選ばなかった PubMedQA の 115 問では，正答率は random 0.591，all 0.800，none 0.661．正しい相手に聞く価値は実在する．
- `snippets_exposed` はシャードごとの取得件数の合計なので，シャードの分け方に依存する（PubMed の 6 シャードで 300 と数える）．

**s8 について**：最初の実行（2026-10-08）は，制御点の GPU で再ランクのモデルと 8B の Ollama が VRAM を取り合い，
Ollama が 33 層中 28 層しか GPU に載らず，生成が 1 問約 79 秒になった．1,874 問で止め，その結果は他の条件と揃わないので捨てた
（`/home/denjo/atrium-s8-offload-partial/` に退避）．質問者の LLM を wafl500 に分けて（`0c9f6c2`）やり直したが，1,191 問の時点で
p0004 に従い保留にした（`/home/denjo/workspace/ktakahashi/atrium/results/20261007_s8_med_all_ce/` に残し，同じ run_id で再開できる）．
再開するときは，その実行に限り `cluster.requester_llm` を wafl500 にする（p0004 §14）．
2026-10-12 に保留が解除され，同じ run_id で全問を終えた（上の表の s8 の行．判定と s4 との比較は「s8：MedRAG の cross_encoder」）．

**計画からの逸脱**：s8 の停止と，`cluster.requester_llm` の追加・結果の commit は，p0003 §3 の待機中に，ユーザーの指示で行った．

## 準備段階の記録（2026-10-05〜06）

### データ準備（制御点 `/home/denjo/atrium-data`）

- MedRAG：4 データ源を MedCPT で全て埋め込み直した（配布の埋め込みは 403．fp16 の混合精度，backlog B2）．
  16 シャード（PubMed 6，Wikipedia 8，StatPearls 1，Textbooks 1．1 シャード最大約 419 万断片）．
  StatPearls は 384,050 断片で，研究計画書 §7.1 の約 30.1 万より多い（2026-10 時点の NCBI の配布物）．
  関連ラベル（統合後の上位 15 件に断片を出したデータ源）は 1 問あたり平均 1.78 個
  （PubMed 7,423 問，Wikipedia 3,240 問，StatPearls 1,715 問，Textbooks 1,291 問）．
  ルーター（RAGRoute の MLP）は test で適合率 0.865，再現率 0.799，AUC 0.930．
- FeB4RAG：13 エンジン．ラベルは 790 件（関連ありのエンジンが無い 5 件を含む），関連ありのエンジンは平均 9.46 個．
  総点検で SGPT の最大長と trec-covid の重心を直したため，2026-10-06 にシャードの重心・クエリの埋め込み・
  split・ルーターを作り直した．ルーターは test で適合率 0.872，再現率 0.874，AUC 0.844．
- 所要時間の実測は docs/d0003_experiment_workflow.md §3．

### E0（results/20261006_052434/e0_summary.md）

研究計画書 §8 の E0 は準備段階で実施した．専門家（13.100，13.101．i5-8350U）の結果:

| 項目 | 値 |
|---|---|
| メモリ | 8 GB × 2（デュアルチャネル，DDR4-2400） |
| qwen3-0.6B（Q4_K_M，4 スレッド） | pp512 約 200，pp4096 約 113，tg128 約 26 tokens/s |
| qwen3-1.7B | pp512 約 68，pp4096 約 54，tg128 約 9 tokens/s |
| qwen3-4B | pp512 約 24，pp4096 約 20，tg128 約 4 tokens/s |
| qwen3-8B | pp512 約 12，pp4096 約 11，tg128 約 2.2 tokens/s |
| FAISS fp16 平坦索引（上位 50 件） | 100 万／200 万／300 万ベクトルで 0.30／0.57／0.85 秒（約 0.28 秒／100 万ベクトル） |
| MedCPT のクエリ埋め込み（CPU） | 16〜128 トークンで 36〜137 ms |
| ネットワーク | RTT 0.5〜0.8 ms，約 930 Mbit/s |

読み取れること（RQ2 の設計への含意）:
- 宿る型の専門家が 15 断片（約 3,000〜4,000 トークン）を読むプレフィルは，0.6B で約 30 秒，1.7B で約 70 秒，
  4B で約 3 分，8B で約 6 分．実用の範囲は 0.6B〜1.7B で，4B 以上は断片数 k を減らす必要がある．
- 1 シャード（約 419 万ベクトル）の検索は約 1.2 秒（外挿）．専門家ノードは検索を直列に処理するので，
  MIRAGE の全 7,663 問の retrieval_only は，ノードあたり約 1.2 秒 × 7,663 問 ≈ 2.5 時間が下限の目安．

### 実機での検証

- FeB4RAG（results/20261006_043855，20261006_044022）：全 790 問，失敗 0．routing=all は再現率 1.000．
  ragroute は test で平均 9.19 エンジンに問い合わせ（研究計画書 §7.2 の RAGRoute の値 9.20 とほぼ同じ），
  削減率 29.3%，再現率 0.866．これは作り直す前のルーターの値である．
- FeB4RAG（results/20261006_214950．作り直した後の先頭 40 問）：ragroute・retrieval_only・qrels_oracle で失敗 0，
  check-data の検査を通過した．全 790 問での ragroute の値は E1 で測り直す．
- 宿る型（results/20261006_050758．FeB4RAG の 5 問）：専門家の qwen3:0.6b が 15 断片で回答し，原文を返さない
  ことを確認した．プレフィル 2,641 トークンに約 20 秒．並列に送ると専門家で待ち行列ができる
  （local_answer.timeout_s を 1,800 秒にした）．
- MedRAG（results/20261006_094749．16 台の専門家，問題集ごとに 20 問の計 100 問）：routing=all・retrieval_only で
  **ラベル一致 1.000**（中継点のラベルと実機の分散検索・統合の結果が完全に一致）．1 問の検索は約 4.65 秒
  （4 問を並列に処理．専門家あたり 1 問約 1.2 秒）．
- MedRAG（results/20261006_100032．問題集ごとに 5 問の計 25 問）：ragroute・snippet_return（llama3.1:8b，
  temperature 0）で正答率 0.72 [0.52, 0.86]，問い合わせ数の削減率 0.66，1 問の生成は中央値約 22 秒
  （並列 4 問を 2 件ずつ処理．25 問で 160 秒）．
- MedRAG（results/20261006_205154，20261006_224015，20261006_230206，20261007_005901，20261007_010910．all・retrieval_only・score，
  50 問または 25 問）：スクリプトの安全化（`7482566`），設定の書式の変更（`273c9e3`），配置先の変更（`82aec35`）などのたびに流した動作確認．
  全て失敗 0，ラベル一致 1.000，e2e の中央値 4.6〜4.7 秒で，変更の前後で結果は変わらなかった．
- 準備段階で直した不具合と運用上の注意は git log（2026-10-06）と docs/d0003_experiment_workflow.md §4 にある．
