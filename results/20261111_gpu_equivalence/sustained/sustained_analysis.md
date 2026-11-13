# 持続負荷の判定

## generation（基準: 末尾 10 分／開始 5 分 >= 0.95）

| host | rep 数 | 最小比 | 温度ピーク | 中止 | 判定 |
|---|---|---|---|---|---|
| wafl503 | 3 | 0.999 | 69.0 | no | sustained_ok |
| wafl506 | 3 | 1.000 | 77.0 | no | sustained_ok |
| wafl507 | 3 | 1.000 | 77.0 | no | sustained_ok |
| wafl508 | 3 | 0.998 | 77.0 | no | sustained_ok |

## score（基準: 末尾 10 分／開始 5 分 >= 0.95）

| host | rep 数 | 最小比 | 温度ピーク | 中止 | 判定 |
|---|---|---|---|---|---|
| wafl503 | 3 | 1.000 | 66.0 | no | sustained_ok |
| wafl506 | 3 | 1.000 | 76.0 | no | sustained_ok |
| wafl507 | 3 | 1.000 | 74.0 | no | sustained_ok |
| wafl508 | 3 | 1.000 | 75.0 | no | sustained_ok |

