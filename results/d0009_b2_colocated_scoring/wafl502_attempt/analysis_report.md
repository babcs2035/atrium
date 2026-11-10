# d0009 B2 採点を生成と同じ GPU に載せる（機械集計．採否は評価の担当）

機: wafl502　有効なラウンド: [1]　ゲート: {"G_GEN": true, "G_S16": true, "G_CO_feasible": true, "G_CO_scores_match": true, "smoke": true, "all_passed": true}

| ラウンド | X_gen(G) | X_gen(B) | r_g | X_req(S) | X_req(B) | r_s | E_co | 共通の候補 |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.472 | 0.232 | 0.458 | 2.626 | 1.419 | 0.540 | 0.998 | 124 |

H-C0: fits　H-C1: small_loss　機械の結論: undetermined_not_closed
載せられない理由: {"copass": [], "d0009_b2_co_r1_b": [], "d0009_b2_co_r2_b": []}
GPU メモリの最大 MiB: {"copass": 8809.0, "d0009_b2_co_r1_b": 8845.0, "d0009_b2_co_r2_b": 8825.0}
無効になった実行: {"d0009_b2_co_r1_g": ["wafl502: slowdown Active samples 1/0"], "d0009_b2_co_r2_g": ["wafl502: slowdown Active samples 1/0"]}
記述（見積もり．採否に使わない）: {"E_co_median": 0.9983525364065862, "X_gen_G_median_cps": 0.4716666666666667, "X_req_S_median_qps": 2.6255555555555556, "dedicated_one_scoring_only": 1.415, "colocated_one": 1.48313264877948}
