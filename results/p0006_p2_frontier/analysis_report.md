# frontier-eval（p0006 P2．EnronQA）

- 方式：flood_score, multi_centroid, centroid_probe, device_probe, body_baseline, sample_summary, term_stats, adaptive_cutoff, hierarchy, overlay_walk．m = 2，受信箱 150
- R4（既存の点の再現）：通過

| 既存の点 | 期待の hit | 得た hit | 期待の要求数 | 得た要求数 | 通過 |
|---|---|---|---|---|---|
| flood_score | 0.8041 | 0.8041 | 32.0 | 32.00 | True |
| device_probe_d24 | 0.7837 | 0.7837 | 26.0 | 26.00 | True |
| device_probe_d12 | 0.6830 | 0.6830 | 14.0 | 14.00 | True |
| centroid_probe_k75 | 0.7612 | 0.7612 | - | 31.12 | True |
| multi_centroid | 0.4401 | 0.4401 | 2.0 | 2.00 | True |

## dev で選んだ設定の test の値（n30 と n150）

### n30

| 族 | 設定 | dev が目標に届く | test の要求数（平均・p95） | 往復（平均） | test の hit@2 | A 群を除く hit@2 | 公開情報の大きさ（bytes） | 出どころ top-1 |
|---|---|---|---|---|---|---|---|---|
| body_baseline | device_probe_body {"D": 30} | True | 32.00・32 | 2.00 | 0.8041 | 0.7595 | 14,367,744 | 0.526 |
| sample_summary | sample_summary {"S": 4, "scorer": "max", "D": 30} | True | 32.00・32 | 2.00 | 0.8041 | 0.7595 | 368,640 | 0.073 |
| term_stats | term_stats {"V": 16000, "D": 24} | True | 26.00・26 | 2.00 | 0.7952 | 0.7462 | 3,840,000 | 0.589 |
| adaptive_cutoff | adaptive_cutoff {"base": "multi_centroid_body", "rule": "softmax_mass", "param": 0.9, "Dmax": 16, "tau": 0.05} | False | 17.50・18 | 2.00 | 0.6963 | 0.6381 | 14,367,744 | - |
| hierarchy | hierarchy {"g": 3, "grouping": "random", "G": 3, "D2": 8} | False | 13.00・13 | 3.00 | 0.5810 | 0.5268 | 294,912 | - |
| overlay_walk | overlay_walk {"d": 8, "ttl": 12, "walkers": 3, "rule": "greedy"} | True | 28.80・31 | 14.00 | 0.7830 | 0.7334 | 3,831,392 | - |

### n150

| 族 | 設定 | dev が目標に届く | test の要求数（平均・p95） | 往復（平均） | test の hit@2 | A 群を除く hit@2 | 公開情報の大きさ（bytes） | 出どころ top-1 |
|---|---|---|---|---|---|---|---|---|
| body_baseline | device_probe_body {"D": 150} | True | 152.00・152 | 2.00 | 0.8041 | 0.7595 | 14,367,744 | 0.435 |
| sample_summary | sample_summary {"S": 4, "scorer": "max", "D": 150} | True | 152.00・152 | 2.00 | 0.8041 | 0.7595 | 1,843,200 | 0.121 |
| term_stats | term_stats {"V": 16000, "D": 48} | True | 50.00・50 | 2.00 | 0.7895 | 0.7375 | 19,200,000 | 0.718 |
| adaptive_cutoff | adaptive_cutoff {"base": "sample_64_max", "rule": "softmax_mass", "param": 0.9, "Dmax": 16, "tau": 0.05} | False | 17.87・18 | 2.00 | 0.6415 | 0.5662 | 28,062,720 | - |
| hierarchy | hierarchy {"g": 6, "grouping": "similarity", "G": 3, "D2": 8} | False | 13.00・13 | 3.00 | 0.3823 | 0.3344 | 589,824 | - |
| overlay_walk | overlay_walk {"d": 8, "ttl": 12, "walkers": 3, "rule": "greedy"} | False | 40.73・41 | 14.00 | 0.5741 | 0.5181 | 766,272 | - |

## H-P2a（hit@m ≥ 0.78 を要求数 ≤ 16 で）

結果：不成立

| 族 | test の hit | test の要求数 | 通過 |
|---|---|---|---|
| body_baseline | 0.8041 | 32.00 | False |
| sample_summary | 0.8041 | 32.00 | False |
| term_stats | 0.7952 | 26.00 | False |
| adaptive_cutoff | 0.6963 | 17.50 | False |
| hierarchy | 0.5810 | 13.00 | False |
| overlay_walk | 0.7830 | 28.80 | False |

