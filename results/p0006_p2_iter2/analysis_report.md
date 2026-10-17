# frontier-eval（p0006 P2．EnronQA）

- 方式：flood_score, device_probe, body_baseline, term_stats, term_stats_large, hybrid_summary．m = 2，受信箱 150
- R4（既存の点の再現）：**不通過**

| 既存の点 | 期待の hit | 得た hit | 期待の要求数 | 得た要求数 | 通過 |
|---|---|---|---|---|---|
| flood_score | 0.8041 | 0.8041 | 32.0 | 32.00 | True |
| device_probe_d24 | 0.7837 | 0.7837 | 26.0 | 26.00 | True |
| device_probe_d12 | 0.6830 | 0.6830 | 14.0 | 14.00 | True |

## dev で選んだ設定の test の値（n30 と n150）

### n30

| 族 | 設定 | dev が目標に届く | test の要求数（平均・p95） | 往復（平均） | test の hit@2 | A 群を除く hit@2 | 公開情報の大きさ（bytes） | 出どころ top-1 |
|---|---|---|---|---|---|---|---|---|
| body_baseline | device_probe_body {"D": 30} | True | 32.00・32 | 2.00 | 0.8041 | 0.7595 | 14,367,744 | 0.526 |
| term_stats | term_stats {"V": 64000, "D": 24} | True | 26.00・26 | 2.00 | 0.7973 | 0.7485 | 15,360,000 | 0.635 |
| hybrid_summary | hybrid_summary {"V": 64000, "w": 0.5, "D": 16} | True | 18.00・18 | 2.00 | 0.8017 | 0.7526 | 29,727,744 | 0.726 |

### n150

| 族 | 設定 | dev が目標に届く | test の要求数（平均・p95） | 往復（平均） | test の hit@2 | A 群を除く hit@2 | 公開情報の大きさ（bytes） | 出どころ top-1 |
|---|---|---|---|---|---|---|---|---|
| body_baseline | device_probe_body {"D": 150} | True | 152.00・152 | 2.00 | 0.8041 | 0.7595 | 14,367,744 | 0.435 |
| term_stats | term_stats {"V": 16000, "D": 48} | True | 50.00・50 | 2.00 | 0.7895 | 0.7375 | 19,200,000 | 0.718 |
| hybrid_summary | hybrid_summary {"V": 16000, "w": 0.5, "D": 16} | True | 18.00・18 | 2.00 | 0.7905 | 0.7320 | 33,567,744 | 0.763 |

## H-P2a（hit@m ≥ 0.78 を要求数 ≤ 16 で）

結果：不成立

| 族 | test の hit | test の要求数 | 通過 |
|---|---|---|---|
| body_baseline | 0.8041 | 32.00 | False |
| term_stats | 0.7973 | 26.00 | False |
| hybrid_summary | 0.8017 | 18.00 | False |

