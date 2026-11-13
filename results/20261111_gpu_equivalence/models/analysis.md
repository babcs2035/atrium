# GPU マイクロベンチマークの同等性判定

対象: 4 台（wafl503, wafl506, wafl507, wafl508）
熱による中止: 6 台（wafl500, wafl501, wafl502, wafl504, wafl505, wafl509）
組数: 6，許容幅: 0.95〜1.05（Bonferroni 同時 95% CI）

## embed: equivalent

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl503 | wafl506 | 1.031 | [1.030, 1.032] | 9 | equivalent |
| wafl503 | wafl507 | 1.033 | [1.031, 1.034] | 9 | equivalent |
| wafl503 | wafl508 | 1.012 | [1.011, 1.013] | 9 | equivalent |
| wafl506 | wafl507 | 1.001 | [1.000, 1.002] | 10 | equivalent |
| wafl506 | wafl508 | 0.981 | [0.980, 0.982] | 10 | equivalent |
| wafl507 | wafl508 | 0.980 | [0.979, 0.980] | 10 | equivalent |

## score: equivalent

| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |
|---|---|---|---|---|---|
| wafl503 | wafl506 | 1.010 | [1.008, 1.012] | 8 | equivalent |
| wafl503 | wafl507 | 1.012 | [1.010, 1.014] | 8 | equivalent |
| wafl503 | wafl508 | 1.006 | [1.004, 1.008] | 7 | equivalent |
| wafl506 | wafl507 | 1.002 | [1.001, 1.003] | 10 | equivalent |
| wafl506 | wafl508 | 0.996 | [0.996, 0.996] | 9 | equivalent |
| wafl507 | wafl508 | 0.994 | [0.992, 0.995] | 9 | equivalent |

