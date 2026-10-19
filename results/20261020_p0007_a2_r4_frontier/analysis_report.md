# frontier-eval（p0006 P2．EnronQA）

- 方式：flood_score, multi_centroid, centroid_probe, device_probe．m = 2，受信箱 150
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

### n150

| 族 | 設定 | dev が目標に届く | test の要求数（平均・p95） | 往復（平均） | test の hit@2 | A 群を除く hit@2 | 公開情報の大きさ（bytes） | 出どころ top-1 |
|---|---|---|---|---|---|---|---|---|

## H-P2a（hit@m ≥ 0.78 を要求数 ≤ 16 で）

結果：不成立

| 族 | test の hit | test の要求数 | 通過 |
|---|---|---|---|

