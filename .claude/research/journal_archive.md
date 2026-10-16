## Iteration 15: 二段の方式の参加・離脱への頑健さのオフライン評価

### 調査・計画（2026-10-16．research-cycle の phase 1）

- 注記: rc-researcher は計画を確定したが，最後の段でツールの呼び出しが失敗し，ファイルへ書き込めなかった．この節は，オーケストレータが rc-researcher の戻り値を書き写したものである（内容は変えていない）．
- 経緯: backlog B39 で人間が選択肢 1（負荷の系列〔Iteration 4〜14〕を閉じて RQ の研究へ戻る）を選んだ．config.yml の levers は使い切っているので，新しいレバーを levers の末尾に足した（backlog B40 [auto-decided]）．

**調査で分かったこと**（出典は検索結果の抜粋で確かめた範囲．年と書誌は一部が未確認）

- 選択的検索の頑健さ: Kim, Y.「Robust Selective Search」（CMU の博士論文，2019．Hendriksen ら「Efficient Session Retrieval Using Topical Index Shards」の参考文献から確認）．
- 分散した選択的検索の負荷の偏り: Kulkarni・Callan「Efficient distributed selective search」．
- 離脱したノードへの要求: Rhea ら「Handling Churn in a DHT」（USENIX）．離脱下では要求が抜けたノードへ頻繁に送られることと，迂回できる経路の価値を論じている．
- P2P の離脱の実測: Stutzbach・Rejaie「Understanding Churn in Peer-to-Peer Networks」．
- 分散したベクトル検索の低ファンアウトのルーティング:「Semord: Learned Semantic-Preserving Placement and Low-Fanout Routing for Distributed Vector Search」（arXiv）．問い合わせる peer の数を 60% 以上減らし，再現率を 15% 以上上げたと報告している．
- 連合 RAG の総説（ACL Anthology「Federated Retrieval-Augmented Generation: A Systematic ...」）と RAGRoute: 検索結果の範囲では，データ源の離脱下でのルーティングの評価は見当たらなかった（網羅的には確かめていない）．
- 本リポジトリの空白: RQ-A で中心になった二段の方式（centroid_probe・device_probe）は，RQ-C（EQ7a・EQ7b）で評価していない．EQ7a で「表の更新の後に参加したノードが見えないことが原因」とした推測も未検証である．

**仮説**: 二段の方式は，2 段目で返事のあった受信箱の中から選ぶので，最後の m 個の枠を離脱済みの受信箱に取られない．このため，メンバー表が古くなったときの hit の相対的な低下は multi_centroid より小さい．

**単一レバー**: 新しいオフライン評価の CLI `atrium churn-cascade-eval`（`src/atrium/churn_cascade_eval.py`）を 1 つ足す（levers の `churn_eval.methods`）．config.yaml・データ・シャード・既存の routing の挙動は変えない．

- `src/atrium/churn_cascade_eval.py`: `churn_eval` の `simulate_availability`・`stale_view`・`summarize_runs`，`device_probe_eval` の `device_index_of_inbox`・`device_scores`，`cascade_eval` の採点を再利用する．
- `src/atrium/cli.py`: `cmd_churn_cascade_eval` を `cmd_churn_eval` と同じ形で足す．
- `scripts/tasks/remote.sh`: `churn_cascade_eval` のサブコマンドを足す．
- `tests/test_churn_cascade_eval.py` を足す．

**二段の方式の離脱下の規則**（事前登録）

1. 1 段目は multi_centroid（C = 32）の得点で，見えている受信箱だけを採点する．centroid_probe は見えている受信箱の上位 K 個を候補にする．device_probe は見えている受信箱の得点をデバイスごとに最大値でまとめ，上位 D 台に載る全ての受信箱を候補にする．
2. 候補の載るデバイスへ probe を送る．
3. 返事のあった（実際に稼働中の）デバイスの候補の中から，2 段目の密ベクトルの得点で上位 m = 2 個を選ぶ．
4. 返事の無かったデバイスの分は補わない（実機の churn の「返事のあった分で続ける」に合わせる）．
5. 問い合わせたノードは probe したデバイスとし，そのうち離脱していたノードを無駄な問い合わせとして数える．
6. 同点の扱いは `device_probe_eval` と同じ規則にする．

**条件**: EQ7a と同じ．稼働率 a ∈ {1.0, 0.9, 0.7, 0.5}，更新間隔 R ∈ {1, 10, 100, 1000}，時刻表の種 5 個，`experiment.seed` = 12，配置は `results/20261009_eq5_b1/placement.json`，問は test の 2,940 問．評価する構成は centroid_probe の K = 75・30 と device_probe の D = 24・12 で，対照として flood_score と multi_centroid を同じ表に出す．

**追加の集計**: hit を失った問を (i) 正解のノードが離脱している，(ii) 正解のノードは稼働中だがメンバー表で見えていない，(iii) 見えていて稼働中だが選ばれない，(iv) 1 段の方式で離脱済みの受信箱が枠を占めた，の 4 つに分ける．

**退行ゲート**（1 つでも通らなければ判定に進まず，原因を調べて記録する）

- G1: a = 1 の hit@2 が，4 構成とも `results/20261015_iter7_device_probe_eval/metrics.json` の test の値と 5e-4 以内で一致する．
- G2: flood_score・multi_centroid の (a, R, 種) ごとの hit・contacted・wasted が，`results/20261012_eq7a_churn/metrics.json` の `runs` と 1/2940 以内で一致する．
- G3: device_probe の D = 30（全台）と centroid_probe の K = 150 が，全ての実行で flood_score と同じ値になる．
- G4: 分解の (i)〜(iv) と hit の和が，全ての実行で問の数に等しい．

**判定**

- H-C2（主）: a = 0.7 で R = 1 → 1000 としたときの hit@2 の相対的な低下（1 − hit(R=1000) / hit(R=1)）が，device_probe（D = 12）で multi_centroid より 5 個の種のすべてで小さければ「支持」，そうでなければ「支持されない」．
- H-C3（記録）: D = 24，K = 75・30 の相対的な低下と flood_score との差を種ごとに出す．1 問あたりの問い合わせたノードの数と，無駄な問い合わせの数も出す．
- E-1（EQ7a の推測の検証）: flood_score の a = 0.7 で，正解が稼働中の問に限った hit@2 の R = 1 → 1000 の低下のうち，(ii) の増分で説明される割合が 0.8 以上なら「表の更新の後に参加したノードの見えなさが主因」と確かめたとする．

**成功条件**: G1〜G4 を通過し，H-C2 と E-1 を機械的に判定して記録すること．評価のためのレバーなので，支持・不支持のどちらでも成果物として採用する．計算は決定的で，揺れは時刻表の種の間だけにある．EQ7a の種の間の相対的な低下の幅（約 ±0.05）より小さい差は，差として読まない．

**手順と所要時間**: 実装とテストに約 2〜3 時間．事前登録（この節）を実行の前に push し，制御点のコンテナで 1 回だけ実行する（10 分未満の見込み．EQ7a は 50 秒）．出力は `results/<日付>_iter15_churn_cascade/`（`metrics.json`，`analysis_report.md`）．実機は使わない（EQ7b で flood_score・multi_centroid のオフラインの規則と実機の hit@2 が全 22 回で小数 4 桁まで一致したため）．

**限界**

- 実機の質問者の部分的な失敗を許す処理（`src/atrium/requester.py` の `_tolerant`．357 行付近）が，centroid_probe・device_probe の probe の失敗にも効くかは確かめていない．効かない場合，H-C2 の結果は「返事のあった分で続ける」と仮定した上限になる．
- オフラインと実機の一致を確かめたのは flood_score・multi_centroid だけで，二段の方式の離脱下の実機での再生は行わない（次の候補）．
- EQ7a と同じく，メールの時間的な到着・削除は再現しない．回答の正答率と待ち時間は測らない．
- 要レビューは backlog B40．

### 事前登録（2026-10-16．実装の後，実行の前に commit・push する．結果を見て変えない．実装は commit `01774a2`）

調査・計画の節の規則を，実行の前にそのまま固定する．計画からの変更と補足は backlog B41・B42 の決定に基づく．

**評価**: `atrium churn-cascade-eval`（`src/atrium/churn_cascade_eval.py`）を制御点のコンテナで 1 回だけ流す．出力名は `20261016_iter15_churn_cascade_eval` とする（計画の `<日付>_iter15_churn_cascade` から変更．B42）．

```bash
ssh wafl-ctrl5 'pgrep -af chain.sh || echo NO_CHAIN'   # 連続実行が動いていないことを確かめる
bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'
bash scripts/tasks/remote.sh churn_cascade_eval 20261016_iter15_churn_cascade_eval 20261009_eq5_b1 20261015_iter7_device_probe_eval 20261012_eq7a_churn
rsync -az wafl-ctrl5:<REMOTE_DIR>/results/20261016_iter15_churn_cascade_eval/ results/20261016_iter15_churn_cascade_eval/
```

**条件**: 調査・計画の「条件」のとおり（a ∈ {1.0, 0.9, 0.7, 0.5}，R ∈ {1, 10, 100, 1000}，種 5 個，`experiment.seed` = 12，m = 2，C = 32，test 2,940 問）．評価する構成は `centroid_probe_k75`・`centroid_probe_k30`・`device_probe_d24`・`device_probe_d12` で，対照は `multi_centroid`・`flood_score` である．G3 のために `centroid_probe_k150`・`device_probe_d30` も流す（K と D は配置の受信箱の数とデバイスの数から決める）．

**規則の実装上の補足**（調査・計画の 1〜6 を具体化したもの）
- 同点の扱い: centroid_probe は受信箱の番号の小さい方を先にする（stable な並べ替え．`cascade_eval` と同じ）．device_probe はデバイスの番号の小さい方を先にする．デバイスの番号は受信箱の名前の中で初めて現れた順で，実機の `top_devices` と同じ規則である．2 段目の順位は，正解と同点の受信箱を正解より上に数える（`route_under_churn` と同じ）．
- デバイスの番号（初出順）とノードの番号（`placement.nodes` の順）は対応表で結ぶ．
- `contacted` は probe を送ったノードの数，`wasted` はそのうち離脱していたノードの数とする．本照会は数えない．

**退行ゲート**（1 つでも通らなければ H-C2・E-1 を判定せず，原因を調べて記録する）
- G1: a = 1 の 4 構成の hit@2 が，全ての (R, 種) で Iteration 7 の test の値（`device_probe_d24` 0.783673，`device_probe_d12` 0.682993，`centroid_probe_k75` 0.761224，`centroid_probe_k30` 0.690476）と 5e-4 以内で一致する．
- G2: multi_centroid・flood_score の (a, R, 種) ごとの hit・contacted・wasted の**すべて**が，EQ7a の `runs` と 1/2940 以内で一致する．160 件のすべてを照合する（B42）．
- G3: `centroid_probe_k150`・`device_probe_d30` が，全ての実行で flood_score と `n_hit`・`contacted`・`wasted` において完全に一致する．
- G4: 全ての実行で，`n_hit` と (i)〜(iv) の件数の和が問の数に等しい．また，二段の方式の (iii) の内訳 2 区分の和が (iii) に等しい．

**判定**
- H-C2: 調査・計画の規則のとおりとする．相対的な低下は `churn_eval.judge_hc1` と同じ式（1 − hit(R=1000) / hit(R=1)）で求める．
- E-1: 件数で求める．種ごとの割合は「(ii) の件数の R = 1 → 1000 の増加 ÷ hit の件数の R = 1 → 1000 の減少」とする．時刻表は R に依らないので，正解が稼働中の問の数は R で変わらない．このため，この比は計画の「正解が稼働中の問に限った hit@2 の低下のうち (ii) の増分で説明される割合」に等しい（出力の `gold_online_same` で確かめる）．5 個の種の**すべて**で 0.8 以上なら「確かめた」とする．減少が 0 以下の種は不成立とする．種の和の比（Σ 増加 ÷ Σ 減少）は記録として出すだけで，判定には使わない（B41）．
- 記録（判定に使わない）: 二段の方式の (iii) を「候補に入らない」と「候補に入ったが上位 m 個に入らない」に分けた件数（B41），H-C3 の値．

**実装の検査**（commit `01774a2`）
- `tests/test_churn_cascade_eval.py` の 19 件は通った．全体の pytest は 377 件が通り，1 件が失敗した．ruff と mypy は通った．
- 失敗した `tests/test_node_workers.py::test_serve_workers_answers_from_every_process_and_stops_on_sigterm` は，プロセスの起動と SIGTERM を扱うテストで，今回の変更の対象外である．全体と単独の実行を合わせると 6 回中 2 回失敗しており，結果が不安定である．`4608ca5` でも同じかは確かめていない．

**限界の補足**（B40 (a) を読んだ結果．実装は変えていない）
- `config.py` の `CHURN_ROUTINGS` には centroid_probe・device_probe が入っていない．このため，今の `experiment.churn` では二段の方式を実機で再生できない．
- `requester.py` の `_probe` は `_gather_requests` を通るので，churn の設定を二段の方式に許せば，probe の失敗は構造上許される．
- 返事のあった候補が m 個より少ない問では，`select_among` が離脱した候補にも本照会を送る．hit には影響しない．

### 実装・実験（2026-10-16．research-cycle の phase 2．数値の規則の機械的な出力のみ．解釈・判定は phase 3）

- phase 2 の委託は 2 回中断した．1 回目は `Unknown tool: Bash` で Bash が使えずに止まった．2 回目は近接コードを読んだ後，実装の前で止まった．どちらもコードの変更・commit・実験は行っていない．
- 実装：commit `01774a2`（`src/atrium/churn_cascade_eval.py`，`tests/test_churn_cascade_eval.py`，`scripts/remote/churn_cascade_eval.sh`，`cli.py` の `churn-cascade-eval`，README・d0001・d0003・d0004）．事前登録：commit `ad986d6`．結果：commit `6a7cbd8`（`results/20261016_iter15_churn_cascade_eval/`．`metrics.json`・`analysis_report.md`・`run.log`）．
- 検査：`tests/test_churn_cascade_eval.py` の 19 件は通過した．全体の pytest は 377 件が通過し，1 件が失敗した．ruff・mypy は通過した．失敗した `tests/test_node_workers.py::test_serve_workers_answers_from_every_process_and_stops_on_sigterm` は今回の変更の対象外で，6 回中 2 回失敗する不安定なテストである（`4608ca5` での挙動は未確認）．
- 実行：制御点で連続実行が動いていないこと（`NO_CHAIN`）を確かめてから publish した（243 秒．イメージの版は `ad986d6a2ac3-dirty`．dirty は `.claude/` の未コミットの変更と未追跡のファイルによる）．`remote.sh churn_cascade_eval` は終了コード 0，pull を含めて 302 秒，評価の本体は 14.6 秒である．`run.log` の警告は faiss の k-means の「学習点が少ない」の警告で，EQ7a と同じ関数による（G2 は完全に一致した）．
- 退行ゲート：全て通過した．
  - G1：4 構成の差の最大は全て 0.0（許容 5e-4）．
  - G2：160 件を照合し，欠けは 0 件．hit・contacted・wasted の差の最大は全て 0.0（許容 1/2940 = 0.00034）．
  - G3：`centroid_probe_k150`・`device_probe_d30` の 160 件で，flood_score との不一致は 0 件．
  - G4：違反は 0 件．
- H-C2（a = 0.7，R = 1 → 1000 の hit@2 の相対的な低下）：機械的な判定は「支持」．

| 種 | device_probe_d12 | multi_centroid | 差 |
|---|---|---|---|
| 0 | 0.2972 | 0.3145 | −0.0172 |
| 1 | 0.2957 | 0.3211 | −0.0254 |
| 2 | 0.2461 | 0.2622 | −0.0161 |
| 3 | 0.2346 | 0.2546 | −0.0199 |
| 4 | 0.2661 | 0.2784 | −0.0123 |

- H-C3（記録）：flood_score は 0.2980，0.3099，0.2398，0.2134，0.2548．centroid_probe_k75 は 0.2924，0.3061，0.2428，0.2246，0.2567．centroid_probe_k30 は 0.3020，0.3093，0.2360，0.2277，0.2643．device_probe_d24 は 0.2972，0.3099，0.2389，0.2170，0.2548．
- E-1（flood_score，a = 0.7，R = 1 → 1000）：機械的な判定は「確かめた」．種の和の比は 1.2993（記録）．全ての種で `gold_online_same` は真である．割合は全ての種で 1 を超えている．

| 種 | hit の減少（問） | (ii) の増加（問） | 割合 |
|---|---|---|---|
| 0 | 514 | 653 | 1.2704 |
| 1 | 528 | 689 | 1.3049 |
| 2 | 410 | 513 | 1.2512 |
| 3 | 364 | 497 | 1.3654 |
| 4 | 429 | 565 | 1.3170 |

- hit を失った問の分解（a = 0.7，種の和，14,700 問．順に hit，(i)，(ii)，(iii)〔候補外／上位 m 外〕，(iv)）
  - R = 1：multi_centroid 4911，4398，0，5391，0．flood_score 8529，4398，0，1773，0．centroid_probe_k75 8326，4398，0，1976〔430／1546〕，0．centroid_probe_k30 7571，4398，0，2731〔1850／881〕，0．device_probe_d24 8524，4398，0，1778〔9／1769〕，0．device_probe_d12 7769，4398，0，2533〔1348／1185〕，0．
  - R = 1000：multi_centroid 3505，4398，2917，3635，245．flood_score 6284，4398，2917，1101，0．centroid_probe_k75 6123，4398，2917，1262〔318／944〕，0．centroid_probe_k30 5542，4398，2917，1843〔1322／521〕，0．device_probe_d24 6277，4398，2917，1108〔18／1090〕，0．device_probe_d12 5688，4398，2917，1697〔991／706〕，0．
- 1 問あたりの問い合わせたノード／うち離脱していたノード（a = 0.7，種の平均）：R = 1000 で multi_centroid 1.94／0.55，flood_score 20.84／5.77，centroid_probe_k75 20.77／5.75，centroid_probe_k30 16.90／4.68，device_probe_d24 20.63／5.70，device_probe_d12 12.00／3.33．

### Iteration 15 実行済み：分析・考察（2026-10-16．research-cycle の phase 3．判定：採用）

- 注記: rc-evaluator は分析を終えたが，ツールの呼び出しが失敗してファイルへ書き込めなかった．この節は，オーケストレータが rc-evaluator の戻り値を書き写したものである．rc-evaluator が未確認とした 2 点（E-1 の遷移の論法の前提，(iv) の反実仮想）は，オーケストレータがコードで確かめて反映した．

**変更**：新しいオフラインの CLI `atrium churn-cascade-eval` を足した（commit `01774a2`）．事前登録は `ad986d6`，結果は `6a7cbd8`（`results/20261016_iter15_churn_cascade_eval/`）．config.yaml・データ・シャード・既存の routing の挙動は変えていない．事前登録の規則・閾値は，結果を見て変えていない．

**正しさの確認**：G1〜G4 は全て通過した．G1・G2 の差の最大は全て 0.0（G2 は 160 件），G3 の不一致は 0 件，G4 の違反は 0 件である．

**結果と判定**
- H-C2（主）：機械的な判定は「支持」．ただし，差は成功条件で「差として読まない」とした大きさである（事実 1）．
- E-1：機械的な判定は「確かめた」．ただし，比は「割合」としては読めない（事実 4）．主因であることは，問ごとの遷移の形で別に確かめた（事実 5）．
- **このイテレーションの結論：採用**．成功条件（G1〜G4 を通過し，H-C2・E-1 を機械的に判定して記録する）を満たした．評価のためのレバーなので，支持・不支持によらず採用とする規則である．

**分析**

事実（記録とコードから確かめたもの）
1. **H-C2 の差は，向きは 5 種でそろうが，種の間の揺れより小さい**．種ごとの差（dp12 − multi_centroid）は −0.0172・−0.0254・−0.0161・−0.0199・−0.0123（平均 −0.0182）である．種の間の相対的な低下の幅（最大 − 最小）は，multi_centroid 0.067，dp12 0.063，flood_score 0.097 で，差の 3〜5 倍ある．成功条件の「EQ7a の種の間の幅（約 ±0.05）より小さい差は差として読まない」に当たる．このため，支持は「向きが 5 種でそろった」という意味に限る（EQ7a の H-C1 と同じ読み方）．
2. **二段の方式の低下は flood_score と同じ程度である**．種の和の相対的な低下（1 − Σhit(R=1000) ÷ Σhit(R=1)）は次のとおり．flood_score 0.2632，device_probe_d24 0.2636，centroid_probe_k75 0.2646，device_probe_d12 0.2679，centroid_probe_k30 0.2680，multi_centroid 0.2863．二段の方式は全て flood_score との差が 0.005 以内である．dp12 − flood_score は種ごとに −0.014〜+0.021 で，符号がそろわない．
3. **仮説の仕組み（離脱済みの受信箱に枠を取られない）は働いているが，その効果は小さい**．二段の方式の (iv) は，全ての実行で 0 件である．multi_centroid の (iv) は，R = 1 で 0 件，R = 1000 で 245 件だった．`single_stage_breakdown` の (iv) は「見えていて稼働中の受信箱の中で，正解の順位が m 以内」の問である．したがって，離脱済みの受信箱を候補から除けば，(iv) は定義により全て hit になる．このときの multi_centroid の低下は 1 − (3505 + 245) ÷ 4911 = 0.2364 で，0.2863 から 0.050 下がり，dp12 の 0.2679 を下回る．multi_centroid と二段の方式の差（約 0.02）は，(iv) で全て説明できる大きさである．
4. **E-1 の比が全ての種で 1 を超えたのは，比の定義による**．flood_score では，(i) は R によらず 4398 件，(iv) は 0 件である．このため，「(ii) の増加 ＝ hit の減少 ＋ (iii) の減少」が恒等的に成り立つ（種の和で 2917 ＝ 2245 ＋ 672）．比の 1 を超える分 0.2993 は「(iii) の減少 ÷ hit の減少」である．R = 1 で (iii) だった問が R = 1000 で (ii) に移ると，hit が減らなくても分子が増える．判定は書き換えない．
5. **問ごとの遷移で数えると，R = 1 の hit の損失は全て (ii) による**（コードで確かめた）．根拠は次の 3 点である．
   - `stale_view` は R = 1 では各問の時点の状態をそのまま返す．このため，R = 1 の見えている受信箱は，稼働中の受信箱と一致する．
   - flood_score の候補は「見えていて稼働中」の受信箱である（`route_under_churn`）．時刻表は R によらないので，R = 1000 の候補は R = 1 の候補の部分集合になる．候補が減るので，正解の順位の数字は大きくならない．
   - このため，「R = 1 で hit → R = 1000 で (iii)」の移りは起きない．(i) の問も R で変わらない．
   
   問の数を次のように置く．x は hit → (ii)，y は (iii) → (ii)，z は (iii) → hit である．このとき x − z = 2245，x + y = 2917，y + z = 672 となり，x は 2245〜2917（z は 0〜672）である．EQ7a の推測（表の更新の後に参加したノードが見えないことが原因）は，flood_score について確かめられた．
6. **稼働中の正解が見えない割合は，全ての方式の低下とほぼ同じ大きさである**．正解が稼働中の問は 14,700 − 4,398 = 10,302 問で，そのうち R = 1000 で見えない問の割合は 2917 ÷ 10302 = 0.283 である．これは，全ての方式の相対的な低下（0.263〜0.286）とほぼ同じ大きさである．
7. **問い合わせたノードのうち，離脱していたノードの割合は，どの方式も約 0.28 である**（R = 1000．multi_centroid 0.55 ÷ 1.94，flood_score 5.77 ÷ 20.84，dp12 3.33 ÷ 12.00）．二段の方式が減らすのは，問い合わせる数そのもの（dp12 は flood_score の約 0.58 倍）であって，無駄な問い合わせの割合は減らさない．

推測（上の事実と矛盾しないが，未確認のもの）
- hit の低下の大部分は，方式によらない「稼働中の正解が見えない確率」で決まると読める．方式の差は，その上の小さな加減に当たる．事実 6 は全体の数の一致で，見えなさと各方式の hit が独立かどうかは確かめていない．

**想定外の挙動**：E-1 の比が全ての種で 1 を超えた（事実 4．比の定義による）．判定には影響しなかった．言語崩れ・発散・OOM は該当しない（オフラインの評価のため）．

**学び**
- メンバー表が古いときの hit の低下は，どの方式でもほぼ「稼働中の正解が見えない確率」で決まる（a = 0.7，R = 1000 で 0.283）．二段の方式は枠を取られない（(iv) = 0）が，その差は種の間の揺れより小さい．頑健さを上げるには，選び方よりも見え方（参加の伝わり方）を変える必要がある．
- 「(ii) の増加 ÷ hit の純減」の形の比は，ほかの区分から (ii) への移りを含むので，1 を超えうる．損失の原因の割合を事前登録するときは，R = 1 の hit に限った問ごとの遷移で数える規則にする．

**限界**
- 実機の `_tolerant` が二段の方式の probe の失敗にも効くかは，確かめていない．`CHURN_ROUTINGS` に centroid_probe・device_probe が無く，今の設定の検査では併用できない．
- 実機の `select_among` は，返事のあった候補が m 個より少ない問では，離脱した候補にも本照会を送る．今回の wasted は probe だけを数えたので，これを含まない（事前登録の規則どおり）．
- 二段の方式の離脱下の実機での再生は行っていない．
- 事実 5 の x は，件数の連立から範囲で求めた値である．問ごとの遷移は出力していない．
- 判定は a = 0.7 の 1 点による．
- 不安定なテスト（`tests/test_node_workers.py::test_serve_workers_answers_from_every_process_and_stops_on_sigterm`．6 回中 2 回失敗）は，今回の変更の対象外として別件にした（backlog B44）．

**次の一手**（backlog B43 [auto-decided]）：参加を通知する形のメンバー表の変種を，オフラインで評価する（B40 の A5）．レバーの名前と，遅れ L などの規則は，次のセッションの rc-researcher が決める．

## Iteration 14: 質問者の検索要求の本文を問ごとに 1 回だけ符号化したときの処理量

### 調査・計画（2026-10-16．research-cycle の phase 1）

**レバーの由来**：backlog B37 [auto-decided]（Iteration 13 は採用）．config.yml の levers 末尾の `requester.retrieve_body: [pre_encoded]`（仮称）を，**config.yaml の新しいキー `experiment.retrieve_body`（`per_request`〔既定．今の挙動〕/ `pre_encoded`）**として確定した（backlog B38）．
依存（uv.lock・イメージの版）・ノードの API・ノードが受け取る本文（バイト単位）・方式の選択・データ・シャード・`cluster`・`node_workers` は変えない．

**事前の測定の方法**（B37 の指示．判定に使わない．スクリプトと出力は `results/20261016_iter14_retrieve_body_summary/prestudy/`）
- Iteration 13 の `prestudy/run_ctrl.sh` と同じ手順：制御点 wafl-ctrl5 の `atrium-full:latest`（2026-10-15 23:48 の build．`apply_pool_assign` を含む）のコンテナ（`--network host`，`/data` は読み取りだけ）で，本物の `atrium node` 30 個（シャードは `/tmp/iter13_prestudy/shards` を読み取りだけで使い回した）と本物の `atrium run`（質問者 1 プロセス）を流した．
  条件は Iteration 13 の事前の測定に **`pool_assign: lean`** を足したもの（600 問，parallel 30，`per_node`・`pre_encoded`・`blocked`，fs と dp12）．作業場所は制御点の `/tmp/iter14_prestudy/`（75 MB．データディレクトリではない）．
- 変種（`run_one.py` の monkeypatch．本番のコードは変えていない）：**cur**（今の最良の構成），**rbody**（retrieve の本文の埋め込みの JSON を配列ごとに 1 回だけ作り，`shard_id`・`k`・`query_id` を差し替えた bytes を `content=` で送る），**rshared**（rbody に加えて probe の pre_encoded の本文と同じ埋め込みの JSON を共有する．問ごとの埋め込みの符号化は 1 回）．
  rbody・rshared は最初の 200 本の本文を `RetrieveRequest(...).model_dump()` の json= の本文とバイト単位で比べ（違えば止める），全て一致した．600 問の `selected_sources`・`top_doc_ids` も cur と 600/600 一致した（`compare_selected.py`）．
- 各変種 3 回（同じ回の中で cur → rbody → rshared を fs・dp12 の順に交互），その後に cur・rshared の py-spy（250 Hz，raw）を 1 回ずつ取った．項目の割り当ては Iteration 13 の `analyze_spy.py` に「retrieve の本文の組み立て」「probe の本文の組み立て」の項目を足したもの（`ctrl5/spy_summary.txt`）．

**事前の測定で分かったこと**（事実）

| 変種（3 回．600 問．失敗 0） | fs の 1 要求の CPU（ms） | 中央値 | fs の問/秒の中央値 | dp12 の 1 要求の CPU（ms） | 中央値 | dp12 の問/秒の中央値 |
|---|---|---|---|---|---|---|
| cur（lean の上の今の最良） | 1.347・1.323・1.325 | 1.325 | 23.10 | 1.490・1.479・1.478 | 1.479 | 47.65 |
| rbody（retrieve だけ使い回す） | 1.312・1.307・1.320 | 1.312 | 23.33 | 1.491・1.437・1.442 | 1.442 | 48.82 |
| **rshared（probe と共有）** | 1.306・1.289・1.330 | **1.306** | **23.53** | 1.398・1.446・1.393 | **1.398** | **50.29** |

- rshared の節約は **dp12 で 0.081 ms（−5.5%．問/秒の比 1.055）**，fs で 0.019 ms（−1.4%．比 1.019）．同じ回の組の比（rshared ÷ cur の問/秒）は dp12 で 1.064・1.011・1.061，fs で 1.035・1.028・0.999 だった．fs の範囲は cur と重なる．rbody は rshared の約半分〜7 割の節約である（probe と共有しない分，問ごとに 1 回多く符号化する）．
- py-spy の主スレッドの標本（cur）で，retrieve の本文の組み立て（`RetrieveRequest` の検証・`tolist`・`model_dump`・httpx の `json=`）は **dp12 7.6%・fs 4.3%**，rshared で dp12 1.5%・fs 0.7% に下がった．probe の本文の組み立ては両方とも 0.7〜0.9% で変わらない．
- lean の上の cur の他の項目（dp12 / fs）：httpx の要求・応答の組み立て 33.0% / 36.5%（大半は `handle_async_request` の下の httpcore・h11・anyio の送受信），anyio 15.5% / 20.8%，h11 9.3% / 11.1%，割り当て（lean）9.3% / 12.3%，**routing（1 段目の `multi_centroid_score`）8.3%** / 0.3%，記録ほか 7.6% / 7.7%，logging 3.1% / 3.4%．
  httpx の `build_request` の下の URL の解析（`_merge_url`・`urlparse`）は dp12 で約 4%．
- **設定キーやコードの差し替えで挙動を変えずに除ける項目の中で，fs・dp12 の両方に効くのは retrieve の本文が最大**（dp12 では routing の 1 段目 8.3% と同じ程度で，routing は dp12 にしか効かない）．routing の 1 段目のベクトル化は浮動小数の和の順が変わり，選択の同一性をバイト単位では保証できない．B37 の選択を変えない．

**調査で分かったこと**（tavily-search）
- 同じ本文を複数の宛先へ送るときに符号化を 1 回にまとめる作りは，gRPC の Go の実装に `PreparedMsg`（v1.21.0 で追加，EXPERIMENTAL．「Marshalled and Compressed object」を作って複数のストリームへ送る）としてある（https://pkg.go.dev/google.golang.org/grpc ．2026-10-16 に確認）．Iteration 11 の `probe_body` と同じ考え方である．
- orjson は json.dumps より速いが，戻り値が bytes で，浮動小数の表記（例：`1e-05` と `1e-5`）が標準の json と違いうる（https://github.com/ijl/orjson ，α-Lyrae「Beware of Performance Pitfalls in Third-Party Python JSON Libraries」https://chr.fan/en/python-json ）．**ノードが受け取る本文をバイト単位で同じに保つ条件と両立しない**ので使わない（表記の差の実例は確かめていない．推測）．

**問い**：(1) retrieve の本文を問ごとに 1 回だけ（probe と共有して）符号化すると，lean の上で実機の dp12 × N = 1 の処理量は見込み（約 1.04 倍）どおり伸びるか．(2) 3 回の範囲の規則では検出できない大きさの効果を，交互に並べた組の比で検出できるか．

**検出の見込み（B37 (a)）**
- 見込み：実機への縮小の率（dp12 は 2 回続けて約 0.6，fs は約 0.8．Iteration 13 の学び）を掛けると，dp12 は 0.081 × 0.6 ≈ 0.049 ms の節約で 1 要求 約 1.18 → 1.13 ms，**処理量の比 約 1.04**．fs は 0.019 × 0.8 ≈ 0.015 ms で **比 約 1.015**．
- Iteration 13 の 3 回の範囲は，dp12 × N = 1 で sk が中央値の −3.2%〜+0.4%，ln が −2.9%〜+2.4%（幅 4〜5%）で，**比 1.04 の差は「範囲が重ならない」規則では検出できない見込み**である．
- 一方で，同じ回の中で隣り合う 2 実行の比（組の比）は Iteration 13 の dp12 × N = 1 で 1.090・1.111・1.093（中央値からの −0.3%〜+1.9%）と揃っていた（連続実行の中の時間の揺れが両方に掛かるため）．fs × N = 1 は 1.161・1.230・1.147（sk の r2 が低い）だった．
- そこで **判定は組の比で行い，主の対象を dp12 × N = 1 に，N = 1 の回数を 5 回に増やす**．5 組の全てで比 > 1 となる確率は，差が無ければ 1/32（片側の符号検定 p = 0.031）．fs × N = 1 は見込みが組の比の揺れ（±1〜4%）より小さいので主にしない．

**仮説**
- **H-R1（主）**：dp12 × N = 1 で，有効な組の全てで pe ÷ pr の処理量の比が 1 を超え，かつ組の比の中央値が 1.02 以上．仕組み：N = 1 は質問者のプロセスが 1 コアに張り付いて律速（Iteration 11〜13 の事実 1）なので，1 要求の質問者の CPU の比の逆数だけ処理量が伸びる．
- **H-R2**：fs × N = 1 で H-R1 と同じ規則が成り立つ（見込み 1.015 なので，成り立たない見込み）．
- **H-R3（記録）**：fs・dp12 × N = 8 の組の比（3 組）．

**単一レバー**：config.yaml の **`experiment.retrieve_body` を `per_request` → `pre_encoded`** と変える．それ以外（方式・D・N・parallel・問・`http_pool: per_node`・`probe_body: pre_encoded`・`sniffio_lookup: blocked`・`pool_assign: lean`・W = 1・依存）は両方の値で同じにする．
- `per_request`（既定）は今の挙動（`_retrieve` がシャードごとに `embedding.tolist()` → `RetrieveRequest` → `model_dump()` → httpx の `json=`）．probe の pre_encoded の埋め込みの JSON の作り方も今のまま．
- `pre_encoded` は，問ごと・検索器ごとに埋め込みの JSON を 1 回だけ作り（`probe_body: pre_encoded` のときは probe の本文と同じ bytes を共有する．事前の測定の rshared），faiss のシャードの retrieve の本文を `{"shard_id":…,"k":…,"embedding":<共有の bytes>,"query_id":…}` の連結で作って `content=` で送る．ノードが受け取る本文は `per_request` とバイト単位で同じ．faiss 以外（埋め込みを送らないシャード）は今の挙動のまま．
- **実装**（phase 2 で行う．事前登録の commit に含める）
  - `src/atrium/config.py`：`ExperimentConfig.retrieve_body: Literal["per_request", "pre_encoded"] = "per_request"`．`config.yaml` に `retrieve_body: per_request` とコメントを足す．
  - `src/atrium/requester.py`：問の処理（`_routing` の前後で `embeddings` を得た直後）で，`probe_body` か `retrieve_body` が `pre_encoded` のときだけ検索器ごとの埋め込みの JSON（`encode_json_like_httpx(embeddings[encoder].tolist())`）を 1 回だけ作る小さな表を作り，`_probe`（今の `encoded_embeddings` の作成をこの表に置き換える）と `_snippets` → `_retrieve` へ渡す．
    `retrieve_body: pre_encoded` かつ faiss のシャードなら，本文を bytes の連結で作り `post_with_retry` に bytes で渡す．`per_request` の経路は今のコードのまま（`retrieve_body: per_request` かつ `probe_body: pre_encoded` のとき，probe の埋め込みの符号化は今と同じ 1 回）．
  - `src/atrium/experiment.py`：`run_meta.json` に `retrieve_body` を残す．
  - テスト（`tests/test_retrieve_body.py`）：既定値が `per_request` であること．**本文のバイト単位の一致**：faiss のシャードで，`shard_id`（記号・非 ASCII を含む文字列）・`k`・`query_id`（文字列と None）・埋め込み（float32 の値，負の値，非常に小さい値〔指数表記になる値〕）を変えて，pre_encoded の bytes が `encode_json_like_httpx(RetrieveRequest(...).model_dump())` と一致すること．
    **全体**：localhost で動かした本物のノード（`create_app` を uvicorn で 1 つ）へ per_request と pre_encoded で retrieve を送り，応答（`docs`）が一致すること．probe と共有したとき（`probe_body: pre_encoded`）にも probe の本文が今と同じ bytes であること．faiss 以外のシャードでは本文が今と同じであること．
  - 文書：`docs/d0002_configuration.md`（`retrieve_body`），`docs/d0004_metrics.md`（`run_meta.json` の `retrieve_body`）．
  - 連続実行：`results/20261016_iter14_retrieve_body_summary/` に `iter14_chain.sh`（`iter13_chain.sh` を写し，`retrieve_body` を `experiment` の直下のキーとして渡す）・`iter14_specs.template.txt`・`iter14_smoke_specs.template.txt`・集計 `iter14_summary.py`（下の規則を機械的に出す．合成の結果で動作を確かめる）．

**固定する構成**：`dataset: enronqa`，`answer_mode: retrieval_only`，m = 2，`http_pool: per_node`，**`probe_body: pre_encoded`**，**`sniffio_lookup: blocked`**，**`pool_assign: lean`**（Iteration 11〜13 の採用の構成），parallel 30（全プロセスの合計），test の全 2,940 問（`question_limit: null`），**`node_workers: 1`**，T4 以来の 30 台 × 5 受信箱の配置，各回の前に deploy し直す．
連続実行の間は制御点で他のコンテナ（事前の測定の `iter14-prestudy` を含む．削除済み）を動かさず commit もしない．依存は変えない．

### 事前登録（2026-10-16．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 0：実機の動作確認**（連続実行の前に 1 回．判定に使わない）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．制御点に `iter14-prestudy` のコンテナが残っていないことを確かめる．
2. `<date>_iter14_smoke_dp12_pe_n1`（`dataset=enronqa answer_mode=retrieval_only question_limit=2 http_pool=per_node probe_body=pre_encoded sniffio_lookup=blocked pool_assign=lean retrieve_body=pre_encoded routing=device_probe routing.probe_d.enronqa=12 parallel=30 requester_procs=1`）を deploy → start → analyze で 1 回流す．続けて同じ条件で `requester_procs=8` を 1 回流す．
3. 次を全て満たすことを確かめる．満たさなければ連続実行を流さず，原因を調べて記録する：失敗 0 問，300 問の `selected_sources` と `top_doc_ids` が Iteration 13 の `dp12_n1_ln_r1` の同じ問と全て一致，`run_meta.json` の `retrieve_body` = pre_encoded・`pool_assign` = lean・`probe_body` = pre_encoded・`sniffio_lookup` = blocked・`http_pool` = per_node・`requester_procs` が仕様どおり，`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない．
   N = 1 の 1 要求あたりの質問者の CPU が Iteration 13 の dp12 × N = 1 × ln（中央値 1.178 ms）以下か（記録．300 問なので判定に使わない）．

**手順 1：実機の連続実行**（`iter14_specs.template.txt` の `<date>` を実行日にした `iter14_specs.txt` を `iter14_chain.sh` に**絶対パス**で渡す）
1. 環境の揺れの確認（前）：flood_score × N = 1 × parallel 30 × 300 問（`question_limit=2`）× `probe_body: per_request` × `sniffio_lookup: search` × `pool_assign: stock` × `retrieve_body: per_request` を 1 回（anchor_pre．Iteration 7〜13 の anchor と同じ条件）．
2. 主の系列：全て `probe_body: pre_encoded`・`sniffio_lookup: blocked`・`pool_assign: lean`，2,940 問．`retrieve_body` ∈ {per_request（pr），pre_encoded（pe）}．
   - 回 r = 1〜3：fs_n1 → fs_n8 → dp12_n1 → dp12_n8 の順に流す．
   - 回 r = 4〜5：fs_n1 → dp12_n1 だけを流す（N = 1 の組を 5 組にするため）．
   - 各組（同じ回・同じ方式・同じ N の pr と pe）は奇数の回で pr → pe，偶数の回で pe → pr の順にする．
3. 環境の揺れの確認（後）：1 と同じ（anchor_post）．
- 計 34 実行（手順 0 を除く．N = 1 が 20，N = 8 が 12，anchor が 2）．所要は約 3 時間の見込み．deploy の失敗の扱い・失敗率の打ち切りは Iteration 13 と同じ（`iter14_chain.sh`）．

**指標**（Iteration 13 と同じ．`docs/d0004_metrics.md`）：処理量（成功した問の数 ÷（`finished_at` − `started_at`）），e2e_s・probe_s の p50・p95，失敗率，再試行の数（例外の型ごと），1 秒あたりの HTTP 要求の数，`requester_cpu_util`・`requester_cpu_util_per_proc` の中央値，`expert_cpu_util_max`（と最も忙しいノード），`control_cpu_busy`，2,940 問の hit@2，1 要求あたりの質問者の CPU（ms），`requester_tcp`，遅れの問の数（N = 8 だけ `probe_s` ≥ 0.9 秒，全ての実行で相対の閾値〔中央値の 5 倍〕）．
**主の量**：各組の **組の比 q = pe の処理量 ÷ pr の処理量**．

**正しさの確認**（満たさない実行は判定に使わず「欠け」とし，原因を調べて記録する）
- G1：失敗率が 1% 以下．
- G2（方式ごと）：その方式の全ての実行（pr・pe・N = 1・8）で，失敗の無い問の `selected_sources` と `top_doc_ids` が問ごとに 100% 一致する．成り立たなければその方式の判定に進まない（ノードが受け取る本文は同じなので変わらないはず）．
- G3：`run_meta.json` の `retrieve_body`・`pool_assign` = lean・`httpcore_version` = 1.0.9・`sniffio_lookup` = blocked・`sniffio_installed` = false・`probe_body` = pre_encoded・`requester_procs`・`http_pool` = per_node・routing・D・`n_questions` が仕様と一致する．
- G4（記録）：2,940 問の hit@2 が Iteration 8・11〜13 の値（fs 0.8041，dp12 0.6830）と一致するか．
- G5（律速の読みだけに効く）：`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない．
- **G6（N = 1 の組の有効性）**：N = 1 の組は，pr・pe の両方が G1・G3 を満たし，かつ両方の `requester_cpu_util_per_proc` の中央値が 0.95 以上のときだけ有効とする（N = 1 の比が 1 要求の CPU の比の逆数になるのは質問者が張り付いているときだけ．Iteration 13 の dp12 × N = 8 × ln の r1 は遅れで 0.673 だった）．無効な組は数えて記録する．

**判定の規則**
- **伸びた**（ある方式・N で pr → pe）：有効な組が 4 組以上あり，**有効な組の全てで q > 1.000**，かつ **q の中央値が 1.02 以上**．
- **H-R1（主）**：dp12 × N = 1 が「伸びた」．
- **H-R2**：fs × N = 1 が「伸びた」．
- **H-R3（記録）**：fs × N = 8・dp12 × N = 8 の各 3 組の q（全て > 1 か，中央値）．N = 8 の組は G6 を当てず，遅れの問の数を併記する．
- **このイテレーションの結論**：
  - **棄却（測定不成立）**：G2 が dp12 で成り立たないか，G3 が 1 つでも不成立か，dp12 × N = 1 の有効な組が 4 組未満．原因を調べて記録する．
  - **採用**（以後の負荷の実験は `retrieve_body: pre_encoded` を明示して流す．既定の値は `per_request` のまま）：上が当てはまらず，H-R1 が成り立つ．
  - **部分的に支持**：H-R1 が成り立たないが，dp12 × N = 1 の有効な組の全てで q > 1.000（中央値 < 1.02），または dp12 × N = 1 の有効な組の全てで 1 要求の質問者の CPU が pe で小さい，または H-R2 が成り立つ．
  - **棄却（伸びない）**：どれでもない．
- 記録（判定に使わない）：**Iteration 13 と同じ範囲の規則**（3 回〔N = 1 は 5 回〕の範囲が重ならず中央値の比 ≥ 1.10．効果の大きさから成り立たない見込み）の出力，方式・N ごとの 1 要求あたりの質問者の CPU の pr と pe の差（事前の測定の dp12 0.081・fs 0.019 ms と，縮小の見込み dp12 約 0.049・fs 約 0.015 ms と比べる），組ごとの CPU の比，N = 8 の E(8)，`requester_cpu_util_per_proc`，新しい接続の数，`requester_tcp`．
  律速の移りの読み（Iteration 12・13 と同じ）：dp12 × N = 8 × pe で `requester_cpu_util_per_proc` の中央値が 0.9 未満かつ最も忙しいノードの `expert_cpu_util` が 1.58 コア以上なら「律速が専門家ノードへ移った」と記録する．遅れの問の数が 10 問以上の実行はこの読みに使わない．

**環境の揺れの規則**（記録のため）：anchor_pre・anchor_post の処理量が [11.127, 14.247] 問/秒，e2e p95 が [3.053, 4.067] 秒（Iteration 7〜13 と同じ範囲）に入るかを記録する．pr の主の系列の中央値が Iteration 13 の ln の同じ条件の範囲（fs N = 1：30.091〜30.714，fs N = 8：171.171〜171.672，dp12 N = 1：59.378〜62.591，dp12 N = 8：198.503〜307.596〔遅れの無い r2・r3 は 305.855〜307.596〕問/秒）から ±5% を超えて外れたら「環境が変わった」と記録する．比べる相手は同じ回の pr なので，判定の基準は変えない．

**ノイズの見積もりと閾値の根拠**：組の比の揺れは Iteration 13 の dp12 × N = 1 で中央値から −0.3%〜+1.9%，fs × N = 1 で −1.2%〜+5.9%，事前の測定の dp12 で 1.011〜1.064 だった．閾値 1.02 は dp12 の見込み（約 1.04）の約半分で，dp12 の組の比の揺れの幅（約 2%）と同じ程度である．
「全ての組で q > 1」は差が無いときに 5 組で 1/32，4 組で 1/16 の確率で起きる（片側の符号検定）．中央値の閾値と併せて，揺れだけで「伸びた」となる見込みは小さい．Iteration 13 までの「範囲が重ならず比 ≥ 1.10」より緩いので，**採用の判定は「質問者の CPU を削る系列の小さな効果の検出」に限った規則**として読む．

**期待効果**（推測．判定に使わない）：dp12 × N = 1 は 1 要求 約 1.18 → 約 1.13 ms，処理量 約 61 → 約 63.5 問/秒（比 約 1.04）．fs × N = 1 は 約 1.03 → 約 1.02 ms，比 約 1.015．N = 8 は N = 1 の比より小さい見込み（Iteration 12・13 の事実 3）．律速は質問者のまま（dp12 × N = 8 の最も忙しいノードは 約 1.4 コアで閾値 1.58 に届かない見込み）．

**記録に足さないもの**（B37 (c)）：dp12 × N = 8 の約 1 秒の遅れと SYN の再送の時刻の突き合わせ（1 秒ごとの `nstat`・専門家ノードの `ListenOverflows`）は，ノードのコードか連続実行の仕組みの変更が要り，このレバーと独立なので今回は足さない．`requester_tcp` は今のまま記録する．

**限界**：有線 LAN・検索だけ・閉じた系・制御点 1 台の質問者．事前の測定はノードを同じ 12 コアで動かし，ループバックで測ったもので，縮小の率（dp12 約 0.6）も 2 回の観測からの推定である．判定の規則を組の比に変えたので，Iteration 11〜13 の判定と規則の強さが違う．

**次の候補**：採用でも棄却でも，lean・pre_encoded の上の残りで挙動を変えずに除ける項目は dp12 の routing の 1 段目（約 8%．選択の同一性の確かめが要る）と httpx の URL の解析（約 4%）程度で，どれも単独では Iteration 14 と同じ程度以下の効果の見込みである．
B37 (b) のとおり，その先は (i) HTTP クライアントの置き換え，(ii) 質問者を複数のデバイスに置く（B25 (a)．人間の判断），(iii) 負荷の系列を止めて RQ の研究へ戻る，のいずれかで，どれを選ぶかは人間の確認を求める．

### 実装・実験（2026-10-16．research-cycle の phase 2．数値の規則の機械的な出力のみ．解釈・判定は phase 3）

**実装**（commit c20aa0a．push 済み）：`experiment.retrieve_body`（`per_request` 既定／`pre_encoded`）を `config.py`・`config.yaml`・`requester.py`（`_encode_embeddings` を新設し，問ごとに 1 回作った埋め込みの JSON を `_probe` と `_retrieve` で共有．faiss のシャードだけ bytes の連結で本文を作り `content=` で送る）・`experiment.py`（`run_meta.json` の `retrieve_body`）・`docs/d0002_configuration.md`・`docs/d0004_metrics.md` に追加．テスト `tests/test_retrieve_body.py`（22 件）．pytest 359 件 passed，ruff・mypy 通過．
テストの「本物のノード」は，uvicorn ではなく既存のテストと同じ ASGI アプリ（`create_app`）の直接呼び出しで行った（`tests/test_requester.py` の構成）．

**手順 0（動作確認．判定に使わない）**：docker ok，`iter14-prestudy` のコンテナ無し．`smoke_dp12_pe_n1`・`smoke_dp12_pe_n8`（300 問）：失敗 0，300 問の `selected_sources`・`top_doc_ids` が Iteration 13 の `dp12_n1_ln_r1` と 300/300 一致，`run_meta.json` の項目は仕様どおり，`expert_cpu_util` 30 台，`control_cpu_busy` あり．N = 1 の 1 要求あたりの質問者の CPU は 1.134 ms（Iteration 13 の dp12 × N = 1 × ln の中央値 1.178 ms 以下）．

**連続実行**：34 実行を `iter14_chain.sh` で完了（2026-10-16 02:32〜04:59．deploy の失敗・再試行・失敗率による飛ばし無し）．ログ・集計は `results/20261016_iter14_retrieve_body_summary/`（`iter14_chain.log`・`iter14_summary.out`）．各実行は `results/20261016_iter14_*`．

**機械的な出力の要点**（全文は `iter14_summary.out`）
- G1：全 34 実行で失敗率 0．G2：fs・dp12 とも全 16 実行で `selected_sources`・`top_doc_ids` が 100% 一致．G3：不成立・欠け 0 件．G4：fs 0.8041・dp12 0.6830 で一致．G5：全実行で 30 台・`control_cpu_busy` あり．
- G6：N = 1 の組は fs・dp12 とも 5／5 が有効（per_proc は全て 0.975 以上）．
- 環境：anchor_pre 13.126 問/秒・p95 3.451 秒，anchor_post 12.332 問/秒・p95 3.944 秒（どちらも範囲内）．pr の中央値は Iteration 13 の ln の範囲の ±5% 内（4 条件とも）．
- 組の比 q（pe ÷ pr の処理量）：
  - fs × N = 1：1.049・1.043・1.022・1.071・1.037（中央値 1.043．全て > 1.000）．pr 中央値 30.429，pe 中央値 31.455 問/秒．
  - dp12 × N = 1：1.118・1.078・1.096・1.032・1.080（中央値 1.080．全て > 1.000）．pr 中央値 61.631，pe 中央値 67.047 問/秒．
  - fs × N = 8（記録）：1.013・1.050・0.911（pe の r3 は遅れの問 24，per_proc 0.848）．中央値 1.013．
  - dp12 × N = 8（記録）：1.049・1.196・0.800（pr の r2 は遅れの問 9，per_proc 0.883．pe の r3 は遅れの問 4，per_proc 0.762）．中央値 1.049．
- 1 要求あたりの質問者の CPU の差 pr − pe（中央値の差）：fs × N = 1 0.036 ms，dp12 × N = 1 0.093 ms，fs × N = 8 0.062 ms，dp12 × N = 8 0.148 ms（事前の測定 fs 0.019・dp12 0.081，縮めた見込み fs 約 0.015・dp12 約 0.049）．
- 規則の機械的な適用：H-R1（dp12 × N = 1）True，H-R2（fs × N = 1）True．結論の欄の出力は「採用」．Iteration 13 と同じ範囲の規則（記録）は，fs × N = 1 で範囲は pe が上・中央値の比 1.034，dp12 × N = 1 で範囲は pe が上・比 1.088（どちらも 1.10 未満）．
- 律速の移りの読み（dp12 × N = 8 × pe）：r1 per_proc 0.994・expert_max 1.497，r2 0.990・1.491，r3 0.762・1.154．いずれも「移った」は False．
- 記録：TCP の再送の大きな値（`TCPTimeouts` 224〜801）が pr・pe の両方の複数の実行で出ている（例：fs_n1_pe_r3，fs_n1_pr_r4，dp12_n8_pe_r3）．再試行は 0〜9 件．

**異常**：なし（連続実行は中断・欠け無し）．N = 8 の一部の実行に遅れの問（最大 24 問）と per_proc の低下があり，q の範囲が広い（0.800〜1.196）．

### Iteration 14 実行済み：分析・考察（2026-10-16．research-cycle の phase 3．判定：採用）

**変更**：config.yaml の `experiment.retrieve_body` を `per_request`（pr）→ `pre_encoded`（pe）と変え，`probe_body: pre_encoded`・`sniffio_lookup: blocked`・`pool_assign: lean` を固定して，fs・dp12 × N = 1 × 5 組，N = 8 × 3 組（2,940 問，per_node，parallel 30，W = 1，検索だけ）を pr と pe を組ごとに交互に並べて流した（前後の anchor を含めて 34 実行．欠け 0）．事前登録の規則・閾値は結果を見て変えていない．

**正しさの確認と環境の揺れ**：G1（34 実行とも失敗 0）・G2（fs・dp12 とも 16 実行の `selected_sources`・`top_doc_ids` が全問一致）・G3（不成立 0 件）・G5 は成立し，G4（hit@2）は fs 0.8041・dp12 0.6830 で Iteration 8・11〜13 と同じ．G6 は N = 1 の 10 組の全てで有効（per_proc は全て 0.975 以上）．
前後の anchor は 13.126・12.332 問/秒（e2e p95 3.451・3.944 秒）で事前登録の範囲の内．pr の中央値は 4 条件とも Iteration 13 の ln の範囲の ±5% の内（「環境が変わった」は無し）．

**結果と判定**（組の比 q = pe ÷ pr の処理量．処理量は問/秒の最小〜中央値〜最大）

| 条件 | 組の比 q（r1〜r5） | q の中央値 | pr | pe | 1 要求の質問者の CPU（ms．pr → pe．中央値） | 組ごとの CPU の比 pr ÷ pe |
|---|---|---|---|---|---|---|
| dp12 N = 1（主） | 1.118・1.078・1.096・1.032・1.080 | **1.080** | 60.276〜61.631〜62.216 | 63.665〜67.047〜67.555 | 1.168 → 1.074（−0.093．−8.0%） | 1.118・1.075・1.096・1.070・1.079 |
| fs N = 1 | 1.049・1.043・1.022・1.071・1.037 | **1.043** | 29.365〜30.429〜30.500 | 31.086〜31.455〜31.940 | 1.031 → 0.995（−0.036．−3.5%） | 1.048・1.044・1.034・1.068・1.041 |
| dp12 N = 8（記録） | 1.049・1.196・0.800 | 1.049 | 270.179〜310.376〜311.156 | 248.338〜323.115〜326.336 | 1.787 → 1.639（−0.148．−8.3%） | 1.086・1.084・1.093 |
| fs N = 8（記録） | 1.013・1.050・0.911 | 1.013 | 167.960〜171.323〜173.660 | 153.037〜175.940〜179.828 | 1.399 → 1.337（−0.062．−4.4%） | 1.049・1.045・1.051 |

- 規則の機械的な出力：H-R1（dp12 × N = 1．有効な 5 組の全てで q > 1.000，中央値 1.080 ≥ 1.02）成立，H-R2（fs × N = 1．全て > 1.000，中央値 1.043）成立，H-R3（記録）は fs・dp12 × N = 8 とも「全て > 1」が不成立（各 1 組が遅れを伴う．下の事実 4）．
- **このイテレーションの結論：採用**（G2 dp12 成立，G3 の不成立 0 件，dp12 × N = 1 の有効な組 5，H-R1 成立）．以後の負荷の実験は `retrieve_body: pre_encoded` を `probe_body: pre_encoded`・`sniffio_lookup: blocked`・`pool_assign: lean` と併せて明示して流す．既定の値は `per_request` のまま．
- **ノイズか信号か**：組の比の揺れの見積もり（事前登録．Iteration 13 の dp12 × N = 1 で中央値から −0.3%〜+1.9%）に対して，dp12 × N = 1 の q の最小 1.032 も 1 を 3.2% 上回り，5 組とも同じ向きである（差が無いときに 5 組とも > 1 となる確率は 1/32）．
  加えて，記録の「Iteration 13 と同じ範囲の規則」でも dp12 × N = 1・fs × N = 1 は pe の 5 回の範囲が pr より上で重ならない（隙間 +2.3%・+1.9%）．比が 1.10 に届かない（1.088・1.034）だけで，向きと分離は 3 回の範囲の規則でも見えている．N = 1 の 2 条件の伸びはノイズでは説明できない．
  N = 8 の q（0.800〜1.196）の広さは，遅れの問を伴う 3 実行（事実 4）による．

**分析**

事実（記録から計算したもの）
1. **N = 1 の処理量の比は，組ごとに 1 要求あたりの質問者の CPU の比の逆数と一致する**（Iteration 11〜13 に続いて 4 回目．今回は組ごとに確かめた）．dp12 × N = 1 の 5 組のうち 4 組は q と CPU の比の差が 0.003 以下（1.118 対 1.118，1.078 対 1.075，1.096 対 1.096，1.080 対 1.079）で，残る r4 だけが q 1.032 対 CPU の比 1.070 と離れた（pe の per_proc 0.975．事実 4）．
   fs × N = 1 も 5 組のうち 4 組で差が 0.004 以下，r3 だけが q 1.022 対 1.034（pe の `TCPTimeouts` 757）．per_proc は全ての N = 1 の実行で 0.975〜1.012 で，律速は質問者のプロセスのままである．
2. **1 要求の CPU の比は，N = 8 を含む 16 組の全てで pe が小さい**（比 1.034〜1.118）．処理量が遅れで乱れた組でも，CPU の比は 1.05〜1.09 にそろっている．処理量の q よりも CPU の比の方が揺れに強い指標である．
3. **実機の節約は事前の測定より大きく，Iteration 13 で学んだ「縮小の率」（dp12 約 0.6・fs 約 0.8）は今回は当てはまらなかった**．1 要求の節約は dp12 で 0.093 ms（事前の測定 0.081 ms の 1.15 倍．縮めた見込み 約 0.049 ms の約 1.9 倍），fs で 0.036 ms（同 0.019 ms の 1.9 倍．見込み 約 0.015 ms の約 2.4 倍）．
   処理量の比も見込み（dp12 約 1.04・fs 約 1.015）の約 2 倍の伸び（1.080・1.043）だった．期待効果の処理量（dp12 × N = 1 約 63.5 問/秒）に対して実測の pe の中央値は 67.0 問/秒である．
4. **N = 8 の外れ値と TCP の再送の多い実行は，連続実行の後半の約 1 時間に集まっている**．`requester_tcp` の `TCPTimeouts` が 200 以上の実行は 34 実行のうち 7 実行で，全て 03:45〜04:50 頃に始まった実行（dp12_n8_pr_r2 549，fs_n1_pe_r3 757，fs_n8_pe_r3 704，dp12_n8_pe_r3 801，fs_n1_pr_r4 329，dp12_n1_pe_r4 342，fs_n1_pe_r5 225）である．それより前の 17 実行（r1・r2 の大半）と，最後の 3 実行（dp12_n1_pr_r5・pe_r5・anchor_post）は 97 以下だった．
   N = 8 で q を外した 3 実行（fs_n8_pe_r3：遅れの問 24・per_proc 0.848・q 0.911，dp12_n8_pr_r2：9・0.883・q 1.196，dp12_n8_pe_r3：4〔相対の閾値で 140〕・0.762・q 0.800）は全てこの 7 実行に入り，N = 8 で `TCPTimeouts` が 97 以下の 9 実行には遅れ（絶対の閾値で 2 問以下）も per_proc の低下（0.990 以上）も無い．
   N = 1 でも，q が各方式で最も小さい組（dp12 r4 の 1.032，fs r3 の 1.022）と fs で最も大きい組（r4 の 1.071．pr の側が 329）は，片方の実行がこの 7 実行に入る組だった．
5. **再送の多い実行は pr・pe の両方に出ている**（pr 2・pe 5 実行）．同じ時間帯の中で，r3 は pr → pe の順で pe が後，r4 は pe → pr の順で両方に出ており，時間帯で決まっていると読める．新しい接続の数は pr・pe で同じ程度（fs × N = 1 で pr 740〜1,037・pe 727〜1,137，dp12 × N = 1 で pr 416〜455・pe 397〜465），再試行は 0〜9 回（`ReadError` が大半，`RemoteProtocolError` が fs_n8_pr_r1 に 2 回・fs_n1_pe_r3 に 6 回）で，pe が接続の作り方を変えた形跡は無い．
6. **事後の感度の確かめ（判定に使わない．規則は変えていない）**：`TCPTimeouts` が 200 以上の実行を含む組を除くと，dp12 × N = 1 は 4 組で q 1.078〜1.118（中央値 1.088），fs × N = 1 は 2 組で 1.043・1.049，N = 8 は fs 2 組で 1.013・1.050，dp12 1 組で 1.049 で，全て > 1 である．判定の結論はこの除外の有無で変わらない．
7. **律速は質問者のままで，dp12 × N = 8 は専門家ノードの頭打ちに近づいた**．遅れの無い dp12 × N = 8 × pe（r1・r2）は 326.3・323.1 問/秒（約 4,550 要求/秒）で，per_proc 0.994・0.990，最も忙しいノードの `expert_cpu_util` 1.497・1.491 コア（閾値 1.58 の約 95%，W = 1 の頭打ち約 1.86 コアの約 80%）．E(8) は fs 0.704 → 0.699，dp12 0.630 → 0.602 と pe でわずかに下がった（遅れの実行を含む中央値からの値）．
8. 制御点 1 台の質問者（N = 8．遅れの無い回）の処理量は Iteration 8 → 14 で dp12 186.2 → 約 325 問/秒（約 1.75 倍），fs 82.4 → 約 178 問/秒（約 2.2 倍）になった．N = 1 は dp12 32.2（Iteration 11 の pr）→ 67.0，fs 13.4 → 31.5 問/秒．

推測（上の事実と矛盾しないが，未確認のもの）
- 事実 3 で実機の節約が事前の測定より大きいのは，pe が 1 問ごとに 768 個の Python の float の list（`tolist`）と `RetrieveRequest` の検証・`model_dump` の中間の object を 2 回分作らなくなり，割り当てと GC の費用も減るためと読める．py-spy の項目の割り当てでは GC の時間は本文の組み立ての項目に入らない．
  事前の測定の 600 問と実機の 2,940 問ではプロセスが持つ object の数が違い，GC の 1 回の費用も違いうる．また，事前の測定の fs の差（0.019 ms）は cur と rshared の 3 回の範囲が重なる大きさで，事前の測定の揺れ（約 ±0.02 ms）と同じ程度だった．どちらの寄与が大きいかは確かめていない．
- 事実 4 の時間帯の再送は，Iteration 11・13 の N = 8 の約 1 秒の遅れと同じ形（遅れた問の `probe_node_s_max` は 0.5 秒未満，再試行 0）で，質問者の外（ネットワーク・専門家ノードの受け入れ）で起きる一時的な事象と読める．時間帯に集まる理由（他の利用者の通信，スイッチ，専門家ノードの側の何か）は区別していない．

**B38 (a)（判定の規則が緩い点）への見解**：今回の採用は，規則の閾値（中央値 1.02，符号検定 1/32）だけでなく次の 3 点で支えられており，誤って採用した見込みは小さい．(1) 主の dp12 × N = 1 の q の中央値 1.080 は閾値 1.02 の約 4 倍の差で，最小の組でも 1.032．(2) 3 回の範囲の規則でも範囲が重ならない（比 1.088 が 1.10 に届かないだけ）．(3) 仕組みの確かめ（組ごとの CPU の比の逆数との一致，16 組の全てで CPU の比 > 1）が独立に成り立つ．
ただし効果の大きさは dp12 で +8%・fs で +4% と，Iteration 11〜13 の採用（fs × N = 1 で +33%・+49%・+16%）より小さい．組の比の規則は「小さな効果の有無」の検出の規則であり，採用の重みを大きさで読み分けるため，以後の報告では採用の判定と並べて q の中央値（効果の大きさ）を必ず書く．
同じ緩い規則で小さなレバーを続けて試すと，偶然の採用（1 回あたり 1/32〜1/16）が積み重なるので，組の比の規則を使うときは CPU の比の一致（仕組みの確かめ）を判定の条件に加えることを次の事前登録で勧める．

**想定外の挙動**：事実 3（効果が見込みの約 2 倍）と事実 4（再送の多い実行が後半の約 1 時間に集まる）．判定には影響しなかった．言語崩れ・発散・OOM は該当なし．

**学び**
- 「N = 1 の処理量の比 ≈ 1 要求の質問者の CPU の比の逆数」は組ごとに見ても成り立つ（10 組のうち 8 組で差 0.004 以下）．外れる組は TCP の再送の多い実行を含む組だけだった．小さな効果は，交互に並べた組の比と組ごとの CPU の比を併せて見ると，5 組で検出できる．
- 事前の測定から実機の効果を見積もる「縮小の率」は，項目によって 0.6 倍にも 1.9 倍にもなる．事前の測定の節約が 0.02〜0.1 ms の小さな項目では，事前の測定の揺れ（約 ±0.02 ms）と GC などの項目に入らない費用のために，見込みの精度は約 2 倍の幅しか無い．
- TCP の再送の多い実行は，ある時間帯に集まって pr・pe の両方に出る．交互に並べた組の比はその時間帯の影響を打ち消しきれない（片方の実行だけが当たる）．組の比の判定には `requester_tcp` を併記し，事後の感度の確かめを記録に残すとよい．
- 質問者の CPU を削る系列の効果は，dp12 × N = 1 の比で Iteration 11 → 14 に 1.30 → 1.19 → 1.09 → 1.08，fs × N = 1 で 1.33 → 1.49 → 1.16 → 1.04 となった．設定キーで挙動を変えずに除ける大きな項目は，これで残っていない（残りは dp12 の routing の 1 段目 約 8%，httpx の URL の解析 約 4%，ログ 約 3%．いずれも Iteration 14 の事前の測定の py-spy の値）．

**限界**
- 判定は組の比の規則（Iteration 11〜13 の「範囲が重ならず比 ≥ 1.10」より緩い）による．効果の大きさは dp12 +8%・fs +4% である．
- 事実 4 の再送の時間帯の原因と，遅れと再送の因果は確かめていない（B37 (c) の 1 秒ごとの記録は足していない）．
- 事実 3 の GC の寄与は推測である．
- 有線 LAN・検索だけ・閉じた系・制御点 1 台の質問者である．

**次の一手**（backlog B39 [needs-human]）：設定キーで除ける大きな項目は残っていない（B37 (b)・B38 (c)）．次の方向は (i) HTTP クライアントの置き換え，(ii) 質問者を複数のデバイスに置く（B25 (a)），(iii) 負荷の系列を止めて RQ の研究へ戻る，のいずれかで，研究の方向の選択と cluster の形式の変更を含むので人間の判断を仰ぐ．推奨は (iii)（理由は B39）．

## Iteration 13: 質問者の httpcore の接続の池の割り当ての走査を軽くしたときの処理量

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B35 [auto-decided]（Iteration 12 は採用．質問者 1 プロセスの処理量は 1 要求あたりの質問者の CPU でほぼ決まる）．config.yml の levers 末尾の `requester.next_cpu_item: [reduce]`（仮称）を，事前の測定で最大の項目と分かった
**httpcore の接続の池の割り当て（`AsyncConnectionPool._assign_requests_to_connections`）の費用**を除く **config.yaml の新しいキー `experiment.pool_assign`（`stock`〔既定．今の挙動〕/ `lean`）**として確定した（backlog B36）．HTTP クライアントの置き換えはしない（B35 (b)）．依存（uv.lock・イメージの版）・ノードの API・要求の本文・方式の選択・データ・シャード・`cluster`・`node_workers` は変えない．

**事前の測定の方法**（B35 の指示．判定に使わない．スクリプトと出力は `results/20261015_iter13_requester_cpu_summary/prestudy/`）
- 制御点 wafl-ctrl5 の `atrium-full` のイメージ（`dc58854`）のコンテナ（`--network host`）の中で，**本物の `atrium node` を 30 個**（127.0.0.1:18100〜18129．実機の placement の 5 シャードずつ．シャードは `/home/denjo/atrium-data/enronqa/shards` の実体を `/tmp/iter13_prestudy/shards` へ写したもの．データディレクトリは読み取りだけ）と，**本物の `atrium run`（質問者 1 プロセス）**を流した．
  Iteration 11・12 の `bench.py`（偽のノード・probe だけ）と違い，retrieve（m = 2）・to_thread・記録・routing を含む実際の経路である．条件は `dataset: enronqa`・`retrieval_only`・`question_limit: 4`（600 問）・parallel 30・`per_node`・`probe_body: pre_encoded`・`sniffio_lookup: blocked`，方式は fs と dp12（`probe_d = 12`）．
- 内訳は py-spy 0.4.2（250 Hz，raw）と cProfile で取った．変種の効果は差し替え（`run_one.py` の monkeypatch．本番のコードは変えていない）を当てた 3 回の 1 要求あたりの質問者の CPU（`requester_cpu_s` ÷ HTTP 要求の数）で比べた．ノードが同じ 12 コアとループバックを使うので，1 要求の CPU は実機（fs 1.189・dp12 1.340 ms）より約 25〜30% 大きい．

**事前の測定で分かったこと**（事実）
- py-spy の主スレッドの標本（問の処理の区間）で，`_assign_requests_to_connections` を含む標本は **fs で 24.7%，dp12 で 15.8%** で，単一の関数（とその下）として最大である．そのうち待機中の接続の生存の確かめ（`has_expired` → `get_extra_info("is_readable")` → anyio の `extra_attributes` の辞書の組み立てと `getpeername`・`poll`）が fs 16.4%・dp12 10.9% を占める．
  ほかの項目は，h11 の組み立てと解析（fs 9.9%・dp12 8.4%），JSON の符号化（fs 5.0%・dp12 10.4%．主に `/v1/retrieve` の `json=` の本文．pre_encoded の本文も含む），routing の 1 段目（dp12 8.6%），httpx の INFO のログ（3.1%），pydantic の検証（0.6〜2.0%），`current_async_library`（blocked の上で 0.9%）だった．
- cProfile の 1 要求あたりの呼び出しの数（同期の関数だけ数えられる）：`has_expired` fs 31.7・dp12 18.5 回，`is_idle` fs 237・dp12 118 回（掃除の段の二重の走査による），`current_async_library`（httpcore）fs 8.6・dp12 6.5 回．
  **B35 (d) の推測（dp12 では `current_async_library` の呼び出しが少ない）は数の上では支持される**（比 0.75．Iteration 12 の節約の比 0.62 より大きいので，それだけで説明し切れるかは分からない）．
- 原因（コードから確かめたこと．`.venv` の httpcore 1.0.9）：`_assign_requests_to_connections` は要求の開始と終了のたびに（1 要求あたり数回）呼ばれ，池の全ての接続を走査する．待機中の接続ごとに `has_expired` がソケットの読み取り可否を確かめ，その中で anyio が属性の辞書を毎回作り `getpeername` を呼ぶ．
  さらに掃除の段の `len([connection.is_idle() for connection in self._connections]) > self._max_keepalive_connections` は待機中の接続ごとに全接続のリストを作る（接続の数の 2 乗）．`per_node` の池には parallel 30 で 1 ノードあたり約 30 本の接続があり，fs は 1 問で全 30 台へ送るので走査が長い．
  `per_node` は `httpx.Limits(max_connections=64)` で `max_keepalive_connections` を省くので，httpcore の中では上限が `sys.maxsize` になり，この条件は常に偽である（費用だけがかかる）．

| 変種（3 回．600 問） | fs の 1 要求の CPU（ms） | 中央値 | fs の問/秒の中央値 | dp12 の 1 要求の CPU（ms） | 中央値 | dp12 の問/秒の中央値 |
|---|---|---|---|---|---|---|
| cur（今の最良の構成） | 1.551・1.569・1.555 | 1.555 | 19.82 | 1.664・1.657・1.629 | 1.657 | 42.66 |
| fastread（生存の確かめを生のソケットへ直接 poll） | 1.373・1.378・1.369 | 1.373 | 22.38 | 1.522・1.531・1.507 | 1.522 | 46.35 |
| linearonly（掃除の段を 1 回の走査に） | 1.506・1.490・1.506 | 1.506 | 20.46 | 1.627・1.618・1.631 | 1.627 | 43.39 |
| **lean（= linear．上の 2 つを併せる）** | 1.329・1.340・1.332 | **1.332** | **23.02** | 1.478・1.479・1.484 | **1.479** | **47.59** |
| cap8（per_node の上限 64 → 8．別の回） | 1.497・1.499・1.495 | 1.497 | 20.59 | 1.607・1.593・1.587 | 1.593 | 44.29 |
| quiet（httpx の INFO のログを止める．別の回） | 1.476・1.518・1.511 | 1.511 | 20.42 | 1.603・1.591・1.589 | 1.591 | 44.38 |

- lean の節約は fs 0.223 ms（−14.3%．問/秒の比 1.161），dp12 0.178 ms（−10.7%．比 1.116）で，測った変種のうち最大である．節約の約 8 割は fastread の部分による．3 回の幅は各変種で中央値の ±1.5% 以内で，cur と lean の範囲は fs・dp12 とも重ならない．失敗は全ての回で 0，再試行は 0〜1 回．
- cap8・cap16（per_node の上限を下げて走査を短くする．httpx の公開の設定の範囲）は round 2 の cur（fs 1.539，dp12 1.643 ms 前後）に比べて 0〜−0.05 ms しか減らず，ログを止めるのも −0.03〜−0.05 ms だった．httpx の設定だけでは最大の項目を除けない．
- lean を当てた後の py-spy では，置き換えた割り当ての関数（`poll` を含む）が fs 13.7%・dp12 8.7% に残り，次に大きいのは h11 の送受信，`/v1/retrieve` の本文の `json.dumps`（fs 5.6%・dp12 10.7%），dp12 の routing の 1 段目（7.6%）である．
- `cur` の r1 は直前の動作確認の出力ディレクトリが残っていて再開扱い（0 問）になったので捨て，round 2 では同じ条件の直前の動作確認の値（fs 1.539・dp12 1.643 ms）を代わりに見た．表の cur は round 3 の 3 回（同じ回の中で cur・fastread・linearonly・lean を交互）である．

**調査で分かったこと**（tavily-search・tavily-extract．今回は Web 検索が通った）
- httpcore の上流に，同じ関数の書き直しの PR がある：VictorPrins, "Fix 2 bugs and improve performance from O(N²+NxM) to O(N+M) in _assign_requests_to_connections"（encode/httpcore PR #1035，2025-09-14 作成，**未マージ**．https://github.com/encode/httpcore/pull/1035 ）．
  PR の説明では，(Bug 1) `len([connection.is_idle() ...])` が待機中の数ではなく全接続の数を返し `max_keepalive_connections` を守らない（PR #1000 も同じ修正で，承認済み・未マージと書かれている），(Bug 2) 待ち行列の複数の要求が同じ HTTP/1.1 の接続へ割り当てられうる，(性能) 要求の開始と終了の 2 回ずつ呼ばれる hot path で，作者のアプリでは全標本の 59% → 4%・CPU 使用率が半分になった，とある（作者の報告で，独立の確認ではない）．
- PyPI の httpcore の最新は 1.0.9（2025-04-24）で，上の修正を含む版は出ていない（2026-10-15 に PyPI の JSON で確認）．**依存の版を上げて解決する道は今は無い**．
- httpx の同時の要求での CPU の重さ（encode/httpx #3215 → pydantic/httpx2 #827．Iteration 11 に記録）は，今回の割り当ての走査と同じ層の話である．aiohttp への置き換えは B35 (b) のとおり扱わない．

**形の判断**：httpx の `AsyncHTTPTransport` は httpcore の `network_backend` を受け取らず，公開の設定（上限・keepalive）では効かない（上の cap8・cap16）．そこで Iteration 12 の `sniffio_lookup` と同じく，**質問者のプロセスの始めに httpcore の 2 か所を差し替える設定キー**にする．
(1) 同じイメージで stock と lean を交互に流せる．(2) 質問者のプロセスだけに効き，専門家ノード・node-bench の負荷生成は変わらない．(3) 依存の版は変えない．
差し替えは httpcore 1.0.9・anyio 4.15.1 の内部（`AsyncConnectionPool._assign_requests_to_connections`，`httpcore._backends.anyio.AnyIOStream.get_extra_info`，anyio の `SocketStream._raw_socket`）に依存するので，版が違えば lean を拒んで止める（黙って stock に戻さない）．

**差し替えの中身**（挙動を変えないことを条件にする）
- **掃除の段を 1 回の走査に**：元の掃除の段（閉じた接続を除き，期限切れの接続を閉じ，`len(今の接続のリスト) > max_keepalive` なら待機中の接続を閉じる）を，各時点のリストの長さを数で持って 1 回の走査で同じ結果にする．**Bug 1 の挙動（全接続の数で比べる）も含めて元と同じにする**（PR #1035 の修正は取り込まない．`per_node` の下では条件は常に偽で差は出ない）．
  割り当ての段（使える接続 → 新しい接続 → 待機中の接続を閉じて作り直す）は元と同じ順・同じ選び方にする（Bug 2 も元のまま）．
- **生存の確かめを生のソケットへ直接**：`get_extra_info("is_readable")` だけを，anyio の属性の辞書を通らずに同じ `httpcore._utils.is_socket_readable(生のソケット)` で答える．anyio の `raw_socket` の属性は同じソケットを返すので，判定の値は同じ．他の `get_extra_info` は元のまま．

**問い**：(1) 池の割り当ての走査を軽くすると，`pre_encoded`・`blocked` の上で実機の質問者 1 プロセスの処理量は事前の測定と同じ程度（fs 約 1.15 倍）伸びるか．(2) dp12 でも伸びるか．(3) N = 8 で律速が専門家ノードへ移るか．

**仮説**
- **H-P1（主）**：flood_score × N = 1 で，lean の 3 回の処理量の範囲が stock の範囲より上にあり重ならず，かつ中央値の比が 1.10 以上．仕組み：N = 1 は質問者のプロセスが 1 コアに張り付いて律速（Iteration 11・12 の事実 1）なので，1 要求の CPU の比の逆数だけ処理量が伸びる．
- **H-P2**：device_probe（D = 12）× N = 1 で H-P1 と同じ規則が成り立つ（事前の測定の比は 1.116 で，余裕は小さい）．
- **H-P3（記録）**：N = 8 で fs・dp12 のそれぞれ H-P1 と同じ規則が成り立つ．

**単一レバー**：config.yaml の **`experiment.pool_assign` を `stock` → `lean`** と変える．それ以外（方式・D・N・parallel・問・`http_pool: per_node`・`probe_body: pre_encoded`・`sniffio_lookup: blocked`・ノードの W = 1・依存）は両方の値で同じにする．
2 つの差し替えは同じ 1 つの項目（`_assign_requests_to_connections` とその下の生存の確かめ）の費用を除くためのもので，1 つのキーで同時に切り替える．片方ずつの効果は上の事前の測定の値を記録として使い，実機では分けない．
- **実装**（phase 2 で行う．事前登録の commit に含める）
  - `src/atrium/config.py`：`ExperimentConfig.pool_assign: Literal["stock", "lean"] = "stock"`．`config.yaml` に `pool_assign: stock` とコメントを足す．
  - `src/atrium/requester.py`（`apply_sniffio_lookup` の近く）：`apply_pool_assign(mode: str) -> str`（httpcore の版を返す）．`lean` なら httpcore 1.0.9 であること・`AnyIOStream` と anyio の `SocketStream` に必要な属性があることを確かめてから 2 か所を差し替え，違えば `RuntimeError` で止める．同じプロセスで 2 回呼んでも 1 回だけ当てる．当てたら INFO のログを 1 行出す（spawn の子で当たったことを requester.log で確かめるため．Iteration 12 の事実 9）．差し替えの理由（上の事実と PR #1035）をコメントに書く．
    `run_experiment`（`apply_sniffio_lookup` の直後，client を作る前）と `requester_procs._worker_main`（同じく `apply_sniffio_lookup` の直後）の 2 か所から呼ぶ．
  - `src/atrium/experiment.py`：`run_meta.json` に `pool_assign` と `httpcore_version` を残す．
  - **記録の指標の修正（B35 (a)．判定に使わない．記録だけの変更）**：(i) `post_with_retry` の warning に例外の型名を足す（`retrying %s (%d/%d): %s`）．
    (ii) `run_experiment` が問の処理の区間の前後で**自分のネットワークの名前空間**の `/proc/net/snmp` の `Tcp: RetransSegs` と `/proc/net/netstat` の `TcpExt: TCPSynRetrans`・`TCPTimeouts` を読み，差分を `run_meta.json` の `requester_tcp`（読めなければ null）に残す（N ≥ 2 の spawn の子も同じコンテナの名前空間なので，親の前後で全体を数えられる）．`iter12_chain.sh` のホストの `nstat` は使わない．
  - テスト（`tests/test_pool_assign.py`）：既定値が `stock` であること．`stock` では httpcore を差し替えないこと．
    **掃除と割り当ての同値**：閉じた・期限切れ・待機中・使用中の接続と待ち行列の要求を持つ偽の接続・要求（`is_closed`・`has_expired`・`is_idle`・`is_available`・`can_handle_request` を持つ小さなクラス）を種を変えて多数作り，`max_keepalive_connections` を `sys.maxsize` と小さい値（1・3）・`max_connections` を余裕ありと上限ちょうどにして，元の関数と lean の関数が返す閉じる接続の列・残る接続の列（順を含む）・各要求の割り当て先が一致すること．
    **生存の確かめの同値**：localhost の本物の TCP の接続で，相手が何も送っていないとき・相手が閉じた後のそれぞれで，元の `get_extra_info("is_readable")` と lean の値が一致すること．
    **全体**：localhost で動かした本物のノード（`create_app` を uvicorn で 1 つ）へ lean で probe と retrieve を同時に複数送り，応答が stock と一致すること．版が違うときに lean が `RuntimeError` になること（`monkeypatch` で版の文字列を変える）．テストの後に差し替えを元に戻す（fixture）．
  - 文書：`docs/d0002_configuration.md`（`pool_assign`），`docs/d0004_metrics.md`（`run_meta.json` の `pool_assign`・`httpcore_version`・`requester_tcp`，遅れの問の数え方）．
  - 連続実行：`results/20261015_iter13_requester_cpu_summary/` に `iter13_chain.sh`（`iter12_chain.sh` を写し，`pool_assign` を `experiment` の直下のキーとして渡す．ホストの `nstat` の記録は除く）・`iter13_specs.template.txt`・`iter13_smoke_specs.template.txt`・集計 `iter13_summary.py`（下の規則を機械的に出す．合成の結果で動作を確かめる）．

**固定する構成**：`dataset: enronqa`，`answer_mode: retrieval_only`，m = 2，`http_pool: per_node`，**`probe_body: pre_encoded`**，**`sniffio_lookup: blocked`**（B35 (c)．Iteration 11・12 の採用の構成），parallel 30（全プロセスの合計），test の全 2,940 問（`question_limit: null`），**`node_workers: 1`**，T4 以来の 30 台 × 5 受信箱の配置，各回の前に deploy し直す（Iteration 8・11・12 と同じ）．
連続実行の間は制御点で他のコンテナを動かさず commit もしない．依存は変えない．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 0：実機の動作確認**（連続実行の前に 1 回．判定に使わない）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．制御点に事前の測定のコンテナ（`iter13-prestudy`）が残っていないことを確かめる．
2. `<date>_iter13_smoke_fs_ln_n1`（`dataset=enronqa answer_mode=retrieval_only question_limit=2 http_pool=per_node probe_body=pre_encoded sniffio_lookup=blocked pool_assign=lean routing=flood_score parallel=30 requester_procs=1`）を deploy → start → analyze で 1 回流す．続けて同じ条件で `requester_procs=8` を 1 回流す．
3. 次を全て満たすことを確かめる．満たさなければ連続実行を流さず，原因を調べて記録する：失敗 0 問，300 問の `selected_sources` が `results/20261015_iter8_anchor_fs_n1_p30_post/results.jsonl` と 300/300 一致，`run_meta.json` の `pool_assign` = lean・`httpcore_version` = 1.0.9・`sniffio_lookup` = blocked・`probe_body` = pre_encoded・`http_pool` = per_node・`requester_procs` が仕様どおり，`requester_tcp` が null でない，`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない，
   N = 1 の 1 要求あたりの質問者の CPU が Iteration 12 の fs × N = 1 × bk（1.189 ms）より小さい（記録．効いていることの粗い確かめ），N = 8 の requester.log に spawn の子が lean を当てた記録（`apply_pool_assign` の INFO の 1 行を子ごとに出す）が 8 件ある．

**手順 1：実機の連続実行**（`iter13_specs.template.txt` の `<date>` を実行日にした `iter13_specs.txt` を `iter13_chain.sh` に**絶対パス**で渡す）
1. 環境の揺れの確認（前）：flood_score × N = 1 × parallel 30 × 300 問（`question_limit=2`）× `probe_body: per_request` × `sniffio_lookup: search` × `pool_assign: stock` を 1 回（anchor_pre．Iteration 7〜12 の anchor と同じ条件）．
2. 主の系列：方式 {fs，dp12（`routing=device_probe routing.probe_d.enronqa=12`）} × N ∈ {1, 8} × `pool_assign` ∈ {stock（sk），lean（ln）} × 3 回，2,940 問，全て `probe_body: pre_encoded`・`sniffio_lookup: blocked`．
   各回の中は fs_n1 → fs_n8 → dp12_n1 → dp12_n8 の順に流し，各組で r1・r3 は sk → ln，r2 は ln → sk の順にする（時間の揺れを両方の値に均等に掛けるため）．
3. 環境の揺れの確認（後）：1 と同じ（anchor_post）．
- 計 26 実行（手順 0 を除く）．所要は約 2〜2.5 時間の見込み（Iteration 11・12 と同じ形）．deploy の失敗の扱い・失敗率の打ち切りは Iteration 12 と同じ（`iter13_chain.sh`）．

**指標**（Iteration 12 と同じ．`docs/d0004_metrics.md`）：処理量（成功した問の数 ÷（`finished_at` − `started_at`）），e2e_s・probe_s の p50・p95，失敗率，再試行の数，1 秒あたりの HTTP 要求の数，`requester_cpu_util`・`requester_cpu_util_per_proc` の中央値，`expert_cpu_util_max`（と最も忙しいノード），`control_cpu_busy`，2,940 問の hit@2，1 要求あたりの質問者の CPU（ms）．主の量は各条件の 3 回の処理量（最小〜中央値〜最大）．
**記録の指標**（B35 (a)．判定に使わない）：
- **遅れの問の数は N = 8 の実行だけで数える**：`probe_s` ≥ 0.9 秒の問の数と，そのうち `probe_node_s_max` < 0.5 秒の数．N = 1 では数えない（Iteration 12 の事実 3．質問者の CPU の待ち行列で probe_s の中央値が 0.7〜1 秒になる）．併せて全ての実行で「`probe_s` がその実行の `probe_s` の中央値の 5 倍以上の問の数」（相対の閾値）を出す．
- **質問者の TCP の再送**：`run_meta.json` の `requester_tcp`（質問者のコンテナのネットワークの名前空間の差分）．
- **再試行の例外の型ごとの数**：requester.log の `retrying ... : <型名>` を数える．
- **遅れの無い実行だけの中央値**（N = 8 のみ．遅れの問の数が 0 の実行の処理量の中央値．参考値）．

**正しさの確認**（満たさない実行は判定に使わず「欠け」とし，原因を調べて記録する）
- G1：失敗率が 1% 以下．
- G2（方式ごと）：その方式の全ての実行（sk・ln・N = 1・8）で，失敗の無い問の `selected_sources` が問ごとに 100% 一致する．成り立たなければその方式の判定に進まない（要求も本文も同じなので選択は変わらないはず）．
- G3：`run_meta.json` の `pool_assign`・`httpcore_version` = 1.0.9・`sniffio_lookup` = blocked・`sniffio_installed` = false・`probe_body` = pre_encoded・`requester_procs`・`http_pool` = per_node・routing・D・`n_questions` が仕様と一致する．
- G4（記録）：2,940 問の hit@2 が Iteration 8・11・12 の値（fs 0.8041，dp12 0.6830）と一致するか．
- G5（律速の読みだけに効く）：`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない．

**環境の揺れの規則**（記録のため）：anchor_pre・anchor_post の処理量が [11.127, 14.247] 問/秒，e2e p95 が [3.053, 4.067] 秒（Iteration 7〜12 と同じ範囲）に入るかを記録する．stock の主の系列の値が Iteration 12 の bk の同じ条件の範囲（fs N = 1：24.850〜26.617，fs N = 8：138.133〜161.288，dp12 N = 1：44.376〜54.251，dp12 N = 8：276.325〜294.681 問/秒）から ±5% を超えて外れたら「環境が変わった」と記録する．
比べる相手は同じ連続実行の中の stock なので，判定の基準は変えない．

**判定の規則**（比べる 2 条件の 3 回の範囲が重ならないときだけ「差がある」）
- **伸びた**（ある方式・N で sk → ln）：ln の 3 回の処理量の範囲が sk の範囲より上にあり重ならず，かつ中央値の比 ln ÷ sk が 1.10 以上．
- **H-P1（主）**：fs × N = 1 が「伸びた」．
- **H-P2**：dp12 × N = 1 が「伸びた」．
- **H-P3（記録）**：fs × N = 8・dp12 × N = 8 のそれぞれが「伸びた」（遅れの問の数を併記する）．
- **このイテレーションの結論**：
  - **棄却（測定不成立）**：G2 が fs で成り立たないか，G3 が 1 つでも不成立か，fs × N = 1 の sk・ln のいずれかで有効な実行が 2 回未満．原因を調べて記録する．
  - **採用**（以後の負荷の実験は `pool_assign: lean` を明示して流す．既定の値は `stock` のまま）：上が当てはまらず，H-P1 が成り立つ．
  - **部分的に支持**：H-P1 が成り立たないが，fs × N = 1 の ln の範囲が sk の範囲より上にある（比 < 1.10），または H-P2 が成り立つ．
  - **棄却（伸びない）**：どれでもない（事前の測定の節約が実機では効かない．実機の質問者を py-spy で取り直す）．
- 記録（判定に使わない）：方式・N ごとの比，1 要求あたりの質問者の CPU の sk と ln の差（事前の測定の fs 0.223・dp12 0.178 ms と，実機の大きさへ縮めた見込み fs 約 0.17・dp12 約 0.14 ms と比べる），N = 8 の E(8) の変化，`requester_cpu_util_per_proc` が 1 に張り付いたままか，e2e p95 の変化，新しい接続の数．
  **律速の移りの読み**（Iteration 12 と同じ）：dp12 × N = 8 × ln で `requester_cpu_util_per_proc` の中央値が 0.9 未満かつ最も忙しいノードの `expert_cpu_util` が 1.58 コア以上なら「律速が専門家ノードへ移った」と記録する．遅れの問の数が 10 問以上の実行はこの読みに使わない．

**ノイズの見積もり**：Iteration 12 の 3 回の処理量の幅は，N = 1 で中央値の −0.9%〜+0.3%（fs sr）・−1.0%〜+0.8%（fs bk の r1 を除く）・−11.7%〜+7.9%（dp12 bk．遅れと再試行による），N = 8 で −5.7%〜+12.1%（fs）・−5.2%〜+1.1%（dp12 bk）だった．事前の測定の 3 回の幅は ±1.5% 以内．
閾値 1.10 は fs × N = 1 の幅の約 10 倍で，範囲が重ならない規則を併用する．dp12 × N = 1 は事前の測定の比が 1.116 と閾値に近く，Iteration 12 のような遅れが 1 回でも出れば範囲が重なりうるので，主の仮説にしない（H-P2）．

**期待効果**（推測．判定に使わない）：事前の測定の節約（fs 0.223 ms，dp12 0.178 ms）を実機と事前の測定の 1 要求の CPU の比（fs 1.189 ÷ 1.555 = 0.76，dp12 1.340 ÷ 1.657 = 0.81）で縮めると，fs × N = 1 は 1.189 → 約 1.02 ms，処理量は 26.4 → 約 30.8 問/秒（比 約 1.17）．
dp12 × N = 1 は 1.340 → 約 1.20 ms，50.3 → 約 56 問/秒（比 約 1.12）．N = 8 は Iteration 11・12 のとおり N = 1 の比からは見積もれない．dp12 × N = 8 で最も忙しいノードは約 1.3 → 1.45 コアの見込みで，閾値 1.58 には届かない見込み．

**限界**：有線 LAN・検索だけ・閉じた系・制御点 1 台の質問者．lean は httpcore 1.0.9・anyio 4.15.1 の内部に依存する実験のための差し替えで，依存の版を上げれば作り直しが要る．上流の PR #1035 の Bug 1・Bug 2 の修正は取り込まない（挙動を変えないため）．待機中の接続ごとの `poll` は残る（lean の後も fs で約 14%）．
事前の測定はノードを同じ 12 コアで動かし，ループバックで測ったものなので，実機の節約の大きさは前後しうる．

**次の候補**：採用なら，lean の上の残り（h11 の送受信，`/v1/retrieve` の本文の `json.dumps`〔dp12 で約 11%〕，dp12 の routing の 1 段目〔約 8%〕）を同じ方法（本物のノード 30 個と本物の `atrium run` の事前の測定）で取り直し，最大の項目を 1 つ除く．dp12 × N = 8 で律速が専門家ノードへ移ったと読めたら，負荷の実験の `node_workers` の扱い（B31 (a)）を人間に確かめる．棄却なら，実機の質問者を py-spy で取り直す．

### 実装・実験（2026-10-15〜16．research-cycle の phase 2）

- 実装 commit `13e004b`（push 済み．事前登録・実装・テスト・docs・chain/specs/summary を含む）．pytest 337 件・ruff 通過．mypy は触っていないファイルの既存の 16 件のみ．手順 0（smoke．N = 1・8）は全条件を満たした（selected_sources 300/300 一致，N = 1 の 1 要求の質問者 CPU 1.140 ms < 1.189 ms，N = 8 の spawn の子 8 個で lean の適用ログを確認）．
- 手順 1 の 26 実行を事前登録どおり完走（2026-10-16 01:45 終了）．失敗率は全て 0，deploy の失敗・skip なし．G1〜G5 成立（selected_sources 一致率 1.0000，hit@2 は fs 0.8041・dp12 0.683）．anchor は前 12.503・後 13.185 問/秒で範囲内．
- 処理量の中央値（問/秒．stock → lean，比）: fs N=1 25.996 → 30.188（1.161．範囲は重ならない），fs N=8 159.299 → 171.520（1.077．重ならない），dp12 N=1 56.118 → 61.143（1.090．重ならない），dp12 N=8 292.395 → 305.855（1.046．lean の r1 が 198.5 で範囲が重なる）．
- 1 要求の質問者 CPU の中央値（ms．stock → lean）: fs N=1 1.208 → 1.033，fs N=8 1.492 → 1.397，dp12 N=1 1.284 → 1.178，dp12 N=8 1.878 → 1.781．
- 規則の機械的な出力: H-P1 成立，H-P2 不成立（比 1.090），H-P3 は fs N=8・dp12 N=8 とも不成立．結論は「採用」（`iter13_summary.out`）．解釈は評価フェーズ．
- 記録: dp12 N=8 lean の r1 は遅れの問が 32（相対 245），TCP の Timeouts 903・SynRetrans 213 を伴う．遅れの無い r2・r3 では per_proc 1.000・expert_max 1.37 で律速は質問者のまま．`requester_tcp` は host ネットワークのコンテナではホスト全体の値になる（d0004 に明記）．結果ディレクトリ 28 個・summary・chain ログ・state.json は未 commit．

### Iteration 13 実行済み：分析・考察（2026-10-16．research-cycle の phase 3．判定：採用）

**変更**：config.yaml の `experiment.pool_assign` を `stock`（sk）→ `lean`（ln）と変え，`probe_body: pre_encoded`・`sniffio_lookup: blocked` を固定して fs・dp12 × N ∈ {1, 8} × 3 回（2,940 問，per_node，parallel 30，W = 1，検索だけ）を sk と ln を組ごとに交互に並べて流した（前後の anchor を含めて 26 実行．欠け 0）．事前登録の規則・閾値は結果を見て変えていない．

**正しさの確認と環境の揺れ**：G1（26 実行とも失敗 0）・G2（fs・dp12 とも 12 実行の `selected_sources` が全問一致）・G3（`pool_assign`・`httpcore_version` 1.0.9・`sniffio_installed` false ほか全て仕様どおり）・G5 は成立し，G4（hit@2）は fs 0.8041・dp12 0.6830 で Iteration 8・11・12 と同じ．
前後の anchor は 12.503・13.185 問/秒（e2e p95 3.491・3.670 秒）で事前登録の範囲の内．sk の中央値は 4 条件とも Iteration 12 の bk の範囲の ±5% の内（「環境が変わった」は無し）．

**結果と判定**（処理量．問/秒．3 回の最小〜中央値〜最大）

| 条件 | sk | ln | 比（中央値） | ln の最小 ÷ sk の最大 | 1 要求の質問者の CPU（ms．sk → ln） | 遅れの問（N = 8 のみ．sk / ln） |
|---|---|---|---|---|---|---|
| fs N = 1 | 24.974〜25.996〜26.239 | 30.091〜30.188〜30.714 | **1.161** | 1.147 | 1.208 → 1.033 | 数えない |
| fs N = 8 | 158.194〜159.299〜159.912 | 171.171〜171.520〜171.672 | 1.077 | 1.070 | 1.492 → 1.397 | 2・0・0 / 0・2・1 |
| dp12 N = 1 | 54.326〜56.118〜56.329 | 59.378〜61.143〜62.591 | 1.090 | 1.054 | 1.284 → 1.178 | 数えない |
| dp12 N = 8 | 290.195〜292.395〜294.949 | 198.503〜305.855〜307.596 | 1.046 | 0.673（r1 を除くと 1.037） | 1.878 → 1.781 | 0・0・0 / 32・0・0 |

- 規則の機械的な出力：H-P1（fs × N = 1）成立，H-P2（dp12 × N = 1）不成立（範囲は重ならないが比 1.090 < 1.10），H-P3（記録）は fs × N = 8（比 1.077）・dp12 × N = 8（範囲が重なる）とも不成立．
- **このイテレーションの結論：採用**（G2・G3 成立，fs × N = 1 の有効な実行は sk・ln とも 3 回，H-P1 成立）．以後の負荷の実験は `pool_assign: lean` を `probe_body: pre_encoded`・`sniffio_lookup: blocked` と併せて明示して流す．既定の値は `stock` のまま．
- **ノイズか信号か**：fs × N = 1 の sk の 3 回の幅は中央値の −3.9%〜+0.9%（r2 が低い），ln は −0.3%〜+1.7% で，ln の最小と sk の最大の隙間は +14.7% ある．幅の約 4 倍で，ノイズでは説明できない．
  dp12 × N = 1・fs × N = 8 も範囲は重ならず（隙間 +5.4%・+7.0%），向きは 4 条件とも同じだが，規則の閾値 1.10 に届かないので「伸びた」とは言わない．dp12 × N = 8 は ln の r1 の遅れ（事実 4）で範囲が重なり，遅れの無い r2・r3 だけでも sk の最大より +3.7〜4.3% 上にとどまる．

**分析**

事実（記録から計算したもの）
1. **N = 1 の処理量の比は，今回も 1 要求あたりの質問者の CPU の比の逆数と一致する**（Iteration 11・12 に続いて 3 回目）．fs × N = 1 は 1.208 ÷ 1.033 = 1.169 に対して処理量の比 1.161，dp12 × N = 1 は 1.284 ÷ 1.178 = 1.090 に対して 1.090．`requester_cpu_util_per_proc` は sk・ln とも 0.999〜1.011 で，律速は質問者のプロセスのままである．
2. **事前の測定に対する実機の節約の大きさは，fs で約 8 割，dp12 で約 6 割だった**．1 要求の節約は fs 0.176 ms（事前の測定 0.223 ms，実機の大きさへ縮めた見込み 約 0.17 ms），dp12 0.107 ms（同 0.178 ms・約 0.14 ms）．処理量の比も fs は事前の測定の 1.161 と同じ，dp12 は 1.116 に対して 1.090 だった．
   Iteration 12 でも dp12 の実機の節約は事前の測定の約 6 割（0.355 対 0.54〜0.63 ms）で，**2 回続けて dp12 の効果を事前の測定が約 1.6 倍に見積もっている**．
3. **N = 8 の節約は N = 1 の約半分〜9 割で，E(8) は両方の方式で下がった**（Iteration 12 と同じ向き）．1 要求の節約は fs で N = 1 の 0.176 → N = 8 の 0.095 ms，dp12 で 0.107 → 0.097 ms．E(8) は fs 0.766 → 0.710，dp12 0.651 → 0.625．N = 8 の比（1.077・1.046〜1.049）は CPU の比の逆数（1.068・1.054）とほぼ一致する．
4. **dp12 × N = 8 × ln の r1 だけで，処理量が他の回より約 35% 低かった**（198.5 問/秒）．遅れの問（probe_s ≥ 0.9 秒）が 32 問（全て `probe_node_s_max` < 0.5 秒．相対の閾値で 245 問），per_proc 0.673，最も忙しいノード 0.910 コア，`control_cpu_busy` 0.451（他の N = 8 は 0.68〜0.69）で，質問者が CPU を使い切らずに待っていた．再試行は 0．
   質問者のコンテナの名前空間の TCP の計数（`requester_tcp`．質問者は既定のブリッジのネットワークで動くので質問者自身のソケットの値）は `TCPSynRetrans` 213・`TCPTimeouts` 903 で，他の N = 8 の実行（SynRetrans 41〜85，Timeouts 41〜85）より大きい．同じ組の直前の sk r1・同じ ln の r2・r3 には遅れが無い．
5. **TCP の計数は遅れの有無と一対一には対応しない**．fs × N = 1 × sk の r2（RetransSegs 11,639・Timeouts 815）と fs × N = 1 × ln の r1（7,848・979）は大きいが処理量の低下は −3.9%・+0% にとどまる（遅れの問の相対の閾値で 2・0 問）．dp12 × N = 1 は 6 回のうち 5 回で 0 だった．N = 8 の SynRetrans は sk・ln とも毎回 40〜85 ある（子のプロセスの起動の区間を含む）．
6. **lean は接続の作り方と再試行を変えていない**．新しい接続の数は fs × N = 1 で sk 804〜1,076・ln 780〜958，dp12 × N = 1 で sk 430〜461・ln 407〜447，N = 8 も同じ程度で，再試行は 0〜7 回（例外の型は `ReadError` が大半，`RemoteProtocolError` 1 回）で sk・ln の差は無い．
7. **dp12 × N = 8 × ln（遅れの無い r2・r3）でも律速は専門家ノードへ移っていない**．per_proc 1.000・1.001，最も忙しいノードの `expert_cpu_util` 1.375・1.372 コア（閾値 1.58 未満．W = 1 の頭打ち約 1.86 コアの約 74%）．処理量 306〜308 問/秒（約 4,290 要求/秒）は Iteration 12 の bk の 291.6 の約 1.05 倍．
8. **期待効果との比較**：fs × N = 1 は約 30.8 の見込みに対して 30.19（ほぼ合う），dp12 × N = 1 は約 56 の見込みに対して 61.14（基準の sk が Iteration 12 の bk〔50.3．遅れを含む〕より高い 56.1 だったため．比は見込みの 1.12 に対して 1.09），dp12 × N = 8 の最も忙しいノードは約 1.45 コアの見込みに対して 1.37．

推測（上の事実と矛盾しないが，未確認のもの）
- 事実 4 の約 1 秒の遅れは，新しい接続の SYN の再送（Linux の初回の再送の待ちは 1 秒）と読める．`TCPSynRetrans` が他の回の約 3 倍で，遅れの問はノードの計算によらない．Iteration 11 の dp12 × N = 8 と Iteration 12 の dp12 × N = 1 × bk の遅れも同じ形だった可能性があるが，当時は質問者の名前空間の計数が無かった．SYN が落ちた場所（専門家ノードの listen の待ち行列，docker のブリッジの conntrack，スイッチ）は区別していない．lean が接続の生成の時機に関わるかは，lean は割り当ての結果を変えない（tests/test_pool_assign.py）ことと sk でも同じ形の遅れが過去に出ていることから，関わらないと読む．
- 事実 2 の dp12 の過大な見積もりは，事前の測定（ノードを同じ 12 コアのループバックで動かす）では dp12 の方が質問者以外の CPU の取り合いの影響を強く受けるためと読めるが，確かめていない．
- 事実 3 で N = 8 の節約が小さくなるのは，物理 6 コアを超える 8 プロセスでは 1 要求の CPU の多くが同じ物理コアの取り合い・カーネルの通信処理に移り，Python の層で減らせる割合が小さくなるためと読める（Iteration 12 の推測と同じ）．

**想定外の挙動**：事実 4（dp12 × N = 8 × ln の r1 の遅れ）．判定には H-P3（記録）でだけ影響した．言語崩れ・発散・OOM は該当なし．

**学び**
- 「N = 1 の処理量の比 ≈ 1 要求の質問者の CPU の比の逆数」は Iteration 11・12・13 の 3 回とも fs・dp12 の両方で成り立った．N = 1 の効果の見込みは「事前の測定の節約 × 実機への縮小の率」で出せるが，**縮小の率は fs で約 0.8，dp12 で約 0.6**（2 回続けて）と方式で違う．次の事前登録では dp12 の見込みにこの率を掛けてから閾値を決める．
- 質問者の CPU を削るレバーの効果は，Iteration 11 → 12 → 13 で fs × N = 1 の比 1.33 → 1.49 → 1.16，dp12 × N = 1 で 1.30 → 1.19 → 1.09 と小さくなっている．lean の後の残りで大きいのは h11 の送受信（HTTP クライアントの置き換えが要る）と待機中の接続ごとの `poll`（挙動を変えずには除けない）で，設定キーで除ける項目は `/v1/retrieve` の本文の符号化（dp12 で約 11%，fs で約 6%）と dp12 の routing の 1 段目（約 8%）が残る程度である．
- 質問者のコンテナの名前空間の TCP の計数（`requester_tcp`）は，N = 8 の約 1 秒の遅れと `TCPSynRetrans` の増加が同じ実行で起きることを初めて示した．1 回だけの観測で，因果は確かめていない．
- 制御点 1 台の質問者（N = 8）の処理量は Iteration 8 → 11 → 12 → 13 で dp12 186.2 → 242.6（遅れの無い回）→ 291.6 → 306.7（遅れの無い回の中央値）問/秒，fs 82.4 → 125.5 → 161.3 → 171.5 問/秒になった．

**限界**
- lean は httpcore 1.0.9・anyio 4.15.1 の非公開の内部への差し替えで，版を上げれば作り直しが要る（B36 (a)）．2 つの差し替え（掃除の段・生存の確かめ）の実機での個別の効果は分けて測っていない（事前の測定では節約の約 8 割が生存の確かめの近道）．
- dp12 × N = 8 の遅れの原因は記録が 1 回分しかなく推測である．
- 有線 LAN・検索だけ・閉じた系・制御点 1 台の質問者である．

**次の一手**（backlog B37 [auto-decided]）：lean の上で設定キーで除ける残りの最大の項目，`/v1/retrieve` の本文（768 次元の埋め込みの JSON．1 問に m = 2 回）を問ごとに 1 回だけ符号化する構成（Iteration 11 の `probe_body` と同じ形）．phase 1 で本物のノード 30 個の事前の測定（`prestudy/run_ctrl.sh`）を lean の上で取り直し，最大の除ける項目を確かめてから事前登録する．

## Iteration 12: 質問者の要求ごとの sniffio の import の失敗を除いたときの処理量

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B33 [auto-decided]（Iteration 11 は採用．質問者 1 プロセスの処理量は 1 要求あたりの質問者の CPU でほぼ決まり，事前の測定の節約が実機で再現した）．
config.yml の levers 末尾の `requester.sniffio_lookup: [skip]`（仮称）を，**config.yaml の新しいキー `experiment.sniffio_lookup`（`search`〔既定．今の挙動〕/ `blocked`）**として確定した（backlog B34）．依存（uv.lock・イメージ）は変えない．ノードの API・要求の本文・方式の選択・データ・シャード・`cluster`・`node_workers` は変えない．

**コードから確かめたこと**（事実．`.venv` の httpcore 1.0.9）
- `httpcore/_synchronization.py` の `current_async_library()` は，呼ばれるたびに `try: import sniffio except ImportError: environment = "asyncio"` を行う．質問者の環境（uv.lock，制御点の `atrium-full` のイメージ）には sniffio が無いので，失敗した import は `sys.modules` に残らず，毎回 `sys.path` の探索が走る（Iteration 11 の cProfile で 1 要求あたり約 9.5 回）．
- Python の import は，`sys.modules[name]` が `None` のとき探索をせずに `ModuleNotFoundError`（`ImportError` の子）を送出する．したがって質問者のプロセスの始めに `sys.modules["sniffio"] = None` を置けば，httpcore は同じ `except ImportError` の枝に入って `"asyncio"` を返し，挙動（返す値・例外の型）は今と同じで探索だけが省かれる．sniffio が実際に入っている環境では置かない（本物のモジュールを隠さない）．
- 質問者のプロセスは 2 通りの入口で動く：N = 1 は `atrium run` のプロセス（`src/atrium/experiment.py` の `run_experiment`），N ≥ 2 は spawn の子（`src/atrium/requester_procs.py` の `_worker_main`）．spawn の子は親の `sys.modules` を引き継がないので，両方の入口で置く必要がある．

**事前の測定**（gpu2．Iteration 11 の `bench.py`〔localhost の偽のノード 30 個，per_node の httpx，parallel 30，1 問 30 probe〕を 300 問 × 3 回．偽のノードは同じ CPU を使う．スクリプトと出力は `results/20261015_iter12_sniffio_summary/prestudy/`．判定に使わない）

| `current_async_library()` 1 回（20 万回の平均） | µs |
|---|---|
| 今の挙動（search．`sys.path` の探索） | 44.03 |
| `sys.modules["sniffio"] = None`（blocked） | 1.09 |
| sniffio を入れる（installed．uv のキャッシュの sniffio 1.3.1） | 0.41 |

| 質問者 1 プロセスの変種 | probe の要求/秒（3 回） | 中央値 | 1 要求の時間（ms） | search との比 |
|---|---|---|---|---|
| base（本文は per_request）× search | 473.1・470.5・465.1 | 470.5 | 2.125 | 1.00 |
| base × blocked | 666.3・670.5・657.4 | 666.3 | 1.501 | 1.42 |
| base × installed | 664.1・669.5・663.8 | 664.1 | 1.506 | 1.41 |
| pre（pre_encoded）× search | 595.0・574.0・597.2 | 595.0 | 1.681 | 1.00 |
| pre × blocked | 877.7・878.2・887.6 | 878.2 | 1.139 | 1.48 |
| pre × installed | 909.4・863.1（3 回目は httpx.ReadError で中断） | 886.3 | 1.128 | 1.49 |

- blocked と installed の差は 1 要求あたり 0.01 ms 以下（`current_async_library` 1 回の差 0.7 µs × 約 9.5 回）で，3 回の幅の内である．**探索を省く設定キー（blocked）で，依存を足すのと同じ節約が得られる**．
- pre の上での節約は gpu2 で 0.54 ms（1.681 → 1.139）．Iteration 11 の制御点の事前の測定（base の上で 0.63 ms）と同じ程度である．
- pre × installed の 3 回目で `httpx.ReadError` が 1 回出た（`bench.py` の pre の経路は再試行をしない）．Iteration 11 の制御点の事前の測定でも 1 回出ており，偽のノード（同じ CPU の 30 個の uvicorn）の側の接続の切断と読める（推測．sniffio の扱いとの関係を示す根拠は無い）．

**調査で分かったこと**
- tavily-search による Web 検索（httpcore の上流でこの費用が報告・修正されているか）は，この環境の権限の判定で拒否されたので行っていない．以下は手元のコードと測定だけによる．上流の httpcore の新しい版で `current_async_library` の結果をキャッシュする等の修正があるかは未確認である．
- Iteration 11 の journal に記録した外部の知見（httpx の同時の要求での CPU の重さ．encode/httpx #3215，現在は pydantic/httpx2 #827．T. Christie のコメント 2024-06-06）は，今回の費用（httpcore の同期の部品の生成ごとの非同期の環境の判定）と同じ層の話である．

**形の判断（B33 (a)）**：`uv add sniffio` ではなく設定キーにする．根拠：(1) 節約の大きさは同じ（上の表）．(2) 同じイメージで search と blocked を交互に流せ，Iteration 11 と同じ設計（組ごとの交互・前後の anchor）で時間の揺れを打ち消せる．依存の追加は前後の 2 つのかたまりの比較になり，連続実行の間に commit・イメージの作り直しが要る．
(3) 依存の追加は専門家ノード・node-bench の負荷生成（同じ httpx・httpcore）にも効き，質問者だけを変える単一レバーにならない．設定キーは質問者のプロセスだけに効く．`uv add sniffio` を既定の解決にするか（専門家ノードにも効く）は人間の判断に残す（B34 (a)）．

**問い**：(1) sniffio の探索を省くと，`pre_encoded` の上で実機の質問者 1 プロセスの処理量は事前の測定と同じ程度（約 1.4〜1.5 倍）伸びるか．(2) device_probe（D = 12）でも伸びるか．(3) N = 8 で律速が質問者から専門家ノード（W = 1）へ移るか，約 1 秒・3 秒の probe の遅れがどれだけ出るか．

**仮説**
- **H-S1（主）**：flood_score × N = 1 で，blocked の 3 回の処理量の範囲が search の範囲より上にあり重ならず，かつ中央値の比が 1.10 以上．仕組み：N = 1 は質問者のプロセスが 1 コアに張り付いて律速（Iteration 11 の per_proc 1.00）なので，1 要求あたりの CPU を約 0.55〜0.63 ms 減らせば，その分だけ処理量が伸びる．
- **H-S2**：device_probe（D = 12）× N = 1 で H-S1 と同じ規則が成り立つ．
- **H-S3（記録）**：N = 8 で fs・dp12 のそれぞれ H-S1 と同じ規則が成り立つ．

**単一レバー**：config.yaml の **`experiment.sniffio_lookup` を `search` → `blocked`** と変える．それ以外（方式・D・N・parallel・問・`http_pool: per_node`・`probe_body: pre_encoded`・ノードの W = 1・依存）は両方の値で同じにする．
- **実装**（phase 2 で行う．事前登録の commit に含める）
  - `src/atrium/config.py`：`ExperimentConfig.sniffio_lookup: Literal["search", "blocked"] = "search"`．`config.yaml` に `sniffio_lookup: search` を足す．
  - `src/atrium/requester.py`（または近い場所）に，`blocked` のとき `importlib.util.find_spec("sniffio") is None` かつ `"sniffio" not in sys.modules` の場合だけ `sys.modules["sniffio"] = None` を置く関数を 1 つ置き，`run_experiment` の質問者の client を作る前と，`requester_procs._worker_main` の始め（`asyncio.run` の前）で呼ぶ．理由のコメント（httpcore 1.0.9 の `current_async_library` が要求ごとに import を試みること）を付ける．
  - `src/atrium/experiment.py`：`run_meta.json` に `sniffio_lookup` と `sniffio_installed`（置く前の `find_spec` の結果．真なら探索の費用は元から無い）を残す．
  - テスト（`tests/test_sniffio_lookup.py` 等．`monkeypatch` で `sys.modules` を戻す）：既定値が `search` であること，`search` では `sys.modules` を変えないこと，`blocked` で sniffio が無いとき `current_async_library()` が `"asyncio"` を返し `sys.meta_path` の探索器が `sniffio` について呼ばれないこと（数える探索器を `sys.meta_path` の先頭に置いて確かめる），sniffio が `sys.modules` に既にあるときは置き換えないこと，実際のノードの ASGI アプリへ blocked で送った probe の応答が search と一致すること．
  - 文書：`docs/d0002_configuration.md`（`sniffio_lookup`），`docs/d0004_metrics.md`（`run_meta.json` の `sniffio_lookup`・`sniffio_installed`，下の遅れの指標・TCP の再送の差分）．
  - 連続実行：`results/20261015_iter12_sniffio_summary/` に `iter12_chain.sh`（`iter11_chain.sh` を写し，`sniffio_lookup` を `experiment` の直下のキーとして渡す．各実行の start の前後で制御点のホストの `nstat -asz TcpRetransSegs TcpExtTCPSynRetrans TcpExtTCPTimeouts` を記録する）・`iter12_specs.template.txt`・`iter12_smoke_specs.template.txt`・集計 `iter12_summary.py`（下の規則を機械的に出す．合成の結果で動作を確かめる）．

**固定する構成**：`dataset: enronqa`，`answer_mode: retrieval_only`，m = 2，`http_pool: per_node`，**`probe_body: pre_encoded`**（B33 (d)．Iteration 11 の採用の構成），parallel 30（全プロセスの合計），test の全 2,940 問（`question_limit: null`），**`node_workers: 1`**（B33 (c)．W を変えると単一レバーが崩れる），T4 以来の 30 台 × 5 受信箱の配置，各回の前に deploy し直す（Iteration 8・11 と同じ）．
連続実行の間は制御点で他のコンテナを動かさず commit もしない．依存は変えない（sniffio は入れない）．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 0：実機の動作確認**（連続実行の前に 1 回．判定に使わない）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．
2. `<date>_iter12_smoke_fs_bk`（`dataset=enronqa answer_mode=retrieval_only question_limit=2 http_pool=per_node probe_body=pre_encoded routing=flood_score parallel=30 requester_procs=1 sniffio_lookup=blocked`）を deploy → start → analyze で 1 回流す．続けて同じ条件で `requester_procs=8` を 1 回流す（spawn の子で置かれることの確認）．
3. 次を全て満たすことを確かめる．満たさなければ連続実行を流さず，原因を調べて記録する：失敗 0 問，300 問の `selected_sources` が `results/20261015_iter8_anchor_fs_n1_p30_post/results.jsonl` と 300/300 一致，`run_meta.json` の `sniffio_lookup` = blocked・`sniffio_installed` = false・`probe_body` = pre_encoded・`http_pool` = per_node・`requester_procs` が仕様どおり，`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない，1 要求あたりの質問者の CPU が Iteration 11 の fs × N = 1 × pe（1.761 ms）より小さい（記録．効いていることの粗い確認）．

**手順 1：実機の連続実行**（`iter12_specs.template.txt` の `<date>` を実行日にした `iter12_specs.txt` を `iter12_chain.sh` に**絶対パス**で渡す）
1. 環境の揺れの確認（前）：flood_score × N = 1 × parallel 30 × 300 問（`question_limit=2`）× `probe_body: per_request` × `sniffio_lookup: search` を 1 回（anchor_pre．Iteration 7〜11 の anchor と同じ条件）．
2. 主の系列：方式 {fs，dp12（`routing=device_probe routing.probe_d.enronqa=12`）} × N ∈ {1, 8} × `sniffio_lookup` ∈ {search（sr），blocked（bk）} × 3 回，2,940 問，全て `probe_body: pre_encoded`．
   各回の中は fs_n1 → fs_n8 → dp12_n1 → dp12_n8 の順に流し，各組で r1・r3 は sr → bk，r2 は bk → sr の順にする（時間の揺れを両方の値に均等に掛けるため）．
3. 環境の揺れの確認（後）：1 と同じ（anchor_post）．
- 計 26 実行（手順 0 を除く）．所要は約 2〜2.5 時間の見込み（Iteration 11 と同じ形）．deploy の失敗の扱い・失敗率の打ち切りは Iteration 11 と同じ（`iter12_chain.sh`）．

**指標**（Iteration 11 と同じ．`docs/d0004_metrics.md`）：処理量（成功した問の数 ÷（`finished_at` − `started_at`）），e2e_s・probe_s の p50・p95，失敗率，再試行の数，1 秒あたりの HTTP 要求の数，`requester_cpu_util`・`requester_cpu_util_per_proc` の中央値，`expert_cpu_util_max`（と最も忙しいノード），`control_cpu_busy`，2,940 問の hit@2，1 要求あたりの質問者の CPU（ms）．主の量は各条件の 3 回の処理量（最小〜中央値〜最大）．
**追加する記録の指標**（B33 (b)．判定に使わない）：
- **遅れの問の数**：`probe_s` ≥ 0.9 秒の問の数（全ての実行．Iteration 11 の約 1 秒・約 3 秒の遅れに当たる），そのうち `probe_node_s_max` < 0.5 秒の数（ノードの計算によらない遅れ）．
- **制御点の TCP の再送の差分**：start の前後の `TcpRetransSegs`・`TcpExtTCPSynRetrans`・`TcpExtTCPTimeouts`（ホストの名前空間で取れなければ「取れない」と記録し，判定には影響させない）．deploy の通信を含まないよう start の直前・直後に取る．
- **遅れの無い実行だけの中央値**（N = 8 のみ．遅れの問の数が 0 の実行の処理量の中央値．参考値）．

**正しさの確認**（満たさない実行は判定に使わず「欠け」とし，原因を調べて記録する）
- G1：失敗率が 1% 以下．
- G2（方式ごと）：その方式の全ての実行（sr・bk・N = 1・8）で，失敗の無い問の `selected_sources` が問ごとに 100% 一致する．成り立たなければその方式の判定に進まない（本文も要求も同じなので選択は変わらないはず）．
- G3：`run_meta.json` の `sniffio_lookup`・`probe_body` = pre_encoded・`requester_procs`・`http_pool` = per_node・routing・D・`n_questions` が仕様と一致し，`sniffio_installed` が全ての実行で false である（true なら search でも探索の費用が無く比較が成立しない）．
- G4（記録）：2,940 問の hit@2 が Iteration 8・11 の値（fs 0.8041，dp12 0.6830）と一致するか．
- G5（律速の読みだけに効く）：`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない．

**環境の揺れの規則**（記録のため）：anchor_pre・anchor_post の処理量が [11.127, 14.247] 問/秒，e2e p95 が [3.053, 4.067] 秒（Iteration 7〜11 と同じ範囲）に入るかを記録する．search の主の系列の値が Iteration 11 の pe の同じ条件の範囲（fs N = 1：17.689〜17.846，fs N = 8：124.220〜126.608，dp12 N = 1：37.752〜42.559，dp12 N = 8：172.702〜242.558 問/秒）から ±5% を超えて外れたら「環境が変わった」と記録する．
比べる相手は同じ連続実行の中の search なので，判定の基準は変えない．

**判定の規則**（比べる 2 条件の 3 回の範囲が重ならないときだけ「差がある」）
- **伸びた**（ある方式・N で sr → bk）：bk の 3 回の処理量の範囲が sr の範囲より上にあり重ならず，かつ中央値の比 bk ÷ sr が 1.10 以上．
- **H-S1（主）**：fs × N = 1 が「伸びた」．
- **H-S2**：dp12 × N = 1 が「伸びた」．
- **H-S3（記録）**：fs × N = 8・dp12 × N = 8 のそれぞれが「伸びた」（遅れの問の数を併記する）．
- **このイテレーションの結論**：
  - **棄却（測定不成立）**：G2 が fs で成り立たないか，G3 が 1 つでも不成立か，fs × N = 1 の sr・bk のいずれかで有効な実行が 2 回未満．原因を調べて記録する．
  - **採用**（以後の負荷の実験は `sniffio_lookup: blocked` を明示して流す．既定の値は変えない）：上が当てはまらず，H-S1 が成り立つ．
  - **部分的に支持**：H-S1 が成り立たないが，fs × N = 1 の bk の範囲が sr の範囲より上にある（比 < 1.10），または H-S2 が成り立つ．
  - **棄却（伸びない）**：どれでもない（事前の測定の節約が実機では効かない．実機の質問者の 1 要求の時間の内訳を py-spy で取り直す）．
- 記録（判定に使わない）：方式・N ごとの比，1 要求あたりの質問者の CPU の sr と bk の差（事前の測定の 0.54〜0.63 ms と比べる），N = 8 の E(8) の変化，`requester_cpu_util_per_proc` が 1 に張り付いたままか，e2e p95 の変化．
  **律速の移りの読み**（B33 (c)）：dp12 × N = 8 × bk で `requester_cpu_util_per_proc` の中央値が 0.9 未満かつ最も忙しいノードの `expert_cpu_util` が 1.58 コア（W = 1 の頭打ち約 1.86 コアの 0.85 倍）以上なら「律速が専門家ノードへ移った」と記録する．遅れの問の数が多い実行（≥ 10 問）は per_proc が下がる別の理由があるので，この読みに使わない．

**ノイズの見積もり**：Iteration 11 の pe の 3 回の処理量の幅は，N = 1 で中央値の −0.4%〜+0.5%（fs）・−9.8%〜+1.7%（dp12．37.75 の低い回を含む），N = 8 で −1.0%〜+0.9%（fs）・−13.8%〜+21.1%（dp12．遅れを含む）だった．閾値 1.10 は fs × N = 1 の幅の 10 倍以上で，dp12 × N = 1 の幅にも範囲が重ならない規則を併用すれば取り違えにくい．dp12 × N = 8 は遅れで大きく揺れるので記録だけにする（H-S3）．

**期待効果**（推測．判定に使わない）：1 要求あたりの節約が 0.54〜0.63 ms なら，fs × N = 1 は 1.761 → 1.13〜1.22 ms，処理量は約 17.8 → 25.5〜27.5 問/秒（比 1.44〜1.56）．dp12 × N = 1 は 1.711 → 1.08〜1.17 ms，約 41.8 → 61〜66 問/秒．
N = 8 は Iteration 11 で節約が N = 1 より大きく効いた（事実 3）ので，fs は 125.5 → 180〜200 問/秒の見込み．dp12 × N = 8 は最も忙しいノード（13.102）が約 1.5〜1.6 コアに達し，W = 1 の頭打ちに近づいて比が小さくなる見込み．事前の測定は gpu2 と制御点の偽のノードによるもので，実機の節約の大きさは前後しうる．

**限界**：有線 LAN・検索だけ・閉じた系・制御点 1 台の質問者．設定キーは探索を省くだけで，sniffio を入れたときの `sniffio.current_async_library()` の費用（1 回 0.4 µs）とは 1 要求あたり 0.01 ms 以下の差がある．依存の追加は扱わない（専門家ノード・node-bench の負荷生成の側の同じ費用は残る）．上流の httpcore の新しい版での扱いは調べられていない（Web 検索が拒否された）．

**次の候補**：採用なら，質問者の 1 要求の CPU の次の内訳（httpcore の接続の池の走査・h11 の組み立てと解析・`ProbeResponse.model_validate_json`）を cProfile で取り直し，最大の項目を単一レバーにする．dp12 × N = 8 で律速が専門家ノードへ移ったと読めたら，負荷の実験の `node_workers` の扱い（B31 (a)）を人間に確かめる．棄却なら，実機の質問者を py-spy で取り直して内訳を確かめる．

### 実装・実験（2026-10-15．research-cycle の phase 2）

- 実装 commit `dc58854`（push 済み．実験中の commit なし）：`experiment.sniffio_lookup`（`search`〔既定〕/ `blocked`）．`requester.apply_sniffio_lookup` を `run_experiment` と `requester_procs._worker_main` の 2 か所から呼ぶ．`run_meta.json` に `sniffio_lookup`・`sniffio_installed`．テスト 7 件を追加（pytest 321 件通過）．リポジトリ全体の ruff・mypy には今回と無関係の既存のエラーが残る．
- 手順 0（smoke．fs × blocked × 300 問，N = 1・8）：失敗 0，`selected_sources` が iter8 の anchor と 300/300 一致，`run_meta` は仕様どおり．N = 1 の 1 要求の質問者 CPU は 1.304 ms（iter11 の 1.761 ms より小）．N = 8 の spawn の子で実際に置かれたかは直接は確かめていない．
- 連続実行：26 実行を完走（20:44〜22:42）．deploy の失敗・打ち切り・スキップなし．結果は `results/20261015_iter12_*`，集計は `results/20261015_iter12_sniffio_summary/iter12_summary.out`．
- 正しさの確認：G1（失敗率 0）・G2（fs・dp12 とも 12 実行で `selected_sources` が 100% 一致）・G3（`sniffio_installed` は全て false）・G4（hit@2 は fs 0.8041・dp12 0.6830）・G5 が成立．anchor は前 13.342・後 12.141 問/秒で範囲内．search の中央値と Iteration 11 の pe の範囲 ±5% の比較では，fs × N = 8 だけが外れた（110.977 問/秒．範囲 124.22〜126.608）．
- 処理量（問/秒．search の中央値 → blocked の中央値，比．blocked の範囲はいずれも search より上で重ならない）：fs × N = 1 は 17.721 → 26.404（1.490），fs × N = 8 は 110.977 → 145.145（1.308），dp12 × N = 1 は 42.228 → 50.277（1.191），dp12 × N = 8 は 240.912 → 291.604（1.210）．
- 規則の機械的な出力：H-S1・H-S2 成立，H-S3（記録）は両方の N = 8 で成立．結論は「採用」．
- 記録：1 要求の質問者 CPU の差（search − blocked，ms）は fs N = 1 が 0.574，fs N = 8 が 0.413，dp12 N = 1 が 0.355，dp12 N = 8 が 0.386（事前の測定は 0.54〜0.63）．fs N = 1 の質問者 CPU は 1.762 → 1.189 ms．dp12 × N = 8 × blocked は 3 回とも「律速は専門家ノードへ移っていない」（per_proc 0.941〜1.002，`expert_cpu_util_max` 1.238〜1.301）．
- 遅れの問（probe_s ≥ 0.9 秒．ほぼ全て `probe_node_s_max` < 0.5 秒）：fs × N = 1 の search は 1759〜1833 問，blocked は 438〜561 問と多い．dp12 × N = 1 の blocked は 104・2・227．N = 8 は fs の search 30・1・16，blocked 0・34・11．遅れの無い N = 8 の実行だけの中央値は fs の blocked が 161.288（1 件），dp12 の blocked が 291.604（3 件）．TCP 再送は小さい（dp12 × N = 8 × blocked の `TcpRetransSegs` 4〜14，`TcpExtTCPTimeouts` 2〜5）．再試行は多くの実行で 0〜6，dp12 × N = 1 × blocked の r1 は 22・r3 は 15．
- 注意：上流の httpcore の扱いは未確認（Web 検索が拒否された）．未追跡の `iter7_chain.log` には触れていない．結果と `iter12_*` の成果物は未 commit（フェーズ 3 で行う）．解釈は評価フェーズ．

### Iteration 12 実行済み：分析・考察（2026-10-15．research-cycle の phase 3．判定：採用）

**変更**：config.yaml の `experiment.sniffio_lookup` を `search`（sr）→ `blocked`（bk）と変え，`probe_body: pre_encoded` を固定して fs・dp12 × N ∈ {1, 8} × 3 回（2,940 問，per_node，parallel 30，W = 1，検索だけ）を sr と bk を組ごとに交互に並べて流した（前後の anchor を含めて 26 実行．欠け 0）．事前登録の規則・閾値は結果を見て変えていない．

**正しさの確認と環境の揺れ**：G1（26 実行とも失敗 0）・G2（fs・dp12 とも 12 実行の `selected_sources` が全問一致）・G3（`sniffio_installed` は全て false）・G5 は成立し，G4（hit@2）は fs 0.8041・dp12 0.6830 で Iteration 8・11 と同じ．
前後の anchor は 13.342・12.141 問/秒（e2e p95 3.617・3.772 秒）で事前登録の範囲の内．sr の中央値は fs × N = 8 だけが Iteration 11 の pe の範囲の ±5% から外れた（110.977．下の事実 5 で扱う）．

**結果と判定**（処理量．問/秒．3 回の最小〜中央値〜最大）

| 条件 | sr | bk | 比（中央値） | bk の最小 ÷ sr の最大 | 1 要求の質問者の CPU（ms．sr → bk） | 遅れの問（sr / bk．probe_s ≥ 0.9 秒） |
|---|---|---|---|---|---|---|
| fs N = 1 | 17.554〜17.721〜17.780 | 24.850〜26.404〜26.617 | 1.490 | 1.398 | 1.762 → 1.189 | 1759・1833・1797 / 478・561・438（待ち行列．事実 3） |
| fs N = 8 | 104.700〜110.977〜124.356 | 138.133〜145.145〜161.288 | 1.308 | 1.111 | 1.900 → 1.487 | 30・1・16 / 0・34・11 |
| dp12 N = 1 | 42.068〜42.228〜43.072 | 44.376〜50.277〜54.251 | 1.191 | 1.030 | 1.695 → 1.340 | 13・20・13 / 104・2・227 |
| dp12 N = 8 | 205.206〜240.912〜247.831 | 276.325〜291.604〜294.681 | 1.210 | 1.115 | 2.268 → 1.882 | 2・1・1 / 0・0・0 |

- 規則の機械的な出力：H-S1（fs × N = 1）成立，H-S2（dp12 × N = 1）成立，H-S3（記録）は fs・dp12 × N = 8 とも成立．
- **このイテレーションの結論：採用**（G2・G3 成立，fs × N = 1 の有効な実行は sr・bk とも 3 回，H-S1 成立）．以後の負荷の実験は `sniffio_lookup: blocked` を `probe_body: pre_encoded` と併せて明示して流す．既定の値は `search` のまま．
- **ノイズか信号か**：fs × N = 1 の sr の 3 回の幅は中央値の −0.9%〜+0.3%（Iteration 11 の pe の −0.4%〜+0.5% と同じ程度）で，bk の最小と sr の最大の隙間は +39.8% ある．幅の 30 倍以上で，ノイズでは説明できない．
  dp12 × N = 1 は規則を満たすが，隙間は +3.0% しかなく，bk の幅（−11.7%〜+7.9%）は下の事実 4 の遅れによる．H-S2 の成立は「規則の上では成立，余裕は小さい」と読む．

**分析**

事実（記録から計算したもの）
1. **N = 1 の処理量の比は，1 要求あたりの質問者の CPU の比の逆数とほぼ一致する**（Iteration 11 の事実 1 と同じ）．fs × N = 1 は 1.762 ÷ 1.189 = 1.482 に対して処理量の比 1.490．`requester_cpu_util_per_proc` は sr・bk とも 0.99〜1.01 で，律速は質問者のプロセスのままである．dp12 × N = 1 の遅れの少ない r2（遅れ 2 問）だけで比べると，CPU の比 1.695 ÷ 1.328 = 1.276 に対して処理量の比 54.251 ÷ 42.228 = 1.285 で同じく一致する．
2. **実機の節約の大きさは fs では事前の測定と合い，dp12 では小さい**．fs × N = 1 の 1 要求あたりの節約は 0.574 ms（事前の測定：gpu2 の pre の上で 0.54 ms，Iteration 11 の制御点の base の上で 0.63 ms）．dp12 × N = 1 は 0.355 ms（r2 だけで 0.367 ms）で約 6 割である．
   sniffio の探索は probe・retrieve のどちらの要求でも起きるので，Iteration 11 のように「変えたのは probe だけ」という説明は使えない．節約の差の理由は未確認である（推測は下）．
3. **fs × N = 1 の「遅れの問」の数（sr 約 1,800 問，bk 約 500 問）は，TCP の遅れではなく質問者の CPU の待ち行列による**．fs × N = 1 の probe_s の中央値は sr 0.998〜1.037 秒，bk 0.667〜0.698 秒，p95 は sr 1.64〜1.68 秒・bk 1.04〜1.17 秒で，0.9 秒の閾値は分布の本体にかかっている（anchor も 300 問のうち 254・255 問）．
   質問者が 1 コアに張り付いた N = 1 で 30 問を同時に流すと，1 問の probe の 30 要求が他の 29 問の要求と CPU を取り合うので，probe_s は 1 問の CPU の約 30 倍になる．bk で数が減ったのは，1 要求の CPU が減って待ち行列が短くなったためで，遅れの原因が減ったのではない．
   **この指標（probe_s ≥ 0.9 秒）は probe_s の中央値が 0.1 秒前後の N = 8 でだけ意味がある**．N = 1 の値は判定・解釈に使わない（事前登録はこの区別をしていなかった）．
4. **dp12 × N = 1 × bk の r1・r3 は，本当の遅れと再試行で処理量が下がった**．probe_s の中央値が 0.28〜0.33 秒の中で 0.9 秒以上の問が 104・227 問あり，再試行が 22・15 回（他の N = 1 の実行は 0〜6 回），新しい接続の数が 652・736（r2 は 453，sr は 460〜507）と多く，`requester_cpu_util_per_proc` が 0.946・0.894 に下がった．再試行は `httpx.ReadError`・`RemoteProtocolError`・`ConnectError` のどれかで起きる（`post_with_retry`）が，requester.log に例外の型が残らないので区別できない．
   同じ bk でも fs × N = 1 の再試行は 1〜3 回（sr 4〜6 回）で，bk に結び付ける根拠は無い．
5. **fs × N = 8 の sr が「環境が変わった」と出たのは，遅れの問を含む回による**．sr の 3 回のうち遅れが 1 問だけの r2（124.356，per_proc 1.000）は Iteration 11 の pe の範囲（124.22〜126.608）の内にあり，遅れの 30・16 問の r1・r3（104.7・111.0，per_proc 0.819・0.908）が中央値を下げた．基準の水準そのものが変わった根拠は無い．
   遅れの少ない回どうし（sr r2 124.356 対 bk r1 161.288．bk r1 は遅れ 0 問）で比べると比は 1.297 で，CPU の比 1.900 ÷ 1.487 = 1.278 と合う．
6. **N = 8 の節約は N = 1 より小さく，E(8) は fs で下がった**（Iteration 11 と逆）．1 要求の CPU の N = 1 → 8 の増え方は，fs で sr +7.8%（1.762 → 1.900）・bk +25%（1.189 → 1.487），dp12 で sr +34%・bk +40%．遅れの少ない回で計算した fs の E(8) は sr 0.877 → bk 0.764 で，bk の N = 8 の伸びは N = 1 の伸び（1.490）より小さい（1.30）．
7. **dp12 × N = 8 × bk でも律速は専門家ノードへ移っていない**．bk の 3 回は遅れ 0 問・per_proc 0.941〜1.002・最も忙しいノードの `expert_cpu_util` 1.238〜1.301 コア（閾値 1.58 未満．W = 1 の頭打ち約 1.86 コアの約 7 割）．処理量 291.6 問/秒は Iteration 8 の N = 8（186.2）の 1.57 倍，Iteration 11 の pe の遅れの無い回（242.6）の 1.20 倍で，全体で約 4,080 要求/秒になった．
8. **期待効果との比較**：fs × N = 1 は 25.5〜27.5 の見込みに対して 26.40（内），dp12 × N = 1 は 61〜66 に対して 50.28（下．事実 2・4），fs × N = 8 は 180〜200 に対して 145.1（遅れの無い回で 161.3．下．事実 6），dp12 × N = 8 は「律速が専門家ノードへ移って比が小さくなる」見込みに対して，移らずに 1.21（事実 7）．
9. **N = 8 の spawn の子で `sys.modules["sniffio"] = None` が置かれたことは，間接にだけ確かめた**．N = 8 の要求は全て spawn の子が送り，bk の 1 要求の CPU は fs で 0.413 ms，dp12 で 0.386 ms 低く，処理量の範囲も sr と重ならない．子で置かれていなければ sr と bk は同じ経路になり，この差は出ない．子の中で直接確かめた記録（ログ等）は無い．
10. **制御点の TCP の再送の差分は，質問者の通信を測れていない可能性が高い**．`docker/compose.requester.yml` に `network_mode` の指定が無く，質問者のコンテナは既定のブリッジのネットワーク（ホストと別のネットワークの名前空間）で動く．Linux の TCP の統計（`nstat` が読む `/proc/net/snmp`・`netstat`）はネットワークの名前空間ごとなので，`iter12_chain.sh` がホストで取った値には質問者のソケットの再送が入らない．
    実際に，遅れの問の数と再送の数は対応しない（dp12 × N = 8 × bk は遅れ 0 問で `TcpExtTCPTimeouts` 2〜5，fs_n8_bk_r2 は遅れ 34 問で 1，fs_n1_sr_r1 は遅れ 1,759 問〔待ち行列〕で 0）．B33 (b) の「TCP の再送」の仮説はこの記録では確かめも否定もできていない．

推測（上の事実と矛盾しないが，未確認のもの）
- 事実 2（dp12 の節約が fs の約 6 割）は，httpcore が 1 要求のうちに `current_async_library()` を呼ぶ回数（同期の部品〔Lock・Event・Semaphore〕を作る回数）が，接続の池の状態によって変わるためと読める．fs は 1 問で全 30 台へ一斉に送るので池の待ちが起きやすく，dp12 は宛先が分散して待ちの部品を作る回数が少ない，という形である．回数は数えていない．
- 事実 6 は，N = 8（物理 6 コアを超える）で処理量が上がるほど，同じ物理コアの取り合いやカーネルの通信処理（制御点の `control_cpu_busy` は bk で 0.59〜0.69）の割合が増え，1 要求の CPU が増えると読める．sniffio の探索（`stat` のシステムコール）を除いた残りが物理コアの取り合いに敏感な処理で占められる，という読みもありうるが区別していない．
- 事実 4 の再試行は，ノードが keep-alive の接続を閉じた直後に質問者が同じ接続で送った競合（`RemoteProtocolError`・`ReadError`）と読める（新しい接続の数が多いことと合う）．bk で 1 要求が速くなり，接続の再利用の間隔が変わったことが関わるかは分からない．

**想定外の挙動**：上の事実 3（N = 1 で遅れの指標が待ち行列を数えていた）と事実 10（TCP の再送の記録が質問者の名前空間を見ていない）は，事前登録の記録の指標の設計の誤りで，判定には影響しない．言語崩れ・発散・OOM は該当なし．

**学び**
- 質問者の 1 要求の CPU を減らすレバーは，N = 1 では「処理量の比 ≈ 1 要求の CPU の比の逆数」が 2 回続けて成り立った（Iteration 11・12）．以後も N = 1 の効果は事前の測定（偽のノード 30 個）で見積もれる．ただし同じ費用でも方式によって 1 要求あたりの節約が違いうる（fs 0.574 対 dp12 0.355 ms）ので，事前の測定は fs と dp12 の両方の要求の形で取る．
- N = 8 の効果は N = 1 の比からは見積もれない．Iteration 11 は N = 8 の方が大きく効き（1.53 対 1.33），今回は小さく効いた（1.31 対 1.49）．N = 8 の値は実機で測るしかない．
- 「遅れの問」の指標は probe_s の中央値に比べて閾値が十分に大きい条件（N = 8）でだけ使う．N = 1 では待ち行列で probe_s が 1 秒前後になるので，閾値を相対（例えばその実行の probe_s の中央値の 5 倍）にするか，N = 8 だけで数える．
- 質問者は既定のブリッジのネットワークで動くので，TCP の再送はホストではなく質問者のコンテナのネットワークの名前空間（`docker exec <requester> cat /proc/net/netstat` 等，または `nsenter -t <pid> -n nstat`）で取る必要がある．
- 制御点 1 台の質問者（N = 8）の処理量は Iteration 8 → 11 → 12 で dp12 186.2 → 242.6（遅れの無い回）→ 291.6 問/秒，fs 82.4 → 125.5 → 161.3（遅れの無い回）問/秒になった．最も忙しい専門家ノードは dp12 で約 1.3 コアで，W = 1 の頭打ち（約 1.86 コア）までまだ約 1.4 倍の余裕がある．

**限界**
- 設定キーは質問者のプロセスの探索を省くだけで，専門家ノード・node-bench の負荷生成の側の同じ費用は残る．`uv add sniffio` を既定にするか（B34 (a)）は人間の判断に残したまま．上流の httpcore の新しい版での扱いは，Web 検索が権限で拒否されたので確かめていない．
- 再試行の例外の型・遅れの原因は記録が無く，dp12 × N = 1 × bk の幅の理由は推測である．
- 有線 LAN・検索だけ・閉じた系・制御点 1 台の質問者である．

**次の一手**（backlog B35 [auto-decided]）：Iteration 11 の cProfile で sniffio の次に上位だった費用（httpcore の接続の池の走査 `_assign_requests_to_connections`，h11 の組み立てと解析，anyio，`ProbeResponse.model_validate_json`）を，pre_encoded × blocked の上で制御点の事前の測定（fs と dp12 の両方の要求の形）で取り直し，最大の項目を 1 つ除く単一レバーにする．同時に，遅れの指標を N = 8 だけに限り，TCP の再送を質問者のコンテナの名前空間で取るよう記録の指標を直す．

## Iteration 11: 質問者の probe の本文を問ごとに 1 回だけ符号化したときの処理量

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B31 [auto-decided]（Iteration 10 は採用．専門家ノードの余裕は W = 1 でも制御点 1 台の質問者の約 3.8 倍あり，システム全体の律速は質問者の 1 要求あたりの CPU）．
config.yml の levers 末尾の `requester.probe_body: [pre_encoded]`（仮称）を，**config.yaml の新しいキー `experiment.probe_body`（`per_request`〔既定．今の挙動〕/ `pre_encoded`）**として確定した（backlog B32）．ノードの API・要求の意味・方式の選択・データ・シャード・`cluster` は変えない．質問者を複数のデバイスに置く形（B25 (a)）は扱わない．

**コードから確かめたこと**（事実）
- `src/atrium/requester.py` の `Requester._probe` の `ask` は，宛先のノードごとに `ProbeRequest(shard_ids=..., embedding=embeddings[encoder].tolist())` を作り，`post_with_retry` が `client.post(url, json=req.model_dump())` で送る．1 問の flood_score では同じ 768 次元の埋め込みを 30 回 `tolist` → pydantic の検証 → `model_dump` → JSON にしている．
- httpx 0.28.1 の `json=` は `json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")` で本文を作る（`.venv/.../httpx/_content.py` の `encode_json`）．同じ区切り・同じキーの順（`shard_ids`，`embedding`）で bytes を組めば，ノードが受け取る本文は**バイト単位で今と同じ**になる．
- `retrieval_only` の 1 問は probe（fs で 30 台，dp12 で約 12 台）と `/v1/retrieve`（m = 2）からなる．今回は probe だけを変える（retrieve の 2 要求は変えない）．
- 質問者の環境（uv.lock・制御点の `atrium-full` のイメージ）には `sniffio` が入っていない．httpcore 1.0.9 の `current_async_library`（`httpcore/_synchronization.py`）は呼ばれるたびに `import sniffio` を試み，失敗した import は `sys.modules` に残らないので，毎回 `sys.path` の探索（`find_spec`・`stat`）が走る．anyio 4.15.1 は sniffio に依存しない．

**事前の測定**（B31 の指示．制御点 wafl-ctrl5 の `atrium-full` のコンテナで，localhost の偽のノード 30 個〔受け取った `ProbeRequest` を検証して固定の得点を返す FastAPI〕へ，質問者と同じ `per_node` の httpx と `post_with_retry`・`ProbeResponse.model_validate_json` で，parallel 30・1 問 30 probe を 300 問×3 回．偽のノードは同じ 12 コアを使う．
スクリプトと出力は `results/20261015_iter11_probe_body_summary/prestudy/`．判定に使わない）

| 質問者 1 プロセスの変種 | probe の要求/秒（3 回） | 中央値 | 1 要求の時間（ms） | 比 |
|---|---|---|---|---|
| 今の挙動（base） | 376.4・381.8・384.2 | 381.8 | 2.62 | 1.00 |
| 本文を問ごとに 1 回だけ符号化（pre） | 471.4・484.5・497.4 | 484.5 | 2.06 | 1.27 |
| httpx の INFO のログを止める（base_quiet） | 385.7・387.9・388.6 | 387.9 | 2.58 | 1.02 |
| sniffio を入れる（base + sniffio．記録） | 497.1・501.9・504.6 | 501.9 | 1.99 | 1.31 |
| pre + sniffio（記録．2 つのレバー） | 687.5・703.9・704.5 | 703.9 | 1.42 | 1.84 |

- 1 要求あたりの内訳（今の挙動 2.62 ms に対して）：本文の符号化 約 0.55 ms（21%），sniffio の import の失敗 約 0.63 ms（24%），httpx の INFO のログ 約 0.04 ms（2%）．2 つの節約はほぼ足し算で効く（2.62 − 0.55 − 0.63 = 1.44 ms の予測に対して pre + sniffio は 1.42 ms）．
- 操作端末 gpu2（論理 64 CPU）でも同じ順位だった（base 457〜476，pre 567〜582，sniffio 648〜656，pre + sniffio 875〜887 要求/秒）．cProfile では `json/encoder.py` の `iterencode` が tottime の 1 位，`_find_and_load`（`current_async_library` から 1 要求あたり約 9.5 回）・httpcore の `_assign_requests_to_connections`（接続の池の走査）が続く．
- **レバーの選び直しの判断**：B31 は「本文の符号化の割合が小さければ最大の項目に選び直してよい」とした．本文の符号化は 21% で小さくないので，**レバーは予定どおり本文の符号化**とする．sniffio の欠落（24%．1 位だが差は 3 ポイント）は次の候補として backlog B32 に記録する．ログの水準は 2% で候補から外す．
- 制御点の最初の試行では，6 回の測定のどこかで `httpx.ReadError` が 1 回出た（出力が切れており，どの変種かは不明．偽のノードの起動を 20 秒しか待っていないことによる可能性．推測）．2 回目の試行（表の値）は失敗 0．

**調査で分かったこと**（tavily-search・tavily-extract．事実と推測を分けて書く）
- httpx の `AsyncClient` は同時の要求が多いと aiohttp より大幅に遅いことが報告されており（20 並列で 10 倍以上の差．localhost の小さな GET．M. Sintonen, encode/httpx issue #3215, 2024．現在は pydantic/httpx2 issue #827 に移されている．https://github.com/pydantic/httpx2/issues/827 ），
  保守者は原因の候補に h11 による HTTP の組み立てと解析の CPU，anyio を介した非同期の層の CPU を挙げている（T. Christie の同 issue のコメント，2024-06-06）．今回の cProfile で httpcore の接続の池の走査と anyio の `extra_attributes` が上位に出たことと矛盾しない．HTTP クライアントの置き換え（aiohttp 等）は 1 要求の CPU をさらに下げうる候補だが，接続の池・再試行・`per_node` の作り直しを伴うので今回は扱わない．
- 標準の `json` は他の実装より遅く，twitter.json の直列化で orjson 0.11 ms に対して json 1.36 ms という比較がある（ijl/orjson の README．https://github.com/ijl/orjson ）．orjson は numpy の配列を直接直列化できる．ただし float の文字列の表現が標準の `json` と一致する保証はなく，ノードが受け取る数値が変わりうる（推測．確かめていない）ので，今回は**標準の `json` のまま回数だけを減らし**，本文をバイト単位で今と同じに保つ．
- 1 回の符号化の結果を宛先ごとに使い回すのは，埋め込みが問ごとに 1 つで宛先によって変わらないので，結果を変えない置き換えである（Iteration 6 の重心の前計算と同じ型）．

**問い**：(1) 本文の符号化を問ごと（検索器ごと）に 1 回にすると，実機（30 台・2,940 問）の質問者 1 プロセスの処理量は事前の測定と同じ程度（約 1.2〜1.3 倍）伸びるか．(2) 1 問あたりの probe の数が少ない device_probe（D = 12）でも伸びるか．(3) N = 8（物理コアを超える）でも同じ比で伸びるか．

**仮説**
- **H-B1（主）**：flood_score × N = 1 で，`pre_encoded` の 3 回の処理量の範囲が `per_request` の範囲より上にあり重ならず，かつ中央値の比が 1.10 以上．仕組み：N = 1 は質問者のプロセスが 1 コアに張り付いて律速（Iteration 8 の per_proc 1.00）なので，1 要求あたりの CPU を約 0.55 ms 減らせば，その分だけ処理量が伸びる．
- **H-B2**：device_probe（D = 12）× N = 1 で H-B1 と同じ規則が成り立つ．
- **H-B3（記録）**：N = 8 で fs・dp12 のそれぞれ H-B1 と同じ規則が成り立つ．

**単一レバー**：config.yaml の **`experiment.probe_body` を `per_request` → `pre_encoded`** と変える．それ以外（方式・D・N・parallel・問・`http_pool`・ノードの W）は両方の値で同じにする．
- **キーを設定キーにした理由**：Iteration 6（重心の前計算）はコードの置き換えにしたが，今回は事前の測定のとおり sniffio の欠落という別の費用が同じ経路にあり，次のイテレーションで同じ基準（`per_request`）と比べ直す可能性が高い．設定キーなら同じイメージで両方を流せ，連続実行の中で交互に並べて時間の揺れを打ち消せる．
- **実装**（phase 2 で行う．事前登録の commit に含める）
  - `src/atrium/config.py`：`ExperimentConfig.probe_body: Literal["per_request", "pre_encoded"] = "per_request"`．`config.yaml` に `probe_body: per_request` を足す．
  - `src/atrium/requester.py`：`_probe` の始めに，`pre_encoded` なら宛先のノードが使う検索器ごとに `json.dumps(embeddings[encoder].tolist(), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()` を 1 回だけ作り，`ask` の中ではノードの `shard_ids` の JSON（同じ区切り）と連結して `b'{"shard_ids":' + … + b',"embedding":' + … + b'}'` を作る．
    `post_with_retry` は本文に `dict` のほかに `bytes` を受け取れるようにし，`bytes` なら `content=` と `Content-Type: application/json` で送る（再試行の挙動は同じ）．`per_request` の経路は変えない．`/v1/retrieve` と `/v1/answer` は変えない．
  - `src/atrium/experiment.py`：`run_meta.json` に `probe_body` を残す．
  - テスト（`tests/test_probe_body.py` 等）：同じ埋め込み・`shard_ids` で，`pre_encoded` の本文が `httpx` の `json=req.model_dump()` が作る本文と**バイト単位で一致**すること（複数の `shard_ids`・空でない 768 次元・負の値・非常に小さい値を含む），NaN を含む埋め込みは両方の経路で例外になること，既定値が `per_request` であること，
    実際のノードの ASGI アプリへ `pre_encoded` で送った probe の得点が `per_request` と一致すること．
  - 文書：`docs/d0002_configuration.md`（`probe_body`），`docs/d0004_metrics.md`（`run_meta.json` の `probe_body`）．
  - 連続実行：`results/20261015_iter11_probe_body_summary/` に `iter11_chain.sh`（`iter8_chain.sh` を写し，`probe_body` を `experiment` の直下のキーとして渡す）・`iter11_specs.template.txt`・`iter11_smoke_specs.template.txt`・集計 `iter11_summary.py`（下の規則を機械的に出す．合成の結果で動作を確かめる）．

**固定する構成**：`dataset: enronqa`，`answer_mode: retrieval_only`，m = 2，`http_pool: per_node`，parallel 30（全プロセスの合計），test の全 2,940 問（`question_limit: null`），`node_workers: 1`（deploy の既定），T4 以来の 30 台 × 5 受信箱の配置，各回の前に deploy し直す（Iteration 8 と同じ）．
連続実行の間は制御点で他のコンテナを動かさず commit もしない．sniffio は入れない（今の環境のまま）．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 0：実機の動作確認**（連続実行の前に 1 回．判定に使わない）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．
2. `<date>_iter11_smoke_fs_pe`（`dataset=enronqa answer_mode=retrieval_only question_limit=2 http_pool=per_node routing=flood_score parallel=30 requester_procs=1 probe_body=pre_encoded`）を deploy → start → analyze で 1 回流す．
3. 次を全て満たすことを確かめる．満たさなければ連続実行を流さず，原因を調べて記録する：失敗 0 問，300 問の `selected_sources` が `results/20261015_iter8_anchor_fs_n1_p30_post/results.jsonl` と 300/300 一致，`run_meta.json` の `probe_body` = pre_encoded・`http_pool` = per_node・`requester_procs` = 1，`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない．

**手順 1：実機の連続実行**（`iter11_specs.template.txt` の `<date>` を実行日にした `iter11_specs.txt` を `iter11_chain.sh` に**絶対パス**で渡す）
1. 環境の揺れの確認（前）：flood_score × N = 1 × parallel 30 × 300 問（`question_limit=2`）× `per_request` を 1 回（anchor_pre）．
2. 主の系列：方式 {fs，dp12（`routing=device_probe routing.probe_d.enronqa=12`）} × N ∈ {1, 8} × `probe_body` ∈ {per_request（pr），pre_encoded（pe）} × 3 回，2,940 問．
   各回の中は fs_n1 → fs_n8 → dp12_n1 → dp12_n8 の順に流し，各組で r1・r3 は pr → pe，r2 は pe → pr の順にする（時間の揺れを両方の値に均等に掛けるため）．
3. 環境の揺れの確認（後）：1 と同じ（anchor_post）．
- 計 26 実行（手順 0 を除く）．所要は約 2〜2.5 時間の見込み（Iteration 8 の 1 実行あたりの deploy などの約 3.8 分 × 26 と，流す時間の合計約 30 分）．deploy の失敗の扱い・失敗率の打ち切りは Iteration 8 と同じ（`iter11_chain.sh`）．

**指標**（Iteration 8 と同じ．`docs/d0004_metrics.md`）：処理量（成功した問の数 ÷（`finished_at` − `started_at`）），e2e_s・probe_s の p50・p95，失敗率，再試行の数，1 秒あたりの HTTP 要求の数，`requester_cpu_util`・`requester_cpu_util_per_proc` の中央値，`expert_cpu_util_max`，`control_cpu_busy`，2,940 問の hit@2．
加えて **1 要求あたりの質問者の CPU**（`requester_cpu_s` ÷ HTTP 要求の数．ms）を記録する．主の量は各条件の 3 回の処理量（最小〜中央値〜最大）．

**正しさの確認**（満たさない実行は判定に使わず「欠け」とし，原因を調べて記録する）
- G1：失敗率が 1% 以下．
- G2（方式ごと）：その方式の全ての実行（pr・pe・N = 1・8）で，失敗の無い問の `selected_sources` が問ごとに 100% 一致する．成り立たなければその方式の判定に進まない（本文がバイト単位で同じなら選択は変わらないはず）．
- G3：`run_meta.json` の `probe_body`・`requester_procs`・`http_pool` = per_node・routing・D・`n_questions` が仕様と一致する．
- G4（記録）：2,940 問の hit@2 が Iteration 8 の値（fs 0.8041，dp12 0.6830）と一致するか．
- G5（律速の読みだけに効く）：`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない．

**環境の揺れの規則**（記録のため）：anchor_pre・anchor_post の処理量が [11.127, 14.247] 問/秒，e2e p95 が [3.053, 4.067] 秒（Iteration 7〜10 と同じ範囲）に入るかを記録する．`per_request` の主の系列の値が Iteration 8 の同じ条件の範囲（fs N = 1：13.286〜13.574，fs N = 8：78.11〜84.95，dp12 N = 1：32.22〜32.82，dp12 N = 8：178.3〜186.7 問/秒）から ±5% を超えて外れたら「環境が変わった」と記録する．
比べる相手は同じ連続実行の中の `per_request` なので，判定の基準は変えない．

**判定の規則**（比べる 2 条件の 3 回の範囲が重ならないときだけ「差がある」）
- **伸びた**（ある方式・N で pr → pe）：pe の 3 回の処理量の範囲が pr の範囲より上にあり重ならず，かつ中央値の比 pe ÷ pr が 1.10 以上．
- **H-B1（主）**：fs × N = 1 が「伸びた」．
- **H-B2**：dp12 × N = 1 が「伸びた」．
- **H-B3（記録）**：fs × N = 8・dp12 × N = 8 のそれぞれが「伸びた」．
- **このイテレーションの結論**：
  - **棄却（測定不成立）**：G2 が fs で成り立たないか，G3 が 1 つでも不成立か，fs × N = 1 の pr・pe のいずれかで有効な実行が 2 回未満．原因を調べて記録する．
  - **採用**（以後の負荷の実験は `probe_body: pre_encoded` を明示して流す．既定の値は変えない）：上が当てはまらず，H-B1 が成り立つ．
  - **部分的に支持**：H-B1 が成り立たないが，fs × N = 1 の pe の範囲が pr の範囲より上にある（比 < 1.10），または H-B2 が成り立つ．
  - **棄却（伸びない）**：どれでもない（事前の測定の節約が実機では効かない．実機の質問者の 1 要求の時間の内訳を py-spy で取り直す）．
- 記録（判定に使わない）：方式・N ごとの比，1 要求あたりの質問者の CPU の pr と pe の差（事前の測定の約 0.55 ms と比べる），N = 8 の E(8) の変化，`requester_cpu_util_per_proc` が 1 に張り付いたままか（律速が質問者のままか），e2e p95 の変化，`expert_cpu_util_max`．

**ノイズの見積もり**：Iteration 8 の 3 回の処理量の幅は，N = 1 で中央値の −1.4%〜+0.7%（fs）・−1.2%〜+0.6%（dp12），N = 8 で −5.1%〜+3.2%（fs）・−4.2%〜+0.3%（dp12）だった．閾値 1.10 は N = 1 の幅の約 5 倍で，N = 8 の幅でも範囲が重ならない規則を併用すれば取り違えにくい．

**期待効果**（推測．判定に使わない）：事前の測定の 1 要求あたり約 0.55 ms の節約が実機でも同じなら，fs × N = 1 は 1 問 約 74.2 ms のうち probe 30 要求分の 16.5 ms が減り，約 13.5 → 17.0〜17.5 問/秒（比 1.25〜1.30）．dp12 × N = 1 は probe 約 12 要求分の 6.6 ms が減り，約 32.6 → 40〜42 問/秒（比 1.23〜1.28）．
N = 8 は物理 6 コアを超えるので比は同じか少し小さい見込み（fs 95〜105，dp12 210〜235 問/秒）．事前の測定は偽のノードが同じ 12 コアを使い，ループバックで測ったものなので，実機の節約の大きさは前後しうる．

**限界**：有線 LAN・検索だけ・閉じた系．変えるのは probe の本文だけで，`/v1/retrieve`（1 問 2 要求）の本文は今の作り方のままである．標準の `json` のまま回数を減らすだけで，直列化の速い実装（orjson）・HTTP クライアントの置き換え・sniffio の欠落は扱わない．専門家ノードの側の JSON の解析の費用は変わらない．

**次の候補**：採用なら，事前の測定で次に大きい sniffio の欠落（約 24%）を単一レバーにする（B32）．その後も質問者が律速なら，httpcore の接続の池の走査・h11 の CPU を減らす HTTP クライアントの置き換えを調べる．棄却なら，実機の質問者を py-spy で取り直して内訳を確かめる．

### 実験の実行（2026-10-15．research-cycle の phase 2）

全 26 実行が完了した（chain は 20:09:30 に ALL DONE）．26 実行すべてで start・inner・analyze の終了コードが 0，failure_rate 0.0，再試行の行なし．実験後の config.yaml に差分なし．
結果は `results/20261015_iter11_probe_body_summary/iter11_summary.out`．なお `atrium metrics --json` は最新の results である iter9_smoke_anchor を返すため，iter11 の値は iter11_summary.py の出力を使う．

事前登録の規則の機械的な出力（3 回の中央値，問/秒）：G1・G3・G5 は 26 実行すべてで成立，G2（selected_sources の一致率）は fs・dp12 とも最小 1.0000，G4（hit@2）は fs 0.8041・dp12 0.683 で Iteration 8 と 5e-4 以内．前後の anchor は 13.223・12.827 で Iteration 8 の範囲内．

| 条件 | pr | pe | 比 | pe の範囲は pr と |
|---|---|---|---|---|
| fs N=1 | 13.387 | 17.756 | 1.326 | 重ならない（H-B1 成立） |
| fs N=8 | 81.835 | 125.510 | 1.534 | 重ならない |
| dp12 N=1 | 32.181 | 41.844 | 1.300 | 重ならない（H-B2 成立） |
| dp12 N=8 | 185.561 | 200.262 | 1.079 | 重なる（H-B3 のうち不成立） |

機械的な結論は「採用」．記録：1 要求あたりの質問者 CPU（ms，pr → pe）は fs N=1 で 2.335 → 1.761，dp12 N=1 で 2.193 → 1.711．dp12 N=8 の pe は 172.7・200.3・242.6 とばらつき，expert_cpu_util_max が 1.037 に達した回がある．

### Iteration 11 実行済み（2026-10-15．research-cycle の phase 3．判定：採用）

**変更**：config.yaml の `experiment.probe_body` を `per_request`（pr）→ `pre_encoded`（pe）と変え，fs・dp12 × N ∈ {1, 8} × 3 回（2,940 問，per_node，parallel 30，検索だけ）を pr と pe を組ごとに交互に並べて流した（前後の anchor を含めて 26 実行）．事前登録の規則・閾値は結果を見て変えていない．

**正しさの確認と環境の揺れ**：G1（26 実行とも失敗 0）・G2（fs・dp12 とも 12 実行の `selected_sources` が全問一致）・G3・G5 は成立し，G4（hit@2）は fs 0.8041・dp12 0.6830 で Iteration 8 と同じ．
前後の anchor は 13.223・12.827 問/秒（e2e p95 3.826・3.520 秒）で事前登録の範囲の内．pr の主の系列の中央値は 4 条件とも Iteration 8 の同じ条件の範囲の ±5% の内で，環境は変わっていない．

**結果と判定**（処理量．問/秒．3 回の最小〜中央値〜最大）

| 条件 | pr | pe | 比（中央値） | 範囲 | 1 要求の質問者の CPU（ms．pr → pe） | E(8)（pr → pe） |
|---|---|---|---|---|---|---|
| fs N = 1 | 13.272〜13.387〜13.422 | 17.689〜17.756〜17.846 | 1.326 | 重ならない | 2.335 → 1.761 | |
| fs N = 8 | 70.389〜81.835〜84.737 | 124.220〜125.510〜126.608 | 1.534 | 重ならない | 2.861 → 1.923 | 0.764 → 0.884 |
| dp12 N = 1 | 31.142〜32.181〜32.874 | 37.752〜41.844〜42.559 | 1.300 | 重ならない | 2.193 → 1.711 | |
| dp12 N = 8 | 184.555〜185.561〜187.577 | 172.702〜200.262〜242.558 | 1.079 | 重なる | 3.002 → 2.265 | 0.721 → 0.598 |

- 規則の機械的な出力：H-B1（fs × N = 1）成立，H-B2（dp12 × N = 1）成立，H-B3 は fs × N = 8 で成立・dp12 × N = 8 で不成立（記録）．
- **このイテレーションの結論：採用**（G2・G3 成立，fs × N = 1 の有効な実行は pr・pe とも 3 回，H-B1 成立）．以後の負荷の実験は `probe_body: pre_encoded` を明示して流す．既定の値は `per_request` のまま（B32 (b)）．
- **ノイズか信号か**：fs × N = 1 の pr の 3 回の幅は中央値の −0.9%〜+0.3%（Iteration 8 の −1.4%〜+0.7% と同じ程度）で，pe の最小と pr の最大の隙間は +31.8% ある．dp12 × N = 1 でも隙間は +14.8%（pe の r3 が 37.75 と低い回を含めて）で，幅の 10 倍以上である．N = 1 の 2 条件の伸びはノイズでは説明できない．
  dp12 × N = 8 の pe の幅（−13.8%〜+21.1%）は Iteration 8 の N = 8 の幅（−4.2%〜+0.3%）より大きく，下の「想定外の挙動」の遅れによる．

**分析**

事実（記録から計算したもの）
1. **N = 1 の処理量の比は，1 要求あたりの質問者の CPU の比の逆数とほぼ一致する**．fs × N = 1 は 2.335 ÷ 1.761 = 1.326 で処理量の比 1.326 と同じ，dp12 × N = 1 は 1.282 に対して 1.300．`requester_cpu_util_per_proc` は pr・pe とも 1.00 に張り付いたままで，律速は質問者のプロセスのままである．
2. **実機の節約の大きさは事前の測定と合う**．fs × N = 1 の 1 要求あたりの節約は 0.574 ms（事前の測定 0.55 ms）．dp12 は 1 問 14 要求のうち probe が 12 要求なので，全要求で均した 0.482 ms は probe 1 要求あたり約 0.56 ms にあたる．retrieve の 2 要求は変えていないので，節約は probe の本文の分だけで説明できる．
3. **N = 8 では節約が N = 1 より大きい**（fs：0.938 ms 対 0.574 ms）．pr の 1 要求の CPU は N = 1 → 8 で 23% 増える（2.335 → 2.861）が，pe では 9% しか増えない（1.761 → 1.923）．その結果 fs の E(8) は 0.764 → 0.884 に上がり，比 1.534 は N = 1 の 1.326 より大きい．fs × N = 8 の pr の r2（70.389）は下の遅れを含む回だが，それを除いた 2 回（81.8・84.7）と比べても比は 1.48 以上である．
4. **dp12 × N = 8 の pe の 3 回のうち，遅れの無い r3（242.6）だけが N = 1 と同じ程度の比（1.31）になった**．r1（200.3）・r2（172.7）では `requester_cpu_util_per_proc` が 0.84・0.71 に下がり，質問者のプロセスが待っていた．最も忙しいノードは 3 回とも 192.168.13.102（0.875・0.765・1.037 コア）で，Iteration 9・10 の 1 プロセスの頭打ち（約 1.86 コア）にはまだ届いていない．
5. **期待効果との比較**：fs × N = 1 は 17.0〜17.5 の見込みに対して 17.76（わずかに上），dp12 × N = 1 は 40〜42 に対して 41.84（内），fs × N = 8 は 95〜105 に対して 125.5（大きく上．事実 3），dp12 × N = 8 は 210〜235 に対して 200.3（下．事実 4）．
6. 全体の処理量は fs × N = 8 で約 4,016 要求/秒（Iteration 8 の制御点の約 2,600 要求/秒の約 1.5 倍）になった．最も忙しいノードの CPU は fs × N = 8 で 0.70〜0.74 コア（pr 0.43〜0.47）に上がった．

推測（上の事実と矛盾しないが，未確認のもの）
- 事実 3 は，JSON の符号化（文字列の組み立て）が物理コアの取り合い（N = 8 は物理 6 コアを超える）やキャッシュ・メモリ帯域の取り合いに敏感な処理で，それを減らすと N = 8 のときの 1 要求の CPU の増え方も小さくなる，と読める．ハイパースレッディングとキャッシュのどちらが効いているかは測っていない．
- 事実 4 の遅れは，専門家の CPU の頭打ちではなく，接続の確立かパケットの再送によると読める（下の「想定外の挙動」）．dp12 は 12 台のうち最も遅いノードを待つので，1 本の要求の遅れがそのまま問の遅れになる．

**想定外の挙動**：N = 8 の 12 実行のうち fs_n8_pr_r2（93 問）・fs_n8_pr_r3（15 問）・dp12_n8_pe_r1（16 問）・dp12_n8_pe_r2（22 問）で，ふだん 0.1 秒前後の probe_s が**約 1.0〜1.2 秒または約 3.2 秒**になった問があった（pr の dp12 の 3 回と pe の r3 は 0 問）．
これらの問の `probe_node_s_max` は全て 0.5 秒未満で，ノードの計算は遅れておらず，質問者の再試行も 0 回である．遅れの値が約 1 秒・約 3 秒にそろうことは，Linux の TCP の最初の再送の待ち（1 秒，その次は 2 秒を足して計 3 秒）と一致する（推測．SYN の再送か，データの再送かは区別していない．`nstat` 等の再送の数は取っていない）．
新しい接続の数は pr と pe で同じ程度（dp12 × N = 8 で 812〜883）で，遅れの出方を pe に結び付ける根拠は無い（fs では pr の側に出た）．言語崩れ・発散・OOM は該当なし．

**学び**
- 質問者 1 プロセスの処理量は「1 ÷（1 要求あたりの質問者の CPU × 1 問の要求の数）」でほぼ決まり，事前の測定（制御点のコンテナ・偽のノード 30 個・cProfile）で見積もった 1 要求あたりの節約は実機でもそのまま再現した（0.55 対 0.574 ms）．以後の質問者の CPU を減らすレバーは，この事前の測定で効果の大きさを先に見積もれる．
- N = 8（物理コアを超える）では，質問者の CPU を減らす節約は N = 1 より大きく効く（fs の E(8) 0.76 → 0.88）．N = 1 の比から N = 8 の値を見積もると小さめに外れる．
- N = 8 の処理量は，約 1 秒・約 3 秒の probe の遅れが出た回だけ大きく下がる（fs pr r2 は −14%，dp12 pe r2 は −14%）．Iteration 8 の N = 8 の幅（−5.1%）もこれを含んでいた可能性がある．N = 8 の比較は，遅れの問の数（probe_s が 0.9 秒以上の問）を併せて記録しないと読み違える．
- pe の後，dp12 × N = 8 の最も忙しいノード（13.102）は 1.04 コアまで来た．質問者の 1 要求の CPU をさらに 3 割ほど減らすと，dp12 × N = 8 では W = 1 のノードの頭打ち（約 1.86 コア）に近づき，律速が質問者から専門家ノードへ移りうる（推測．見積もり 1.04 × 1.5 ≈ 1.6 コア）．

**限界**
- 変えたのは probe の本文だけで，`/v1/retrieve` の 2 要求の本文は今の作り方のままである．標準の `json` のまま回数を減らしただけで，直列化の速い実装・HTTP クライアントの置き換えは扱っていない．
- 遅れの原因（再送か接続の確立か）は推測で，TCP の再送の数・待ち受けの溢れの数は測っていない．dp12 × N = 8 の pe の値は遅れの有無で大きく変わるので，3 回の中央値の 200.3 を pe の能力の値として使わない（遅れの無い r3 の 242.6 が上限の目安）．
- 有線 LAN・検索だけ・閉じた系・制御点 1 台の質問者である．

**次の一手**（backlog B33 [auto-decided]）：事前の測定で次に大きい費用である，httpcore が要求ごとに `import sniffio` に失敗して `sys.path` を探す費用（1 要求の約 24%．B32 (a)）を除く．`probe_body: pre_encoded` を固定した上で，fs・dp12 × N ∈ {1, 8} を同じ形で流し，選択が全問で一致したまま処理量が伸びるかを測る．

## Iteration 10: 専門家ノードのプロセスの数によるノード 1 台の処理量の頭打ちの確認

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B29 [auto-decided]（Iteration 9 は採用．192.168.13.102 の X_sat 354.3 要求/秒，192.168.15.103 は 473.3．両対象ともノードのプロセスの CPU 約 1.9 コアで頭打ちで，デバイスの CPU は 2〜3 割．律速の分類は「未特定」）．
config.yml の levers 末尾の `node.workers: [1, 2, 4]`（仮称）を，**config.yaml の新しいキー `experiment.node_workers`（既定 1 ＝今の挙動）**として確定した（backlog B30）．`cluster` の形式とデバイスの役割，方式の選択，データ・ラベル・シャードは変えない．質問者を複数のデバイスに置く形（B25 (a)）は扱わない．

**コードと既存の結果から確かめたこと**（事実）
- `src/atrium/node.py` の `main` は `uvicorn.run(app, ...)` にアプリの実体を渡しており，1 プロセスで動く．uvicorn 0.54 の `--workers`（`uvicorn/supervisors/multiprocess.py`）は親が 1 つのソケットを開いて spawn の子に共有させ，子はアプリを import の文字列から作り直す（アプリの実体は子へ渡せない）．
- ノードのコンテナの中の `atrium` は compose の exec 形式の command で PID 1 になる（Dockerfile に ENTRYPOINT が無い）．192.168.13.102 は cgroup v2（`cgroup2fs`，Docker の Cgroup Version 2，cgroupns）で，空きメモリは約 14.6 GB（`free -m`）．13.102 のシャードは 5 受信箱 計 3,304 通で，W 倍にしても数十 MB の見込み．
- `/v1/admin/availability`（EQ7b の離脱）の状態はプロセスの中の dict なので，複数のプロセスでは全体を切り替えられない．`*.offsets.npy`（行の先頭位置のキャッシュ）はシャードの読み込みのときに書かれ，W 個のプロセスが同時に書くと読みかけを読みうる．
- `/healthz` の `process_cpu_s` は答えたプロセスの値なので，複数のプロセスでは前後の読み取りが別のプロセスになりうる（B29 (a)）．`atrium run` の `expert_cpu_util`（`read_node_cpu_seconds`）も同じ値を使っていた．

**調査で分かったこと**（tavily-search．事実と推測を分けて書く）
- 複数のプロセスが 1 つの待ち受けのソケットを共有して accept を取り合う形では，起こされるプロセスが公平でなく，接続が少数のプロセスに偏る．Google では accept の数が最多と最少のスレッドで約 3 倍の差が出たと報告され，SO_REUSEPORT（Linux 3.9〜）で各プロセスが自分のソケットを持つとカーネルが接続を均等に振り分ける（M. Kerrisk, "The SO_REUSEPORT socket option", LWN.net, 2013．https://lwn.net/Articles/542629 ）．
  Cloudflare も epoll の起こし方（LIFO）で NGINX の 1 つの worker に負荷が集まることを報告し，回避策に SO_REUSEPORT を挙げた（M. Majkowski, "Perfect locality and three epic SystemTap scripts", Cloudflare Blog, 2017．https://blog.cloudflare.com/perfect-locality-and-three-epic-systemtap-scripts ）．Envoy でも同じ偏りが報告され，TCP の待ち受けには SO_REUSEPORT が望ましいとされた（envoyproxy/envoy issue #16213, 2021．https://github.com/envoyproxy/envoy/issues/16213 ）．
  node-bench は水準の始めに C 本の接続を一斉に張り，以後は使い回すので，共有のソケットでは偏りがそのまま区間の全体に残りうる（推測）．一方 SO_REUSEPORT は接続の 4 つ組のハッシュで振り分けるので，接続が少ない（C = 4 で W = 4 など）と確率的に偏る（推測．頂点は C の最大で取るので，接続の多い水準で均される）．
- cgroup v2 の `cpu.stat` の `usage_usec` は cgroup の全てのプロセスの CPU 時間を数える（Linux kernel documentation, "Control Group v2"．https://docs.kernel.org/admin-guide/cgroup-v2.html ）．コンテナの cgroup の値を区間の前後で読めば，W 個のプロセスと親の合計の CPU を 1 回の読み取りで得られる．
- CPU を使う Python の要求の処理を複数のコアに広げるには，GIL を共有しない複数のプロセス（uvicorn の worker 等）にするのが一般的な手とされる（例：N. Naidu, "Ruminating on FastAPI's Speed and how to scale it with multiple Uvicorn workers", 2025．https://www.narendranaidu.com/2025/07/ruminating-on-fastapis-speed-and-how-to.html ．解説記事であり実測の根拠ではない）．
  1 プロセスの中の直列の部分には GIL だけでなく，HTTP の受け取りと応答を 1 本のイベントループのスレッドで扱う部分も含まれる．W を増やす実験は両者をまとめて W 倍にするので，どちらが律速かは区別できない（限界に書く）．

**問い**：(1) 専門家ノード 1 台（i5-8350U）の約 1.9 コア・354 要求/秒の頭打ちは，ノードを 2・4 個のプロセスに分けると外れるか（頂点の処理量が W にほぼ比例して伸びるか）．(2) 伸びが止まるとき，デバイスの CPU（論理 8・物理 4）と，プロセスごとの CPU のどちらが張り付くか．(3) i7-8700（論理 12・物理 6）では同じか．

**仮説**
- **H-W1（主）**：192.168.13.102 で，W = 1 → 2 で頂点の処理量 P が「伸び」，中央値の比が 1.5 以上になる．仕組み：Iteration 9 の頭打ちは 1 プロセスの中の直列（GIL・イベントループ）によるもので，デバイスの CPU は 7〜8 割空いているので，プロセスを 2 個にすれば各プロセスがそれぞれ約 1.9 コアまで使える．
- **H-W2（記録）**：13.102 で W = 2 → 4 でも伸びる．ただし W = 4 は約 7.6 コア分を要し，物理 4 コア（論理 8）を超えるので，伸びは 2 倍より小さく，頂点で host busy が高くなる見込み．
- **H-W3（記録）**：192.168.15.103 でも H-W1 と同じことが成り立つ．

**単一レバー**：config.yaml の **`experiment.node_workers`（W）を 1 → 2 → 4** と変える（W ごとに deploy し直し，全 30 台が W 個のプロセスで起動する）．負荷は Iteration 9 と同じ `atrium node-bench`（主 192.168.13.102，記録 192.168.15.103，C ∈ {1, 2, 4, 8, 16, 32, 64}，各 W × 各対象 3 回）．それ以外は全ての W で固定する．
- **プロセスの分け方を SO_REUSEPORT にした理由**：uvicorn の `--workers` は 1 つのソケットの共有で接続が偏りうり（上の調査），アプリを import の文字列から作る必要がある（ノード名・シャードは compose の command の引数で渡しており，環境変数は使わない方針）．各プロセスが自分のソケットを `SO_REUSEPORT` で開く形なら，引数をそのまま spawn の子へ渡せる．
- **実装**（済み．下の commit）
  - `src/atrium/config.py`：`experiment.node_workers: int = 1`（≥ 1）．`churn` は `node_workers = 1` のときだけ使える（離脱の状態がプロセスごとのため）．`config.yaml` に `node_workers: 1` を足した．
  - `src/atrium/node.py`：W = 1 は従来どおり `uvicorn.run(app)`．W ≥ 2 は親がシャードを 1 回読み込んで（検証と `*.offsets.npy` を 1 回だけ書くため）手放し，`serve_workers` が spawn で W 個の子を起こす．各子（`serve_one_worker`）はシャードを読み込み，`open_reuseport_socket`（SO_REUSEADDR・SO_REUSEPORT，backlog 2048）で同じポートを開いて `uvicorn.Server.run(sockets=[sock])` で待ち受ける．
    親は要求を受けず，子のどれかが終われば全てを止めて終了コード 1 で終わる（compose の `restart: unless-stopped` で起き直す）．SIGTERM・SIGINT では子を止めて正常に終わる．`/healthz` に `cgroup_cpu_s`（コンテナの cgroup v2 の `usage_usec`．v2 でなければ null）・`pid`・`workers` を足した（追加だけ）．
  - `src/atrium/cpu_stat.py`：`read_cgroup_cpu_s`（`/proc/self/cgroup` の `0::<path>` から `/sys/fs/cgroup/<path>/cpu.stat` を読む）．
  - `src/atrium/node_bench.py`：水準ごとに `node_cgroup_util`（cgroup の CPU の差 ÷ 区間）と `node_workers` を出す．`node_proc_util` は前後の `/healthz` の `pid` が同じときだけ求める（別のプロセスが答えたら null）．
  - `src/atrium/requester_procs.py`：`read_node_cpu_seconds`（`atrium run` の `expert_cpu_util`）は，`workers` ≥ 2 のノードでは `cgroup_cpu_s` を使う（W = 1 は従来どおり `process_cpu_s`）．
  - `scripts/remote/node_bench.sh`：5 番目の引数で負荷生成のプロセスの数の上限（`--max-procs`．省略時 4）を渡せるようにした．
  - `tests/test_node_workers.py`（12 件．既定値・0 の拒否・churn との併用の拒否，cgroup の `cpu.stat` の読み方，`summarize_level` の cgroup の CPU と pid の違うときの扱い，`/healthz` の項目，`read_node_cpu_seconds` の W ごとの読み分け，**実際に uvicorn を 2 個のプロセスで立てて，新しい接続ごとに 2 つの pid が答え，SIGTERM で終了コード 0 で全ての子が止まること**）．pytest 307 件・`ruff check src tests`・`ruff format --check src tests`・`mypy src` は通過．
  - 連続実行：`results/20261015_iter10_node_workers_summary/iter10_chain.sh`（`iter9_chain.sh` に bench の `max_procs` の列を足したもの．anchor の行は `iter8_chain.sh` に渡し，`node_workers=W` で deploy する）と仕様の雛形 `iter10_specs.template.txt`・`iter10_smoke_specs.template.txt`．集計：同じ場所の `iter10_summary.py`（下の規則を機械的に出す．合成の結果で動作を確かめた）．
  - 文書：`docs/d0001_architecture.md`（`/healthz`），`docs/d0002_configuration.md`（`node_workers`），`docs/d0003_experiment_workflow.md`（`node_bench` の `max_procs`），`docs/d0004_metrics.md`（`node_cgroup_util`・`node_workers`，`expert_cpu_util` の読み方）．

**固定する構成**：Iteration 9 と同じ要求（flood_score の probe，対象のノードの全 5 シャード，k = 1，EnronQA の cached の埋め込み 2,940 問を順に巡回），慣らし 5 秒，区間 30 秒，打ち切り 30 秒，水準の間の休み 2 秒．
**負荷生成のプロセスの数の上限は全ての W で 8**（Iteration 9 の 4 から変える．W = 4 で 1,000 要求/秒を超えうるので，負荷生成の CPU の余裕を取る．W = 1 も同じ 8 で測り直し，比べる基準にする．Iteration 9 の値は記録として並べるだけ）．
ノードは各 W の anchor の `mise run deploy` で起動したもので，bench の間は deploy し直さない．負荷生成は制御点 wafl-ctrl5 のコンテナ．連続実行の間は制御点で他のコンテナを動かさず commit もしない．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 0：実機の動作確認**（連続実行の前に 1 回．判定に使わない）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．
2. `iter10_smoke_specs.template.txt` の `<date>` を実行日にしたものを `iter10_chain.sh` に絶対パスで渡す（W = 4 で deploy し，192.168.13.102 へ C = 1・16，max_procs 8）．
3. 流し終えた直後（W = 4 のまま）に，制御点から `for i in $(seq 40); do curl -s http://192.168.13.102:8100/healthz; echo; done` を流し，答えた `pid` の種類の数を数える．
4. 次を全て満たすことを確かめる．満たさなければ連続実行を流さず，原因を調べて記録する：anchor の失敗 0 問・`expert_cpu_util` に 30 台が揃う，bench の 2 水準とも `n_errors` = 0・`node_workers` = 4・`node_cgroup_util` と `node_host_busy` が null でない・負荷生成の CPU の最大 ≤ 0.80・`little_ratio` ≥ 0.90，`cross_level_score_mismatches` = 0，手順 3 の pid が 4 種類．

**手順 1：実機の連続実行**（`iter10_specs.template.txt` の `<date>` を実行日にした `iter10_specs.txt` を `iter10_chain.sh` に絶対パスで渡す）
1. W = 1：anchor（`node_workers=1` の deploy ＋ flood_score × per_node × N = 1 × parallel 30 × 300 問）→ bench 6 回（t13102・t15103 を交互に，r1 は C を昇順，r2 は降順，r3 は 2,8,32,1,64,4,16 の順．B29 (c)）．
2. W = 2：同じ（anchor は `node_workers=2`）．
3. W = 4：同じ（anchor は `node_workers=4`）．
4. W = 1 へ戻す：anchor ＋ t13102 の r4（昇順）を 1 回（時間による変化の記録．判定に使わない）．
- 計 23 実行（anchor 4・bench 19）．所要は約 2 時間の見込み（anchor 各約 5 分，bench 各約 5 分）．bench が失敗したら 60 秒おいて 1 回だけやり直す（`iter10_chain.sh`）．

**指標**（水準ごと．`docs/d0004_metrics.md`）：Iteration 9 の全ての指標に加え，`node_cgroup_util`（コンテナの全プロセスの CPU．コアの数の単位）と `node_workers`．**主の量は実行ごとの頂点の処理量 P**（その実行の欠けでない水準の処理量の最大．その水準の C を C_peak とする）で，W ごとに 3 個の P を最小〜中央値〜最大で比べる．
頂点の水準の CPU（cgroup・host busy）は 3 回の中央値．記録として Iteration 9 と同じ X_sat（水準ごとの中央値の最大）と，1 要求あたりの cgroup の CPU（ms）を出す．集計は `iter10_summary.py`．
- P を主の量にした理由：W を増やすと飽和する C が大きい方へ動きうり，水準ごとの中央値の比較では C の格子と実行の中の位置の揺れ（B29 (c)．13.102 の 5 番目の水準の約 16〜19% の低下）が混ざる．実行ごとの最大なら，1 つの水準が下がっても他の水準が頂点を担う．

**正しさの確認**（G1・G3・G5 を満たさない水準は「欠け」とし，頂点から除く）
- G1：区間の内の失敗率が 1% 以下．
- G2（対象ごと）：その対象の全ての W・全ての実行で，同じ問の probe の得点が完全に一致する（実行の内・水準の間・実行の間．プロセスの数で検索の結果は変わらないはず）．成り立たなければその対象の判定に進まない．
- G3（負荷生成が律速でない）：負荷生成のプロセスごとの CPU の最大が 0.80 以下で，かつ `little_ratio` が 0.90 以上．
- G4（律速の分類だけに効く）：`node_cgroup_util` と `node_host_busy` が null でない．
- G5（構成の確認）：水準の `node_workers` がその W と一致する．
- G6（記録）：W = 1 の頂点の水準で `node_cgroup_util ÷ node_proc_util` が 0.9〜1.2（cgroup の測り方が Iteration 9 のプロセスの CPU と整合するか）．外れたら律速の分類を慎重に読む．

**環境の揺れの規則**（記録のため）：4 回の anchor の処理量が [11.127, 14.247] 問/秒，e2e p95 が [3.053, 4.067] 秒（Iteration 7〜9 と同じ範囲）に入るかを記録する．W = 2・4 の anchor は 30 台に負荷を散らしており質問者が律速なので，W によらず同じ範囲に入る見込み（推測）．r4（W = 1 へ戻した後）の頂点は記録だけ．

**判定の規則**（対象ごと．3 回の範囲が重ならないときだけ「差がある」）
- **伸びた**（W → 2W）：2W の 3 個の P の範囲が W の範囲より上にあり重ならず，かつ中央値の比 P(2W) ÷ P(W) が 1.10 以上．
- **スケールの効率** E(W) = P(W) の中央値 ÷ (W × P(1) の中央値)（記録）．
- **律速の分類**（各 W の頂点の水準の中央値で．上から順に最初に当てはまるもの．Iteration 9 の学びで「プロセスの数 × 約 1.9 コア」の欄を足した）：
  1. `node_host_busy` ≥ 0.90 → **ノードのデバイスの CPU 全体が律速**．
  2. G4 が欠け → **測定欠け**．
  3. W ≥ 2 で `node_cgroup_util` ≥ 0.85 × W × （W = 1 の頂点の `node_cgroup_util`）→ **各プロセスが W = 1 と同じ上限で止まる（プロセスの中の直列が律速）**．
  4. どれでもない → **未特定（プロセスの外：ネットワーク・カーネル・物理コアの取り合いなど）**．
- **H-W1（主）**：192.168.13.102 で W = 1 → 2 が「伸びた」で，かつ中央値の比 ≥ 1.5．
- **H-W2（記録）**：13.102 で W = 2 → 4 が「伸びた」．
- **H-W3（記録）**：192.168.15.103 で W = 1 → 2 が「伸びた」で，かつ中央値の比 ≥ 1.5．
- **このイテレーションの結論**（192.168.13.102 で決める）：
  - **棄却（測定不成立）**：G2 が成り立たないか，G5 が 1 つでも不成立か，いずれかの W で欠けの水準が 2 つ以上か，いずれかの W で有効な実行が無い．原因を調べて記録する．
  - **採用**（約 1.9 コアの頭打ちは 1 プロセスの中の直列によると判断し，以後のノード 1 台の能力は最も P の高い W の値を使う）：上が当てはまらず，H-W1 が成り立つ．
  - **部分的に支持**（プロセスの数で一部は外れるが，プロセスの外にも律速がある）：W = 1 → 2 が「伸びた」が，比が 1.5 未満．
  - **棄却（W を増やしても伸びない）**：W = 1 → 2 が「伸びた」にならない（約 1.9 コアの頭打ちはプロセスの中の直列ではなく，ネットワーク・カーネル・ノードの外にある）．
- 記録（判定に使わない）：W ごとの処理量と p95 の曲線，C_peak の W による動き，1 要求あたりの cgroup の CPU（W で増えればコアの取り合いの費用），30 台の全体の上限の見積もり（最も P の高い W の P × Iteration 8 の比．fs 0.89〜0.92，dp12 1.14〜1.19．目安），`atrium run` の anchor の `expert_cpu_util`（W ≥ 2 は cgroup の値）．

**ノイズの見積もり**：Iteration 9 の 15.103 は全水準で 3 回の幅が中央値の ±3% 以内，13.102 は正常な回が ±3% 以内で，実行の中の位置に結び付いた約 16〜19% の低下が 21 回・水準のうち 3 つあった．実行ごとの頂点 P はこの低下を 1 つの水準なら受け流すので，3 個の P の幅は ±3% 程度と見込む（推測）．「伸びた」の閾値 1.10 はこの幅の約 3 倍で，範囲が重ならない規則を併用する．H-W1 の 1.5 は「W 倍に比例する」の 2.0 から，カーネルのネットワーク処理・ハイパースレッディングの取り合いの分の余裕を見た値である．

**期待効果**（推測．判定に使わない）：13.102 の P は W = 1 で約 350，W = 2 で 600〜700（比 1.7〜1.9．host busy 約 0.45），W = 4 で 750〜1,000（物理 4 コアを超えるので比は 1.1〜1.4，host busy 0.7〜0.95）．15.103 は W = 1 で約 470，W = 2 で 850〜930，W = 4 で 1,300〜1,700．
W = 4 で 13.102 が 900 要求/秒に届けば，30 台の flood_score の上限の見積もりは約 800 問/秒になり，制御点 1 台の質問者（N = 8 で約 82 問/秒）の約 10 倍になる．

**限界**：有線 LAN・閉じた系・probe だけ（`/v1/retrieve` を含まない）・1 台ずつの値である．W を増やす実験は GIL とイベントループの 1 本のスレッドをまとめて W 倍にするので，どちらが 1 プロセスの中の律速かは区別しない（区別には py-spy 等の計測が要る）．SO_REUSEPORT の振り分けは接続のハッシュなので，接続の少ない水準（C ≤ W）では偏りうる（頂点は接続の多い水準で取る）．
W ≥ 2 のノードは各プロセスがシャードを読み込むので，メモリはシャードの約 W 倍になる（EnronQA では数十 MB．MedRAG の PubMed のシャードでは使えない）．`atrium run` の 30 台で W を上げるか（deploy の既定を変えるか）は，結果を見て決める（B29 (b)）．

**次の候補**：採用で W = 4 の分類が「デバイスの CPU」なら，ノード 1 台の能力は W ≈ 物理コアの数で決まると読み，30 台の全体の限界の実測（質問者を複数のデバイスに置く形．B25 (a)．人間の判断）か，1 要求あたりの CPU を減らす実装（probe の JSON の解析・応答の組み立て）へ進む．棄却なら，py-spy でノードの 1 プロセスの中の時間の内訳を取るか，ネットワーク（softirq・NIC の割り込みの CPU）を調べる．

### 実装・実験（2026-10-15．research-cycle の phase 2．事実のみ．解釈・判定は評価フェーズ）

実行は制御点 wafl-ctrl5（docker 権限 ok）から，gpu2 の `iter10_chain.sh` で行った．コードは変更していない（commit 5ca205f．bench の `git_head` は作業ツリーの state.json の変更で `-dirty`）．連続実行の間は commit していない．実験後に `mise run stop` を実行した（clean・データの削除なし）．
結果：`results/20261015_iter10_*`（anchor 4・bench 19），ログ `results/20261015_iter10_node_workers_summary/iter10_chain.log`，集計 `iter10_summary.out`（`uv run python .../iter10_summary.py 20261015`）．

**手順 0（smoke．W = 4．13.102 へ C = 1・16）**：通過．anchor 失敗率 0・`expert_cpu_util` 30 台，bench 2 水準とも n_errors 0・node_workers 4・node_cgroup_util（0.484，6.324）と node_host_busy（0.076，0.823）あり，負荷生成の CPU 最大 0.178，little_ratio 0.9995・0.9996，cross_level_score_mismatches 0，curl 40 回の pid は 4 種類（22〜25）．healthz の記録：`results/20261015_iter10_smoke_healthz.txt`．

**手順 1**：anchor 4 回（w1_pre・w2・w4・w1_post）と bench 19 回，全て成功（bench のやり直し・失敗 0．`failed` の行 0）．
- anchor（処理量 問/秒，e2e p95 秒，失敗率）：w1_pre 12.321・3.931・0；w2 12.095・3.702・0；w4 13.050・3.469・0；w1_post 13.078・3.504・0．全て [11.127, 14.247]・[3.053, 4.067] の内．
- 頂点の処理量 P（13.102．3 回の昇順，中央値）：W = 1 [343.4, 352.0, 354.0] 352.0；W = 2 [558.8, 594.6, 599.7] 594.6；W = 4 [666.3, 764.5, 766.7] 764.5．E(W) = 1.000・0.845・0.543．比 1→2 は 1.689，2→4 は 1.286．両方とも範囲は重ならず「伸びた」．
- 頂点の P（15.103）：W = 1 [456.5, 475.8, 476.0] 475.8；W = 2 [786.7, 792.8, 797.9] 792.8；W = 4 [1197.8, 1246.5, 1283.5] 1246.5．E(W) = 1.000・0.833・0.655．比 1→2 は 1.666，2→4 は 1.572．
- 頂点の水準の CPU の中央値（cgroup コア / host busy）：13.102 W1 1.862 / 0.245，W2 3.719 / 0.487，W4 6.454 / 0.837．15.103 W1 1.858 / 0.155，W2 3.607 / 0.433，W4 6.968 / 0.694．
- 規則の機械的な適用（iter10_summary.out）：G1 欠けの水準 0（失敗 0），G2 両対象で成立（不一致 0 問／2,940 問），G3 負荷生成の CPU 最大 13.102 で 0.187・15.103 で 0.283（≤ 0.80），Little 最小 0.998，G4 成立，G5 成立，G6 W = 1 の cgroup ÷ proc = 1.000（両対象）．H-W1 成立（比 1.689 ≥ 1.5），H-W2 成立，H-W3 成立．律速の分類の出力は，W = 2・4 とも両対象で「各プロセスが W = 1 と同じ上限で止まる（プロセスの中の直列が律速）」．スクリプトの結論は「採用」．
- 記録（判定に使わない）：W = 1 へ戻した後の 13.102 の r4 の頂点 355.9．

**異常・未確認**：W = 4 の一部の水準（13.102 の C = 1・8・16，15.103 の C = 1〜32 の多く）と W = 2 の C = 2 で `node_proc_util` が null または約 0.001（前後の healthz の pid が違う，または別のプロセスが答えた）．cgroup の値は全水準で得られた．W = 4 の 13.102 の C = 32 の頂点の host busy は 0.795〜0.837，C = 64 は 0.844．P の範囲の幅は W = 2・4 で W = 1 より広い（13.102 W = 4 で 666.3〜766.7）．失敗・欠けは無し．集計スクリプトの規則は結果を見て変えていない．

### Iteration 10 実行済み（2026-10-15．research-cycle の phase 3．判定：採用）

**変更**：config.yaml の `experiment.node_workers`（W）を 1 → 2 → 4 と変え，W ごとに deploy し直して `atrium node-bench`（主 192.168.13.102，記録 192.168.15.103，C ∈ {1, …, 64}，負荷生成の上限 8 プロセス，各 3 回）を流した．事前登録の規則・閾値は結果を見て変えていない．
（記録の修復：phase 2 の追記で `## Iteration 9:` の見出しの行が壊れていた〔行頭の `## Iteration 9:` が消えていた〕ので，この節の直後に元の見出しを戻した．）

**正しさの確認と環境の揺れ**：G1（失敗 0．欠けの水準 0）・G2（両対象とも全ての W・回で 2,940 問の得点が完全に一致）・G3（負荷生成の CPU 最大 0.283，Little の比の最小 0.998）・G4・G5（全水準で `node_workers` = W）は成立し，G6（W = 1 の cgroup ÷ proc）は両対象で 1.000．
4 回の anchor（w1_pre 12.321，w2 12.095，w4 13.050，w1_post 13.078 問/秒．e2e p95 3.469〜3.931 秒）は全て事前登録の範囲の内．W = 1 へ戻した後の 13.102 の r4 の頂点は 355.9 で，W = 1 の 3 回（343.4〜354.0）とほぼ同じ（時間による変化は見えない）．

**結果と判定**（実行ごとの頂点の処理量 P．要求/秒．3 回の最小〜中央値〜最大）

| 対象 | W = 1 | W = 2 | W = 4 | 比 1→2 | 比 2→4 | E(2) | E(4) |
|---|---|---|---|---|---|---|---|
| 13.102（i5-8350U．物理 4・論理 8） | 343.4〜352.0〜354.0 | 558.8〜594.6〜599.7 | 666.3〜764.5〜766.7 | 1.689 | 1.286 | 0.845 | 0.543 |
| 15.103（i7-8700．物理 6・論理 12） | 456.5〜475.8〜476.0 | 786.7〜792.8〜797.9 | 1197.8〜1246.5〜1283.5 | 1.666 | 1.572 | 0.833 | 0.655 |

| 対象・W | 頂点の cgroup CPU（コア） | 1 プロセスあたり | host busy | 1 要求の cgroup CPU（ms．3 回の中央値） | 頂点の C |
|---|---|---|---|---|---|
| 13.102 W = 1 / 2 / 4 | 1.862 / 3.719 / 6.454 | 1.86 / 1.86 / 1.61 | 0.245 / 0.487 / 0.837 | 5.28 / 6.33 / 8.44 | 4,16,4 / 16,64,8 / 64,32,32 |
| 15.103 W = 1 / 2 / 4 | 1.858 / 3.607 / 6.968 | 1.86 / 1.80 / 1.74 | 0.155 / 0.433 / 0.694 | 3.90 / 4.55 / 5.52 | 4,4,8 / 8,16,8 / 16,64,64 |

- 規則の機械的な出力：13.102 は W = 1 → 2・2 → 4 とも「伸びた」，H-W1 成立（比 1.689 ≥ 1.5），H-W2 成立．15.103 も両方「伸びた」で H-W3 成立．律速の分類は両対象・W = 2・4 とも 3 番「各プロセスが W = 1 と同じ上限で止まる」（13.102 の W = 4 は閾値 0.85 × 4 × 1.862 = 6.33 に対して 6.454）．
- **このイテレーションの結論：採用**（G2・G5 成立，欠け 0，H-W1 成立）．約 1.9 コアの頭打ちは 1 プロセスの中の直列によると判断し，以後のノード 1 台の能力は最も P の高い W = 4 の値（i5 764.5，i7 1246.5 要求/秒）を使う．
- **ノイズか信号か**：W = 1 の 3 個の P は中央値の −2.4%〜+0.6%（Iteration 9 と同じ幅）．W ≥ 2 では幅が広がった（13.102 の W = 4 は −12.8%〜+0.3%，W = 2 は −6.0%〜+0.9%）が，隣の W どうしの範囲の隙間は 13.102 で 558.8 対 354.0（+58%），666.3 対 599.7（+11%）で，最も狭い W = 2 → 4 でも広がった幅より大きい．判定はこの揺れでは変わらない．
  W = 4 の中央値の比 1.286 は，13.102 の W = 4 の 3 回のうち r1 の頂点（666.3．C = 64．実行の最後の水準）が他の 2 回（764.5・766.7）より低いことを含む．2 回の値で見ても比は約 1.29 で同じ．

**分析**

事実（記録から計算したもの）
1. **W = 2 では各プロセスが W = 1 と同じ約 1.86 コアを使い，処理量は約 1.67〜1.69 倍になった**．プロセスの数を増やすだけで約 1.9 コアの頭打ちが外れたので，Iteration 9 の頭打ちはデバイスやネットワークではなく 1 プロセスの中にある．
2. **1 要求あたりの CPU は W とともに増える**（13.102：5.28 → 6.33 → 8.44 ms，15.103：3.90 → 4.55 → 5.52 ms）．W = 2 でもデバイスの CPU は半分以下（host busy 0.43〜0.49）だが，1 要求の CPU は 15〜20% 増えた．E(W) が 1 を下回る分は，ほぼこの増え方で説明できる（13.102 の W = 2 で 1.86 × 2 ÷ 6.33 ms = 588 要求/秒の予測に対して実測 594.6）．
3. **13.102 の W = 4 はデバイスの CPU の限界に近い**．論理 8 CPU のうち 6.45 CPU 分（host busy 0.84．C = 64 の水準は 0.84〜0.86）を使い，1 プロセスあたりは 1.61 コアに下がった．分類の規則（3 番）は 6.454 対閾値 6.33 の 2% の差で成り立ったもので，1 番（host busy ≥ 0.90）までは 0.06 の差しかない．15.103 の W = 4（論理 12 CPU のうち 8.3 CPU 分，host busy 0.69）は余裕が残る．
4. **W ≥ 2 の小さな C では，cgroup の CPU が W × 1.86 に届かない**（15.103 の W = 2 の C = 4 で 2.71〜2.96 コア，W = 4 の C = 4 で 2.71 コア）．`node_proc_util` が null・約 0.001 になったのは，前後の `/healthz` に別のプロセスが答えたため（設計どおり．cgroup の値は全水準で得られた）．
5. **13.102 の実行の中の位置に結び付いた低下は W = 1 でも続いている**（r2 の 4 番目の C = 8 が 276.4，r3 の 4 番目の C = 1 が 130.2，r4 の 2 番目の C = 2 が 215.4．どれも同じ C の他の回より 20〜30% 低い）．15.103 の W = 1 には無い．
6. **`atrium run` の処理量は W によらない**（anchor：W = 1 12.321・13.078，W = 2 12.095，W = 4 13.050 問/秒．全て Iteration 7〜9 の範囲の内）．30 台に負荷を散らす実行では質問者（制御点 1 台）が律速のままである．
7. **30 台の全体の上限の見積もり**（記録．事前登録の式 P × Iteration 8 の比．fs 0.89〜0.92，dp12 1.14〜1.19）：W = 1 は fs 313〜324・dp12 401〜419 問/秒，W = 2 は fs 529〜547・dp12 678〜708，**W = 4 は fs 680〜703・dp12 872〜910 問/秒**．制御点 1 台の質問者（N = 8：fs 82.35，dp12 186.21）の約 8.3〜8.5 倍・4.7〜4.9 倍である．fs で 690 問/秒を出すには質問者が約 22,000 要求/秒（32 要求/問）を送る必要があり，制御点 1 台（約 2,600 要求/秒）の約 8.5 台分にあたる．

推測（上の事実と矛盾しないが，未確認のもの）
- **1 プロセスの上限が約 1.86 コアである理由**：GIL を持つ部分が約 1 コアで直列になり，GIL を外す部分（FAISS の検索・カーネルの送受信）が残りを足す，という Iteration 9 の読みと矛盾しない．GIL とイベントループの 1 本のスレッドのどちらが律速かは，W を増やす実験では区別できない（事前登録の限界のとおり）．
- **1 要求の CPU が W とともに増える理由**：13.102 の W = 4 は物理 4 コアの 2 つの論理 CPU を同時に使う（ハイパースレッディングの取り合い）ことで説明しやすい．ただし物理コアを使い切っていない W = 2 でも 15〜20% 増えているので，動くコアが増えたときのターボの周波数の低下（i5-8350U は 15 W のノート向け）や，L3 キャッシュ・メモリ帯域の共有も効いている見込みである．
- 事実 4 は，SO_REUSEPORT が接続の 4 つ組のハッシュで振り分けるので，接続の数が少ないと一部のプロセスに接続が偏ることで説明できる（事前登録の限界のとおり）．W ≥ 2 の P の幅が広がったのも，水準の始めに張った接続の偏りが区間の全体に残ることと，事実 5 の位置による低下が重なったためと読める．
- **プロセスの数の上限の目安**：1 プロセスが約 1.9 論理 CPU を使うので，W × 1.9 ≈ 論理 CPU の数（≈ 物理コアの数 × 2）で W の伸びは止まる．i5 は W = 4（物理 4）で既に host busy 0.84 で，W = 8 にしても伸びはわずかと見込む．i7 は W = 6 まで伸びる余地がある．効率（E ≥ 0.8）を保てるのは W × 1.9 が物理コアの数以下（i5 で W = 2，i7 で W = 3）の範囲である．

**想定外の挙動**：13.102 の W = 4 の r1 の頂点（666.3）が他の 2 回より約 13% 低い（r1 は C の昇順で，C = 32・64 が実行の最後の 2 水準．1 要求の CPU が 9.76 ms と他の 2 回の 8.0〜8.4 ms より高い）．長く負荷をかけた後の熱・電力による周波数の低下の可能性がある（推測．周波数は測っていない）．言語崩れ・発散・OOM は該当なし．

**30 台の deploy の既定（B29 (b)）の扱い**：`node_workers` の既定は 1 のまま変えない．理由は (1) 30 台に散らす実行では質問者が律速で，W = 1 でもノードには制御点 1 台の約 3.8 倍の余裕がある（anchor の処理量は W によらない），(2) W ≥ 2 は `churn` と併用できず，メモリがシャードの約 W 倍で MedRAG の PubMed のシャードでは使えない，(3) 既定の値の変更は public な設定の変更で人間の判断に当たる．質問者を複数のデバイスに置いて 30 台の全体の限界を測るときに，i5 は W = 2〜4，i7 は W = 4〜6 を明示して使う（backlog B31）．

**学び**
- 専門家ノードの約 1.9 コアの頭打ちはプロセスの数で外れる．ノード 1 台の能力は「プロセスの数 × 約 1.9 コア ÷ 1 要求の CPU」で決まり，プロセスの数を物理コアの数に近づけると 1 要求の CPU が増えて効率が落ちる（i5 で E(4) = 0.54）．
- 律速の分類の規則は「W × W = 1 の上限の 0.85 倍」で 3 番を出すが，そのときデバイスの CPU が 0.84 まで来ていても 1 番には届かない．物理コアの数を超える W では，1 要求の CPU の増え方を併せて読む必要がある（今回の 13.102 の W = 4 は実質的に「デバイスの CPU に近い」）．
- 複数のプロセスのノードでは `process_cpu_s` は使えず，コンテナの cgroup の値が W = 1 の値と 1.000 で一致した．以後のノードの CPU は cgroup の値で測る．
- システム全体の律速は，Iteration 8〜10 を通して質問者（制御点 1 台）のままで，専門家ノードの余裕は W = 4 で約 8 倍に広がった．質問者を 1 台のまま処理量を上げるには，質問者の 1 要求あたりの CPU を減らすしかない．操作端末での小さな測定で，質問者が probe の本文を作る費用（`ProbeRequest` の組み立て・`model_dump`・httpx の JSON 化．768 次元）は 1 要求あたり約 0.39 ms で，制御点の質問者 1 プロセスの 1 要求あたりの時間（約 2.3 ms ＝ 1 ÷ 約 430 要求/秒）の約 17% にあたる（同じ埋め込みを 1 問で 30 台分 JSON にしている．ノードの側の JSON の解析は約 0.03 ms）．

**限界**
- probe だけ（`/v1/retrieve` を含まない）・有線 LAN・閉じた系・1 台ずつの値で，30 台の全体の上限は Iteration 8 の要求の集まり方の比を掛けた目安である．30 台に同時に負荷がかかるときのネットワークの混み方は含まない．
- 周波数・温度は測っておらず，1 要求の CPU の増え方の内訳（ハイパースレッディング・ターボ・キャッシュ）は推測である．GIL とイベントループのどちらが 1 プロセスの中の律速かも区別していない．
- W は 1・2・4 だけで，i7 の W = 6，i5 の W = 8 は測っていない．対象は各型 1 台である．
- bench の `git_head` の `-dirty` は作業ツリーの state.json の変更によるもので，コードの差分ではない（`git status` で確認）．

**次の一手**（backlog B31 [auto-decided]）：質問者の 1 要求あたりの CPU を減らす．まず probe の本文の符号化を問ごとに 1 回にする（埋め込みの JSON を 1 回だけ作り，ノードごとに shard_ids だけを差し替えて同じ bytes を送る）．flood_score・device_probe（D = 12）を per_node・2,940 問で N = 1・8 に流し，選択と得点が全問で一致したまま処理量が伸びるかを測る．

## Iteration 9: 専門家ノード 1 台への集中負荷による処理能力の飽和点の測定

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B27 [auto-decided]（Iteration 8 は採用．N = 8 でも律速は制御点 1 台の質問者のプロセスで，専門家の `expert_cpu_util_max` は最大 0.78．専門家 1 台の能力は「約 190〜210 要求/秒（1 コアで飽和すると仮定した推測）」「実測の下限 157〜164 要求/秒」にとどまる）．
config.yml の levers 末尾の `node_bench.concurrency: [1, 2, 4, 8, 16, 32, 64]`（仮称）を，下のとおり **新しい負荷試験の CLI `atrium node-bench` の同時の要求の数 C** として確定した（backlog B28）．config.yaml のキーは足さない（ノードの配置・方式の選択・`cluster` の形式とデバイスの役割は変えない）．
質問者を複数のデバイスに置く形（B25 (a)）は扱わない．データ準備・ラベル・シャード・公開情報は作り直さない．

**コードと既存の結果から確かめたこと**（事実）
- 専門家は 2 種類のハードウェアである．192.168.13.100〜109・14.100〜109 の 20 台は i5-8350U（物理 4 コア・論理 8 CPU），EnronQA の配置に入っている 192.168.15.100〜109（wafl500〜509）の 10 台は i7-8700（物理 6 コア・論理 12 CPU）である（`lscpu`．13.102・14.100・15.103 で確認）．
- Iteration 8 の dp12 × N = 8 の r1 で最も忙しかったノードは 192.168.13.102（16.5 秒に probe 2,274・retrieve 308．約 157 要求/秒．`expert_cpu_util` 0.751．`logs/192.168.13.102.log`）である．このノードのシャードは brawner-s・dickson-s・maggi-m・shackleton-s・townsend-j（41・44・47・3,120・52 通．計 3,304 通）．15.x で最も忙しかったのは 192.168.15.103（heard-m ほか 5 受信箱．計 2,094 通）．
- ノードの `/v1/probe` は同期の関数（FastAPI のスレッドプール．anyio の既定は 40 トークン）で，シャードごとに `FaissShardStore.search` を順に呼ぶ．`search` はシャードごとのロックの中で FAISS の `IndexScalarQuantizer`（fp16）を検索し，k = 1 でも上位の断片の本文を 1 行ファイルから読む（`src/atrium/store.py`）．1 回の probe は 5 シャードの検索と 5 回のファイルの読み出しと，768 次元の埋め込みの JSON の解析を含む．
- Iteration 8 の `fs_n2_p30_r1` の 11 台の `/healthz` の読み取りの失敗は，`read_node_cpu_seconds` に再試行が無いことによる（B27 (d)）．質問者の検索・probe には `post_with_retry` の再試行（2 回）がある．

**調査で分かったこと**（tavily-search・tavily-extract．事実と推測を分けて書く）
- 閉じた系の負荷を上げると，処理量は飽和点まで伸びた後に頭打ちになり（あるいは下がり），応答時間は同時の要求の数にほぼ比例して伸びる．この形を直列の部分（競合 α）と整合の費用（β）で表すのが Gunther の Universal Scalability Law である（N. J. Gunther, "Guerrilla Capacity Planning", Springer, 2007．https://link.springer.com/book/10.1007/978-3-540-31010-5 ）．
  WSO2 の実測の例では，同時の要求を 100 → 200 → 500 → 1,000 にすると処理量は 17,588 → 19,509 → 19,941 → 19,764 要求/秒で頭打ちになり，平均の待ち時間は 5.6 → 10.2 → 24.9 → 50.5 ms とほぼ比例して伸びる（WSO2, "Measuring Software Scalability using Universal Scalability Law"．https://wso2.com/blog/research/measuring-software-scalability-using-universal-scalability-law ）．
  今回は USL の当てはめはせず（7 水準・3 回では α・β の推定が不安定になりうる．推測），「C を倍にしても処理量が 10% 以上伸びない最小の C」を飽和点とする単純な規則を事前登録する．USL の当てはめは記録として評価フェーズに残す．
- 閉じた系では Little の法則から「処理量 × 平均の往復の時間 ＝ 同時の要求の数」が成り立つ（Iteration 8 の調査の Schroeder ら NSDI 2006 と同じ前提）．負荷生成の側が送れずに待つと左辺が C を下回るので，その比（`little_ratio`）を負荷生成が律速でないことの確認に使う．
- Starlette・FastAPI は同期の関数を `anyio.to_thread.run_sync` で動かし，既定のスレッドプールは 40 トークンで，同時に 40 個までしか動かない（Starlette documentation, "Thread Pool"．https://starlette.dev/threadpool ．FastAPI Discussion #8690．https://github.com/fastapi/fastapi/discussions/8690 ）．C = 64 ではノードの中で 24 個以上が待つことになる（推測．待ち時間の伸びとして見えるが，処理量の上限は CPU が先に決める見込み）．
- FAISS は Python からの呼び出しで GIL を解放し，単一のクエリの検索は呼び出したスレッドで行われるので，Python のスレッドから並列に呼ぶと複数のコアを使える．一方で，クエリの数がスレッドの数より少ないときは内部の OpenMP がデータベースのベクトルを複数のスレッドに分ける，とも書かれている（FAISS wiki, "Threads and asynchronous calls"．https://github.com/facebookresearch/faiss/wiki/Threads-and-asynchronous-calls ．M. Douze et al., "The Faiss Library", arXiv:2401.08281, 2024 は「単一のベクトルの検索では複数のスレッドを起こさない」と書く）．
  OpenMP の待機中のスレッドは既定で回り続けて CPU を使うことがある（OpenMP 5.0 仕様の `OMP_WAIT_POLICY`．https://www.openmp.org/spec-html/5.0/openmpse55.html ）．
  操作端末（論理 64 CPU）で実際の uvicorn のノードに小さな索引を載せて `node-bench` を試したところ，C = 1 でノードのプロセスの CPU 使用率が約 62 コア分，デバイス全体の忙しさが 0.997 になった（C = 2・6 では 3.8・5.7 コア分）．小さな索引の 1 件の検索でも OpenMP のスレッドが CPU を使っている可能性がある（推測．実機のノードの論理 8・12 CPU で同じことが起きるかは今回の測定で分かる）．

**問い**：(1) 専門家ノード 1 台（i5-8350U）は，flood_score と同じ probe を閉じた系で受けたとき，同時の要求の数 C をいくつにすると処理量が頭打ちになり，そのときの処理量（要求/秒）と待ち時間はいくらか．(2) 頭打ちのとき，ノードのプロセスとデバイスの CPU のどちらが張り付くか．(3) i7-8700 のノードとの差はどの程度か．

**仮説**
- **H-N1（主．飽和する）**：192.168.13.102 は C ≤ 64 の範囲で処理量が頭打ちになる（下の飽和の規則で飽和の C が定まる）．仕組み：ノードは 1 プロセスで，probe の処理のうち Python の部分（JSON の解析・pydantic・ファイルの読み出し）は GIL で直列になり，FAISS の部分もシャードごとのロックで直列になる．
- **H-N2（Iteration 8 の見積もりの確認）**：192.168.13.102 の飽和の処理量（処理量の中央値の最大）は 190 要求/秒以上である（Iteration 8 の「最も忙しいノードの要求/秒 ÷ CPU 使用率」の見積もり 190〜210 が過大でない）．
- **H-N3（記録）**：192.168.15.103（i7-8700）の飽和の処理量は 192.168.13.102 より高い．
- **H-N4（記録）**：ノードのプロセスの CPU 使用率は飽和の手前で 1 コアを超えうる（FAISS が GIL を解放し，OpenMP のスレッドも CPU を使うため）．

**単一レバー**：新しい CLI `atrium node-bench` の**同時の要求の数 C** を，1 台のノードに対して **1 → 2 → 4 → 8 → 16 → 32 → 64** と変える（1 回の実行で 7 水準を流す）．それ以外（要求の本文・対象のノード・測定の区間・負荷生成のプロセスの数の上限）は全ての水準で固定する．
- **実装の形を CLI にした理由**：既存の質問者の経路（`atrium run`）は 1 問で 30 台に probe を送るので，1 台に負荷を集めるには `cluster` の形式か配置を変える必要がある（運用ルール 2・3 に触れる）．新しい CLI は deploy 済みのノードのうち 1 台にだけ要求を送り，配置・シャード・方式の選択を変えない．Iteration 7 の `device-probe-eval` と同じく制御点の full イメージのコンテナで流す．
- **実装**（済み．下の commit）
  - `src/atrium/node_bench.py`（新規）：要求の本文は，flood_score がそのノードへ送るのと同じ `/v1/probe`（EnronQA の全 2,940 問の cached の埋め込みを `atrium run` と同じ種で並べ，そのノードの全シャードの `shard_ids`）を前もって JSON にしたもの．C を高々 4 個の負荷生成のプロセス（spawn）へ `split_parallel` で割り，各プロセスの各流れは「前の要求が返ったら次を送る」閉じた系で送る．
    全プロセスが同じ時刻 t0 から送り始め，[t0 + 5 秒, t0 + 35 秒) を測定の区間とし，区間の内に返った要求だけを集計する．親は区間の始まりと終わりに対象のノードの `/healthz`（プロセスとデバイスの CPU 時間）と制御点の `/proc/stat` を読む．応答のノード名・シャードが対象と違えば失敗として数える．同じ問の probe の得点が違えば数える．
    対象のノードは配置（`placement.json`）の host で指定し，`/v1/profile` のシャードが配置と一致することと，検索器が 1 種類であることを確かめてから流す．出力は `node_bench.json`（水準ごとの集計）と `scores.json`（qid → シャードごとの得点）．
  - `src/atrium/cli.py`：`atrium node-bench --data-dir --placement --target-host --concurrency 1,2,... --out-dir [--max-procs 4] [--warmup-s 5] [--measure-s 30]`．`scripts/remote/node_bench.sh`（新規）：制御点の full イメージのコンテナで流す（`bash scripts/tasks/remote.sh node_bench <out> <placement_run> <target> <concurrency>`）．
  - `src/atrium/node.py`：`/healthz` に `host_cpu`（デバイス全体の `/proc/stat` の累計 `busy`・`total`・`softirq` と `n_cpu`）を足した（追加だけ．既存の項目は変えない）．`/proc/stat` の読み方は `src/atrium/cpu_stat.py`（新規）に移し，`requester_procs`・`experiment`・`node`・`node_bench` が共有する（関数の中身は変えていない）．
  - **B27 (d) の対処**：`src/atrium/requester_procs.py` に `fetch_healthz` を足し，`/healthz` の GET が `ReadError`・`RemoteProtocolError`・`ConnectError` で落ちたら 1 回だけ送り直す（`HEALTH_RETRIES = 1`）．`read_node_cpu_seconds`（`atrium run` の専門家の CPU）と `node-bench` の両方が使う．処理量の区間の外の読み取りなので，`atrium run` の処理量の定義は変わらない．
  - `tests/test_node_bench.py`（15 件．区間の内の要求だけを数えること，失敗の種類，Little の比，CPU の使用率の求め方，`atrium run` と同じ問の順，本文の埋め込みの行，実際のノードの ASGI アプリへの閉じた系の送り方と区間の終わりで止まること，別のノードの応答を失敗にすること，対象の確かめ方，`/healthz` の読み取りの再試行）．pytest 295 件・`ruff check src tests`・`ruff format --check src tests`・`mypy src` は通過．
    加えて，操作端末の localhost に実際の uvicorn のノードを 1 台立て，`run_bench` を C = 1・6・2（spawn の負荷生成 1・4・2 個）で流し，失敗 0・得点の不一致 0・`little_ratio` 0.996〜0.999 を確かめた（スクリプトは `/tmp` に置き，commit しない）．
  - 連続実行：`results/20261015_iter9_node_bench_summary/iter9_chain.sh`（anchor の行は `iter8_chain.sh` に渡して deploy → start → analyze，bench の行は `node_bench.sh` を流して結果を取得する）と仕様の雛形 `iter9_specs.template.txt`．集計：同じ場所の `iter9_summary.py`（下の判定の規則を機械的に出す．事前に合成の結果で動作を確かめた）．
  - 文書：`docs/d0001_architecture.md`（`/healthz`・モジュール），`docs/d0003_experiment_workflow.md`（`node_bench` の流し方），`docs/d0004_metrics.md`（`node_bench.json` の項目）．

**固定する構成**：要求は flood_score の probe（そのノードの全 5 シャード，k = 1，EnronQA の cached の埋め込み 2,940 問を順に巡回），慣らし 5 秒，測定の区間 30 秒，負荷生成のプロセスの数は min(C, 4)（C = 64 で 16 × 4），要求の打ち切り 30 秒，水準の間の休み 2 秒．
ノードは直前の anchor の `mise run deploy`（T4 以来の 30 台 × 5 受信箱，`dataset: enronqa`）で起動したもので，bench の間は deploy し直さない（他のノードには要求を送らない）．負荷生成は制御点 wafl-ctrl5 のコンテナ（質問者のコンテナと同じデバイス）．連続実行の間は制御点で他のコンテナを動かさず commit もしない．
**主の対象は 192.168.13.102**（i5-8350U．Iteration 8 の dp12 で最も忙しかったノード．全 30 台の 20 台と同じハードウェア），**記録の対象は 192.168.15.103**（i7-8700）．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 0：実機の動作確認**（連続実行の前に 1 回．判定に使わない）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．
2. 次の 2 行の仕様（`<date>` は実行日）を `iter9_chain.sh` に絶対パスで渡して流す：
   `anchor <date>_iter9_smoke_anchor dataset=enronqa answer_mode=retrieval_only http_pool=per_node question_limit=2 routing=flood_score parallel=30 requester_procs=1` と
   `bench <date>_iter9_smoke_nb <date>_iter9_smoke_anchor 192.168.13.102 1,8`．
3. 次を全て満たすことを確かめる．満たさなければ連続実行を流さず，原因を調べて記録する：anchor の失敗 0 問・`expert_cpu_util` に 30 台が揃う（B27 (d) の対処の確認），`node_bench.json` の `target.host` = 192.168.13.102・`target.shard_ids` が配置と一致，
   2 水準とも `n_errors` = 0・`node_proc_util` と `node_host_busy` が null でない・`node_n_cpu` = 8・`generator_cpu_util_per_proc` の最大 ≤ 0.80・`little_ratio` ≥ 0.90，`cross_level_score_mismatches` = 0．

**手順 1：実機の連続実行**（`iter9_specs.template.txt` の `<date>` を実行日に置き換えた `iter9_specs.txt` を，`iter9_chain.sh` に絶対パスで渡す）
1. 環境の揺れの確認（前）＋ ノードの起動：flood_score × per_node × N = 1 × parallel 30 × 300 問（`question_limit=2`）を 1 回（anchor_pre）．
2. 主・記録の系列：192.168.13.102（t13102）と 192.168.15.103（t15103）× C ∈ {1, 2, 4, 8, 16, 32, 64} × 3 回．1 回の実行で 7 水準を流し，r1・r3 は C を昇順，r2 は降順にし，対象を交互にする（t13102_r1 → t15103_r1 → t13102_r2 → …）．配置は anchor_pre の `placement.json`．
3. 環境の揺れの確認（後）：1 と同じ（anchor_post）．
- 計 8 実行（手順 0 を除く）．所要は約 45〜55 分の見込み（anchor 2 回で各約 4 分，bench 6 回で各約 5 分〔7 水準 × (起動 約 3 秒 ＋ 慣らし 5 秒 ＋ 区間 30 秒 ＋ 休み 2 秒 ＋ 送信中の要求の待ち)〕，deploy の push などの余裕）．手順 0 は約 7 分．
- bench が失敗したら 60 秒おいて 1 回だけやり直し，それでも失敗すれば記録して次へ進む（`iter9_chain.sh` の実装のとおり）．

**指標**（水準ごと．`docs/d0004_metrics.md` の「専門家ノード 1 台への負荷試験」）：処理量（区間の内に成功した要求の数 ÷ 30 秒），往復の時間の p50・p95・p99・平均，ノードの中の probe の時間の p50・p95・p99，`little_ratio`，`node_proc_util`（コアの数の単位），`node_host_busy`・`node_host_softirq`，負荷生成のプロセスごとの CPU 使用率，`control_cpu_busy`，失敗の数と種類，得点の不一致．
3 回の集計は，処理量は最小〜中央値〜最大，その他は中央値（`iter9_summary.py`）．

**正しさの確認**（水準ごと．G1・G3 を満たさない水準は「欠け」とし，飽和の規則から除いて原因を記録する）
- G1：区間の内の失敗率が 1% 以下．
- G2（対象ごと）：同じ対象の全実行で，実行の内・水準の間・実行の間の同じ問の probe の得点が完全に一致する（`score_mismatches` = 0，`cross_level_score_mismatches` = 0，`scores.json` の共通の問で不一致 0）．成り立たなければその対象の判定に進まない．
- G3（負荷生成が律速でない）：負荷生成のプロセスごとの CPU 使用率の最大が 0.80 以下で，かつ `little_ratio` が 0.90 以上．
- G4（律速の分類だけに効く）：`node_proc_util` と `node_host_busy` が null でない．欠けたら律速の分類を「測定欠け」とする．
- hit@2 は今回の測定に無いので比べない．anchor（300 問）は処理量・e2e p95 だけを Iteration 8 の範囲と比べ，2,940 問の値と同じ欄に並べない（B27 (e)）．

**環境の揺れの規則**（記録のため）：前後の anchor の処理量が [11.127, 14.247] 問/秒，e2e p95 が [3.053, 4.067] 秒（Iteration 7・8 と同じ範囲）に入るかを記録する．外れたら「環境が変わった」と記録する．bench は同じ時間帯の 3 回で比べるので，判定の基準は変えない．

**判定の規則**（対象ごと．3 回の範囲が重ならないときだけ「差がある」）
- **伸びた**（C/2 → C）：C の 3 回の処理量の範囲が C/2 の範囲より上にあり重ならず，かつ中央値の比（C ÷ C/2）が 1.10 以上．
- **飽和の C**：C ∈ {2, 4, 8, 16, 32, 64} を小さい順に見て，「伸びた」が初めて成り立たない C（C と C/2 の両方が欠けでないものだけを見る）．全て成り立てば「C = 64 まで飽和なし」．
- **飽和の処理量 X_sat**：欠けでない水準の処理量の中央値の最大．その C を C_best とする．
- **律速の分類**（C_best の中央値で．上から順に最初に当てはまるもの）：
  1. `node_host_busy` ≥ 0.90 → **ノードのデバイスの CPU 全体が律速**．
  2. 0.85 ≤ `node_proc_util` < 1.5 → **ノードの 1 プロセスの直列の処理（GIL 等）が律速の候補**．
  3. G4 が欠け → **測定欠け**．
  4. どれでもない → **未特定**（ロック・スレッドプール・ネットワークなど）．
- **H-N1（主）**：192.168.13.102 で飽和の C が定まり（C ≤ 64），G2 が成り立つ．
- **H-N2**：192.168.13.102 の X_sat が 190 要求/秒以上．
- **H-N3（記録）**：192.168.15.103 の X_sat の C_best の 3 回の範囲が，192.168.13.102 の C_best の範囲より上にあり重ならない．
- **このイテレーションの結論**（192.168.13.102 で決める）：
  - **採用**（専門家 1 台の飽和の処理量 X_sat と飽和の C を，以後の負荷の主張と 30 台の全体の限界の見積もりに使う）：G2 が成り立ち，欠けの水準が 1 つ以下で，H-N1 が成り立つ．
  - **部分的に支持**（C = 64 の処理量を専門家 1 台の能力の下限として使う）：G2 が成り立ち，欠けの水準が 1 つ以下だが，C = 64 まで飽和しない．
  - **棄却（測定不成立）**：G2 が成り立たないか，欠けの水準が 2 つ以上（負荷生成の側の限界か，ノードの誤り）．原因を調べて記録する．
- 記録（判定に使わない）：USL の当てはめ（α・β と予測の最大），処理量の曲線と p95 の曲線，ノードの中の時間と往復の時間の差（待ち行列），30 台の全体の限界の見積もり（方式ごとに X_sat × 〔Iteration 8 の N = 8 の 問/秒 ÷ 最も忙しいノードの要求/秒〕．fs 0.89〜0.92，dp12 1.14〜1.19．probe と retrieve の混ざり方が bench と違うので目安），H-N4（`node_proc_util` が 1 を超える水準）．

**ノイズの見積もり**：Iteration 8 の 2,940 問の N = 1 の 3 回の幅は中央値の ±2% 以内だった．bench の 1 水準は 30 秒に数千要求を数えるので，同じ程度か小さい揺れを見込む（推測）．「伸びた」の閾値 1.10 はこの幅の約 2.5 倍で，範囲が重ならない規則を併用する．

**期待効果**（推測．判定に使わない）：192.168.13.102 の処理量は C = 1 で 100〜300 要求/秒（ノードの中の probe 数 ms ＋ 往復 1 ms 未満），C = 4〜16 で 150〜400 要求/秒に頭打ちになり，その先は p95 だけが C に比例して伸びる．`node_proc_util` は 1〜2 コア，`node_host_busy` は 0.2〜0.5 で，分類は「1 プロセスの直列の処理」か「未特定」を見込む．
X_sat が 190 要求/秒を大きく超えれば，Iteration 8 の 1 コアの仮定は控えめで，30 台の fs の限界は制御点 1 台の能力（約 82 問/秒）より高いことになる．

**限界**：有線 LAN・閉じた系・probe だけ（`/v1/retrieve` を含まない）の処理量である．負荷生成は制御点のコンテナ（docker のブリッジの NAT を通る）で，`atrium run` の質問者とは HTTP クライアントの使い方が違う（本文を前もって JSON にする）．ノードは 1 台ずつ測り，他の 29 台は空いている（30 台に同時に負荷がかかるときのネットワークの混み方は含まない）．
対象は 2 台で，同じハードウェアの他のノードとの差は測らない．bench の間は deploy し直さないので，前の水準・前の実行の状態（ページキャッシュなど）が残る（C の順を r2 で逆にして偏りを打ち消す）．

**次の候補**：採用で律速が「1 プロセスの直列の処理」なら，ノードの uvicorn の worker の数（プロセスの数）を変える構成．「デバイスの CPU 全体」なら，FAISS の OpenMP のスレッドの数（`OMP_NUM_THREADS`）の固定を先に確かめる（H-N4 で 1 コアを大きく超えるとき）．部分的に支持なら，負荷生成の C を 128 以上に広げる．

### Iteration 9 実行済み（2026-10-15．research-cycle の phase 3．判定：採用）

**実行の記録**（phase 2 の「実装・実験」の節は journal に書かれていなかったので，ここに要点を残す）：手順 0（`20261015_iter9_smoke_anchor`・`20261015_iter9_smoke_nb`）は全て満たした（anchor の失敗 0・`expert_cpu_util` 30 台，bench は C = 1・8 で失敗 0・`node_n_cpu` 8・負荷生成の CPU 最大 0.104・`little_ratio` ≥ 0.997・水準の間の不一致 0）．
手順 1 の 8 実行を 14:18〜14:56（JST）に流し，bench の失敗・やり直しは 0 件（`results/20261015_iter9_node_bench_summary/iter9_chain.log`）．コードは変えていない（HEAD `34b0333`）．`node_bench.json` の `git_head` が `-dirty` なのは，実験の前からある作業ツリーの `state.json` の変更と untracked の `iter7_chain.log` による（コードの差分ではない）．集計は `iter9_summary.out`．実験の後に `mise run stop` で全ノードのコンテナを止めた（削除はしていない）．

**変更**：新しい CLI `atrium node-bench` の同時の要求の数 C を 1 → 64 と変えた（主 192.168.13.102，記録 192.168.15.103．各 3 回）．事前登録の規則・閾値は結果を見て変えていない．

**正しさの確認と環境の揺れ**：G1（失敗 0）・G2（両対象とも実行の内・水準の間・実行の間で 2,940 問の得点が完全に一致）・G3（負荷生成の CPU 最大 0.28，`little_ratio` 0.998〜1.000）・G4 は全ての水準で成立し，欠けの水準は 0．前後の anchor は 12.609・12.808 問/秒，e2e p95 3.892・3.599 秒で，事前登録の範囲の内（Iteration 8 の前後 12.887・12.951 と同じ水準）．

**結果と判定**（要求/秒．3 回の最小〜中央値〜最大）

| C | 13.102（i5-8350U）処理量 | proc CPU | host busy | RTT p95（秒） | 15.103（i7-8700）処理量 | proc CPU |
|---|---|---|---|---|---|---|
| 1 | 181.0〜185.4〜187.1 | 0.78 | 0.106 | 0.0060 | 274.2〜281.4〜281.9 | 0.91 |
| 2 | 294.3〜297.2〜297.5 | 1.40 | 0.187 | 0.0076 | 413.0〜415.8〜418.3 | 1.49 |
| 4 | 299.3〜354.3〜356.4 | 1.86 | 0.244 | 0.0138 | 473.0〜473.3〜475.4 | 1.85 |
| 8 | 346.4〜346.8〜347.0 | 1.92 | 0.249 | 0.0288 | 454.6〜455.4〜457.1 | 1.87 |
| 16 | 280.2〜282.3〜344.4 | 1.57 | 0.210 | 0.0761 | 454.3〜455.3〜456.3 | 1.88 |
| 32 | 341.1〜342.0〜342.1 | 1.89 | 0.245 | 0.1033 | 445.9〜447.4〜448.8 | 1.88 |
| 64 | 333.7〜334.4〜334.8 | 1.86 | 0.242 | 0.2031 | 434.1〜434.7〜435.9 | 1.87 |

- 規則の機械的な出力（13.102）：C = 1 → 2（比 1.603）と 2 → 4（比 1.192）は「伸びた」，4 → 8 は伸びない → **飽和の C = 8**，**X_sat = 354.3 要求/秒（C_best = 4）**，律速の分類は「未特定」（proc 1.862 は 1.5 以上，host 0.244 は 0.90 未満）．H-N1 成立，H-N2 成立（354.3 ≥ 190）．
- 15.103：飽和の C = 8，X_sat = 473.3（C_best = 4），分類は「未特定」（proc 1.854，host 0.158）．H-N3 成立（C = 4 の範囲 473.0〜475.4 は 13.102 の 299.3〜356.4 より上で重ならない．中央値の比 1.34）．
- **このイテレーションの結論：採用**（G2 成立，欠け 0，H-N1 成立）．以後，専門家 1 台（i5-8350U）の飽和の処理量は 354 要求/秒（probe だけ），i7-8700 は 473 要求/秒として負荷の見積もりに使う．
- **ノイズか信号か**：15.103 は全水準で 3 回の幅が中央値の ±3% 以内（C ≥ 2 では ±1% 以内）．13.102 も 21 回・水準のうち 18 は同じ C の正常な回の ±3% 以内だが，**3 つの回・水準だけが同じ C の正常な回より約 16〜19% 低い**（r1 の C = 16：280.2，r2 の C = 4：299.3，r3 の C = 16：282.3．正常な回は C = 4 で 354〜356，C = 16 で 344.4）．このため C = 16 の中央値 282.3 は外れの回の値である．C = 1 → 2 → 4 の伸び（1.60 倍・1.19 倍）はこの揺れより大きい信号で，C ≥ 4 での頭打ちは両対象で一致する．
  判定への影響：C = 2 → 4 の「伸びた」は，C = 4 の最小 299.3 が C = 2 の最大 297.5 をわずか 0.6% 上回ったことで成り立った．この外れの回がもう少し低ければ飽和の C は 4 になっていたが，X_sat（中央値の最大 354.3）と採用の結論は変わらない．**「膝」は両対象とも C = 4 で，飽和の C = 8 は「倍にしても伸びない最初の C」という規則の定義による値**として読む．
- **期待との一致**：C = 1 の 185 要求/秒（予測 100〜300）と，C = 4〜16 での頭打ち（予測 150〜400）は当たった．H-N4（proc CPU が 1 コアを超える）は C ≥ 2 で成り立った（1.40〜1.92）．host busy は予測 0.2〜0.5 の下端（0.24）．
  Iteration 8 の見積もり 190〜210 要求/秒（1 コアで飽和すると仮定）は実測の約 0.55 倍で，1 コアの仮定が外れていた（proc CPU は約 1.9 コアで頭打ち）．1 要求あたりのノードのプロセスの CPU は飽和点で 5.3 ms（13.102．1.862 ÷ 354.3）で，Iteration 8 の dp12 の最も忙しいノード（0.75 ÷ 157 = 4.8 ms．retrieve を含む）と同じ程度である．
- USL の当てはめ（記録．中央値に最小二乗）：13.102 は λ 219，α 0.60，β 1.2e−3，予測の最大 343（RMSE 32 要求/秒），15.103 は λ 312，α 0.60，β 2.1e−3，最大 475（RMSE 23）．λ が C = 1 の実測（185・281）を大きく上回り，C = 4 の鋭い膝を表せないので，α・β を性質の推定には使わない．

**分析**

事実（記録から計算したもの）
1. **頭打ちは両対象とも「ノードのプロセスの CPU 約 1.86〜1.92 コア」で起き，デバイスの CPU は大きく余る**．13.102（論理 8 CPU）は host busy 0.24（約 1.96 CPU 分），15.103（論理 12 CPU）は 0.16（約 1.9 CPU 分）で，デバイス全体の 2〜3 割しか使っていない．ハードウェアが違っても同じ約 1.9 コアで止まり，処理量の差（1.34 倍）は 1 要求あたりの CPU の差（13.102 5.3 ms 対 15.103 3.9 ms．比 1.34）でそのまま説明できる．
2. **C を 4 より大きくすると，処理量はわずかに下がり，待ち時間は C に比例して伸びる**（13.102：354 → 334，15.103：473 → 435．RTT p95 は C = 8 → 64 で約 7 倍）．C = 64 では RTT の p50（0.19 秒）がノードの中の時間の p50（0.11 秒）より大きく，ノードの中のスレッドプール（40）に入る前の待ちが見える（事前登録の調査のとおり）．
3. **13.102 の 3 つの低い水準は，C ではなく実行の中の位置で決まっている**．3 回とも各実行の 5 番目の水準（開始から約 3 分後．r2 は降順なので C = 4 が 5 番目）で起き，C = 16 の同じ水準でも r2（3 番目）は 344.4 と正常だった．測定の区間は 14:25:20〜50，14:34:58〜14:35:28，14:44:39〜14:45:09（JST）で約 9.7 分おきである．
   この 3 区間では proc CPU（1.56〜1.58）と host busy（0.208〜0.212）がともに下がり，1 CPU 秒あたりの要求の数（約 180〜190）は正常な水準と同じだった．つまりノードは計算を遅くしたのではなく，区間の約 16% の間 要求を受け取っていなかった．C = 16 の 2 回は RTT の p99 が 0.32 秒でノードの中の p99（0.047 秒）より約 0.28 秒長い．15.103 の 21 回・水準には同じ形が無い．
   13.102 には pcp（pmcd・pmlogger・pmie）と，cron の出力のメールの配送に失敗し続ける postfix が動いている（15.103 には無い．journalctl で確認）が，postfix の大量の再送（14:21〜14:23）や pmlogger_check（14:25:07）・anacron（14:35:06）の時刻は 3 区間と一致しきらない．
4. **30 台の全体の処理量の上限の見積もり**（記録．事前登録の式 X_sat × Iteration 8 の N = 8 の「問/秒 ÷ 最も忙しいノードの要求/秒」）：flood_score は 354.3 × 0.89〜0.92 = **約 315〜326 問/秒**，device_probe（D = 12．最も忙しいノードは 13.102 自身）は 354.3 × 1.14〜1.19 = **約 404〜422 問/秒**．
   Iteration 8 の制御点 1 台の N = 8（fs 82.35，dp12 186.21 問/秒）の約 3.8〜4.0 倍・2.2〜2.3 倍で，専門家の側にはそれだけの余裕が残っている．fs で 315 問/秒を出すには質問者が約 10,100 要求/秒（32 要求/問）を送る必要があり，制御点 1 台の約 2,600 要求/秒の約 4 台分にあたる．

推測（上の事実と矛盾しないが，未確認のもの）
- **約 1.9 コアで頭打ちになる理由**：ノードは 1 プロセスの uvicorn で，HTTP の受け取り・JSON の解析・応答の組み立て（イベントループのスレッド）と，probe の本体（スレッドプールのスレッド．pydantic・ファイルの読み出し・FAISS の呼び出し）が同じ GIL を取り合う．
  GIL を持つ部分の合計が約 1 コアで直列になり，GIL を外す部分（FAISS の検索・カーネルの送受信の処理）が残りの約 0.9 コアを足す，という形と読める．ハードウェアによらず同じコアの数で止まることは，デバイスの資源ではなくプロセスの中の直列の部分が律速であることと整合する．事前登録の分類の 2 番（0.85 ≤ proc < 1.5）は「GIL を持つ部分だけで 1 コア強」を想定しており，GIL を外す部分の大きさを見込んでいなかったので「未特定」になった．
  確かめるには，プロセスの数（uvicorn の worker の数）を変えて処理量がそれに比例して伸びるかを見るか，py-spy で GIL の保持の割合を測る必要がある．
- 事実 3 の低い区間は，RTT の長い裾（約 0.28 秒．TCP の最小の再送の時間 200 ms に近い）と合わせると，13.102 の側の一時的なパケットの取りこぼし・ネットワークの止まりで要求が届かない時間があったと読める．13.x のネットワーク（スイッチ）または 13.102 の常駐の処理によるものかは分からない．30 台の全体の負荷では最も遅いノードがそのまま全体を引き下げるので，ノード 1 台の能力は「正常な区間の中央値」と「約 16% 下がる区間がある」の両方で扱う．

**想定外の挙動**：上の事実 3（13.102 の実行の中の 5 番目の水準だけの約 16〜19% の低下）．手順 0 の C = 1（102 要求/秒）が本番の C = 1（181〜187）より大きく低いのは，deploy の直後の最初の水準（ページキャッシュ・接続が冷えている）であったためと読める（推測．判定に使わない）．言語崩れ・発散・OOM は該当なし．

**学び**
- 専門家ノード 1 台は「1 コアで飽和する」のではなく，ハードウェアによらず**プロセスの CPU 約 1.9 コアで頭打ちになり，デバイスの CPU の 7〜8 割は空いたまま**である．1 台の能力を上げる手は CPU の速さよりもプロセスの中の直列の部分を外すこと（プロセスの数を増やす等）にある見込みで，Iteration 8 の「要求/秒 ÷ CPU 使用率」の外挿は使わない．
- 閉じた系の負荷試験の「膝」は C = 4 で，それより大きな C は待ち時間を増やすだけで処理量を下げる．ノードあたりの同時の要求を 4 前後に保つのが 1 台の処理量にとって最も良い（30 台の負荷を設計するときの目安）．
- 同じ順で水準を流すと，「実行の開始からの時刻」に結び付いた揺れが特定の水準に乗る．r2 を降順にしたことで，揺れが C ではなく位置で決まると切り分けられた．揺れの原因を調べるときは，区間の時刻とノードの journalctl を突き合わせる．
- 事前登録の律速の分類に「proc が 1.5〜2 コアで host が低い」の欄が無く「未特定」になった．次にプロセスの中の直列を扱うときは，proc がコアの整数倍の近くで止まるか（プロセスの数 × 約 1.9）を分類に入れる．

**限界**
- probe だけ（`/v1/retrieve` を含まない）・有線 LAN・閉じた系・他の 29 台は空いた状態の 1 台ずつの値である．30 台の全体の見積もり（約 315〜422 問/秒）は Iteration 8 の要求の集まり方の比を掛けた目安で，実測していない．30 台の全体の限界を実測するには質問者の側が約 4 台分の能力を要し，質問者を複数のデバイスに置く形（B25 (a)．人間の判断）が要る．
- 約 1.9 コアで止まる理由（GIL の取り合い）は推測で，プロファイルもプロセスの数を変える実験もしていない．
- 13.102 の約 16〜19% の低下の原因は未特定である．各 i5 のノードに同じことが起きるかは測っていない．対象は 2 台で，同じハードウェアの他のノードとの差は測っていない．
- 負荷生成は制御点のコンテナ（docker のブリッジの NAT）で，`atrium run` の質問者と HTTP クライアントの使い方が違う．

**次の一手**（backlog B29 [auto-decided]）：ノードの uvicorn の worker の数（ノードのプロセスの数）を 1 → 2 → 4 と変え，同じ `node-bench`（13.102，C の格子は同じ）で X_sat がプロセスの数に比例して伸びるかを測る．伸びれば約 1.9 コアの頭打ちはプロセスの中の直列が原因と確かめられ，ノード 1 台の能力（と 30 台の全体の上限の見積もり）を上げられる．

## Iteration 8: 複数の質問者のプロセスによる専門家の側の処理量の限界の測定

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B25 [auto-decided]（Iteration 7 は採用．Iteration 4〜7 の負荷の比較は全て質問者 1 プロセス（1 コアに張り付き，R ≈ 390〜440 要求/秒）の能力の比較で，専門家の側の限界は測れていない）．config.yml の levers 末尾の `experiment.requester_procs: [2, 4, 8]`（仮称）を，下のとおり **`experiment.requester_procs`（追加だけの設定キー．既定 1 ＝今の挙動）** として確定した（backlog B26）．
質問者を複数のデバイスに置く形（B25 (a)）は扱わない．データ準備・ラベル・シャード・公開情報は作り直さない．方式の選択の挙動は変えない．

**コードと既存の結果から確かめたこと**（事実）
- 制御点 wafl-ctrl5 は Intel Core i7-8700（**物理 6 コア・論理 12 CPU**．`lscpu`），メモリ 62 GB．専門家は i5-8350U（論理 8 CPU）．質問者は制御点の docker のコンテナ（`docker/compose.requester.yml`．CPU の上限の指定なし）で，専門家のノードは uvicorn の 1 プロセス（`atrium.node.main`．`/v1/probe`・`/v1/retrieve` は同期の関数で FastAPI のスレッドプールで動く）．
- 質問者の `run_questions` は 1 つのイベントループの上で `asyncio.Semaphore(parallel)` で問を流す．Iteration 7 の parallel 30 で `requester_cpu_util` は 0.965〜1.002 であり，処理量は「1 ÷（route_s ＋ 要求の数 ÷ R）」の予測と 0.977〜1.054 で一致した（journal の Iteration 7）．
- 同じ時期の parallel 30 の `probe_node_s_max` の p95（ノードの中の probe の計算時間の最大）は 0.0035〜0.032 秒で，flood_score の 1 ノードあたりの要求は約 14 要求/秒（≈ 420 ÷ 30）にとどまる（`results/20261015_iter7_load_summary/iter7_summary.out`）．専門家は今の負荷では空いていると読める（推測．専門家の CPU 使用率は記録していなかった）．
- 質問者のログは全ての HTTP 要求を INFO で 1 行ずつ書いている（`results/20261015_iter7_dp12_pn_p30_r1/requester.log` の 4,236 行のうち 4,230 行が `httpx: HTTP Request`）．これは 1 要求あたりの質問者の費用に含まれており，今回も変えない（変えると 2 つ目のレバーになる）．
- EnronQA の test は 2,940 問（`benchmark/questions.jsonl`）で，`device-probe-eval` の test の hit@2（flood_score 0.8041，D = 24 0.7837，D = 12 0.6830）と同じ集合である（`metrics.json` の `n_test` = 2940）．`live_selected_sources` は 300 問（`question_limit: 2`）の分だけ．

**調査で分かったこと**（tavily-search・tavily-extract．事実と推測を分けて書く）
- Python の負荷生成器は 1 プロセスで 1 コアしか使えない（GIL）ので，コアの数だけ worker のプロセスを立てる．Locust の文書は「Python は 1 プロセスで 1 コアより多くを使えないので，プロセッサのコアごとに 1 つの worker を動かす必要があるかもしれない」と書き，`--processes` で 1 台の上に複数の worker を立てる形を用意している
  （Locust documentation, "Distributed load generation"．https://docs.locust.io/en/stable/running-distributed.html ）．本研究の質問者は Iteration 4〜7 でまさにこの状態（1 コアに張り付き）にあり，同じ対処（同じデバイスの上でプロセスを分ける）が素直に当てはまる．
- 負荷試験の結果は負荷生成器の側の設計で大きく変わり，先行研究の負荷生成器の不正確さが実証されている（Y. Zhang, D. Meisner, J. Mars, L. Tang, "Treadmill: Attributing the Source of Tail Latency through Precise Load Testing and Statistical Inference", ISCA 2016, pp. 456–468．https://research.facebook.com/publications/treadmill-attributing-the-source-of-tail-latency-through-precise-load-testing-and-statistical-inference ）．
  Lancet は，クライアントの基盤のために遅れの分布を正しく測れない状況を統計的な検定で自動的に見つける設計をとる（M. Kogias, S. Mallon, E. Bugnion, "Lancet: A self-correcting Latency Measuring Tool", USENIX ATC 2019, pp. 881–896．https://www.usenix.org/conference/atc19/presentation/kogias-lancet ）．
  本研究の「処理量」は，これまで負荷生成器（質問者）の側の限界を測っていたことになる．今回は質問者のプロセスごとの CPU と，専門家・制御点の CPU を同じ区間で記録し，どこが張り付いたかで律速を判定する（Lancet のような統計的な自己補正は行わない）．
- 本研究の質問者は「前の問が終わったら次の問を出す」閉じた系（closed loop．同時の問の数 parallel を固定）である．閉じた系と開いた系（到着が完了と独立）は，同じ負荷でも応答時間などの振る舞いが大きく違いうる（B. Schroeder, A. Wierman, M. Harchol-Balter, "Open Versus Closed: A Cautionary Tale", NSDI 2006．https://www.usenix.org/legacy/event/nsdi06/tech/full_papers/schroeder/schroeder.pdf ）．
  閉じた系では「処理量 ≤ 同時の問の数 ÷ 1 問の時間」（Little の法則）で上限が決まるので，parallel の合計を固定してプロセスを増やすと，ある所で同時の問の数そのものが律速になりうる（推測）．このため，parallel を増やした記録の系列（N = 8 で合計 240）を別に置く．今回の結論は閉じた系の処理量についてのもので，開いた系の遅れの主張には使わない．

**問い**：(1) 同じ制御点の上で質問者を N 個のプロセスに分けると（parallel の合計は 30 のまま），処理量は N に比例して伸びるか．(2) 伸びが止まるとき，張り付くのは質問者のプロセス・専門家・制御点のどれか．(3) そのときの 1 ノードあたりの要求の数と専門家の CPU 使用率から，専門家の側の限界はどの程度と見積もれるか．

**仮説**
- **H-P1（主．質問者の律速を外すと処理量が伸びる）**：flood_score の parallel 30 で，N = 2 の処理量は N = 1 より高い．仕組み：Iteration 4〜7 の処理量は質問者 1 プロセスが 1 秒に扱える要求の数（R）で決まっており，プロセスを 2 個にすると R の合計が約 2 倍になる．
- **H-P2（device_probe でも同じ）**：device_probe（D = 24・12）でも N = 2 の処理量は N = 1 より高い．
- **H-P3（記録．伸び方と律速）**：N = 4 までは N にほぼ比例して伸び，N = 8 では制御点の物理 6 コアを超えるので伸びが鈍る．専門家の CPU 使用率は N = 8 でも 1 コアに届かない（推測）．

**単一レバー**：`config.yaml` の `experiment.requester_procs` を **1（今の挙動）から 2・4・8 へ**変える．`parallel`（全プロセスの合計の同時の問の数）は 30 のまま固定し，各プロセスへほぼ均等に割る（N = 4 で 8・8・7・7，N = 8 で 4・4・4・4・4・4・3・3）．
- **parallel を合計で固定する理由**：同時の問の数を Iteration 5〜7 と同じにすれば，変わるのは「同時の問を何個のプロセス（コア）で扱うか」だけになり，単一レバーになる．プロセスごとに 30 にすると同時の問の数も N 倍になり，2 つの要因が混ざる．後者は記録の系列（N = 8・合計 240）として別に流す．
- **実装**（済み．下の commit）
  - `src/atrium/config.py`：`ExperimentConfig.requester_procs`（既定 1，`parallel` ≥ `requester_procs`，`churn` とは併用しない）．`config.yaml`：`experiment.requester_procs: 1`．
  - `src/atrium/requester_procs.py`（新規）：問を順番に配る `split_questions`（i 番目の問を i mod N 番目へ．並べ替えの種は既存の `experiment.seed`），`split_parallel`，`spawn` で起動する子のプロセス（各自で `build_http_client`・`build_requester` を作る），全員の準備が済んでから一斉に流す合図（`multiprocessing.Event`），子の異常終了の検出，プロセスごとの結果（`results.part{i}.jsonl`）を `results.jsonl` にまとめる処理（失敗時・再開時も）．
    専門家の CPU 時間（`/healthz` の `process_cpu_s`）と制御点全体の CPU（`/proc/stat`）を測定の区間の前後で読む関数．
  - `src/atrium/experiment.py`：質問者の組み立てを `build_requester` に切り出し（親と子で共有），`requester_procs` ≥ 2 で `run_questions_in_processes` を呼ぶ．`started_at`〜`finished_at` は「一斉に流し始めた時刻〜最後のプロセスが終えた時刻」（子の準備の時間を含めない）．
    1 プロセスの経路は従来どおりで，区間のすぐ外側で専門家・制御点の CPU を読む処理だけを足した（区間の外の 30 件の GET が 2 回．処理量の分母には入らない）．
  - `src/atrium/node.py`：`/healthz` に `process_cpu_s`（`time.process_time()`）を足した（追加だけ）．
  - 新しい `run_meta.json` の項目：`requester_procs`，`requester_cpu_util`（全プロセスの合計．コアの数の単位），`requester_cpu_util_per_proc`，`requester_parallel_per_proc`，`requester_wall_s_per_proc`，`expert_cpu_util`（ホストごと）・`_max`・`_median`，`control_cpu_busy`・`control_cpu_softirq`（`docs/d0004_metrics.md`）．
  - `tests/test_requester_procs.py`（17 件．設定の検証，parallel と問の割り方，結果のまとめ方，`/proc/stat` の読み方，ノードの CPU 時間の読み方と読めないノードの扱い）．pytest 280 件・`ruff check src tests`・`ruff format --check src tests`・`mypy src` は通過．
    加えて，操作端末の localhost に実際の uvicorn のノードを 4 台立てた使い捨ての確認で，N = 3（spawn の子 3 個）と N = 1 の 40 問の `top_doc_ids` が全問一致し，`results.part*.jsonl` が残らないことを確かめた（スクリプトは `/tmp` に置き，commit しない）．
  - 連続実行：`results/20261015_iter8_load_summary/iter8_chain.sh`（`iter7_chain.sh` の失敗率の打ち切りの単位に N を足したもの）と仕様の雛形 `iter8_specs.template.txt`（43 実行）．
  - 文書：`docs/d0001_architecture.md`（`/healthz`・モジュール），`docs/d0002_configuration.md`（`requester_procs`），`docs/d0004_metrics.md`（質問者・専門家の CPU）．

**固定する構成**（Iteration 7 と同じ）：`dataset: enronqa`，`answer_mode: retrieval_only`，m = 2，`merge: score`，`query_embedding.enronqa: cached`，`seed` は既存の値（問の並べ替えも同じ），**`http_pool: per_node`（全実行で明示．池はプロセスごと）**，前計算した 1 段目，`parallel: 30`（合計），質問者のログの水準（INFO．全要求を 1 行ずつ），
配置は T4 以来の 30 台 × 5 受信箱，各回の前に `mise run deploy`，失敗した問は流し直さない，連続実行の間は制御点で他のコンテナを動かさず commit もしない．専門家 30 台・質問者（制御点）の構成は T4 と同じ（運用ルール 8 のフルスペックの実機実験）．
問の数は主の系列で **test の全 2,940 問**（`question_limit: null`）とする．Iteration 6・7 で 300 問の高い処理量の条件は約 1 秒の一斉の待ち 1 回で 5〜30% 揺れた（B23 (b)）．N = 8 で数十問/秒を超えると 300 問は数秒で終わるので，問を 10 倍にする．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 0：実機の動作確認**（連続実行の前に 1 回．判定に使わない）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．
2. `<date>_iter8_smoke_dp24_n2_p30`（`dataset=enronqa answer_mode=retrieval_only question_limit=2 http_pool=per_node routing=device_probe routing.probe_d.enronqa=24 parallel=30 requester_procs=2`）を deploy → start → analyze で 1 回流す．
3. 次を全て満たすことを確かめる．満たさなければ連続実行を流さず，原因を調べて記録する：失敗 0 問，300 問の `selected_sources` が `results/20261015_iter7_device_probe_eval/metrics.json` の `live_selected_sources["24"]` と 100% 一致，`run_meta.json` の `requester_procs` = 2・`requester_parallel_per_proc` = [15, 15]・`http_pool` = per_node，`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない，`results.part*.jsonl` が残っていない，`requester.log` に `[w0]`・`[w1]` の両方の行がある．

**手順 1：実機の連続実行**（`iter8_specs.template.txt` の `<date>` を実行日に置き換えた `iter8_specs.txt` を，`iter8_chain.sh` に**絶対パス**で渡す）
1. 環境の揺れの確認（前）：flood_score × per_node × N = 1 の parallel 30 を 300 問（`question_limit=2`）で 1 回．
2. 主の系列：flood_score（fs）・device_probe D = 24（dp24）・D = 12（dp12）の 3 方式 × N ∈ {1, 2, 4, 8} × 3 回，2,940 問・parallel 30（合計）．時間の偏りを打ち消すため，回ごとに方式を fs → dp24 → dp12 の順に回し，N は r1・r3 で昇順，r2 で降順に流す．
3. 記録の系列（判定に使わない）：N = 8・合計 parallel 240（各プロセス 30）を fs で 3 回，dp24・dp12 で 1 回ずつ．
4. 環境の揺れの確認（後）：1 と同じ．
- 計 43 実行（手順 0 を除く）．所要は約 3.5〜4 時間の見込み（Iteration 7 の deploy などの 1 実行あたり約 3.8 分 × 43 と，2,940 問の流す時間の合計約 50 分）．deploy の失敗の扱い・失敗率の打ち切り（routing・D・N ごと）は Iteration 4〜7 と同じ（`iter8_chain.sh` の実装のとおり）．

**指標**：処理量（成功した問の数 ÷（`finished_at` − `started_at`）），e2e_s の p50・p95，probe_s・retrieve_s・route_s の p50・p95，`probe_node_s_max` の p95，失敗率とエラーの種類，再試行の数，新しい接続の数，1 秒あたりの HTTP 要求の数（ノードの access log），1 ノードあたりの 1 秒の要求の数（平均と最大），
`requester_cpu_util`（合計）と `requester_cpu_util_per_proc` の中央値，`expert_cpu_util_max`・`_median`，`control_cpu_busy`・`control_cpu_softirq`，2,940 問の hit@2．効率 E(N) = 処理量の中央値(N) ÷（N × 処理量の中央値(1)）．

**正しさの確認**（満たさない実行は判定に使わず「欠け」とし，原因を調べて記録する）
- G1：失敗率が 1% 以下．
- G2：同じ方式の 2,940 問の全実行（主・記録の系列）で，失敗の無い問の `selected_sources` が問ごとに 100% 一致する（N と parallel で選択が変わらない）．一致しなければ，その方式の判定に進まない．
- G3：`run_meta.json` の `requester_procs`・`requester_parallel_per_proc`（長さ N，合計が parallel）・`http_pool` = per_node・routing・D・`n_questions` が仕様と一致する．
- G4（記録）：2,940 問の hit@2 が `device-probe-eval` の test の値（fs 0.8041，dp24 0.7837，dp12 0.6830）と 5e-4 以内で一致するか．外れても G2 が成り立てば判定は行い，差の原因（問の集合・hit の定義）を記録する．
- G5（律速の分類だけに効く）：`expert_cpu_util` に 30 台が揃い，`control_cpu_busy` が null でない．欠けた実行は律速の分類を「測定欠け」とする．

**環境の揺れの規則**（記録のため）：前後 2 回の flood_score × N = 1 × 300 問の p30 の処理量が [11.127, 14.247] 問/秒，e2e p95 が [3.053, 4.067] 秒（Iteration 7 と同じ範囲）に入るかを記録する．外れたら「環境が変わった」と記録する．比べる相手は同じ時間帯の N = 1 なので，判定の基準は変えない．

**判定の規則**（比べる 2 条件の 3 回の範囲が重ならないときだけ「差がある」．全て parallel 30 の主の系列）
- **H-P1（主）**：fs の N = 2 の 3 回の処理量の範囲が N = 1 の範囲より上にあり重ならず，かつ中央値の比（N = 2 ÷ N = 1）が 1.5 以上．
- **H-P2**：dp24・dp12 のそれぞれで，H-P1 と同じ規則が成り立つか（方式ごとに記録）．
- **頭打ちの N**（方式ごと）：N ∈ {2, 4, 8} を小さい順に見て，「N の範囲が N/2 の範囲より上にあり重ならず，かつ中央値の比が 1.2 以上」が初めて成り立たない N を頭打ちの N とする．全て成り立てば「N = 8 まで頭打ちなし」．
- **律速の分類**（方式ごと．主の系列の N = 8 と，記録の系列の N = 8・parallel 240 で．3 回あれば中央値）．上から順に最初に当てはまるもの：
  1. `requester_cpu_util_per_proc` の中央値 ≥ 0.90 → **質問者のプロセスが律速**（N = 8 は物理 6 コアを超えるので，その旨を添える）．
  2. `expert_cpu_util_max` ≥ 0.90 → **専門家の側が律速の候補**（ノードの Python の処理は 1 プロセスなので，1 コア付近を飽和の目安とする．FAISS のスレッドで 1 を超えうる点は記録する）．
  3. `control_cpu_busy` ≥ 0.50 → **制御点の CPU が律速の候補**（論理 12 CPU の半分 ＝ 物理 6 コア相当）．
  4. どれでもない → **未特定**（同時の問の数〔Little の法則〕・ネットワーク・ノードの待ちなど）．
- **このイテレーションの結論**：
  - **採用**（以後の負荷の主張は，質問者を律速から外した構成〔頭打ちの N の手前で最も処理量の高い N〕の値で行う）：H-P1 が成り立つ．
  - **部分的に支持**：H-P1 が成り立たないが，fs の N = 2 の範囲が N = 1 より上にある（比 < 1.5），または H-P2 がどちらかの方式で成り立つ．
  - **棄却**：3 方式とも N = 2 の範囲が N = 1 の範囲より上にない（N = 1 で既に質問者以外が律速だったことになり，Iteration 4〜7 の解釈を見直す）．
- 記録（判定に使わない）：E(N) の曲線，N = 8・parallel 240 の処理量と律速の分類，1 ノードあたりの要求の数 ÷ `expert_cpu_util_max` による専門家 1 台の要求の処理能力の見積もり（ノードが 1 コアで飽和すると仮定した推測），方式間の処理量の比と 1 問あたりの要求の数の比，e2e p95 の N による変化．

**ノイズの見積もり**：Iteration 5〜7 の parallel 30 の 3 回の処理量の幅は中央値の ±5% 以内で，flood_score × N = 1 × 300 問は 5 回で 12.28〜12.86 問/秒だった．H-P1 の倍率 1.5 と頭打ちの 1.2 は，この幅の 10 倍・4 倍に当たる．2,940 問にしたので，1 秒の一斉の待ちの影響は 300 問のときの約 1/10 になる見込みである（推測）．
プロセスを増やすと揺れが増えうるので，範囲が重ならない規則を併用する．

**期待効果**（推測．判定に使わない）：fs の処理量は N = 1 で約 12.5，N = 2 で 22〜25，N = 4 で 40〜48，N = 8 で 55〜75 問/秒（物理 6 コアで頭打ち）．N = 8 の fs で全体 1,800〜2,400 要求/秒，1 ノードあたり 60〜80 要求/秒になり，`expert_cpu_util_max` は 0.2〜0.5 程度で専門家はまだ飽和しない見込みである．
その場合の結論は「同じ制御点の上のプロセスの分割では専門家の限界に届かず，専門家 1 台は少なくとも X 要求/秒を扱える（下限）」になり，それより上を測るには質問者を複数のデバイスに置く形（B25 (a)．人間の判断）が要る．dp12 は 1 問あたりの要求が少ないので，N = 8 で 150 問/秒を超えうる．

**限界**：有線 LAN・検索だけ・閉じた系（同時の問の数を固定）の処理量である．質問者は同じ制御点の上のプロセスで，制御点の NIC・カーネル・物理コアが共通の律速になりうる（B25 (b)．その場合は「制御点 1 台の限界」と呼び，専門家の限界とは呼ばない）．専門家の CPU はノードのプロセスの CPU 時間で，docker・カーネルのネットワーク処理は含まない．
配置は話題と無関係な 30 台 × 5 受信箱の 1 通り．

**次の候補**：採用で専門家が飽和しなければ，質問者を複数のデバイスに置く構成（B25 (a)．人間の判断を要する）を backlog に上げ，それまでは「同じ制御点の上の N プロセスでの値」を負荷の主張に使う．専門家が飽和すれば，ノードの側の処理（uvicorn の worker の数など）が次の候補．棄却なら，N = 1 での律速（未特定）の切り分け．

### 実装・実験（2026-10-15．research-cycle の phase 2．解釈は評価フェーズに委ねる）

**実行**：手順 0 の動作確認 1 回（`20261015_iter8_smoke_dp24_n2_p30`）→ 手順 1 の 43 実行（`iter8_specs.txt`．事前登録の `iter8_specs.template.txt` の `<date>` を 20261015 にしただけ）．全 44 実行を 10:12〜13:33 に流し，deploy の失敗・start の異常終了・失敗率の打ち切り・再試行の遅延は 0 件（`iter8_chain.log` に FAILED・SKIPPED なし）．コードは変えていない（HEAD 68308d8）．集計は `results/20261015_iter8_load_summary/iter8_summary.py`（出力 `iter8_summary.out`，連続実行のログ `iter8_chain.log*`・`smoke8.log*` も同じ場所）．

**手順 0（判定に使わない）**：docker ok．失敗 0 問，300 問の `selected_sources` が iter7 の `live_selected_sources["24"]` と 300/300 一致，`requester_procs` = 2・`requester_parallel_per_proc` = [15, 15]・`http_pool` = per_node，`expert_cpu_util` 30 台，`control_cpu_busy` 0.177，`results.part*` なし，`requester.log` に `[w0]`・`[w1]` の両方．全て満たして連続実行へ進んだ．

**正しさの確認**
- G1：43 実行すべて失敗率 0（失敗 0 問）．
- G2：fs（anchor を含む 17 実行）・dp24（13）・dp12（13）とも，N・parallel・300/2,940 問によらず `selected_sources` が共通の問で 100% 一致．
- G3：全 43 実行で `requester_procs`・`requester_parallel_per_proc`（長さ N，合計 30 または 240）・`http_pool` = per_node・routing・D・`n_questions` = 2,940 が仕様と一致．
- G4（記録）：hit@2 は fs 0.8041・dp24 0.7837・dp12 0.6830 で `device-probe-eval` の test の値と 5e-4 以内で一致（fs の表に 0.92 が出るのは 300 問の anchor 2 回の値で，2,940 問ではない）．
- G5：42 実行は `expert_cpu_util` 30 台・`control_cpu_busy` あり．**`fs_n2_p30_r1` だけ専門家 11 台の `/healthz` が読めず（`requester.log` に WARNING 11 行．"Server disconnected without sending a response"．13.100・13.101・13.103・13.104・13.108 ほか）19 台**．この実行の `expert_cpu_util_max`（0.139）は下限であり，G5 は未達．G5 は律速の分類（N = 8 のみ）にだけ効くので，判定には影響しない．
- 環境の揺れ：前 12.887 問/秒・e2e p95 3.384 秒，後 12.951 問/秒・3.431 秒．どちらも事前登録の範囲 [11.127, 14.247]・[3.053, 4.067] の内．

**処理量（問/秒．parallel 30 合計．3 回の最小〜中央値〜最大）**
| 方式 | N=1 | N=2 | N=4 | N=8 |
|---|---|---|---|---|
| fs | 13.29〜13.48〜13.57 | 26.00〜27.06〜27.90 | 53.30〜53.55〜57.81 | 78.11〜82.35〜84.95 |
| dp24 | 16.11〜16.41〜16.51 | 33.57〜33.79〜34.71 | 63.82〜64.73〜65.89 | 98.53〜100.91〜101.35 |
| dp12 | 32.22〜32.62〜32.81 | 64.38〜64.59〜67.50 | 103.29〜121.47〜121.83 | 178.31〜186.21〜186.68 |

E(N)（効率）：fs 1.000/1.004/0.993/0.764，dp24 1.000/1.030/0.986/0.769，dp12 1.000/0.990/0.931/0.714（N = 1/2/4/8）．e2e p95 の中央値（秒）：fs 3.152→1.493→0.708→0.526，dp24 2.550→1.153→0.587→0.399，dp12 1.320→0.589→0.314→0.218．

**判定の規則の機械的な出力**
- H-P1（fs，N = 2 対 N = 1）：N = 2 の範囲は N = 1 より上で重ならず，中央値の比 2.008（1.5 以上）→ 成り立つ．
- H-P2：dp24 は比 2.059，dp12 は比 1.980．どちらも成り立つ．
- 頭打ちの N：fs・dp24・dp12 とも「N = 8 まで頭打ちなし」（各 N の範囲が N/2 の範囲より上で，比 1.2 以上）．
- 律速の分類（N = 8）：主（p30，3 回）も記録（p240）も，6 組全てが「質問者のプロセスが律速」（`requester_cpu_util_per_proc` の中央値 fs 0.983・dp24 1.001・dp12 1.002，p240 は 1.001）．事前登録に従い，N = 8 は物理 6 コアを超える点を添える．
- このイテレーションの結論（規則の機械的な適用）：**採用**．処理量の中央値が最大の N は 3 方式とも N = 8（fs 82.35，dp24 100.91，dp12 186.21 問/秒）．

**事前登録の予測との差（記録）**：fs の N = 8 は予測 55〜75 問/秒に対し 82.35 問/秒で，予測の上限を超えた．HTTP 要求は N = 8 の fs で 2,500〜2,718 要求/秒（予測 1,800〜2,400），1 ノードあたりの平均 86.9〜90.6 要求/秒，最大のノード 89.5〜92.3 要求/秒．`expert_cpu_util_max` は fs 0.443〜0.507，dp24 0.495〜0.507，dp12 0.751〜0.783（予測の 0.2〜0.5 に対し dp12 は 0.75 超）．dp12 の N = 8 は予測 150 問/秒を超え 186 問/秒．

**記録の系列（N = 8，各プロセス 30．合計 parallel 240）**：fs 60.83/61.49/61.17 問/秒（p30 の N = 8 の 82.35 より低い），e2e p95 5.6〜5.7 秒，HTTP 要求/秒 1,947〜1,968，`expert_cpu_util_max` 0.353〜0.372，`control_cpu_busy` 0.658〜0.667，再試行 8〜46，新しい接続 6,285〜6,772．dp24 76.29，e2e p95 4.649 秒．dp12 156.48，e2e p95 2.258 秒，`expert_cpu_util_max` 0.659．失敗は全て 0．

**制御点の CPU（記録）**：`control_cpu_busy`（論理 12 CPU 全体に対する割合）は N = 1 で 0.090，N = 2 で 0.170〜0.180，N = 4 で 0.294〜0.351，N = 8 で 0.634〜0.684．`control_cpu_softirq` は N = 8 で 0.025〜0.027．N = 8 では事前登録の 3 番（0.50 以上）にも当たるが，分類は 1 番（質問者のプロセスが律速）が先に当たる．

**専門家 1 台の要求の処理能力の見積もり（記録．平均要求/秒 ÷ `expert_cpu_util_max`．ノードが 1 コアで飽和すると仮定した推測）**：N = 8 の p30 で fs 189・dp24 173・dp12 111 要求/秒，p240 で 184・170・111 要求/秒．方式間でそろわず，`expert_cpu_util_max` は最も忙しいノードの値で平均の要求数とは分母の対象が違うので，下限として読む．

**方式間の処理量の比（中央値）**：dp24/fs は N = 1/2/4/8 で 1.218/1.249/1.209/1.225，dp12/fs は 2.421/2.387/2.268/2.261．1 問あたりの要求の数は fs 32.0・dp24 26.0・dp12 14.0（比 0.81・0.44）．

**注意点**
- 結果は未 commit（phase 3 で行う）．`results/20261015_iter8_*` と `iter8_specs.txt`・`iter8_summary.py`・`.out` は untracked．
- `fs_n2_p30_r1` の専門家 CPU が 19 台のみ（上記 G5）．連続実行の最初の N = 2 の実行で，ノード側のコンテナが start 直後の `/healthz` 読み取りを切断した可能性があるが，原因は調べていない（推測）．
- N = 8 の p30 でも `requester_cpu_util_per_proc` が 0.98〜1.0 のため，専門家の側（`expert_cpu_util_max` 最大 0.78）はまだ飽和していない．専門家の限界は今回の構成（制御点 1 台）では測れていない．

### Iteration 8 実行済み（2026-10-15．research-cycle の phase 3．判定：採用）

**変更**：`experiment.requester_procs` を 1 から 2・4・8 へ変えた（`parallel` は合計 30 で固定．`68308d8`．44 実行は全て同じ HEAD）．事前登録の規則・閾値は結果を見て変えていない．

**正しさの確認と環境の揺れ**：G1〜G3 は全 43 実行で成立（失敗 0 問，`selected_sources` は方式ごとに N・parallel・問の数によらず 100% 一致）．G4 は 2,940 問の hit@2 が 3 方式とも `device-probe-eval` の test の値と一致した（出力の fs の False は 300 問の anchor の 0.92 を同じ欄に入れた集計の表示上の誤りで，2,940 問の fs は 0.8041）．
G5 は `fs_n2_p30_r1` だけ未達（専門家 19 台）．G5 は N = 8 の律速の分類にだけ効くので，判定には影響しない．前後の flood_score × N = 1 × 300 問の p30（12.887・12.951 問/秒，e2e p95 3.384・3.431 秒）は規則の範囲の内で，Iteration 7 の前後（12.860・12.278）と同じ水準である．

**結果と判定**（parallel 30 合計．3 回の範囲．規則は「範囲が重ならないときだけ差」）

| 方式 | N=2 対 N=1（中央値の比．最も不利な組の比） | 頭打ちの N | N = 8 の処理量（中央値） | E(8) | N = 8 の律速の分類 | 規則の結論 |
|---|---|---|---|---|---|---|
| fs（主） | 2.008（26.00 ÷ 13.57 = 1.92） | なし（N = 8 まで） | 82.35 | 0.764 | 質問者のプロセス（per_proc 0.983） | H-P1 成立 |
| dp24 | 2.059（33.57 ÷ 16.51 = 2.03） | なし | 100.91 | 0.769 | 質問者のプロセス（1.001） | H-P2 成立 |
| dp12 | 1.980（64.38 ÷ 32.81 = 1.96） | なし | 186.21 | 0.714 | 質問者のプロセス（1.002） | H-P2 成立 |

- **このイテレーションの結論：採用**（H-P1 が成立）．以後の負荷の主張は，事前登録のとおり頭打ちの手前で最も処理量の高い N = 8（parallel 30 合計）の値で行う．ただし N = 8 でも質問者のプロセスが律速で，専門家の側の限界は測れていない（下の限界）．
- **ノイズか信号か**：N = 1 の 3 回の幅は中央値に対して fs −1.4%〜+0.7%，dp24 −1.8%〜+0.6%，dp12 −1.2%〜+0.6% で，Iteration 5〜7 の ±5% より狭い（2,940 問にした効果と読める）．N = 2 対 N = 1 は最も不利な組でも 1.92〜2.03 倍で，揺れより 2 桁大きい（信号）．
  N = 8 対 N = 4 も最も不利な組で 1.35（fs）〜1.50（dp24）倍で，頭打ちの閾値 1.2 を上回る．揺れが大きかったのは dp12 の N = 4 の r1（103.29．中央値 121.47 の −15%）と fs の N = 4 の r2（57.81．+8%）である．dp12 の N = 4 の r1 は 4 プロセスのうち 1 つだけ壁時計の時間が 28.45 秒（他の 3 つは 21.9〜22.5 秒），CPU 使用率 0.906 で，約 6 秒止まっていた．
  処理量は最後のプロセスが終えた時刻で決まるので，1 プロセスの一斉の待ち（B23 (b) と同じ形）が全体の値をそのまま下げる．中央値の判定はこの 1 回に左右されていない．
- **期待との一致**：H-P1・H-P2 は予測どおり．H-P3 の「N = 4 まではほぼ比例」は当たった（E(4) 0.93〜0.99）．「N = 8 で伸びが鈍る」も当たったが，事前の予測 55〜75 問/秒（fs）は外れ，82.35 問/秒だった．
  E(8) 0.71〜0.77 は 6 ÷ 8 = 0.75 とほぼ同じで，N = 8 の処理量は「物理 6 コア × N = 1 の処理量」（fs で 6 × 13.48 = 80.9）と読める．予測は 1 プロセスあたりの値を 12.5 問/秒（300 問の値）とし，6 コアの手前で頭打ちになる余裕を見ていたので低く出た．
  専門家の CPU が 1 コアに届かない予測は当たった（`expert_cpu_util_max` は fs 0.44〜0.51，dp24 0.50〜0.51）．dp12 だけは 0.75〜0.78 で予測（0.2〜0.5）を超えた．

**分析**

事実（記録から計算したもの）
1. **N = 8 の頭打ちは制御点の物理コアの数で説明できる**．fs の N = 8 の HTTP 要求は 2,500〜2,718 要求/秒で，質問者の CPU の合計（7.19〜7.79 CPU）で割ると 1 CPU 秒あたり 348〜349 要求で，N = 1（431 要求/CPU 秒）の約 0.8 倍である．物理 6 コアで割ると 417〜453 要求/秒で，N = 1 の 1 プロセスの値と同じ水準になる．
   8 プロセスは論理 CPU を 8 個使っていても，ハイパースレッディングで物理コアを分け合うので，仕事の量は物理 6 コア分にとどまる．`control_cpu_busy` は 0.63〜0.68（論理 12 CPU のうち約 8 個）で，softirq は 0.025〜0.027 と小さく，NIC・カーネルのネットワーク処理は律速の候補にならない．
2. **N = 8 でも専門家は飽和していない**．1 ノードあたりの要求は fs で平均 83〜91 要求/秒，最大のノードで 85〜92 要求/秒，`expert_cpu_util_max` は 0.44〜0.51 である．dp12 は最も忙しいノードに 157〜164 要求/秒が集まり（平均 83〜87 の約 1.9 倍），`expert_cpu_util_max` は 0.75〜0.78 である．
   device_probe は人気のあるデバイスに要求を集めるので，全体の要求の数が同じでも，専門家の側の飽和は fs より先に最も忙しいノードで起きる見込みである．最も忙しいノードの要求の数 ÷ `expert_cpu_util_max`（分母と分子の対象が同じノード）は fs 192・dp24 211・dp12 209 要求/秒で，ノードが 1 コアで飽和すると仮定すると専門家 1 台は約 190〜210 要求/秒を扱える（推測．1 コアで飽和するかは未確認）．
3. **parallel を合計 240 にすると，N = 8 の処理量は下がる**（fs 82.35 → 61.17，dp24 100.91 → 76.29，dp12 186.21 → 156.48）．質問者のプロセスの CPU は 1.0 に張り付いたままで，HTTP 要求は fs で 2,636 → 1,958 要求/秒に減り，新しい接続は約 1,200 → 6,300〜6,800，再試行は 0〜8 → 8〜46 に増える．
   同時の要求が多いほど 1 要求あたりの質問者の費用が増える形で，Iteration 5〜7（dp12 の parallel 16 が parallel 30 より速い）と同じ向きである．p240 の e2e p50（fs 3.5〜3.6 秒）と処理量の積は 213〜220 で parallel 240 に近く，閉じた系の Little の法則と整合する．
4. **方式間の処理量の比は N によらずほぼ一定である**（dp24/fs 1.21〜1.25，dp12/fs 2.26〜2.42）．1 問あたりの要求の数の比の逆数（32 ÷ 26 = 1.23，32 ÷ 14 = 2.29）と一致し，質問者が律速の間は「処理量 ∝ 1 ÷ 1 問あたりの要求の数」という Iteration 6・7 の式が N = 8 でも成り立つ．
5. **2,940 問の N = 1 の処理量（fs 13.48）は，同じ日の 300 問の anchor（12.89・12.95）より約 4% 高い**．300 問では始まりと終わりの同時の問の少ない区間の割合が大きいためと読める（推測）．300 問の Iteration 5〜7 の値と 2,940 問の値を直接比べない．

推測（上の事実と矛盾しないが，未確認のもの）
- N を 12（論理 CPU の数）に上げても，事実 1 のとおり物理 6 コアの仕事の量で頭打ちになり，処理量はほぼ伸びない．同じ制御点の上の質問者のプロセスの分割は N = 8 で使い切った．
- `fs_n2_p30_r1` の 11 台の `/healthz` の切断（"Server disconnected without sending a response"）は，per_node の池の古い keep-alive の接続を区間の前の GET が使い，ノードの側が既に閉じていたためと読める（質問者の `_post_with_retry` には再試行があるが，`/healthz` の読み取りには無い）．原因は調べていない．

**想定外の挙動**：上の dp12 の N = 4 の r1 の 1 プロセスの約 6 秒の停止と，`fs_n2_p30_r1` の G5 の欠け．集計の G4 の表示上の誤り（fs の欄に 300 問の anchor の値が混ざる．判定・結論には影響しない）．言語崩れ・発散・OOM は該当なし．

**学び**
- 1 台の制御点の上で質問者のプロセスを増やすと，処理量は物理コアの数まではほぼ比例して伸び，その先はハイパースレッディングの分しか伸びない（N = 8 で約 6 倍）．論理 CPU の使用率（プロセスごとに 1.0）だけを見ると「CPU に余裕がある」と誤読するので，効率 E(N) を 物理コアの数 ÷ N と比べて読む．
- 複数プロセスの処理量は最後のプロセスの終わりで決まるので，1 プロセスの数秒の停止が全体を 15% 下げる．プロセスごとの壁時計の時間（`requester_wall_s_per_proc`）を必ず見て，外れた回の原因をプロセスの単位で調べる．
- device_probe は専門家の側の負荷を最も人気のあるデバイスに集める（dp12 で平均の約 1.9 倍）．専門家の限界を論じるときは平均ではなく最も忙しいノードの要求の数と CPU で見る．
- 2,940 問にすると N = 1 の 3 回の揺れは ±2% 以内になった．処理量の高い条件では問の数を増やす方が，回数を増やすより安く揺れを抑えられる．

**限界**
- 専門家の側の限界はまだ測れていない．N = 8 でも律速は質問者のプロセス（物理 6 コアの制御点）で，`expert_cpu_util_max` は最大 0.78 である．約 190〜210 要求/秒という専門家 1 台の見積もりは，1 コアで飽和するという仮定に基づく推測であり，「専門家 1 台は少なくとも 157〜164 要求/秒を失敗なしで扱った（dp12 の最も忙しいノード）」が実測の下限である．
- N = 8 は物理 6 コアを超えており，`control_cpu_busy` も 0.63〜0.68 と事前登録の 3 番（0.50 以上）に当たる．質問者の律速は「制御点 1 台の限界」と言い換えられ，専門家の限界とは呼ばない．
- parallel を合計 30 に固定したので，N を増やすとプロセスあたりの同時の問の数は 30 → 4 程度に減る．N による伸びには，プロセスあたりの同時の要求が減って 1 要求あたりの費用が下がる効果（事実 3 の逆向き）が混ざっている可能性がある．E(2) が 0.99〜1.03 と 1 を下回らないのはこの効果と矛盾しない（推測）．
- G5 の欠け（`fs_n2_p30_r1`）は律速の分類には効かないが，原因は未調査である．
- 有線 LAN・検索だけ・閉じた系の処理量で，配置は話題と無関係な 30 台 × 5 受信箱の 1 通りである．専門家の CPU はノードのプロセスの CPU 時間で，docker・カーネルのネットワーク処理を含まない．

**次の一手**（backlog B27 [auto-decided]）：同じ制御点の上の質問者の分割は N = 8 で使い切った．専門家 1 台の飽和点は，30 台の全体に負荷を散らすのではなく，**少数の専門家ノードに質問者の全プロセスの負荷を集めて**測る（質問者の律速 約 2,600 要求/秒は，見積もりの専門家 1 台 約 200 要求/秒の 10 倍以上ある）．
`cluster` の形式とデバイスの役割は変えない．質問者を複数のデバイスに置く形（B25 (a)）は，30 台の全体の限界を測るときにだけ要るので，今回は人間の判断を求めない．

## Iteration 7: デバイスを単位に上位を選ぶ二段の方式の再現率と処理量

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B23 [auto-decided]（Iteration 6 は採用．前計算の後も処理量は「1 問の時間 = route_s ＋ 要求の数 ÷ R」，R ≈ 420 要求/秒で決まり，K = 75 は probe が 29.1 台に届くので flood_score と同じ程度にしかならない）．config.yml の levers 末尾の `experiment.routing: [device_probe]`（仮称）を，下のとおり **`experiment.routing: device_probe` と新しいキー `routing.probe_d.enronqa`（D）** として確定した（backlog B24）．
データ準備・ラベル・シャード・公開情報は作り直さない．既存の routing の値の挙動は変えない．

**コードと既存の結果から確かめたこと**（事実）
- 配置は 30 台 × 5 受信箱で，T4 から Iteration 6 まで同じ（`results/20261014_t4_flood_score_p1_r1/placement.json` と `results/20261015_iter6_cp75_pn_p30_r1/placement.json` はバイト単位で同じ）．
- 受信箱を単位に上位 K 個を選ぶ centroid_probe の届くデバイスの数（`results/20261014_iter4_probe_reach/`．test 2,940 問の平均）は K = 4 で 3.8，15 で 12.1，30 で 19.9，50 で 26.0，75 で 29.1 台であり，K を半分にしても届く台数はほとんど減らない．test の hit@2 は K = 30 で 0.6905，K = 75 で 0.7612，flood_score で 0.8041（Iteration 1 の dev は K = 30 で 0.6906，K = 75 で 0.7649）．
- Iteration 6 の per_node・前計算の下の parallel 30 の処理量は，K = 75 が 13.099〜13.444 問/秒（e2e p95 3.436〜3.558 秒），K = 30 が 18.709〜19.698 問/秒（2.311〜2.741 秒）である．R は K = 75 で 415〜426，K = 30 で 421〜444 要求/秒．
- 質問者の 2 段目（`Requester._route_by_candidate_probe`・`_probe`）は「候補のデータ源の名前」を受け取り，そのシャードを持つノードにだけ，候補のシャードの `shard_ids` で probe を送る．したがってデバイスを単位に選ぶ方式は，候補を「上位 D 台に載る全ての受信箱」にすれば同じ経路で流せる．

**調査で分かったこと**（tavily-search・tavily-extract．事実と推測を分けて書く）
- selective search の負荷の研究では，資源選択はシャード（本研究の受信箱）を単位に行い，シャードを機械へ割り当てる方針（Random と，学習の問の記録から負荷を均す Log-based）を別に扱う．機械の数が増えると Random の割り当てでは負荷が偏り，Log-based の割り当てで処理量が上がったと報告している
  （Y. Kim, J. Callan, J. S. Culpepper, A. Moffat, "Load-balancing in distributed selective search", SIGIR 2016, pp. 905–908．https://www.cs.cmu.edu/~callan/Papers/sigir16-yubink.pdf ）．同じ著者らの IRJ 2017（https://people.eng.unimelb.edu.au/ammoffat/abstracts/kccm17irj.pdf ）も，資源選択の後に「選ばれたシャードを持つ機械へ」問を送る構成を測っている．
  これらは「シャードを選び，その結果として届く機械が決まる」形で，**機械を単位に上位を選ぶ資源選択は，今回の検索では見つからなかった**（無いとは言えない）．本研究のように 1 台の質問者が要求の数で律速され，ノードの計算が無視できる（Iteration 4 の事実 1）条件では，届く機械の数を直接選ぶ方が式の上で効く（推測）．
- 分散のベクトル検索でも，パーティションの中心で問の行き先を選ぶ階層の設計が普通で，問ごとに触るノードの数が処理量を決める．SPire は，DSPANN が 1 問あたり全パーティションの 20〜80% を読むために処理量が伸びないこと，全ノードに問う Milvus+ は処理量が低いことを報告し，近い中心のパーティションを同じノードに集める配置は偏り（hot spot）で処理量を下げると述べている
  （"Scalable Distributed Vector Search via Accuracy Preserving Index Construction", arXiv:2512.17264, 2025．https://arxiv.org/abs/2512.17264 ．著者は今回の取得では確かめていない）．本研究の配置は受信箱の話題と無関係に散らばっているので，デバイスを単位に選んでも特定の台に問が集まりにくい一方，1 台に正解に近い受信箱が集まる効果も期待できない（推測）．
- 受信箱の得点のデバイスごとのまとめ方について：sample-based の資源選択（ReDDE・Rank-S など）は，標本の文書の得点を「票」として親のシャードに足し合わせる（Rank-S は順位で指数的に減らした得点の和．Kim ほか IRJ 2017 の §2 の説明）．和は「関連の文書を多く持つシャード」を上げる規則であり，CORI と ReDDE は大きさの正規化の向きが違うと整理されている
  （A. Kulkarni, "Efficient and Effective Large-scale Search", CMU 博士論文, 2013．https://www.lti.cs.cmu.edu/people/alumni/alumni-thesis/kulkarni_anagha-thesis.pdf ）．本研究の問は正解の受信箱が 1 個で，デバイスの 5 受信箱は話題と無関係に置かれているので，和は「正解を持つか」ではなく「似た受信箱が多いか」を測ってしまう（推測）．このため主のまとめ方は最大値とし，和はオフラインの参考に留める（下の事前登録）．

**問い**：(1) 受信箱の得点をデバイスごとに最大値でまとめた上位 D 台に probe を送ると，centroid_probe（K = 75・30）と同じ hit@2 を，より少ないデバイス（＝要求）で得られるか．(2) 得られるなら，実機の parallel 30 の処理量は要求の数の比のとおりに上がるか．

**B23 の要レビューへの対応**
- (a) 複数の質問者のプロセス・デバイスの構成は，この比較の後の候補のまま残す（どの方式の処理量も質問者 1 プロセスの能力の比較である点は限界に書く）．
- (b) 処理量の高い条件の揺れ（約 1 秒の一斉の待ち）への対策として，比べる centroid_probe（同じ K）を**同じ時間帯に device_probe と交互に 3 回ずつ流し**，Iteration 6 の値ではなく同じ時間帯の値と比べる．問の数は，オフラインの選択と実機の選択を照合するため 300 問のままとする（device_probe の想定の処理量 15〜23 問/秒では 1 回 13〜20 秒で，1 秒の待ちは 5〜8% に当たる．判定は 3 回の範囲が重ならないことを要るので，1 回の待ちで差を作り出すことはない）．
- (c) `experiment.http_pool` の既定値は `shared` のまま．全実行の仕様に `http_pool=per_node` を明示する．

**仮説**
- **H-D1（再現率．オフライン）**：最大値でまとめた device_probe は，同じ hit@2 を centroid_probe より少ないデバイスの数で得る．dev で K の hit@2 以上になる最小の D（D_K）が，K の届くデバイスの数より 1 割以上少ない要求の数になり，test の hit@2 が K の hit@2 − 0.01 以上である．
  根拠：最大値でまとめたデバイスの順位は「そのデバイスの受信箱が受信箱の順位に最初に現れる位置」の順なので，上位 D 台は，届くデバイスが D 台以下の上位 K 個を全て含む（包含．コードの確認項目にした）．さらに各台の残りの受信箱も probe されるので，同じ台数で再現率は centroid_probe 以上になる見込みである（推測）．
- **H-D2（処理量）**：parallel 30 で，device_probe（D_K）の処理量は同じ時間帯の centroid_probe（K）より高い．仕組み：1 問あたりの要求の数が（K の届くデバイス ＋ 2）から（D_K ＋ 2）に減る．
- **H-D3（待ち時間）**：parallel 30 で，device_probe（D_K）の e2e p95 は同じ時間帯の centroid_probe（K）より小さい．

**単一レバー**：`config.yaml` の `experiment.routing` を，Iteration 6 の `centroid_probe` から **`device_probe`** へ変える．D は新しいキー `routing.probe_d.enronqa`（既定は全 30 台の仮置き．評価の後に事前登録の規則の値を連続実行が書き換える）．1 段目（前計算した multi_centroid の採点）・2 段目（`/v1/probe`）・本照会は centroid_probe と同じで，**候補の単位（上位 K 個の受信箱 → 上位 D 台のデバイスの全ての受信箱）だけ**が違う．
- **まとめ方（事前登録）：最大値**（上の調査と包含の性質）．和はオフラインの評価に参考として並べ，判定に使わない．同点はデバイスが受信箱の並びに最初に現れた順の先を採る（`top_m` と同じ考え方）．
- **実装**（済み．下の commit）
  - `src/atrium/routing/advert.py`：`top_devices`（データ源の得点をデバイスごとに最大値でまとめ，上位 d 台に載るデータ源を返す）と `DeviceProbeRouter`（`candidates(query, sources, device_of_source)`・`select_among`．`select` はデバイスの対応を持たないので `ValueError`）．
  - `src/atrium/routing/__init__.py`：`make_router` に `device_probe` を登録（`routing.top_m` と `routing.probe_d` が要る）．`src/atrium/config.py`：`RoutingName` に `device_probe`，`RoutingConfig.probe_d` を追加．`config.yaml`：`routing.probe_d.enronqa: 30`（仮置き）．
  - `src/atrium/requester.py`：起動時にデータ源 → ノードの URL の表（1 データ源 1 シャードのものだけ）を作り，`DeviceProbeRouter` のときは `_route_by_candidate_probe` で候補を求める（複数のシャードのデータ源があればその問を失敗として記録する）．centroid_probe・flood_score などの経路は変えていない．
  - `src/atrium/device_probe_eval.py`・`atrium device-probe-eval`・`scripts/remote/device_probe_eval.sh`：下の事前登録の手順をコードに固定したオフラインの評価．
  - `tests/test_device_probe.py`（15 件．最大値と和の違い，同点の規則，包含の性質，オフラインの選択と実機のルーターの一致，D の選択と見込みの規則）と `tests/test_requester.py`（2 件．候補のデバイスにだけ候補のシャードで probe を送ること，複数台に分かれたデータ源で問が失敗すること）．pytest 263 件・`ruff check src tests`・`ruff format --check src tests`・`mypy src` は通過．
  - 連続実行：`results/20261015_iter7_load_summary/iter7_chain.sh`（`iter5_chain.sh` に `routing.probe_d.enronqa` を足したもの）と，仕様の雛形 `iter7_specs.template.txt`．
  - 文書：`docs/d0001_architecture.md`（方式の表），`docs/d0002_configuration.md`（`routing` の値と `probe_d.enronqa`），`docs/d0003_experiment_workflow.md`（コマンド），`docs/d0004_metrics.md`（出力の項目）．

**固定する構成**（Iteration 6 の per_node と同じ）：`dataset: enronqa`，`answer_mode: retrieval_only`，`question_limit: 2`（300 問），m = 2，`merge: score`，`query_embedding.enronqa: cached`，`seed` は既存の値，**`http_pool: per_node`（全実行で明示）**，前計算した 1 段目（`d510a21` 以降の実装），1 段目の採点はイベントループの上で同期，
公開情報はシャードにある既存のヘッダー込みの multi_centroid（C = 32），配置は T4 以来の 30 台 × 5 受信箱，各回の前に `mise run deploy`，失敗した問は流し直さない，連続実行の間は制御点で他のコンテナを動かさず commit もしない．専門家 30 台・質問者（制御点）の構成は T4 と同じ（運用ルール 8 のフルスペックの実機実験）．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**手順 1：オフラインの評価**（イメージを publish した後，実機の実行の前に 1 回．`bash scripts/tasks/remote.sh device_probe_eval 20261015_iter7_device_probe_eval 20261014_t4_flood_score_p1_r1 20261014_iter1_cascade_eval` → rsync で取得）
- D の候補：2, 4, …, 28, 30（偶数と全台）．まとめ方は最大値（主）と和（参考）．
- **D の選択（dev だけ）**：比べる K ∈ {75, 30} ごとに，最大値の device_probe の dev の hit@2 が，同じ採点の centroid_probe（K）の dev の hit@2 以上になる最小の D を D_K とする（無ければ「見込みなし」）．
- **見込みあり**：D_K ＋ 2 ≤ 0.9 ×（centroid_probe（K）の dev の届くデバイスの数の平均 ＋ 2）．1 問あたりの要求の数が 1 割以上減り，Iteration 6 の式で処理量が約 1.11 倍以上になる見込みの D だけを実機で測る（test の届くデバイスの数で見積もると，K = 75 は約 29.1 台なので D_75 ≤ 25.99，K = 30 は約 19.9 台なので D_30 ≤ 17.7，偶数の候補では 24 以下・16 以下．判定には dev の値を使う）．
- **H-D1（test での確認）**：見込みありの D_K について，test の hit@2 が centroid_probe（K）の test の hit@2 − 0.01 以上．
- **正しさの確認**（満たさなければ判定にも実機にも進まず，原因を調べて記録する）：`checks.passed`（centroid_probe の dev・test の hit@2 が Iteration 1 と 5e-4 以内で一致，flood_score の test が一致，D = 30 が flood_score と一致，包含の違反が全ての D と K で 0 問）．
- 見込みありが 1 つも無ければ，実機の実行は 0 回とし，結論は「棄却（オフラインで見込みなし）」とする．

**手順 2：実機の連続実行**（`iter7_specs.template.txt` を手順 1 の結果で展開した `iter7_specs.txt` を `iter7_chain.sh` で流す．run_id の接頭辞は実行日の `<YYYYMMDD>_iter7_`）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ 1 回も流さずに止め，backlog に `[needs-human]` を記録する．
2. 環境の揺れの確認（前）：flood_score × per_node の parallel 30 を 1 回．
3. 見込みありの K ごとに（K = 75 を先）：parallel 30 を centroid_probe（K）と device_probe（D_K）で交互に 3 回ずつ（cp r1 → dp r1 → cp r2 → dp r2 → cp r3 → dp r3），続けて device_probe（D_K）の parallel 1・16 を 3 回ずつ（曲線の記録）．D_75 = D_30 なら dp の系列は 1 回だけ流し，cp は両方の K で流す．
4. 環境の揺れの確認（後）：2 と同じ．
- 最大 26 実行（1 回 1〜3 分，全体で約 1.5 時間の見込み）．deploy の失敗の扱い・失敗率の打ち切り（routing・K・D ごと）は Iteration 4〜6 と同じ（`iter7_chain.sh` の実装のとおり）．

**指標**（Iteration 6 と同じ定義）：処理量，e2e_s の p50・p95，probe_s・retrieve_s・route_s の p50・p95，`probe_node_s_max`，失敗率とエラーの種類，`requester_cpu_util`，再試行の数，新しい接続の数，1 秒あたりの HTTP 要求の数，**1 問あたりの probe を受けたデバイスの数（`probe_recipients`）と probe したシャードの数**，300 問の hit@2，parallel 30 の R（記録）．

**正しさの確認（実機）**（満たさなければ判定に進まず，原因を調べて記録する）
- device_probe の全実行で，失敗の無い問の `selected_sources` が `device-probe-eval` の `live_selected_sources`（同じ D）と **100%** 一致し，全問で `probe_recipients` = D，`probe_shards` = 5D．
- centroid_probe の全実行で，失敗の無い問の `selected_sources` が `results/20261014_iter4_probe_reach` の選択（同じ K）と 100% 一致する．
- `run_meta.json` の `http_pool` が全実行で `per_node`，routing・K・D が仕様と一致する．

**環境の揺れの規則**（記録のため．比べる相手は同じ時間帯の centroid_probe なので，判定の基準は変えない）：前後 2 回の flood_score × per_node の p30 の処理量が [11.127, 14.247] 問/秒（Iteration 5・6 の 5 回の [最小 × 0.9, 最大 × 1.1]），e2e p95 が [3.053, 4.067] 秒に入るかを記録する．外れたら「環境が変わった」と記録し，同じ時間帯の centroid_probe の値を Iteration 6 の値と並べる．

**判定の規則**（比べる K ごと．比べる 2 条件の 3 回の範囲が重ならないときだけ「差がある」）
- **H-D1**：手順 1 の test の確認が成り立つ．
- **H-D2**：parallel 30 の device_probe（D_K）の 3 回の処理量の範囲が，同じ時間帯の centroid_probe（K）の 3 回の範囲より上にあり，重ならない．
- **H-D3**：parallel 30 の device_probe（D_K）の 3 回の e2e p95 の範囲が，同じ時間帯の centroid_probe（K）の範囲より下にあり，重ならない．
- **K ごとの結論**：
  - **採用**（device_probe（D_K）は，同じ再現率で centroid_probe（K）より負荷の軽い方式として以後の候補になる）：H-D1・H-D2・H-D3 が全て成り立ち，かつ parallel 30 の処理量の 3 回の中央値の比（dp ÷ cp）が 1.10 以上．1.10 は見込みの規則（要求の数が 0.9 倍以下）から予想される最小の倍率 1.11 に合わせ，Iteration 6 の同じ条件の 3 回の幅（K = 75 で中央値の −2.3%〜+0.2%，K = 30 で −4.9%〜+0.1%）の約 2 倍とした．
  - **部分的に支持**：H-D1 が成り立ち，H-D2 か H-D3 の少なくとも一方が成り立つが，採用の条件を満たさない．
  - **棄却**：H-D1 が成り立たない，H-D2 と H-D3 がどちらも成り立たない，またはその K で見込みありの D が無い．
- **このイテレーションの結論**：主は K = 75（Iteration 1 の規則で選んだ K）の結論．K = 75 で見込みありの D が無ければ K = 30 の結論を主とする．どちらも見込みなしなら「棄却（オフラインで見込みなし）」．もう一方の K の結論は記録する．
- 記録（判定に使わない）：parallel 1・16 の device_probe の曲線，和でまとめた hit@2 の曲線，Iteration 6 の式による処理量の予測（1 ÷（route_s ＋（D ＋ 2）÷ R），R は同じ時間帯の centroid_probe の R）と実測の比，300 問の hit@2．

**ノイズの見積もり**：オフラインの hit@2 は決定的（同じ埋め込み・同じ k-means の種）で，揺れは問の標本の揺れだけである（test 2,940 問の hit@2 の 95% 区間の幅は約 ±0.015．Iteration 1）．H-D1 の許容 0.01 はこの区間より狭く，「劣らない」を緩く判定しない側に置いた．
実機の処理量の 3 回の幅は Iteration 6 で中央値の ±5% 以内（parallel 30 の K = 75・30）であり，採用の 1.10 倍と範囲が重ならない規則で区別できる．

**期待効果**（推測．判定に使わない）：D_75 は 20〜26，D_30 は 14〜20 のどこかで，どちらの K も見込みあり（D_75 ≤ 24，D_30 ≤ 16）にぎりぎり入るかどうかである．見込みありなら，parallel 30 の処理量は K = 75 の比較で約 13.4 → 15〜17 問/秒，K = 30 の比較で約 19.7 → 22〜25 問/秒（R ≈ 420 の式）．`requester_cpu_util` は 1.0 に張り付いたままの見込みで，処理量の差は要求の数の比でほぼ決まる．
和でまとめた hit@2 は最大値より低い見込み．

**限界**：有線 LAN・300 問・検索だけ・単一の質問者（1 プロセス・1 コア）であり，比べるのは質問者が扱える要求の数で，専門家の側の限界は測らない．配置は話題と無関係な 30 台 × 5 受信箱の 1 通りで，1 台の受信箱の数や話題の偏った配置では結果が変わりうる．
device_probe は 1 データ源 1 シャード（EnronQA）でしか使えない．D は dev で選び，test は H-D1 の確認にだけ使う．

**次の候補**：採用か部分的に支持なら，複数の質問者のプロセス・デバイスの構成（専門家の側の限界の測定．B23 (a)）．棄却なら，デバイスの数を減らしても再現率を保てない理由（正解の受信箱の 1 段目の順位の分布）を記録し，同じく複数の質問者の構成へ進む．

### 実行結果（2026-10-15．rc-executor．解釈・判定は phase 3）

実装は `66f43f1` のまま（コード変更なし．実験中の commit なし）．docker 権限は ok．事前登録どおり手順 1 → 手順 2 の 26 実行を完了した（失敗率は全て 0．deploy・start の再試行なし）．
連続実行の最初の起動は，仕様ファイルを相対パスで渡したため 1 実行も流さずに終了した（ログは `iter7_chain.log`（リポジトリ直下）に残る）．絶対パスで起動し直し，以降の 26 実行は 08:02〜09:48 に流れた．

**手順 1（`results/20261015_iter7_device_probe_eval/`）**：`checks.passed` = true（差の最大 0.0，包含の違反 0 問）．選択の規則の出力：

| K | dev の目標 hit@2 | dev の届くデバイス | D_K | 要求の数の比 | 見込みあり | test の cp hit@2 | test の dp hit@2 | 劣らない（−0.01） |
|---|---|---|---|---|---|---|---|---|
| 75 | 0.7649 | 29.095 | 24 | 0.836 | True | 0.7612 | 0.7837 | True |
| 30 | 0.6906 | 19.826 | 12 | 0.641 | True | 0.6905 | 0.6830 | True |

**手順 2**：結果は `results/20261015_iter7_*`，集計は `results/20261015_iter7_load_summary/iter7_summary.py` → `iter7_summary.out`，仕様は `iter7_specs.txt`（K = 75（D = 24）→ K = 30（D = 12）の順．最大 26 実行のとおり）．

parallel 30 の 3 回（最小〜最大．中央値）：

| 条件 | 処理量（問/秒） | e2e p95（秒） | 1 問あたりの要求 | probe を受けたデバイス | 300 問の hit@2 |
|---|---|---|---|---|---|
| cp75 | 12.733〜13.513（12.819） | 3.476〜3.819（3.496） | 31.1 | 29.06 | 0.8700 |
| dp24 | 14.892〜16.067（15.638） | 2.936〜3.221（3.154） | 26.0 | 24 | 0.9200 |
| cp30 | 19.128〜19.579（19.533） | 2.280〜2.417（2.364） | 21.9 | 19.87 | 0.7800 |
| dp12 | 29.434〜31.270（30.752） | 1.451〜1.733（1.478） | 14.0 | 12 | 0.7900 |

中央値の比（dp ÷ cp）：K = 75 で 1.220，K = 30 で 1.574．dp の parallel 1・16（3 回）：dp24 は 19.747〜19.917・15.594〜17.622 問/秒，dp12 は 31.927〜32.420・33.961〜34.539 問/秒（e2e p95：dp24 p1 0.052 秒，p16 1.267〜1.803 秒，dp12 p1 0.033 秒，p16 0.620〜0.663 秒）．
`requester_cpu_util`：p30 は dp24 の 0.965・1.001・1.000，dp12 の 1.002・0.999・1.002，cp は全て 1.001．dp の p1 は 0.80〜0.88．

**正しさの確認（実機）**：不一致・欠け 0 件．device_probe の全 18 実行で `selected_sources` が `live_selected_sources`（同じ D）と 100% 一致（300 問），全問で `probe_recipients` = D・`probe_shards` = 5D．centroid_probe の 6 実行で `iter4_cp{K}_p1_r1` の選択と 100% 一致．全 26 実行で `http_pool` = per_node，routing・K・D が仕様と一致．
**環境の揺れ**：前 12.860 問/秒・e2e p95 3.652 秒，後 12.278 問/秒・3.733 秒．どちらも規則の範囲（[11.127, 14.247]・[3.053, 4.067]）の内．

**事前登録の規則の機械的な出力**（解釈は phase 3）：
- K = 75（D = 24）：H-D1 True，H-D2 True（範囲が重ならず dp が上），H-D3 True（dp が下），比 1.220 ≥ 1.10 → 採用．
- K = 30（D = 12）：H-D1 True，H-D2 True，H-D3 True，比 1.574 ≥ 1.10 → 採用．
- このイテレーションの結論（主は K = 75）：採用．
- 記録：R（cp の 3 回の中央値）は K = 75 で 406.1，K = 30 で 440.3 要求/秒．Iteration 6 の式での予測に対する dp の実測の比は，K = 75 で 0.977・1.054・1.026，K = 30 で 1.042・0.982・1.025．和でまとめた hit@2 は手順 1 の曲線（D = 24 で dev 0.7754，D = 12 で 0.6530）．

**未達・注意**：事前登録の基準で未達なし．300 問の hit@2 は dp24 0.9200（cp75 0.8700．flood_score の anchor は 0.9200），dp12 0.7900（cp30 0.7800）で，オフラインの test（0.7837・0.6830）より高い（問の集合が違う）．`dp24_pn_p16_r2` の処理量（15.594）と e2e p95（1.803 秒）は他の 2 回（17.6・1.27〜1.30 秒）と離れている（原因は未調査）．

### Iteration 7 実行済み（2026-10-15．research-cycle の phase 3．判定：採用）

**変更**：`experiment.routing` を `centroid_probe` から `device_probe`（受信箱の得点をデバイスごとに最大値でまとめ，上位 D 台の全ての受信箱に probe する二段の方式．D は `routing.probe_d.enronqa`）へ変えた（`66f43f1`．26 実行は全て同じ HEAD．`run_meta.json` の `git_head` の `-dirty` は journal の未 commit の変更によるもので，Iteration 6 と同じ）．
D は事前登録の規則で dev から D_75 = 24，D_30 = 12 と決まり，どちらも見込みあり（要求の数の比 0.836・0.641）だった．事前登録の規則・基準は結果を見て変えていない．

**正しさの確認と環境の揺れ**：オフラインの `checks.passed` = true（centroid_probe・flood_score の hit@2 の差 0.0，D = 30 が flood_score と一致，包含の違反は全ての D・K で 0 問）．実機では device_probe の 18 実行の `selected_sources` がオフラインの `live_selected_sources` と 100% 一致し，全問で `probe_recipients` = D・`probe_shards` = 5D，
centroid_probe の 6 実行は Iteration 4 の選択と 100% 一致した．`http_pool` は全 26 実行で `per_node`．前後の flood_score × per_node の p30（12.860 問/秒・p95 3.652 秒，12.278 問/秒・3.733 秒）は規則の範囲の内．失敗率は全実行で 0．

**結果と判定**（parallel 30．3 回の範囲．規則は「範囲が重ならないときだけ差」）

| K（D_K） | H-D1（test hit@2．dp 対 cp） | H-D2（処理量．dp 対 cp） | H-D3（e2e p95．dp 対 cp） | 中央値の比 | 結論 |
|---|---|---|---|---|---|
| 75（24．主） | 成立（0.7837 対 0.7612） | 成立（14.89〜16.07 対 12.73〜13.51） | 成立（2.94〜3.22 秒 対 3.48〜3.82 秒） | 1.220 | 採用 |
| 30（12．記録） | 成立（0.6830 対 0.6905．許容 −0.01 の内） | 成立（29.43〜31.27 対 19.13〜19.58） | 成立（1.45〜1.73 秒 対 2.28〜2.42 秒） | 1.574 | 採用 |

- **このイテレーションの結論：採用**．device_probe（D_K）は，同じ再現率で centroid_probe（K）より負荷の軽い方式として以後の候補になる．
- **ノイズか信号か**：同じ時間帯の cp75 の 3 回（12.73〜13.51）は Iteration 6 の cp75（13.10〜13.44）と重なり，cp30（19.13〜19.58）も Iteration 6（18.71〜19.70）と重なる．比べる相手の水準は日をまたいで再現している．
  K = 75 の dp の最小（14.89）と cp の最大（13.51）の比は 1.10 で，最も不利な組み合わせでも 3 回の揺れ（中央値に対して cp75 で −0.7%〜+5.4%，dp24 で −4.8%〜+2.7%）を上回る（信号．ただし採用の倍率 1.10 に対して中央値の比 1.22 の余裕は約 2 倍で，K = 30 の 1.57 ほど大きくない）．K = 30 は最も不利な組み合わせでも 1.50 倍で，揺れより 1 桁大きい．
- **期待との一致**：事前の推測（D_75 は 20〜26，D_30 は 14〜20，処理量は K = 75 の比較で 15〜17，K = 30 の比較で 22〜25 問/秒）に対して，D_75 = 24・15.6 問/秒は範囲の内．D_30 = 12 は推測より小さく，そのぶん処理量 30.8 問/秒は推測を上回った．
  処理量は Iteration 6 の式 1 ÷（route_s ＋（D ＋ 2）÷ R）の予測と 0.977〜1.054 で一致した．「和は最大値より低い」の推測は D ≤ 22 では dev・test とも当たり，D ≥ 24 では差が小さく向きが揃わない（D = 24 の和 対 最大値は，dev で 0.7754 対 0.7734，test で 0.7755 対 0.7837）．

**分析**

事実（記録から計算したもの）
1. **同じデバイスの数で比べると，device_probe の再現率は centroid_probe より高い**．test の曲線で，D = 12（12 台）の hit@2 0.6830 は，届くデバイスが同じ約 12 台の centroid_probe（K = 15．12.05 台）の 0.6218 より 0.061 高い．D = 20（20 台）の 0.7605 は，29.1 台に届く K = 75 の 0.7612 とほぼ同じである．
   受信箱を単位に選ぶと，選んだ受信箱の載る台にある他の受信箱を probe しないが，1 問あたりの要求の数（質問者の費用）はデバイスの数で決まるので，同じ要求の数のまま probe する受信箱を 5 倍まで増やせる．これが D_K が K の届くデバイスの数より小さくなる仕組みである（包含の性質は全 D・K で違反 0）．
2. **処理量は要求の数の比のとおりに上がった**．parallel 30 の R（1 秒あたりの要求の数）は dp24 で 387〜418，dp12 で 412〜438 で，cp（396〜428）と同じ範囲にある．`requester_cpu_util` は dp の p30 で 0.965〜1.002 と 1 コアに張り付いたままで，質問者 1 プロセスが律速の構図は変わらない．
   dp12 の処理量は parallel 16（33.96〜34.54 問/秒．R 475〜484）の方が parallel 30（29.43〜31.27．R 412〜438）より高い．同時の要求が増えると 1 要求あたりの費用が増える形（Iteration 5・6 と同じ）である．
3. **300 問の hit@2 では dp24（0.92）が flood_score（0.92）と同じで，cp75（0.87）より高い**．ただし 300 問は test のオフラインと別の集合で，test 2,940 問では dp24 0.7837，flood_score 0.8041 と差が残る．test の曲線で flood_score と同じ hit@2 になるのは D = 30（全台）だけである．
4. **`dp24_pn_p16_r2` の低さ（15.59 問/秒・e2e p95 1.80 秒）は，Iteration 6 の事実 5 と同じ形の一斉の待ちと読める**．経過時間は 19.2 秒（r1 17.1 秒，r3 17.0 秒）で，e2e が 1.5 秒を超えた問は 39 問（r1 1 問，r3 5 問），最大 2.95 秒，`requester_cpu_util` は 0.944 と低い．判定に使わない parallel 16 の記録であり，結論に影響しない．

推測（上の事実と矛盾しないが，未確認のもの）
- D_75 = 24 は dev の規則で選んだ値で，test では D = 22（0.7735）でも K = 75（0.7612）を上回る．dev と test の差（約 0.01）が D の刻み 2 台の hit@2 の差と同じ大きさなので，D の最小値は 1〜2 台の幅でしか決まらない．
- test の hit@2 の dp24 と cp75 の差（+0.0225）は，独立 2 標本の 95% 区間の半幅（約 0.015）より大きいが，対応ありの検定（McNemar）は行っていない（device-probe-eval は問ごとの hit を出力しない）．「劣らない」の判定には十分で，「高い」とは主張しない．

**想定外の挙動**：上の事実 4 の一斉の待ち（言語崩れ・発散・OOM は該当なし）．連続実行の最初の起動が，仕様ファイルを相対パスで渡したため 1 実行も流さずに終わった（リポジトリ直下の未追跡の `iter7_chain.log`．中身は `ALL DONE` の 1 行で，commit に含めない）．

**学び**
- 1 問あたりの要求の数が費用を決める構成では，二段の方式の 2 段目は「何を probe するか（受信箱）」ではなく「誰に要求を送るか（デバイス）」を単位に選ぶ．要求を送るデバイスの受信箱は全て probe しても費用は増えないので，同じ要求の数で再現率が上がる．
  受信箱を単位にした selective search の資源選択の考え方を，要求の数が律速の構成へそのまま持ち込むと損をする．
- 受信箱の得点のまとめ方は，選ぶ台が少ないとき（D ≤ 22）に最大値の方が和より高く，ほぼ全台を選ぶときは差が小さい．正解が 1 個の問では最大値（そのデバイスの最良の受信箱の順位）を使う．
- 連続実行のスクリプトには仕様ファイルを絶対パスで渡す（相対パスでは 1 実行も流れずに `ALL DONE` で終わり，失敗に見えない）．

**限界**
- 質問者は 1 プロセス・1 コアのままで，比べたのは「質問者が扱える要求の数」である．専門家の側の限界は測っていない．有線 LAN・300 問・検索だけ．
- 配置は話題と無関係な 30 台 × 5 受信箱の 1 通りである．1 台の受信箱の数が違う配置や，話題の偏った配置では，D と再現率の関係が変わりうる．device_probe は 1 データ源 1 シャードの EnronQA でしか使えない．
- device_probe の hit@2 は flood_score（test 0.8041）より低い．D = 24 で −0.020，D = 12 で −0.121 である．要求の数と再現率の兼ね合いの曲線（`results/20261015_iter7_device_probe_eval/analysis_report.md` の dev・test の表）を主張し，flood_score と同じ再現率とは言わない．

**次の一手**（backlog B25 [auto-decided]）：事前登録の「次の候補」どおり，Iteration 8 は**複数の質問者のプロセスの構成**とし，専門家の側の限界を測る．方式は flood_score・device_probe（D = 24・12）を使う．
制御点 wafl-ctrl5 は 12 コアで，今の質問者は 1 コアしか使っていないので，同じ制御点の上で質問者のプロセスの数を増やす（追加だけの新しい設定キー．既定は今の 1 プロセス）．`cluster.requester` を複数のデバイスにする形は設定の形式とデバイスの役割を変えるので，人間の判断に残す．

## Iteration 6: 1 段目の採点の重心の前計算による二段の方式の処理量の測り直し

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B21 [auto-decided]（Iteration 5 は採用．per_node の下で multi_centroid・centroid_probe の処理量は 1 段目の採点 route_s 約 0.111 秒で決まっている）．config.yml の levers 末尾の `requester.advert_scoring: [precomputed]`（仮称）を，下の B22 のとおり**設定キーではなくコードの置き換え**として確定した．
データ準備・ラベル・シャード・公開情報は作り直さない．

**コードと既存の結果から確かめたこと**（事実）
- `MultiCentroidRouter.score`（`src/atrium/routing/advert.py`）は，問ごと・データ源ごとに `np.asarray(advert.centroids, np.float32)` で Python のリスト（`Advert.centroids: list[list[float]]`．pydantic のモデル）から行列を作っていた．質問者は 1 問で 150 受信箱を採点するので，1 問あたり 150 回の変換になる．
  `CentroidProbeRouter` の 1 段目（`candidates`）は内部の `MultiCentroidRouter(K)` を使うので，同じ費用を持つ．`SourceProfile` は質問者の起動時に `build_source_profiles` で 1 回だけ作られ，以後は変わらない．
- 質問者の `_route`・`_route_by_candidate_probe`（`src/atrium/requester.py`）は採点をイベントループの上で同期に行い，その時間を `timings.route_s` に記録する．Iteration 5 の per_node の p30 で route_s の p50 は multi_centroid 0.1113〜0.1119 秒，K = 75 は 0.113 秒で，parallel 1〜30 を通して変わらない．
- Iteration 5 の per_node の基準値（3 回の範囲．`results/20261015_iter5_load_summary/iter5_summary.out`）：multi_centroid の p30 は処理量 8.519〜8.566 問/秒，e2e p95 4.865〜5.234 秒．K = 75 の p30 は処理量 5.34〜5.39 問/秒，e2e p95 8.49〜8.52 秒．flood_score の p30 は処理量 12.534〜12.785 問/秒，e2e p95 3.392〜3.626 秒．
  per_node の multi_centroid は p30 しか測っておらず，K = 30 は per_node で測っていない．
- 実装の後の操作端末での測定（150 個の乱数の公開情報，C = 32，768 次元．実データ・制御点ではない）：1 段目の 1 問あたりの時間は，リストから変換する形で 0.105 秒，前計算した行列では m = 2・K = 75 とも 0.0010 秒だった．150 個の `SourceProfile` を作る時間（前計算込み）は 0.29 秒で，起動時に 1 回だけかかる．

**調査で分かったこと**（tavily-search・tavily-extract．事実と推測を分けて書く）
- 分散の selective search では，資源選択（どのシャードに問い合わせるかの採点）の計算の費用が処理量に効く．Kim, Callan, Culpepper, Moffat は，Rank-S（標本の索引を引く資源選択）が Taily（統計だけを見る資源選択）より正確だが，資源選択の計算の費用のために待ち時間が長く処理量が低くなりやすく，資源選択を複数の機械に複製して偏りを直す必要があったと報告している
  （Y. Kim, J. Callan, J. S. Culpepper, A. Moffat, "Efficient distributed selective search", Information Retrieval Journal 20, 221–252, 2017．https://link.springer.com/article/10.1007/s10791-016-9290-6 ，本文 https://people.eng.unimelb.edu.au/ammoffat/abstracts/kccm17irj.pdf ）．
  本研究の 1 段目（multi_centroid の採点）は資源選択に当たり，その費用が質問者 1 プロセスで直列になって処理量を決めている点は同じ構図である（対応づけは推測）．
- asyncio のイベントループの上の CPU を使う処理は，その間ループ全体を止める（Python documentation, "Event loop", "Executing code in thread or process pools"．https://docs.python.org/3/library/asyncio-eventloop.html ）．route_s が parallel によらず一定で，処理量が約 1 / route_s で頭打ちになる Iteration 4・5 の観測と合う．
  executor へ移す案もあるが，numpy の行列積は GIL を外すものの，リストからの変換（Python のオブジェクトを 1 要素ずつ読む）は GIL を持ったまま動くので，移しても 1 コアの CPU の取り合いは残る（推測．測っていない）．そこで費用そのものを除く前計算を選ぶ．
- Python のリストから numpy の配列を作るときの 1 要素ごとの費用についての一次の資料は，今回の検索では見つからなかった（検索の結果は一般の解説だけだった）．費用の大きさは上の操作端末の測定（0.105 秒 → 0.001 秒）だけを根拠にする．

**問い**：(1) route_s の 0.111 秒は重心の変換でほぼ全部が説明できるか（前計算で 0.01 秒未満になるか）．(2) 1 段目の費用を除くと，multi_centroid と centroid_probe の処理量は何で決まるか（Iteration 5 の「1 問の時間 = route_s ＋ 要求の数 ÷ R」，R ≈ 400 要求/秒の式がそのまま当てはまるか）．
(3) 1 段目の費用を除いた状態で，K = 30・75 の二段の方式は flood_score（全員に聞く方式）より処理量で上回るか（要求の数は K = 75 で約 31，K = 30 で約 22，flood_score は 32）．

**B21 の要レビューへの対応**
- (a) **1 段目の採点の費用の除去を，デバイスを単位に上位を選ぶ方式より先にする**（B21 の順序のとおり）．デバイスを単位に選ぶ方式も同じ 1 段目の採点を持つので，この費用を残すと比較が「変換の費用」の比較になる．デバイスを単位に選ぶ方式は Iteration 7 の候補として残す．
- (b) **`experiment.http_pool` の既定値は `shared` のまま変えず，全 38 実行の仕様（`iter6_specs.txt`）に `http_pool=per_node` を明示する**．既定値の変更は人間の判断に残す．
- (c) 複数の質問者のプロセス・デバイスの構成は，この比較の後の候補として残す．このイテレーションの結果で `requester_cpu_util` が 1.0 に張り付いたまま処理量が R ≈ 400 要求/秒の式で決まるなら，その時点で次の候補になる．
- (d) d0006 §6.3 の T4 の訂正は Iteration 5 で済んでいる．このイテレーションの結果で，外部レビュー R4 (b)（multi_centroid が 1 問ずつで flood_score より遅い理由）の答えを d0006 §6.3 に足す（phase 3）．

**仮説**
- **H-S1（機構）**：前計算で，multi_centroid・K = 75・K = 30 の全実行の route_s の p50 が 0.010 秒未満になる（Iteration 5 は 0.111〜0.113 秒）．
- **H-S2（multi_centroid の処理量）**：multi_centroid × per_node の parallel 30 の処理量は Iteration 5（8.519〜8.566 問/秒）より上がる．
- **H-S3（K = 75 の処理量）**：K = 75 × per_node の parallel 30 の処理量は Iteration 5（5.34〜5.39 問/秒）より上がる．
- **H-S4（待ち時間）**：multi_centroid と K = 75 の parallel 30 の e2e p95 は Iteration 5（multi_centroid 4.865〜5.234 秒，K = 75 8.49〜8.52 秒）より下がる．
- **H-S5（記録．方式の順序）**：1 段目の費用を除いた parallel 30 で，処理量の順は multi_centroid ＞ K = 30 ＞ K = 75 ≈ flood_score になる（要求の数の順．推測）．K = 30・75 と flood_score（Iteration 5 の per_node の値）の比較を「範囲が重ならない」の規則で記録する．

**単一レバー**：1 段目の採点の重心の持ち方．`MultiCentroidRouter.score` が問ごと・データ源ごとに `Advert.centroids` のリストから行列へ変換していたのを，**`SourceProfile` を作るときに 1 回だけ (C, 次元) の fp32 の行列（`SourceProfile.advert_centroids`）にして，採点はそれを使う**形に変える．他は全て Iteration 5 の per_node の構成に固定する．
- **形（事前登録）：設定キーにせず，コードを置き換える**（理由は backlog B22）．比べる相手は Iteration 5 の per_node の値（`cd42b41` のイメージ）とし，環境の揺れは採点を持たない flood_score × per_node の p30 で前後に確かめる．
- **実装**（済み．下の commit）
  - `src/atrium/routing/__init__.py`：`SourceProfile` に `advert_centroids: F32Array | None = field(init=False, repr=False, compare=False)` を足し，`__post_init__` で `np.asarray(advert.centroids, np.float32)`（advert が無ければ None）を入れる（frozen なので `object.__setattr__`）．`dataclasses.replace`（`with_card_embeddings` が使う）でも作り直される．
  - `src/atrium/routing/advert.py`：`MultiCentroidRouter.score` は `source.advert_centroids` を使う（advert が無いときの `ValueError` は今と同じ）．`CentroidProbeRouter` の 1 段目はこれを通るので自動で速くなる．`CardSimRouter` の `card_embedding` の変換（1 本 768 要素）はこのレバーの外とし，変えない．
  - `tests/test_advert_routing.py`：前計算の行列が fp32 で中心と一致すること，advert の無いデータ源は None であること，`replace` で作り直されること，採点の値がリストから変換したときと一致すること（選択を変えないことの単体の確認）の 4 件を足した．pytest 246 件・`ruff check src tests`・`ruff format --check`・`mypy src` は通過．
  - 連続実行：`results/20261015_iter5_load_summary/iter5_chain.sh` をそのまま使い（`setcfg` は `http_pool`・`routing`・`parallel`・`routing.probe_k.enronqa` を扱える），仕様は `results/20261015_iter6_load_summary/iter6_specs.txt`（38 実行．この commit に含める）．
    集計は `iter5_summary.py` を元に `iter6_summary.py` を rc-executor が作る（指標は同じ．基準値の読み込み先に Iteration 5 の per_node の実行と Iteration 4 の K = 30 の p1 r1 を足す）．

**固定する構成**（Iteration 5 の per_node と同じ）：`dataset: enronqa`，`answer_mode: retrieval_only`，`question_limit: 2`（300 問．T4 と同じ集合），m = 2，`merge: score`，`query_embedding.enronqa: cached`，`seed` は既存の値，**`http_pool: per_node`（全実行で明示）**，`MAX_CONNECTIONS_PER_NODE` = 64，
`keepalive_expiry` 5.0 秒，`RETRIEVE_RETRIES` = 2，`RETRIEVE_TIMEOUT_S` = 120，ノードの uvicorn の設定（既定），1 段目の採点はイベントループの上で同期（executor へ移さない），公開情報はシャードにある既存のヘッダー込みの multi_centroid（C = 32），各回の前に `mise run deploy`，失敗した問は流し直さない，
連続実行の間は制御点で他のコンテナを動かさず commit もしない．専門家 30 台・質問者（制御点）の構成は T4 と同じ（運用ルール 8 のフルスペックの実機実験）．

### 事前登録（2026-10-15．実装の後，実行の前に commit・push する．結果を見て変えない）

**実行の順序と回数**（計 38 実行．1 回 2〜5 分，全体で約 2.5〜3 時間の見込み．run_id の接頭辞は `20261015_iter6_`．仕様は `iter6_specs.txt` の並びのとおり）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ実行を 1 回も始めずに止め，backlog に `[needs-human]` を記録する．
2. **環境の揺れの確認（前）**：flood_score × per_node の parallel 30 を 1 回（`anchor_fs_pn_p30_pre`）．flood_score は 1 段目の採点を持たないので，このレバーで変わらないはずである．
3. **multi_centroid × per_node**：parallel 1・4・16・30 × 3 回（`mc_pn_p<parallel>_r<1|2|3>`．12 実行）．
4. **centroid_probe（K = 75．判定の主）× per_node**：parallel 1・4・16・30 × 3 回（`cp75_pn_p<parallel>_r<n>`．12 実行）．
5. **centroid_probe（K = 30．記録）× per_node**：parallel 1・4・16・30 × 3 回（`cp30_pn_p<parallel>_r<n>`．12 実行）．
6. **環境の揺れの確認（後）**：2 と同じ条件を 1 回（`anchor_fs_pn_p30_post`）．
- deploy の失敗の扱い（5 分おきに 3 回まで再試行，それでも失敗した回は飛ばして後で同じ run_id で流す．失敗率に数えない）と，失敗率の打ち切り（同じ条件の 3 回とも失敗率が 10% を超えたら，その系列のより大きい parallel は飛ばして記録する）は Iteration 4・5 と同じ（`iter5_chain.sh` の実装のとおり）．

**指標**（Iteration 5 と同じ定義）：処理量（成功した問の数 ÷ `run_meta.json` の finished_at − started_at），e2e_s の p50・p95，probe_s・retrieve_s の p50・p95，**route_s の p50・p95**，`probe_node_s_max` の p50・p95，失敗率とエラーの種類，`requester_cpu_util`，再試行の数（`requester.log` の `retrying` の行の数），
新しい接続の数（access log の送信元ポートの数），1 秒あたりに扱った HTTP 要求の数（処理量 × 1 問あたりの要求の数），1 問あたりに probe を受けたデバイスの数（K = 30・75），300 問の hit@2（参考）．
加えて，Iteration 5 の式の当てはまりとして，parallel 30 の各方式で R = 1 問あたりの要求の数 ÷（1 / 処理量 − route_s の p50）を出す（記録．判定に使わない）．

**正しさの確認**（満たさなければ判定に進まず，原因を調べて記録する）
- 全ての実行で，失敗の無い問の `selected_sources` が，同じ方式・同じ K の基準の選択と **100%** 一致する（前計算は同じ fp32 の値を同じ行列積に渡すので，選択はビット単位で同じになるはずである．Iteration 5 の 99% より厳しくする）．
  基準は multi_centroid が T4 の `20261014_t4_multi_centroid_p1_r1`，K = 75 が `20261014_iter4_cp75_p1_r1`，K = 30 が `20261014_iter4_cp30_p1_r1`（multi_centroid と K = 75 は，Iteration 5 の per_node の全実行がこの基準と 100% 一致した系列である．K = 30 は per_node で流していない）．
- `run_meta.json` の `http_pool` が全実行で `per_node`，routing と K が仕様と一致する．
- 単体テストで，採点の値がリストから変換したときと一致する（上の 4 件）．

**環境の揺れの規則**：前後 2 回の flood_score × per_node の p30 の処理量と e2e p95 が，どちらも Iteration 5 の `fs_pn_p30` の 3 回の [最小 × 0.9, 最大 × 1.1]（処理量 11.281〜14.064 問/秒，e2e p95 3.053〜3.989 秒）に入れば，比べる相手は Iteration 5 の per_node の 3 回の値とする．
1 つでも外れたら「環境が変わった」と記録し，下の採用の基準の倍率 1.5 を，1.5 × max(1, Iteration 5 の fs_pn_p30 の処理量の中央値 12.721 ÷ 前後 2 回の処理量の小さい方) に引き上げる（環境が遅くなった分を差の側に有利に数えない）．範囲の規則（重ならないこと）はそのまま使う．

**判定の規則**（比べる 2 条件の回ごとの値の範囲が重ならないときだけ「差がある」．比べる相手は Iteration 5 の per_node の parallel 30 の 3 回）
- **H-S1**：multi_centroid・K = 75・K = 30 の全 36 実行で route_s の p50 が 0.010 秒未満．
- **H-S2**：multi_centroid の p30 の 3 回の処理量の範囲が 8.519〜8.566 より上にあり，重ならない．
- **H-S3**：K = 75 の p30 の 3 回の処理量の範囲が 5.34〜5.39 より上にあり，重ならない．
- **H-S4**：multi_centroid の p30 の e2e p95 の範囲が 4.865〜5.234 秒より下，かつ K = 75 の p30 の e2e p95 の範囲が 8.49〜8.52 秒より下にあり，どちらも重ならない．
- **このイテレーションの結論**：
  - **採用**（1 段目の採点の費用は重心の変換であり，以後の負荷の実験はこの実装で行う）：H-S1〜H-S4 が全て成り立ち，かつ parallel 30 の処理量の 3 回の中央値が，multi_centroid で 8.551 の 1.5 倍以上（12.83 問/秒以上），K = 75 で 5.372 の 1.5 倍以上（8.06 問/秒以上）．
    1.5 倍は Iteration 4・5 と同じ基準である（Iteration 5 の per_node の p30 の 3 回の幅は multi_centroid で中央値の −0.4%〜+0.2%，K = 75 で −0.6%〜+0.3%，flood_score で −1.5%〜+0.5% であり，1.5 倍はこの揺れの 30 倍以上）．
  - **部分的に支持**：H-S1 が成り立ち，H-S2 か H-S3 の少なくとも一方が成り立つが，採用の条件を満たさない．
  - **棄却**：H-S2 と H-S3 がどちらも成り立たない，または H-S1 が成り立たない（重心の変換が route_s の主因ではなかった）．
- **H-S5**（記録）：parallel 30 で，K = 30・K = 75・multi_centroid の各 3 回の処理量と e2e p95 を，Iteration 5 の flood_score × per_node の 3 回（12.534〜12.785 問/秒，3.392〜3.626 秒）と「範囲が重ならない」の規則で比べ，上・下・重なるのどれかを記録する．parallel 1〜30 の曲線を 4 方式で並べた表で残す．
- parallel 1・4・16 の multi_centroid と全 parallel の K = 30 は Iteration 5 の per_node の値が無いので，判定に使わず曲線として記録する（K = 30 は Iteration 4 の shared の値を参考に並べる）．

**ノイズの見積もり**：上の採用の基準の項のとおり，Iteration 5 の per_node の p30 の 3 回の幅は中央値の ±1.5% 以内（e2e p95 は multi_centroid で 4.865〜5.234 秒の ±4% 程度）．parallel 1 の幅は大きい（flood_score の p1 は 13.08〜17.35 問/秒）ので，parallel 1 は判定に使わない．

**期待効果**（推測．判定に使わない）：route_s の p50 は約 0.001〜0.003 秒（制御点の CPU と実データで操作端末と少し違う）．parallel 30 の処理量は，Iteration 5 の式（R ≈ 400 要求/秒）を当てはめると K = 75 は約 31 要求で 12〜13 問/秒（flood_score と同じ程度），K = 30 は約 22 要求で 17〜19 問/秒（flood_score の約 1.4 倍），
multi_centroid は 2 要求で式の上では約 200 問/秒だが，1 問あたりの要求に依らない質問者の費用（結果の書き出し，本照会の応答の JSON の解釈など）と，2 台に集まる本照会のノードの側の時間で，数十問/秒で頭打ちになる見込み．
したがって H-S5 は「multi_centroid ＞ K = 30 ＞ K = 75 ≈ flood_score」になる見込みである．`requester_cpu_util` は全実行で 1.0 に張り付いたままの見込み．

**限界**：有線 LAN・300 問・検索だけ．質問者は 1 プロセスのまま．比べる相手は Iteration 5（`cd42b41` のイメージ）で，同時性は前後 2 回の揺れの確認でしか担保しない（新旧を同じイメージで流す形にはしない．B22）．
`CardSimRouter` の `card_embedding` の変換は残る（このレバーの外）．K = 30 と parallel 1〜16 の multi_centroid は per_node の旧い値が無く，「変化」ではなく「水準」だけを記録する．CPU 使用率はプロセス全体の値で，内訳は分けない．

**次の候補**：採用か部分的に支持なら，per_node と前計算の下で「デバイスを単位に上位を選ぶ方式」（Iteration 7．Iteration 4 からの候補）に進む．全方式で `requester_cpu_util` が 1.0 に張り付き，処理量が R ≈ 400 要求/秒の式で決まるなら，複数の質問者のプロセスの構成をその次の候補にする．
棄却（H-S1 が成り立たない）なら，route_s の残りの内訳（py-spy のプロファイル）を取ってから次を決める．

### 実装・実験（2026-10-15．research-cycle の phase 2）

実装は `d510a21` のまま（コード変更なし）．事前登録どおり 38 実行を完了した（失敗率は全て 0．`cp75_pn_p4_r2` の deploy が 1 回失敗し，5 分後の再試行で成功．飛ばした実行なし）．実験中の commit なし．
所在: `results/20261015_iter6_*/`，ログ `results/20261015_iter6_load_summary/iter6_chain.log`，集計 `iter6_summary.py`・`iter6_summary.out`．

**正しさの確認**：全 36 実行で `selected_sources` が基準と 100% 一致（不一致 0）．`http_pool` は全実行で `per_node`．環境の揺れ：前 12.363 問/秒・p95 3.697 秒，後 12.952 問/秒・p95 3.609 秒．ともに範囲内（採用の倍率は 1.5 のまま）．

**事前登録の規則の機械的な出力**（解釈はフェーズ 3）
- H-S1 True：route_s p50 の最大 0.00157 秒（Iteration 5 は 0.111〜0.113 秒）．
- H-S2 True：multi_centroid の p30 処理量 116.97・169.51・174.31 問/秒（中央値 169.508．Iteration 5 は 8.519〜8.566）．
- H-S3 True：K = 75 の p30 処理量 13.099・13.413・13.444 問/秒（中央値 13.413．Iteration 5 は 5.340〜5.394）．
- H-S4 True：p30 の e2e p95 は multi_centroid 0.229〜0.735 秒，K = 75 3.436〜3.558 秒．
- 採用の倍率：中央値が閾値（multi_centroid 12.83，K = 75 8.06）以上．規則の結論は採用．
- H-S5（記録）：p30 の処理量は K = 30 が 18.709〜19.698（中央値 19.681．p95 2.311〜2.741 秒）．3 方式とも Iteration 5 の flood_score（12.534〜12.785）より上で範囲は重ならない．e2e p95 は K = 75 だけ flood_score（3.392〜3.626）と重なる．
- R（要求/秒，p30）：K = 75 は 415.2〜426.4，K = 30 は 421.2〜444.2，multi_centroid は 279.8・442.6・458.7．`requester_cpu_util` ≥ 0.9 は 28/36 実行．hit@2（300 問）：multi_centroid 0.4833，K = 75 0.87，K = 30 0.78．

**未達・注意**：事前登録の基準で未達なし．multi_centroid の 2 実行は処理量が低い（`mc_pn_p4_r2` 59.875 問/秒・cpu_util 0.369，`mc_pn_p30_r1` 116.966 問/秒・cpu_util 0.674．他は 169〜181）．原因は未調査．
失敗の無い問での再試行：`cp75_pn_p16_r1` 2，`cp75_pn_p16_r2` 5，`cp75_pn_p30_r2` 2，`cp30_pn_p30_r3` 1．

### Iteration 6 実行済み（2026-10-15．research-cycle の phase 3．判定：採用）

**変更**：1 段目の採点の重心の持ち方を，問ごとに `Advert.centroids` のリストから変換する形から，`SourceProfile.advert_centroids` に読み込み時に 1 回だけ前計算した fp32 の行列を使う形へ置き換えた（`d510a21`．38 実行は全て同じ HEAD）．`http_pool: per_node` を全実行で明示した．
事前登録の規則・基準は結果を見て変えていない．

**正しさの確認と環境の揺れ**：選択の一致（全 36 実行で基準と 100%）・`http_pool` の一致・単体テスト 4 件は通過．前後の flood_score × per_node の p30（12.363 問/秒・p95 3.697 秒，12.952 問/秒・3.609 秒）は Iteration 5 の [最小 × 0.9, 最大 × 1.1] の内 → 比べる相手は Iteration 5 の per_node の 3 回，採用の倍率は 1.5 のまま．

**結果と判定**（parallel 30．3 回の範囲．規則は「範囲が重ならないときだけ差」）
- **H-S1（機構）：成立**．全 36 実行で route_s の p50 は 0.00133〜0.00157 秒（Iteration 5 は 0.111〜0.113 秒．約 1/75）．
- **H-S2：成立**．multi_centroid の処理量 116.97〜174.31 問/秒は Iteration 5 の 8.519〜8.566 より上で重ならない．中央値の比 169.51 / 8.551 = **19.8 倍**（最も低い r1 でも 13.7 倍）．
- **H-S3：成立**．K = 75 の処理量 13.10〜13.44 問/秒は 5.34〜5.39 より上で重ならない．中央値の比 13.413 / 5.372 = **2.50 倍**．
- **H-S4：成立**．e2e p95 は multi_centroid 0.229〜0.735 秒（Iteration 5 は 4.865〜5.234 秒），K = 75 は 3.436〜3.558 秒（8.49〜8.52 秒）．
- **このイテレーションの結論：採用**（中央値は閾値 multi_centroid 12.83・K = 75 8.06 を大きく超える）．1 段目の採点の費用は重心の変換であり，以後の負荷の実験はこの実装で行う．
- **ノイズか信号か**：Iteration 5 の p30 の 3 回の幅は中央値の ±1.5% 以内，今回は K = 75 で −2.3%〜+0.2%，K = 30 で −4.9%〜+0.1% である．K = 75 の 2.5 倍・multi_centroid の 19.8 倍は，この揺れより 2 桁大きい（信号）．multi_centroid の幅（−31%〜+2.8%）は下の 2 実行の停止によるもので，判定には効かない（最も低い回でも閾値の 9 倍）．
- **H-S5（記録）**：parallel 30 の処理量の中央値は multi_centroid 169.5 ＞ K = 30 19.68 ＞ K = 75 13.41 ＞ flood_score（Iteration 5）12.72 の順．規則の出力は 3 方式とも flood_score より「上」で，e2e p95 は multi_centroid と K = 30 が「下」，K = 75 が「重なる」．
  ただし K = 75 と flood_score の差は，同じ日の前後の flood_score（12.36・12.95）と比べると 1.04〜1.08 倍で，前後の 2 回の間の揺れ（4.8%）と同じ大きさである．**K = 75 は処理量で flood_score とほぼ同じ**と読む（事前の推測「K = 75 ≈ flood_score」と一致）．K = 30 は flood_score の 1.55 倍・e2e p95 2.31〜2.74 秒（3.39〜3.63 秒より下）．

**分析**

事実（記録から計算したもの）
1. **R4 (b) の答え：1 問ずつで multi_centroid が flood_score より遅かったのは，重心の変換が全てだった**．parallel 1 の multi_centroid は e2e p50 0.0080〜0.0083 秒・処理量 113.6〜120.6 問/秒で，flood_score × per_node（Iteration 5 の p1．e2e p50 0.056〜0.057 秒，13.1〜17.3 問/秒）より速い．
   T4 の multi_centroid の p1（e2e p50 0.120 秒）のうち 0.111 秒が採点で，変換を除いた採点は 0.0013 秒になった（操作端末の見積もり 0.001 秒と同じ桁．制御点・実データの route_s で確かめた）．
2. **「1 問の時間 = route_s ＋ 要求の数 ÷ R」の式は，前計算の後も 4 方式に当てはまる**．parallel 30 の R は K = 75 で 415〜426，K = 30 で 421〜444，multi_centroid（停止の無い r2・r3）で 443〜459，flood_score の前後の確認で 396・414 要求/秒（Iteration 5 は 401〜409）．4 方式が 396〜459（±8%）に揃う．
   multi_centroid の 1 問 5.8 ms のうち採点は 1.4 ms，残り 4.4 ms が 2 要求（1 要求約 2.2 ms）で，1 要求あたりの費用は全員に聞く方式（1 / 420 ≈ 2.4 ms）と同じである．事前の推測「multi_centroid は 1 問あたりの固定の費用で数十問/秒で頭打ち」は外れた（要求の数に依らない大きな固定の費用は無い）．
   parallel 1・4 の R は約 520〜570 要求/秒で，同時の要求が増えると 1 要求あたりの費用が少し増える形は Iteration 5 と同じである．
3. **質問者の CPU は今も 1 コアに張り付いている**．parallel 4 以上で停止の無い実行の `requester_cpu_util` は 0.92〜1.01（大半は 1.00）である．36 実行のうち 0.9 未満は 8 実行で，parallel 1 の multi_centroid の 3 回（0.63〜0.65．1 問 8 ms のうち応答の待ちの割合が大きい）と，下の事実 5 の停止のある 5 回（`cp75_pn_p1_r3` の 0.88 を含む）である．
   したがって今回の処理量も「質問者 1 プロセスが 1 秒に約 420 の要求を扱える」ことの表れであり，方式の差は 1 問あたりの要求の数の差（2・21.9・31.1・32）でほぼ決まる．専門家の側の限界はまだ測れていない．
4. **再試行は遅さの症状という Iteration 5 の読みと合う**．multi_centroid の p30 の再試行は Iteration 5 の 15〜19 回から 0 回になった（イベントループが 0.11 秒ずつ止まらなくなった）．K = 75・30 の p16・30 の再試行（0〜5 回）は Iteration 5 の K = 75（7〜9 回）より少ない．
5. **低い 2 実行は，質問者の外での約 1 秒の一斉の待ちによる**．`mc_pn_p4_r2` は経過 5.01 秒（他の 2 回は約 1.68 秒）で，e2e が 0.5 秒を超えた問が 9 問（最大 1.04 秒．他の回は最大 0.08 秒），`mc_pn_p30_r1` は経過 2.56 秒（他は 1.72〜1.77 秒）で 33 問（最大 1.39 秒．他は 0.31 秒）．
   遅い問の本照会の宛先は 14 台・23 台に散らばり，1 台のノードに偏らない．その間の `requester_cpu_util` は 0.37・0.67 と低く，質問者は CPU を使わずに待っている．同じ形（0.5〜1.4 秒の待ち，cpu_util 0.68〜0.88）は `cp30_pn_p1_r2`・`cp30_pn_p4_r3`・`cp75_pn_p1_r3` と Iteration 5 の `fs_pn_p1_r3` にもある．
   multi_centroid の 300 問は 1.7 秒で終わるので，1 回の約 1 秒の待ちで処理量が 30〜65% 下がる．16〜25 秒かかる K = 30・75 では同じ待ちが数 % にしかならない．事前登録どおり除外せずに中央値で判定した（結論は変わらない）．

推測（上の事実と矛盾しないが，未確認のもの）
- 一斉の約 1 秒の待ちの原因は，TCP の接続の確立の再送（Linux の SYN の初期の再送の時間は 1 秒）か，ノードの側の一時的な停止（docker のネットワーク，GC）のどちらかと考えられるが，ノードの側の記録を突き合わせていないので確かめていない．待ちの長さが 0.7〜1.4 秒に集まることは前者と合う．
- 1 要求あたり約 2.4 ms の質問者の費用の内訳（JSON の組み立て・解釈，池ごとの管理，イベントループ）は分けていない（プロファイルなし）．

**期待との一致・不一致**：route_s（推測 0.001〜0.003 秒 → 0.0013〜0.0016 秒），K = 75 の処理量（推測 12〜13 → 13.4），K = 30（推測 17〜19 → 19.7），H-S5 の順序，cpu_util の張り付きは当たった．multi_centroid の頭打ち（推測 数十問/秒）は外れ，170 問/秒に達した．

**想定外の挙動**：上の事実 5 の一斉の待ち（言語崩れ・発散・OOM は該当なし．失敗は全実行で 0）．`cp75_pn_p4_r2` の deploy の 1 回の失敗は再試行で解消した．

**学び**
- 「1 問ずつ流して遅い」方式は，まず段ごとの時間（route_s など）で質問者の側の同期の計算を疑う．今回は Python のリストから numpy への変換（150 × 32 × 768 要素）だけで 1 問 0.11 秒を使い，方式の比較（T4 の「multi_centroid は 1 問ずつで flood_score より遅い」）を逆転させていた．公開情報のように起動時に決まる値は読み込み時に行列にしておく．
- 質問者 1 プロセスが律速の間は，方式の処理量は「1 問あたりの要求の数」でほぼ決まる（R ≈ 420 要求/秒，±8%）．二段の方式の利点を処理量で出すには，受信箱の数ではなく**要求の数（＝ probe を受けるデバイスの数）**を減らす必要がある．K = 75 は受信箱を半分にしても 29.1 台に probe が届き，flood_score と同じ処理量にしかならない．
- 300 問が 2 秒未満で終わる条件では，約 1 秒の一斉の待ち 1 回で処理量が大きく揺れる．処理量の高い方式の比較では，問の数を増やすか，経過時間の短い実行の揺れを前提に中央値と最小値の両方を見る．

**限界**
- 質問者は 1 プロセス・1 コアのままで，比べたのは「質問者が扱える要求の数」である．専門家の側の限界は測っていない．有線 LAN・300 問・検索だけ・単一の質問者．
- route_s は制御点・実データで確かめた（操作端末の見積もりだけではない）．ただし採点の時間の内訳（行列積と上位の選択）は分けていない．
- 比べる相手は Iteration 5（`cd42b41`）で，同時性は前後 2 回の揺れの確認でしか担保していない（B22）．K = 30 と parallel 1〜16 の multi_centroid は旧い per_node の値が無く，水準だけの記録である．
- 一斉の約 1 秒の待ちの原因は未確認．hit@2 は 300 問の値（multi_centroid 0.483，K = 30 0.78，K = 75 0.87，flood_score 0.92）で，test 2,940 問の値ではない．

**次の一手**（backlog B23 [auto-decided]）：事前登録の「次の候補」どおり，Iteration 7 は**デバイスを単位に上位を選ぶ二段の方式**（multi_centroid の採点をデバイスごとにまとめ，上位 D 台にだけ probe を送る）を，まずオフラインで hit@2 と probe を受けるデバイスの数を K = 30・75 と比べ，見込みのある D を実機の per_node で測る．
上の学びのとおり，質問者が律速の間は要求の数が処理量を決めるので，probe のデバイスの数を直接減らす方式がこの式の上で最も効く．複数の質問者のプロセスの構成はその次の候補とする．

## Iteration 5: 質問者の HTTP 接続の持ち方を変えた負荷の律速の切り分け

### 調査・計画（2026-10-15．research-cycle の phase 1）

**レバーの由来**：backlog B19 [auto-decided]（Iteration 4 は棄却．parallel 30 の待ちはノードの計算ではなく質問者の側の HTTP の扱いにあると読めた）．config.yml の levers 末尾の `requester.http_pool: [per_node]`（仮称）を，下の B20 のとおり `experiment.http_pool: [per_node, shared_unlimited]` と確定した．
docker の権限は B18 の [human-decided]（A3．恒久対応済み）で解決している．データ準備・ラベル・シャード・公開情報は作り直さない．

**コードと既存の結果から確かめたこと**（事実）
- 質問者は全ノードと自分の Ollama で 1 つの `httpx.AsyncClient(limits=httpx.Limits(max_connections=MAX_CONNECTIONS))`（`MAX_CONNECTIONS` = 512．`src/atrium/experiment.py` の `run_experiment`）を共有する．
  この指定の `httpx.Limits` は `max_keepalive_connections=None, keepalive_expiry=5.0` になる（操作端末の httpx 0.28.1 で `print(httpx.Limits(max_connections=512))` を確かめた）．したがって「待機中の接続を 20 本までしか持たない」ことによる作り直しは起きておらず，Iteration 4 の接続の作り直しは池の上限 512 に当たったときのもの（上限で待つ要求に，別のノードの待機中の接続を閉じて開き直す）と読める．
- `uv.lock` の版は httpx 0.28.1・httpcore 1.0.9・uvicorn 0.54.0 である．httpcore 1.0.9 の `AsyncConnectionPool._assign_requests_to_connections`（操作端末の `.venv` の `httpcore/_async/connection_pool.py` 270 行目〜）は，要求の追加と終了のたびに呼ばれ，
  (1) 全接続の走査の中で待機中の接続ごとに全接続の内包表記を作り直す（接続の数 N に対して O(N²)），(2) 待ち行列の要求ごとに全接続を 2 回走査する（待ちの要求の数 M に対して O(N × M)）．
- 専門家の `/v1/probe`・`/v1/retrieve` は同期の `def`（`src/atrium/node.py`．uvicorn の既定の設定で `uvicorn.run(app, host, port)`）で，uvicorn の待機中の接続の打ち切りは既定の 5 秒である．質問者の `keepalive_expiry` も 5.0 秒で同じ長さである．
- Iteration 4 の parallel 30 で，同時に処理中の要求は flood_score で約 900（30 問 × 30 台）であり，池の上限 512 を超える．parallel 1 では約 30 で，上限にも O(N × M) の費用にもほとんど当たらない．parallel 1 の flood_score は約 410 要求/秒，parallel 30 では約 80 要求/秒を扱った（journal の「Iteration 4 実行済み」の分析 3・4）．

**調査で分かったこと**（tavily-search・tavily-extract．事実と推測を分けて書く）
- httpcore の `_assign_requests_to_connections` は，接続の数 N・要求の数 M に対して O(N² + N × M) であり，要求の開始と終了の 2 回ずつ呼ばれる hot path である．プロファイルではこの関数が全標本の 59% を占め，O(N + M) に書き直すと 4% に下がり，アプリケーションの CPU 使用率が半分になったと報告されている
  （V. Prins, "Fix 2 bugs and improve performance from O(N²+NxM) to O(N+M) in _assign_requests_to_connections", encode/httpcore PR #1035, 2025-09-14．https://github.com/encode/httpcore/pull/1035 ．2026-10-15 の時点で未マージ）．
  同 PR は `max_keepalive_connections` の判定の誤り（`len([... is_idle() ...])` が全接続の数を返す）も指摘している．本研究の設定では `max_keepalive_connections` が None なのでこの誤りの影響は受けないが，O(N²) の内包表記は残る．
- NVIDIA の NeMo Gym の技術メモ（"aiohttp vs httpx", NeMo Gym documentation, 2025．https://docs.nvidia.com/nemo/gym/infrastructure/engineering-notes/aiohttp-vs-httpx ）は，同時の要求が多いと httpx（httpcore の池）が待ち行列の要求の数の 2 乗の時間を使い，16,000 の同時要求で最初の要求の処理まで 40 分止まったこと，並列を 32 程度に抑えざるを得なかったことを報告し，aiohttp に替えて解決している．
  「並列が 32 を超えると影響しうる」という同メモの目安は，本研究で parallel 4（同時の要求 約 120）から処理量が下がり始めたことと矛盾しない（推測．本研究の値は測っていない）．
- 同じ問題の利用者側の報告として，`httpx.AsyncClient` が同時の要求で aiohttp より大幅に遅いという issue（pydantic/httpx2 #827．https://github.com/pydantic/httpx2/issues/827 ）がある．httpx は `mounts` で接続先ごとに別の `AsyncHTTPTransport`（別の接続の池）を持てる（例：Bhagya Rana, "5 uvloop + httpx Clients That Actually Hit High QPS", Medium．httpx の公式の機能）．
- uvicorn の待機中の接続の打ち切りは既定で 5 秒であり（Uvicorn documentation, "Server Behavior"．https://uvicorn.dev/server-behavior ），クライアント側の待機の長さが同じか長いと，サーバーが閉じた直後の接続を再利用して接続のエラーになる競合が知られている（Kludex/uvicorn Discussion #2939．https://github.com/Kludex/uvicorn/discussions/2939 ）．
  `post_with_retry` の docstring が想定している「ノードが待機中の接続を閉じた直後の再利用」はこれに当たる．ただし Iteration 4 では再試行が parallel 16 以上で急に増えており（parallel 1〜8 で 0〜4 回），待機の 5 秒の競合だけでは増え方を説明しにくい（推測．池の上限での接続の作り直しとの関係は未確認）．

**問い**：Iteration 4 の parallel 30 の律速は，(i) 質問者の接続の池の管理の CPU（httpcore の O(N² + N × M)．全ノードの要求が 1 つの池に集まる），(ii) 池の上限 512 による接続の作り直し，(iii) それ以外（イベントループ上の JSON の組み立て・解釈，ノードの側の受け付け）のどれか．

**B19 の要レビューへの対応**
- (a) **律速の切り分けを，デバイスを単位に選ぶ方式より先にする**．理由：上の調査で，同時の要求の数とともに質問者の 1 要求あたりの費用が増える既知の仕組み（httpcore の池の 2 乗の費用）が見つかり，Iteration 4 の事実 4（parallel 1 → 4 で既に 1 要求あたりの費用が増える）をそのまま説明できる候補である．これを確かめないまま方式を比べると，どの方式も「1 つの池に入る要求の数」の比較になる．
- (b) **基準の方式も同じイテレーションで測り直す**．flood_score は per_node で parallel 1・4・8・16・30 の全曲線を 3 回ずつ測り（T4 と同じ点），multi_centroid は per_node の parallel 30 だけを対照として 3 回測る（multi_centroid は 1 問 2 要求で，律速は 1 段目の採点 0.111 秒なので，per_node で変わらないことを確かめる）．
  K = 75 の centroid_probe も per_node で測り，Iteration 4 の「K = 75 は flood_score より遅い」が池の律速の下でだけ成り立つのかを記録する．環境の揺れは，今の構成（shared）の flood_score・multi_centroid の parallel 30 を前後に流して T4 と比べる．
- (c) 律速が質問者の接続の池と確かめられた場合（採用），以後の負荷の実験は `experiment.http_pool: per_node` を明示して行い，d0006 §6.3 の T4 の記述を「1 つの接続の池を共有した質問者の値」と限定し，per_node での値を並べる．`experiment.http_pool` の既定値は `shared`（今の挙動）のまま変えない（既存の実験の再現のため）．
  複数の質問者（プロセス・デバイス）から流す構成は，per_node でも parallel 30 で質問者のプロセスの CPU 使用率が 1 コアに張り付く場合にだけ，次の候補にする．
- (d) 棄却（per_node でも処理量が変わらない）なら，次はデバイスを単位に上位を選ぶ方式（Iteration 4 の事前登録の「次の候補」）に進む．質問者の CPU 使用率の記録から (iii) の手がかりも添える．

**仮説**
- **H-H1（処理量）**：flood_score の parallel 30 の処理量は，per_node で shared（T4）より高い．仕組み：ノードごとの池では N・M がそれぞれ高々 2 × parallel（60）程度（多くの時間は parallel 程度）になり，池の管理の 1 回の費用が約 (512 × 400) から約 (30 × 30) の規模に下がる（推測．400 は上限で待つ要求の数の概算）．池の上限にも当たらないので接続の作り直しも無くなる．
- **H-H2（待ち時間）**：flood_score の parallel 30 の e2e p95 は，per_node で shared（T4）より小さい．
- **H-H3（何が律速か．記録）**：shared_unlimited（1 つの池のまま上限を外す）は (ii) だけを外し，per_node は (i) と (ii) の両方を外す．parallel 30 の flood_score の処理量の中央値で，
  shared_unlimited が T4 の中央値の ±10% 以内かつ per_node が T4 の 1.5 倍以上なら「(i) 池の管理の費用」，shared_unlimited が per_node の 0.9 倍以上かつ T4 の 1.5 倍以上なら「(ii) 池の上限による作り直し」，per_node も T4 の ±10% 以内なら「(iii) 池以外」，それ以外は「混合」とする．
  質問者のプロセスの CPU 使用率（下の記録）が shared の parallel 30 で 0.9 コア以上なら「質問者のイベントループが CPU で飽和」を支持する手がかりとして添える．
- 参考（判定に使わない）：per_node でも K = 75 の centroid_probe は 1 段目の採点（0.111 秒）を直列に行うので，処理量は約 9 問/秒を超えない．per_node で flood_score が 9 問/秒に近づけば，parallel 30 で K = 75 が flood_score より遅いという Iteration 4 の順序は変わらない（推測）．

**単一レバー**：質問者の HTTP の接続の持ち方．新しい設定キー `experiment.http_pool`（`config.yaml` の `experiment` の直下）を，今の `shared`（既定．全ノードで 1 つの池，`max_connections` = 512）から **`per_node`**（判定の主）と **`shared_unlimited`**（切り分けの副）へ変える．他は全て固定する．
- **実装**（変更するファイル）
  - `src/atrium/config.py`：`ExperimentConfig` に `http_pool: Literal["shared", "shared_unlimited", "per_node"] = "shared"` を足す（既定は今の挙動．既存の config.yaml はそのまま読める）．`config.yaml` の `experiment` に既定値 `http_pool: shared` を説明のコメント付きで書く．`docs/d0002_configuration.md` に項目を足す．
  - `src/atrium/experiment.py`：`run_experiment` の `httpx.AsyncClient` の作り方だけを `http_pool` で分ける．
    - `shared`：今と同じ `httpx.Limits(max_connections=MAX_CONNECTIONS)`（挙動を変えない）．
    - `shared_unlimited`：1 つの池のまま `httpx.Limits(max_connections=None)`（上限なし．`keepalive_expiry` は既定の 5.0 のまま）．
    - `per_node`：1 つの `httpx.AsyncClient` に，`placement` の各ノードの URL の origin ごとに別の `httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=MAX_CONNECTIONS_PER_NODE))` を `mounts` で付ける（ノードの数だけ池ができる）．`MAX_CONNECTIONS_PER_NODE` = 64 とし，parallel ≤ 30 では 1 ノードへの同時の要求が 2 × parallel を超えない（各問はノードへ probe を 1 件送り，その後に本照会を高々 m = 2 件送る）ので上限に当たらない，と定数の近くにコメントで書く．
      質問者の Ollama など mounts に無い宛先は既定の池（`shared` と同じ上限）を使う．`Requester` の公開の挙動・要求の内容・宛先は変えない（client を 1 個渡す形のまま）．
  - **記録の追加**（3 つの持ち方の全てで同じく記録する．判定の解釈に使う）：`run_meta.json` に `http_pool` と，質問の処理の区間（`run_questions` の前後）の質問者のプロセスの CPU 時間（`resource.getrusage(RUSAGE_SELF)` の user・sys の差）と `requester_cpu_util`（CPU 時間 ÷ 経過時間．1.0 が 1 コアの飽和）を書く．
    再試行の数は Iteration 4 と同じく `requester.log` の `retrying` の行の数，新しい接続の数はノードの uvicorn の access log の送信元ポートの数で数え，集計のスクリプトで自動で出す（事後に手で数えない）．
  - `tests/`：`shared` は今と同じ上限 512 の 1 つの池であること，`per_node` はノードの origin ごとに別の transport へ振り分けられ，mounts に無い宛先は既定の池に行くこと，`shared_unlimited` は上限が None であること．httpx の `MockTransport` などで要求の宛先の振り分けを確かめる（標準の httpx の機能そのものは試さない）．
  - 連続実行のスクリプト（`results/<日付>_iter5_load_summary/iter5_chain.sh`，`iter5_specs.txt`）：`iter4_chain.sh` と同じ形．`setcfg` は `experiment` の直下のキー（`http_pool`・`routing`・`parallel`）と `routing.probe_k.enronqa` を扱える既存の形を使い，`python3` ではなく `uv run python` を使う．集計 `iter5_summary.py`（`iter4_summary.py` と `iter4_timing_breakdown.py` の指標に CPU 使用率・再試行の数・接続の数を足したもの）．

**固定する構成**（T4・Iteration 4 と同じ）：`dataset: enronqa`，`answer_mode: retrieval_only`，`question_limit: 2`（300 問．T4 と同じ集合），m = 2，`merge: score`，`query_embedding.enronqa: cached`，`seed` は既存の値，`keepalive_expiry` 5.0 秒（httpx の既定），`RETRIEVE_RETRIES` = 2，`RETRIEVE_TIMEOUT_S` = 120，ノードの uvicorn の設定（既定），1 段目の採点はイベントループの上で同期（executor へ移さない），centroid_probe の K = 75，各回の前に `mise run deploy`，失敗した問は流し直さない，連続実行の間は制御点で他のコンテナを動かさず commit もしない．専門家 30 台・質問者（制御点）の構成は T4 と同じ（運用ルール 8 のフルスペックの実機実験）．

### 事前登録（2026-10-15．実装の後，実行の前に push する．結果を見て変えない）

**実行の順序と回数**（計 43 実行．1 回約 5 分，全体で約 3.5 時間の見込み．run_id の接頭辞は `<日付>_iter5_`）
1. docker の権限の確認：`docker info > /dev/null 2>&1 && echo ok`．ok でなければ実行を 1 回も始めずに止め，backlog に `[needs-human]` を記録する（Iteration 4 と同じ）．
2. **環境の揺れの確認（前）**：`http_pool: shared` の flood_score の parallel 30 と multi_centroid の parallel 30 を各 1 回（`anchor_shared_<routing>_p30_pre`）．
3. **flood_score × per_node**：parallel 1・4・8・16・30 × 3 回（`fs_pn_p<parallel>_r<1|2|3>`．15 実行）．
4. **flood_score × shared_unlimited**：parallel 4・16・30 × 3 回（`fs_su_p<parallel>_r<n>`．9 実行．parallel 1 は同時の要求が約 30 で上限 512 に当たらず shared と同じなので測らない）．
5. **centroid_probe（K = 75）× per_node**：parallel 1・4・16・30 × 3 回（`cp75_pn_p<parallel>_r<n>`．12 実行）．
6. **multi_centroid × per_node（対照）**：parallel 30 × 3 回（`mc_pn_p30_r<n>`．3 実行）．
7. **環境の揺れの確認（後）**：2 と同じ 2 条件を各 1 回（`..._post`）．
- deploy の失敗の扱い（5 分おきに 3 回まで再試行，それでも失敗した回は飛ばして後で同じ run_id で流す．失敗率に数えない）と，失敗率の打ち切り（同じ条件の 3 回とも失敗率が 10% を超えたら，その系列のより大きい parallel は飛ばして記録する）は Iteration 4 と同じ．

**指標**（Iteration 4 と同じ定義に加えて）：処理量（成功した問の数 ÷ `run_meta.json` の finished_at − started_at），e2e_s の p50・p95，probe_s・retrieve_s の p50・p95，route_s の p50，`probe_node_s_max` の p50・p95，失敗率とエラーの種類，
`requester_cpu_util`，再試行の数（`retrying` の行の数），新しい接続の数（access log の送信元ポートの数），1 秒あたりに扱った HTTP 要求の数（処理量 × 1 問あたりの要求の数），300 問の hit@2（参考）．

**正しさの確認**（満たさなければ判定に進まず，原因を調べて記録する）
- 全ての実行で，失敗の無い問の `selected_sources` が，同じ方式の T4（flood_score・multi_centroid）または Iteration 4（K = 75）の parallel 1 の r1 の選択と 99% 以上で集合として一致する（接続の持ち方は選択を変えないはず．選択は決定的）．
- `run_meta.json` の `http_pool` が仕様（`iter5_specs.txt`）の値と一致する．
- 単体テストで，`shared` の挙動（上限 512 の 1 つの池）が変わっていないこと．
- per_node の parallel 30 で，新しい接続の数が 1 実行で 300 以下（各ノードへ高々 2 × parallel 本を開いて使い回す見込みで，30 台 × 60 本 = 1,800 が理論の上限．300 は「作り直しが起きていない」ことの目安）．超えた場合は判定に進むが，H-H3 の解釈で (ii) を外せたとはみなさない．

**環境の揺れの規則**（Iteration 4 と同じ）：前後 4 回の確認の処理量と e2e p95 が全て T4 の同じ条件の 3 回の [最小 × 0.9, 最大 × 1.1] に入れば，比べる相手は T4 の 3 回の値とする．1 つでも外れたら「環境が変わった」と記録し，比べる相手を前後の確認の値（各条件 2 回）に替える．

**判定の規則**（比べる 2 条件の回ごとの値の範囲が重ならないときだけ「差がある」）
- **H-H1**：parallel 30 で，flood_score × per_node の 3 回の処理量の範囲が，比べる相手（shared）の範囲より上にあり，重ならない．
- **H-H2**：parallel 30 で，flood_score × per_node の 3 回の e2e p95 の範囲が，比べる相手の範囲より下にあり，重ならない．
- **このイテレーションの結論**：
  - **採用**（質問者の接続の池が負荷の律速であり，以後の負荷の実験は `per_node` で行う）：H-H1 と H-H2 がどちらも成り立ち，かつ parallel 30 の処理量の 3 回の中央値が比べる相手の中央値の 1.5 倍以上（T4 なら 3.75 問/秒以上）．1.5 倍は Iteration 4 と同じ基準（3 回の揺れ ±5% の 10 倍）．
  - **部分的に支持**：H-H1 か H-H2 の少なくとも一方が成り立つが，採用の条件を満たさない．
  - **棄却**：H-H1 と H-H2 がどちらも成り立たない．
- **H-H3**（記録）：上の仮説の規則を parallel 30 の処理量の中央値に機械的に当てはめる．`requester_cpu_util` と再試行・接続の数を解釈に添える．
- **対照**：multi_centroid × per_node の parallel 30 の 3 回の処理量が，T4 の [最小 × 0.9, 最大 × 1.1] に入ること．外れた場合は，per_node が池以外の何か（例：振り分けの費用）を変えた疑いとして記録し，結論に「対照が外れた」と添える（結論の規則そのものは変えない）．
- K = 75 × per_node は判定に使わず，parallel 30 の処理量・e2e p95 を flood_score × per_node と並べて，Iteration 4 の順序（K = 75 が flood_score より遅い）が per_node でも成り立つかを同じ「範囲が重ならない」の規則で記録する．parallel 1〜30 の曲線（3 つの持ち方 × 方式）を T4・Iteration 4 と並べた表で残す．

**ノイズの見積もり**：T4・Iteration 4 の 3 回の幅は処理量で中央値の ±5% 以内，e2e p95 で ±5〜10%（Iteration 4 の K = 75 の parallel 30 の e2e p95 は 22.8〜27.5 秒で ±10% 程度）．1.5 倍の差はこの揺れと区別できる．

**期待効果**（推測．判定に使わない）：per_node の flood_score の parallel 30 の処理量は 5〜10 問/秒（T4 の 2〜4 倍），e2e p95 は 5〜10 秒，`requester_cpu_util` は shared で 0.9 以上，per_node で下がる．shared_unlimited は T4 と同じか少し遅い（池の接続の数 N が 512 を超えて増え，2 乗の費用が増えるため）．
したがって H-H3 は「(i) 池の管理の費用」になる見込み．ただし，parallel 30 の約 900 の同時の要求の JSON の組み立て・解釈（1 要求約 15 KB）も同じイベントループの上にあり，per_node でも 1 コアに張り付いて 1.5 倍に届かない可能性がある（その場合は部分的に支持か棄却になる）．

**限界**：有線 LAN・300 問・検索だけ．質問者は 1 プロセスのまま（複数の質問者は扱わない）．aiohttp など別の HTTP クライアントへの置き換えと httpcore の修正版（PR #1035．未マージ）は扱わない．`keepalive_expiry` と uvicorn の待機の打ち切りが同じ 5 秒である競合は変えない．
CPU 使用率はプロセス全体の値で，池の管理と JSON の処理などを分けない（py-spy のプロファイルは取らない）．接続の数は送信元ポートの数による下限である．比べる相手は T4（`ffe0880` のイメージ）で，揺れの確認でしか同時性を担保できない．

**次の候補**：採用なら，per_node の下で「デバイスを単位に上位を選ぶ方式」（Iteration 4 の次の候補）と，parallel 30 で `requester_cpu_util` が 1 コアに近ければ 1 段目の採点の executor への移動（multi_centroid・centroid_probe に効く）．部分的に支持で CPU が飽和していれば，質問者の JSON の処理（埋め込みを全ノードへ同じ形で 30 回組み立てている）か，複数の質問者のプロセスを候補にする．棄却なら，デバイスを単位に選ぶ方式に進む．

### 実装・実験（2026-10-15．research-cycle の phase 2．解釈は phase 3）

**実装 commit**：`cd42b41`（`experiment.http_pool`・`run_meta.json` の `http_pool` と CPU 時間・`tests/test_http_pool.py`・連続実行の一式）．pytest 242 件・ruff（変更したファイル）・mypy src は通過．
httpcore は上限なし（`max_connections=None`）を `sys.maxsize` として持つので，テストではその値で確かめた．連続実行 43 実行（`results/20261015_iter5_load_summary/iter5_chain.sh`・`iter5_specs.txt`．ログ `iter5_chain.log`）は 2026-10-15 01:42〜04:43 に全て完了した（docker 権限は ok．deploy の失敗・飛ばした回は 0）．
集計は `iter5_summary.py`（出力 `iter5_summary.out`）．以下は規則の機械的な出力で，解釈は含まない．

**正しさの確認**：全実行で失敗の無い問の `selected_sources` が基準（T4 の p1 r1，K = 75 は Iteration 4 の p1 r1）と 100%（比較 300 問）一致．`http_pool` は全実行で仕様と一致．単体テストで `shared` は上限 512 の 1 つの池のまま．
**新しい接続の数の基準（per_node の p30 で 300 以下）は満たさなかった**：flood_score × per_node の p30 は 859・548・711，centroid_probe × per_node の p30 は 797・727・793（下限．送信元ポートの異なり数）．事前登録どおり，判定には進むが H-H3 の (ii) を外せたとはみなさない．

**環境の揺れの規則**：前後 4 回が全て T4 の [最小 × 0.9, 最大 × 1.1] の内（flood_score 処理量 2.561・2.484，e2e p95 18.396・18.297，multi_centroid 処理量 8.393・8.366，e2e p95 4.789・4.997）．比べる相手は T4 の 3 回．

**flood_score，parallel 30**（3 回の最小〜最大．括弧は中央値）
| 持ち方 | 処理量（問/秒） | e2e p95（秒） | requester_cpu_util | 再試行（回） | 新しい接続（下限） | HTTP 要求/秒 |
|---|---|---|---|---|---|---|
| shared（T4） | 2.440〜2.585（2.498） | 18.88〜20.60（19.05） | 前後の確認 1.001・1.001 | 前後の確認 111（前） | 1406（前） | 81.9（前） |
| per_node | 12.534〜12.785（12.721） | 3.392〜3.626（3.615） | 1.001・1.001・1.001 | 1・1・2 | 859・548・711 | 401〜409 |
| shared_unlimited | 2.377〜2.586（2.433） | 18.06〜19.84（18.26） | 1.001・1.001・1.001 | 258・91・139 | 1615・1188・1827 | 76〜83 |

- 規則の出力：H-H1 True，H-H2 True，処理量の中央値の比（per_node / shared）5.092，**結論（規則の機械的な適用）：採用**．
- H-H3（規則の出力）：**(i) 池の管理の費用**（shared_unlimited / shared = 0.974，shared_unlimited / per_node = 0.191）．CPU の手がかり（shared の p30 で requester_cpu_util が全て 0.9 以上）：True．ただし requester_cpu_util は shared・shared_unlimited・per_node・multi_centroid の全実行（parallel 4 以上）で 1.0 に張り付いている（parallel 1 の flood_score per_node は 0.68〜0.90）．
- 対照（multi_centroid × per_node，p30）：処理量 8.519〜8.566（T4 の [最小 × 0.9, 最大 × 1.1] = 7.52〜9.23 の内）．1 実行（r1）に ReadError が 1 問（失敗率 0.0033）．

**flood_score の曲線**（処理量の中央値，問/秒．e2e p95 秒は括弧）
| parallel | shared（T4） | per_node | shared_unlimited |
|---|---|---|---|
| 1 | 12.83（0.085） | 16.90（0.060） | 測らず |
| 4 | 6.48（0.802） | 17.83（0.272） | 6.48（0.784） |
| 8 | 4.72（2.50） | 16.72（0.595） | 測らず |
| 16 | 2.95（8.10） | 14.09（1.587） | 2.93（7.97） |
| 30 | 2.50（19.05） | 12.72（3.615） | 2.43（18.26） |

per_node の flood_score の p1 は 13.08・16.90・17.35 問/秒（3 回の幅が大きい）．

**K = 75 の centroid_probe × per_node**（判定に使わない）：処理量（問/秒）p1 5.81（4.91〜5.82）・p4 5.69・p16 5.52・p30 5.37（5.34〜5.39），e2e p95 は p30 で 8.49〜8.52 秒．parallel 30 の flood_score × per_node（処理量 12.534〜12.785，e2e p95 3.392〜3.626）に対し，処理量の範囲は下，e2e p95 の範囲は上で重ならない．再試行は p30 で 9・7・7，新しい接続は 797・727・793．
multi_centroid × per_node の p30 の e2e p95 は 4.865〜5.234 秒，再試行 15〜19，新しい接続 205〜214．

### Iteration 5 実行済み（2026-10-15．research-cycle の phase 3．判定：採用）

**変更**：質問者の HTTP の接続の持ち方 `experiment.http_pool` を `shared`（全ノードで 1 つの池，上限 512．T4・Iteration 4 の構成）から `per_node`（ノードの origin ごとに別の池，上限 64．判定の主）と `shared_unlimited`（1 つの池のまま上限なし．切り分けの副）に変えた．実装 `cd42b41`，43 実行は全て同じ HEAD．
事前登録の規則・基準は結果を見て変えていない．

**正しさの確認と環境の揺れ**
- 選択の一致（全実行 1.0000）・`http_pool` の一致・単体テストの 3 項目は通過．前後 4 回の揺れの確認は全て T4 の [最小 × 0.9, 最大 × 1.1] の内 → 比べる相手は T4 の 3 回．
- **未達：per_node の parallel 30 の新しい接続の数（300 以下）**．flood_score 548〜859，K = 75 は 727〜797．事前登録どおり判定には進み，「per_node が (ii)（池の上限による作り直し）を外した」とはみなさない．

**結果と判定**（parallel 30．3 回の範囲．規則は「範囲が重ならないときだけ差」）
- **H-H1（処理量）：成立**．per_node 12.534〜12.785 問/秒は shared（T4）2.440〜2.585 より上で重ならない．中央値の比 12.721 / 2.498 = **5.09 倍**（採用の基準 1.5 倍以上）．3 回の幅は per_node で中央値の −1.5%〜+0.5%，T4 で −2.3%〜+3.5% であり，差はこの揺れの 100 倍以上である（信号）．
- **H-H2（待ち時間）：成立**．e2e p95 は per_node 3.392〜3.626 秒，T4 18.88〜20.60 秒で重ならない（中央値 19.05 → 3.615 秒，0.19 倍）．
- **このイテレーションの結論：採用**（質問者の接続の池が負荷の律速であり，以後の負荷の実験は `per_node` を明示して行う）．
- **H-H3（記録）：規則の出力は「(i) 池の管理の費用」**．shared_unlimited の処理量の中央値 2.433 は T4 の 0.974 倍（±10% 以内），per_node は 5.09 倍．上限を外すだけでは何も変わらず，池を分けると 5 倍になった．
  この結論は新しい接続の数の未達に左右されない（(ii) を単独で外した shared_unlimited が効かなかったことが根拠であり，per_node が (ii) を外せたかどうかには依らない）．
- **対照**：multi_centroid × per_node の p30 の処理量 8.519〜8.566 は T4 の [7.52, 9.23] の内（per_node は池以外を変えていない）．r1 に `ReadError` が 1 問（失敗率 0.0033）．
- **K = 75 × per_node（判定に使わない）**：p30 の処理量 5.34〜5.39 問/秒は flood_score × per_node（12.53〜12.78）より下，e2e p95 8.49〜8.52 秒は 3.39〜3.63 秒より上で，どちらも重ならない．**Iteration 4 の順序（K = 75 が flood_score より遅い）は per_node でも成り立ち，差は 0.79 倍から 0.42 倍に広がった**．

**分析**

事実（記録から計算したもの）
1. **parallel 1 でも per_node の方が速い**．flood_score の p1 は per_node 13.08〜17.35 問/秒，T4 11.91〜12.88 で範囲が重ならない（r3 の 13.08 は他の 2 回より 23% 低く，`requester_cpu_util` も 0.68 と低い．理由は未確認）．同時の要求が約 32 の段階で既に 1 つの池の管理の費用が見えている．
2. **per_node でも質問者の CPU は 1 コアに張り付いている**．flood_score × per_node の処理量は p4 の 17.83 問/秒（570 要求/秒）が最大で，p30 では 12.72 問/秒（405 要求/秒）に下がる．`requester_cpu_util` は parallel 4 以上の全実行（3 つの持ち方・全方式）で 1.00 である．したがって per_node は律速を外したのではなく，**同じ 1 コアの上で 1 要求あたりの費用を約 1/5 にした**（p30 で 80 → 405 要求/秒）．同時の要求が増えると 1 要求あたりの費用が増える形（570 → 405 要求/秒）は弱く残る．
3. **3 方式の parallel 30 の処理量は「1 問の時間 = route_s ＋ 要求の数 ÷ R」でほぼ説明できる**（R は 1 コアの質問者が扱える要求/秒）．flood_score：32 要求，route_s 0 → R = 401〜409．K = 75：1 / 5.372 = 0.186 秒のうち route_s 0.113 秒，残り 0.073 秒で 31.06 要求 → R ≈ 424．multi_centroid：1 / 8.551 = 0.117 秒のうち route_s 0.111 秒，残り 0.006 秒で 2 要求 → R ≈ 360（残りが小さく誤差が大きい）．
   R が 3 方式で ±10% の範囲に揃う．3 点の算術の整合であり，モデルの検定ではない．
4. **接続の作り直しと再試行は，池の上限ではなく遅さに付いている**．shared_unlimited（上限なし）でも p30 の再試行は 91〜258 回，新しい接続は 1,188〜1,827 で，shared（T4・前後の確認．再試行 111・177 回，接続 1,406・1,592）と同じ程度だった．per_node の p30 は再試行 1〜2 回である．
5. **per_node の新しい接続の数の基準（300）は，計画の見積もりが小さすぎた**．flood_score は各問が全 30 台へ probe を送るので，parallel 30 では 1 ノードあたり同時に最大 30 本前後の接続が要り，30 台 × 30 本 ≈ 900 が「作り直しの無い」場合の目安になる．観測の 548〜859 はこの範囲の内である（計画の「300」は 1 ノードあたり 10 本を想定した値で，根拠の計算を誤っていた）．
6. **1 段目の採点（route_s 約 0.111 秒）の主因は，重心のリストから numpy への変換と読める**．`MultiCentroidRouter.score`（`src/atrium/routing/advert.py`）は問ごと・受信箱ごとに `np.asarray(advert.centroids, np.float32)` を呼ぶ．操作端末で 150 受信箱 × 32 × 768 の乱数のリストで測ると，変換込みで 0.105 秒，変換済みの行列なら 0.0012 秒だった（実機の制御点・実データでは測っていない．768 は EnronQA の埋め込み `snowflake-arctic-embed-m-v1.5` の次元）．

推測（上の事実と矛盾しないが，未確認のもの）
- shared の 1 つの池の費用は，httpcore 1.0.9 の `_assign_requests_to_connections` の O(N² + N × M) で説明できる（事前の調査の候補どおり）．per_node は N・M を 1/30 程度に分けたので費用が下がった．プロファイル（py-spy）は取っていないので，関数の名前までは確かめていない．
- 再試行と接続の作り直しは，イベントループが遅いと 1 本の接続の要求と要求の間が uvicorn の待機の打ち切り（5 秒）を超え，ノードが閉じた接続を質問者が再利用して `ReadError` になる，という競合で説明できる（事実 4．上限を外しても減らず，速くすると消えた）．
- per_node の後の律速は，質問者 1 プロセスの CPU（JSON の組み立て・解釈，池ごとの管理，イベントループ）である．1 段目の採点を持つ方式（multi_centroid・K = 75）は，その上に重心の変換の 0.11 秒が直列に乗る．

**Iteration 4 と T4 の解釈への影響**（d0006 §6.3 を訂正した）
- T4 の「flood_score は同時に流す質問数を増やすと処理量が 12.8 → 2.5 問/秒に減り，multi_centroid（約 8.4）を大きく下回る」は，**質問者が全ノードで 1 つの接続の池を共有した構成の産物**だった．per_node では flood_score の parallel 30 は 12.72 問/秒・e2e p95 3.6 秒で，multi_centroid（8.55 問/秒・4.87 秒）を処理量・待ち時間とも上回る．
  したがって「全員に聞く方式の代償が処理量に現れる」という T4 の主張は，per_node の測定では成り立たない．全員に聞く方式の代償として残るのは，質問の露出（30 台）と，質問者の 1 問あたりの要求の数（32 対 2）である．
- Iteration 4 の「K = 75 は flood_score より遅い」は，per_node でも成り立つ（事実 3 のとおり，要求の数がほぼ減らずに 1 段目の採点が加わるため）．Iteration 4 の結論（棄却）は変わらない．

**期待との一致・不一致**：事前の推測（per_node で 5〜10 問/秒，e2e p95 5〜10 秒，H-H3 は (i)，shared_unlimited は T4 と同じか少し遅い）は，H-H3 と shared_unlimited では当たり，処理量（12.7 問/秒）・e2e p95（3.6 秒）は推測より良い側に外れた．
「per_node で `requester_cpu_util` が下がる」は外れた（下がらず，同じ 1 コアでより多くの要求を処理した）．

**想定外の挙動**：mc_pn_p30_r1 に再試行を使い切った `ReadError` が 1 問．per_node の flood_score では parallel 30 でも再試行は 1〜2 回で，失敗は 0．fs_pn_p1_r3 の処理量（13.08）が他の 2 回より低いことの理由は未確認．

**学び**
- httpx の 1 つの `AsyncClient` を多数の宛先へのファンアウトに共有すると，同時の要求が数十の段階から 1 要求あたりの費用が増え，同時 900 では 1 コアで約 80 要求/秒しか扱えない．宛先ごとに `mounts` で池を分けると，同じ 1 コアで約 400 要求/秒になる．上限（`max_connections`）を外すことは効かない．
- 負荷の実験で「方式の処理量」を比べる前に，計測器（質問者）の側の CPU 使用率と 1 秒あたりの要求の数を記録し，律速が比べたいもの（専門家の側）にあるかを確かめる必要がある．T4 は計測器の律速を測っていた．
- 再試行で吸収される `ReadError` は，イベントループの遅さの症状として現れる（上限を外しても減らない）．再試行の数は，失敗率よりも先に質問者の飽和を示す指標として使える．
- 事前登録の目安の数値（今回の「接続 300 本」）は，その根拠の計算を事前登録の本文に書くべきである．書いていれば，1 ノードあたりの同時の要求の数（parallel）を見落とした誤りに事前に気づけた．

**限界**
- CPU 使用率はプロセス全体の値で，池の管理・JSON・採点の内訳は分けていない（プロファイルなし）．httpcore の関数が原因という点は推測である．
- 質問者は 1 プロセスのままで，per_node でも 1 コアに張り付いている．したがって per_node の処理量も「質問者 1 プロセスの能力」であり，専門家の側の限界はまだ測れていない．
- 比べる相手は T4（`ffe0880` のイメージ）で，同時性は前後 4 回の揺れの確認でしか担保していない．有線 LAN・300 問・検索だけ．
- 新しい接続の数の基準は未達のまま（上の事実 5 の見積もりの誤り）．

**次の一手**（backlog B21 [auto-decided]）：Iteration 6 は，1 段目の採点の重心を numpy の行列として前計算し，`route_s` を除いた状態で multi_centroid と centroid_probe（K = 75・30）の処理量を per_node で測り直す．
事実 3 のとおり，per_node の下では 1 段目の採点を持つ方式の処理量は route_s で決まっており，デバイスを単位に上位を選ぶ方式も同じ 1 段目の採点を持つので，この費用を外さないとどの二段の方式も比べられない．

## Iteration 4: centroid_probe（二段ルーティング）の実機での待ち時間と処理量の測定

### 調査・計画（2026-10-14．research-cycle の phase 1）

**レバーの由来**：backlog B17 [auto-decided]（Iteration 3 は棄却．B11 の「次の候補」である centroid_probe の実機化を行う）．config.yml の levers 末尾の `experiment.routing: [centroid_probe]`（実機）である．
1 段目の公開情報はシャードにある既存のヘッダー込みの multi_centroid（C = 32）を使い，データ準備・ラベル・シャード・公開情報は作り直さない．

**コードと既存の結果から確かめたこと**（事実）
- 質問者（`src/atrium/requester.py` の `process`）は，ルーターが `needs_probe` を持つときだけ `_probe` で**全ノード**へ probe を送る．`CentroidProbeRouter`（`src/atrium/routing/advert.py`）は `needs_probe` を持たず，`probe_scores` が無いと `ValueError` を投げる．
  したがって今のコードで `routing: centroid_probe` を実機で流すと全問が失敗する．2 段目（候補だけへの probe）の実装が要る．
- 専門家の `/v1/probe`（`src/atrium/node.py`）は `shard_ids` を受け取り，シャードごとに FAISS の検索（上位 1 件）を順に行う．したがって**ノードの probe の仕事量は，そのノードで probe するシャードの数に比例する**．
  配置は 30 台 × 5 受信箱で，受信箱はデバイスに散らばっている（`results/20261014_t4_flood_score_p1_r1/placement.json`）．
- T4 の段ごとの時間（`results/20261014_t4_*/results.jsonl` の `timings`．中央値）：multi_centroid の `route_s` は parallel 1 でも 30 でも 0.111 秒（p95 0.114 秒）で一定である．`_route` はイベントループの上で同期に動くので，1 問 0.111 秒の採点が直列になり，処理量の上限は約 1 / 0.111 ≈ 9.0 問/秒になる．
  実測の 8.3〜8.5 問/秒はこの上限に近い．外部レビュー R4 (b)（parallel 1 で multi_centroid が遅い理由）は，「質問者の側の重心の採点 0.111 秒が律速」とほぼ確かめられた（重心の JSON のリストを毎回 numpy に変換していることが主因かは未確認）．
  flood_score の parallel 30 は `probe_s` 6.77 秒，`retrieve_s` 3.46 秒で，parallel 1 では `probe_s` 0.068 秒，`route_s` 0.0001 秒である．
- T4 の 3 回の揺れは小さい：処理量の 3 回の幅は中央値の ±5% 以内（例：flood_score の parallel 30 は 2.44〜2.58 問/秒，multi_centroid は 8.36〜8.39 問/秒）．parallel 30 の e2e p95 は flood_score 18.9〜20.6 秒，multi_centroid 4.93〜5.02 秒（`results/20261014_t4_load_summary/t4_summary.out`）．
- `mise run deploy` は毎回 `publish_images`（操作端末での `docker build`・`push`）を行う．2026-10-14 の時点で操作端末の ktakahashi は docker グループに無く（`id` の groups は sudo，systemd-journal，elab-ops），`/var/run/docker.sock` は `root:docker` の 660 である（B13・B16 と同じ状態）．制御点の denjo は docker グループに入っている．
  `start.sh` は質問者のイメージの `/etc/atrium-git-head` が操作端末の `GIT_HEAD`（未コミットの変更があれば `-dirty` が付く）と一致しなければ止まる．T4 の連続実行（`t4_chain.sh`）は config.yaml を書き換えるので，実行中の `GIT_HEAD` は常に `<HEAD>-dirty` になる．
- `t4_chain.sh` の `setcfg` は `experiment` の直下（字下げ 2）のキーしか書き換えられず，`routing.probe_k.enronqa`（字下げ 4）は扱えない．また global の `python3` を使っている（利用者の規約では `uv run python` を使う）．

**調査で分かったこと**（tavily-search．事実と推測を分けて書く）
- 分散 selective search の負荷の実測（Kim, Callan, Culpepper, Moffat, "Efficient distributed selective search", Information Retrieval Journal, 2016．https://www.cs.cmu.edu/~callan/Papers/IRJ16-yubink.pdf）は，資源選択の後に一部のシャードだけを検索する構成が，全シャードを検索する構成より処理量（単位時間あたりの問の数）が常に高いと報告している．
  同論文の表 9 では，問の時間に占める資源選択の割合が，中央の標本索引を引く Rank-S で 13.4%，統計だけで選ぶ Taily で 1.6% であり，**中央での資源選択の計算そのものが処理量の律速になりうる**．本研究の multi_centroid の `route_s` 0.111 秒と同じ向きの観察である．
  ただし同論文の利点は「1 問あたりに検索するシャードが 3〜5 個（884 個中）」という強い絞り込みから来ている．本研究の K = 75 は 150 受信箱の半分で，絞り込みの度合いがまったく違う（同論文の数値の倍率は本研究に当てはまらない．推測）．
- 多数のノードへ同時に問い合わせると，最も遅い応答が全体の待ち時間を決める（Dean & Barroso, "The Tail at Scale", CACM 56(2), 2013．https://cacm.acm.org/research/the-tail-at-scale）．同論文の例では，1 台の 99 パーセンタイルが 10 ms でも，全台の応答を待つと 99 パーセンタイルが大きく伸びる．
  centroid_probe の K = 75 は，probe を受けるデバイスの数（約 29 台の見込み）が flood_score（30 台）とほぼ変わらないので，この「届く台数で決まる裾」の効果はほとんど減らないと予想する（推測）．一方，各台で probe するシャードの数は平均 5 → 2.5 に減る．
- Python の公式文書（"Event loop", Python 3 documentation．https://docs.python.org/3/library/asyncio-eventloop.html）は，CPU を使う処理はイベントループを塞ぐので，executor で動かすのが望ましいとしている．質問者の 1 段目の採点（multi_centroid と同じ計算）は，multi_centroid と同じくイベントループの上で動かす（比較の条件をそろえるため．下の「固定する構成」）．これは centroid_probe の処理量の上限を約 9 問/秒にする．

**問い**：T4 で flood_score の処理量が parallel とともに落ちた（12.8 → 2.5 問/秒）原因は，(i) 1 問が届くデバイスの数（要求の数）か，(ii) ノードで検索するシャードの数（仕事量）か．
K = 75 はデバイスの数をほとんど変えずにシャードの数を半分にし，K = 30 はデバイスの数を約 2/3，シャードの数を 1/5 にする（デバイスの数は推定）．2 つの K を測れば，(i) と (ii) をある程度分けられる．

**B17 の要レビューへの対応**
- (a) **K = 30 も条件によらず実機で測る**（事前登録で決める．オフラインの分布の結果を見て決めることはしない）．理由：(1) 受信箱が散らばった 30 台 × 5 の配置では，K = 75 でほぼ全台に届くことは事前にほぼ確実で（Iteration 1 の推定で約 29 台），条件にしても結論が変わらない．
  (2) 上の問い（デバイスの数か，シャードの数か）を分けるには，2 つ目の K が要る．(3) K = 30 は Iteration 1 で test の hit@2 0.690 の点で，負荷と hit の兼ね合いの曲線の点になる．(4) 追加の費用は 15 実行（約 75 分）である．
  K = 30 は単一レバー（`experiment.routing: centroid_probe`）の大きさ K の条件の追加として扱い，判定の主は K = 75（Iteration 1 で選んだ値）とする．
- (b) 質問者の 2 段目は，`experiment.routing` の既存の値（flood_score・multi_centroid など）の挙動を変えない形で実装する（下の「実装」）．flood_score の `_probe` の経路は変えず，単体テストで確かめる．
- (c) 件名を残してアドレスだけを除いた公開情報の変形は，このイテレーションでは扱わない（後の候補として残す）．

**仮説**
- **H-P1（処理量）**：parallel 30 で，centroid_probe（K = 75）の処理量は flood_score より高い．仕組み：ノードで probe するシャードの数が 1 問あたり 150 → 75 に減る．
- **H-P2（待ち時間）**：parallel 30 で，centroid_probe（K = 75）の e2e p95 は flood_score より小さい．
- **H-P3（何が負荷を決めるか．記録）**：parallel 30 の処理量は，デバイスの数よりシャードの数で決まる．K = 75 の処理量が flood_score の 1.5 倍以上なら (ii)（シャードの数），flood_score の ±10% 以内なら (i)（デバイスの数）を支持とみなし，それ以外は「分けられない」とする．
- parallel 1 では，centroid_probe は 1 段目の採点（約 0.111 秒）と probe の両方を待つので，flood_score・multi_centroid のどちらより遅い（e2e p50 は 0.15〜0.20 秒．推測．判定に使わない）．

**単一レバー**：`config.yaml` の `experiment.routing` を，T4 の `flood_score`・`multi_centroid` から **`centroid_probe`**（実機の 2 段目つき）へ変える．K は `routing.probe_k.enronqa`（既存のキー．既定 75．K = 30 の実行だけ 30 に書き換える）．
- **実装**（変更するファイル）
  - `src/atrium/routing/advert.py`：`CentroidProbeRouter` に，質問者が候補だけへ probe を送るための印（例：`probes_candidates = True`）と，候補と probe のスコアから上位 m 個を選ぶメソッド（例：`select_among(candidates, probe_scores)`）を足す．**1 段目の採点を 1 問で 2 回しない**（今の `select` は内部で `candidates` を呼び直すので，そのまま使うと `route_s` が倍になり処理量の上限が約 4.5 問/秒に下がる）．docstring の「実機の 2 段目は未実装」を更新する．
  - `src/atrium/requester.py`：`process` で，候補の印を持つルーターのときだけ，(1) 1 段目の候補を同期に求め（時間は `timings.route_s` に記録．multi_centroid と同じくイベントループの上），(2) 候補の受信箱のシャードを持つノードにだけ，候補のシャードの `shard_ids` で `/v1/probe` を送り（`_probe` に「対象のデータ源」を渡せるようにする．渡さないときは今と同じく全ノード），(3) 候補の中から上位 m 個を選ぶ．
    記録に `probe_recipients`（probe を受けたデバイスの数），`probe_shards`（probe したシャードの数＝K），`probe_shards_per_device`（デバイスごとのシャードの数の並び），`probe_node_s_max`（`ProbeResponse.duration_s` の最大．ノードの計算時間と待ち行列・通信を分けるため）を足す．`query_recipients` は既存の規則（`max(本照会のデバイス数, probe_recipients)`）のまま．
    flood_score の要求の宛先・内容・選択は変えない（`probe_node_s_max` などの記録の追加だけは flood_score にも入ってよい）．
  - オフラインの事前確認の CLI（例：`atrium probe-reach`，`src/atrium/probe_reach.py`．`scripts/remote/probe_reach.sh`，`scripts/tasks/remote.sh` のタスク）：配置（placement.json）と 1 段目・2 段目の採点から，T4 の 300 問と test 2,940 問について，K ∈ {4, 8, 15, 30, 50, 75, 150} ごとの「届くデバイスの数」の平均・中央値・最小・最大と度数，デバイスあたりの受信箱の数，上位 K の再現率（正解の受信箱が上位 K に入る割合），
    300 問の K = 30・75 での選ばれる受信箱（実機の照合用）を出す．既存の `cascade-eval` とその出力は変えない．
  - `tests/`：flood_score は従来どおり全ノードへ全シャードの probe を送ること，centroid_probe は候補を持つノードにだけ候補のシャードで probe を送ること，1 段目の採点が 1 問で 1 回であること，選ばれる受信箱がオフラインの `cascade_scores` と一致すること．
  - 連続実行のスクリプト（`results/<日付>_iter4_load_summary/iter4_chain.sh`．`t4_chain.sh` と同じ形）：`routing.probe_k.enronqa` を書き換えられるようにし，`python3` ではなく `uv run python` を使う．docs（`docs/d0004_metrics.md` など該当の節）に新しい記録の項目と CLI を書く．

**固定する構成**（T4 と同じ）：`dataset: enronqa`，`answer_mode: retrieval_only`，`question_limit: 2`（受信箱ごとの先頭 2 問．300 問．T4 と同じ集合），m = 2（`routing.top_m.enronqa`），`merge: score`，`query_embedding.enronqa: cached`，
1 段目はシャードの既存のヘッダー込みの multi_centroid（C = 32），2 段目は flood_score と同じ `/v1/probe`，`experiment.parallel` ∈ {1, 4, 8, 16, 30}，各条件 3 回，各回の前に deploy し直す，失敗した問は流し直さない，連続実行の間は制御点で他のコンテナを動かさず commit もしない．
1 段目の採点はイベントループの上で同期に行う（multi_centroid と同じ．executor へ移すのは別のレバー）．専門家 30 台・質問者（制御点）の構成は T4 と同じ．

### 事前登録（2026-10-14．実装の後，実行の前に push する．結果を見て変えない）

**実行の順序と回数**（計 34 実行．1 回約 5 分，全体で約 3 時間の見込み）
1. **docker 権限の確認**（実装・テスト・commit・push・この事前登録の push の後，イメージを配る前）：操作端末で `docker info > /dev/null 2>&1 && echo ok` を実行する．
   - ok なら，通常の `mise run deploy` を含む連続実行（T4 と同じ形）に進む．
   - ok でなければ（2026-10-14 の計画の時点では ok でない），**実行を 1 回も始めずに止め**，backlog に `[needs-human]` を記録して Slack で @mention する．選択肢は次の 3 つ．どれを選んでも事前登録の内容は変えない．
     - A1 (Recommended)：人間が docker 権限のある端末で `bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'` を 1 回実行する（作業ツリーに未コミットの変更がある状態で．イメージの印が `<HEAD>-dirty` になる）．
       その後の連続実行は，各回の deploy を `publish_images` を除いた `sync_to_control && remote deploy` に替える（運用ルール 7 の「毎回 build・push」から外れるが，`start.sh` のイメージの照合は残る）．連続実行の最初に，質問者のイメージの `/etc/atrium-git-head` が `<HEAD>-dirty` と一致することを確かめ，一致しなければ 1 回も流さずに止める．
     - A2：人間が docker 権限のある端末から連続実行のスクリプトそのものを流す（運用ルール 7 のまま．約 3 時間の端末の占有）．
     - A3：恒久対応（ktakahashi を docker グループに入れる，または制御点で build する）．B13・B16 から 3 回目なので検討を求める．
   - 連続実行の途中で deploy が失敗した回は，`t4_chain.sh` と同じく 5 分おきに 3 回まで再試行し，それでも失敗した回は結果の無いまま飛ばす．飛ばした回は原因を除いた後に同じ run_id で流し直す（結果のある回は飛ばす規則なので，二重には流れない）．deploy の失敗は失敗率に数えない．
2. **オフラインの事前確認**（イメージを配った後，連続実行の前に 1 回）：`atrium probe-reach` を制御点のコンテナで流し，K = 30・75 の届くデバイスの数の分布と上位 K の再現率を記録する．この結果で条件（K の値・実行の数）は変えない．
3. **環境の揺れの確認（前）**：flood_score の parallel 30 と multi_centroid の parallel 30 を各 1 回（run_id は `<日付>_iter4_anchor_<routing>_p30_pre`）．
4. **centroid_probe（K = 75）**：parallel 1・4・8・16・30 × 3 回（`<日付>_iter4_cp75_p<parallel>_r<1|2|3>`）．
5. **centroid_probe（K = 30）**：同じく 15 回（`<日付>_iter4_cp30_p<parallel>_r<1|2|3>`）．
6. **環境の揺れの確認（後）**：3 と同じ 2 条件を各 1 回（`..._post`）．
- 失敗率の打ち切りは T4 と同じ（同じ K・parallel の 3 回とも失敗率が 10% を超えたら，その K のより大きい parallel は飛ばして記録する）．

**指標**（T4 と同じ定義に加えて）：e2e_s の p50・p95，probe_s の p50・p95，route_s の p50，処理量（成功した問の数 ÷ `run_meta.json` の finished_at − started_at），失敗率とエラーの種類，
1 問あたりの probe を受けたデバイスの数（平均と度数）と probe したシャードの数，デバイスあたりのシャードの数，`probe_node_s_max` の p50・p95，参考に 300 問の hit@2（正解の受信箱が選ばれた割合．T4 の flood_score・multi_centroid の `selected_sources` からも同じ定義で出す）．

**正しさの確認**（満たさなければ判定に進まず，原因を調べて記録する）
- 各実行の失敗の無い問のうち 99% 以上で，実機の `selected_sources` が `probe-reach` の出したオフラインの選択（同じ K）と集合として一致する．
- 各実行の `probe_shards` の平均が K と一致し，`probe_recipients` の平均がオフラインの届くデバイスの数の平均（300 問）と 0.01 以内で一致する．
- 単体テストで，flood_score の probe の宛先と `shard_ids` が変わっていないこと．

**環境の揺れの規則**：前後 4 回の確認の処理量と e2e p95 が，全て T4 の同じ条件の 3 回の範囲の [最小 × 0.9, 最大 × 1.1] に入れば，判定の比べる相手は T4 の 3 回の値とする．
1 つでも外れたら，「環境が変わった」と記録し，判定の比べる相手を前後の確認の値（各条件 2 回）に替える（T4 の値は参考として並べる）．flood_score・multi_centroid の 3 回の全条件を流し直すことはしない．

**判定の規則**（T4 と同じ：比べる 2 条件の回ごとの値の範囲が重ならないときだけ「差がある」）
- **H-P1**：parallel 30 で，K = 75 の 3 回の処理量の範囲が flood_score の範囲より上にあり，重ならない．
- **H-P2**：parallel 30 で，K = 75 の 3 回の e2e p95 の範囲が flood_score の範囲より下にあり，重ならない．
- **このイテレーションの結論（K = 75）**：
  - **採用**（centroid_probe は flood_score に代わる，負荷の軽い方式として以後の候補になる）：H-P1 と H-P2 がどちらも成り立ち，かつ parallel 30 の K = 75 の処理量の 3 回の中央値が flood_score の中央値の 1.5 倍以上（T4 の値なら 3.75 問/秒以上）．1.5 倍は，3 回の揺れ（±5%）の 10 倍の大きさで，運用で意味のある差として決めた．
  - **部分的に支持**：H-P1 か H-P2 の少なくとも一方が成り立つが，採用の条件を満たさない．
  - **棄却**：H-P1 と H-P2 がどちらも成り立たない．
- **H-P3**（記録）：上の仮説の規則（K = 75 の処理量が flood_score の 1.5 倍以上なら「シャードの数」，±10% 以内なら「デバイスの数」，それ以外は「分けられない」）．K = 30 の処理量と，`probe_node_s_max` と `probe_s` の差（待ち行列と通信の時間）を解釈に添える．
- K = 30 は判定に使わず，K = 75・30・2（multi_centroid）・150（flood_score）の処理量と e2e p95 の曲線を，Iteration 1 の hit@2 と並べた表で記録する．

**ノイズの見積もり**：T4 の 3 回の幅は処理量で中央値の ±5% 以内，e2e p95 で ±5% 程度（parallel 30 の flood_score で 18.9〜20.6 秒）．この揺れの大きさなら，1.5 倍の差は回の揺れと区別できる．
実機の選択は決定的（クエリの埋め込みは事前計算，FAISS は同じ索引）なので，選ばれる受信箱は回ごとに変わらないはずである．

**期待効果**（推測．判定に使わない）：K = 75 の probe を受けるデバイスは 1 問あたり約 29 台，K = 30 は約 20 台．parallel 30 の処理量は K = 75 で 3.5〜5 問/秒（flood_score の 1.4〜2 倍），K = 30 で 5〜8 問/秒（1 段目の上限 9 問/秒に近づく）．
parallel 30 の e2e p95 は K = 75 で 8〜14 秒．parallel 1 の e2e p50 は 0.15〜0.20 秒（flood_score の 0.076 秒，multi_centroid の 0.120 秒より遅い）．採用か部分的に支持のどちらかになる見込みで，どちらになるかは事前には言えない．

**限界**：有線 LAN・300 問・検索だけ（回答の生成を含まない）．T4 から時間が経った後の比較なので，環境の揺れの確認でしか同時性を担保できない．1 段目の採点は質問者のイベントループの上で直列に動く既存の実装のままで，処理量の上限（約 9 問/秒）はこの実装に依存する．
K は 75・30 の 2 点だけで，問ごとに K を変える打ち切り（Rank-S・Dynamic Shard Cutoff の型）は扱わない．hit@2 は Iteration 1 のオフラインの値（test 2,940 問）を使い，300 問の実機の hit は参考である．

**次の候補**：採用か部分的に支持で，K = 30 の処理量が 1 段目の上限（約 9 問/秒）に張り付くなら，1 段目の採点の高速化（重心の行列を起動時に 1 度だけ numpy にする，または executor で動かす．multi_centroid にも効く）を別のレバーにする．
棄却なら，負荷が「デバイスの数」で決まることになるので，probe を受けるデバイスの数そのものを減らす選び方（受信箱ではなくデバイスを単位に上位を選ぶ）を候補にする．B17 (c) の件名を残す変形は，それらの後に回す．

### 実装・実験（2026-10-14．research-cycle の phase 2）

- 実装を `adbf605` で push した（`CentroidProbeRouter.select_among`，requester の候補だけへの probe と `probe_shards`・`probe_shards_per_device`・`probe_node_s_max` の記録，`atrium probe-reach`，`iter4_chain.sh`・`iter4_specs.txt`（34 実行））．
  既存の flood_score の probe の宛先・内容は変えていない（単体テストで確認）．pytest 237 件・ruff・`mypy src` は通過．`mypy src tests` の 8 件は変更前からあるもの（新規の追加分ではない）．
- `docker info` が `permission denied ... /var/run/docker.sock` で失敗した（ktakahashi は docker グループに無い．B13・B16 と同じ原因で 3 回目）．事前登録の手順 1 に従い，実験を 1 回も流さずに止めた．事前登録の「実験の回数」は消費していない．
- 未実施: `probe-reach`，揺れの確認（前・後），K = 75・K = 30 の各 15 回，正しさの確認．backlog B18 [needs-human] に判断待ちを記録した．

#### 実行の記録（2026-10-14 22:38 〜 2026-10-15 01:24．B18 の A3 の後に再開．数値のみ．判定・解釈は phase 3）

- docker 権限：`docker info` が ok（B18 A3 済み）．通常の `mise run deploy`（毎回 build・push）を含む連続実行 `results/20261014_iter4_load_summary/iter4_chain.sh`（34 実行）を，HEAD `adbf605`（実行中は commit なし）で流した．`ALL DONE` まで約 2 時間 44 分．
- `probe-reach`：`results/20261014_iter4_probe_reach/`（制御点で実行し rsync で取得．placement は `20261014_t4_flood_score_p1_r1`）．300 問で届くデバイスの数の平均は K = 75 が 29.063（26〜30），K = 30 が 19.873（15〜24）．上位 K の再現率は 0.9133・0.8000，hit@2 は 0.87・0.78．test 2,940 問では K = 75 が 29.119，再現率 0.9078，hit@2 0.7612．
- 実行：34/34 が完了．deploy の再試行・飛ばし・流し直しは無し．失敗率の打ち切りは発動せず．失敗は cp30_p30 の 3 回だけ（r1 0.0100 = 3 問，r2・r3 0.0033 = 1 問．全て `ReadError`）．他の 31 実行は失敗率 0．
- 集計：`results/20261014_iter4_load_summary/iter4_summary.py` → `iter4_summary.out`（事前登録の指標・正しさの確認・揺れの規則・判定規則の機械的な適用）．
- 正しさの確認：全 30 回の centroid_probe で，`selected_sources` のオフライン（probe-reach）との一致率は 1.0000（失敗の無い問すべて），`probe_shards` の平均は K と一致（75.000・30.000），`probe_recipients` の平均は 0.01 以内で一致（cp30_p30_r3 の 19.883 が最大の差 0.010 で，他は 0.001 以内）．
- 環境の揺れの規則：前後 4 回の確認は全て T4 の [最小 × 0.9, 最大 × 1.1] の内．flood_score p30 の処理量 pre 2.581・post 2.505，e2e p95 pre 17.30・post 19.11．multi_centroid p30 の処理量 pre 8.396・post 8.155，e2e p95 pre 4.984・post 4.936．→ 比べる相手は T4 の 3 回．
- 主要な数値（3 回の中央値．処理量は問/秒，e2e は秒）：

| 条件 | 処理量 p1 / p4 / p8 / p16 / p30 | e2e p50（p1 → p30） | e2e p95（p30．3 回の幅） | probe p50（p30） | route p50 |
|---|---|---|---|---|---|
| centroid_probe K = 75 | 5.32 / 4.00 / 3.30 / 2.37 / 1.97 | 0.187 → 13.52 | 27.24（22.82〜27.48） | 8.25 | 0.111〜0.114 |
| centroid_probe K = 30 | 5.93 / 4.91 / 4.31 / 3.60 / 2.99 | 0.168 → 9.15 | 16.14（15.95〜17.77） | 5.06 | 0.112〜0.114 |
| flood_score（T4） | 12.83 / 6.48 / 4.72 / 2.95 / 2.50 | 0.076 → 11.4 | 19.05（18.88〜20.60） | 6.7 | 0.0001 |
| multi_centroid（T4） | 8.30 / 8.49 / 8.45 / 8.43 / 8.39 | 0.120 → 3.5 | 4.95（4.93〜5.02） | - | 0.111 |

  - parallel 30 の処理量の 3 回の幅：K = 75 が 1.974〜2.113，K = 30 が 2.868〜3.012，flood_score（T4）が 2.440〜2.585．
  - `probe_node_s_max`（ノードの計算時間の最大）の p50 は K = 75 で 0.0025〜0.0028 秒，K = 30 で 0.0015〜0.0017 秒（parallel 1〜30 を通して）．p95 は K = 75 で 0.003〜0.007 秒，K = 30 で 0.002〜0.007 秒．
  - `shards_per_device` の平均は K = 75 が 2.583，K = 30 が 1.520．300 問の hit@2（実機）は K = 75 が 0.870，K = 30 が 0.780（K = 30 の p30 は失敗の分だけ 0.779〜0.781），flood_score 0.920，multi_centroid 0.483．
- 判定規則の機械的な出力（`iter4_summary.out`．解釈は phase 3）：H-P1 False，H-P2 False，処理量の中央値の比（K = 75 / flood_score）0.790，結論の規則の適用は「棄却」，H-P3 の規則の出力は「分けられない」．
- 異常：`iter4_summary.py` の実行時に numpy の `Mean of empty slice` の警告（原因は未確認．表の数値は有限で，判定の計算には使っていない箇所）．実行・ログ上の他の異常（OOM・容器の異常終了）は確認していない．

### Iteration 4 実行済み（2026-10-15．research-cycle の phase 3．判定：棄却）

**変更**：`experiment.routing` を `centroid_probe`（実機の 2 段目つき．multi_centroid の上位 K の受信箱を持つノードにだけ，その受信箱の `shard_ids` で `/v1/probe` を送る）にした．K = 75（判定の主）と K = 30（記録）．実装 `adbf605`，34 実行は全て HEAD `adbf605`（実行中の commit なし）．
T4 と同じ 300 問・検索だけ・parallel 1/4/8/16/30 × 3 回．事前登録の規則・基準は変えていない．

**正しさの確認と環境の揺れ**（どちらも通過．判定に進める）
- 30 回全てで，失敗の無い問の `selected_sources` がオフライン（`probe-reach`）の選択と 1.0000 一致．`probe_shards` の平均は K と一致，`probe_recipients` の平均とオフラインの差は最大 0.010（cp30_p30_r3）．
- 前後 4 回の揺れの確認は全て T4 の [最小 × 0.9, 最大 × 1.1] の内 → 比べる相手は T4 の 3 回．

**結果と判定**（parallel 30．3 回の値の範囲．規則は「範囲が重ならないときだけ差」）
- **H-P1（処理量で上回る）：不成立**．K = 75 の 1.974〜2.113 問/秒は flood_score（T4）の 2.440〜2.585 より**下**にあり，重ならない．中央値の比 1.975 / 2.498 = 0.790．
- **H-P2（e2e p95 で下回る）：不成立**．K = 75 の 22.82〜27.48 秒は flood_score の 18.88〜20.60 秒より**上**で，重ならない．
- **このイテレーションの結論（K = 75）：棄却**（H-P1・H-P2 とも不成立）．単に「差が無い」のではなく，3 回の範囲が逆向きに重ならないので，**K = 75 は flood_score より処理量が低く，裾の待ち時間も長い**（信号）．
- **H-P3（記録）：規則の出力は「分けられない」**（比 0.790 は 1.5 倍以上でも ±10% 以内でもない）．ただし，下の分析のとおり「シャードの数」（ii）は明確に支持されない（1 問の probe のシャードの数を 150 → 75 に半分にしても処理量は上がらなかった）．
- K = 30（判定に使わない）：処理量 2.868〜3.012（中央値 2.989．flood_score の 1.20 倍）と e2e p95 15.95〜17.77 秒は，どちらも flood_score と範囲が重ならず良い側にある．ただし 1.5 倍には遠く，Iteration 1 の test の hit@2 は 0.690（flood_score 0.804，K = 75 0.761）である．
- 兼ね合いの曲線（parallel 30 の処理量の中央値／e2e p95 の中央値／Iteration 1 の test の hit@2）：K = 150（flood_score）2.50／19.05／0.804，K = 75 1.97／27.24／0.761，K = 30 2.99／16.14／0.690，K = 2（multi_centroid）8.39／4.95／0.440．K について単調ではない（K = 75 が最も遅い）．
- parallel 1 の e2e p50 は K = 75 0.187 秒，K = 30 0.168 秒で，事前の推測（0.15〜0.20 秒）の範囲．flood_score（0.076）・multi_centroid（0.120）より遅い．

**分析：なぜ K = 75 は flood_score より遅いのか**（段ごとの時間 `results/20261014_iter4_load_summary/iter4_timing_breakdown.{py,out}`，接続の数はノードの uvicorn の access log の送信元ポートの数，再試行の数は `requester.log` の `retrying` の行の数）

事実（記録から計算したもの）
1. **ノードの計算は無視できる**．parallel 30 の `probe_s` の中央値は K = 75 で 8.29 秒，そのうち `probe_node_s_max`（ノード内の probe の計算の最大）は 0.003 秒で，残り 8.287 秒（99.9% 超）はノードの計算の外（通信・待ち行列）である．本照会も同じで，2 個の受信箱への `retrieve_s` 3.82 秒のうちノードの計算は数 ms である．
   ノードの計算時間はシャードの数に比例して K = 75 で 0.0025〜0.0028 秒，K = 30 で 0.0015〜0.0017 秒，flood_score（前後の確認）で 0.007〜0.009 秒だが，どれも e2e の 0.1% 未満である．
2. **1 段目の同期の採点（route_s 0.111 秒）は，parallel 30 では単独の律速ではない**．質問者のイベントループを 1 段目の採点が塞ぐ割合（処理量 × route_s の平均）は，K = 75 で 0.22，K = 30 で 0.34 であり，multi_centroid の 0.93（ほぼ飽和）より十分に小さい．
   一方，**parallel 1 では 1 段目の採点が遅さの主因**である（K = 75 の e2e 0.187 秒のうち route_s 0.114 秒．flood_score の e2e 0.077 秒との差 0.110 秒とほぼ同じ）．
3. **待ちは質問者の側の HTTP の扱いの量で決まっている形をしている**．parallel 30 で 1 秒あたりに質問者が処理した HTTP 要求の数（probe ＋ 本照会 2 件）は，flood_score 79.9（2.498 × 32），K = 75 61.3（1.975 × 31.06），K = 30 65.4（2.989 × 21.87）である．
   1 段目の採点が塞いだ残りのループの時間で割ると，flood_score 79.9，K = 75 78.7，K = 30 98.3 要求/秒になり，flood_score と K = 75 はほぼ同じ値になる．K = 75 は 1 問あたりの要求の数を 32 → 31.06 にしか減らさない（受け取るデバイス 29.06 台）ので，要求の扱いの節約はほぼ無く，1 段目の 0.111 秒の分だけ遅くなった，と読める．
4. **同時に処理中の要求の数が増えると，1 要求あたりの扱いの費用が増える**．parallel 1 の flood_score は 12.83 × 32 ≈ 410 要求/秒を処理できたが，parallel 30 では 80 要求/秒である．問ごとのファンアウトがほぼ同じ（K = 75 の 1 問の届くデバイスの数 26〜30）なのに，parallel 30 の `probe_s` は問ごとの届くデバイスの数とほぼ無相関（相関係数 0.01）で，parallel 1 では弱い正の相関（0.10〜0.16．1 デバイスあたり 1〜3 ms）である．待ちは問自身のファンアウトではなく，全体の混み具合で決まる．
5. **接続の作り直しと再試行が parallel 16 以上で急に増える**．ノードの access log の送信元ポートの数（新しい TCP 接続の数の下限）は，parallel 1 では 1 実行で 37〜39（約 9,600 要求をほぼ同じ接続で使い回す）で，flood_score は parallel 4 で 164，8 で 356，16 で 1,662，30 で 1,346〜1,872，K = 75 の parallel 30 は 2,964，K = 30 の parallel 30 は 1,068 である．
   `post_with_retry` の再試行（`ReadError` などの接続のエラー）は parallel 1〜8 では 0〜4 回だが，parallel 16 で 127〜133 回（数えた 2 実行），parallel 30 で 93〜278 回（K = 75・30 の 6 実行．T4 の flood_score の 2 実行も 143〜230 回）起きている．cp30_p30 の失敗 5 問は，この再試行（2 回）を使い切った `ReadError` である．
   質問者の `httpx.AsyncClient` は全ノードで 1 つの接続の池を共有し，`max_connections` = 512（`src/atrium/experiment.py`）で，parallel 30 の同時の要求（flood_score で約 900，K = 75 で約 870，K = 30 で約 600）はこれを超える．
6. ネットワークの帯域は律速ではない（parallel 30 の probe の要求は 1 件約 15 KB で，80 要求/秒でも約 1.2 MB/s．E0 の実測は約 930 Mbit/s）．

推測（上の事実と矛盾しないが，未確認のもの）
- parallel 30 の律速は，**質問者 1 プロセスの HTTP クライアント（イベントループの上の要求の組み立て・応答の解釈と，接続の池の管理）**であり，専門家の CPU ではない．処理量の低下（flood_score の 12.8 → 2.5 問/秒）は「同時に処理中の要求が増えると 1 要求あたりの費用が増える」形をしており，接続の池の管理の費用が池の大きさ・待ち行列の長さとともに増えること，parallel 16 以上での接続の作り直し（池の上限 512 に当たり，空いた接続を閉じて別のノードへ開き直す）が候補である．
  parallel 4・8 で既に処理量が下がり，その時点では接続の作り直しがまだ少ない（164・356）ので，接続の作り直しだけでは p1 → p4 の低下を説明できない．質問者の CPU 使用率・プロファイルは記録していないので，どちらが主かは分けられない．
- したがって，T4 と本イテレーションの「処理量」は，**フェデレーション全体の負荷ではなく，1 台の質問者が同時に扱える HTTP 要求の数を測っている**．専門家の側の負荷（ノードの計算）は flood_score でも 1 問あたり約 10 ms 未満で，30 台に分散している．
- H-P3 の問い（デバイスの数か，シャードの数か）への答えは，「シャードの数ではない．要求の数（≈ 届くデバイスの数）と 1 段目の採点の時間の和に近い」である．事前登録の H-P3 の規則は，1 段目の採点の費用が処理量を下げる場合を想定しておらず，「分けられない」と出た．

**期待との一致・不一致**：事前の推測（K = 75 で 3.5〜5 問/秒，e2e p95 8〜14 秒，採用か部分的に支持）は外れた．外れた理由は，「ノードの probe の仕事量（シャードの数）が負荷を決める」という計画の仮定が誤っていたためで，実際にはノードの計算は e2e の 0.1% 未満だった．
parallel 1 の e2e（0.15〜0.20 秒），「K = 75 でもほぼ全台（29 台）に届く」という見込みは当たった．

**想定外の挙動**：cp30_p30 の 3 回で `ReadError`（再試行を使い切ったもの）が計 5 問．他の 31 実行は失敗 0．T4 の「`ReadError` は出なかった」は，再試行が吸収していたためで，parallel 16 以上では接続のエラー自体は毎回 100 回前後起きている（T4 の flood_score の parallel 30 も 143〜230 回）．
`iter4_summary.py` の `Mean of empty slice` の警告の原因は，T4 の flood_score の結果（`adbf605` より前のコードで記録）が `probe_recipients` を持つが `probe_shards`・`probe_shards_per_device`・`probe_node_s_max` を持たず，`summarize` が空の並びの平均を取るため（`-W error` で T4 の flood_score の実行だけが警告になることを確かめた）．この値は T4 の行では出力・判定に使っていないので，表と判定は影響を受けない．

**学び**
- 「ノードの仕事量（probe するシャードの数）を減らせば処理量が上がる」は，この実験系では成り立たない．ノードの FAISS の検索は 1 シャードで 1 ms 程度で，負荷は質問者の HTTP のファンアウトの扱いで決まる．受信箱がデバイスに散らばった配置で受信箱を単位に候補を絞っても，届くデバイスの数（＝要求の数）は 30 → 29 台にしか減らない．
- 二段の方式は，1 段目の採点（質問者のイベントループの上で同期に 0.111 秒）を足すので，要求の数が十分に減らない限り flood_score より遅くなる．要求の数が 2/3 になった K = 30 で初めて flood_score を 1.2 倍上回った．
- T4 と本イテレーションの「処理量」は，単一の質問者プロセスの HTTP クライアントの能力を測っている．方式間の負荷の比較を論文で主張する前に，この律速を切り分ける必要がある（d0006 の §6.3 の T4 の記述を限定した）．
- 再試行で吸収される接続のエラーは，失敗率に現れない．負荷の実験では `requester.log` の `retrying` の数と，ノードの access log の接続の数も記録すべきである（今回は事後に数えた）．
- 段ごとの時間は，ノード内の時間（`duration_s`）と質問者の側の時間を対で記録すると，「どこで待っているか」を事後に分けられる．`probe_node_s_max` を足したことで今回の切り分けができた．

**限界**
- 質問者の CPU 使用率とプロファイルを記録していないので，「イベントループの CPU」と「接続の池の待ち」のどちらが主かは分けられない．接続の数は送信元ポートの数（使い回されたポートは数えない）による下限である．
- 3 の「要求あたりの費用がほぼ同じ」は 3 点（flood_score・K = 75・K = 30）の算術による整合で，モデルの検定ではない．K = 30 は同時の要求が少ない分だけ 1 要求あたりの費用が小さい（98 要求/秒）と読めるが，未確認．
- 比べる相手は T4（`ffe0880` のイメージ）で，K = 75・30 とはコード（記録の項目の追加）とイメージが違う．揺れの確認 4 回は T4 の範囲に入った．

**次の一手**（backlog B19 [auto-decided]）：Iteration 5 は，負荷の律速が質問者の HTTP クライアントにあるかを確かめるレバー（質問者の接続の持ち方）にする．事前登録の「棄却なら，デバイスを単位に上位を選ぶ方式」は，律速の切り分けの後に回す（律速が質問者の側なら，どの方式の比較もこの律速を測ることになるため）．

## Iteration 3: ヘッダーを除いた本文から公開情報を作り直した RQ-A の測り直し

### 調査・計画（2026-10-14．research-cycle の phase 1）

**レバーの由来**：backlog B14 [auto-decided]（Iteration 2 の H-R1b 支持を受け，公開情報の作り直しを centroid_probe の実機化より先にする）．実装の形と card_sim の扱いは B15 [auto-decided] で決めた．
config.yml の levers 末尾のレバー名を `advert_eval.input: [body_only]` に確定した（設定キーではなく評価 CLI）．実機は使わず，データ準備・ラベル・シャード・既存の Advert・既存の results は作り直さない・消さない．

**コードとデータから確かめたこと**（事実）
- 4 方式の公開情報は全て受信箱の chunk（`content`．`File:` だけを除いたメールの文字列）から作られる．term_sketch は `term_sketch(content)`，centroid_sim は既存の埋め込み（ヘッダー込みの文字列を埋め込んだ `corpus/<inbox>/emb/<inbox>.f16.npy`）の平均，multi_centroid はその k-means（C = 32，種 `experiment.seed`），
  card_sim は `title`（`clean_email` が先頭行の `Subject:` から取った件名）の頻出 40 個の説明文の埋め込みである（`src/atrium/data_enronqa.py` の `card_text`・`term_sketch`・`kmeans_centroids`，`src/atrium/advert_eval.py` の `build_methods`）．
  したがって card_sim はもともと `Sender:`・`Recipients:` を含まず，残る 3 方式が作り直しの対象になる．
- centroid_sim・multi_centroid の作り直しには，全 73,772 通の本文（区切り線より後）の埋め込みが要る．既存の手順では同じ件数のヘッダー込みのメールを制御点の GPU 1 枚で約 20 分で埋め込んだ（journal の「p0004 の実装と退行の確認」）．本文はヘッダーの分だけ短いので，**所要は 20 分以下と見積もる**（推測．arctic-embed-m-v1.5 は 512 トークンで切る）．
  `remote/header_eval.sh` はデータディレクトリを読み取り専用で載せるので，本文の埋め込みはデータディレクトリに保存せず，評価の中で計算してメモリだけに持つ（73,772 × 768 × 4 バイト ≈ 230 MB）．EQ2（`advert-eval`）の所要は 339 秒で，評価全体は 30 分前後の見込み．
- Iteration 2 で，所有者推定の 1,460 通には区切り線の無いメールも本文が空のメールも 0 通だった．全 73,772 通での数は未確認（評価で数える）．
- 既存の関数で足りる：`header_eval.split_header_body`・`owner_surname`・`classify_question`（A 群），`owner_eval.truncate_to_tokens`・`median_token_count`・`owner_metrics`，`cascade_eval.cascade_scores`・`hit_stats`，`advert_eval` の採点の関数．

**調査で分かったこと**（tavily-search．事実と推測を分けて書く）
- 分散検索の資源選択では，データ源の要約（resource description）を内容の標本から作るのが標準である（Callan, "Distributed Information Retrieval", 2000．https://www.cs.cmu.edu/~callan/Papers/ciir00.pdf．query-based sampling で得た少数の文書の語の統計が完全な要約に十分近いという仮説）．
  要約はもともと「中身の語の統計」として定義されており，メールの宛先のような付属情報を含めるかは扱われていない．本文だけから公開情報を作るのは，この定義に近い（推測）．
- Enron のメールを使う著者推定のコーパスは，ヘッダー・署名などの付属情報を全て除いた本文だけにしている（Enron Authorship Verification Corpus，Mendeley Data．https://data.mendeley.com/datasets/n77w7mygwg/1）．除かないと「誰のメールか」を付属情報で当てることになる（Iteration 2 の H-R1b 支持と同じ向き）．
- 埋め込みは名前を強く保持する．Morris et al., "Text Embeddings Reveal (Almost) As Much As Text", EMNLP 2023（https://aclanthology.org/anthology-files/pdf/emnlp/2023.emnlp-main.765.pdf）は，臨床記録の埋め込みから姓の 95%・名の 94% を復元したと報告している．
  重心（多数の埋め込みの平均）に同じことが言えるかは確かめられていない（未確認）．ただ，ヘッダーのアドレスが全メールに入る受信箱では，重心が持ち主の名前の方向に寄りうる．Iteration 2 で埋め込みの 3 方式もヘッダーを除くと持ち主の特定が下がったことと矛盾しない．
- 仮名化の指針（EDPB, "Guidelines 01/2025 on Pseudonymisation", 2025．https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf）は，直接の識別子を除いても準識別子（役職・勤続年数など）で本人に結びつけられる場合があり，その最も直接の対策は準識別子を除くことだと述べる．
  B14 の要レビュー (d)（持ち主が自分の名前を公開情報から除くのは現実的か）について：ヘッダーを除くことは公開情報を作る前の前処理 1 段で済み，持ち主が自分で行える．ただし本文の署名・引用ヘッダーの名前と話題は準識別子として残るので，「名前を除いた」とは言えない（限界）．

**B14 の要レビューへの対応**
- (a) 順序：B14 のとおり，公開情報の作り直しを先にする．centroid_probe の 1 段目は multi_centroid なので，本文だけの multi_centroid を 1 段目にした centroid_probe（K = 75）の hit@2 を参考として同時に出す（実機化の前の見積もりになる）．
- (b) card_sim の件名：残す（B15）．card_sim の公開情報は変わらないので，対照として退行の確認に使う．
- (c) 設定キーか CLI か：CLI（B15）．
- (d) 上の調査のとおり，ヘッダーの除去は持ち主が行える前処理である．除けない運用では EQ2 の値が実態に近い．両方を並べて報告する．

**仮説**
- **H-B1（見つけてもらう度合い）**：term_sketch の test の hit@2 は，本文だけの公開情報で下がり，下がり幅は A 群（持ち主の姓を含む問．757 問）に集まる（Iteration 2 で A 群の hit@2 0.956，それ以外 0.151）．
  埋め込みの 2 方式（centroid_sim・multi_centroid）は，ヘッダーの成分が無くなる分だけ重心が話題に寄るので，A 群以外では下がらない（推測）．
- **H-B2（持ち主の特定）**：本文だけの公開情報では，ヘッダー込みのメール全体（E）からの持ち主の top-1 は下がる（公開情報からアドレスの語・成分が消えるので）．ヘッダーを除いた本文の先頭 28 トークン（E_body_short）からの top-1 は，入力と公開情報が同じ種類の文字列になるので，上がりうる（向きは事前に決めない）．
- **H-B3（RQ-A の兼ね合い）**：EQ2 の H-A2（公開情報を増やすほど hit も持ち主の特定も上がる）は，本文だけの公開情報でも成り立つ．

**単一レバー**：EnronQA の公開情報（term_sketch・centroid_sim・multi_centroid）を作る入力を，**chunk の `content`（ヘッダー込み）から区切り線より後の本文（`Subject:`・`Sender:`・`Recipients:` を含まない）へ**変える．card_sim は件名だけの公開情報なので変えない（対照）．
- 実装：新しい CLI `atrium body-advert-eval`（`src/atrium/body_advert_eval.py`），`scripts/remote/body_advert_eval.sh`，`scripts/tasks/remote.sh` の `body_advert_eval` タスク，`tests/test_body_advert_eval.py`，`docs/d0004_metrics.md` の節の追加．
  同じ実行の中で，ヘッダー込み（既存の chunk と埋め込み）と本文だけの 2 通りの公開情報を作って同じ採点で比べる．既存の `advert-eval`・`owner-eval`・`header-eval`・`cascade-eval` とその出力，config.yaml は変えない．
- 本文の埋め込み：全 73,772 通の `split_header_body(content)` の本文を `ArcticEncoder.encode_texts`（文書側．接頭辞なし）で埋め込み，既存の保存と同じく **fp16 に丸めてから float32 に戻す**（ヘッダー込みの埋め込みとの精度の条件を揃える）．行の順は既存の埋め込みと同じ．データディレクトリには書かない．

**固定する構成**：`dataset: enronqa`，公開情報の大きさ（件名 40，T = 200，C = 32．dev で選び直さない），m = 2，k-means の種（`experiment.seed`），test 2,940 問とそのクエリ埋め込み，所有者推定のメール 1,460 通（EQ2・T2・Iteration 2 と同じ抜き出し．種 12），
E_body_short の切り詰めの長さ 28 トークン（Iteration 2 と同じ規則で求める），flood_score（既存のヘッダー込みの埋め込み）と centroid_probe の K = 75．ラベル・シャードは変えない．

### 事前登録（2026-10-14．実装の後，実行の前に push する．結果を見て変えない）

**評価する量**（4 方式 × 公開情報 2 通り（`full`：ヘッダー込み，`body_only`：本文だけ））
- test 2,940 問の hit@1・hit@2（95% ブートストラップ区間），全体・A 群（`classify_question` が A を返す問．持ち主の姓を単語として含む）・A 以外（B+C）のそれぞれ．
- 所有者の特定（1,460 通）の top-1（区間）と AUC：入力 E（EQ2 と同じヘッダー込みのメール全体の既存の埋め込み．term_sketch はその文字列），E_body_short（Iteration 2 と同じ），参考に E_body（本文の全体）．
- 差（`body_only` − `full`）は，同じ質問・同じメールの組で対にしたブートストラップ（1,000 回．種は `experiment.seed`．問またはメールの番号を復元抽出して差の平均を取る）の 95% 区間で示し，
  区間の上端 < 0 を「下がる」，下端 > 0 を「上がる」，それ以外を「差がはっきりしない」とする．
- H-A2 の曲線（EQ2 と同じ大きさの候補．term_sketch の T ∈ {10, 20, 50, 100, 200}，multi_centroid の C ∈ {2, 4, 8, 16, 32}）の test の hit@1・所有者 top-1（E）と，大きさとのスピアマンの順位相関を `body_only` で出す．card_sim は `full` と同じ値になる．
- 参考（判定に使わない）：本文だけの multi_centroid を 1 段目にした centroid_probe（K = 75）の hit@1・hit@2，公開情報の大きさ（バイト），term_sketch の上位 20 語・200 語に持ち主の姓が入る受信箱の数と card_sim の説明文に持ち主の姓が入る受信箱の数（`full`・`body_only`），
  全 73,772 通のうち区切り線の無いメール・本文が空のメールの数（区切り線が無ければ全体を本文とし，本文が空ならそのメールは本文だけの公開情報の計算から除く．除いた数が全体の 1% を超えたら限界に書く）．

**退行の確認**（一致しなければ判定に進まず，原因を調べて記録する．許容は 5e-4，term_sketch だけ 2e-3（B8））
- `full` の hit@1・hit@2・所有者 top-1（E）・AUC が EQ2（`results/20261009_eq2_advert_eval/metrics.json`）と一致する．基準：hit@2 は card_sim 0.1527，term_sketch 0.3592，centroid_sim 0.3228，multi_centroid 0.4401．所有者 top-1 は 0.1685，0.4247，0.2842，0.5267．
- `full` の E_body_short の top-1 が Iteration 2（`results/20261014_iter2_header_eval/metrics.json` の `subset.E_body_short`）と一致する（card_sim 0.0342，term_sketch 0.2185，centroid_sim 0.0836，multi_centroid 0.1075）．
- `full` の centroid_probe（K = 75）の hit@2 が Iteration 1（`results/20261014_iter1_cascade_eval/metrics.json`）の 0.761 と一致する．
- card_sim の `body_only` の全ての値が `full` と完全に一致する（公開情報を変えていないことの確認）．
- 本文の埋め込みの行数が既存の埋め込みの行数と受信箱ごとに一致する．

**判定の規則**
- **H-B1**：方式ごとに，全体と A 以外の hit@2 の差の区分（下がる・上がる・差がはっきりしない）を記録する．term_sketch で「全体は下がる，かつ A 以外は差がはっきりしない」なら H-B1 の term_sketch の部分を支持とする．
- **H-B2**：方式ごとに，E と E_body_short の所有者 top-1 の差の区分を記録する（向きは事前に決めない）．
- **H-B3**：EQ2 の H-A2 と同じ規則（term_sketch・multi_centroid は `body_only` の曲線，card_sim は `full` の値で，大きさと hit@1 の順位相関と，大きさと所有者 top-1（E）の順位相関がどちらも正なら，その方式で単調な兼ね合いがある．3 方式全てで支持，どれでもなければ反証，それ以外は部分的に支持）．
- **このイテレーションの結論（成功条件）**：最良の公開情報の方式 multi_centroid（centroid_probe の 1 段目）について，
  - **採用**（本文だけの公開情報を以後の既定の候補にする）：hit@2 の差の 95% 区間の下端が −0.03 より上（見つけてもらう度合いをほぼ失わない），かつ E_body_short の所有者 top-1 の差の区間の上端が 0 より下（ヘッダーを除いた本文からも持ち主を当てにくくなる）．
  - **部分的に支持**：どちらか一方だけが成り立つ．
  - **棄却**：どちらも成り立たない．
  - 非劣性の幅 0.03 は，EQ2 の hit@2 の区間の半幅（約 ±0.018）の 1.5 倍強で，対にした差の区間は単独の区間より狭い見込み（推測）．
  - どの結論でも，`d0006_research_overview.md` の RQ-A の数値に「ヘッダーを除いた公開情報での値」を並べて書く．採用でもシャードの公開情報は自動では作り直さない（`data.enronqa` に入力の指定を足す config.yaml の形式の変更になるので，B15 の要レビューとして確認する）．

**ノイズの見積もり**：オフラインの評価で流し直しの揺れは無い．k-means は faiss の CPU 実装で種を固定している．本文の埋め込みは GPU の計算順の違いで 1e-3 程度の値の揺れがありうるが，評価は 1 回だけなので結果を固定する．
hit@2 の 95% 区間の半幅は 2,940 問で約 ±0.018，A 群（757 問）で約 ±0.02〜0.03，所有者 top-1 は 1,460 通で約 ±0.02〜0.025．term_sketch の値は文字列のハッシュの種で ±0.002 揺れる（B8．PYTHONHASHSEED は固定しない）．

**期待効果**（推測．判定に使わない）：term_sketch の hit@2 は 0.359 → 0.20〜0.28（A 群の 0.956 が大きく下がり，A 以外の 0.151 は同程度）．multi_centroid の hit@2 は 0.440 → 0.40〜0.47．
所有者 top-1（E）は 3 方式とも下がる（multi_centroid 0.527 → 0.35〜0.45）．E_body_short の所有者 top-1 は multi_centroid で 0.108 → 0.10〜0.16 で，「採用」より「部分的に支持」の可能性が高い．

**実行**（実装・テスト・push の後に 1 回だけ．所要 30 分前後の見込み．制御点の GPU 1 枚）：`bash scripts/tasks/remote.sh body_advert_eval 20261014_iter3_body_advert_eval 20261009_eq2_advert_eval`
（Iteration 2・Iteration 1 の metrics.json を既定の引数で読む）．出力は `results/20261014_iter3_body_advert_eval/`（`metrics.json`，`analysis_report.md`）．本文・本文の埋め込みは results/ と git に入れない．

**限界**：メール先頭のヘッダーだけを除き，本文の中の引用ヘッダー（`From:`・`To:`）と署名の人名は残る（公開情報にも名前が残りうる）．card_sim は件名を残すので，件名の中の名前は除かれない．
質問の側（A 群）の人名は変えない．flood_score の受信箱の埋め込みはヘッダー込みのまま（probe はメールそのものとの照合で，公開情報ではない）．公開情報の大きさは EQ2 で選んだ値に固定し，本文だけの公開情報で dev から選び直さない．

**次の候補**：採用・部分的に支持なら，centroid_probe の実機化（backlog B11）で 1 段目に使う公開情報をどちらにするかを，本イテレーションの参考の centroid_probe の値で決める．

### 実行の要約（2026-10-14．`results/20261014_iter3_body_advert_eval/`．数値のみ．判定・解釈は phase 3）

- 実装 commit `e57ab75`・事前登録 `6ead387`・結果 `8ac3d38`．実験は 1 回だけ（1,332 秒）．docker 権限不足で一度止まった（backlog B16．人間が `publish_images` を実行して再開）．
- 退行の確認：22 項目中 22 項目が通過．最大差は term_sketch の full の所有者 top-1（E）の +1.37e-3（許容 2e-3）．card_sim の body_only は full と完全に一致．本文の埋め込みの行数は受信箱ごとに一致．区切り線なし・本文空は 0 通．
- hit@2（full → body_only．差は対にしたブートストラップの 95% 区間）：multi_centroid 0.440 → 0.347（−0.093 [−0.110, −0.076]），A 以外 0.368 → 0.318（−0.050 [−0.068, −0.034]）．term_sketch 0.359 → 0.328（−0.031 [−0.038, −0.023]），A 以外 0.152 → 0.147（−0.005 [−0.011, +0.001] unclear），A 群 0.956 → 0.851．
  centroid_sim 0.323 → 0.227（−0.096）．card_sim 0.153（差 0）．参考の centroid_probe（K = 75）0.761 → 0.751（−0.011 [−0.021, +0.000] unclear）．
- 所有者 top-1（E → E_body_short）：multi_centroid E 0.527 → 0.420（−0.107），E_body_short 0.108 → 0.120（+0.012 [−0.003, +0.027] unclear）．term_sketch E 0.426 → 0.314（−0.112），E_body_short 0.218 → 0.197（−0.022 [−0.031, −0.013] lower）．
  multi_centroid の E_body（参考）0.447 → 0.486（+0.038 higher）．
- 持ち主の姓が term_sketch の上位 20 語に入る受信箱：full 132 → body_only 119（150 中）．
- 事前登録の判定規則の出力：H-B1 True，H-B3 supported（term_sketch・multi_centroid とも単調），multi_centroid の結論は `keeps_hit` False・`hides_owner` False → reject（hit@2 差の下端 −0.110 < −0.03，E_body_short の差の上端 +0.027 > 0）．

### Iteration 3 実行済み（2026-10-14．research-cycle の phase 3．判定：棄却）

**変更**：EnronQA の公開情報（term_sketch・centroid_sim・multi_centroid）を作る入力を，ヘッダー込みの chunk から区切り線より後の本文（Subject なし）へ変えた（`atrium body-advert-eval`，実装 `e57ab75`，事前登録 `6ead387`，結果 `8ac3d38`）．
card_sim は対照として変えていない．シャードの公開情報・ラベル・config.yaml は変えていない．評価は 1 回だけ（docker 権限の停止 B16 は評価の開始前で，回数を消費していない）．

**結果と判定**（事前登録の規則のまま．基準は変えていない）
- 退行の確認：22/22 通過（最大の差は term_sketch の full の所有者 top-1（E）の +1.37e-3 で，B8 の許容 2e-3 の中）．card_sim の body_only は full と完全に一致し，本文の埋め込みの行数も一致した．区切り線なし・本文空は 0 通で，限界の条件（1% 超）には当たらない．
- **このイテレーションの結論（multi_centroid）：棄却**．
  - 見つけてもらう度合い：hit@2 0.440 → 0.347，差 −0.093 [−0.110, −0.076]．区間の下端は非劣性の幅 −0.03 を大きく下回り，区間の上端も 0 より下（はっきり下がる）．差の大きさは区間の半幅（±0.017）の約 5 倍である．
  - 持ち主の特定（E_body_short）：0.108 → 0.120，差 +0.012 [−0.003, +0.027]．区間が 0 をまたぐので「差がはっきりしない」で，「当てにくくなる」（上端 < 0）は成り立たない．点推定はむしろ上向きである．
- **H-B1：term_sketch の部分は支持**（全体 −0.031 [−0.038, −0.023] は下がる，A 以外 −0.005 [−0.011, +0.001] は差がはっきりしない）．下がり幅は A 群（0.956 → 0.851，−0.106）に集まった．
  **埋め込みの 2 方式についての予想（A 以外では下がらない）は外れた**：A 以外の hit@2 は multi_centroid −0.050 [−0.068, −0.034]，centroid_sim −0.015 [−0.026, −0.005] で，どちらも下がった（判定の規則の対象ではなく，記録）．
- **H-B2（記録．向きは事前に決めていない）**：E からの top-1 は 3 方式ともはっきり下がる（term_sketch −0.112，centroid_sim −0.102，multi_centroid −0.107．区間はどれも上端 < −0.08）．
  E_body_short からは term_sketch だけ下がり（−0.022 [−0.031, −0.013]），centroid_sim（−0.010）と multi_centroid（+0.012）は差がはっきりしない．参考の E_body では multi_centroid が**上がる**（0.447 → 0.486，+0.038 [+0.016, +0.060]）．
- **H-B3：支持**．本文だけの公開情報でも，大きさと hit@1・所有者 top-1（E）の順位相関は term_sketch 0.90・1.00，multi_centroid 1.00・1.00，card_sim（full の値）1.00・1.00 で，3 方式とも正である．EQ2 の H-A2（単調な兼ね合い）は，公開情報からヘッダーを除いても成り立つ．
- 参考の centroid_probe（K = 75）：hit@2 0.761 → 0.751，差 −0.011 [−0.021, +0.000]（差がはっきりしない．A 群だけ −0.017 で下がる）．

**分析**
- **ノイズと信号**：オフラインの評価で流し直しの揺れは無く，揺れは問・メールの標本による区間と term_sketch のハッシュの種（±0.002．B8）だけである．multi_centroid の hit@2 の差（−0.093）と E からの top-1 の差（−0.107）は，対にした区間がどちらも 0 から 0.07 以上離れており信号である．
  E_body_short の multi_centroid（+0.012）・centroid_sim（−0.010），centroid_probe（−0.011）は区間が 0 をまたぎ，ノイズと区別できない．term_sketch の A 以外の −0.005 は B8 の揺れの 2 倍強だが区間が 0 を含む．
- **因果として言えること**：単一レバー（公開情報の入力からヘッダーの 3 行を除く）で，multi_centroid の見つけてもらう度合いが 0.09 下がり，ヘッダー込みのメール（E）からの持ち主の特定が 0.11 下がった．ヘッダーを除いた本文（E_body_short）からの持ち主の特定は下がらなかった．
  事前の期待（hit@2 は 0.40〜0.47，E_body_short は 0.10〜0.16 で「部分的に支持」）のうち，E_body_short は期待の範囲に入り，hit@2 は期待より下に外れた．
- **埋め込みの方式が A 以外の問でも下がった理由（推測．未確認）**：このレバーは `Sender:`・`Recipients:` のアドレスと同時に `Subject:` の件名も除いている（B12 の定義）．件名は話題の要約であり，重心を話題の側に寄せていた可能性がある．
  また，A 以外の問（B 群）は持ち主以外の相手の名前を含むことがあり，ヘッダーの相手のアドレスがそれと一致していた可能性もある．どちらの寄与かは今回の結果からは分けられない（件名だけを残す変形を測っていない）．
- **兼ね合いの曲線の上での位置（点推定．曲線の点には区間が無い）**：multi_centroid の本文だけの C = 32（hit@1 0.268，所有者 top-1（E）0.420）は，ヘッダー込みの C = 8（0.253，0.395）と C = 16（0.289，0.436）の間にあり，ヘッダー込みの曲線とほぼ同じ線の上に乗る．
  multi_centroid にとってヘッダーの除去は，兼ね合いの線そのものを良くするのではなく，公開情報を小さくしたのと同じ向きに線の上を動かした，と読める．
  term_sketch では，本文だけの T = 50（0.231，0.286）がヘッダー込みの T = 10（0.232，0.415）と同じ hit@1 で所有者 top-1 が 0.13 低く，ヘッダーの除去で線そのものが良い側に動いた．term_sketch のヘッダー込みの所有者の特定は，ヘッダーのアドレスの語に強く依存していた（Iteration 2 の H-R1b と同じ向き）．
- **どの入力を持つ相手を想定するか**：ヘッダー込みのメール（E）を手にした相手は `Recipients:` から持ち主を直接読めるので，E からの持ち主の特定が下がったことは，現実の露出の減り方を表さない．
  ヘッダーを除いた本文を手にした相手（E_body_short・E_body）に対しては，本文だけの公開情報は持ち主を隠さず，multi_centroid の E_body ではむしろ当てやすくなる（公開情報と入力が同じ種類の文字列になるため．推測）．
- **ヘッダーを除いても名前は残る**：持ち主の姓が term_sketch の上位 20 語に入る受信箱は 132 → 119（150 中），全 200 語では 141 → 135 である．持ち主の姓の大半は本文（署名・引用ヘッダー）から来ており，ヘッダーの除去は名前の除去にならない．A 群の term_sketch の hit@2 が 0.851 と高いまま残るのも同じ理由と読める．
- **centroid_probe は 1 段目の公開情報の変化に強い**：1 段目の multi_centroid の hit@2 が 0.093 下がっても，K = 75 の二段では差 −0.011（区間は 0 を含む）にとどまった．正解が上位 75 に入っていれば，2 段目の probe（ヘッダー込みの埋め込み）で取り戻せるためである．
- 想定外の挙動（発散・OOM・言語の崩れ）は無い．所要 1,332 秒（見込みの 30 分前後の中）．

**学び**
- 「公開情報からヘッダーを除く」は，multi_centroid では見つけてもらう度合いを 0.09 失う一方で，ヘッダーを除いた本文からの持ち主の特定は減らさない．持ち主の名前は本文（署名・引用ヘッダー）に残るので，前処理 1 段の除去は露出の対策として弱い．準識別子を除く対策（EDPB の指針）には本文の中の名前の除去まで要る．
- 持ち主の特定を「ヘッダー込みのメール全体（E）」で測ると，ヘッダーの除去の効果を過大に見積もる．E を持つ相手はヘッダーから持ち主を直接読めるので，露出の評価にはヘッダーを除いた入力（E_body_short・E_body）を使う方がよい．
- 公開情報の除去の効果は方式で違う．語のスケッチでは兼ね合いの線が良くなり，重心の方式では線の上を動くだけだった．「何を除くか」の比較は，同じ大きさではなく兼ね合いの曲線どうしで比べる必要がある．
- 単一レバーとしての「ヘッダー」には件名も含めたので，件名の寄与とアドレスの寄与が混ざっている．名前の寄与だけを確かめたいなら，件名を残しアドレスだけを除く変形が要る．

**限界**
- 曲線の点（C・T ごと）には区間が無く，兼ね合いの線の比較は点推定である．
- 件名とアドレスの寄与を分けていない．本文の中の引用ヘッダー・署名の名前は残る．card_sim の件名の名前（31/150 の受信箱）も残る．
- 本文だけの公開情報の大きさは dev から選び直していない（事前登録どおり EQ2 の値に固定）．
- term_sketch の値は ±0.002 の揺れと退行の許容 2e-3 を持つ（B8）．

**RQ-A の主張への含意と記述の訂正**
- EQ2 の H-A2（公開情報を増やすほど見つけてもらう度合いも持ち主を当てられる度合いも上がる）は，ヘッダーを除いた公開情報でも成り立つ（H-B3 支持）．RQ-A の中心の主張は残る．
- ただし数値はヘッダーに依存していた．multi_centroid の hit@2 0.440 は本文だけでは 0.347，term_sketch の 0.359 は 0.328 であり，d0006 の §6.3 の表はヘッダー込みの公開情報の値であることを明記し，本文だけの値を並べた（事前登録のとおり）．
  あわせて，§6.3 の限界の「ヘッダーを除いて作り直したときの hit と持ち主の特定は未測定」を，今回の結果に書き換え，持ち主の姓の由来（ヘッダーより本文が多い）を追記した．journal の EQ2 の節に注記を足した（既存の値は変えていない）．
- **B15 の「採用でもシャードを作り直さない」の扱い**：判定は棄却なので，シャードの公開情報はヘッダー込みのまま既定とし，作り直さない．`data.enronqa` に入力の指定を足す config.yaml の形式の変更も要らなくなった．B15 の要レビューのうち，この点は閉じる（件名を除いた card_sim の変形の問いは残る）．

**次の一手**（backlog B17 [auto-decided]）：Iteration 4 は centroid_probe の実機化（backlog B11 の内容）．1 段目の公開情報は既存のシャードのヘッダー込みの multi_centroid（K = 75）を使う．
本文だけの公開情報は，K = 75 の二段で hit@2 が −0.011（区間は 0 を含む）しか変わらないが，持ち主の特定を減らさず，シャードの作り直しも要るので，実機化の 1 段目には使わない．

## Iteration 2: T2 の入力からヘッダーを除いた持ち主推定の測り直し

### 調査・計画（2026-10-14．research-cycle の phase 1）

**レバーの由来**：backlog B10 の [human-decided 2026-10-14]（外部レビューの R1・R2 を Iteration 1 の後に行う）と B11 [auto-decided] による．config.yml の levers 末尾に `owner_eval.input`（R1）を追記した．
実機は使わず，データ準備・ラベル・シャード・公開情報（Advert）は作り直さない．

**コードとデータから確かめたこと**（事実）
- EnronQA のメールの文字列は `Subject: …` `Sender: …` `Recipients: [...]` `File: …` の各行，区切り線 `=====================================`，本文の順に並ぶ（`tests/test_data_enronqa.py` の `RAW`）．
  `clean_email` は `File:` の行だけを除くので，受信箱の本文（`content`）・埋め込み・公開情報（term_sketch の語の文書頻度）は，`Subject:`・`Sender:`・`Recipients:` の行を含んで作られている．
- T2 の E_short（`owner_eval.py`）は，この本文を先頭から 28 トークンに切ったものである．28 トークンはヘッダーの 3 行でほぼ埋まると見込まれる（Recipients が 1 件でも `Subject:`・`Sender:` の行と合わせて 20 トークン前後．未計測）．
- 重複の検出（`write_questions_files`）は既に `text.split(HEADER_SEPARATOR, 1)[-1]` で区切り線より後だけを使っており，リポジトリ内に「区切り線より前をヘッダーとする」定義が既にある．
- term_sketch は語を `[a-z0-9]+` に分けるので，`stephanie.panus@enron.com` は `stephanie`・`panus`・`enron`・`com` になる．持ち主のアドレスはほぼ全てのメールの `Sender:` か `Recipients:` にあるので，文書頻度の上位に入りうる（未計測．これを (a) で数える）．

**調査で分かったこと**（tavily-search．事実と推測を分けて書く）
- EnronQA の論文（Ryan et al., "EnronQA: Towards Personalized RAG over Private Documents", arXiv:2505.00263, 2025．https://arxiv.org/html/2505.00263v1）は，質問の生成を「10 通の中から 1 通を特定できるほど具体的」になるよう最適化したため，質問が**メール中の特定の実体を名指しする必要があった**と書き，BM25 の Recall@5 が 87.5 と高いのは質問とメールの語の重なりが大きいためと述べている．
  R2（質問文の人名による見つけやすさの水増し）は，データセットの作り方から起こりうる．人名を含む質問の割合は論文に書かれていない（未確認．本イテレーションで数える）．
- Enron のメールを使う著者推定・本文の研究では，ヘッダー・引用・機械的な定型文を除く前処理が一般的である（EnronSent，Styler 2011．https://www.colorado.edu/ics/sites/default/files/attached-files/01-11_0.pdf は引用と機械生成の文を状態機械で除いた．Enron Authorship Verification Corpus，Mendeley Data．https://data.mendeley.com/datasets/n77w7mygwg/1 も同じ前処理を全文書に適用している）．
  ヘッダーを残したまま「本文から持ち主を当てる」と，文体や話題ではなくアドレスで当てることになる，というのが外部レビュー R1 の懸念であり，これらの前処理の慣行と同じ向きである（推測．本研究のデータで (b) によって確かめる）．
- 本文の中にも，転送・返信の引用ヘッダー（`-----Original Message----- From: … To: …`）と署名に人名が残る（EnronSent が除いた対象）．本イテレーションはメール先頭のヘッダーだけを除き，引用・署名は残す（限界に書く）．

**仮説**
- **H-R1a**：term_sketch の特徴語（受信箱ごとの上位の語）の上位は，ヘッダーにしか現れない語（アドレス・人名・ドメイン）が多くを占める．
- **H-R1b**：T2 で term_sketch の E_short（0.543）が Q（0.292）や E（0.427）より高かったのは，E_short の 28 トークンがヘッダー（持ち主のアドレス）で占められ，それが特徴語と一致したためである．ヘッダーを除いた本文の先頭 28 トークン（E_body_short）では，term_sketch の持ち主の top-1 は大きく下がる．
  埋め込みの 3 方式（card_sim・centroid_sim・multi_centroid）は，T2 で既に「E_short の方が低い」であり，ヘッダーの除去で判定は変わらないと予想する（推測）．
- **H-R2**：人名を含む質問は，含まない質問より見つけやすい（flood_score の hit@2 0.804 の一部は名前の一致による）．

**単一レバー**：T2（`atrium owner-eval`）の短い入力を，**「本文（ヘッダー込み）の先頭 28 トークン（E_short）」から「区切り線より後の本文の先頭 28 トークン（E_body_short）」へ**変える．
- 実装：`src/atrium/header_eval.py`（新規．`owner_eval` の `median_token_count`・`truncate_to_tokens`・`owner_metrics`・`compare_inputs` と `cascade_eval` の採点を再利用する）と CLI の `atrium header-eval`，`scripts/tasks/remote.sh` の `header_eval` のタスク．
  既存の `owner-eval`・`advert-eval`・`cascade-eval` とその出力は変えない．`config.yaml` は変えない．
- (a) の特徴語の集計と R2 の集計は，同じ CLI の追加の出力として出す（判定の対象は下のとおり）．

**固定する構成**：`dataset: enronqa`，EQ2 で選んだ公開情報の大きさ（件名 40，T = 200，C = 32．ヘッダー込みで作った既存の Advert のまま），所有者推定のメール 1,460 通（EQ2・T2 と同じ抜き出し．種 12），test 2,940 問，
切り詰めの長さは T2 と同じ 28 トークン（test の質問のトークン数の中央値．arctic-embed-m-v1.5 のトークナイザ），E_body_short の埋め込みは T2 の E_short と同じく文書側（接頭辞なし）．m = 2，centroid_probe の K = 75．

### 事前登録（2026-10-14．実装の後，実行の前に push する．結果を見て変えない）

**「ヘッダー」の範囲（B11 の要レビュー．backlog B12）**：区切り線（`HEADER_SEPARATOR`）より前の全行（`Subject:`・`Sender:`・`Recipients:`．`File:` は既に除かれている）をヘッダーとし，**判定に使う E_body_short は `Subject:` を含めない**．
理由：(1) リポジトリの既存の定義（重複の検出の `split(HEADER_SEPARATOR, 1)[-1]`）と同じにできる．(2) R1 の問いは「書かれた中身ではなく，メールの付属情報で持ち主を当てているか」であり，件名も送信者が付ける付属情報で，返信・転送で受信箱をまたいで同じものが繰り返される．
(3) card_sim の公開情報は件名そのもの（`card_text`）なので，件名を本文に含めると card_sim に，アドレスが term_sketch に与えたのと同じ種類の有利さを与える．
参考として，件名の行の値＋本文の先頭 28 トークン（E_subj_short）も出すが，判定には使わない．
区切り線の無いメールは文字列全体を本文とし（既存の定義と同じ），その通数を出す．1,460 通の 1% を超えたら限界に書く．

**評価する入力**（4 方式：card_sim・term_sketch・centroid_sim・multi_centroid．同じ採点の関数）
- 判定に使う：Q（test の質問文），E_short（T2 と同じ．ヘッダー込みの先頭 28 トークン），E_body_short（ヘッダーを除いた本文の先頭 28 トークン）．
- 参考：E_body（ヘッダーを除いた本文の全体を文書側で埋め込み直したもの．埋め込みのモデルの上限で切れる），E_subj_short．
- 本文が空（トークン 0）になるメールは，E_body_short・E_body・E_subj_short と，判定で比べる E_short の両方から除き（同じ部分集合で対にして比べる），その通数を出す．退行の確認は 1,460 通の全体で行う．

**退行の確認**（一致しなければ判定に進まず，原因を調べて記録する）
- 1,460 通の全体で，Q の top-1，E の top-1・AUC，E_short の top-1・AUC が T2 の `results/20261014_t2_owner_eval/metrics.json` と一致する．許容は 5e-4，**term_sketch だけは 2e-3**（B8）．
  基準：E_short の top-1 は card_sim 0.0740，term_sketch 0.5432，centroid_sim 0.1651，multi_centroid 0.2110．切り詰めの長さが 28 であること．
- R2 の集計：群ごとの hit@1・hit@2 を問の数で重みづけて合わせた値が，全 test の値（flood_score 0.7221/0.8041，multi_centroid 0.3486/0.4401，centroid_probe K = 75 の 0.688/0.761．Iteration 1 の metrics.json の値）と 5e-4 以内で一致する．

**判定の規則**（95% ブートストラップ信頼区間．種は experiment.seed．T2 と同じ関数）
- **H-R1b（主判定．term_sketch）**：E_body_short と E_short（同じ部分集合）の top-1 を比べる．
  - **支持**：E_body_short の区間の上端が E_short の区間の下端より下，かつ E_body_short と Q の比較（T2 の H-O1 の 3 区分）が「higher」でない（T2 の term_sketch の結論「同じ長さでも，メール本文の方が持ち主を当てやすい」はヘッダーに依存していた）．
  - **部分的に支持**：E_body_short の区間が E_short より下だが，Q との比較は「higher」のまま（ヘッダーは寄与したが，本文だけでも質問文より当てやすい）．
  - **棄却**：E_body_short と E_short の区間が重なる（ヘッダーは T2 の term_sketch の値の主因ではない）．
- 埋め込みの 3 方式は，E_body_short と Q の H-O1 の 3 区分と，E_short からの差を記録する（判定は付けない）．
- **H-R1a（特徴語．記述と閾値）**：受信箱ごとの term_sketch の上位 20 語と上位 200 語のうち，「ヘッダー依存の語」（その受信箱で，区切り線より後の本文だけで数えた文書頻度が，ヘッダー込みの文書頻度の半分未満の語）の割合を，150 受信箱で平均する．
  上位 20 語の平均の割合が 0.25 以上なら「特徴語の上位はヘッダーに多くを占められる」とする．
  参考として，全メールの `Sender:`・`Recipients:` のアドレスから取った語を，ドメインの語（@ より後）とアドレスの名前の語（@ より前．3 文字以上，英語の機能語を除く）に分けた割合，持ち主の姓（受信箱の名前の `-` より前）が上位 20 語に入る受信箱の数，語の重み（文書頻度の割合）で重みづけた割合を出す．
- **H-R2（追加の集計）**：test の質問を 3 群に分ける．(A) 持ち主の姓（受信箱の名前の `-` より前．3 文字以上）を単語として含む（大文字小文字を区別しない），(B) A でなく，大文字で始まる語のうち小文字にするとアドレスの名前の語の集合に入るものを含む，(C) どちらでもない．
  flood_score・multi_centroid・centroid_probe（K = 75）について群ごとの n・hit@1・hit@2 と区間を出す（参考に term_sketch も）．
  方式ごとに，(A ∪ B) の hit@2 の区間の下端が C の区間の上端より上なら「名前を含む問の方が見つけやすい」と書く．B で一致した語の上位 30 個を出力し，人名でない語の誤検出を目で確かめられるようにする．
- **このイテレーションの結論**は H-R1b の 3 区分とする（成功・失敗ではなく，T2 と EQ2 の記述を書き直すかどうかを決める確認である）．支持・部分的に支持なら T2 の節と EQ2 の「持ち主を当てることの方に役立つ」の term_sketch の記述を訂正する．

**ノイズの見積もり**：オフラインの評価で流し直しの揺れは無い．持ち主の top-1 の 95% 区間の半幅は 1,460 通で約 ±0.025（T2 の term_sketch の E_short は [0.515, 0.568]）．term_sketch の採点は文字列のハッシュの種で揺れる（Q の top-1 ±0.001，E の top-1 ±0.002．B8）．
term_sketch の上位の語は同じ文書頻度の語の並びがハッシュの種で変わるので，上位 20・200 語の境界の語が入れ替わりうる（H-R1a の割合が受信箱ごとに 1/20 程度動きうる）．PYTHONHASHSEED は固定しない（B8 の追認）．

**期待効果**（推測．判定に使わない）：term_sketch の E_body_short は 0.25〜0.40 に下がり，Q（0.292）と区間が重なる（H-R1b 支持）．埋め込みの 3 方式の E_body_short は E_short と同程度か少し上がる（ヘッダーで切り詰めの枠を使わなくなるため）．
特徴語の上位 20 語には `enron`・`com` と持ち主の姓・名が入る受信箱が多い．人名を含む質問（A ∪ B）は半数以上で，flood_score の hit@2 は C より高い．

**実行**（実装の後，push してから 1 回だけ．所要は T2 と同程度の数分の見込み）：`bash scripts/tasks/remote.sh header_eval 20261014_iter2_header_eval 20261009_eq2_advert_eval`．出力は `results/20261014_iter2_header_eval/`（`metrics.json`，`analysis_report.md`）．本文は results/ と git に入れない（p0004 §2.3）．B の一致語は語だけを出す．

**限界**：メール先頭のヘッダーだけを除き，本文の中の引用ヘッダー（`From:`・`To:`）と署名の人名は残る．E_body_short の 28 トークンにも引用の人名が入りうる．
公開情報（Advert）はヘッダー込みのまま作り直さない（持ち主の側がヘッダーを除いて公開情報を作る変形は別のレバー）．R2 の名前の検出は規則による近似で，固有表現の抽出器は使わない．
term_sketch の値は ±0.002 の揺れと退行の許容 2e-3 を持つ（B8）．

**次の候補**：H-R1b が支持なら，公開情報（term_sketch・multi_centroid）をヘッダーを除いた本文から作り直し，EQ2 の hit と所有者の特定を測り直す（term_sketch は文字列だけで作り直せるが，multi_centroid は本文の埋め込みの計算し直しが要る．ラベルは変わらない見込み．要確認）．その後に backlog B11 の centroid_probe の実機化．

### 実行の要約（2026-10-14．`results/20261014_iter2_header_eval/`．数値のみ．判定・解釈は phase 3）

実行は 1 回（`remote.sh header_eval`．イメージの `/etc/atrium-git-head` は `c817f02cbabd-dirty`．dirty は `.claude/` 配下の差分による）．所要 70.7 秒．
n_inboxes 150，n_test 2,940，n_owner_probes 1,460，切り詰め 28 トークン，空の本文 0 通，区切り線なし 0 通，対にした部分集合 1,460 通．

**退行の確認**：T2 は通過（許容 5e-4．term_sketch は 2e-3）．最大の差は term_sketch の E の top-1 が -6.8e-4（許容 2e-3 以内），term_sketch の Q の AUC が 1.9e-4．他の方式は全て 0．R2 の群の重みづけ和は全 test の値と差 0.0（flood_score・multi_centroid・centroid_probe の hit@1・hit@2）で通過．

**持ち主の top-1（1,460 通．[95% 区間]）**

| 方式 | Q（2,940 問） | E_short | E_body_short | E_body | E_subj_short |
|---|---|---|---|---|---|
| card_sim | 0.1116 | 0.0740 [0.060, 0.088] | 0.0342 [0.024, 0.044] | 0.1397 [0.120, 0.156] | 0.1027 [0.088, 0.118] |
| term_sketch | 0.2918 | 0.5432 [0.515, 0.568] | 0.2185 [0.197, 0.238] | 0.3568 [0.331, 0.381] | 0.1952 [0.174, 0.214] |
| centroid_sim | 0.2639 | 0.1651 [0.146, 0.186] | 0.0836 [0.070, 0.098] | 0.2377 [0.216, 0.258] | 0.1301 [0.113, 0.148] |
| multi_centroid | 0.3486 | 0.2110 [0.190, 0.232] | 0.1075 [0.092, 0.123] | 0.4473 [0.422, 0.472] | 0.2445 [0.222, 0.266] |

CLI が出した区分（H-R1b．E_body_short と E_short の比較）：4 方式とも `supported`．E_body_short と Q の H-O1：4 方式とも `lower`．E_short と Q：term_sketch だけ `higher`，他は `lower`．
E_body_short - E_short の top-1 の差：card_sim -0.0397，term_sketch -0.3247．

**H-R1a（特徴語）**：上位 20 語のヘッダー依存の語の割合の平均 0.179（重み付き 0.295），上位 200 語は 0.029（重み付き 0.104）．閾値 0.25 に対する `h_r1a_header_dominated`：false．
上位 20 語の内訳：ドメインの語 0.292，アドレスの名前の語 0.577．上位 200 語：0.201，0.480．持ち主の姓が上位 20 語に入る受信箱：132/150．

**R2（hit@1 / hit@2．群 A 757 問，B 1,880 問，C 303 問）**

| 方式 | A hit@2 | B hit@2 | C hit@2 | A+B hit@2 [95% 区間] | A+B の方が見つけやすい |
|---|---|---|---|---|---|
| flood_score | 0.933 | 0.789 | 0.574 [0.515, 0.630] | 0.830 [0.816, 0.844] | True |
| multi_centroid | 0.647 | 0.368 | 0.370 [0.317, 0.426] | 0.448 [0.430, 0.468] | True |
| centroid_probe（K = 75） | 0.939 | 0.722 | 0.561 [0.502, 0.617] | 0.784 [0.769, 0.799] | True |
| term_sketch（参考） | 0.956 | 0.152 | 0.142 [0.102, 0.182] | 0.383 [0.364, 0.401] | True |

hit@1 は A / B / C：flood_score 0.880 / 0.697 / 0.488，multi_centroid 0.499 / 0.299 / 0.281，centroid_probe 0.890 / 0.637 / 0.498，term_sketch 0.843 / 0.101 / 0.102．
B で一致した語の上位：enron(155)，energy(71)，gas(49)，john(40)，williams(38)，new(37)，mark(36)，houston(34)，power(33)，news(32)，january(31)，scott(27)，david(26)，michael(25)．（enron・energy・gas・new・houston・power・news・january・center・market・live・report・day・june は人名でない語の一致）

### Iteration 2 実行済み（2026-10-14．research-cycle の phase 3．判定：H-R1b 支持）

**変更**：T2 の短い入力を，ヘッダー込みの先頭 28 トークン（E_short）から，区切り線より後の本文の先頭 28 トークン（E_body_short．Subject なし）へ変えた（`atrium header-eval`，実装 `da091ec`，事前登録 `c817f02`，結果 `a0c74ea`）．
公開情報（Advert）・ラベル・config.yaml は変えていない．実行は 1 回だけで，失敗・再実行は無い．

**結果と判定**（事前登録の規則のまま．基準は変えていない）
- 退行の確認：T2 は通過（最大の差は term_sketch の E の top-1 の −6.8e-4 で，許容 2e-3 の中．他の方式は差 0）．R2 の群の重みづけ和も全 test の値と差 0．
- **H-R1b（主判定．term_sketch）：支持**．E_body_short 0.219 [0.197, 0.238] の上端は，E_short 0.543 [0.515, 0.568] の下端より大きく下にある（差 −0.325．区間の半幅 ±0.02〜0.03 の 10 倍以上で，B8 の揺れ ±0.002 とは桁が違う）．
  E_body_short と Q（0.292 [0.276, 0.311]）の比較は `lower`（higher ではない）．T2 の term_sketch の結論「同じ長さでも，メール本文の方が持ち主を当てやすい」は，ヘッダー（持ち主のアドレス）に依存していた．
- 埋め込みの 3 方式（判定なし．記録）：E_body_short は E_short からさらに下がり（card_sim −0.040，centroid_sim −0.082，multi_centroid −0.103），Q との比較は 3 方式とも `lower` のまま．T2 の「E_short の方が低い」は変わらない．
  事前の予想（ヘッダーで枠を使わなくなるので同程度か少し上がる）とは逆で，埋め込みの方式もヘッダーの中の名前・アドレスを持ち主の手がかりにしていた．
- **H-R1a：閾値に届かず**（「特徴語の上位はヘッダーに多くを占められる」とは言わない）．上位 20 語のヘッダー依存の語の割合の平均は 0.179（< 0.25）．ただし語の重みで重みづけると 0.295 で，持ち主の姓が上位 20 語に入る受信箱は 132/150 である．
- **H-R2：規則の上では 4 方式とも「名前を含む問の方が見つけやすい」**（A+B の hit@2 の区間の下端が C の上端より上）．ただし B 群の定義が粗いので，解釈は下の分析のとおり A 群に限って読む．

**分析**
- **T2 と EQ2 の書き直しが要る（事前登録の規則どおり訂正した）**．T2 の term_sketch の E_short 0.543 は，28 トークンの多くを占める `Sender:`・`Recipients:` の持ち主のアドレスが，語のスケッチの持ち主の名前と一致した結果である．件名を足した E_subj_short（0.195）も低いので，件名ではなくアドレスの行が効いている．
  ヘッダーを除くと，4 方式とも「同じ長さなら，持ち主を当てるより見つけてもらう方が高い」で揃う．EQ2 の「公開する情報は，見つけてもらうことより持ち主を当てることの方に役立つ」は，**入力の長さの違いとヘッダーの名前によるもの**であり，方式を問わずそのまま主張しない．
  訂正は `docs/d0006_research_overview.md` §6.3 と，journal の T2・EQ2 の節の注記で行った（既存の値は変えていない）．
- **H-R1a と H-R1b が食い違って見える点**：特徴語の上位 20 語の大半（0.82）は「本文だけでもヘッダー込みの半分以上の文書頻度を持つ語」であり，スケッチの側がヘッダーの語で埋まっているわけではない．
  一方で，持ち主の姓は 132/150 の受信箱で上位 20 語にあり，ヘッダー依存の語は重みの大きい語に偏っている（重みづけ 0.295）．持ち主の名前は署名・引用ヘッダーにより本文にも現れるので，「ヘッダー依存」の定義（本文の文書頻度が半分未満）では数えられない語も多いと考えられる（推測．未計測）．
  効いていたのは**入力の側**（E_short の 28 トークンがアドレスで占められること）であり，スケッチの側の数個の名前の語と合わせて一致が起きた，と読むのが数値と矛盾しない．
- **E_body（本文の全体）が E_short・E_body_short より高い点**：E_body は term_sketch 0.357，multi_centroid 0.447 で，ヘッダーを除いても本文が長ければ持ち主の特定は上がる．これは長さ（情報量）の効果であり，本文の中の引用ヘッダー・署名の人名も含む（限界）．
  T2 の E（ヘッダー込みの全体．term_sketch 0.427，multi_centroid 0.527）より 0.07〜0.08 低く，長い入力でもヘッダーの寄与はあるが，28 トークンのときほど支配的ではない．
  E_body と Q の区間の関係は，term_sketch（[0.331, 0.381] 対 [0.276, 0.311]）と multi_centroid（[0.422, 0.472] 対 [0.332, 0.366]）で E_body が上，centroid_sim と card_sim は重なる．したがって「メール 1 通の全体を手にした人は，質問 1 つで見つける場合より持ち主を当てやすい」は，ヘッダーを除いても term_sketch と multi_centroid では成り立つ（同じ長さの比較ではない）．
  なお `E_body` の埋め込みはモデルの上限（トークン数）で切れるので，埋め込みの方式の E_body は本文の先頭の部分である．
- **R2 の B 群の誤検出の影響**：B の一致語の上位は enron・energy・gas・houston など人名でない語が多く，B は「人名を含む問」ではなく「大文字の固有名詞を含む問」に近い．
  B と C を比べると，multi_centroid（0.368 対 0.370）と term_sketch（0.152 対 0.142）は差が無く，flood_score（0.789 [0.771, 0.806] 対 0.574 [0.515, 0.630]）と centroid_probe（0.722 対 0.561）は B が高い．flood 型の B の有利さは，名前ではなく「具体的な固有名詞があるほど probe で本文に当たりやすい」ことで説明できる（C は 303 問と小さく，区間が広い）．
  **A 群（持ち主の姓を含む．757 問，test の 26%）だけで読むと**：hit@2 は flood_score 0.933 [0.914, 0.950]，centroid_probe 0.939 [0.922, 0.955]，multi_centroid 0.647 [0.613, 0.679]，term_sketch 0.956 [0.942, 0.968] で，どれも C の区間と離れている．
  A 以外（B+C の 2,183 問．群の値を問の数で重みづけた点推定．区間は出していない）の hit@2 は，flood_score 0.760，centroid_probe 0.699，multi_centroid 0.368，**term_sketch 0.151** である．
  全 test の値（0.804，0.761，0.440，0.359）との差は，flood_score −0.044，centroid_probe −0.062，multi_centroid −0.072，term_sketch −0.208 で，**term_sketch の発見（EQ2 の hit@2 0.359）は大半が持ち主の姓の一致による**．
  flood_score の高さ（0.804）のうち持ち主の姓で説明できるのは 0.04 程度で，残りは名前以外でも成り立つ．Iteration 1 の centroid_probe と flood_score の差（0.043）は，A 群ではほぼ 0（0.939 対 0.933）で，A 以外の問に集まっている（0.699 対 0.760）．
- **B8 の揺れ**：term_sketch の値は採点の文字列のハッシュの種で Q ±0.001，E ±0.002 揺れる．本イテレーションの term_sketch の差（−0.325）や R2 の群の差に比べて十分に小さく，判定には影響しない．限界として残す．
- 想定外の挙動（失敗・発散・メモリ不足）は無い．所要 70.7 秒．

**学び**
- EnronQA のメールの文字列はヘッダー（`Sender:`・`Recipients:` のアドレス）を含んだまま埋め込み・公開情報に使われているので，「メールから持ち主を当てる」評価は，ヘッダーを除かない限り名前の照合になる．長さを揃えるだけでは足りなかった（T2 の限界に「ヘッダが多く含まれるかは確かめていない」と書いたが，それが主因だった）．
- 質問文の側にも持ち主の姓が 26% の問に入っており（EnronQA の質問は特定のメールを指すように作られている），公開情報に持ち主の名前が入る方式（term_sketch）の発見はそれに強く依存する．hit の比較を報告するときは，A 群（持ち主の姓を含む）とそれ以外を分けた値を添える．
- 規則による名前の検出は，`enron` のような組織名・地名・月名を大量に拾う．人名かどうかを問う集計には，持ち主の姓のような狭い定義（A 群）だけを使う方が解釈しやすい．

**限界**
- メール先頭のヘッダーだけを除き，本文の中の引用ヘッダー（`From:`・`To:`）と署名の人名は残る．E_body_short・E_body の値にも名前の一致が残りうる．
- 公開情報（Advert）はヘッダー込みのまま作り直していない．公開情報からヘッダーを除いたとき，EQ2 の hit と所有者の特定がどう変わるかは未測定である（次の一手）．
- R2 の名前の検出は規則による近似で，B 群は人名以外を多く含む．A 以外の値は群の重みづけの点推定で，区間は出していない．
- term_sketch の値は ±0.002 の揺れと退行の許容 2e-3 を持つ（B8）．

**未確認のまま残す点（外部レビュー R4．新しい実験はしない．backlog B10 の [human-decided 2026-10-14] に従う）**：(a) EQ3 と T4 の `ReadError` の食い違いの原因，(b) parallel 1 で multi_centroid が flood_score より遅い理由，(c) ragroute-frozen が one-hot 0 の新参者を hit@2 0.118 で選ぶ理由．
3 点とも Iteration 1 の節に書いたとおり未確認で，`docs/d0006_research_overview.md` の限界（§6.3）にも「未確認」として書いた．

**次の一手**（backlog B14 [auto-decided]）：Iteration 3 は，**公開情報（term_sketch・card_sim・centroid_sim・multi_centroid）をヘッダーを除いた本文から作り直し，EQ2 の hit と所有者の特定（E と E_body_short）を測り直す**．centroid_probe の実機化はその後に回す．

## Iteration 1: 公開情報で候補を絞ってから probe する二段ルーティング

### 調査・計画（2026-10-14．research-cycle の phase 1）

**レバーの状況**：config.yml の levers（E1-a〜E1-e，E2 card_sim，EQ8 k_context）は全て実施済みである（journal の「E1 の判定」「s8」「E2：LOSO の判定」「E2 の下流確認」「EQ8〜EQ10」）．
p0005（T1〜T4）も完了した．そこで関連研究を調べ，新しいレバーを 1 つ定義して config.yml の levers 末尾に追記した（backlog B9 [auto-decided]）．
B8（term_sketch の退行の許容）はレビュー待ちなので，term_sketch に依存する設計は避けた．

**これまでの結果から見える課題**（EQ2・T3・T4）
- 公開情報なしで全員に聞く flood_score は hit@2 0.804 だが，1 問が全 150 受信箱（30 台）に届き，parallel 30 では処理量が 12.8 → 2.5 問/秒に落ちる（T4）．
- 公開情報を使う最良の multi_centroid は 2 台だけに聞くが，hit@2 は 0.440 にとどまる．ただし hit@3 0.489，MRR 0.447 で，正解の受信箱は上位の近くに来ていることが多い（`results/20261009_eq2_advert_eval/metrics.json`）．
- この 2 つの間（問い合わせる受信箱の数 K を 2〜150 の間で選ぶ）は，まだ測っていない．

**調査で分かったこと**（tavily-search．事実と推測を分けて書く）
- 分散検索の資源選択では，安い要約（語の統計・標本の索引）でシャードを順位づけてから，上位の一部だけを検索する「selective search」が標準的な構成である．
  ReDDE（Si & Callan, SIGIR 2003）は中央の標本索引で関連文書の分布を推定し，Rank-S（Kulkarni et al., CIKM 2012．https://www.cs.cmu.edu/~anaghak/cikm2012.pdf）は検索するシャード数の打ち切りも推定する．
  Kulkarni et al. は，提案法が ReDDE と同等の効果で 1 問あたりに処理する文書数を減らしたと報告している（検索結果の断片では「Rank-S は Gov2 で検索コストを 27% 減らす」．比較の基準は本文で確認していない）．
- Taily（Aly et al., SIGIR 2013．https://ugentt2k.github.io/papers/2013/aly2013sigir.pdf）はシャードごとのスコア分布の裾の統計だけで選ぶ．公開する要約から候補を選ぶ点で，本研究の公開情報の方式に近い．
- 検索するシャード数を問ごとに予測する研究がある（Mohammad et al., "Dynamic Shard Cutoff Prediction for Selective Search", SIGIR 2018．https://espace.library.uq.edu.au/view/UQ:1b7a48c）．
  打ち切りの数 K が効果とコストの兼ね合いを決める主要な変数であるという扱いは，これらの研究で共通している．
- 負荷のある分散 selective search では，問の待ち時間の多くが中央と各ノードの待ち行列で生じ，ネットワークと統合のコストは小さい（Kim et al., "Efficient distributed selective search", Information Retrieval Journal 2016．https://www.cs.cmu.edu/~callan/Papers/IRJ16-yubink.pdf）．T4 の flood_score の処理量の低下と同じ向きの観察である．
- 連合 RAG の側では，DFAMS（Yang et al., arXiv:2508.20353, 2025）がデータ源ごとに複数の prototype を学習して振り分ける．multi_centroid と同じく「データ源を複数の代表点で表す」発想だが，注釈つきの質問での学習が要る．
  RAGRoute（EuroMLSys 2025）は中央の分類器で絞る．どちらも，公開要約で絞った候補にだけ実際の検索スコアを問い合わせる二段の構成は評価していない（2 本の抄録・序論を読んだ範囲．網羅的な確認ではない）．
- 未確認：EnronQA のような「1 人 1 受信箱」の私的データで，二段構成の K と hit の関係を報告した研究は見つからなかった．

**仮説**：multi_centroid の外れの多くは，正解の受信箱が上位 K（K ≪ 150）の中にはあるが 1〜2 位に来ないものである．
候補を multi_centroid の上位 K 個に絞り，その K 個にだけ flood_score と同じ probe（各受信箱のメールとの内積の最大）を送って上位 m = 2 個を選べば，
flood_score に近い hit@2 を，クエリを受け取る受信箱を 150 から K に減らして得られる．

**単一レバー**：`experiment.routing` を，新しい方式 `centroid_probe`（multi_centroid の上位 K 個 → その K 個への probe → 上位 m 個）にする．
- 新しい Router を `src/atrium/routing/advert.py` に 1 個追加し，`make_router()` に登録する（config.yml の運用ルール 1）．
- K は方式の大きさであり，新しい設定キー `routing.probe_k.enronqa` で持つ．EQ2 の公開情報の大きさと同じく，dev で事前登録の規則で選ぶ（下）．
- 評価は実機を使わないオフライン（EQ2・T3 と同じ制御点のコンテナ）で行う新しい CLI（例：`atrium cascade-eval`）．既存の `advert-eval` の出力（`results/20261009_eq2_advert_eval/`）は作り直さない．

**固定する構成**：`dataset: enronqa`，m = 2（`routing.top_m.enronqa`），multi_centroid の C = 32（`data.enronqa.n_centroids`），probe は flood_score の密ベクトル（arctic-embed-m-v1.5），
EQ2 と同じ dev・test の質問集合（test 2,940 問）と所有者推定のメール．データ準備・ラベル・シャードは作り直さない．

**K の選び方（事前登録．実装の後，評価の前に journal に書いて push する）**：候補 K ∈ {4, 8, 15, 30, 50, 75}．dev の hit@2 が，dev の flood_score の hit@2 の 0.95 倍以上になる最小の K を選ぶ．
どの K も届かなければ K = 75 とし，その旨を記録する．test の全 K の曲線は参考として出す（判定には選んだ K だけを使う）．

**退行の確認**（一致しなければ判定に進まない）：K = 150 で test の hit@2 が flood_score の 0.8041 と，K = 2 で multi_centroid の 0.4401 と，5e-4 以内で一致すること．

**期待効果**：test の hit@2 が 0.70〜0.78 程度（推測．multi_centroid の上位 K の再現率が上限になる），クエリを受け取る受信箱が 1 問あたり K 個．

**成功条件**（test 2,940 問．95% ブートストラップ信頼区間．EQ2 の hit@2 の区間の半幅は約 ±0.015 で，オフラインの評価は流し直しの揺れが無い）
- **S1**：選んだ K での centroid_probe の hit@2 が，multi_centroid（0.440）より 0.10 以上高く，区間が重ならない．
- **S2**：選んだ K での hit@2 が，flood_score（0.804）との差 0.05 以内（0.754 以上）で，かつ K ≤ 30（受信箱の 20% 以下）．
- S1 と S2 の両方で「採用」，S1 だけで「部分的に支持（K と hit の兼ね合いを記録）」，S1 が成り立たなければ「棄却」．
- 参考（判定に使わない）：T3 の新参者 600 問での hit@2（学習が要らないので新参者でも同じ値になるはず），所有者の特定は multi_centroid と同じ公開情報なので変わらない．

**限界と次の候補**：オフラインの評価は待ち時間を測らない．採用なら，次のイテレーションで T4 と同じ条件（300 問，parallel 1〜30）で実機の待ち時間・処理量を flood_score・multi_centroid と比べる．
1 段目に term_sketch（公開情報 0.87 MB）を使う変形は，B8 の結論が出るまで扱わない．

### 事前登録（2026-10-14．実行の前に push する．実装は commit `0d019f9`）

調査・計画の節の規則を，実行の前にそのまま固定する（結果を見て条件・基準を変えない）．

- **評価**：`atrium cascade-eval`（`src/atrium/cascade_eval.py`）を制御点のコンテナで 1 回だけ流す．`bash scripts/tasks/remote.sh cascade_eval 20261014_iter1_cascade_eval 20261009_eq2_advert_eval`．
  `dataset: enronqa`，m = 2，C = 32，probe は flood_score の密ベクトル，dev・test は EQ2 と同じ．
- **K の選び方**：候補 K ∈ {4, 8, 15, 30, 50, 75}．dev の hit@2 が dev の flood_score の hit@2 の 0.95 倍以上になる最小の K．届かなければ K = 75 とし，その旨を記録する．
  `config.yaml` の `routing.probe_k.enronqa` は評価前は 75（届かない場合の値の仮置き）で，評価の入力ではない．評価の後に選んだ K へ書き換える．
- **退行の確認**：test の hit@2 が K = 150 で 0.8041（flood_score），K = 2 で 0.4401（multi_centroid）に 5e-4 以内で一致する（基準は EQ2 の metrics.json の値）．一致しなければ判定に進まず，原因を調べて記録する．
- **成功条件**：S1（選んだ K の test の hit@2 が multi_centroid より 0.10 以上高く，95% ブートストラップ区間が重ならない），
  S2（flood_score との差が 0.05 以内で K ≤ 30）．両方で採用，S1 だけで部分的に支持，S1 が不成立なら棄却．
- **出力**：全 K の test の hit@1・hit@2 と区間，1 問あたりにクエリを受け取る受信箱の数（= K），T3 の新参者 600 問（30 受信箱）での参考値（判定には使わない）．
- 前提：実機の質問者の 2 段目（候補だけへの probe）は未実装なので，評価はオフラインだけである．B8 は未解決なので term_sketch は使わない．

### 実行の要約（2026-10-14．`results/20261014_iter1_cascade_eval/`．数値のみ．判定・解釈は phase 3）

事前登録のとおり，イメージ（commit `5c842e2`．作業ツリーに追跡外の state.json があり `-dirty` が付く）で `cascade_eval` を制御点のコンテナで 1 回だけ流した．失敗・再実行は無い．
- **退行の確認**：一致．K = 150 の test の hit@2 0.8041（EQ2 0.8041，差 +0.00000），K = 2 の hit@2 0.4401（EQ2 0.4401，差 +0.00000）．
- **K の選択**：dev の flood_score の hit@2 0.7988（0.95 倍 = 0.7589）．dev の hit@2：K = 4: 0.5078，8: 0.5706，15: 0.6296，30: 0.6906，50: 0.7286，75: 0.7649．75 で初めて目標に届き，選んだ K = 75（届かなかった場合の既定値と同じ）．
- **test の曲線**（2,940 問．hit@1 [95%CI]，hit@2 [95%CI]）：multi_centroid（K = 2）0.349，0.440 [0.422, 0.460] ／ K = 4：0.481，0.506 [0.488, 0.523] ／ K = 8：0.530，0.568 [0.551, 0.586] ／
  K = 15：0.572，0.622 [0.604, 0.640] ／ K = 30：0.626，0.690 [0.674, 0.707] ／ K = 50：0.663，0.732 [0.717, 0.748] ／ K = 75：0.688 [0.672, 0.705]，0.761 [0.746, 0.777] ／ flood_score（K = 150）0.722，0.804 [0.790, 0.818]．1 問あたりにクエリを受け取る受信箱の数は K．
- **成功条件**（K = 75）：S1 成立（multi_centroid との差 +0.3211，区間は重ならない）．S2 不成立（flood_score との差 0.0429 は 0.05 以内だが，K = 75 > 30）．規則上の結論は「部分的に支持」．
- **T3 の新参者 600 問（参考）**の hit@2：flood_score 0.802 [0.768, 0.832]，multi_centroid 0.442 [0.403, 0.482]，centroid_probe（K = 75）0.747 [0.712, 0.778]．
- 未実装・未確認：実機の質問者の 2 段目（候補だけへの probe）．待ち時間・処理量は測っていない．`routing.probe_k.enronqa` は 75 のまま（選んだ K と同じなので config.yaml の変更は無い）．

### Iteration 1 実行済み（2026-10-14．research-cycle の phase 3．判定：部分的に支持）

**変更**：`experiment.routing: centroid_probe`（multi_centroid の上位 K 個 → その K 個だけに flood_score と同じ probe → 上位 m = 2 個）を追加し（`0d019f9`），
オフラインの `atrium cascade-eval` を事前登録（`5c842e2`）どおり 1 回流した（結果 `8db670e`）．K は dev の規則で 75．

**結果と判定**（事前登録の規則のまま．基準は変えていない）
- 退行の確認は一致（K = 150 で 0.8041，K = 2 で 0.4401．差 0）．
- **S1：成立**．K = 75 の test の hit@2 0.761 [0.746, 0.777] は multi_centroid 0.440 [0.422, 0.460] より +0.321 高く，区間は大きく離れている．ノイズの範囲（区間の半幅 ±0.015〜0.018）を一桁上回る．
- **S2：不成立**．flood_score 0.804 との差 0.043 は 0.05 以内だが，K = 75 は条件 K ≤ 30（受信箱の 20% 以下）を満たさない．K = 30 の hit@2 は 0.690 で，flood_score との差 0.114 は 0.05 を超える．
  したがって，S2 の 2 つの条件（差 0.05 以内，K ≤ 30）を同時に満たす K は候補の中に無い．
- **判定：部分的に支持**（S1 だけ成立）．`experiment.routing` の既定は変えない（`routing.probe_k.enronqa: 75` は記録用の値として残す）．

**分析**（ノイズと信号の切り分け．オフラインの評価に流し直しの揺れは無く，揺れは質問の標本による区間だけである）
- K = 75 でも flood_score との差 0.043 は信号である．区間 [0.746, 0.777] と [0.790, 0.818] は重ならない．K = 75 の centroid_probe は flood_score の hit@2 の 95% にとどまる．
- K の選択は境界ぎりぎりであった．dev の K = 75 の hit@2 0.7649 は目標 0.7589 を 0.006 だけ上回り，この差は dev の区間の半幅（約 ±0.015）より小さい．dev の標本が違えば「どの K も届かない（既定の 75）」になりえたが，その場合も K = 75 なので判定は変わらない．
- **K と hit の兼ね合い**：test の hit@2 は K = 2・4・8・15・30・75 で 0.440・0.506・0.568・0.622・0.690・0.761 であり，K を約 2 倍にするごとに +0.05〜0.07 増える（log K にほぼ比例）．小さい K で頭打ちになる「膝」は見えない．
  flood_score と multi_centroid の差 0.364 のうち，K = 30 で 69%（+0.250），K = 75 で 88%（+0.321）を埋める．
- 仮説「multi_centroid の外れの多くは，正解が上位 K（K ≪ 150）にあるが 1〜2 位に来ないもの」は，K = 4 で hit@1 が 0.349 → 0.481（+0.13）に上がる点では支持される（上位の数個の並べ替えだけで多くを取り戻す）．
  一方，K = 30 でも差の 31% が残るので，正解が multi_centroid の 31 位以下にある外れも少なくない．1 段目（重心 32 個の公開情報）の上位 K の再現率が上限であり，2 段目の probe ではそれを越えられない．上位 K の再現率そのものは出力していない（未測定）．
- 期待効果（hit@2 0.70〜0.78）は K = 75 で一致し，K ≤ 30 では 0.690 で下に外れた．
- 新参者 600 問の hit@2 0.747 [0.712, 0.778] は全体の 0.761 を区間に含み，学習が要らない方式なので新参者でも下がらないという予想と矛盾しない（参考．判定には使わない）．
- 想定外の挙動（発散・OOM・失敗）は無い．評価は 10 秒で終わった．

**学び**
- 公開情報（multi_centroid）で候補を絞る二段の方式は，問い合わせる受信箱の数を減らすほど hit@2 がほぼ log K に比例して下がる．「受信箱の 20% で flood_score の差 0.05 以内」は，今の 1 段目では成り立たない．
  K ≤ 30 で S2 を満たすには，1 段目の上位 K の再現率を上げる（公開情報を変える）必要がある．
- 受信箱の数 K と，クエリが届く**デバイス**の数は別物である．30 台が 5 受信箱ずつ持つので，候補の受信箱がデバイスに偏らないと仮定すると，K = 75 でもほぼ全台（約 29 台．各台に 1 つも当たらない確率 ≈ 0.5^5）に届き，K = 30 でも約 20 台（0.8^5 ≈ 0.33）に届く（推定．未測定）．
  T4 の flood_score の処理量の低下が「1 問が届くデバイスの数」によるなら，K = 75 では処理量の改善は小さい可能性がある．実機化するときは，1 問あたりのデバイスの数と各デバイスに届く受信箱の数を両方記録する．
- 判定の規則の S2 は「hit の差」と「K の上限」を AND にしたので，K の上限を外れると hit が近くても不成立になる．K の上限が厳しすぎたかどうかは，実機の待ち時間を測るまで言えない（基準は事後に変えない）．

**限界**
- オフラインの評価だけである．実機の質問者の 2 段目（候補だけへの probe）は未実装で，待ち時間・処理量・届くデバイスの数は測っていない．「flood_score より負荷が軽い」はまだ主張できない．
- 1 段目は multi_centroid（C = 32）だけで，term_sketch など他の公開情報との組み合わせは試していない．m = 2 に固定した．
- 外部レビューの R1・R2（backlog B10）が示すとおり，EnronQA の質問文とメールのヘッダーに含まれる人名が flood_score の高さの一部を説明する可能性がある．centroid_probe の 2 段目は flood_score と同じ probe なので，同じ影響を受ける（未確認．Iteration 2 で R2 として集計する）．

**未確認のまま残す点（外部レビュー R4．新しい実験はしない．backlog B10 の [human-decided 2026-10-14] に従う）**
- (a) EQ3（2,940 問・parallel 30．p50 92 秒・`ReadError` 175 件）と T4（300 問・parallel 30．p95 19 秒・失敗 0）の食い違いが，規模の違いによるものか，長い実行で蓄積する問題（接続の枯渇など）によるものかは区別できていない．運用の頑健性は主張しない．
- (b) parallel 1 で multi_centroid（e2e p50 0.120 秒）が flood_score（0.076 秒）より遅い理由は，質問者の側の重心の採点が律速という推測にとどまり，未確認である．
- (c) ragroute-frozen が新参者の one-hot を 0 にしても hit@2 0.118 で新参者を選ぶ理由は，ベクトルが 0 のデータ源が「平均的なデータ源」として採点されるためという解釈にとどまり，未確認である．

**次の一手**：backlog B10 の [human-decided 2026-10-14] に従い，Iteration 2 の単一レバーは R1（T2 の入力がヘッダーだけになっていないかの確認），R2 を同じイテレーションの追加の集計とする（backlog B11）．
centroid_probe の実機化（質問者の 2 段目の実装と，T4 と同じ条件での待ち時間・処理量・届くデバイスの数）は，その次の候補として backlog B11 に残す．

