# GPU マイクロベンチマークの同等性判定

対象: 5 台（wafl502, wafl503, wafl506, wafl507, wafl508）
熱による中止: 5 台（wafl500, wafl501, wafl504, wafl505, wafl509）
組数: 10，許容幅: 0.95〜1.05（Bonferroni 同時 95% CI）

## fp32: performance_difference

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl502 | wafl503 | 1.021 | [1.020, 1.023] | 8 | equivalent |
| wafl502 | wafl506 | 1.062 | [1.060, 1.063] | 10 | performance_difference |
| wafl502 | wafl507 | 1.061 | [1.060, 1.062] | 10 | performance_difference |
| wafl502 | wafl508 | 1.032 | [1.031, 1.034] | 10 | equivalent |
| wafl503 | wafl506 | 1.039 | [1.037, 1.041] | 8 | equivalent |
| wafl503 | wafl507 | 1.038 | [1.037, 1.040] | 8 | equivalent |
| wafl503 | wafl508 | 1.011 | [1.009, 1.012] | 8 | equivalent |
| wafl506 | wafl507 | 0.999 | [0.998, 1.000] | 10 | equivalent |
| wafl506 | wafl508 | 0.972 | [0.971, 0.974] | 10 | equivalent |
| wafl507 | wafl508 | 0.973 | [0.972, 0.974] | 10 | equivalent |

## fp16: equivalent

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl502 | wafl503 | 1.030 | [1.028, 1.032] | 9 | equivalent |
| wafl502 | wafl506 | 1.044 | [1.041, 1.046] | 10 | equivalent |
| wafl502 | wafl507 | 1.042 | [1.039, 1.044] | 9 | equivalent |
| wafl502 | wafl508 | 1.032 | [1.029, 1.035] | 10 | equivalent |
| wafl503 | wafl506 | 1.014 | [1.012, 1.016] | 9 | equivalent |
| wafl503 | wafl507 | 1.011 | [1.009, 1.013] | 8 | equivalent |
| wafl503 | wafl508 | 1.003 | [0.999, 1.006] | 9 | equivalent |
| wafl506 | wafl507 | 0.998 | [0.997, 1.000] | 9 | equivalent |
| wafl506 | wafl508 | 0.989 | [0.986, 0.992] | 10 | equivalent |
| wafl507 | wafl508 | 0.991 | [0.987, 0.994] | 9 | equivalent |

## vram_bandwidth: equivalent

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl502 | wafl503 | 1.000 | [1.000, 1.000] | 8 | equivalent |
| wafl502 | wafl506 | 1.000 | [0.999, 1.000] | 9 | equivalent |
| wafl502 | wafl507 | 1.000 | [1.000, 1.000] | 8 | equivalent |
| wafl502 | wafl508 | 0.994 | [0.994, 0.994] | 8 | equivalent |
| wafl503 | wafl506 | 1.000 | [1.000, 1.000] | 8 | equivalent |
| wafl503 | wafl507 | 1.000 | [1.000, 1.000] | 7 | equivalent |
| wafl503 | wafl508 | 0.994 | [0.994, 0.994] | 7 | equivalent |
| wafl506 | wafl507 | 1.000 | [1.000, 1.000] | 9 | equivalent |
| wafl506 | wafl508 | 0.994 | [0.994, 0.994] | 9 | equivalent |
| wafl507 | wafl508 | 0.994 | [0.994, 0.994] | 8 | equivalent |

## h2d: performance_difference

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl502 | wafl503 | 0.999 | [0.998, 1.001] | 8 | equivalent |
| wafl502 | wafl506 | 1.023 | [1.021, 1.024] | 9 | equivalent |
| wafl502 | wafl507 | 0.999 | [0.997, 1.000] | 9 | equivalent |
| wafl502 | wafl508 | 1.102 | [1.097, 1.107] | 8 | performance_difference |
| wafl503 | wafl506 | 1.023 | [1.023, 1.024] | 8 | equivalent |
| wafl503 | wafl507 | 0.999 | [0.997, 1.002] | 8 | equivalent |
| wafl503 | wafl508 | 1.102 | [1.098, 1.107] | 7 | performance_difference |
| wafl506 | wafl507 | 0.977 | [0.975, 0.978] | 10 | equivalent |
| wafl506 | wafl508 | 1.078 | [1.074, 1.082] | 9 | performance_difference |
| wafl507 | wafl508 | 1.104 | [1.099, 1.108] | 9 | performance_difference |

## d2h: performance_difference

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl502 | wafl503 | 0.999 | [0.998, 1.001] | 8 | equivalent |
| wafl502 | wafl506 | 1.310 | [1.308, 1.312] | 10 | performance_difference |
| wafl502 | wafl507 | 0.997 | [0.996, 0.999] | 10 | equivalent |
| wafl502 | wafl508 | 1.108 | [1.105, 1.111] | 9 | performance_difference |
| wafl503 | wafl506 | 1.311 | [1.310, 1.313] | 8 | performance_difference |
| wafl503 | wafl507 | 0.998 | [0.996, 1.000] | 8 | equivalent |
| wafl503 | wafl508 | 1.108 | [1.104, 1.113] | 7 | performance_difference |
| wafl506 | wafl507 | 0.761 | [0.760, 0.762] | 10 | performance_difference |
| wafl506 | wafl508 | 0.845 | [0.843, 0.848] | 9 | performance_difference |
| wafl507 | wafl508 | 1.110 | [1.107, 1.114] | 9 | performance_difference |

## vram_check: equivalent

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl502 | wafl503 | 1.000 | [1.000, 1.000] | 7 | equivalent |
| wafl502 | wafl506 | 1.000 | [1.000, 1.000] | 10 | equivalent |
| wafl502 | wafl507 | 1.000 | [1.000, 1.000] | 10 | equivalent |
| wafl502 | wafl508 | 0.997 | [0.997, 0.998] | 10 | equivalent |
| wafl503 | wafl506 | 1.000 | [1.000, 1.000] | 7 | equivalent |
| wafl503 | wafl507 | 1.000 | [1.000, 1.000] | 7 | equivalent |
| wafl503 | wafl508 | 0.998 | [0.998, 0.998] | 7 | equivalent |
| wafl506 | wafl507 | 1.000 | [1.000, 1.000] | 10 | equivalent |
| wafl506 | wafl508 | 0.998 | [0.998, 0.998] | 10 | equivalent |
| wafl507 | wafl508 | 0.998 | [0.998, 0.998] | 10 | equivalent |

