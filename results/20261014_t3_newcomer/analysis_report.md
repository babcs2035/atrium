# T3：150 人での新参者の評価（再学習なしで新参者を見つけられるか）

- 新参者 30 個（抽出した 30 個の先頭から），test の質問は新参者の問だけ（pooled），m = 2
- 退行の確認（全 test の hit@1・hit@2 が EQ2 と一致）：通過
- ragroute-frozen の学習時間：1 個あたり平均 169 秒

| 方式 | n | hit@1 | hit@2 [95%CI] |
|---|---|---|---|
| card_sim | 600 | 0.118 | 0.155 [0.127, 0.185] |
| term_sketch | 600 | 0.310 | 0.377 [0.338, 0.415] |
| centroid_sim | 600 | 0.263 | 0.328 [0.293, 0.365] |
| multi_centroid | 600 | 0.363 | 0.442 [0.403, 0.482] |
| flood_score | 600 | 0.705 | 0.802 [0.768, 0.832] |
| ragroute-upper | 600 | 0.233 | 0.282 [0.248, 0.317] |
| random | 600 | 0.010 | 0.017 [0.007, 0.027] |
| ragroute-frozen | 600 | 0.070 | 0.118 [0.092, 0.143] |

- 参考：ragroute-frozen の，新参者ではない受信箱の質問での hit@2 = 0.284（n = 87600）

## 判定

- H-N1（ragroute-frozen < ragroute-upper，区間が重ならない）：支持
- H-N2（flood_score が ragroute-frozen より高い，区間が重ならない）：支持
- H-N2（公開情報を使う最良の方式 multi_centroid が ragroute-frozen より高い，区間が重ならない）：支持
