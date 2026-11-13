# LLM 生成の同等性判定

対象: 4 台（wafl503, wafl506, wafl507, wafl508）
熱による中止: 6 台（wafl500, wafl501, wafl502, wafl504, wafl505, wafl509）
組数: 6，許容幅: 0.95〜1.05（Bonferroni 同時 95% CI）

## short_c1: equivalent（出力ハッシュ一致: True）

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl503 | wafl506 | 1.025 | [1.019, 1.031] | 10 | equivalent |
| wafl503 | wafl507 | 1.026 | [1.026, 1.026] | 10 | equivalent |
| wafl503 | wafl508 | 0.992 | [0.992, 0.993] | 10 | equivalent |
| wafl506 | wafl507 | 1.001 | [0.995, 1.007] | 10 | equivalent |
| wafl506 | wafl508 | 0.968 | [0.963, 0.974] | 10 | equivalent |
| wafl507 | wafl508 | 0.967 | [0.967, 0.968] | 10 | equivalent |

## short_c4: performance_difference（出力ハッシュ一致: True）

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl503 | wafl506 | 1.061 | [1.048, 1.073] | 10 | undetermined |
| wafl503 | wafl507 | 1.058 | [1.048, 1.068] | 10 | undetermined |
| wafl503 | wafl508 | 0.996 | [0.988, 1.003] | 10 | equivalent |
| wafl506 | wafl507 | 0.997 | [0.984, 1.010] | 10 | equivalent |
| wafl506 | wafl508 | 0.939 | [0.924, 0.954] | 10 | undetermined |
| wafl507 | wafl508 | 0.941 | [0.933, 0.950] | 10 | performance_difference |

## long_c1: equivalent（出力ハッシュ一致: True）

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl503 | wafl506 | 1.026 | [1.025, 1.026] | 10 | equivalent |
| wafl503 | wafl507 | 1.025 | [1.024, 1.025] | 10 | equivalent |
| wafl503 | wafl508 | 0.996 | [0.995, 0.996] | 10 | equivalent |
| wafl506 | wafl507 | 0.999 | [0.998, 1.000] | 10 | equivalent |
| wafl506 | wafl508 | 0.971 | [0.970, 0.972] | 10 | equivalent |
| wafl507 | wafl508 | 0.972 | [0.971, 0.973] | 10 | equivalent |

## long_c4: undetermined（出力ハッシュ一致: True）

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl503 | wafl506 | 1.046 | [1.039, 1.054] | 10 | undetermined |
| wafl503 | wafl507 | 1.046 | [1.041, 1.051] | 10 | undetermined |
| wafl503 | wafl508 | 0.995 | [0.993, 0.998] | 10 | equivalent |
| wafl506 | wafl507 | 1.000 | [0.992, 1.008] | 10 | equivalent |
| wafl506 | wafl508 | 0.951 | [0.945, 0.957] | 10 | undetermined |
| wafl507 | wafl508 | 0.951 | [0.948, 0.955] | 10 | undetermined |

