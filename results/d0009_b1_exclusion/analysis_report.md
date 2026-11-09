# d0009 B1（Iteration 35）生存確認で止まったノードを外す仕組みの障害時の効果

有効なラウンド：なし．採用：False．H-B1a の仮説の成立（container の 3 つ以上の k で上がった）：False．

## ゲート

```json
{
 "G_T": "mise run check の結果は journal に記録（このスクリプトでは確かめない）",
 "G_TB_live_table_miss_total": 0,
 "G_R_all_rounds": true,
 "G_FP_ii_false_positive_drops_by_round": {
  "d0009_b1_exclusion_r1": 10,
  "d0009_b1_exclusion_r2": 5,
  "d0009_b1_exclusion_r3": 7
 },
 "G_FP_i_pre_stop_hit_rate_by_round": {
  "d0009_b1_exclusion_r1": 0.8453038674033149,
  "d0009_b1_exclusion_r2": 0.8342857142857143,
  "d0009_b1_exclusion_r3": 0.8181818181818182
 },
 "n_valid_rounds": 0
}
```

## 主の指標（止めた間に答えられた割合．終わった時刻の版／聞いた時刻の版）

| 止め方 | k | 判定 | F_on 終わった時刻（ラウンド） | F30 終わった時刻（ラウンド） | F_on 聞いた時刻 | F30 聞いた時刻 | 問の数 |
|---|---|---|---|---|---|---|---|

## (D4) 窓の積分と境目 S*

| 止め方 | k | I_P（ラウンド） | I_C | S*（ラウンド） | S*（Iteration 30） |
|---|---|---|---|---|---|
