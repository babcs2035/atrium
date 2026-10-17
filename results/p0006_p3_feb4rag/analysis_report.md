# frontier-eval（p0006 P3．feb4rag）

- 方式：flood_score, body_baseline, sample_summary, term_stats, term_stats_large, hybrid_summary, adaptive_cutoff．m = 9，デバイス 13
- dev の全員に聞く方式の再現率が最大の配置：feb4rag_none

## feb4rag_none（全員に聞く方式の再現率 dev 0.7903・test 0.7814．目標 = 0.95 倍）

| 族 | 目標に届く最小の要求数（test） | 面積 | 最良の再現率 |
|---|---|---|---|
| adaptive_cutoff | - | 0.4565 | 0.6602 |
| body_baseline | 9.00 | 0.7861 | 0.7878 |
| hybrid_summary | 17.00 | 0.5010 | 0.7898 |
| reference | 22.00 | 0.0000 | 0.7814 |
| sample_summary | 21.00 | 0.4530 | 0.7815 |
| term_stats | 17.00 | 0.5064 | 0.7891 |

## feb4rag_zscore（全員に聞く方式の再現率 dev 0.7616・test 0.7499．目標 = 0.95 倍）

| 族 | 目標に届く最小の要求数（test） | 面積 | 最良の再現率 |
|---|---|---|---|
| adaptive_cutoff | - | 0.4565 | 0.6602 |
| body_baseline | 9.00 | 0.7860 | 0.7860 |
| hybrid_summary | 17.00 | 0.4996 | 0.7710 |
| reference | 22.00 | 0.0000 | 0.7499 |
| sample_summary | 21.00 | 0.4517 | 0.7637 |
| term_stats | 17.00 | 0.5052 | 0.7736 |

## feb4rag_rank（全員に聞く方式の再現率 dev 0.7498・test 0.7393．目標 = 0.95 倍）

| 族 | 目標に届く最小の要求数（test） | 面積 | 最良の再現率 |
|---|---|---|---|
| adaptive_cutoff | - | 0.4565 | 0.6602 |
| body_baseline | 9.00 | 0.7860 | 0.7860 |
| hybrid_summary | 17.00 | 0.4991 | 0.7653 |
| reference | 22.00 | 0.0000 | 0.7393 |
| sample_summary | 21.00 | 0.4512 | 0.7577 |
| term_stats | 17.00 | 0.5050 | 0.7701 |
