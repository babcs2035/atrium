# atrium 研究ジャーナル

研究サイクル（research-cycle）の各イテレーションの記録．新しいイテレーションを先頭に追記する（逆時系列）．
末尾の「準備段階の記録」は，サイクルを始める前に行った環境構築・データ準備・実機での検証の要約である．

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

## p0005 の完了のまとめ（2026-10-14．T1〜T4．各節は下にある）

| 仮説・項目 | 判定 | 要点 |
|---|---|---|
| T1 判定の補正 | 検算が一致 | 狭い補正：B2 0.297 → 0.261，B3 0.260 → 0.215．EQ8 の「差はほぼ消える」は誤った広い補正に基づくので訂正（EQ8 の判定は不変） |
| T2 H-O1（持ち主推定の入力を揃える） | 方式ごと．検算は term_sketch だけ揺れの範囲で不一致（B8） | 埋め込みの 3 方式は，同じ長さ（28 トークン）なら持ち主の特定が質問文からの発見より低い．term_sketch は同じ長さでも高い．EQ2 の「持ち主を当てることの方に役立つ」は方式ごとに書き直した．**Iteration 2 で訂正**：term_sketch の高さはヘッダーによるもので，ヘッダーを除くと 4 方式とも Q より低い |
| T3 H-N1 | 支持 | 再学習しない素朴な中央方式の hit@2 0.118，全受信箱で学習した場合 0.282（区間が重ならない） |
| T3 H-N2 | 支持（2 つとも） | flood_score 0.802，multi_centroid 0.442 は，新参者でも ragroute-frozen を大きく上回る |
| T4 H-L1 | 支持 | parallel 30 の e2e p95：flood_score 19.1 秒，multi_centroid 4.95 秒（3 回の幅が重ならない） |
| T4 H-L2 | 支持 | p50 の p = 1 → 30 の倍率：flood_score 149 倍，multi_centroid 29 倍 |
| T4 H-L3 | 支持されない（定義が両方式を同じ値にした．下の T4 の節） | 処理量は flood_score が parallel とともに減り（12.8 → 2.5 問/秒），multi_centroid は一定（約 8.4 問/秒） |

事前登録した退行の確認・基準は変えていない．計画からの差は，T1 の CPU の受信箱の決め方（`bank`），T2 の term_sketch の退行の許容（B8）の 2 つで，どちらも該当の節に書いた．

## T4：負荷試験（確定．2026-10-14 18:24．`results/20261014_t4_*`，集計は `results/20261014_t4_load_summary/`）

事前登録（下の「T4 の事前登録」）のとおり，30 実行（flood_score と multi_centroid × parallel 1・4・8・16・30 × 3 回）を `t4_chain.sh` で連続実行した（15:55〜18:24．1 回あたり約 5 分，deploy を含む）．
**失敗は全 30 実行で 0**（失敗率が 10% を超えた実行は無く，打ち切りの規則は動かなかった）．結果を見て条件・基準は変えていない．イメージは `ffe0880`．

**結果**（3 回の中央値．300 問．e2e は成功した問，単位は秒．処理量は問／秒）

| routing | parallel | e2e p50 | e2e p95 | probe p95 | 処理量 |
|---|---|---|---|---|---|
| flood_score | 1 | 0.076 | 0.085 | 0.071 | 12.83 |
| flood_score | 4 | 0.598 | 0.802 | 0.504 | 6.48 |
| flood_score | 8 | 1.53 | 2.50 | 1.48 | 4.72 |
| flood_score | 16 | 5.11 | 8.10 | 4.87 | 2.95 |
| flood_score | 30 | 11.4 | 19.1 | 13.5 | 2.50 |
| multi_centroid | 1 | 0.120 | 0.123 | － | 8.30 |
| multi_centroid | 4 | 0.470 | 0.591 | － | 8.49 |
| multi_centroid | 8 | 0.938 | 1.30 | － | 8.45 |
| multi_centroid | 16 | 1.80 | 2.58 | － | 8.43 |
| multi_centroid | 30 | 3.49 | 4.95 | － | 8.39 |

1 問あたりにクエリを受け取ったデバイスは，flood_score が 30 台（全台），multi_centroid が 1.96 台．

**判定**（EQ6・EQ10 と同じ規則：3 回の幅が重ならないときだけ差がある）
- **H-L1：支持**．parallel 30 の e2e p95 は，flood_score が [18.9, 20.6]，multi_centroid が [4.93, 5.02] で，重ならない（約 4 倍）．
- **H-L2：支持**．e2e p50 の p = 1 → 30 の倍率は，flood_score が 149 倍，multi_centroid が 29 倍．
- **H-L3：支持されない**．定義（処理量が前の段より 10% 以上増えなくなる最小の parallel）では，両方式とも 4 になった（flood_score は 12.83 → 6.48 と減り，multi_centroid は 8.30 → 8.49 と +2%）．
  定義が「増えなくなる」だけを見て，flood_score のように**減る**場合と，multi_centroid のように最初から頭打ちの場合を区別しないため，差が出ない．事前登録の定義どおりに判定し，下に実測の形を書く．

**読み取れること**
- multi_centroid の処理量は parallel 1 から 30 までほぼ一定（約 8.4 問/秒）で，e2e p50 は parallel にほぼ比例して延びる（30 ÷ 8.4 ≈ 3.6 秒．同時に流した問の待ち行列）．質問者の側（クエリ埋め込みのスコアの計算など）が律速で，専門家は余裕がある．
- flood_score の処理量は，parallel が上がるほど**減る**（12.8 → 2.5 問/秒．約 1/5）．1 問が 30 台へ probe を送るので，parallel 30 では 900 件の要求が同時に飛び，専門家の CPU か質問者の接続が競合して，全体が遅くなると考えられる（原因は未確認）．
  （2026-10-15 追記．Iteration 4 の分析）ノードの計算は probe の時間の 0.1% 未満で，専門家の CPU ではない．待ちは質問者の側（HTTP クライアントの要求の扱いと接続の池）にあると考えられる．また，parallel 16 以上では接続のエラーが 1 実行で 100 回以上起き，`post_with_retry` の再試行が吸収していた（下の「`ReadError` は出なかった」は再試行を使い切った問が無かったという意味）．
  parallel 1 では flood_score の方が速い（12.8 対 8.3 問/秒）が，parallel 4 で逆転する．
- 事前登録の予想（parallel が上がると p95 が急に伸び，`ReadError` が出る）のうち，p95 の伸びは確認できたが，**`ReadError` は出なかった**．EQ3 の 175 問の `ReadError` は，300 問ではなく 2,940 問を parallel 30 で続けて流したときに起きたもので，この規模（300 問）では再現しなかった．
- parallel 1 の e2e は EQ7b のゲート（0.089 秒．2,940 問）と同じ程度（0.076 秒．300 問）で，条件の違い（質問集合）の範囲に収まる．
- 限界：有線 LAN・300 問・検索だけ．回答の生成を含む待ち時間は EQ6・EQ10 にある．処理量は `run_meta.json` の開始・終了から求めた（質問ごとの壁時計時間は記録していない）．

## T4 の事前登録（2026-10-14．実行の前に push する．p0005 §6）

flood_score は公開情報なしで最も見つけてもらいやすい（EQ2 の hit@1 0.722）が，1 問を全 30 台へ問い合わせる．1 問ずつ流すと e2e の中央値は 0.089 秒（EQ7b のゲート），30 問を同時に流すと 92.2 秒（EQ3）だった．
同時に流す質問の数に対して，待ち時間と処理量がどう変わるかを，公開情報を使う方式（multi_centroid）と比べて測る．

**条件（固定）**：`dataset: enronqa`，`answer_mode: retrieval_only`，`question_limit: 2`（受信箱ごとの先頭 2 問．300 問．EQ6・EQ10 と同じ集合），m = 2．
`routing` は flood_score と multi_centroid，`experiment.parallel` は 1・4・8・16・30，各条件 3 回の 30 実行（`results/20261014_t4_load_summary/t4_specs.txt`）．
run_id は `20261014_t4_<routing>_p<parallel>_r<1|2|3>`．各回の前に deploy し直す（`t4_chain.sh`．eq8_chain.sh と同じ形．deploy の再試行は 5 分おきに 3 回まで）．
失敗した問は流し直さない（待ち時間の測定．EQ10 と同じ）．連続実行の間，制御点で他のコンテナ（T2・T3）を動かさず，commit もしない．

**指標**：e2e_s の p50・p95（成功した問），probe_s（flood_score）の p50・p95，処理量（成功した問の数 ÷ `run_meta.json` の finished_at − started_at），失敗率とエラーの種類，1 問あたりにクエリを受け取ったデバイスの数．
質問ごとの壁時計時間は記録に無いので，処理量は `run_meta.json` の開始・終了から求める（質問の処理の開始から終了まで）．

**判定**（EQ6・EQ10 と同じ規則：比べる 2 条件の 3 回の幅が重ならないときだけ「差がある」）
- **H-L1**：parallel = 30 で，flood_score の e2e p95 が multi_centroid より大きい（3 回の幅が重ならない）．
- **H-L2**：parallel を 1 から 30 に上げたときの e2e p50 の増加の倍率（3 回の中央値どうしの比）が，flood_score の方が multi_centroid より大きい．
- **H-L3**：処理量が前の段より 10% 以上増えなくなる最小の parallel（無ければ「30 より上」）が，flood_score の方が小さい．
- 失敗率が 1% を超えた実行があっても止まらない（負荷による破綻は結果である）．ただし，同じ routing・parallel の 3 回とも失敗率が 10% を超えたら，その routing のより大きい parallel は飛ばして記録する（p0004 §12 の 4 の代わり．この実験に限る）．

**予想**（判定に使わない）：flood_score は 1 問ごとに 30 台へ probe を送るので，parallel が上がると専門家と質問者の接続の待ち行列で p95 が急に伸び，EQ3 と同じ `ReadError` が出る．
**限界**：有線 LAN・1 種類の質問集合．検索だけで，回答の生成は含まない（回答の生成を含む待ち時間は EQ6・EQ10）．parallel 1 の e2e は EQ7b のゲートと同じ条件のはずだが，質問集合（300 問）が違う．

**集計**：`results/20261014_t4_load_summary/t4_summary.py` → `t4_summary.out`．

## T3：150 人での新参者の評価（確定．2026-10-14 15:54．`results/20261014_t3_newcomer`）

事前登録（下の「T3 の事前登録」）のとおり，制御点のコンテナで 1 回だけ実行した（所要 1.6 時間．イメージは `b89d289`，`src` は実装の commit `d00d350` と同じ）．結果を見て条件・基準は変えていない．
ragroute-frozen の学習は 1 個あたり平均 169 秒（30 個の合計 1.4 時間）で，所要時間の上限（4 時間）に当たらず，新参者は 30 個すべてを使った．再学習したルーターは制御点の `atrium-data/enronqa/newcomer/` にあり，`results/` には入れていない．

**退行の確認：通過**．ragroute-upper と学習の要らない方式の全 test の hit@1・hit@2 が EQ2 と一致した．card_sim・centroid_sim・multi_centroid・flood_score・ragroute は差 0，term_sketch だけ hit@1 −0.0007・hit@2 −0.0010 で，事前登録した許容（2e-3）の中だった（採点の揺れ．backlog B8）．

新参者（名前の昇順に並べた 150 受信箱から `default_rng(12)` で非復元抽出）：taylor-m, king-j, lewis-a, baughman-d, lokay-m, gilbertsmith-d, mccarty-d, cash-m, holst-k, campbell-l, stclair-c, hain-m, geaccone-t, haedicke-m, kitchen-l, ring-a, benson-r, richey-c, shively-h, badeer-r, salisbury-h, dickson-s, perlingiere-d, cuilla-m, hyvl-d, arnold-j, white-s, maggi-m, harris-s, lay-k．

**結果**（新参者の問 600 問を合わせた pooled．m = 2）

| 方式 | hit@1 | hit@2 [95%CI] |
|---|---|---|
| flood_score | 0.705 | 0.802 [0.768, 0.832] |
| multi_centroid | 0.363 | 0.442 [0.403, 0.482] |
| term_sketch | 0.310 | 0.377 [0.338, 0.415] |
| centroid_sim | 0.263 | 0.328 [0.293, 0.365] |
| ragroute-upper（150 受信箱で学習済み） | 0.233 | 0.282 [0.248, 0.317] |
| card_sim | 0.118 | 0.155 [0.127, 0.185] |
| **ragroute-frozen**（新参者を除いて学習し直した） | **0.070** | **0.118 [0.092, 0.143]** |
| random | 0.010 | 0.017 [0.007, 0.027] |

**判定**
- **H-N1：支持**．ragroute-frozen（0.118）は ragroute-upper（0.282）より低く，区間が重ならない．再学習しない素朴な中央方式は，新参者を見つけにくい．新参者ごとでは，30 個のうち 22 個で frozen が upper より低い（平均 0.118 対 0.282）．
- **H-N2：支持**（2 つとも）．flood_score（0.802）と，公開情報を使う最良の方式 multi_centroid（0.442）は，どちらも ragroute-frozen より高く，区間が重ならない．
  学習の要らない方式は，4 方式のうち card_sim（0.155）以外が，新参者の発見で ragroute-frozen の 2 倍以上である．card_sim（0.155）も frozen を上回る（区間は [0.127, 0.185] と [0.092, 0.143] で重なる）．
- 参考：新参者ではない受信箱の質問での ragroute-frozen の hit@2 は 0.284（n = 87,600）で，全受信箱で学習した ragroute-upper の全体の値（0.279．EQ2）と同じである．
  再学習なしの素朴な中央方式が失うのは，新参者の分（0.284 → 0.118）に限られる．
- 公開情報を使う方式と flood_score は，新参者でも他の受信箱と同じ値（flood_score 0.802，multi_centroid 0.442，全 test では 0.804・0.440）で，新参者であることによる低下が無い．

**書き方の制約（D2）**：ragroute は，関連の無い受信箱を 1 問あたり 10 個だけ抜き出して学習した素朴な中央方式であり，この結果は「中央方式一般」を代表しない．
**限界**：公開情報の大きさ（件名 40，T = 200，C = 32）と m = 2 は，新参者を含む dev で選んだ値である．新参者の評価は 150 受信箱の 1 回の抽出（30 個）で，抽出を変えた繰り返しはしていない．

## T3 の事前登録（2026-10-14．実行の前に push する．p0005 §5）

150 人の受信箱で，学習のときにいなかった受信箱（新参者）を再学習なしで見つけられるかを測る．コードは `atrium newcomer-eval`（`src/atrium/newcomer_eval.py`．実装の commit は下の「実行」に書く）に固定し，結果を見て条件・基準を変えない．実行は 1 回だけ．

**問い**：公開情報を使う方式と flood_score は学習が要らないので，新参者でも挙動は同じである．比べる中心は，**素朴な中央方式（ragroute．D2）の再学習の有無**である．

**設計（固定）**
- 新参者：150 受信箱を名前の昇順に並べ，`numpy.random.default_rng(12)` で 30 個を非復元抽出する．選ばれた 30 個は結果の `metrics.json`（`newcomers_sampled`）に記録する．
- 各新参者 n：ragroute-upper（既存のルーター．150 受信箱で学習済み），ragroute-frozen（n を除いた 149 受信箱で学習し直したルーター．`train_router(exclude=[n])`．負例の数・種・ハイパーパラメータは既存と同じ．判定のとき n の one-hot を 0 にする），
  学習の要らない方式（card_sim・term_sketch・centroid_sim・multi_centroid は EQ2 で選んだ大きさ，flood_score は密ベクトル，random）．
- 評価する質問：test のうち正解の受信箱が n の問（受信箱あたり最大 20 問．最大 600 問）を，新参者を合わせて集計する（pooled）．m = 2（EQ2）．
- 再学習したルーターは制御点の `/home/denjo/atrium-data/enronqa/newcomer/` に置き（既存の `router/` とは別），`results/` には評価の値だけを書く．
- **所要時間の上限**：最初の 1 個の学習の時間 × 新参者の数 が 4 時間を超えるなら，新参者を先頭の半数（15 個．種と並びは同じ）に減らす．

**退行の確認**（一致しなければ判定に進まず，原因を調べて記録する）：全 test の hit@1・hit@2 を，ragroute-upper と学習の要らない方式について，EQ2 の `metrics.json` の値と 5e-4 以内で一致させる．
**term_sketch だけは 2e-3 以内**とする（採点が文字列のハッシュの種で揺れる．T2 の診断，journal の「T2」と backlog B8）．

**仮説**
- **H-N1**：新参者の問の pooled hit@2 で，ragroute-frozen < ragroute-upper かつ 95% ブートストラップ信頼区間が重ならなければ，「再学習しない素朴な中央方式は，新参者を見つけにくい」を支持．
- **H-N2**：flood_score と，公開情報を使う方式のうち pooled hit@2 が最良のものが，それぞれ ragroute-frozen より高く（区間が重ならない），「学習の要らない方式は，新参者の発見で再学習しない素朴な中央方式に勝る」を支持．
- 書き方の制約（D2）：ragroute は素朴な中央方式（関連の無い受信箱を 1 問あたり 10 個だけ抜き出して学習）なので，「中央方式一般」についての主張はしない．
- 参考（判定に使わない）：新参者ごとの値，新参者の問以外（既存の受信箱の問）での ragroute-frozen の hit@2．
- 限界：公開情報の大きさ（件名 40，T = 200，C = 32）と m = 2 は，新参者を含む dev で選んだ値である．

**実行**：制御点のコンテナで 1 回だけ．`results/20261014_t3_newcomer/`（`metrics.json`，`analysis_report.md`）に書く．

```bash
bash scripts/tasks/remote.sh newcomer_eval 20261014_t3_newcomer 20261009_eq2_advert_eval
```

## T2：持ち主推定の定義の統一（確定．2026-10-14 14:04．`results/20261014_t2_owner_eval`）

事前登録（下の「T2 の事前登録」）のとおり，制御点のコンテナで 1 回だけ実行した（所要 44 秒．イメージは `01e8908`．`src` は固定した `5b6c190` と同じ）．結果を見て条件・基準は変えていない．

**退行の確認：term_sketch だけ不一致．原因を特定した（p0005 §9 の 1 に当たるが，判定は続けた．下の「計画からの差」）**
- card_sim・centroid_sim・multi_centroid は，Q の top-1・E の top-1・E の AUC が EQ2 と**完全に一致**した（差 0）．
- term_sketch は Q の top-1 が 0.2922 対 EQ2 の hit@1 0.2929（差 −0.0007．2 問），E の top-1 が 0.4274 対 0.4253（差 +0.0021．3 通）で，基準（5e-4）を超えた．AUC は一致した．
- 原因：term_sketch の採点は，クエリに含まれる語の重みの和を，語の**集合**を走査して足す（`score_terms`）．足す順序が Python の文字列のハッシュの種（`PYTHONHASHSEED`）で変わり，
  浮動小数の末尾の桁がずれて，正解の受信箱との同点（Q で 88〜89 問）の扱いが 1〜3 件変わる．同じ制御点のコンテナで `PYTHONHASHSEED` だけを変えて 4 回採点すると，
  Q の top-1 は 0.2915〜0.2922，E の top-1 は 0.4240〜0.4274 に動いた（`ts_diag.out`）．EQ2 の値（0.2929・0.4253）も，同じ揺れの中にある．
  実装の誤りではなく，term_sketch の採点が持つプロセスごとの揺れである．ほかの 3 方式は和を取る順序に依存しない（内積と最大値）ので一致する．
- 影響：揺れは Q で ±0.001，E で ±0.002 程度で，下の H-O1 の差（term_sketch の 0.25 以上）に比べて十分に小さい．

**結果**（持ち主の top-1 [95%CI]．E_short は 28 トークンに切ったメール）

| 方式 | Q（質問文） | E（メール本文） | E_short（28 トークン） | Q の AUC | E の AUC | E_short の AUC | H-O1（E_short と Q） |
|---|---|---|---|---|---|---|---|
| card_sim | 0.112 [0.100, 0.123] | 0.168 [0.149, 0.188] | 0.074 [0.060, 0.088] | 0.704 | 0.760 | 0.659 | E_short の方が低い |
| term_sketch | 0.292 [0.276, 0.311] | 0.427 [0.401, 0.452] | 0.543 [0.515, 0.568] | 0.793 | 0.939 | 0.888 | 同じ長さでも，メール本文の方が持ち主を当てやすい |
| centroid_sim | 0.264 [0.248, 0.280] | 0.284 [0.260, 0.308] | 0.165 [0.146, 0.186] | 0.804 | 0.853 | 0.754 | E_short の方が低い |
| multi_centroid | 0.349 [0.332, 0.366] | 0.527 [0.502, 0.552] | 0.211 [0.190, 0.232] | 0.866 | 0.950 | 0.830 | E_short の方が低い |

**読み取れること**（EQ2 の「公開する情報は，見つけてもらうことより持ち主を当てることの方に役立つ」の訂正）
- 埋め込みを使う 3 方式（card_sim・centroid_sim・multi_centroid）は，メール本文からの持ち主の特定（E）が質問文からの発見（Q）より高いが，メール本文を質問と同じ長さ（28 トークン）に切ると，
  持ち主の特定（E_short）は質問文からの発見（Q）より**低い**．EQ2 の差は，入力の長さ（情報量）の違いで説明でき，同じ長さなら「持ち主を当てる」より「見つけてもらう」の方が高い．
  card_sim と centroid_sim では，長いメール本文でも持ち主の特定は Q と同程度にとどまる（0.168 対 0.112，0.284 対 0.264）．
- term_sketch は，28 トークンでも持ち主の特定が 0.543 で，Q（0.292）より高く，長いメール本文（0.427）よりも高い．メールの先頭の送信者・宛先・件名などの固有名詞が，語のスケッチと一致しやすいためと考えられる（未確認）．
  語のスケッチは，同じ入力の長さでも，見つけてもらうことより持ち主を当てることに役立つ方式である．
- したがって EQ2 の読み取りは，方式ごとに分けて書く：語のスケッチ（term_sketch）は持ち主の特定に強く，埋め込みを使う方式は入力の長さを揃えると持ち主の特定が弱い．公開情報の量を増やすと両方が単調に増える（H-A2）という結論は，この評価の対象ではない．
- 限界：E_short は先頭の 28 トークンで，メールの署名・引用など末尾の情報を含まない．Q はクエリ側，E_short は文書側の接頭辞なしの埋め込みで，モードが違う．
  E_short の先頭 28 トークンにメールのヘッダが多く含まれるかは確かめていない．
- **訂正（Iteration 2．値は変えない）**：ヘッダーを除いた本文の先頭 28 トークン（E_body_short）では，term_sketch の持ち主の top-1 は 0.219 で Q（0.292）より低い．上の term_sketch の「同じ長さでも，メール本文の方が持ち主を当てやすい」はヘッダー（持ち主のアドレス）によるもので，主張しない．4 方式とも，同じ長さなら持ち主の特定は Q より低い（journal の「Iteration 2 実行済み」）．

**計画からの差**：p0005 §9 の 1 は「退行の確認が一致しなければ止まる」である．term_sketch の不一致は，原因（採点の揺れ）を診断で特定し，影響（±0.002）が判定の差より十分に小さいので，止まらずに記録して続けた．
この判断は，計画の止まる条件を自動で緩めたものであり，ユーザーの確認を求める（backlog B8）．T3 の事前登録には，term_sketch の揺れを許す基準を書く．

## T2 の事前登録（2026-10-14．実行の前に push する．p0005 §4）

持ち主推定の定義を揃える．コードは `5b6c190`（`atrium owner-eval`，`src/atrium/owner_eval.py`）に固定し，結果を見て入力・大きさ・切り詰めの長さを変えない．実行は 1 回だけ．

**問い**：EQ2 の「公開する情報は，見つけてもらうことより持ち主を当てることの方に役立つ」は，入力の長さの違い（質問文は短く，メール本文は長い）で説明できるか．

**手順**：公開情報を使う 4 方式（card_sim・term_sketch・centroid_sim・multi_centroid．EQ2 で選んだ大きさ：件名 40，T = 200，C = 32）について，同じ採点の関数で 3 つの入力の top-1 と AUC を出す．
- Q：test の質問文（EQ2 と同じ 2,940 問）．E：EQ2 と同じ所有者推定用のメール（受信箱あたり最大 10 通，種 12．1,460 通）．
- E_short：E の各メールを，test の質問のトークン数の中央値（arctic-embed-m-v1.5 のトークナイザ．特殊トークンを除く）に先頭から切ったもの．E_short は文書側（接頭辞なし），Q はクエリ側（接頭辞あり）で埋め込む．
- flood_score・ragroute・random は受信箱ごとの公開情報が無いので対象外（EQ2 と同じく偶然の水準）．

**退行の確認**（一致しなければ判定に進まず，原因を調べて記録する）：Q の top-1 が EQ2 の hit@1（card_sim 0.112，term_sketch 0.293，centroid_sim 0.264，multi_centroid 0.349），
E の top-1・AUC が EQ2 の所有者 top-1・AUC（0.168/0.760，0.425/0.939，0.284/0.853，0.527/0.950）と，小数 3 桁（差 5e-4 以内）で一致すること．

**H-O1**（方式ごとに記録．全体の判定は付けない）：95% ブートストラップ信頼区間で，E_short の top-1 が Q の top-1 より高く，区間が重ならなければ「同じ長さでも，メール本文の方が持ち主を当てやすい」，
区間が重なれば「EQ2 の差は入力の長さで説明できる」，E_short の区間が Q より下なら「E_short の方が低い」と書く．

**予想**（判定に使わない）：term_sketch は送信者の名前などの固有名詞で，短くてもメール本文の方が当てやすい．

**限界**：E_short は先頭を切るので，署名・引用など末尾の情報は含まない．Q と E_short の埋め込みのモード（クエリ側と文書側）が違う．

**実行**：制御点のコンテナで 1 回だけ．`results/20261014_t2_owner_eval/`（`metrics.json`，`analysis_report.md`）に書く．

```bash
bash scripts/tasks/remote.sh owner_eval 20261014_t2_owner_eval 20261009_eq2_advert_eval
```

## 判定の補正の確定と EQ8 の読み方の訂正（2026-10-14．p0005 T1．`results/20261014_t1_judge_audit/`）

既存の `results.jsonl`・`judgements.jsonl` を読み直しただけで，実験は流していない．訂正する節は「EQ5：RQ-B の判定」（判定モデルの偽陽性）と「EQ8〜EQ10」の EQ8（判定の読み方の注意）である．過去の節は書き換えていない．

**決定（ユーザー．p0005 §1）**
- D3：0.6B の正答率の主な値は，事前登録どおり**判定モデル（qwen3:8b）の元の値**とし，補正値を併記する．
- D4：補正の定義は**狭い補正**（`final_answer` が例文 `The answer to the question` **だけ**の回答を不正解に直す）．
  広い補正（例文を**含む**回答を全て不正解に直す）は，EQ5 の B2（CPU の受信箱）で例文を含み中身がある回答のうち判定が正解とした 213 件から 30 件を目視したところ，27 件が正解と照らして正しい答えだったので，誤りである．
- D2：中央の分類器（ragroute．EnronQA の test で hit@1 0.215）は学習し直さない．文書・表では「素朴な中央方式（naive central classifier）」と呼び，中央方式一般についての主張はしない．

**定義**：例文だけの回答 = `re.fullmatch(r"\s*the answer to the question\s*[.:]?\s*", final_answer, re.IGNORECASE)`．
狭い補正の正答率 =（判定が正解とした数 − 例文だけの回答で正解とされた数）／ 問の数．判定できなかった出力は不正解．CPU の受信箱 = CPU の専門家（13.x・14.x）が持つ受信箱の質問 1,940 問．

**検算：一致した**（plan の値．小数 3 桁．B2・B3 のプロンプトのまま返した回答の数は全 2,940 問で数えた値）

| 実行 | 例文だけ（うち正解） | 元の正答率 | 狭い補正 | プロンプトのまま（うち正解．全問） |
|---|---|---|---|---|
| EQ5 B2（`20261009_eq5_b2`） | 696（71） | 0.297 | 0.261 | 40（6） |
| EQ5 B3 | 877（88） | 0.260 | 0.215 | 113（22） |
| EQ8 B2 k = 1 | 1,021（85） | 0.249 | 0.205 | 48（14） |
| EQ8 B2 k = 3 | 878（63） | 0.258 | 0.226 | 94（16） |
| EQ8 B2 k = 5 | 697（70） | 0.297 | 0.261 | 40（7） |
| EQ9 B3 k = 5 | 916（95） | 0.254 | 0.205 | 108（18） |
| EQ5 B1・B1 flood_score・B2'・B4，EQ8 B1・B2' の全 k | 0 | 不変 | 不変 | 0〜9（正解 0〜2） |

全 16 実行の表は `judge_audit.out` にある．例文だけの回答は B2（0.6B）と B3（0.6B）にしか無い．

**訂正**
- EQ8 の「B2 で k を増やして上がった分の多くは偽陽性で，補正すると差はほぼ消える」という読み方（副指標 0.147・0.142・0.154）は，**誤った広い補正に基づく**．
  狭い補正では 0.205 → 0.226 → 0.261 と，k とともに上がる．文脈が増えると，例文だけの回答が減る（1,021 → 878 → 697）．
  EQ8 の判定（Holm 補正後の 6 組）は元の値によるもので，変わらない．
- EQ5 の「結論の向きは変わらない」は，狭い補正でも成り立つ：B2 対 B3（CPU の受信箱）は 0.261 対 0.215，B2 対 B1 は 0.261 対 0.789．0.6B の条件の偽陽性による上乗せは 0.032〜0.049 である．
- 限界：プロンプトをそのまま返した回答（`Emails:` で始まる）は，末尾に正しい答えを含むもの（回答の抽出の失敗）と，メールの本文を吐き出したもの（偽陽性の疑い）が混ざる．
  正解とされた数は最大で B3 の 22 / 2,940（約 0.7 pt）なので補正せず，限界として記録する．
- 計画からの差：p0005 §3.2 は CPU の受信箱を `selected_sources[0]` で決めるが，flood_score の実行では選んだ先が正解の受信箱と限らないので，質問の置き場所（正解の受信箱．`bank`）で分けた．
  routing=oracle の実行では同じ値になる（検算の全項目が一致した）．

## EQ7b：実機の参加・離脱の再生（確定．2026-10-14 03:53 に全 22 回が終了．`results/20261013_eq7b_*`，集計は `results/20261013_eq7b_summary/final/`）

事前登録（下の「EQ7b の事前登録」）のとおりに 22 回を流した．結果を見て条件・基準は変えていない．失敗した問は全 22 回で 0（HTTP のエラー以外の失敗も無い）．
1 回の所要は 2.6〜42 分（平均 20 分）．

**判定**
- **退行ゲート：通過**．a = 1 の hit@2 が，multi_centroid 0.4401・flood_score 0.8041 で EQ7a（＝ EQ2）と 0 問の差で一致した．
- **H-C2（EQ7a の再現）：成立**．本番の 20 回すべてで，実機の hit@2 が EQ7a の同じ条件の値と**小数 4 桁まで一致した**（最大の差 0.00000．基準は 2 問分 = 0.00068 以内）．
  EQ7a の規則（メンバー表・離脱したノードの扱い・返事のあった受信箱の中から選ぶ規則）が，実機の質問者とノード 30 台の振る舞いと同じであることを示す．
- **H-C1（実機）：支持**．a = 0.7 で R = 1 → 1000 の hit@2 の相対的な低下は，multi_centroid（0.314，0.321，0.262，0.255，0.278）が flood_score（0.298，0.310，0.240，0.213，0.255）より，
  5 個の種のすべてで大きかった（差は +0.011〜+0.041）．数値は EQ7a と同じなので，EQ7a の限界（差が小さい）もそのまま当てはまる．

**a = 0.7 の平均（5 個の種）**

| 方式 | R | hit@2 | 正解が稼働中のときの hit@2 | 問い合わせたノード／問 | うち離脱していたノード |
|---|---|---|---|---|---|
| multi_centroid | 1 | 0.334 | 0.477 | 1.94 | 0 |
| multi_centroid | 1000 | 0.238 | 0.340 | 1.94 | 0.55 |
| flood_score | 1 | 0.580 | 0.828 | 21.08 | 0 |
| flood_score | 1000 | 0.427 | 0.610 | 20.84 | 5.77 |

**考察**
- 絶対値では，どの条件でも flood_score の hit@2 が multi_centroid を大きく上回る（R = 1000 で 0.427 対 0.238）．ただし flood_score は 1 問に約 21 台へ問い合わせる（multi_centroid は 1.9 台）．
  メンバー表が古くなったときの低下の割合は，2 方式で似ている（0.21〜0.32）．「公開情報を使う方式は，表が古いと大きく hit を失う」とは言えず，EQ7a の考察と同じである．
- 無駄な問い合わせの割合は，R = 1000 で方式によらず約 28%（multi_centroid 0.55 / 1.94，flood_score 5.77 / 20.84）．離脱の検出が遅れる影響は，稼働率にほぼ比例する．
- 質問者の変更（離脱を許す．`experiment.churn` を指定した実験だけ）は，離脱の無い条件の結果を変えなかった（退行ゲート）．
- **EQ7a の未検証の項目**：「R が大きいと，正解が稼働中でも hit が 3 割前後下がる原因は，表の更新の後に参加したノードが見えないこと」は，EQ7b でも分けて数えていないので未検証のままである
  （実機と EQ7a が完全に一致するため，EQ7b はこの点について新しい情報を与えない）．

**限界**（事前登録のとおり）：離脱は「プロセスが生きたまま 503 を返す」ことで起こしたので，ホストの停止・接続拒否・タイムアウトによる検出の遅れは測っていない．
刻みは質問の番号で，質問は 1 問ずつ流したため，同時の質問による待ち行列は含まない（e2e は 0.05〜0.12 秒．EQ3 の待ち時間とは比べられない）．メールの時間的な到着は再現していない．

**退行ゲートの 2 回（先に確定した記録）**

- 事前登録（下の「EQ7b の事前登録」）の 22 回のうち，退行ゲートの 2 回（稼働率 1）を先に流した．本番の 20 回はこの後に流す．
- イメージは `d59123a-dirty`（事前登録のコミット．`src` は固定した `5613f8c` と同じ．dirty は連続実行が書き換える `config.yaml` だけ）．

| 方式 | 失敗 | hit@2（実機） | EQ7a の離脱なしの hit@2 | 差 | 問い合わせたノード／問 | e2e p50 / p95 (s) | 所要 |
|---|---|---|---|---|---|---|---|
| multi_centroid | 0 / 2,940 | 0.4401 | 0.4401 | 0 問 | 1.96 | 0.124 / 0.153 | 42 分 |
| flood_score | 0 / 2,940 | 0.8041 | 0.8041 | 0 問 | 30.00 | 0.089 / 0.100 | 40 分 |

- **退行ゲート：通過**．2 方式とも，実機の hit@2 が EQ7a（＝ EQ2）の値と完全に一致した（基準は 1 問以内）．離脱を許す質問者の変更は，離脱の無い条件の結果を変えていない．
- 観察（判定には使わない）：parallel 1 の flood_score の e2e の中央値は 0.089 秒で，EQ3（parallel 30）の 92.2 秒の約 1/1000 である．
  EQ3 の待ち時間のほとんどは，30 問が同時に 30 台へ probe を送ったときの待ち行列だったことになる．
  multi_centroid（0.124 秒）より速いのは，multi_centroid が 1 問ごとに 150 受信箱 × 32 重心のスコアを質問者の CPU で計算するためと考えられる（未検証）．
- 集計のコマンドの修正（`atrium churn-live-report`．集計だけで，実行の経路は変えていない）：22 回が揃う前に集計すると H-C1 の判定で止まった（`KeyError`）ので，
  判定に要る実行が揃うまで H-C1 を判定しないようにした．また，比べる実行が 0 回のときに H-C2 が「成立」と出ていたので，「未判定」と出すようにした．

## EQ7b の事前登録（2026-10-13．実行の前に push する．p0004 §6.4）

EQ7a（実機を使わない）の仕組みを，実機のノード 30 台と実機の質問者で再生して確かめる．コードは `5613f8c`（`experiment.churn`，`atrium/churn_live.py`）に固定し，
結果を見て条件・基準を変えない．

**問い**：EQ7a の（メンバー表が古いときの）hit@2 は，実機で再現するか．H-C1 は実機でも支持されるか．

**手順**
- 質問は EQ2 の test の 2,940 問を，EQ7a と同じ順（`experiment.seed` = 12 の置換）で **1 問ずつ順に**流す（`parallel: 1`．1 問 = 1 刻み）．m = 2．
- 各ノードの稼働状態は，EQ7a と同じ時刻表（2 状態のマルコフ連鎖．稼働中の平均 200 刻み．種は `[12, schedule_seed, 稼働率×100]`）を，刻みごとに `/v1/admin/availability` でノードへ伝える．
  離脱中のノードは検索・probe・プロファイルの要求を 503 で拒否する．
- 質問者は，`refresh_steps` 刻みごとにだけ，全ノードの `/v1/profile` を引いて返事のあったノードをメンバー表にする．
  公開情報を使う方式は，表に載っているデータ源の中から上位 m 個を選び，離脱していても問い合わせる．`flood_score` は表のノード全部の `/v1/probe` に問い合わせ，返事のあった受信箱の中から選ぶ．
  質問者は，離脱による要求の失敗（HTTP のエラー・接続の失敗）を問の失敗にせず，返事のあった分で続ける．
- 実行は 22 回（`results/20261013_eq7b_summary/eq7b_specs.txt`．`eq8_chain.sh` で 1 回ずつ deploy → start → analyze）：
  - 退行ゲート 2 回：a = 1，R = 1，種 0，multi_centroid と flood_score．
  - 本番 20 回：a = 0.7，R ∈ {1, 1000}，種 0〜4，multi_centroid と flood_score．
- 集計：`atrium churn-live-report`．hit@2・正解が稼働中の割合・問い合わせたノード・うち離脱していたノードを，EQ7a と同じ定義で数える（失敗した問は hit = 0）．

**判定**
- **退行ゲート**：a = 1 の hit@2 が，EQ7a の離脱なしの値（multi_centroid 0.440，flood_score 0.804．EQ2 と同じ）と 1 問（1/2940）以内で一致すること．
  一致しなければ本番の判定に進まず，原因を調べて記録する．
- **H-C2（EQ7a の再現）**：本番の全 20 回で，|実機の hit@2 − EQ7a の同じ条件の hit@2| が 2 問分（2/2940）以内なら「EQ7a の仕組みは実機で再現した」．超える回があれば，どの条件か・原因の候補を記録する．
- **H-C1（実機）**：EQ7a と同じ規則．a = 0.7 で R = 1 → 1000 の hit@2 の相対的な低下が，multi_centroid では flood_score より，5 個の種のすべてで大きければ支持．
- 記述のみ（判定に使わない）：e2e の中央値，問い合わせたノードの数と離脱していたノードの数，失敗した問の数（HTTP のエラー以外の失敗は 0 であるはず）．

**予想**（判定に使わない）：実機の質問者の振る舞いは EQ7a の仮定と同じ規則なので，hit@2 は EQ7a と（ほぼ）一致する．H-C1 の判定も EQ7a と同じになる．

**限界**
- ノードの離脱は「プロセスが生きたまま 503 を返す」ことで起こす．ホストの停止や接続拒否（TCP の RST）・タイムアウトは起こさないので，離脱の検出の遅さは測れない．
- 刻みは質問の番号で，壁時計時間ではない．質問は 1 問ずつ流すので，同時に流したときの待ち行列の影響は含まない（e2e は EQ3 の値と比べられない）．
- 表の更新の後に参加したノードの見えなさは，EQ7a と同じく分けて数えない．メールの時間的な到着は再現しない．

## s8：MedRAG の cross_encoder（確定．2026-10-13 12:29．`results/20261007_s8_med_all_ce`）

- B4 の撤回（2026-10-12）を受け，同じ run_id で再開して全 7,663 問を終えた．**失敗 0**．
  質問者の LLM は wafl500（`cluster.requester_llm`），専門家は先頭 16 台である．
- **正答率 0.689**［0.678, 0.699］（test 4,599 問は 0.690）．routing=all・merge=score の s4（0.659）との McNemar の正確検定は，
  共通 7,606 問で s8 のみ正解 879 問・s4 のみ正解 655 問，p = 1.2e-8．RAG なしの s7（0.640）との差は p = 1.8e-17 である．
  問題集ごとの s8：bioasq 0.875，medmcqa 0.634，medqa 0.667，mmlu 0.792，pubmedqa 0.742（pubmedqa だけ s4 の 0.766 より低い）．
- 判定：**cross_encoder の再ランクは，score の統合より正答率が +3.0 pt 高い（p < 0.05）**．ただし s4 との差には，統合方式だけでなく
  質問者の LLM の置き場所（wafl-ctrl5 → wafl500）と，s4 の失敗 57 問の除外による共通問の違いが混ざる．
  生成の待ち時間は p50 6.9 秒（s4 は 21.0 秒）と大きく違うので，LLM の置き場所を揃えた比較は未実施である．
- 待ち時間：merge_s の p50 22.1 秒（cross_encoder の再ランク），e2e p50 31.5 秒・p95 54.5 秒．
- 実行の経緯：最初の 1,191 問は 2026-10-07〜09 のコード（`9f115ae-dirty`，`llm.timeout_s` = 600）で，残りの 6,472 問は
  `16febdc-dirty`（`llm.timeout_s` = 1800）で流した．`run_meta.json` の `git_head` は再開時の値である．
  再開時，2026-10-09 の手動停止が残した `.exit`（値 0）を，start が「完了済み」と見なしたので，制御点で削除した．
- ここでも，MedRAG の正答率はルーティングではなく統合方式と生成側の条件に反応する（E1 の判定と整合する）．

## S6：MedRAG の宿る型のパイロット（確定．2026-10-12 19:45．`results/20261012b_e3_{smoke,pilot}`）

- B4 の保留を 2026-10-12 にユーザーが撤回し，p0003 S6 を実行した（`s6_run.sh`．routing=all，answer_mode=local_answer，merge=score）．
- **1 回目の smoke（`20261012_e3_smoke`）**：10 問中 2 問が失敗した（専門家 13.103 が 502）．原因は専門家側の Ollama 呼び出しが
  `llm.timeout_s`（600 秒）で切れたこと（e2e 602.9 秒）．`llm.timeout_s` を 1800 にして（`f3e50e3`）やり直した．
- **2 回目の smoke（`20261012b_e3_smoke`）**：10 問すべて成功（1,779 秒）．
- **pilot（`20261012b_e3_pilot`，100 問）**：**失敗 64 問**（成功 36 問，所要 13,090 秒）．p0003 S6 の基準（失敗 0・3 時間以内）を満たさないので，
  pilot50 は実行していない．判定：**不成功（基盤の限界）**．
  - 失敗は問題集に偏る．medmcqa 20/20，pubmedqa 20/20，mmlu 9/20，medqa 10/20，bioasq 5/20 が失敗した．
  - 原因の確認：専門家 13.103（wafl303，メモリ 16 GB）のカーネルログに，`llama-server`（qwen3:0.6b）の OOM kill がある
    （anon-rss 9.2 GB．19:36:58）．Ollama は 500 を返し，専門家は 502，質問者は失敗として数えた．7 問は質問者側の ReadTimeout である．
  - 推定される機序（未検証）：`llm.num_ctx` = 16384 に，k_context = 15 の断片（約 4,800〜6,700 トークン）を渡し，
    同じ専門家に `experiment.parallel` = 4 の要求が重なって，KV キャッシュと検索のシャード（`cluster.shard_budget_gb` = 6）が
    16 GB のメモリを超える．
- 成功した 36 問の e2e の中央値は約 534 秒である．この条件で 7,663 問を流すのは現実的でない．
- 参考（主張には使わない）：成功した問の正答率は smoke 0.50（10 問），pilot 0.58 [0.42, 0.73]（36 問）．失敗が問題集に偏るので，
  s4（0.659）などとは比べられない．
- 次の候補は (1) `experiment.parallel` を 1 に下げる，(2) `llm.num_ctx` または `local_answer.k_context` を下げる，
  (3) 宿る型の主評価は EnronQA の RQ-B に任せ，MedRAG の宿る型は不成功として閉じる，の 3 つだった．
  **2026-10-13 にユーザーが (3) を選び，MedRAG の宿る型は不成功として閉じた**（backlog B6）．
  (1) は 1 問約 9 分で全問に現実的でなく，(2) は E1 と文脈の条件が変わって比較が崩れるためである．

## EQ7a：RQ-C のオフラインの評価（確定．2026-10-12 12:19．`results/20261012_eq7a_churn`）

- 事前登録（下の「EQ7a の事前登録」．`f5e663d` で評価の前に push 済み）のとおり，制御点のコンテナで **1 回だけ**実行した（所要 50 秒）．
  実行したコードは `03698c3` で，`src`・`tests`・`scripts` は事前登録の `34258f7` と同じ．結果を見て条件・基準は変えていない．
- **退行ゲート：通過**．離脱なし（a = 1）の hit@2 が，6 方式すべてで EQ2 の test の値と一致した（差は +0.0000．許容は 1/2940）．
  card_sim 0.153，term_sketch 0.359，centroid_sim 0.323，multi_centroid 0.440，flood_score 0.804，ragroute 0.279．
- **H-C1：支持**（a = 0.7，R = 1 → 1000 の hit@2 の相対的な低下）．multi_centroid の低下は 5 個の種のすべてで flood_score より大きかった（差は +0.011〜+0.041）．
  平均では multi_centroid が 0.286，flood_score が 0.263 で，差は 0.023 にとどまる．

| 方式（a = 0.7） | 相対的な低下の平均 | flood_score との差（種ごとの最小〜最大） |
|---|---|---|
| multi_centroid（判定の対象） | 0.286 | +0.011〜+0.041 |
| card_sim | 0.292 | −0.020〜+0.081 |
| term_sketch | 0.279 | −0.027〜+0.044 |
| centroid_sim | 0.267 | −0.024〜+0.045 |
| ragroute | 0.268 | −0.023〜+0.037 |
| flood_score | 0.263 | － |

- **判定の読み方の注意**（判定は書き換えない）：
  - 事前登録した判定は，multi_centroid 1 方式についてである．他の 4 方式は，どれも全ての種で flood_score を上回るとは言えない（参考の表）．
    「公開情報を使う方式は，メンバー表が古いと flood_score より大きく hit を失う」と一般化することはできない．
  - 差が小さい（平均 0.023）うえに，方式ごとの低下の範囲は flood_score と重なる（multi_centroid 0.255〜0.321，flood_score 0.213〜0.310）．支持は，5 個の種で向きがそろったという意味に限る．
  - **絶対値では，どの条件でも flood_score の hit が公開情報を使う方式を大きく上回る**．a = 0.7，R = 1000 で flood_score 0.427 に対し multi_centroid 0.238，
    a = 0.5，R = 1000 で 0.239 に対し 0.136．ただし flood_score は 1 問に平均 20.8 台（a = 0.7，R = 1000）〜15.3 台（a = 0.5）に問い合わせ，multi_centroid は 1.9 台である．
  - 事前登録の予想は「離脱済みの受信箱が上位 m 個の枠を占めて hit を失う（公開情報を使う方式）」だった．データは，この効果が小さいことを示す．
    メンバー表が古いときの低下は，全ての方式で同じ程度（平均 0.26〜0.29）に起きた．
    正解が稼働中の問に限った hit@2 も，どの方式でも R = 1 から R = 1000 で 3 割前後下がる（flood_score 0.828 → 0.610，multi_centroid 0.477 → 0.340）．
    考えられる原因は，表の更新の後に参加したノードが見えず，正解の受信箱が稼働中でも問い合わせられないこと（**未検証**．見えていたかどうかを分けて数えていない）．
- 無駄な問い合わせ（問い合わせたノードのうち，離脱していたノード）の割合は，方式によらず同じ程度である（a = 0.7，R = 1000 で multi_centroid 0.55 / 1.94，flood_score 5.77 / 20.84．どちらも約 28%）．
  絶対数は flood_score の方が約 10 倍多い．
- 参考：a = 0.7 で R = 1 のとき，正解が稼働中の問に限った hit@2 は，離脱なしの値より高い（multi_centroid 0.477 対 0.440，flood_score 0.828 対 0.804）．
  稼働中の受信箱が減って，競合が減るためと考えられる（未検証）．
- **限界**（事前登録のとおり）：flood_score は「返事のあった分で続ける」仮定の上限で，今の質問者の実装の挙動ではない（1 台でも失敗すると問全体が失敗する）．
  メールの時間的な到着は再現していない．回答の正答率と待ち時間は測っていない（実機の EQ7b は 2026-10-14 に行い，hit@2 は全 22 回で EQ7a と一致した．上の「EQ7b」）．
- 次に調べる候補（未登録）：表の更新の後に参加したノードの見えなさを分けて数える．質問者が部分的な失敗を許す実装にして，実機で EQ7b を行う（後者は実施済み．上の「EQ7b」）．

## E2 の下流確認（確定．2026-10-12 11:44．p0003 S5．`results/20261012_e2_desc_sim`）

- 条件：`dataset: medrag`，`routing: card_sim`（S4 の `desc_sim`．`routing.top_m.medrag` = 3），`answer_mode: retrieval_only`，`merge: score`，全 7,663 問．
  イメージは `ac63bc6`．`-dirty` は `config.yaml` の `routing` の書き換えによる（`src` の差分は無い）．質問者の起動時に，自己紹介文を S4 と同じ埋め込みで広告に付けた（`244b4b4`）．
- **失敗は 0 / 7,663**（p0003 S5 の完了条件「失敗率 1% 以下」を満たす）．所要時間は 09:31〜11:44 の 2.2 時間（見込み 2.5 時間以下）．
  実行前のディスク使用率は，専門家 16 台と制御点のすべてで 70% 未満だった．実行後に `mise run stop` し，`config.yaml` は元に戻っている．
- 全問で，3 源（シャード 11.86 個／問．16 個のうち）に問い合わせた．e2e は p50 4.63 秒・p95 4.67 秒（`retrieval_only`．埋め込み 0.009 秒）．事前に公開した情報は 238 バイト．

**データ源の選択（test 4,599 問．E1 の同じ質問の値と並べる）**

| 方式 | 適合率 | 再現率 | F1 | 平均問い合わせ源 | 削減率 |
|---|---|---|---|---|---|
| all（s3） | 0.445 | 1.000 | 0.616 | 4.00 | 0 |
| ragroute（s5） | 0.865 | 0.799 | 0.830 | 1.65 | 0.589 |
| random（s6．3 源） | 0.445 | 0.749 | 0.558 | 3.00 | 0.250 |
| **desc_sim（今回．3 源）** | 0.505 | 0.850 | 0.634 | 3.00 | 0.250 |

- 同じ 3 源に問い合わせる random と比べて，desc_sim は再現率（0.850 対 0.749）と F1（0.634 対 0.558）が高い．差の検定は事前登録していないので，記述にとどめる．
- 学習済みの ragroute は，より少ない問い合わせ（1.65 源）で F1 が高い（0.830）．全データ源で学習済みの通常の条件では，desc_sim は ragroute に及ばない．
  desc_sim が使えるのは，S4 で測った「再学習なしで新参源を見つける」場面である（新参源の再現率 0.830 対 ragroute-frozen 0.354）．
- S4 のオフラインの値（4 源がそろった世界で k = 3 の `train`・`val` の F1 = 0.637）と，実機の test の F1（0.634）はほぼ同じである．実機の実装は，オフラインの評価と一致した．

**`top_doc_ids` と s3（routing=all）の比較**

- 上位の文書のうち s3 にも現れた割合は，全 7,663 問で 0.908（上位の列が完全に一致したのは 73.5%），test 4,599 問で 0.906（完全一致 73.3%）．
- この差は，落とした源の文書が抜けた分だけで説明できる．s3 の上位 15 件のうち，選ばれた源の文書の割合の平均は 0.9061（test）で，実測の 0.906 と同じである．
  4,599 問のうち 4,597 問で，s3 との共通の文書の数が「s3 の上位のうち選ばれた源の文書の数」と一致した．残りの 2 問は一致しなかった（原因は未確認）．
  したがって，検索そのものは s3 と同じで，違いは問い合わせ先を絞ったことだけから生じている．
- 文書の源は文書 ID の接頭辞で判定した（`pubmed`・`wiki`・`article` はそれぞれ PubMed・Wikipedia・StatPearls，それ以外は Textbooks）．
  最初は接頭辞の対応表が不完全で，615 問が不一致になった．教科書の接頭辞を直して，2 問になった．

**p0003 の完了の条件**：S1〜S5 を実行した．**S6（E3 のパイロット）は保留のままで，実行していない**（backlog B4．p0004 §10・U1．2026-10-12 にユーザーが保留の維持を選んだ）．
s8（cross_encoder の全問）も同じ理由で保留である．途中の結果（1,191 問）は制御点の `results/20261007_s8_med_all_ce/` に残してあり，再開する場合は B4 の手順に従う．
（2026-10-13 の追記：保留は 2026-10-12 に解除され，S6 は不成功（「S6」．B6 で閉じた），s8 は完了した（「s8」）．これで p0003 の S1〜S6 は全て実行済みである．）

## EQ7a の事前登録（2026-10-12．評価の前に push する．p0004 §6.4・§8.7）

RQ-C（専門家の参加・離脱）のうち，実機を使わずに測れる部分だけを先に測る（EQ7a）．実機の時刻表の再生（EQ7b）は行わない（下の「限界」）．
コードは `34258f7`（branch `eq7-churn`．`atrium churn-eval`，`src/atrium/churn_eval.py`）に固定する．結果を見て条件・基準を変えない．

**問い**：質問者のメンバー表が古くなると，公開情報を使う方式は，公開情報を使わない flood_score より大きく hit を失うか．

**手順**
- 質問は EQ2 の test の 2,940 問．方式のスコアは 1 回だけ計算する（公開情報の大きさは EQ2 で選んだ値：件名 40，T = 200，C = 32．m = 2）．
- 受信箱は EQ5 の配置（`results/20261009_eq5_b1/placement.json`．30 台に 5 受信箱ずつ）に従い，ノード単位で参加・離脱する．
- 各ノードの状態は 2 状態のマルコフ連鎖（1 問 = 1 刻み）．稼働率 a ∈ {1.0, 0.9, 0.7, 0.5}，稼働中の平均の長さ 200 問，初期値は定常分布．
  質問の順番は `experiment.seed`（12）で並べ替える．時刻表の種は 5 個．
- 質問者はメンバー表を R 問ごとにだけ更新する．R ∈ {1, 10, 100, 1000}．各問で見えているのは，直前の更新の時点で稼働中だったノードである．
- 方式：card_sim，term_sketch，centroid_sim，multi_centroid，ragroute は，見えている受信箱の中でスコアの上位 m 個に問い合わせる（離脱済みのノードも枠を占める）．
  flood_score は，見えているノード全部に問い合わせ，返事のあった受信箱の中で上位 m 個を選ぶ．
- 指標：hit@2（正解の受信箱が稼働中で，選ばれた 2 個に入る割合），正解が稼働中の問に限った hit@2，正解が稼働中の割合，問い合わせたノードの数，うち離脱していたノードの数．
  値は時刻表の種をまたいだ平均 [最小, 最大] で出す．

**退行ゲート**：a = 1 の hit@2 が，どの方式でも EQ2 の test の hit@2 と 1 問（1/2940）以内で一致すること．
一致しなければ判定に進まず，原因を調べて記録する（card_sim は埋め込みを計算し直すので，末尾の桁が揺れうる）．

**仮説 H-C1**：a = 0.7 で，R = 1 から R = 1000 にしたときの hit@2 の相対的な低下（1 − hit(R=1000) / hit(R=1)）が，
multi_centroid では flood_score より，**5 個の時刻表の種のすべてで**大きければ「支持」，そうでなければ「支持されない」とする．
multi_centroid を選んだのは，EQ2 で公開情報を使う方式の中で hit が最も高かったためである．他の方式は参考として同じ表に出し，判定には使わない．

**予想**（判定には使わない）：公開情報を使う方式は，古い表に残った離脱済みの受信箱が上位 m 個の枠を占めるので，R が大きいほど hit を失う．
flood_score は返事のあった受信箱から選ぶので枠を奪われず，失うのは無駄な問い合わせ（離脱したノードへの送信）の方である．

**限界**
- 実機のノードは止めない．質問者は 1 台でも失敗すると問全体を失敗にする（`gather_or_cancel`）ので，ノードを止めると flood_score の全問が失敗する．
  この評価の flood_score は「返事のあった分で続ける」と仮定した上限であり，現在の実装の挙動ではない．
- メールの時間的な到着・削除は再現しない（EnronQA に日付のフィールドが無い）．離脱はノード単位で，受信箱の内容は変わらない．
- 回答の正答率と待ち時間は測らない（EQ7b．実機の質問者を部分的な失敗を許す形に直す変更が要るので，この事前登録には含めない）．

**実行**：制御点のコンテナで 1 回だけ．`results/20261012_eq7a_churn/`（`metrics.json`，`analysis_report.md`）に書く．

```bash
bash scripts/tasks/remote.sh churn_eval 20261012_eq7a_churn 20261009_eq5_b1 20261009_eq2_advert_eval
```

## EQ8〜EQ10：文脈の通数・流し直しの揺れ・待ち時間（確定．2026-10-12 04:17 に全 28 実行が終了．`results/20261011_eq{8,9,10}_*`，集計は `results/20261011_eq8_summary/`）

- 事前登録（下の「EQ8〜EQ10 の事前登録」，実行の前に push 済み）のとおりに流した．結果を見て基準や比べる組は変えていない．
- 28 実行はすべて `b9e25ba26bcf-dirty` で動いた（dirty は連続実行が書き換える `config.yaml` と `eq8_chain.sh` だけで，`src` に差分は無い）．受信箱の配置は EQ5 と同じ（ハッシュ `1d99505d5a17`）．
- 失敗した問は全 28 実行で 0．EQ8 の B2 k = 5 と B2' k = 5 は，start が終了コード 2 で終わったので，同じ run_id で 1 回だけ再開した（流し直したのはそれぞれ 1 問と 4 問）．重複した 5 問は，最後の行（成功）を集計に使った．
- 集計：`eq8_summary.py`（EQ8・EQ9）と `eq10_summary.py`（EQ10）．出力は同じ場所の `eq8_summary.out` と `eq10_summary.out`．実行名の `b2_1p7b` が B2' である．

**EQ8（文脈の通数と正答率．CPU の受信箱 1,940 問）**

| 条件 | k | 正答率 [95%CI] | ひな形を不正解に直した正答率 | 入力トークン p50 |
|---|---|---|---|---|
| B1 | 1 | 0.687 [0.666, 0.707] | 0.687 | 572 |
| B1 | 3 | 0.765 [0.746, 0.784] | 0.762 | 1528 |
| B1 | 5 | 0.786 [0.767, 0.804] | 0.782 | 2475 |
| B2 | 1 | 0.249 [0.230, 0.269] | 0.147 | 599 |
| B2 | 3 | 0.258 [0.239, 0.278] | 0.142 | 1600 |
| B2 | 5 | 0.297 [0.277, 0.318] | 0.154 | 2596 |
| B2' | 1 | 0.625 [0.603, 0.646] | 0.625 | 599 |
| B2' | 3 | 0.647 [0.626, 0.668] | 0.647 | 1600 |
| B2' | 5 | 0.651 [0.630, 0.672] | 0.650 | 2596 |

| 条件 | 組 | 差 | 片方だけ正解 | 補正後の p（Holm，6 組） | 判定 |
|---|---|---|---|---|---|
| B1 | k=1 対 k=5 | −0.099 | 133 対 325 | 7.73e-19 | 差がある |
| B1 | k=3 対 k=5 | −0.021 | 103 対 143 | 3.82e-02 | 差がある |
| B2 | k=1 対 k=5 | −0.048 | 213 対 306 | 2.58e-04 | 差がある |
| B2 | k=3 対 k=5 | −0.039 | 201 対 276 | 2.75e-03 | 差がある |
| B2' | k=1 対 k=5 | −0.026 | 193 対 244 | 3.82e-02 | 差がある |
| B2' | k=3 対 k=5 | −0.004 | 159 対 166 | 7.39e-01 | 差があるとは言えない |

- **判定**：6 組のうち 5 組で差があり，どれも k = 5 のほうが高い．B2' の k = 3 と k = 5 だけは差があるとは言えない．予想（k を減らすと下がる）と合う．
- 外に出た本文は B1 で 1 問あたり k 件ちょうど（1.00・3.00・5.00），B2・B2' は 0．
- 参考（GPU の受信箱 1,000 問．補正しない）：3 条件とも k = 1 対 k = 5 は p < 1e-7 で k = 5 が高い．k = 3 対 k = 5 はどれも p > 0.4 で，k = 3 のほうがわずかに高い．
- **判定の読み方の注意**（判定は書き換えない）：B2 で k を増やして上がった分の多くは，判定モデルがひな形の文を正解とみなした分である．副指標（ひな形を不正解に直した正答率）では 0.147・0.142・0.154 で，k による違いはほぼ消える．B2 の CPU の専門家の答えでは，文脈を増やしても正しく答えられる問はほとんど増えていない．

**EQ9（流し直しによる揺れ．CPU の受信箱 1,940 問）**

| 条件 | final_answer の一致 | 正答率 今回 | 正答率 EQ5 | 差 | 今回だけ正解 対 EQ5 だけ正解 | p |
|---|---|---|---|---|---|---|
| B1 | 0.812 | 0.786 | 0.789 | −0.003 | 41 対 46 | 0.668 |
| B2 | 0.921 | 0.297 | 0.297 | −0.001 | 58 対 59 | 1.000 |
| B2' | 0.998 | 0.651 | 0.650 | +0.001 | 22 対 20 | 0.878 |
| B3 | 0.602 | 0.254 | 0.260 | −0.006 | 134 対 146 | 0.511 |

- **流し直しの幅は 0.006**（B3）．EQ5 の条件間の差（15 組）のうち，これより小さいのは B1 と B4（0.001）だけで，この組は「流し方の揺れと区別できない」．
  B1 flood_score と B2'（0.009）は幅をわずかに超えるだけなので，差として読むには弱い．他の 13 組は 0.037 以上．
- `final_answer` は B3 で 4 割，B1 で 2 割の問が変わったが，正誤が両方向にほぼ同じ数だけ入れ替わり，正答率はほとんど動かなかった．
  p0004 §8.7 の「正答率の実行は各条件 1 回」は，差が 0.01 を大きく超える比較に限れば保てる．
- **H-B1 の再判定**：今回は B2 0.297 対 B3 0.254（B2 だけ正解 264，B3 だけ正解 181），p = 9.72e-05 で同等とは言えない．EQ5（p = 3.73e-04）と同じ結論．EQ5 の判定は書き換えない．

**EQ10（待ち時間．300 問 × 3 回．p50 は 3 回の中央値 [最小〜最大]，単位は秒）**

| 条件 | 置き場所 | e2e p50 | e2e p95 | generate p50 |
|---|---|---|---|---|
| B2 k=1 | cpu | 6.16 [6.14〜6.21] | 10.47 | 6.16 |
| B2 k=3 | cpu | 13.62 [13.59〜13.65] | 26.78 | 13.62 |
| B2 k=5（EQ6） | cpu | 23.73 [23.73〜23.76] | 50.12 | 23.73 |
| B2' k=1 | cpu | 7.71 [7.69〜7.74] | 16.56 | 7.71 |
| B2' k=3 | cpu | 21.59 [21.57〜21.59] | 46.82 | 21.59 |
| B2' k=5（EQ6） | cpu | 37.36 [37.29〜37.63] | 81.52 | 37.36 |
| B1 p1 | all | 1.62 [1.62〜1.62] | 2.97 | 1.61 |
| B4 p1 | all | 1.55 [1.54〜1.55] | 2.63 | 1.55 |
| B1 p4（EQ6） | all | 5.70 [5.67〜5.84] | 8.59 | 5.69 |

- **判定**：事前登録の 5 組はすべて，e2e_s と generate_s の両方で「差がある」．B2・B2' は k が小さいほど速い．B1 と B4（parallel 1）では B4 が速い．
- B1 と B4 の差は 0.07 秒（p50）で，3 回の幅が狭いので判定上は差があるが，実用上は小さい．
- 参考：質問者での順番待ち（B1 の parallel 4 − parallel 1）は p50 で +4.08 秒（判定はしない）．

**合わせて読むと**
- B2' は k を 5 から 3 に減らすと，CPU の受信箱で待ち時間が約 42% 短くなり（37.4 → 21.6 秒），正答率の差は −0.004 で差があるとは言えない．k = 1 まで減らすと −0.026 で差が出る．
- B1 は k = 3 でも −0.021 の差があり，流し直しの幅（0.006）より大きい．B1 では k = 5 を保つ理由がある．
- 次に調べる候補（未登録）：B2' の既定を k = 3 にした場合の H-B2 の読み直し．GPU の受信箱で k = 3 が k = 5 と並ぶ理由（長い文脈での 8B の劣化か）．

**連続実行で起きたこと**
1. 2026-10-11 04:42〜04:47 に gpu2 の DNS が引けず，続く 25 回の deploy が 1 秒ずつで失敗した（`ALL DONE` 04:46:48）．
   `eq8_chain.sh` に `retry`（deploy と judge up を 5 分おいて 3 回まで試す）を足し，失敗したログを `failed_20261011_0446/` に移して 10:39 に再開した．結果のある実行は飛ばした．
2. 21:10:12 から，連続実行のプロセスが `/var/run/docker.sock` に触れなくなった（プロセスが docker グループを持っていなかった）．judge up が docker build で失敗し，`retry` の 3 回も尽きる前に止めた．
   21:56 に `sg docker` の下で再開した（最初の起動はスクリプトのパスを誤り，21:56:25〜21:56:53 は何も動いていない）．採点は 21:57〜23:43．
- 所要時間：EQ8・EQ9 は障害の 6 時間を除いて約 14.5 時間（見込み 12 時間），採点は約 1.8 時間（見込み 2 時間），EQ10 は約 4.5 時間（見込み 4 時間）．
- `eq8.log*`・`*.nohup*`・`failed_20261011_0446/`（連続実行のログと，失敗した最初の試行）は，2026-10-14 に `results/20261011_eq8_summary/` へ commit した．

## E2 の下流確認の準備（2026-10-11．p0003 S5．`244b4b4`）

- S4 の `desc_sim` は，計算が同じ既存の `CardSimRouter`（`routing: card_sim`）を使っている．実機の質問者で動かすには，次の 2 点が足りなかった．
  - MedRAG のデータ源は複数のシャードからなるので，広告（`card_embedding`）を持たない（`build_source_profiles` は 1 シャードのデータ源にしか広告を渡さない）．
  - `routing.top_m` に `medrag` のキーが無い．
- したこと（`244b4b4`．`mise run check` と同じ検査が全て通った．pytest 151 件・ruff・format・mypy）：
  - `routing.with_card_embeddings` を足し，LOSO の評価（`loso.load_loso_data`）と質問者（`experiment.run_experiment`）の両方がこれを使うようにした．
    質問者は `routing: card_sim` かつ `dataset: medrag` のとき，起動時に自己紹介文を S4 と同じ `loso.embed_descriptions`（MedCPT の文書側，fp32）で埋め込む．ノードと manifest は変えていない．
  - `routing.top_m.medrag: 3`．k は S4 と同じ規則で，4 源がそろった世界の `train`・`val`（3,064 問）の F1 が最大のものを選んだ（test は見ていない）．
    F1 は k = 1 が 0.438，k = 2 が 0.589，k = 3 が 0.637．
- 制御点の HF キャッシュの MedCPT は，固定した revision と一致している（Query `d83a36c`，Article `d05a736`．`refs/main` と `snapshots/`）．
- 実行の条件：`dataset: medrag`，`routing: card_sim`，`answer_mode: retrieval_only`，`merge: score`，`question_limit: null`．run_id は `20261011_e2_desc_sim`（日付は実行日に合わせる）．
  main の作業ツリーで deploy するので，EQ8〜EQ10 の連続実行の `ALL DONE` の後に行う．参照の実行は `results/20261007_s3_med_all_ret`．

## E2：LOSO の判定（2026-10-11．p0003 S4．`results/e2_loso_20261011_120539`）

- 事前登録（`ce97d7c`，評価の前に push 済み）のとおり，`atrium loso --split test` を MedRAG（4,599 問）・FeB4RAG（474 問）で 1 回だけ実行した．
  コードは `ce97d7c`（`eb6c12c` から journal だけが進んだもの）．結果を見て成功条件や k は変えていない．
- MedRAG の test（4 通りの除外を合わせた値）：

| 方式 | 平均問い合わせ源 | F1 | 新参源の再現率（合計） | pubmed | statpearls | textbooks | wikipedia |
|---|---|---|---|---|---|---|---|
| desc_sim | 2.75 | 0.625 | 0.830 | 0.893 | 0.776 | 0.858 | 0.704 |
| centroid_sim | 3.00 | 0.403 | 0.541 | 0.169 | 0.981 | 1.000 | 0.981 |
| ragroute-frozen | 1.62 | 0.731 | 0.354 | 0.258 | 0.798 | 0.431 | 0.308 |
| ragroute-upper | 1.64 | 0.830 | 0.799 | 1.000 | 0.365 | 0.601 | 0.644 |
| random（3 源） | 3.00 | 0.558 | 0.749 | 0.747 | 0.760 | 0.756 | 0.745 |
| all | 4.00 | 0.616 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

- 選んだ k：`desc_sim` は statpearls を除いたときだけ 2，他は 3．`centroid_sim` はすべて 3．
- **判定：成功**．`desc_sim` が (a) 2.75 ≤ 3.0，(b) 0.830 ≥ 0.8 × 0.799 = 0.639（上限との比 1.040）を同時に満たした．
  `centroid_sim` は (b) を満たさない（比 0.677）．分岐に従い，S5 は `desc_sim` で行う．
- **判定の読み方の注意**（判定は書き換えない）：
  - 判定の対象外の `random`（3 源）も (a)（3.00）と (b)（比 0.937）を満たす．この基準は「中央の分類器に頼らない」方式の下限としては弱い．
    `desc_sim` は `random` より少ない問い合わせ（2.75 対 3.00）で，新参源の再現率（0.830 対 0.749）と F1（0.625 対 0.558）が高い．
    ただし両者の差の検定は事前登録していないので，記述にとどめる．
  - `desc_sim` の新参源の再現率が上限を超えるのは，問い合わせる源が多い（2.75 対 1.64）ためで，同じ問い合わせ数での比較ではない．
    適合率と F1 は `ragroute-upper` が高い（F1 0.830 対 0.625）．
  - `ragroute-frozen`（新参源の one-hot を 0 にする）は，新参源の再現率が 0.354 で，再学習しない中央の分類器は新参源をほとんど選ばない．
  - `centroid_sim` は PubMed を除いたときだけ再現率が 0.169 と低い．PubMed は断片数が多く話題が広いので，重心がどの質問からも遠くなると考えられる（未検証）．
  - データ源が 4 つしかなく，除外は 4 通りなので，除外ごとの値の揺れは大きい（p0003 B1）．
- FeB4RAG の test（参考．判定には使わない）：`centroid_sim` は 13 通りすべてで k = 13（N−1 = 12 源なので全源）を選び，`all` と同じ値（平均 13.00 源，新参源の再現率 1.000）になった．
  `train`・`val` の F1 が k とともに単調に増えたためで，重心の類似度では削減できなかった．`ragroute-upper` は平均 9.44 源，再現率 0.874．
- 次：S5（`routing: desc_sim`，`answer_mode: retrieval_only` の実機での下流確認）．main の作業ツリーで deploy するので，EQ8〜EQ10 の連続実行が終わってから行う．
  `desc_sim` を実機で動かすには，各データ源の広告に自己紹介文の埋め込みが要る．S5 の前に，deploy の経路でそれが作られるかを確かめる．

## E2 の成功条件の事前登録（2026-10-11．評価の前に push する．p0003 S4）

p0003 S4 で固定した成功条件を写す．E1 の結果と，S3 の `train`・`val` の値を見ても変えていない．

**成功条件**：MedRAG の 4 通りの除外について，次の 2 つを**同時に**満たす方式が 1 つ以上あること．

- (a) 1 問あたりの平均問い合わせ数が 3.0 以下（`all` の 4.00 に対する削減率 25% 以上）
- (b) 新参源の再現率が，`ragroute-upper` の新参源の再現率の 80% 以上

- 判定の対象とする方式は `desc_sim` と `centroid_sim`（`loso.CANDIDATE_METHODS`）．
- 新参源の再現率は，4 通りの除外の（質問，新参源）の組を合わせて計算する（`pooled.newcomer_recall`）．4 通りそれぞれの値も併記する．
- k は除外ごとに，新参源を除いた N−1 源で，`train`・`val` の F1 が最大のものを選ぶ（MedRAG は {1, 2, 3}，FeB4RAG は {5, 7, 9, 11, 13}）．
- FeB4RAG の結果は参考にとどめ，判定には使わない（もともと関連源が多く，削減の余地が小さいため）．
- 80% は，研究計画書 §8 E2 の「上限の一定割合以上」を数値にした暫定の基準である．

**手順**

- `atrium loso --split test` を，MedRAG・FeB4RAG で **1 回だけ** 実行し，`results/e2_loso_<YYYYMMDD_HHMMSS>/` に書く（`metrics.json` と `analysis_report.md`）．
- 実行するコードは `eb6c12c`（branch `e2-loso`）とする．

**分岐**

- どちらかが成功したとき → S5（実機での下流確認）に進む．成功した方式が複数あれば，新参源の再現率が最大のものを選ぶ（同点なら平均問い合わせ数が小さい方）．
  S5 は main の作業ツリーで deploy するので，EQ8〜EQ10 の連続実行が終わってから行う．
- どちらも不成功のとき → S5 を飛ばして S6 に進み，原因の候補を記録する．

## E2 の実装（2026-10-11．p0003 S3．branch `e2-loso`）

- EQ8〜EQ10 の連続実行が main の作業ツリーで動いているので，`git worktree` の branch `e2-loso` で作業した．
  main の HEAD を進めると，連続実行の deploy と start の版が合わなくなるためである．
  push は `git push origin e2-loso:main`（早送り）で行い，ローカルの main は連続実行の後に `git merge --ff-only` で取り込む．
- 3 つの commit を作った．各 commit で ruff，ruff format，mypy，pytest（150 件）が通った．
  1. `ed06913`：`train_router` に，除外するデータ源（`exclude`）と出力先（`out_dir`）を足した．既定値は今までの動作と同じ．
  2. `8f6a557`：`RagrouteRouter(allow_unseen=True)` で，学習に無いデータ源の one-hot を 0 にして判定する（`ragroute-frozen`）．
  3. `6c1e9bc`：`src/atrium/loso.py` と `atrium loso`，`top_doc_ids` の重なりの関数（S5 用），テスト，`d0001` §5 と `d0004` の追記．
- **計画からの逸脱**：
  - p0003 S3 の 1・2（新しい `desc_sim`・`centroid_sim` の `Router` と，`RoutingConfig.similarity_top_k`）は行わなかった．
    代わりに p0004 で入れた `CardSimRouter`・`CentroidSimRouter` を使った．方式の中身（自己紹介文または重心とクエリ埋め込みの内積で，上位 k 個を選ぶ）は計画と同じである．
    同じ処理の `Router` を二重に持たないためである．
  - k は設定に置かず，除外ごとに `loso.K_CANDIDATES`（計画と同じ．MedRAG は {1, 2, 3}，FeB4RAG は {5, 7, 9, 11, 13}）から選ぶ．
    計画の「新参源の関連ラベルを k の選択に使わない」を守るには，除外ごとに N−1 源で選ぶ必要がある．
  - このため commit の分け方が計画の 1〜3 と違う（2 つめが `ragroute-frozen`）．`config.yaml` と `d0002` は変えていない．
- データ：制御点から写した `artifacts/{medrag,feb4rag}/`（manifest・queries・router）．N−1 源のルーターは `artifacts/medrag/loso/<新参源>/`（計 3.4 MB）．
  `ncbi/MedCPT-Article-Encoder` は固定した版をオフラインで読んだ．
- `train`・`val` での動作確認（S3 の完了条件．S4 の判定には使わない）．MedRAG の値は次のとおり．
  `train` はルーターの学習に使い，k の選択にも `train`・`val` を使うので，どちらも評価用の値ではない．

| 方式 | val：平均問い合わせ源 | val：新参源の再現率 | train：平均問い合わせ源 | train：新参源の再現率 |
|---|---|---|---|---|
| desc_sim | 2.75 | 0.810 | 2.75 | 0.835 |
| centroid_sim | 3.00 | 0.533 | 3.00 | 0.538 |
| ragroute-frozen | 1.69 | 0.401 | 1.63 | 0.360 |
| ragroute-upper | 1.71 | 0.812 | 1.65 | 0.845 |
| random（3 源） | 3.00 | 0.742 | 3.00 | 0.744 |

- FeB4RAG の `val`（79 問）では，`centroid_sim` が 13 通りの除外すべてで k = 13 を選んだ．候補の中で F1 が k とともに単調に増えるためである．
  N−1 = 12 源で k = 13 は全源を選ぶので，評価では `all` と同じ値（平均 13.00 源，新参源の再現率 1.000）になる．FeB4RAG は判定に使わない（p0003 S4）．
- 気づいた点（判定には使わない）：
  - `ragroute-frozen` は，新参源の再現率が 0.36〜0.40 と低い．one-hot を 0 にすると，新参源をあまり選ばない．
  - `centroid_sim` は，PubMed を除いたときだけ新参源の再現率が 0.13〜0.16 と低い．

## EQ8〜EQ10 の事前登録（2026-10-11．実行の前に push する．p0004 §8.7）

EQ6 の「次に調べる候補」のうち，文脈の通数（1 つめ）と，流し方による答えの揺れ（3 つめ）を調べる．
3 つめは，キャッシュを空にして採点し直す代わりに，同じ条件を今の HEAD で流し直して EQ5 と比べる．2 つめ（裾を縮める）は今回は行わない．

**共通**
- ルーティングは oracle．判定モデルは qwen3:8b（EQ4 で合格したもの）．他の設定は EQ5・EQ6 と同じ．
- 文脈の通数は `data.enronqa.k_context` で変える．質問者の取得数・統合後の数・専門家が使う数は，すべてこの値になる（`requester.py` の `_k_ret`・`_k_rerank`・`_k_local`）．
  `local_answer.k_context` と `retrieval.k_ret` は EnronQA では使われない．k はその場の処理だけで使うので，データの作り直しは要らない．
- 各回の前に deploy し直し，KV キャッシュが空の状態から始める（EQ6 の手順の変更と同じ）．
- 連続実行は `results/20261011_eq8_summary/eq8_chain.sh`，実行の一覧は同じ場所の `eq8_specs.txt`．EQ8・EQ9 → 採点 → EQ10 の順に流す．
- 失敗した問が 1% を超えた実行は集計から除き，記録する（§12 の 4）．EQ8・EQ9 は，失敗した問が残ったら同じ run_id で 1 回だけ再開する（EQ5 と同じ）．
  EQ10 は待ち時間を測るので再開しない．
- 連続実行の途中では commit しない（HEAD が進むと，次の deploy と start の版が合わなくなるため）．

**EQ8（文脈の通数と正答率．test の 2,940 問，parallel 30，各 1 回）**
- 条件：B1・B2・B2' のそれぞれで k = 1, 3, 5．計 9 実行．
- k = 5 も今の HEAD で流し直す．EQ5 の B1 は，質問を並べ替える前の版（`ae5b099`）で動いた．EQ6 の 7 のとおり，答えは同じ専門家で前に何を処理したかで変わる．
  そのため EQ5 を k = 5 として使うと，k の効果と流し方の違いが混ざる．
- 比較：各条件の中で，k = 1 と k = 5，k = 3 と k = 5 を McNemar の正確検定で比べる（計 6 組）．Holm 法で 6 組の多重性を補正し，補正後の p < 0.05 のときだけ差があるとする．
  対象は CPU の専門家に置かれた受信箱の 1,940 問（EQ5 と同じ．B2 では GPU に置かれた受信箱には 8B が答えるため）．GPU の受信箱の値は参考として出す．
- 副指標：
  - ひな形の文だけの回答を不正解に直した正答率．`final_answer` に，`prompts.py` の回答形式の例の文 `The answer to the question` を含む回答を指す（EQ5・EQ6 と同じ文字列）．
  - 外に出た本文の数（B1）と，入力トークン数の中央値．
- 予想（判定には使わない）：k を減らすと正答率は下がる．EQ1 では，正解のメールが上位 5 通に入る割合が 0.967 だった．上位 1 通に限れば，これより低い．

**EQ9（流し直しによる揺れ．test の 2,940 問，parallel 30）**
- 追加の実行は B3（k = 5）の 1 回だけ．EQ8 の k = 5 の B1・B2・B2' と合わせた 4 条件を，EQ5 の同じ条件と比べる．
- 指標：`final_answer` が一致した問の割合，正答率の差，McNemar の p（CPU の受信箱の 1,940 問）．
- 判定：同じ条件の 2 回の正答率の差の絶対値の最大を「流し直しの幅」とする．EQ5 の条件間の差のうち，この幅より小さいものは「流し方の揺れと区別できない」と書く．
- H-B1 の再判定：B2（k = 5）と B3（k = 5）を，CPU の受信箱で McNemar の正確検定で比べる．基準は EQ5 と同じ（p ≥ 0.05 で同等）．EQ5 の判定は書き換えず，両方を並べて記録する．
- p0004 §8.7 は「温度 0 で正誤は決定的なので，正答率の実行は各条件 1 回」としていた．EQ6 の 7 でこの前提が崩れたので，その大きさを測る．

**EQ10（待ち時間．各受信箱の先頭 2 問の 300 問，各 3 回）**
- 条件：B2 と B2' の k = 1, 3（parallel 4）．B1 と B4 の k = 5（parallel 1）．計 6 条件 × 3 回 = 18 回．
- k = 5・parallel 4 の値には EQ6 の 3 回を使う．EQ6 から後の `src` の差分は Hugging Face の版の固定だけで，固定した版は EQ6 で使った版と同じである．
- 指標と判定は EQ6 と同じ：e2e_s と generate_s の p50・p95．3 回の p50 の幅が重ならないときだけ「差がある」とする．比べる組は次の 5 つに限る．
  - B2 の k = 1 と k = 5，B2 の k = 3 と k = 5，B2' の k = 1 と k = 5，B2' の k = 3 と k = 5．B2・B2' は CPU の受信箱に限った値で判定する．
  - B1 と B4（parallel 1）．質問者の Ollama での順番待ち（EQ6 の内訳の 3）を除いて，答える場所の違いだけを見る．
- 参考：B1 の parallel 1 と parallel 4（EQ6）の差を，質問者での順番待ちの大きさとして出す（判定はしない）．
- 正答率は EQ10 では採点しない（EQ8 で測る）．

**所要時間の見込み**：EQ8・EQ9 で約 12 時間，採点で約 2 時間，EQ10 で約 4 時間．EQ5 と EQ6 の実測（`run_meta.json` の時刻と `eq6.log`）から見積もった．

## Hugging Face のモデルの版の固定（2026-10-11．`4d4c6ad`）

- EQ6 が終わったので，`pin-hf-revisions` の commit を main に取り込んだ（cherry-pick．main は直線の履歴なので merge commit は作らない）．取り込み済みの branch は消した．
  - `src/atrium/hf_revisions.py` に 12 モデルの commit を固定した．読み込む箇所（`encoders.py`，`requester.py` の reranker，`cli.py` の `fetch-models`）は，すべて `pinned_revision()` を通す．表に無いモデルは `KeyError` で止まる．
  - 固定した commit は，2026-10-10 に制御点のキャッシュの `refs/main` が指していた版である．EQ0〜EQ6 を含むこれまでの実験は，すべてこの版で動いた．したがって，固定によって結果は変わらない．
- 確認：
  - ruff，ruff format，mypy，pytest（137 件）が通った．
  - 制御点の `$DATA_DIR/.cache/huggingface/hub` に，12 モデルすべての固定した snapshot があり，`refs/main` も一致した．
  - `atrium-full`（`4d4c6add0cd7`）を `--network none`・`HF_HUB_OFFLINE=1` で起動し，12 モデルすべてを固定した版から解決できた．
    SGPT（5.8B）は config と tokenizer まで，他の 11 個は重みまで読み込んで推論した．埋め込みの次元は 768 または 1024，reranker のスコアは 0.995．
  - medrag の既定の設定で再配備した．配置先の専門家 16 台はすべて `4d4c6add0cd7` のイメージで起動し，`/healthz` が応答した．残りの 14 台は配置に入っていない．
- 運用上の注意：
  - 制御点が pull するのは `atrium-full` だけである．そのため，制御点に古い `atrium-node:latest`（`c2574bf1f3d9-dirty`）が残っていた．専門家は deploy で最新を pull するので，実験には影響しない．制御点のものも pull し直した．
  - この記録の commit で HEAD が進む．次の実験の前に `mise run deploy` が要る（start.sh は，イメージの版が HEAD と一致しないと止まる）．
- gpu2 の一時ディレクトリ `/mnt/data-raid/ktakahashi/tmp_atrium_e2e`（約 110 GB）は，2026-10-11 の時点で既に無かった．

## EQ6：待ち時間（確定．2026-10-10 23:17 に全 18 回が終了．`results/20261010_eq6_*`，集計は `results/20261010_eq6_summary/`）

- 終わった回：6 条件（B1・B1 flood_score・B2・B2'・B3・B4）を 3 回ずつ，計 18 回．どれも失敗が 0（300 / 300 問）．
- 受信箱の配置は 18 回すべてで同じだった（`placement.json` の専門家と受信箱の対応が一致）．
  専門家ごとの受信箱を並べたハッシュは `c16d1686361f`（18 回で計算）．途中経過で書いた `cbbcb93c9a52` は別の並べ方で計算した値で，その計算では先の 15 回が一致していた（B2' の 3 回にはこの計算を使っていない）．
- コードの版（`run_meta.json` の `git_head`）：
  - `193ac3b`：B2・B3 の 1 回目．`f540690`：B1 の 3 回，B2・B3 の 2・3 回目，B4 の 3 回．
  - `844cc46`：B1 flood_score の 1 回目．`0b2386b`：B1 flood_score の 2・3 回目，B2' の 1 回目．`86b208d`：B2' の 2・3 回目．
  - `git diff 193ac3b 86b208d -- src` は空で，18 回はすべて同じ `src` で動いた（版の違いは journal と集計スクリプトだけ）．
- 集計：`eq6_summary.py`（事前登録の判定），`eq6_breakdown.py`（時間の内訳），`eq6_b2_vs_b2p.py`（B2 と B2' の比，裾，再現性，EQ5 との答えの一致，KV キャッシュの影響）．出力は同じディレクトリの `.out`．

| 条件 | 有効な回 | e2e p50 [3 回の幅] (s) | e2e p95 (s) | 内訳（CPU の受信箱．prefill / decode / その他，中央値） | 出力トークン |
|---|---|---|---|---|---|
| B1（質問者の llama3.1:8b） | 3 | 5.70 [5.67〜5.84] | 8.59 | 1.05 / 1.03 / 3.25 | 28 |
| B1 flood_score（2 源へ問い合わせ，スコアで統合） | 3 | 5.73 [5.71〜5.83] | 10.58 | 1.11 / 1.17 / 3.32 | 30 |
| B2（CPU は qwen3:0.6b） | 3 | 18.82 [18.79〜18.82]（CPU の受信箱 23.73 [23.73〜23.76]） | 44.60（CPU 50.12） | 13.5 / 8.6 / 0.18 | 102 |
| B2'（CPU は qwen3:1.7b） | 3 | 30.08 [30.03〜30.22]（CPU の受信箱 37.36 [37.29〜37.63]） | 75.62（CPU 81.52） | 30.4 / 5.3 / 0.18 | 37 |
| B3（質問者の qwen3:0.6b） | 3 | 2.49 [2.44〜2.51] | 3.54 | 0.16 / 0.85 / 1.39 | 100 |
| B4（GPU の専門家へ委託） | 3 | 1.72 [1.72〜1.72] | 3.72 | 0.92 / 0.46 / 0.16 | 29 |

「その他」は generate_s − prefill − decode で，Ollama の中での順番待ち・通信・委託の往復を含む．B2 の GPU の受信箱（llama3.1:8b）は e2e p50 1.60 秒で，B4 と同じ程度．B2' の GPU の受信箱も同じモデルで 1.60 秒（B2 と同じ）．

**判定（事前登録の 4 組．3 回の p50 の幅が重ならないときだけ差がある）**
- B2 と B3：**差がある（B3 が速い）**．幅は [18.79, 18.82] と [2.44, 2.51] で重ならない．同じ 0.6B でも，答える場所（データの持ち主の CPU か，質問者の GPU か）で待ち時間が約 7.6 倍違う．
- B1 と B4：**差がある（B4 が速い）**．幅は [5.67, 5.84] と [1.72, 1.72] で重ならない（約 3.3 倍）．
  ただしこの差は `parallel: 4`・`llm.requester_num_parallel: 2` の組での値で，主に質問者の Ollama での順番待ち（下の 3）による．計算だけなら約 2.1 秒と約 1.4 秒．
- B1 と B1 flood_score：**差があるとは言えない**．幅は [5.67, 5.84] と [5.71, 5.83] で重なる．
  - p95 は flood_score が約 2 秒長い（8.59 → 10.58）．e2e と generate の差は約 0.1 秒で，問い合わせ（probe_s の p95 0.14〜0.15 秒）ではなく生成が長くなったためである．
    2 源の断片をスコアで混ぜるので入力が長くなり（入力トークンの p95 3,257 → 3,465，最大 5,062 → 6,555），出力も伸びた（出力トークンの p95 66〜74 → 87〜105）．
  - 代価：待ち時間は縮まない一方で，クエリを受け取る源は 2 倍（1.00 → 2.00），外に出る断片も 2 倍（5 → 10 件），受信量も 2 倍（8.3 → 17.9 KB）になる．再現率は 0.920 で，8% の問で関連する源を取りこぼす．
- B2 と B2'：**差がある（B2 が速い）**．幅は [18.79, 18.82] と [30.03, 30.22] で重ならない（約 1.6 倍）．CPU の受信箱に限っても [23.73, 23.76] と [37.29, 37.63] で重ならない．
  同じ問・同じ専門家・同じ入力トークン数で比べると，CPU の受信箱の 200 問すべてで B2' の方が遅かった（e2e の比の中央値 1.62）．
- 別件：B1 の 1 回目に，出力が上限の 2,048 トークンに達した問が 1 問あった（e2e 92 秒）．p50・p95 には影響しないが，生成が止まらなくなる例として残す．

**内訳から分かったこと**
1. B2 の CPU の受信箱の待ち時間は，CPU での計算そのものでほぼ決まる．
   - prefill 13.5 秒（約 2,138 トークン），decode 8.6 秒（約 102 トークン）．
   - CPU の専門家 20 台（i5-8350U，8 スレッド）の速さはそろっていた：prefill 5.7〜7.0 ms/入力トークン，decode 76〜96 ms/出力トークン．
   - qwen3:0.6b は llama3.1:8b（約 30 トークン）の約 3.5 倍の長さで答えるので，decode の時間も延びる．B3 も 100 トークンなので，これはモデルの性質である．
2. B2 の裾（p95 50 秒，最大 145 秒）は，専門家の Ollama の順番待ちによるもの．
   - 専門家の Ollama は要求を 1 件ずつ処理する（`OLLAMA_NUM_PARALLEL` が 1）．
   - 最も遅い問（192.168.13.101 の `merriss-s` 61-0，145 秒）は，計算が約 24 秒，残りの約 121 秒が順番待ちだった．
     直前に同じ専門家へ，入力の長い `slinger-r` の 2 問（5,082・6,080 トークン．300 問の最大は 6,080）が届いており，その prefill（49.5・71.3 秒）の後ろで待った．
   - EQ6 の 300 問では，CPU の専門家はどれも受信箱を 5 個ずつ持つ．このため，専門家ごとの差は受信箱の数ではなく，入力の長い問がどの専門家に重なったかで決まる．
3. B1・B3 の「その他」（3.25 秒・1.39 秒）は，質問者の Ollama の中での順番待ちと考えられる．
   - 質問者は 4 問を同時に送る（`parallel: 4`）が，質問者の Ollama は 2 件までしか同時に処理しない（`llm.requester_num_parallel: 2`）．
   - B1 の計算（prefill ＋ decode）は約 2.1 秒で，B4 の約 1.4 秒との差は小さい．B4 が速い主な理由は，生成を GPU の専門家 10 台に分けて，順番待ちがほぼ無いこと（その他 0.16 秒）にある．
   - したがって B1 と B4 の差は，「答える場所」よりも「生成に使える GPU の数」の差を強く反映する．B1 の値は `requester_num_parallel` と `parallel` の組に依存するので，判定を書くときはこの条件を付ける．
4. B2' が遅いのは prefill による．出力が短くなる分では取り返せない．
   - CPU の受信箱の中央値で，prefill 13.5 → 30.4 秒，decode 8.6 → 5.3 秒，出力 102 → 37 トークン．入力のトークン数は同じ（中央値 2,138）．
   - 同じ問どうしの比（1 回目，200 問）：prefill 2.24 倍［1.75〜2.57］，decode の 1 トークンあたり 1.72 倍，出力トークン 0.37 倍．
   - prefill の 1 トークンあたりの時間は，B2 6.29 ms（p5〜p95 5.24〜7.89），B2' 14.12 ms（12.26〜16.68）．B2' でも専門家 20 台の中央値は 12.85〜15.17 ms でそろっていた．
   - RAG のように入力が長く（約 2,100 トークン）出力が短い場面では，CPU での待ち時間は入力側の計算でほぼ決まり，モデルが大きくなるとその分だけ延びる．
     「大きいモデルは答えが短いので速いかもしれない」という見込みは，この入力の長さでは成り立たなかった．
5. B2' の裾は B2 と同じ問・同じ専門家で起き，順番待ちが約 2 倍に延びた．
   - 最も遅い 3 問は，B2 でも B2' でも 192.168.13.101 の `merriss-s` 61-0，`slinger-r` 80-0，`linder-e` 41-0 で，6 回とも同じ順だった．
   - 61-0：B2 145 秒（計算 24 秒，順番待ち 121 秒）→ B2' 278〜281 秒（計算 43〜46 秒，順番待ち 233〜235 秒）．
     80-0（入力 6,080 トークン）：prefill 71 秒 → 125〜128 秒．41-0：順番待ち 42 秒 → 84〜93 秒．
   - 専門家の Ollama は 1 件ずつ処理する（`OLLAMA_NUM_PARALLEL` が 1）ので，前の問の計算が長くなるほど，同じ並び順でも後ろの問の順番待ちがそれに比例して延びる．p95 は 44.60 → 75.62 秒．
6. temperature 0 の再現性：B2 も B2' も，3 回の答え（`final_answer`）と出力トークン数が 300 / 300 問で一致した．
   - 各回は deploy の直後（KV キャッシュが空）から，同じ問を同じ順で同じ専門家へ送るので，生成は完全に再現する．回ごとの待ち時間の差は計算の揺らぎだけによる．
   - 出力が上限の 2,048 トークンに達した問は，B2・B2' とも 0．
   - `The answer to the question` を含む答え（ひな形の繰り返し）は，CPU の受信箱で B2 133 / 200，B2' 0 / 200．1.7B ではひな形の繰り返しが起きない．
     （EQ5 の 35.9% は CPU の受信箱の 1,940 問でひな形の文をそのまま答えにした割合で，数え方も対象の問も違う．）
7. **KV キャッシュの状態で答えが変わる**（EQ5 の正答率の解釈に関わる）．
   - KV キャッシュに当たった無効な B2 の 2・3 回目（`results/20261010_eq6_invalid_warmcache/`）は，有効な 1 回目と答えが一致したのが 2・3 回目とも 221 / 300 問だった（CPU の受信箱 132 / 200，GPU の受信箱 89 / 100）．
     temperature 0・同じプロンプト・同じ重み・同じ専門家でも，プロンプトの KV を計算し直すか再利用するかで生成が分かれる．GPU の 8B でも起きる．
   - EQ5（同じ設定で parallel 30．2,940 問を続けて流した）の同じ 300 問と比べると，届いた専門家は 300 / 300 で同じだが，答えが一致したのは
     B2 278 / 300（CPU 182 / 200，GPU 96 / 100），B2' 278 / 300（CPU 181 / 200，GPU 97 / 100）だった．
     EQ5 では，前の問とプロンプトの先頭（システムの指示など）が共通なので KV を再利用した可能性があり，上と同じ仕組みで答えが分かれたと考えられる（確かめてはいない）．
   - 含意：EQ5 の各問の答えは，その問の前に同じ専門家で何が処理されたかに依存する．EQ5 の正答率の差のうち小さいもの（H-B1 の 0.297 vs 0.260 など）は，この揺れを含む．
     EQ5 の H-B1 の原因を調べたときに挙げた「CPU と GPU の計算の違い」に加えて，KV キャッシュの再利用の有無も生成を分ける要因である．
     正答率への影響（向きと大きさ）は，EQ6 では採点しないので分からない．

**考察（RQ-B．EQ5 の正答率と合わせる．暫定）**
- CPU の受信箱で比べると：B4 は正答率 0.789・p50 1.70 秒で，露出は 1 問あたり 5 通（相手は対応づけた GPU の専門家 1 台）．
  B1 は 0.789・5.58 秒で，露出は 5 通（相手は質問者）．B2 は露出 0 だが，0.297・23.73 秒．B2' も露出 0 で，0.650・37.36 秒．B3 は 2.51 秒だが 0.260．
- 露出を 0 にする（B2）代価は，正答率（0.789 → 0.297）と待ち時間（1.70 → 23.73 秒，約 14 倍）の両方に出る．その待ち時間の大半は，CPU での長い入力の prefill である．
- CPU の専門家のモデルを大きくすると（B2 → B2'），露出 0 のまま正答率は 0.297 → 0.650 に上がる．一方で待ち時間は 23.73 → 37.36 秒（約 1.6 倍．B4 の約 22 倍）に延びる．
  それでも正答率は B1・B4 の 0.789 に届かない．露出 0 の条件では，正答率と待ち時間が引き換えになる．
- 次に調べる候補（事前登録はまだしていない）：
  - B2・B2' の文脈の通数や長さを減らしたときの待ち時間と正答率．prefill は入力のトークン数にほぼ比例するので待ち時間は縮むはずで，prefill の割合が大きい B2' ほど効く．正答率がどれだけ下がるかは分からない．
  - 裾を縮める方法（入力の長い問を専門家の間で散らす，専門家の Ollama の同時処理数を増やす）．
  - KV キャッシュの状態による答えの揺れが正答率に与える影響（EQ5 の条件を，各問の前にキャッシュを空にして採点し直す）．
- 注意：parallel 4 で測った値である．各受信箱の先頭 2 問だけを使っている．各回は「重みは読み込み済み，KV キャッシュは空」の状態から測っている．

## EQ6 の事前登録（2026-10-10．実行の前に push する．p0004 §8.7）

- 質問：test の抜き出しの各受信箱の先頭 2 問（`experiment.question_limit: 2`．150 受信箱 × 2 = 300 問．全ての受信箱が 5 問以上ある）．
- 条件：EQ5 の 6 条件（B1，B1 flood_score，B2，B2'，B3，B4）．`experiment.parallel` は既定の 4．他の設定は EQ5 と同じ．
- 手順：条件ごとに 1 回 deploy し，同じ設定で 3 回続けて実行する（run_id `20261010_eq6_<条件>_r<1〜3>`）．
  deploy は，その条件で答える Ollama のモデルを実験と同じ num_ctx で読み込んでおく．
  EQ5 の宿る型では，各専門家の最初の回答に読み込みの時間が上乗せされた（中央値 3〜5 秒，最大 59 秒）．
  300 問では約 1 割の問と 1 回目の実行だけに入るため，deploy で読み込むように直した．
- 指標：e2e_s と generate_s の p50・p95（300 問）．3 回の中央値を代表値とし，最小〜最大を幅として出す．
  回ごとの値もすべて記録する（順序の影響を見るため）．B2・B2' は CPU の専門家に置かれた受信箱の質問に限った値も出す．
- 判定：2 条件の待ち時間は，3 回の p50 の幅（最小〜最大）が重ならないときに限り「差がある」とする．比べる組は次の 4 つに限る．
  B2 と B3（同じ 0.6B．答える場所だけが違う．H-B1 の待ち時間），B1 と B4（同じ 8B．答える場所が違う），
  B2 と B2'（専門家のモデルの大きさ），B1 と B1 flood_score（ルーティング）．
- 失敗した問が 1% を超えた実行は集計から除き，記録する（§12 の 4）．
- 正答率は EQ6 では主張に使わず，採点もしない（正答率は EQ5 の 2,940 問で測った）．
- **手順の変更（2026-10-10 18:35．B2 の 3 回と B3 の 1 回目の途中で変更．判定の規則と比べる組は変えない）**：
  各回の前に deploy し直す（コンテナを作り直す）．条件ごとに 1 回だけ deploy すると，2・3 回目は 1 回目と同じ問が
  同じ専門家に届くため，Ollama のプロンプトの KV キャッシュに当たる．B2 では，CPU の専門家の prefill が
  1 回目 14〜17 秒から 2・3 回目 0.08 秒に縮み（入力のトークン数は同じ 2107.5，decode は比 1.00），
  e2e の p50 は 18.8 → 7.4 秒になった．これは同じ条件を 3 回測ったものではないので，B2 の 2・3 回目は無効とし，
  集計の対象から外した（`results/20261010_eq6_invalid_warmcache/` に残す．ログ類は除く）．条件をまたいだ持ち越しは無い
  （deploy は `docker compose up -d --force-recreate` で Ollama のプロセスを作り直し，KV キャッシュはその中にだけある）．
  B2 の 1 回目と B3 の 1 回目は deploy の直後に始まったので有効．この変更で各回は「重みは読み込み済み，KV キャッシュは空」から測る．

## EQ4 の検証 2：参照モデルとの一致（2026-10-10．`results/20261010_eq4_judge_kappa`）

- EQ5 の 6 実行から 50 件ずつ（計 300 件）を qwen3:14b（制御点と wafl500〜509）で採点し直した．
  **κ = 0.828**（一致率 0.915，n = 293．参照モデルが判定できなかった 7 件を除く）．基準（κ ≥ 0.8）を満たし，検証 1 と合わせて**合格**．
  主指標は判定モデル（qwen3:8b）の採点のままとし，選択式への切り替えはしない．
- 実行の途中で 2 回止まった．原因は Ollama 0.35 が返す 500 `prediction aborted, token repeat limit reached` で，qwen3:14b が
  300 件中 2 件（`b2:610-1`，`b2_1p7b:54698-0`）で同じトークンを繰り返して打ち切られたもの．温度 0 なのでどの Ollama でも同じく打ち切られる．
  `judge_items` を直し，この 500 は判定できなかった出力として扱い，他の失敗は別の Ollama へ振り替えて送り直すようにした（f9925cb，c2574bf）．
- 診断用に同じ 300 件を採点したときは κ = 0.835（n = 294）で，判定できなかった件の数が 1 件違った．
  温度 0 でも GPU が違うと生成が揺れるため（EQ5 の H-B1 と同じ現象）．どちらも基準を満たす．
- 注意：検証 1・2 はどちらも，中身の無い回答（ひな形の文だけ）を正解とする偽陽性を測れない．EQ5 の 0.6B の条件の正答率は
  この偽陽性で約 0.04 高い（下の EQ5 の記録）．

## EQ5：RQ-B の判定（2026-10-10．`results/20261009_eq5_*`，判定モデル qwen3:8b）

受信箱の置き場所で分けた結果（CPU の専門家に置かれた受信箱 1,940 問，GPU の専門家に置かれた受信箱 1,000 問）．
B2 と B2' の `results.jsonl` には再試行の前の失敗の行（35 行・4 行）が残るが，`read_results` は同じ qid の最後の行を採るので集計には入らない．
待ち時間は parallel 30 のもので主張には使わない（EQ6 で測る）．

| 条件 | 置き場所 | 正答率 [95%CI] | e2e p50 / p95 (s) | 外に出た本文（件/問） |
|---|---|---|---|---|
| B1（質問者の llama3.1:8b） | cpu / gpu | 0.789 [0.770, 0.806] / 0.754 [0.726, 0.780] | 57.9 / 78.9 | 5 |
| B1 flood_score | cpu / gpu | 0.659 / 0.666 | 59.4 / 78.0 | 10 |
| B2（CPU は qwen3:0.6b，GPU は llama3.1:8b） | cpu / gpu | 0.297 [0.277, 0.318] / 0.750 [0.722, 0.776] | 75.7 / 394.8（cpu） | 0 |
| B2'（CPU は qwen3:1.7b） | cpu / gpu | 0.650 [0.628, 0.671] / 0.749 | 101.9 / 374.5（cpu） | 0 |
| B3（質問者の qwen3:0.6b） | cpu / gpu | 0.260 [0.241, 0.280] / 0.253 | 23.2 / 36.4 | 5 |
| B4（CPU の専門家が GPU の専門家 1 台に委託） | cpu / gpu | 0.789 [0.770, 0.807] / 0.747 | 17.4 / 45.4（cpu） | 5 / 0（全問の平均 3.30） |

| 比較（McNemar の正確検定） | 置き場所 | 正答率 A vs B | A だけ正解 / B だけ正解 | p |
|---|---|---|---|---|
| B2 vs B3（H-B1） | cpu | 0.297 vs 0.260 | 236 / 164 | 3.7e-4 |
| B2 vs B1（H-B2 (a)） | cpu | 0.297 vs 0.789 | 48 / 1001 | 9e-233 |
| B4 vs B1 | cpu | 0.789 vs 0.789 | 46 / 45 | 1.00 |
| B2' vs B1 | cpu | 0.650 vs 0.789 | 99 / 368 | 2e-37 |
| B1 vs B3（モデルの大きさ） | cpu | 0.789 vs 0.260 | 1067 / 42 | 7e-258 |
| B2 vs B1（同じ 8B，答える場所だけが違う） | gpu | 0.750 vs 0.754 | 20 / 24 | 0.65 |
| B1 flood_score vs B1 oracle | 全問 | 0.662 vs 0.777 | 120 / 459 | 1e-47 |

**判定**（事前登録の基準）
- H-B1：**支持されない**（p = 3.7e-4 < 0.05）．事前登録どおり原因を調べた．
  - プロンプト長・切り詰め：B2 と B3 のプロンプトは 1,940 問すべてで同じトークン数（中央値 2,596，最大 12,458．num_ctx 16,384 の内）で，文脈はどちらも 5 通．
  - 打ち切り：num_predict（2,048）に達したのは B2 4 問・B3 6 問だけ．
  - 差の出どころ：qwen3:0.6b はプロンプトのひな形の文 `The answer to the question` をそのまま答えにすることが多く，その割合が
    B2（CPU で動く）35.9%，B3（制御点の GPU で動く）45.2% と違った．両方とも中身のある回答を返した 918 問に限ると 0.411 vs 0.403（p = 0.63）で差は無い．
    temperature 0・同じプロンプトでも最終の回答が一致したのは 736 / 1,940 問だった．qwen3:0.6b の manifest の digest は制御点・CPU の専門家・GPU の専門家で同じ（`7df6b6e09427a769`）なので，同じ重みでも CPU と GPU の計算の違い（浮動小数の演算順など）で生成が分かれたと考えられる．
  - 解釈：「誰が答えるか」そのものの効果ではなく，0.6B の出力の不安定さ（ひな形の繰り返し）が計算機器の違いで揺れたもの．
- H-B2：**支持**．(a) CPU の受信箱で B2 0.297 < B1 0.789（p = 9e-233）．(b) 全問で B1 − B4 = 0.002 < B1 − B2 = 0.326．
  (c) B4 の外に出た本文は 1 問あたり 3.30 件で B1 の 5 件より少ない（CPU の受信箱では 5 件のまま．ただし受け取るのは対応づけた GPU の専門家 1 台）．
- 参考：GPU の受信箱では，同じ 8B で答える場所だけが違う B2 と B1 は同等（0.750 vs 0.754，p = 0.65）．CPU の専門家を 1.7B にすると（B2'）0.650 まで上がるが B1 には届かない．

**判定モデルの偽陽性（EQ4 の検証 1 では見つからなかったもの）**
- ひな形の文だけの回答（中身が無い）を，qwen3:8b は約 1 割正解とした（B2 cpu 71 / 696，B3 cpu 88 / 877，B3 gpu 36 / 443．他の条件にこの回答は無い）．
  検証 1 は付属の別解と誤答だけを使うので，中身の無い回答の偽陽性を測れない．
- これらを不正解に直しても結論の向きは変わらない：B2 vs B3（cpu）0.261 vs 0.215（p = 1e-6），B2 vs B1（cpu）0.261 vs 0.789．
  影響を受けるのは 0.6B の条件（B2 の cpu・B3）だけで，正答率を約 0.04 高く見積もっている．

## EQ3：RQ-A の実機の確認と EQ4 の検証 1（2026-10-09〜10．`results/20261009_eq3_*`，`results/20261009_eq4_judge_validate`．2026-10-13 に journal へ追記）

結果はコミット（`ae5b099`，`343f0f5`，`6ddbe65`）にだけ書いてあり，journal に判定が無かったので，2026-10-13 に同じ値で追記した．判定は事前登録（下の「EQ3〜EQ5 の事前登録」）の基準による．

**EQ3**（retrieval_only，test の 2,940 問，m = 2，parallel 30）

| 方式 | 実機の再現率（正解の受信箱が選ばれた割合） | EQ2 の hit@2 | 差 | 失敗 | e2e p50 / p95 (s) | 事前の公開情報 |
|---|---|---|---|---|---|---|
| term_sketch | 0.3588 | 0.359 | 0.000 | 0 | 0.47 / 0.96 | 0.83 MB |
| multi_centroid | 0.4401 | 0.440 | 0.000 | 0 | 3.52 / 4.83 | 73.6 MB |
| flood_score | 0.8041 | 0.804 | 0.000 | 0（下記） | 92.2 / 107.2 | 0 |

- 判定：**一致**．3 方式とも，実機の再現率が EQ2 のオフラインの hit@2 と 0.01 以内（小数 3 桁で同じ）．実機の実装はオフラインの評価と一致している．
- flood_score は，最初の実行で 175 問が `ReadError`（30 台への同時の probe で，待機中の接続を閉じられたもの）で失敗した．`.exit` に終了コード 2 を残す修正（`719a0bc`）の後に，
  同じ run_id で失敗した問だけを流し直して，失敗 0 になった（`6ddbe65`）．`results.jsonl` には最初の失敗の行が残る（3,115 行）が，集計は同じ qid の最後の行を使う．
- 1 問あたりにクエリを受け取ったデバイスは，公開情報の 2 方式で約 2 台，flood_score で 30 台（全台）．外に出た本文は全方式で 10 件／問（2 受信箱 × 5 通）．
- 待ち時間は parallel 30 のもので，主張には使わない（flood_score の 92 秒は 30 台への probe の待ち行列による）．

**EQ4 の検証 1**（判定モデル qwen3:8b，EnronQA の原論文の付録 B.6 のプロンプト）
- 付属の別解 500 件を正解とした割合 **0.956**，誤答 500 件を不正解とした割合 **0.984**，判定できなかった出力 0．基準（どちらも 0.95 以上）を満たし，**合格**．
- この判定モデルで EQ1 の回答を採点した：B1 0.813，B2 0.420（各 150 問）．
- 検証 1 では，中身の無い回答（ひな形の文だけ）の偽陽性は測れない（EQ5 の記録を参照）．

## EQ1：実機の健全性（2026-10-09．受信箱ごとに 1 問の計 150 問，routing=oracle）

| run | 条件 | 失敗 | e2e p50 / p95 (s) | 外に出た本文（件/問） | 備考 |
|---|---|---|---|---|---|
| `20261009_eq1_oracle_retrieval` | retrieval_only | 0 | 0.01 / 0.02 | 5 | 正解のメールが上位 5 通に入った割合 0.967 |
| `20261009_eq1_oracle_b1` | B1 snippet_return（質問者の llama3.1:8b） | 0 | 5.6 / 9.4 | 5 | プロンプトの中央値 2,035 トークン．"Answer:" の行が無い出力 29 問（出力の全体を採点に回す） |
| `20261009_eq1_oracle_b2` | B2 local_answer | 0 | 20.6 / 45.5 | 0 | GPU の専門家は llama3.1:8b を GPU（100%），生成の中央値 2.0 秒．CPU の専門家は qwen3:0.6b を CPU（100%），24 秒 |

- 30 台の専門家に 5 個ずつ受信箱を配った．GPU の専門家として wafl500〜509 の 10 台を検出し，CPU の 20 台を 2 台ずつ対応づけた（B4 の委託先）．
- 制御点の GPU には質問者の llama3.1:8b だけが載る（クエリ埋め込みは事前計算なので，§8.1 の VRAM の懸念は起きない）．
- 所要時間の見積もりの更新：B2 は experiment.parallel 4 では 150 問に約 25 分かかり，全 2,940 問では 8 時間を超える．EQ5 では parallel を 30 にする（下の事前登録）．

## EQ3〜EQ5 の事前登録（2026-10-09．評価の前に push する．p0004 §8.6・§8.7）

**共通**
- 評価の質問は test の抜き出し 2,940 問（受信箱あたり最大 20 問．他の受信箱にほぼ同じメールがある質問は除いた．V3）．各条件 1 回．
- `experiment.parallel` は EQ3・EQ5 の全ての条件で 30 にする（既定の 4 では CPU の専門家 20 台のほとんどが待つだけになり，
  宿る型の全問に 8 時間以上かかるため）．待ち時間の主張には使わず，待ち時間は EQ6（300 問，既定の 4，各条件 3 回）で測る．

**EQ3（RQ-A の実機の確認．retrieval_only）**
- 条件：EQ2 の hit@1 が高い公開情報の 2 方式（multi_centroid，term_sketch）と flood_score．m = 2．
- 基準：実機のデータ源の選択の再現率（正解の受信箱が選ばれた割合）が，EQ2 のオフラインの hit@2 と 0.01 以内で一致すれば，
  実機の実装がオフラインの評価と一致しているとする．一致しなければ原因を調べて記録する．

**EQ4（判定モデルの検証．§8.6）**
- 判定モデルは qwen3:8b，プロンプトは EnronQA の原論文の付録 B.6．判定できない出力は不正解として数え，割合を記録する．
- 検証 1（EQ5 の前）：test の付属の別解と誤答を 500 件ずつ採点する．別解を正解とした割合と誤答を不正解とした割合が，どちらも 0.95 以上で合格．
- 検証 2（EQ5 の後）：EQ5 の各条件から同じ数ずつ抜き出した 300 件を，参照モデル qwen3:14b で採点し直す．Cohen の κ が 0.8 以上で合格．
  gpu2 はディスクの空きが少なく，データとモデルは制御点に置く指示があるので，参照モデルは制御点と wafl500〜509 で動かす（§8.6 の規定どおり 14B 級）．
- 切り替え規則：検証 1 か 2 が不合格なら，主指標を選択式（正解 1 個と誤答 2 個の 3 択．原論文に無い派生指標）に切り替え，判定モデルの結果は副指標にする．
  選択式は追加の回答の実行を要するので，切り替えたときにだけ実装して実行する．

**EQ5（RQ-B．ルーティングは oracle に固定）**
- 条件：B1 snippet_return（質問者の llama3.1:8b），B2 local_answer（CPU の専門家は qwen3:0.6b，GPU の専門家は llama3.1:8b），
  B2' local_answer（CPU の専門家を qwen3:1.7b にする．`llm.expert_model` をこの実行だけ変える），B3 snippet_return_small（質問者が qwen3:0.6b），
  B4 delegate_gpu．加えて，実機に実装した非オラクルの方式で EQ2 の hit@1 が最も高い flood_score で B1 を 1 回．
- 正答率は判定モデルの採点で，Wilson の 95% 信頼区間を付ける．方式間の比較は同じ質問の正誤の組の McNemar の正確検定．
- 誰が答えるかの比較（H-B1・H-B2 の (a)）は，CPU の専門家に置かれた受信箱の質問に限る（B2 では GPU に置かれた受信箱は 8B が答えるため）．
  GPU に置かれた受信箱の質問は，同じ 8B で B1 と B2 を比べる（答える場所だけが違う）．
- H-B1：CPU の受信箱の質問で，B2（0.6B）と B3（0.6B）の McNemar の p が 0.05 以上なら支持（同等）．0.05 未満なら，原因（プロンプト長・切り詰め・
  打ち切り）を調べて記録する．
- H-B2：(a) CPU の受信箱の質問で，B2（0.6B）の正答率が B1 より低く p < 0.05．(b) 全問で B1 と B4 の差が B1 と B2 の差より小さい．
  (c) B4 の 1 問あたりの外に出た本文の数が B1 より少ない．3 つとも成り立てば支持，どれも成り立たなければ反証，それ以外は部分的に支持．

## EQ2：RQ-A のオフラインの評価（2026-10-09．`results/20261009_eq2_advert_eval`）

事前登録（下の「EQ2 の事前登録」）の手順で 1 回だけ評価した．受信箱 150 個，dev 2,948 問，test 2,940 問，所有者の特定に使うメール 1,460 通．
dev で選んだ値は，件名 40，T = 200，C = 32．m は dev の MRR が最大の flood_bm25_shared_idf の hit@m が 0.9 に届く最小の値で，m = 2 になった．

| 方式 | 公開情報（KB） | hit@1 [95%CI] | hit@2 | 質問を受け取る受信箱 | 所有者 top-1 | 所有者 AUC |
|---|---|---|---|---|---|---|
| card_sim | 169 | 0.112 [0.100, 0.123] | 0.153 | 2 | 0.168 | 0.760 |
| term_sketch | 870 | 0.293 [0.276, 0.311] | 0.359 | 2 | 0.425 | 0.939 |
| centroid_sim | 2,509 | 0.264 [0.248, 0.280] | 0.323 | 2 | 0.284 | 0.853 |
| multi_centroid | 75,386 | 0.349 [0.332, 0.366] | 0.440 | 2 | 0.527 | 0.950 |
| flood_score（密ベクトル） | 0 | 0.722 [0.707, 0.737] | 0.804 | 150 | 偶然（0.007） | 0.5 |
| flood（BM25，受信箱ごとの IDF） | 0 | 0.603 [0.585, 0.621] | 0.702 | 150 | 偶然 | 0.5 |
| flood（BM25，共有した IDF） | 0（＋文書頻度の共有） | 0.866 [0.855, 0.878] | 0.928 | 150 | 偶然 | 0.5 |
| ragroute（中央の MLP） | 0（＋学習データの中央への集約） | 0.215 [0.201, 0.229] | 0.279 | 2 | － | － |
| random | 0 | 0.007 | 0.013 | 2 | 0.007 | 0.5 |

**判定**（事前登録の基準）
- H-A1：**支持**．flood_score の hit@1（0.722）は，公開情報を使う 4 方式のどれ（最大は multi_centroid の 0.349．信頼区間は重ならない）よりも高い．
  代わりに質問（クエリ埋め込み）は 150 個の受信箱すべてに届く（他は 2 個）．
- H-A2：**支持**．card_sim・term_sketch・multi_centroid の 3 方式すべてで，公開情報の大きさと hit@1 の順位相関（1.00・1.00・1.00）と，
  大きさと所有者 top-1 の順位相関（1.00・0.40・1.00）がどちらも正だった．term_sketch の所有者の特定は語数 10 ですでに 0.415 と高く，
  語数を増やしてもあまり変わらない（送信者の名前などが少ない語数でも入るためと考えられる．未確認）．
- H-A3：**支持**．密ベクトル（0.722）≥ 受信箱ごとの IDF の BM25（0.603）で，IDF を共有すると BM25 は 0.866 まで上がった．
  ただし共有した IDF の BM25 は密ベクトルより高く，「密ベクトルの方が良い」は，統計を共有しない条件に限って成り立つ．

**読み取れること**
- 公開情報を使う方式は，所有者の特定の精度（0.17〜0.53）が，見つけてもらえる度合い（hit@1 0.11〜0.35）と同程度か，それ以上だった．
  公開する情報は，見つけてもらうことより持ち主を当てることの方に役立っている．
  **訂正（T2・Iteration 2．値は変えない）**：この読み取りは入力の長さの違いと，メールのヘッダー（持ち主のアドレス）の名前の一致によるもので，そのまま主張しない（journal の「T2」と「Iteration 2 実行済み」）．
  **注記（Iteration 3．値は変えない）**：この表の公開情報はヘッダー込みのメールから作った．本文だけから作り直すと hit@2 は multi_centroid 0.347，term_sketch 0.328，centroid_sim 0.227 に下がるが，H-A2（単調な兼ね合い）は 3 方式とも成り立つ（journal の「Iteration 3 実行済み」）．
- 公開情報なしで全員に聞く flood は，見つけてもらえる度合いが最も高く，所有者は特定されないが，質問の露出が最大になる．
  検索の統計を共有する BM25 は最も高い hit を得るが，文書頻度の共有そのものが露出になる（H-A3 の仮説の前提）．
- 中央の MLP（ragroute）は 150 個の受信箱では hit@1 0.215 にとどまり，公開情報を使う方式より低い．1 問 1 個の正解に対して，
  関連の無い受信箱を 10 個だけ抜き出した学習（150 個の全ての組は大きすぎる）が十分でない可能性がある．
- m = 2 は，最良の方式（共有 IDF の BM25）に合わせて決まったので，公開情報を使う方式には小さい（hit@2 は 0.15〜0.44）．
  事前登録の規則なので変えない．EQ3 の実機の確認は m = 2 で行う．

**設定への反映**：`data.enronqa` の card_subjects 40，term_sketch_size 200，n_centroids 32，`routing.top_m.enronqa` 2，
比較を揃えるため `routing.random_k.enronqa` も 2 にした．シャードの公開情報はこの値で作り直す．

## p0004 の実装と退行の確認（2026-10-09）

- I1〜I9 を実装した（`308db94`〜`b35a170`，`81ff7cb`）．既存の設定キーは消さず，値を変えたのは `cluster` の 3 か所
  （`requester_llm` の削除，`experts` の末尾への wafl500〜509 の追加，`gpu_workers` への wafl500 の追加．p0004 §8.1）だけである．
- 退行ゲート：**退行なし**．
  - R1（`results/20261009_r1_medrag_regression`．MedRAG，all，retrieval_only，10 問）：ラベル一致 1.000，`top_doc_ids` は s3 と 10 問中 10 問で一致．
  - R2（`results/20261009_r2_feb4rag_regression`．FeB4RAG，ragroute，2 問）：`selected_sources` は s2 と 2 問中 2 問で一致．
  - 専門家の配置は MedRAG 16 台，FeB4RAG 13 台のまま（wafl500〜509 は末尾に足したので使われない）．
- 計画からの逸脱と理由：
  - データ準備（メールの埋め込み）は制御点の GPU 1 枚で行った．約 7.4 万通を約 20 分で埋め込めたので，11 台への分担（§8.1）は要らなかった．
  - 受信箱ごとの BM25 の索引は専門家のノードには持たせず，H-A3 の確認に使うオフラインの評価（`advert-eval`）でだけ計算する．
    実機の受信箱をまたぐ統合は密ベクトルのスコアで行う（§8.2）ので，ノードの BM25 は実験に使わない．
  - 回答と正解のメールの最長共通部分列の比率（§8.4 の `answer_overlap_lcs`）は，正解のメールの本文を質問者に配らないよう，
    採点（`atrium judge`）の段で計算し，`judgements.jsonl` から分析に取り込む．
  - Agent Card の説明文は，受信箱で多い件名の上位 N 個とした（N は dev で選ぶ）．持ち主が自分のメールだけから作れる自己紹介として定めた．
  - 中央の分類器（ragroute）は，150 個の受信箱の全ての組を作るとメモリが足りないので，1 問あたり関連の無い受信箱を 10 個だけ抜き出して学習する．
  - 判定の参照モデル（§8.6 の 2）は，gpu2 のディスクの空きが少なく，データとモデルを制御点に置く指示があるので，制御点・GPU の PC で qwen3:14b を使う．

## EQ2 の事前登録（2026-10-09．評価の前に push する．p0004 §8.7）

EQ2（RQ-A のオフラインの評価．`atrium advert-eval`）の手順と判定の基準を，test の評価の前に固定する．

**手順**（`src/atrium/advert_eval.py` に固定）
- 受信箱は 150 個．質問は，dev（受信箱あたり最大 20 問）と test（受信箱あたり最大 20 問）．他の受信箱にほぼ同じメール（Jaccard 0.9 以上）が
  ある質問は除く（V3）．
- 公開情報の大きさの候補：Agent Card の件名の数 {5, 10, 20, 40}，語のスケッチの語数 T {10, 20, 50, 100, 200}，k-means の中心の数 C {2, 4, 8, 16, 32}．
  方式ごとに，dev の MRR が最大の値を選ぶ．
- 問い合わせる数 m：dev で MRR が最大の方式（flood_score・ragroute を含む非オラクルの全方式）の dev の hit@m が 0.9 以上になる最小の m
  （候補 {1, 2, 3, 5, 10, 20}．届かなければ 20）．
- test は 1 回だけ評価する．結果を見て候補・選択の規則・m を変えない．選んだ T・C・件名の数・m は，EQ3 の実機の確認と，
  シャードの公開情報（`data.enronqa`・`routing.top_m`）に使う．
- 所有者の特定：各受信箱から test の正解のメール以外を最大 10 通（種 12）抜き出し，公開情報だけから持ち主の受信箱を当てる．
  flood_score と ragroute は受信箱ごとの公開情報が無いので偶然の水準（1/150，AUC 0.5）とする．
- 公開情報の大きさと所有者の特定の兼ね合い（H-A2）を見るため，test では選んだ値に加えて全ての候補の値の hit@1・所有者 top-1 も出す
  （報告用の曲線であり，値の選択には使わない）．

**仮説の判定の基準**
- H-A1（flood_score は公開情報なしで高い hit を得るが，質問の露出は全台）：test の flood_score の hit@1 が，公開情報を使う 4 方式
  （card_sim・term_sketch・centroid_sim・multi_centroid）のどれの hit@1 以上なら支持する．質問を受け取る受信箱の数（flood は 150，他は m）を併記する．
- H-A2（公開情報を増やすほど hit も所有者の特定も上がる）：大きさの候補を持つ 3 方式（card_sim・term_sketch・multi_centroid）それぞれで，
  test の大きさと hit@1 のスピアマンの順位相関と，大きさと所有者 top-1 の順位相関が，どちらも正なら，その方式で単調な兼ね合いがあるとする．
  3 方式の全てで成り立てば支持，どれでも成り立たなければ反証，それ以外は部分的に支持とする．
- H-A3（BM25 は受信箱ごとの IDF では比べにくく，統計を共有すると改善する．密ベクトルの方が良い）：test の hit@1 が
  「flood_score（密ベクトル）≥ flood_bm25_local_idf」かつ「flood_bm25_shared_idf > flood_bm25_local_idf」なら支持する．
- 差の主張には hit@1 の 95% ブートストラップ信頼区間を併記し，区間が重なる差は「差がはっきりしない」と書く．

## p0004 の着手前の確認（2026-10-09）

### 新規性の確認（p0004 §5.3．N1〜N3）

C1〜C4 と実質的に重なる先行研究は見つからなかった（§12 の 5 には当たらない）．本文の読み取りは要約を経由しているので，
引用するときは原文で確かめ直す．

| 研究 | 設定 | 測っているもの | 重なり |
|---|---|---|---|
| DRAG（arXiv:2505.00443） | NetworkX による 1 プロセスのシミュレーション（20〜100 ピア）．公開データ（MMLU・合成の医療 QA・ニュース） | メッセージ数と精度．漏洩は定性的に触れるだけ | C1 の一部（探索のコスト）．C2〜C4 は無し |
| FedRepRAG（arXiv:2609.22162） | 1 枚の A100 で 10〜20 クライアントを模擬 | 表現の inversion と membership inference．クエリ埋め込みは全員へ送る（ルーティング無し） | C1 の一部（共有物の漏洩）．公開の段階・見つけてもらえる度合い・質問の露出は無し |
| Semord（arXiv:2609.25514） | 分散ベクトル検索．実機 32 ピアとシミュレーション．RAG ではない | recall・接触ピア数・待ち時間 | C1・C4 の一部 |
| A Wolf in Sheep's Clothing（arXiv:2605.28112） | 申告する profile（重心・k-means・説明文など）を偽造してルーティングを乗っ取る攻撃．シミュレーション | 完全性（改ざん）．profile からの漏洩は測っていない | C1 の一部（公開する記述の種類が近い）．関連研究として引用する |
| FD-RAG（arXiv:2605.27432），DRAGON（arXiv:2504.11197） | エッジの連合 RAG／端末とクラウドの分散 RAG | 共有物の復元攻撃／実機の待ち時間 | C1・C4 の一部 |

EnronQA を連合・分散の設定で使った研究は見つからなかった．C2 型の比較（質問者の大きいモデル，専門家の小さいモデル，同じ小さいモデルの対照，
GPU への委託）も見つからなかった．C1 の差別化は，FedRepRAG と A Wolf in Sheep's Clothing との対比で明確に書く．

### EnronQA のデータ検証とゲート G0（`results/20261009_eq0_data_check/`）

- V1：1 行は 1 通のメールで，train・dev・test は同じ 73,772 通を持ち，質問だけを分割している（333,473／105,515／89,316 問．論文と一致．分割をまたぐ重複は 0）．
  論文のメール数 103,638 通に対し，配布物は質問の付いた 73,772 通なので，検索の絶対値は論文と直接比べない．
- V2：論文に「CC BY 4.0」と明記されている．Hugging Face のメタデータには記載が無い．
- V3：テストのメールの 3.0%（2,250 通，テスト質問 2,722 問）に，他の受信箱に Jaccard 0.9 以上のメールがある．これらの質問は主評価から除く．
- V4：受信箱は 150 個．20 問に満たない受信箱は 5 個．受信箱あたり最大 20 問の抽出で 2,965 問（除外の前）．
- V5：全てのテスト質問に別解 1 個・誤答 2 個がある．判定の検証と 3 択の補助指標に使える．
- G0 を通過した．判定と回答のプロンプトは論文の付録 B.5・B.6 にある（判定モデルの実装で使う）．
- 制御点の取得：期限切れの Hugging Face のトークンが gpu2 に残っていたため，匿名で取得した．データは制御点だけに置く（ユーザーの指示）．

## E1 の判定（2026-10-09．p0003 S2）

E1 の全問実行は research-cycle を通さず，`run_full.sh` で 2026-10-07〜09 に行った．条件は全て temperature 0，
`k_ret` 50，`k_rerank` 15，質問者の LLM は llama3.1:8b．

| run | 条件 | n | 失敗率 | 選択できない率 | 正答率 [95% CI] | 適合率 / 再現率（test） | 問い合わせ（削減率） |
|---|---|---|---|---|---|---|---|
| s1 | FeB4RAG all，retrieval_only，qrels_oracle | 790 | 0 | － | － | 0.724 / 1.000 | 13.00（0） |
| s2 | FeB4RAG ragroute | 790 | 0 | － | － | 0.872 / 0.874 | 9.44（27.4%） |
| s3 | MedRAG all，retrieval_only，score | 7,663 | 0 | － | － | 0.445 / 1.000 | 4.00（0） |
| s4 | MedRAG all，snippet_return | 7,663 | 0.74%（57） | 1.6% | 0.659 [0.648, 0.670] | 0.445 / 1.000 | 4.00（0） |
| s5 | MedRAG ragroute | 7,663 | 0 | 1.4% | 0.657 [0.647, 0.668] | 0.865 / 0.799 | 1.65（58.7%） |
| s6 | MedRAG random（3 源） | 7,663 | 0 | 1.4% | 0.654 [0.643, 0.665] | 0.445 / 0.749 | 3.00（25.0%） |
| s7 | MedRAG none | 7,663 | 0 | 1.2% | 0.640 [0.629, 0.651] | － | 0（100%） |
| s8 | MedRAG all，cross_encoder | 7,663 | 0 | 1.4% | 0.689 [0.678, 0.699] | 0.445 / 1.000 | 4.00（0） |

**判定**（`.claude/research/config.yml` の `success_criteria`）
- ラベル一致：s3 で 0.999（7,663 問中 6 問が，上位 15 件の境界で 1 データ源だけ違う）．基準 0.99 以上を満たす．
- 失敗率：全実行で 1% 以下．s4 の 57 問は専門家への接続切れ（ReadError 51，RemoteProtocolError 6）で，
  再試行の追加（`0d7ed6b`）より前のイメージで動いていた．s5 以降は失敗 0．
- s3 と s4 の `top_doc_ids` は，s4 で成功した 7,606 問の全てで一致した（検索は同じで，回答方式だけが違う）．
- RAGRoute の報告値との比較：FeB4RAG の ragroute の問い合わせ数 9.44 は，論文の 9.20 とほぼ同じ．MedRAG の再現率・削減率は，
  リポジトリ内に論文の報告値が無いので比較できない．差の原因の候補は p0002 §7（fp16 の埋め込み，温度 0，ラベルの再計算）．
- 方式間の正答率（s4 を基準に，s4 で成功した 7,606 問で McNemar の正確検定）：
  - all と ragroute：0.659 と 0.658，p = 0.71．差は無い
  - all と random：0.659 と 0.655，p = 0.33．差は無い
  - all と none：0.659 と 0.640（+1.9 pt），p = 0.00094．差がある
- 実行条件の揃い：`git_head` は s1・s2・s4 が `d238f5fdf1ac-dirty`，s3 が `d238f5fdf1ac`，s5〜s7 が `0d7ed6ba587c-dirty`．
  間の差は `src/` で analysis・cli（ラベル一致を出す条件．分析だけ）と requester（検索要求の接続エラーの再試行）の 3 ファイル．
  再試行は接続エラーのときだけ働くので，成功した質問の結果の値は変わらない．
- 待ち時間は各条件 1 回の実測なので，主張に使わない．

**再集計で分かったこと**（p0004 の動機）
- 問題集ごとの all − none（McNemar）：PubMedQA +18.4 pt（p = 1.9×10⁻¹⁹），BioASQ +6.9 pt（p = 1.8×10⁻⁴），MedMCQA +0.7 pt（p = 0.37），
  MedQA −1.7 pt（p = 0.22），MMLU-Med −0.1 pt（p = 1）．RAG の効果は PubMedQA と BioASQ にほぼ限られる．
- ragroute は全 7,663 問で PubMed を選んでいた．関連ラベル（統合後の上位 15 件への寄与）は，専門性よりコーパスの大きさを反映している．
- random が PubMed を選ばなかった PubMedQA の 115 問では，正答率は random 0.591，all 0.800，none 0.661．正しい相手に聞く価値は実在する．
- `snippets_exposed` はシャードごとの取得件数の合計なので，シャードの分け方に依存する（PubMed の 6 シャードで 300 と数える）．

**s8 について**：最初の実行（2026-10-08）は，制御点の GPU で再ランクのモデルと 8B の Ollama が VRAM を取り合い，
Ollama が 33 層中 28 層しか GPU に載らず，生成が 1 問約 79 秒になった．1,874 問で止め，その結果は他の条件と揃わないので捨てた
（`/home/denjo/atrium-s8-offload-partial/` に退避）．質問者の LLM を wafl500 に分けて（`0c9f6c2`）やり直したが，1,191 問の時点で
p0004 に従い保留にした（`/home/denjo/workspace/ktakahashi/atrium/results/20261007_s8_med_all_ce/` に残し，同じ run_id で再開できる）．
再開するときは，その実行に限り `cluster.requester_llm` を wafl500 にする（p0004 §14）．
2026-10-12 に保留が解除され，同じ run_id で全問を終えた（上の表の s8 の行．判定と s4 との比較は「s8：MedRAG の cross_encoder」）．

**計画からの逸脱**：s8 の停止と，`cluster.requester_llm` の追加・結果の commit は，p0003 §3 の待機中に，ユーザーの指示で行った．

## 準備段階の記録（2026-10-05〜06）

### データ準備（制御点 `/home/denjo/atrium-data`）

- MedRAG：4 データ源を MedCPT で全て埋め込み直した（配布の埋め込みは 403．fp16 の混合精度，backlog B2）．
  16 シャード（PubMed 6，Wikipedia 8，StatPearls 1，Textbooks 1．1 シャード最大約 419 万断片）．
  StatPearls は 384,050 断片で，研究計画書 §7.1 の約 30.1 万より多い（2026-10 時点の NCBI の配布物）．
  関連ラベル（統合後の上位 15 件に断片を出したデータ源）は 1 問あたり平均 1.78 個
  （PubMed 7,423 問，Wikipedia 3,240 問，StatPearls 1,715 問，Textbooks 1,291 問）．
  ルーター（RAGRoute の MLP）は test で適合率 0.865，再現率 0.799，AUC 0.930．
- FeB4RAG：13 エンジン．ラベルは 790 件（関連ありのエンジンが無い 5 件を含む），関連ありのエンジンは平均 9.46 個．
  総点検で SGPT の最大長と trec-covid の重心を直したため，2026-10-06 にシャードの重心・クエリの埋め込み・
  split・ルーターを作り直した．ルーターは test で適合率 0.872，再現率 0.874，AUC 0.844．
- 所要時間の実測は docs/d0003_experiment_workflow.md §3．

### E0（results/20261006_052434/e0_summary.md）

研究計画書 §8 の E0 は準備段階で実施した．専門家（13.100，13.101．i5-8350U）の結果:

| 項目 | 値 |
|---|---|
| メモリ | 8 GB × 2（デュアルチャネル，DDR4-2400） |
| qwen3-0.6B（Q4_K_M，4 スレッド） | pp512 約 200，pp4096 約 113，tg128 約 26 tokens/s |
| qwen3-1.7B | pp512 約 68，pp4096 約 54，tg128 約 9 tokens/s |
| qwen3-4B | pp512 約 24，pp4096 約 20，tg128 約 4 tokens/s |
| qwen3-8B | pp512 約 12，pp4096 約 11，tg128 約 2.2 tokens/s |
| FAISS fp16 平坦索引（上位 50 件） | 100 万／200 万／300 万ベクトルで 0.30／0.57／0.85 秒（約 0.28 秒／100 万ベクトル） |
| MedCPT のクエリ埋め込み（CPU） | 16〜128 トークンで 36〜137 ms |
| ネットワーク | RTT 0.5〜0.8 ms，約 930 Mbit/s |

読み取れること（RQ2 の設計への含意）:
- 宿る型の専門家が 15 断片（約 3,000〜4,000 トークン）を読むプレフィルは，0.6B で約 30 秒，1.7B で約 70 秒，
  4B で約 3 分，8B で約 6 分．実用の範囲は 0.6B〜1.7B で，4B 以上は断片数 k を減らす必要がある．
- 1 シャード（約 419 万ベクトル）の検索は約 1.2 秒（外挿）．専門家ノードは検索を直列に処理するので，
  MIRAGE の全 7,663 問の retrieval_only は，ノードあたり約 1.2 秒 × 7,663 問 ≈ 2.5 時間が下限の目安．

### 実機での検証

- FeB4RAG（results/20261006_043855，20261006_044022）：全 790 問，失敗 0．routing=all は再現率 1.000．
  ragroute は test で平均 9.19 エンジンに問い合わせ（研究計画書 §7.2 の RAGRoute の値 9.20 とほぼ同じ），
  削減率 29.3%，再現率 0.866．これは作り直す前のルーターの値である．
- FeB4RAG（results/20261006_214950．作り直した後の先頭 40 問）：ragroute・retrieval_only・qrels_oracle で失敗 0，
  check-data の検査を通過した．全 790 問での ragroute の値は E1 で測り直す．
- 宿る型（results/20261006_050758．FeB4RAG の 5 問）：専門家の qwen3:0.6b が 15 断片で回答し，原文を返さない
  ことを確認した．プレフィル 2,641 トークンに約 20 秒．並列に送ると専門家で待ち行列ができる
  （local_answer.timeout_s を 1,800 秒にした）．
- MedRAG（results/20261006_094749．16 台の専門家，問題集ごとに 20 問の計 100 問）：routing=all・retrieval_only で
  **ラベル一致 1.000**（中継点のラベルと実機の分散検索・統合の結果が完全に一致）．1 問の検索は約 4.65 秒
  （4 問を並列に処理．専門家あたり 1 問約 1.2 秒）．
- MedRAG（results/20261006_100032．問題集ごとに 5 問の計 25 問）：ragroute・snippet_return（llama3.1:8b，
  temperature 0）で正答率 0.72 [0.52, 0.86]，問い合わせ数の削減率 0.66，1 問の生成は中央値約 22 秒
  （並列 4 問を 2 件ずつ処理．25 問で 160 秒）．
- MedRAG（results/20261006_205154，20261006_224015，20261006_230206，20261007_005901，20261007_010910．all・retrieval_only・score，
  50 問または 25 問）：スクリプトの安全化（`7482566`），設定の書式の変更（`273c9e3`），配置先の変更（`82aec35`）などのたびに流した動作確認．
  全て失敗 0，ラベル一致 1.000，e2e の中央値 4.6〜4.7 秒で，変更の前後で結果は変わらなかった．
- 準備段階で直した不具合と運用上の注意は git log（2026-10-06）と docs/d0003_experiment_workflow.md §4 にある．
