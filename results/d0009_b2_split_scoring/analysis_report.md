# d0009 B2 採点を G 枚の GPU に分けたときの処理量（機械集計．採否は評価の担当）

入れ子の順: wafl502 wafl503 wafl504 wafl505 wafl506 wafl507 wafl508 wafl509

| G | 有効なラウンド | X(G) 問/秒（ラウンドごと） | E(G)（ラウンドごと） | 中央値 X | Σx_h に対する比 | 分け合いの上限の見積もり |
|---|---|---|---|---|---|---|
| 1 | [1, 2, 3] | r1: 0.750, r2: 0.750, r3: 0.750 | r1: 1.000, r2: 1.000, r3: 1.000 | 0.750 | 1.452 | 0.750 |
| 2 | [1, 2, 3] | r1: 1.308, r2: 1.292, r3: 1.292 | r1: 0.872, r2: 0.861, r3: 0.861 | 1.292 | 1.123 | 1.056 |
| 4 | [1, 2, 3] | r1: 2.492, r2: 2.600, r3: 2.525 | r1: 0.831, r2: 0.867, r3: 0.842 | 2.525 | 1.082 | 0.792 |
| 8 | [1, 2] | r1: 5.267, r2: 5.200 | r1: 0.878, r2: 0.867 | 5.233 | 1.106 | 0.264 |

H-B2a: undetermined　H-B2b（G*）: 4
無効になった実行: {"r3_g8": ["safety abort: wafl509 temp=72 sw=Active", "wafl505: slowdown Active samples 1/0", "wafl509: slowdown Active samples 2/0"], "r3_g8_retry": ["safety abort: wafl509 temp=83 sw=NotActive", "no score_bench.json", "wafl509: max temperature 83.0"]}

## 評価（Iteration 37 の phase 3．2026-11-10．上の機械集計は変えていない）

- 判定：**決まらない**（採用でも棄却でもない記述として値 `multi_gpu_scoring` を閉じる）．事前登録の成功条件 (1)〜(3) を満たし，H-B2a は E(4) の r1・r3 が 0.85 未満で 0.70 未満のラウンドは無い．`verdict.json` の `adoption` に記録した．
- 事後の記述（採否に使わない．`verdict.json` の `verdict_revised`）：G = 1 では 30 問が塊で同期して終わり，120 秒の区間にちょうど 3 塊が入るので X(1) = 0.750 になった．塊の間隔（43.5 秒）からの X(1) は 0.690 で，その値なら E(2) 0.936〜0.947・E(4) 0.902〜0.942・E(8) 0.942〜0.953．数値は `window_bias.json`（`scripts/d0009/b2_window_bias.py`）．
- 記述は `.claude/research/journal.md` の Iteration 37「実行済み」．
