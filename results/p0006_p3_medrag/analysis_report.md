# frontier-eval（p0006 P3．medrag）

- 方式：flood_score, body_baseline, sample_summary, term_stats, term_stats_large, hybrid_summary, adaptive_cutoff．m = 3，デバイス 16
- dev の全員に聞く方式の再現率が最大の配置：medrag
- シャードのラベルから求めたデータ源の集合が labels.json と一致する割合：1.0000

## medrag（全員に聞く方式の再現率 dev 0.5737・test 0.5740．目標 = 0.95 倍）

| 族 | 目標に届く最小の要求数（test） | 面積 | 最良の再現率 |
|---|---|---|---|
| adaptive_cutoff | - | 0.0943 | 0.1006 |
| body_baseline | 19.00 | 0.3069 | 0.5740 |
| hybrid_summary | 15.00 | 0.4362 | 0.5740 |
| reference | 19.00 | 0.0000 | 0.5740 |
| sample_summary | 15.00 | 0.3827 | 0.5740 |
| term_stats | 11.00 | 0.4490 | 0.5740 |
