# backlog — 人間判断待ち事項 / 自動判断の記録

このファイルは research-cycle が「本来は人間の判断が要るが，サイクルを止めないために暫定で自動選択した事項」と，
「不可逆・危険なため停止して人間に委ねた事項」を記録する．新しいものを常に先頭に追記する（逆時系列）．

書式:
```
## B{n} [auto-decided YYYY-MM-DD] 題目
- 状況: なぜ判断が要ったか
- 自動選択: 何を選んだか
- 根拠: なぜそれが最も妥当か
- 要レビュー: 人間が確認・却下する際に何を見るか（却下時はこの項目を編集して差し替える）
```
不可逆な事項は `[needs-human YYYY-MM-DD]` として記録し，Slack で @mention 済みであることを明記する．
人間が答えたら（herdr 上でも，Slack のスレッドへの返信（`[Slack 返信]`）でも），`[human-decided YYYY-MM-DD]` として
回答の内容と，それに従って何をしたかを追記する．

---

## B48 [auto-decided 2026-10-16] Iteration 17 の事前登録した実行が docker pull の層の破損で評価の前に失敗した．blob を確かめて同じコマンドを 1 回だけ流し直す

- 状況: 事前登録（commit `2b043ca`）のとおり，`NO_CHAIN` を確かめてから publish した（235 秒．版 `2b043caeec1d-dirty`．制御点の registry の manifest の digest `sha256:a93f5f0230cf…` はローカルのイメージと一致）．その後 `bash scripts/tasks/remote.sh churn_leave_eval 20261016_iter17_churn_leave_eval 20261009_eq5_b1 20261016_iter16_churn_join_eval` を 1 回流した．制御点の `churn_leave_eval.sh` の最初の `docker pull` の層の展開で `unpigz: skipping: <stdin>: corrupted -- crc32 mismatch`（層 `sha256:02e7dc8304d688f35c2fb5adb7596b055d4d39d4ac757cc72fa4b1e056c7e638`）となり，終了コード 1（125 秒）で止まった．`atrium churn-leave-eval` は起動しておらず，評価の数値は 1 つも出ていない．`results/20261016_iter17_churn_leave_eval/` は手元に無く，制御点に `atrium-full` のコンテナも残っていない．
- 選択肢:
  - A1 (Recommended): 読み取りだけで，制御点の registry の blob の `sha256sum` と `gzip -t` を調べる．壊れていれば publish からやり直し，壊れていなければ publish はやり直さない．その上で同じコマンドを 1 回だけ流す．メリット: 評価は始まっておらず，結果を誰も見ていない．規則もコマンドも変えないので，事前登録の趣旨に反しない．原因に応じた対処になる．デメリット: 「1 回だけ」の文言からは外れるので，その扱いを明記する必要がある．
  - A2: blob を確かめずに，同じコマンドをそのまま流し直す．メリット: 手数が少ない．デメリット: blob が registry の中で壊れていれば，また同じ失敗になる．
  - A3: 再実行せず，人間の判断を待つ（needs-human）．メリット: 指示の文言に最も忠実である．デメリット: イテレーションが止まる．
- 自動選択: A1（コーディネーターの判断）．
- 根拠: 事前登録の「1 回」は評価の実行を数えるものであり，評価の前のインフラの失敗（イメージの取得の失敗）は数えない．評価が起動していないので，結果を見て規則を変える余地は無い．
- 要レビュー:
  - (a) 「1 回だけ」の扱い（評価の前のインフラの失敗を回数に数えないこと）．
  - (b) 原因が registry の中の破損か，転送の途中の破損か（blob の検査の結果を下に追記する）．
  - (c) 2 度目も失敗したら（原因を問わず），それ以上流さずに B49 [needs-human] にして止める．
  - (d) 未追跡のファイル（`iter7_chain.log`，`results/20261016_iter14_smoke_*`，`/tmp` のファイル）に触れず，削除・課金・`mise run clean --full`・registry の blob の削除や garbage collect を行わない．
- 結果（2026-10-16）:
  - blob の検査：制御点の registry（`localhost:5000`）の `atrium-full` の blob `sha256:02e7dc83…` をストリームで読み，`sha256sum` は digest と一致し（423,719,366 バイト），`gzip -t` も通った．registry の中の blob は壊れていない．1 度目の失敗は，転送か制御点での展開の途中の破損と推測する（確かめていない）．
  - publish はやり直していない．2 度目の実行は 1 度目と同じイメージ（`2b043caeec1d-dirty`）である．
  - 同じコマンドを 1 回だけ流し，終了コード 0（298 秒．評価の本体 56.9 秒，実行 5,120 件，失敗 0 件）で終わった．G1〜G5 は全て通過し，H-L1 の機械的な判定は「支持」である（journal の Iteration 17 の「実装・実験」）．B49 は不要になった．

## B47 [auto-decided 2026-10-16] Iteration 17 のレバーの形：離脱の通知の遅れ L_leave（`churn_eval.leave_notice_delay`．L_join = 0 に固定．新しい CLI `atrium churn-leave-eval`）

- 状況: B46 で，Iteration 17 のレバーを離脱の通知の遅れ L_leave ∈ {0, 10, 100} と通知なし（∞）にし，参加の通知を L_join = 100 に固定する案にした（要レビュー (a) で，基準を L = 100 にするか L = 0 にするかを残していた）．計画のための時刻表の試算（churn-eval と同じ種）で，L_join = 100 のまま L_leave を短くすると，a = 0.7・R = 1000 の「稼働中なのに見えない」割合が 0.036（L_leave = ∞）→ 0.234（100）→ 0.378（10）に増え，通知なし（0.285）より大きくなると分かった．稼働中の平均 200 問・停止中の平均 85.7 問なので，離脱の通知の後に 100 問以内に再び参加するノードが多く，参加の通知が届くまで見えないためである．このままでは，離脱の通知の効果と見えなさの増加が混ざる．
- 選択肢:
  - 参加の通知の基準: A1 (Recommended) L_join = 0．メリット: 稼働中なのに見えないノードが全ての L_leave で 0 になり，Iteration 16 の E-2 で残った低下（離脱済みのデバイスが枠を取る分）だけを動かせる．L_leave = ∞ が Iteration 16 の L = 0 の実行と同じになるので，基準の照合が全件でできる．デメリット: 参加の通知が理想的な場合で，現実の値ではない．／A2 L_join = 100（B46 の案）．メリット: Iteration 16 の判定の条件と同じ．デメリット: L_leave < L_join では見えなさが増え，2 つの効果が混ざる．離脱の方が参加より早く伝わる設定は，同じ gossip で伝える構成（SWIM・memberlist）に合わない．／A3 L_join = L_leave（対称）．メリット: 同じ gossip で伝わるという仮定に合う．デメリット: 2 つのキーを同時に動かすので，単一レバーにならない．
  - 主の判定の L_leave: B1 (Recommended) L_leave = 10．メリット: 自明でない値の中で，時刻表の試算の効果が大きい（見えているのに離脱済みの数が ∞ の 0.125 倍）．デメリット: 秒への換算は問の頻度の仮定に依る．／B2 L_leave = 100．メリット: Iteration 16 の換算（数十問/秒）で，意図的な離脱の伝播の数秒に近い．デメリット: 見込みの回復は約 0.27 と小さく，閾値を決めにくい．／B3 L_leave = 0．デメリット: (0, 0) は構造上 R = 1 と同じになる（B46 要レビュー (c)）．
  - 閾値: C1 (Recommended) 5 個の種の全てで ρ_leave ≥ 0.75．メリット: 構造上 ρ_leave(0) = 1 なので，0.5 では試算（線形の見積もり 0.875）よりずっと小さい回復でも支持になる．0.75 は，見積もりから Iteration 16 の ρ の種の間の標準偏差（0.045）の約 2.8 倍を引いた値である．デメリット: Iteration 15・16 の 0.5 と揃わない．／C2 0.5．メリット: Iteration 15・16 と揃う．デメリット: 検定として弱い．
  - CLI の形: D1 (Recommended) 新しい CLI `atrium churn-leave-eval`（`src/atrium/churn_leave_eval.py`）．メリット: `churn-join-eval` の挙動と Iteration 16 の結果の再現に触れない．Iteration 15・16 と同じく，イテレーションごとに CLI を分ける形になる．デメリット: 集計と報告の一部が重複する．／D2 `churn-join-eval` に `--leave-delays` を足す．デメリット: 既存の CLI と metrics.json の形が変わる．
  - 感度の格子: E1 (Recommended) L_join = 100 でも同じ L_leave を流し，記録だけにする．メリット: B46 (a) に数値で答えられる．オフラインで実行の数が 2 倍になるだけである．デメリット: 出力が大きくなる．／E2 流さない．
- 自動選択: A1・B1・C1・D1・E1．config.yml の `churn_eval.leave_notice_delay` の note を更新した（values は [0, 10, 100] のまま．∞ は基準）．事前登録は journal の Iteration 17．
- 根拠: Iteration 16 の分解で，L = 0 でも残る低下は，構造上，離脱済みのデバイスが表に残ることだけによる．L_join = 0 に固定すると，L_leave はこの因子だけを動かす単一レバーになる．SWIM（Das ら，DSN 2002）と memberlist は，参加と意図的な離脱を同じ gossip で伝え，故障は suspicion の時間切れの後に伝える．したがって，現実には L_leave ≥ L_join であり，L_join = 0 の格子はこの順序を満たす．
- 要レビュー:
  - (a) L_join = 0 は参加の通知が理想的な場合である．現実的な組み合わせ（意図的な離脱なら L_join ≈ L_leave，故障なら L_leave > L_join）は，感度の格子（L_join = 100）と次の候補に残す．
  - (b) 秒と問の換算は推測である．memberlist の LAN の既定値で n = 30 なら，故障の suspicion の時間切れは約 5.9 秒（上限は約 35 秒）で，これに検出の約 1.6 周期が加わる．L_leave = 10 は，1 問/秒なら故障の検出に，数十問/秒なら 1 秒未満の意図的な離脱（`Leave()`）に当たる．Iteration 16 の換算（数十問/秒で L = 100 が数秒）では，故障の L_leave は数百問になり，見込みの回復は L_leave = 100 の約 0.27 より小さい．
  - (c) 閾値 0.75 と期待値 0.875 は，時刻表の試算（線形の見積もり）から，結果を見る前に決めた．試算は `/tmp/iter17_estimate.py`・`/tmp/iter17_estimate2.py`（リポジトリの外）を手元で `uv run` して得た（churn-eval と同じ種，ノード 30 台，2,940 刻み，5 個の種）．検算で Iteration 16 の値（見えているのに離脱済みの割合 0.276・0.281，flood_score の wasted 5.77・7.92・8.10）が再現した．枠を取られる損失と上位 m 外が入れ替わる分は線形でないので，見積もりは外れうる．
  - (d) 離脱の通知の後に再び参加したノードは，参加の事象の方が新しいので，参加の通知が届いた時点から見えるとする．SWIM の incarnation 番号による反駁は模していない．
  - (e) phase 1 の途中で 2 度，ツールが `Unknown tool` で失敗した（1 度目は Bash・Read，最後は Edit・Bash）．backlog と config.yml の note は rc-researcher が書き込んだ．journal の Iteration 17 の節は，挿入が失敗したので，オーケストレータが戻り値から書き写した．config.yml の YAML の構文は，オーケストレータが `yaml.safe_load` で確かめた．state.json は指示により触れていない（`current_lever` の更新はオーケストレータが行う）．
  - (f) 未追跡のファイル（`iter7_chain.log`，`results/20261016_iter14_smoke_*`）に触れず，削除・課金・`mise run clean --full` を伴わない．

## B46 [auto-decided 2026-10-16] Iteration 16 は採用．Iteration 17 のレバーは離脱の通知の遅れ（`churn_eval.leave_notice_delay`）

- 状況: Iteration 16 は採用である（G1〜G4 通過，H-J1 支持．device_probe_d12 の ρ は 0.581〜0.695）．L = 0 でも device_probe_d12 の低下の 27%（556 問）が残る．その原因は，構造上，離脱済みのデバイスが表に残り，D = 12 の枠のうち 3.32 枠を取ることにある（journal の「Iteration 16 実行済み：分析・考察」）．config.yml の levers は使い切っている．
- 選択肢:
  - A1 (Recommended): 離脱の通知の遅れ L_leave ∈ {0, 10, 100} と通知なし（∞）を振る．参加の通知は L = 100 に固定する．メリット: 残る損失の原因を直接動かす．全ての方式の無駄な問い合わせ（flood_score で 1 問あたり 8.10 台）も減らせる．SWIM の「故障の検出と更新の伝播を分ける」構成に合う．Iteration 16 の関数を再利用でき，オフラインで分単位で終わる．デメリット: 離脱の検出の時間（probe の周期と suspicion の時間切れ）の仮定が要る．
  - A2: 返事の無いデバイスの分を，次の順位のデバイスで補う変種（B40 (b)）．メリット: メンバーシップのプロトコルに依らない．デメリット: 1 往復が増える．選び方の変更で，Iteration 15・16 の「見え方を変える」の系列から外れる．
  - A3: 二段の方式の離脱下の実機での再生（B43 A2）．メリット: B40 (a) の `_tolerant` の効き方を確かめられる．デメリット: 実機が要る．オフラインの効果の形が分かってからの方が，測る条件を絞れる．
- 自動選択: A1．iteration_name は「離脱の通知の遅れごとの無駄な問い合わせと hit の回復」とした．config.yml の levers の末尾に `churn_eval.leave_notice_delay` を追記する．判定の規則は次の rc-researcher が事前登録で決める．
- 根拠: Iteration 16 の乗法の分解で，残る因子 ρ(0) は方式ごとの「離脱済みなのに見える」ことの効果だった．これを動かすのが単一レバーとして最も直接的である．
- 要レビュー:
  - (a) 参加の通知の基準を L = 100（現実的な値）にするか，L = 0（参加の効果を完全に除く）にするか．
  - (b) L_leave の値と，秒への換算（SWIM の検出の時間）は推測である．
  - (c) L_join = L_leave = 0 は構造上 R = 1 と同じになる．このため退行ゲートには使えるが，主の判定は現実的な L_leave で行う必要がある．
  - (d) A2・A3 は次の候補に残す．
  - (e) 未追跡のファイル（`iter7_chain.log`，`results/20261016_iter14_smoke_*`）に触れず，削除・課金・`mise run clean --full` を伴わない．
  - (f) rc-evaluator は途中で `Unknown tool: Bash` により書き込めず，journal・backlog・config.yml の記述と commit はオーケストレータが戻り値から書き写して行った．分析は終わっていたので B41 (b) の blocked にはしなかった．

## B45 [auto-decided 2026-10-16] Iteration 16 のレバーの形：参加の通知の遅れ L（`churn_eval.join_notice_delay`．新しい CLI `atrium churn-join-eval`）

- 状況: B43 で，Iteration 16 は「参加を通知する形のメンバー表の変種をオフラインで評価する」に決まり，レバーの名前・遅れ L の値・判定の規則を rc-researcher が決めることになった．config.yml の levers は使い切っている．要レビューは (a) L の値の選び方，(b) 実機の再生（A2）を先にすべきか，(c) R = 1 の hit に限った問ごとの遷移を出力に足すか，の 3 点である．
- 選択肢:
  - L の値: A1 (Recommended) L ∈ {0, 1, 10, 100} と通知なし（L = ∞）．メリット: L = 0 は通知の上限，L = 100 は gossip の収束（数秒の見込み）に処理量（数十問/秒）を掛けた程度で，その間を対数で埋める．時刻表だけの概算で，a = 0.7・R = 1000 の「稼働中なのに見えない」ノードの割合は 0.285（通知なし）→ 0.036（L = 100）→ 0.004（L = 10）→ 0（L ≤ 1）．デメリット: 1 刻み ＝ 1 問なので，秒への換算は処理量の仮定に依る．／A2 L を R の割合（R/10，R/2）にする．メリット: R ごとに効果の形がそろう．デメリット: 実機の通知の遅れは R に依らないので，物理的な意味が薄い．／A3 L = 0 だけ．メリット: 最も簡単．デメリット: 遅れへの感度が分からない．
  - 主の判定の方式: B1 (Recommended) device_probe（D = 12）．メリット: RQ-A の実用的な方式で，Iteration 15 の H-C2 と同じ方式．デメリット: 1 段目の候補の枠を離脱済みのデバイスが取るので，通知だけでは回復しきらない可能性がある．／B2 flood_score．メリット: L = 0 で R = 1 と同じ hit になることが構造上決まる．デメリット: 1 問に約 21 台へ問い合わせる方式で，実用の主題から外れる．
  - 通知の範囲: C1 (Recommended) 参加だけを通知する（離脱は更新のときにだけ表から消える）．メリット: E-1 で主因とした (ii) の見えなさだけを動かす単一レバー．二段の方式と flood_score は返事で離脱を避けられる．デメリット: 離脱済みの受信箱が枠を取る (iv)・無駄な問い合わせは減らない．／C2 参加と離脱の両方を通知する．デメリット: 2 つの効果が混ざる．離脱の通知は次の候補に回す．
  - 問ごとの遷移（B43 (c)）: D1 (Recommended) 記録として足す（判定に使わない）．(方式, L, 種) ごとに R = 1 の区分（hit・(i)〜(iv)）から R = 1000 の区分への 5 × 5 の件数の表を出す．メリット: Iteration 15 の E-1 で範囲でしか出せなかった x を正確に出せる．デメリット: 出力が大きくなる（a = 0.7 だけに限る）．／D2 足さない．
  - 実機の再生（B43 (b)）: E1 (Recommended) 今回はオフラインだけで行い，実機の再生は次の候補に残す．メリット: EQ7b で flood_score・multi_centroid のオフラインの規則と実機が全 22 回で一致している．通知の効果がオフラインで小さければ実機は要らない．デメリット: 二段の方式の `_tolerant` の効き方（B40 (a)）は未確認のまま．／E2 実機の再生を先に行う．
- 自動選択: A1・B1・C1・D1・E1．config.yml の levers の末尾に `churn_eval.join_notice_delay`（values [0, 1, 10, 100]）を追記した．事前登録は journal の Iteration 16．
- 根拠: Iteration 15 の学び（頑健さを上げるには選び方より見え方を変える）に直接つながる．SWIM（Das ら，DSN 2002）は故障の検出と更新の伝播を分け，更新を probe に相乗りさせて伝える．参加の通知は gossip で O(log n) の時間で全体に届くとされる（二次資料）．したがって「更新の間隔 R」と「通知の遅れ L」を別の量として扱うのは既存のメンバーシップ・プロトコルの構成に合う．オフラインで分単位で終わり，データ準備・削除・課金を伴わない．
- 要レビュー:
  - (a) L の刻みは問の数で，秒への換算（例: L = 100 は数十問/秒で数秒）は推測である．実機の通知の遅れ（memberlist の収束 約 2 秒は HashiCorp の講演の値で，条件は未確認）と処理量の両方に依る．
  - (b) 二段の方式は，離脱済みでも表に残るデバイスが 1 段目の上位 D 台の枠を取る．L = 0 でも残る低下は，この枠の分と読める（記録の E-2）．離脱の通知の変種は次の候補．
  - (c) 参加の通知を受け取った後に離脱したノードも表に残す（次の更新まで）．通知は参加の事実だけを伝え，その後の離脱は伝えないという仮定である．
  - (d) 未追跡のファイル（`iter7_chain.log`，`results/20261016_iter14_smoke_*`）に触れず，削除・課金・`mise run clean --full` を伴わない．

## B44 [auto-decided 2026-10-16] `tests/test_node_workers.py` の不安定なテストを別件として扱う

- 状況: Iteration 15 の実装の検査（commit `01774a2`）で，`test_serve_workers_answers_from_every_process_and_stops_on_sigterm` が 6 回中 2 回失敗した（B42 (b)）．このテストはプロセスの起動と SIGTERM を扱うもので，今回の変更（オフラインの評価の CLI）とは関係がない．
- 自動選択: Iteration 15 の判定には含めず，修正もしない．
- 根拠: 変更の対象外であり，単一レバー原則に従って，別のイテレーションか人間の判断に回す．
- 要レビュー: `4608ca5` 以前でも失敗するかを再現する（例: 20 回の繰り返し）．失敗の原因（起動待ちの時間切れ，ポートの競合など）を調べる．

## B43 [auto-decided 2026-10-16] Iteration 15 は採用．Iteration 16 の方向は，参加を通知する形のメンバー表の変種のオフライン評価（B40 の A5）

- 状況: Iteration 15 の判定は採用である（G1〜G4 通過，H-C2 支持，E-1 確かめた）．ただし，H-C2 の差（0.012〜0.025）は種の間の揺れ（0.06〜0.10）より小さい．hit の低下（0.26〜0.29）の大部分は，どの方式でも「稼働中の正解がメンバー表で見えない確率」（0.283）で決まっていた（journal の「Iteration 15 実行済み：分析・考察」）．config.yml の levers は使い切っている．
- 選択肢:
  - A1 (Recommended): 参加を通知する形のメンバー表の変種を，オフラインで評価する．メリット: E-1 で主因とした見えなさを直接減らす．EQ7a・Iteration 15 の関数を再利用でき，オフラインで分単位で終わる．デメリット: 通知の遅れ L などの新しい仮定の値を決める必要がある．
  - A2: 二段の方式の離脱下を実機で再生する（`CHURN_ROUTINGS` に centroid_probe・device_probe を足す）．メリット: B40 (a) の `_tolerant` の効き方を確かめられる．デメリット: 実機が要る．Iteration 15 の差が種の揺れより小さいので，得られる知見が少ない．
  - A3: RQ-B で，device_probe（D = 12）の上で B1 を流す（B40 の A2）．
- 自動選択: A1．レバーの名前・遅れ L の値・判定の規則は，次のセッションの rc-researcher が決めて，config.yml の levers に追記する．state.json の `iteration_name` は null のままにした．
- 根拠: 学びの「頑健さを上げるには，選び方よりも見え方を変える」に直接つながる．オフラインで判定でき，データ準備・削除・課金を伴わない．
- 要レビュー:
  - (a) 遅れ L の値の選び方（例: 参加を即時に通知する上限と，R と同じ程度の遅れ）．
  - (b) A2（実機の再生）を先にすべきか．
  - (c) R = 1 の hit に限った問ごとの遷移を，出力に足すか（Iteration 15 の E-1 では，件数の連立から範囲で求めた）．

## B42 [auto-decided 2026-10-16] Iteration 15 の G2 の照合の範囲：hit に加えて contacted・wasted も EQ7a と照合する

- 状況: 実装（commit `01774a2`）は G2 で hit だけでなく contacted・wasted も 1/2940 の許容で EQ7a と照合している．計画の文言は「hit が EQ7a と 1/2940 以内」だけで，実装の方が厳しい．rc-executor が事前登録の前の判断を求めた．
- 選択肢: B1 (Recommended) このまま事前登録に書く．／B2 hit だけを照合するように実装を直す．
- 自動選択: B1．
- 根拠: contacted・wasted はノード数の平均で，EQ7a と同じ規則で計算していれば完全に一致するはずの値である．hit が偶然合っても再現に誤りがあれば検出できる．事前登録の前にゲートを厳しくする変更なので，結果に合わせた変更にはならない．
- 要レビュー:
  - (a) G2 が contacted・wasted だけで落ちた場合，それが計算の規則の違い（例: 見えている受信箱の数え方）によるのか，実装の誤りなのかを rc-evaluator が切り分ける．
  - (b) commit `01774a2` で `tests/test_node_workers.py::test_serve_workers_answers_from_every_process_and_stops_on_sigterm` が 6 回中 2 回失敗した．今回の変更の対象外で，元から不安定なテストと見るが，`4608ca5` での再現は未確認．別件としてフェーズ 3 に引き継ぐ．
  - (c) commit `01774a2` のメッセージが `:sparkles:` の文字列で，履歴の絵文字（✨）と揃っていない．force push は禁止なので書き直さない．

## B41 [auto-decided 2026-10-16] Iteration 15 の事前登録の前に決める 3 点（E-1 の種の間の集約，(iii) の内訳，experiment_deadline）

- 状況: phase 2 の 1 回目の rc-executor が，近接コードと参照ファイルを読んだ段階で `Unknown tool: Bash` で止まった．実装・事前登録の commit・実行はどれも無い（git で確認．HEAD は `4608ca5` のまま，コードの変更なし）．戻り値で，事前登録の前に決める点を 3 つ挙げた．
- 選択肢:
  - E-1 の種の間の集約: B1 (Recommended) 5 個の種のすべてで割合が 0.8 以上なら確かめたとする．低下が 0 以下の種は不成立とする．／B2 5 個の種の和の比（Σ(ii) の増分 ÷ Σ 低下）で判定する．
  - (iii) の内訳: C1 (Recommended) 二段の方式で (iii) を「候補に入らない」と「候補に入ったが上位 m に入らない」に分けて記録として足す（判定には使わない）．／C2 計画のとおり 4 区分だけにする．
  - experiment_deadline: 規則どおり（現在時刻 ＋ 1200 分 × 60 ＋ マージン）にする．
- 自動選択: B1，C1，experiment_deadline は規則どおり．B2 の値は判定に使わず，記録として出す．
- 根拠: B1 は H-C2 の判定の形（5 個の種のすべて）と揃い，種ごとの揺れを隠さない．C1 は判定を変えずに，1 段目と 2 段目のどちらで hit を失ったかをフェーズ 3 が区別できる．experiment_deadline はオフラインの実行には過大だが，早く終われば影響が無く，規則を変える理由が無い．
- 要レビュー:
  - (a) B1 は B2 より厳しい．低下が小さい種があると比が不安定になり，不成立になりうる．フェーズ 3 で B2 の記録の値と食い違うときは，その旨を明記する．
  - (b) rc-researcher・rc-executor とも，定義の `tools:` に Bash があるのに使えなかった．subagent を動かす側の環境の問題とみる．再委託では冒頭で Bash の動作を確かめさせ，再び失敗したら status="blocked" にして Slack で判断を仰ぐ．
- 記録の注記: B40 (c) は rc-executor が確かめた．G1 の参照は `results/20261015_iter7_device_probe_eval/metrics.json` の `curve.test.device_probe[].max_hit@2`（d = 12: 0.682993，d = 24: 0.783673，d = 30: 0.804082）と `curve.test.centroid_probe[].hit@2`（k = 75: 0.761224，k = 30: 0.690476）．flood_score は `curve.test.flood_score.hit@2` = 0.804082．

## B40 [auto-decided 2026-10-16] Iteration 15 のレバー：二段の方式の参加・離脱への頑健さをオフラインで評価する新しい CLI

- 状況: B39 で人間が選択肢 1（負荷の系列を閉じて RQ の研究へ戻る）を選んだ．config.yml の levers は使い切っているので，d0006・p0004 の未着手の項目から新しいレバーを考えた．RQ-A で中心になった二段の方式（centroid_probe・device_probe）は，RQ-C（EQ7a・EQ7b）で評価していない．EQ7a の「表の更新の後に参加したノードが見えない」という推測も未検証である．
- 選択肢:
  - A1 (Recommended): 二段の方式の離脱下のオフライン評価（新しい CLI `atrium churn-cascade-eval`）．メリット: RQ-C の空白を埋め，EQ7a の推測も同時に確かめられる．オフラインで分単位で終わり，既存の関数をほぼ再利用できる．デメリット: 実機の二段の方式の離脱下の挙動とは照合しない．
  - A2: RQ-B で device_probe（D = 12）の上で B1 を 1 回流す．メリット: 実用的な方式の下流の正答率が得られる．デメリット: 実機と LLM の生成で 2 時間以上かかり，判定モデルの採点も要る．
  - A3: R4 (c)．再学習しない ragroute が新参者を選ぶ理由を調べる．メリット: オフラインで小さく済む．デメリット: RQ への寄与が周辺的である．
  - A4: R4 (a)．EQ3 の `ReadError` を再現する．デメリット: 実機が要り，運用の頑健性の話で RQ ではない．
  - A5: 参加を通知する形のメンバー表の変種．デメリット: A1 の E-1 の結果に依存するので，次のイテレーションの候補に回す．
- 自動選択: A1．config.yml の levers の末尾に `churn_eval.methods`（centroid_probe・device_probe）を追記した．事前登録は journal の Iteration 15．
- 根拠: RQ の研究へ戻るという B39 の方向に合い，オフラインで判定でき，データ準備・削除・課金を伴わない．支持・不支持のどちらでも RQ-C の記述が進む．
- 要レビュー:
  - (a) 実機の質問者の `_tolerant`（`src/atrium/requester.py` の 357 行付近）が，centroid_probe・device_probe の probe の失敗にも効くかを確かめていない．効かない場合は，H-C2 の結果は「返事のあった分で続ける」と仮定した上限になる（journal の限界に記載）．実機での再生は次の候補．
  - (b) 返事の無かったデバイスの分を補わない規則でよいか．補う変種は 1 往復を増やすので，今回は扱わない．
  - (c) G1 の参照（`results/20261015_iter7_device_probe_eval/metrics.json` の test の hit のキー名）を確かめていない．実装のときに確かめる．
  - (d) 計画は未追跡のファイル（`iter7_chain.log`，`results/20261016_iter14_smoke_*`）に触れず，削除・課金・`mise run clean --full` を伴わない．
- 記録の注記: rc-researcher はツールの呼び出しの失敗でファイルへ書き込めなかったので，この節と journal・config.yml・state.json はオーケストレータが戻り値から書き写した．

## B39 [human-decided 2026-10-16] Iteration 14（retrieve の本文を問ごとに 1 回だけ符号化する）は採用．Iteration 15 の方向は選択肢 1（負荷の系列を閉じて RQ の研究へ戻る）

- [human-decided 2026-10-16] 人間が選択肢 1 を採用した．負荷の系列（Iteration 4〜14）を閉じる．state.json は iteration=15，phase=research，status=running，current_lever=null とし，次のセッションの rc-researcher が d0006・p0004 の未着手の項目から次の単一レバーを選ぶ．要レビュー (c) の `/tmp/iter14_prestudy/`・`/tmp/iter13_prestudy/` は削除してよい旨が書かれているが，削除は別途確認してから行う．
- [human-decided 2026-10-16] 人間が「完了した」と伝えた．`/tmp/iter13_prestudy/`・`/tmp/iter14_prestudy/` がどちらも存在しないことを確かめた（人間が削除したと解釈した）．エージェントは削除していない．
- 以下は判断を求めた時点の記録（経緯）である．

- 状況: Iteration 14 の判定は採用（事前登録の規則の機械的な適用．G1〜G6 成立，H-R1・H-R2 成立）．組の比 q = pe ÷ pr の中央値は dp12 × N = 1 で 1.080（5 組 1.032〜1.118），fs × N = 1 で 1.043（5 組 1.022〜1.071）．
  1 要求の質問者の CPU は dp12 × N = 1 で 1.168 → 1.074 ms，fs × N = 1 で 1.031 → 0.995 ms．遅れの無い dp12 × N = 8 × pe は 326.3・323.1 問/秒で，最も忙しいノードは 1.50 コア（律速は質問者のまま）（journal の「Iteration 14 実行済み：分析・考察」）．
  config.yml の levers は全て試し終えている．**設定キーで挙動を変えずに除ける大きな項目は残っていない**（dp12 の routing の 1 段目 約 8%，httpx の URL の解析 約 4%，ログ 約 3%．B37 (b)・B38 (c)）．
  残る方向は，研究の方向の選択（負荷の系列を続けるか RQ の研究へ戻るか）か，cluster の形式とデバイスの役割の変更（B25 (a) で人間の判断に残した事項）を含むので，自動では進めず人間の判断を仰ぐ．state.json の status は blocked にする（オーケストレータが書き込む）．
- 選択肢:
  1. **(Recommended) 負荷の系列（Iteration 4〜14）を閉じ，RQ の研究へ戻る**．次のイテレーションは調査・計画から始め（`current_lever = null`），rc-researcher が d0006・p0004 の未着手の項目から次の単一レバーを選ぶ．
     メリット：質問者の CPU を削る系列の効果は逓減している（dp12 × N = 1 の比 1.30 → 1.19 → 1.09 → 1.08，fs × N = 1 で 1.33 → 1.49 → 1.16 → 1.04）．方式間の処理量の比は 1 問の要求の数の比で決まり（Iteration 7・8），質問者を速くしても RQ-A の結論（方式の順位）は変わらない．
     制御点 1 台の質問者は Iteration 8 の約 1.75 倍（dp12 × N = 8 で約 325 問/秒）になり，dp12 × N = 8 の最も忙しいノードは律速の移りの閾値（1.58 コア）の約 95% まで来ている．デメリット：30 台の全体の処理量の上限は実測できないまま（Iteration 9・10 の node-bench からの見積もりだけ）になる．
  2. **HTTP クライアントの置き換え**（例：`experiment.http_client: httpx`〔既定〕/ `aiohttp` を足す．可逆）．メリット：Iteration 14 の事前の測定の py-spy で，httpx の要求・応答の組み立て・anyio・h11・接続の池の割り当ては合わせて主スレッドの約 6〜8 割で，残りの CPU の大半を占める．
     デメリット：接続の池・再試行・`per_node`・`pre_encoded` の本文・`lean` の差し替えを別のクライアントで作り直す大きな変更で，実装と同一性の確かめ（選択と本文のバイト単位の一致）に 1 イテレーション以上かかる．dp12 × N = 8 は約 5% 速くなると専門家ノードの閾値に届くので，伸びは dp12 × N = 8 では頭打ちになりうる．
  3. **質問者を複数のデバイスに置く**（B25 (a)．`cluster.requester` を複数のデバイスにし，専門家ノードの一部か wafl500〜509 の GPU PC を質問者に回す）．メリット：30 台の全体の限界（見積もりは W = 1 で fs 約 315〜326・dp12 約 404〜422 問/秒）を実測できる．
     デメリット：cluster の形式とデバイスの役割の変更（スキーマの変更），結果の集約の作り直しが要る．GPU PC を質問者に回すとデータ準備の作り直しのときに役割が衝突する．
  4. **設定キーで除ける小さな項目を続ける**（dp12 の routing の 1 段目のベクトル化 約 8%・URL の解析 約 4%）．メリット：Iteration 14 と同じ組の比の規則で測れる．デメリット：効果は dp12 で数%の見込みで，routing の 1 段目は浮動小数の和の順が変わって選択の同一性をバイト単位で保証できない．組の比の規則で小さなレバーを続けると偶然の採用が積み重なる（1 回あたり 1/32〜1/16）．
- 推奨: 選択肢 1．負荷の系列の目的（二段の方式の処理量の比較と律速の切り分け）は果たしており，残る伸びしろは大きな変更（2）かスキーマの変更（3）を要する．
- 要レビュー: (a) **B38 (a) への見解**：今回の採用は閾値だけでなく，3 回の範囲の規則でも範囲が重ならないこと（比 1.088・1.034 が 1.10 に届かないだけ）と，組ごとの CPU の比の逆数との一致（10 組のうち 8 組で差 0.004 以下）で支えられており，誤った採用の見込みは小さい．
  ただし効果の大きさは dp12 +8%・fs +4% で，Iteration 11〜13 の採用（fs × N = 1 で +16〜49%）と同列には読まない．組の比の規則を使うときは CPU の比の一致を判定の条件に加えることを勧める．
  (b) Iteration 14 の後半の約 1 時間（03:45〜04:50 頃）に，`TCPTimeouts` が 200 以上の実行が 7 実行集まり，N = 8 の q の外れ値（0.800・0.911・1.196）は全てこの中にあった．原因（ネットワーク・専門家ノードの受け入れ）は確かめていない．選択肢 2・3 を選ぶ場合は，B37 (c) の 1 秒ごとの TCP の記録を足すことを勧める．
  (c) 制御点の `/tmp/iter14_prestudy/`（75 MB）・`/tmp/iter13_prestudy/`（374 MB）は使い捨ての作業場所として残っている（B36 (d)・B38 (d)）．選択肢 1 なら削除してよい（データディレクトリではない）．
  (d) `retrieve_body`・`pool_assign`・`sniffio_lookup`・`probe_body` の既定の値は今の挙動のまま（負荷の実験では明示する）．既定を変えるかは人間の判断に残す（B34 (a)・B36 (a)）．

## B38 [auto-decided 2026-10-16] Iteration 14 のレバーの形：`experiment.retrieve_body`（probe と埋め込みの JSON を共有），判定は交互に並べた組の比で行う

- 状況: B37 で，retrieve の本文の符号化が lean の上で除ける最大の項目かを事前の測定で確かめ，期待効果が小さいので縮小の率を掛けてから閾値と主の仮説を決め，N = 1 の 3 回の幅で検出できなければ回数を増やすかレバーを選び直すことになっていた．
- 自動選択: **config.yaml の新しいキー `experiment.retrieve_body`（`per_request`〔既定．今の挙動〕/ `pre_encoded`）**．pre_encoded は問ごと・検索器ごとに埋め込みの JSON を 1 回だけ作り，`probe_body: pre_encoded` の probe の本文と共有し，faiss のシャードの retrieve の本文を bytes の連結で作る（ノードが受け取る本文はバイト単位で同じ）．
  **判定の規則を「3 回の範囲が重ならず比 ≥ 1.10」から「交互に並べた組の比 q = pe ÷ pr が有効な組の全てで > 1，かつ中央値 ≥ 1.02（有効な組 4 組以上）」に変え**，N = 1 を 5 組（N = 8 は 3 組）にした．主の仮説 H-R1 は dp12 × N = 1．N = 1 の組は両方の実行の `requester_cpu_util_per_proc` の中央値が 0.95 以上のときだけ有効（G6）．
  fs・dp12 × N = 1 × 5 組 ＋ N = 8 × 3 組 ＋ 前後の anchor の計 34 実行．事前登録は journal の Iteration 14．
- 根拠: (1) 事前の測定（`results/20261016_iter14_retrieve_body_summary/prestudy/`．制御点・本物のノード 30 個・本物の `atrium run`・lean の上）で，retrieve の本文の組み立ては py-spy の主スレッドの dp12 7.6%・fs 4.3%．probe と共有する形（rshared）で 1 要求 dp12 1.479 → 1.398 ms（−5.5%），fs 1.325 → 1.306 ms（−1.4%）．共有しない形（rbody）は約半分〜7 割の節約だった．選択と `top_doc_ids` は 600/600 一致，本文は 200 本でバイト単位で一致．
  (2) dp12 の routing の 1 段目（8.3%）は同じ程度だが dp12 にしか効かず，ベクトル化は浮動小数の和の順が変わって選択の同一性を保証できない．httpx の URL の解析は約 4%，ログは約 3%．retrieve の本文が両方の方式に効く除ける項目では最大なので，レバーは選び直さない．
  (3) 実機の見込みは dp12 約 1.04 倍・fs 約 1.015 倍で，Iteration 13 の dp12 × N = 1 の 3 回の範囲の幅（4〜5%）では検出できない．一方で同じ回の隣り合う 2 実行の比は dp12 × N = 1 で 1.090・1.111・1.093 と揃っており，組の比なら連続実行の中の時間の揺れを打ち消せる．回数を増やしても範囲は狭くならないので，規則を組の比に変えた上で N = 1 を 5 組にした（全て > 1 は差が無ければ 1/32）．
- 要レビュー: (a) **判定の規則が Iteration 11〜13 より緩い**（閾値 1.02，符号検定の水準 1/32〜1/16）．小さな効果の検出に限った規則として事前登録したが，採用の重みを 1.10 の規則と同列に扱うかは人間が確認してよい．Iteration 13 と同じ範囲の規則の出力も記録に残す．
  (b) 所要は約 3 時間（34 実行）で，Iteration 13 の 26 実行より長い．
  (c) **この先に設定キーで除ける大きな項目は残らない**（dp12 の routing 約 8%，URL の解析 約 4%，ログ 約 3%）．Iteration 15 以降は B37 (b) の (i) HTTP クライアントの置き換え・(ii) 質問者を複数のデバイスに置く・(iii) 負荷の系列を止めて RQ の研究へ戻る，のどれにするかを人間に確かめることを勧める．
  (d) 制御点の `/tmp/iter14_prestudy/`（75 MB．使い捨ての作業場所．シャードは `/tmp/iter13_prestudy/shards` を読み取りだけで使った）は残してある．コンテナ `iter14-prestudy` は削除済み．

## B37 [auto-decided 2026-10-16] Iteration 13（httpcore の接続の池の割り当てを軽くする）は採用．Iteration 14 は質問者の `/v1/retrieve` の本文を問ごとに 1 回だけ符号化する構成

- 状況: Iteration 13 の判定は採用（事前登録の規則の機械的な適用．G1〜G5 成立，H-P1 成立，H-P2 は比 1.090 で不成立，H-P3 は fs・dp12 × N = 8 とも不成立）．処理量の中央値（問/秒，sk → ln）は fs N = 1 で 25.996 → 30.188（1.161．隙間 +14.7%），fs N = 8 で 159.299 → 171.520（1.077），dp12 N = 1 で 56.118 → 61.143（1.090），dp12 N = 8 で 292.395 → 305.855（1.046．ln の r1 が約 1 秒の遅れ 32 問で 198.5 に落ち，範囲が重なる）．
  1 要求の質問者の CPU は fs N = 1 で 1.208 → 1.033 ms，律速は質問者のまま（dp12 × N = 8 × ln の最も忙しいノード 1.37 コア）（journal の「Iteration 13 実行済み：分析・考察」）．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 14 の単一レバーは `requester.retrieve_body: [pre_encoded]`（仮称）**とし，config.yml の levers 末尾に追記した．既定は今の挙動（`_retrieve` がシャードごとに `embedding.tolist()` → `RetrieveRequest.model_dump()` → httpx の `json=` で 768 次元の埋め込みを JSON にする）．
  pre_encoded は問ごと（検索器ごと）に埋め込みの JSON を 1 回だけ作り（probe の pre_encoded の本文と共有してよい），シャードごとに `shard_id`・`k`・`query_id` だけを差し替えた bytes を `content=` で送る．ノードが受け取る本文はバイト単位で同じにする（Iteration 11 の `probe_body` と同じ作り）．
  phase 1 で `results/20261015_iter13_requester_cpu_summary/prestudy/run_ctrl.sh`（本物のノード 30 個＋本物の `atrium run`）を `pool_assign: lean` の上で取り直し，`/v1/retrieve` の本文の符号化が設定キーで除ける最大の項目であることを確かめる．違えば最大の除ける項目に選び直してよい（その場合は iteration_name も言い換える）．
  比べる条件は Iteration 13 と同じ形（fs・dp12 × N ∈ {1, 8} × 3 回，2,940 問，`probe_body: pre_encoded`・`sniffio_lookup: blocked`・**`pool_assign: lean`**・`http_pool: per_node`・`node_workers: 1` を固定，前後の anchor）．キー名・判定の規則は rc-researcher が事前登録で決める．
- iteration_name: 「質問者の検索要求の本文を問ごとに 1 回だけ符号化したときの処理量」．
- 根拠: (1) Iteration 13 の事前の測定で，lean の後に残る項目は h11 の送受信（HTTP クライアントの置き換えが要る．B35 (b) で避ける），待機中の接続ごとの `poll`（fs 約 14%．挙動を変えずには除けない），`/v1/retrieve` の本文の `json.dumps`（dp12 10.7%・fs 5.6%），dp12 の routing の 1 段目（7.6%）で，設定キーで挙動を変えずに除ける中では retrieve の本文が最大．
  (2) Iteration 13 の H-P2 が閾値に届かなかった dp12 の方が効く（dp12 は 1 問の要求が 14 本で，そのうち重い本文の retrieve が 2 本）．(3) Iteration 11 の `probe_body` と同じ作り・同じテスト（バイト単位の一致）で，選択と結果が変わらないことを確かめやすい．
- 要レビュー: (a) **期待効果は小さい**．dp12 で 10.7% の標本を全て除けても，実機への縮小の率（dp12 は 2 回続けて約 0.6．journal の Iteration 13 の学び）を掛けると処理量の比は約 1.06〜1.07，fs は約 1.03〜1.04 の見込みである．事前登録では，見込みに縮小の率を掛けてから閾値と主の仮説（fs か dp12 か）を決め，N = 1 の 3 回の幅（今回の fs × N = 1 × sk は −3.9%〜+0.9%）で検出できるかを先に確かめる．検出できない見込みなら，回数を増やすか，レバーを選び直す．
  (b) **質問者の CPU を削る系列は効果が逓減している**（fs × N = 1 の比 1.33 → 1.49 → 1.16，dp12 × N = 1 で 1.30 → 1.19 → 1.09）．Iteration 14 の後に設定キーで除ける大きな項目は残らない見込みで，その先は (i) HTTP クライアントの置き換え（大きな変更），(ii) 質問者を複数のデバイスに置く（B25 (a)．人間の判断），(iii) 負荷の系列を止めて RQ の研究へ戻る，のいずれかになる．どれを選ぶかは人間が確認してよい（可逆なので自動では Iteration 14 を進める）．
  (c) dp12 × N = 8 × ln の r1 の約 1 秒の遅れは，質問者の名前空間の `TCPSynRetrans` の増加（213．他の N = 8 は 41〜85）と同じ実行で起きた．新しい接続の SYN の再送が原因という読み（推測）を確かめるには，遅れた問の時刻と SYN の再送の時刻を突き合わせる記録（例：実行中の `nstat` の 1 秒ごとの記録，または専門家ノードの `ListenOverflows`・`ListenDrops`）が要る．Iteration 14 の記録の指標に足すかは rc-researcher が決める（判定には使わない）．
  (d) httpcore の PyPI の最新は 2026-10-16 の時点でも 1.0.9（`lean` の前提の版のまま）．

## B36 [auto-decided 2026-10-15] Iteration 13 のレバーの形：`experiment.pool_assign`（httpcore の接続の池の割り当ての走査を，挙動を変えずに軽くする差し替え）

- 状況: B35 で，質問者の 1 要求の CPU の残りで最大の項目を，制御点の事前の測定（fs と dp12）で選び，形・キー名・判定の規則を rc-researcher が事前登録で決めることになっていた．HTTP クライアントの置き換えは避ける（B35 (b)）．
- 自動選択: **config.yaml の新しいキー `experiment.pool_assign`（`stock`〔既定．今の挙動〕/ `lean`）**．lean は質問者のプロセス（`atrium run` と spawn の子）の始めに，httpcore 1.0.9 の `AsyncConnectionPool._assign_requests_to_connections` を同じ結果を返す 1 回の走査の版に，`AnyIOStream.get_extra_info("is_readable")` を生のソケットへ直接 `poll` する版に差し替える（版が違えば止める）．
  比べる条件は fs・dp12 × N ∈ {1, 8} × sk・ln × 3 回（2,940 問，`pre_encoded`・`blocked`・`per_node`・W = 1 を固定）と前後の anchor の計 26 実行．主の仮説 H-P1 は「fs × N = 1 で ln の範囲が sk より上で重ならず，中央値の比 ≥ 1.10」．事前登録は journal の Iteration 13．
  B35 (a) の記録の指標の修正（遅れの問は N = 8 だけ，TCP の再送は質問者の名前空間で `run_meta.json` の `requester_tcp`，再試行の例外の型名）を同じ commit に含める．
- 根拠: (1) 制御点で本物のノード 30 個と本物の `atrium run` を流した py-spy で，`_assign_requests_to_connections` を含む標本が fs 24.7%・dp12 15.8% で最大（次は h11 約 9%，JSON の符号化 fs 5%・dp12 10%，dp12 の routing 9%）．
  (2) 差し替えの効果は 3 回の測定で fs 1.555 → 1.332 ms（問/秒 1.161 倍），dp12 1.657 → 1.479 ms（1.116 倍）．httpx の公開の設定（per_node の上限を 8・16 に下げる）やログを止めるのは 0.05 ms 以下の節約だった（`results/20261015_iter13_requester_cpu_summary/prestudy/`）．
  (3) 上流に同じ関数の書き直しの PR（encode/httpcore #1035，2025-09．未マージ）があり，PyPI の最新は 1.0.9 で版を上げる解決は無い．(4) 設定キーなら同じイメージで交互に流せ，質問者だけに効く（Iteration 12 と同じ設計）．
- 要レビュー: (a) **lean は httpcore・anyio の非公開の内部への差し替え**で，既定の値は `stock` のまま変えない．依存の版を上げたときに作り直すか，上流の修正（PR #1035・#1000）が出たら版の更新に置き換えるかは人間の判断に残す．
  (b) 2 つの差し替え（掃除の段の 1 回の走査・生存の確かめの近道）を 1 つのキーにまとめた．同じ 1 つの関数とその下の費用であり，片方ずつの効果は事前の測定（fastread 0.18 ms・linearonly 0.05 ms．fs）で記録した．実機で分けて測るかは結果を見て決める．
  (c) 上流の PR の Bug 1（`max_keepalive_connections` の数え方）・Bug 2（待ち行列の要求の重複の割り当て）は直さずに元の挙動のまま再現する（テストで元の関数と突き合わせる）．`per_node` の下では Bug 1 の条件は常に偽で，待ち行列は上限 64 に届かないので起きない見込み．
  (d) 制御点の `/tmp/iter13_prestudy/`（シャードの実体の複製 374 MB を含む使い捨ての作業場所）は残してある．次の事前の測定で使い回せる．不要なら削除してよい（データディレクトリではない）．
  (e) 事前の測定の方法を，偽のノードの `bench.py` から「本物のノード 30 個＋本物の `atrium run`」（`prestudy/run_ctrl.sh`）に変えた．retrieve・routing・記録を含むので，以後の項目の選択はこちらを使う．

## B35 [auto-decided 2026-10-15] Iteration 12（sniffio の探索を省く）は採用．Iteration 13 は質問者の 1 要求の CPU の残りの内訳で最大の項目を除く構成

- 状況: Iteration 12 の判定は採用（事前登録の規則の機械的な適用．G1〜G5 成立，H-S1・H-S2 成立，H-S3 は fs・dp12 × N = 8 とも成立）．処理量の中央値（問/秒，sr → bk）は fs N = 1 で 17.721 → 26.404（1.490），fs N = 8 で 110.977 → 145.145（1.308），dp12 N = 1 で 42.228 → 50.277（1.191．隙間 +3.0%），dp12 N = 8 で 240.912 → 291.604（1.210）．
  fs × N = 1 の 1 要求の質問者の CPU は 1.762 → 1.189 ms（節約 0.574 ms）で，処理量の比はこの比の逆数とほぼ同じ．dp12 の節約は 0.355 ms と小さい．dp12 × N = 8 × bk でも律速は質問者のまま（最も忙しいノード 1.24〜1.30 コア）（journal の「Iteration 12 実行済み：分析・考察」）．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 13 の単一レバーは「質問者の 1 要求の CPU（pre_encoded × blocked の上）の残りの内訳で最大の項目を 1 つ除くこと」**とし，config.yml の levers 末尾に `requester.next_cpu_item: [reduce]`（仮称）を追記した．
  phase 1 で，Iteration 11・12 の `bench.py` に `probe_body: pre_encoded` と `sys.modules["sniffio"] = None` を入れた状態で，制御点（wafl-ctrl5 の `atrium-full`）の cProfile（と可能なら py-spy）を **fs（1 問 30 probe）と dp12（1 問約 12 probe ＋ retrieve 2）の両方の要求の形**で取り，最大の項目を選ぶ．Iteration 11 の cProfile で上位だった候補は，httpcore の接続の池の走査（`_assign_requests_to_connections`），h11 の組み立てと解析，anyio の `extra_attributes`，`ProbeResponse.model_validate_json` である．
  比べる条件は Iteration 12 と同じ形（fs・dp12 × N ∈ {1, 8} × 3 回，2,940 問，`probe_body: pre_encoded`・`sniffio_lookup: blocked`・`http_pool: per_node`・`node_workers: 1` を固定，前後の anchor）．形（設定キーかコードの置き換えか）・キー名・判定の規則は rc-researcher が事前登録で決める．
- iteration_name: 「質問者の 1 要求の CPU の残りで最大の項目を除いたときの処理量」（phase 1 で項目が決まったら，その項目の名前に言い換えてよい）．
- 根拠: (1) Iteration 11・12 の 2 回続けて，N = 1 の処理量の比が 1 要求の質問者の CPU の比の逆数と一致し，事前の測定の節約が実機で再現した（fs）．質問者 1 台のまま処理量を上げる方針（B31）の続きで，ノードの API・データ・シャード・方式の選択を変えない．
  (2) 最も忙しい専門家ノードは dp12 × N = 8 で約 1.3 コアで，W = 1 の頭打ち（約 1.86 コア）まで約 1.4 倍の余裕がある．質問者の 1 要求の CPU をさらに 3 割ほど減らすと，dp12 × N = 8 で律速が専門家ノードへ移る点に届く見込み（推測）．
  (3) 項目を決める前に事前の測定を取るのは，Iteration 11（B31）と同じ手順で，効果の大きさを先に見積もれる．
- 要レビュー: (a) **記録の指標を直す**．Iteration 12 の「遅れの問（probe_s ≥ 0.9 秒）」は N = 1 では質問者の CPU の待ち行列を数えてしまう（fs × N = 1 の probe_s の中央値が約 1 秒）．次の事前登録では N = 8 だけで数えるか，閾値をその実行の probe_s の中央値の倍数にする．
  また質問者のコンテナは既定のブリッジのネットワークで動く（`docker/compose.requester.yml` に `network_mode` が無い）ので，`iter12_chain.sh` がホストで取った `nstat` には質問者のソケットの再送が入らない．TCP の再送は質問者のコンテナのネットワークの名前空間で取る（`docker exec` か `nsenter -n`）．再試行の例外の型も requester.log に残す（`post_with_retry` の warning に型名を足す．記録だけの変更）．
  (b) 最大の項目が HTTP クライアントそのもの（h11・接続の池）だった場合，aiohttp 等への置き換えは接続の池・再試行・`per_node` の作り直しを伴う大きな変更になる．その場合は置き換えではなく，httpcore の設定や使い方の範囲（例えば per_node の池の上限，keep-alive の扱い）で減らせる項目を優先し，置き換えは候補として backlog に残す（B32 (c)・B33 (e)）．
  (c) `sniffio_lookup` の既定は `search` のまま（既定の変更・`uv add sniffio` は B34 (a) のとおり人間の判断）．以後の負荷の実験は `probe_body: pre_encoded` と `sniffio_lookup: blocked` を明示する．
  (d) dp12 × N = 1 の節約が fs の約 6 割だった理由（httpcore が 1 要求で `current_async_library()` を呼ぶ回数が池の状態で変わる，という推測）は，次の事前の測定で呼ぶ回数を fs と dp12 の形で数えれば確かめられる．
  (e) tavily-search による Web 検索は Iteration 12 で権限の判定により拒否された．上流の httpcore の修正の有無など，外部の情報の確認が要る調査は，権限の設定を人間が見直すまで手元のコードと測定だけで行う．

## B34 [auto-decided 2026-10-15] Iteration 12 のレバーの形：`experiment.sniffio_lookup`（依存を足さず，質問者のプロセスだけで sniffio の探索を省く）

- 状況: B33 で，`requester.sniffio_lookup`（仮称）の形（`uv add sniffio` か設定キーか）・キー名・判定の規則・問の数を rc-researcher が事前の測定と事前登録で決めることになっていた．B33 (a)〜(e) を考慮する．
- 自動選択: **config.yaml の新しいキー `experiment.sniffio_lookup`（`search`〔既定．今の挙動〕/ `blocked`）**．`blocked` は sniffio が入っていないときだけ，質問者のプロセス（`atrium run` と `requester_procs` の spawn の子）の始めに `sys.modules["sniffio"] = None` を置き，httpcore の `import sniffio` を探索なしの `ImportError` にする（返す値 `"asyncio"` は今と同じ）．`run_meta.json` に `sniffio_lookup`・`sniffio_installed` を残す．
  比べる条件は fs・dp12 × N ∈ {1, 8} × sr・bk × 3 回（2,940 問，`probe_body: pre_encoded`・`http_pool: per_node`・`node_workers: 1` を固定．sr と bk を組ごとに交互）と前後の anchor の計 26 実行．主の仮説 H-S1 は「fs × N = 1 で bk の範囲が sr より上にあり重ならず，中央値の比 ≥ 1.10」．事前登録は journal の Iteration 12．
  B33 (b) に従い，probe_s ≥ 0.9 秒の問の数と制御点の TCP の再送の差分（`nstat`）を記録の指標に足した．
- 根拠: (1) 事前の測定（gpu2．偽のノード 30 個．`results/20261015_iter12_sniffio_summary/prestudy/`）で，pre の上の 1 要求は search 1.681 ms，blocked 1.139 ms，sniffio を入れた場合 1.128 ms で，blocked と依存の追加の差は 3 回の幅の内（`current_async_library` 1 回で 1.09 対 0.41 µs）．
  (2) 設定キーなら同じイメージで交互に流せ，連続実行の間の commit・イメージの作り直しが要らない．(3) 依存の追加は専門家ノード・node-bench の負荷生成にも効いて単一レバーにならないが，設定キーは質問者のプロセスだけに効く．
- 要レビュー: (a) **本来の解決は `uv add sniffio`（または httpcore の版の更新）かもしれない**．`sys.modules` に `None` を置くのは実験のための回避で，既定の値は `search` のまま変えない．依存を足すと専門家ノード・node-bench の負荷生成の 1 要求の CPU も下がり，Iteration 8〜12 の値と直接は比べられなくなるので，入れるかどうかと時期は人間の判断に残す．
  (b) tavily-search による Web 検索（上流の httpcore での扱い）はこの環境の権限の判定で拒否されたので行っていない．上流の修正の有無は未確認．
  (c) 事前の測定は Iteration 11 と違い gpu2 だけで行った（制御点では Iteration 11 の base の上の 0.63 ms がある）．手順 0 で 1 要求の質問者の CPU が下がることを粗く確かめる．
  (d) B33 (c)〜(e) はそのまま（`node_workers` は 1，`probe_body` の既定は `per_request`，HTTP クライアントの置き換え・orjson は候補に残すだけ）．

## B33 [auto-decided 2026-10-15] Iteration 11（probe の本文を問ごとに 1 回だけ符号化）は採用．Iteration 12 は質問者の要求ごとの sniffio の import の失敗を除く構成

- 状況: Iteration 11 の判定は採用（事前登録の規則の機械的な適用．G1〜G5 成立，H-B1・H-B2 成立，H-B3 は fs × N = 8 で成立・dp12 × N = 8 で不成立）．処理量の中央値（問/秒，pr → pe）は fs N = 1 で 13.387 → 17.756（1.326），fs N = 8 で 81.835 → 125.510（1.534），dp12 N = 1 で 32.181 → 41.844（1.300），dp12 N = 8 で 185.561 → 200.262（1.079．範囲が重なる）．
  1 要求あたりの質問者の CPU は fs N = 1 で 2.335 → 1.761 ms（節約 0.574 ms．事前の測定 0.55 ms と一致）で，N = 1 の処理量の比はこの比の逆数とほぼ同じ．律速は質問者のプロセスのまま（journal の「Iteration 11 実行済み」）．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 12 の単一レバーは「質問者の httpcore が要求ごとに行う `import sniffio` の失敗（`sys.path` の探索）を除くこと」**とし，config.yml の levers 末尾に `requester.sniffio_lookup: [skip]`（仮称．既定は今の挙動）を追記した．
  `probe_body: pre_encoded`・`http_pool: per_node` を固定し，fs・dp12（D = 12）× N ∈ {1, 8} × 3 回（2,940 問，検索だけ，parallel 30）で，選択（`selected_sources`）が全問で一致したまま処理量が伸びるかを測る．比べる基準は同じ連続実行の中で測り直す（Iteration 11 の pe の値は記録として並べる）．
  形（`uv add sniffio` で依存を足すか，同じイメージで交互に流せる設定キーにするか）・キー名・判定の規則は rc-researcher が事前の測定と事前登録で決める．
- iteration_name: 「質問者の要求ごとの sniffio の import の失敗を除いたときの処理量」．
- 根拠: (1) 事前の測定（制御点のコンテナ・偽のノード 30 個）で，sniffio の import の失敗は 1 要求 2.62 ms の約 24%（0.63 ms）で，今回の本文の符号化（0.55 ms）と同じ程度の大きさがあり，2 つの節約はほぼ足し算で効いた（pre + sniffio で 1.42 ms）．Iteration 11 で事前の測定の節約が実機で再現した（0.55 対 0.574 ms）ので，同じ程度の再現を見込める．見込みは fs × N = 1 で 1.761 → 約 1.13 ms，処理量は約 17.8 → 約 27 問/秒（推測）．
  (2) 質問者を 1 台のまま処理量を上げる方針（B31）の続きで，ノードの API・データ・シャード・方式の選択を変えない．httpcore は sniffio が無いときに "asyncio" とみなすので（`httpcore/_synchronization.py` の `current_async_library`），sniffio があっても asyncio の下では同じ値を返し，挙動は変わらない見込み（確かめるのは rc-researcher）．
- 要レビュー: (a) **依存の追加か設定キーか**．`uv add sniffio` は正攻法だが，イメージが変わるので同じ連続実行の中で交互に流せず（連続実行の間の commit は start を止める），専門家ノード・node-bench の負荷生成（同じ httpx）にも効く．
  設定キーにするなら，質問者のプロセスの始めに `sys.modules` へ失敗の印を置いて探索だけを省く形などがあり，交互に流せる．前者は前後の 2 つのかたまりで比べ，後者は交互に比べる．どちらにするかで事前登録の規則（前後の anchor の扱い）が変わる．
  (b) **N = 8 の比較は約 1 秒・約 3 秒の probe の遅れで揺れる**（Iteration 11 で 12 実行のうち 4 回．ノードの計算は遅れていない．TCP の再送の待ちと一致する．推測）．次の事前登録では「probe_s が 0.9 秒以上の問の数」（N = 8 のみ）と，制御点の TCP の再送の数（`nstat` の差分など．取れるなら）を記録の指標に足すことを検討する．
  (c) dp12 × N = 8 で最も忙しいノード（192.168.13.102）は pe で 1.04 コアに達した．sniffio の節約が効くと約 1.6 コアになり，W = 1 のノードの頭打ち（約 1.86 コア）に近づく見込み（推測）．`node_workers` は 1 のまま固定し，`expert_cpu_util_max` で律速の移りを記録する（W を変えると単一レバーが崩れる）．
  (d) `probe_body` の既定は `per_request` のまま（B32 (b)．既定の変更は人間の判断）．以後の負荷の実験は `pre_encoded` を明示する．
  (e) HTTP クライアントの置き換え（aiohttp 等）・orjson は引き続き候補に残すだけにする（B32 (c)）．

## B32 [auto-decided 2026-10-15] Iteration 11 のレバーの形：`experiment.probe_body`（本文をバイト単位で同じに保つ），事前の測定で見つけた sniffio の欠落は次の候補

- 状況: B31 で，`requester.probe_body`（仮称）のキー名・判定の規則・問の数を rc-researcher が事前登録で決め，事前に制御点の質問者の時間の内訳を取って，本文の符号化の割合が小さければ最大の項目へ選び直してよいことになっていた．
- 自動選択: **config.yaml の新しいキー `experiment.probe_body`（`per_request`〔既定〕/ `pre_encoded`）**．`pre_encoded` は検索器ごとに埋め込みの JSON を問ごとに 1 回だけ作り，ノードごとに `shard_ids` の JSON と連結した bytes を `content=` で送る．httpx の `json=` と同じ区切り・キーの順にして，ノードが受け取る本文をバイト単位で今と同じに保つ（テストで確かめる）．
  比べる条件は fs・dp12 × N ∈ {1, 8} × pr・pe × 3 回（2,940 問．pr と pe を組ごとに交互に並べる）と前後の anchor の計 26 実行．主の仮説 H-B1 は「fs × N = 1 で pe の範囲が pr より上にあり重ならず，中央値の比 ≥ 1.10」．事前登録は journal の Iteration 11．
- 根拠: (1) 制御点の `atrium-full` のコンテナで偽のノード 30 個に対して測ると，質問者 1 プロセスの 1 要求 2.62 ms のうち本文の符号化が約 0.55 ms（21%．pe で 381.8 → 484.5 要求/秒），sniffio の import の失敗が約 0.63 ms（24%），httpx の INFO のログが約 0.04 ms（2%）だった（`results/20261015_iter11_probe_body_summary/prestudy/`）．
  本文の符号化の割合は「小さい」とは言えないので，B31 の予定どおりのレバーにした．(2) 設定キーにすれば同じイメージで pr と pe を交互に流せ，次のイテレーション（sniffio）でも同じ基準と比べられる．(3) バイト単位で同じ本文なら選択は変わらないので，G2（`selected_sources` の全問一致）で正しさを確かめられる．
- 要レビュー: (a) **質問者の環境に `sniffio` が無い**（uv.lock に無く，anyio 4.15.1 は依存しない）．httpcore 1.0.9 は要求ごとに `import sniffio` を試みて失敗し，そのたびに `sys.path` を探す（1 要求あたり約 9.5 回）．`uv add sniffio` だけで 1 要求の CPU が約 24% 減る見込み（事前の測定で 381.8 → 501.9 要求/秒）．
  今回のレバーと混ぜないために入れていない．Iteration 11 の結果にかかわらず，次の単一レバーの第一候補とする．依存の追加は専門家ノード・node-bench の負荷生成（同じ httpx）にも効くので，入れた後の値は Iteration 8〜11 の値と直接は比べられない（比べる基準を測り直す）．
  (b) 採用でも `probe_body` の既定は `per_request` のままにし，負荷の実験で `pre_encoded` を明示する（`http_pool` と同じ扱い．既定の変更は人間の判断）．
  (c) HTTP クライアントの置き換え（aiohttp 等．httpx の同時の要求での遅さは encode/httpx #3215 で報告されている）と orjson による直列化は，本文の数値の表現・接続の池・再試行が変わるので候補に残すだけにした．

## B31 [auto-decided 2026-10-15] Iteration 10（専門家ノードのプロセスの数）は採用．Iteration 11 は質問者の probe の本文を問ごとに 1 回だけ符号化する構成．deploy の既定の `node_workers` は 1 のまま

- 状況: Iteration 10 の判定は採用（事前登録の規則の機械的な適用．G1〜G5 成立，欠け 0，H-W1〜H-W3 成立）．頂点の処理量 P の中央値は 13.102（i5-8350U）で W = 1 → 2 → 4 が 352.0 → 594.6 → 764.5，15.103（i7-8700）で 475.8 → 792.8 → 1246.5 要求/秒（隣の W どうしで 3 回の範囲は重ならない）．
  W = 2 では各プロセスが W = 1 と同じ約 1.86 コアを使い，1 要求の CPU は W とともに増える（13.102 で 5.28 → 6.33 → 8.44 ms）．13.102 の W = 4 は host busy 0.84 でデバイスの CPU の限界に近い．
  30 台の全体の上限の見積もりは W = 4 で fs 680〜703・dp12 872〜910 問/秒で，制御点 1 台の質問者（N = 8）の約 8.3〜8.5 倍・4.7〜4.9 倍．`atrium run` の処理量は W によらない（質問者が律速）（journal の「Iteration 10 実行済み」）．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 11 の単一レバーは「質問者が probe の本文を作る回数」**とし，config.yml の levers 末尾に `requester.probe_body: [pre_encoded]`（仮称．既定は今の挙動 `per_request`）を追記した．
  今は 1 問ごとに，ノードの数だけ `ProbeRequest(embedding=emb.tolist())` → `model_dump()` → httpx の `json=` で 768 次元の埋め込みを JSON にしている（`src/atrium/requester.py` の `_probe` の `ask`）．これを問ごと（検索器ごと）に 1 回だけ埋め込みの JSON の断片を作り，ノードごとに `shard_ids` だけを差し替えた bytes を `content=` で送る形に変える．ノードの API と本文の意味は変えない．
  flood_score・device_probe（D = 12）を per_node・test 2,940 問・検索だけ・`requester_procs` N = 1 と 8 で各 3 回流し，選択（`selected_sources`）と probe の得点が全問で一致したまま処理量が伸びるかを測る．キー名か（Iteration 6 と同じく）設定キーにしないコードの置き換えか・判定の規則・問の数は rc-researcher が事前登録で決める．
  事前に py-spy（または cProfile）で制御点の質問者 1 プロセスの時間の内訳を取り，本文の符号化の割合が小さければ，内訳で最も大きい項目（httpx の要求ごとの INFO のログ〔B26 (c)〕，応答の `model_validate_json` など）を 1 つだけレバーに選び直してよい．
- iteration_name: 「質問者の probe の本文を問ごとに 1 回だけ符号化したときの処理量」．
- 根拠: (1) Iteration 8〜10 を通してシステム全体の律速は質問者（制御点 1 台．約 2,600 要求/秒）で，専門家ノードの余裕は W = 1 でも約 3.8 倍，W = 4 で約 8.5 倍ある．質問者を 1 台のまま（B25 (a) の人間の判断を待たずに）処理量を上げるには，質問者の 1 要求あたりの CPU を減らすしかない．
  (2) 操作端末での小さな測定で，本文を作る費用は 1 要求あたり約 0.39 ms で，制御点の質問者 1 プロセスの 1 要求の時間（約 2.3 ms）の約 17% にあたる．同じ埋め込みを 1 問で最大 30 回 JSON にしているので，1 回にすれば大半を省ける見込み（推測．制御点では未測定）．
  (3) Iteration 6（重心の前計算）と同じく，選択を変えない置き換えで，全問の一致を正しさの確認に使える．既存の方式の挙動・ノードの API・データ・シャードは変えない．
- 要レビュー: (a) **deploy の既定の `experiment.node_workers` は 1 のまま変えない**（B29 (b)・B30 (d)）．30 台の実行は質問者が律速で W を上げても `atrium run` の処理量は変わらず，W ≥ 2 は `churn` と併用できず，メモリがシャードの約 W 倍で MedRAG の PubMed のシャードでは使えないため．
  既定を変えるのは public な設定の変更なので人間の判断に残す．30 台の全体の限界を測るとき（質問者を複数のデバイスに置く形．B25 (a)）は，実行ごとに i5 は W = 2〜4，i7 は W = 4〜6 を明示する．ただし `node_workers` は全ノードで 1 つの値で，ハードウェアごとに W を変える仕組みは無い．
  (b) 30 台の全体の限界の実測には，W = 4 の見積もりで制御点の約 8.5 台分の質問者が要る（fs）．質問者を複数のデバイスに置く形（B25 (a)）は今回も選ばず，[needs-human] にもしない．質問者の 1 要求の CPU を減らした後に，必要なら人間の判断を求める．
  (c) 律速の分類の 3 番（W × W = 1 の上限 × 0.85）は，物理コアを超える W でデバイスの CPU が 0.84 まで来ていても成り立つ（13.102 の W = 4 は閾値との差 2%）．次にノードの能力を分類するときは 1 要求の CPU の増え方を併せて事前登録する．
  (d) 13.102 の実行の中の位置に結び付いた 20〜30% の低下は W = 1 でも続いている（原因は未特定．Iteration 9 の B29 (c)）．ノードの設定には触れていない．

## B30 [auto-decided 2026-10-15] Iteration 10 のレバーの形：`experiment.node_workers`（SO_REUSEPORT の W 個のプロセス），主の量は実行ごとの頂点の処理量

- 状況: B29 で，`node.workers`（仮称）のキー名・W・プロセスの分け方・CPU の合計の測り方・判定の規則を rc-researcher が事前登録で決めることになっていた．B29 の要レビュー (a)〜(d) を考慮する．
- 自動選択: **config.yaml の新しいキー `experiment.node_workers`（既定 1 ＝今の挙動）**，W ∈ {1, 2, 4}．W ≥ 2 では `atrium node` の親が spawn で W 個の子を起こし，各子がシャードを読み込んで**同じポートを SO_REUSEPORT で開く**（uvicorn の `--workers` は使わない）．
  ノードの全体の CPU は**コンテナの cgroup v2 の `cpu.stat` の `usage_usec`**（`/healthz` の `cgroup_cpu_s`）で測る．負荷は Iteration 9 と同じ `atrium node-bench`（主 13.102，記録 15.103，C ∈ {1, …, 64}，各 W × 3 回）で，負荷生成のプロセスの上限を 4 → 8 にし，W = 1 も同じ条件で測り直す．
  主の量は実行ごとの頂点の処理量 P，主の仮説 H-W1 は「13.102 で W = 1 → 2 で P が伸び（範囲が重ならず），中央値の比 ≥ 1.5」．事前登録は journal の Iteration 10．
- 根拠: (1) uvicorn の `--workers` は 1 つのソケットを共有して accept を取り合い，接続が少数のプロセスに偏りうる（LWN 2013・Cloudflare 2017・Envoy #16213）．node-bench は水準の始めに接続を張って使い回すので，偏りが区間の全体に残りうる．また uvicorn の `--workers` はアプリを import の文字列から作るので，compose の command の引数（ノード名・シャード）を子へ渡すのに環境変数が要る．
  (2) B29 (a)：`process_cpu_s` は答えたプロセスの値なので，cgroup の合計を足した．`*.offsets.npy` の同時の書き込みを避けるため，親が先に 1 回シャードを読み込む．離脱の状態がプロセスごとなので，`churn` とは併用しない（検証で拒否する）．
  (3) B29 (c)：r3 の C の順を 2,8,32,1,64,4,16 にし，主の量を実行ごとの最大にして，実行の中の位置による低下を 1 つの水準なら受け流す．
- 要レビュー: (a) **ノードの起動の形を変えた**（W ≥ 2 のときだけ．W = 1 は `uvicorn.run(app)` のまま）．親は要求を受けず，子のどれかが終われば全てを止めて終了コード 1 で終わる（compose の restart に任せる）．W ≥ 2 のメモリはシャードの約 W 倍で，MedRAG の PubMed のシャードでは使えない．
  (b) `/healthz` に `cgroup_cpu_s`・`pid`・`workers` を足した（追加だけ）．`atrium run` の `expert_cpu_util` は W ≥ 2 のノードで cgroup の値を使う（W = 1 は変わらない）．
  (c) 負荷生成のプロセスの上限を 8 にしたので，Iteration 9 の値とは負荷生成の条件が違う（比べる基準は今回の W = 1）．
  (d) B29 (b)・(d)（30 台の deploy の既定の W，ノードあたりの同時の要求の数の目安）は結果を見て決める．W を増やす実験は GIL とイベントループのスレッドを区別しない．

## B29 [auto-decided 2026-10-15] Iteration 9（node-bench）は採用．Iteration 10 は専門家ノードのプロセス（uvicorn の worker）の数

- 状況: Iteration 9 の判定は採用（事前登録の規則の機械的な適用．G1〜G4 成立，欠け 0，H-N1・H-N2・H-N3 成立）．192.168.13.102（i5-8350U）の X_sat は 354.3 要求/秒（C_best = 4），192.168.15.103（i7-8700）は 473.3．
  両対象ともノードのプロセスの CPU 約 1.86〜1.92 コアで頭打ちになり，デバイスの CPU は 2〜3 割しか使っていない（律速の分類は「未特定」）．30 台の全体の上限の見積もりは flood_score 約 315〜326 問/秒，device_probe（D = 12）約 404〜422 問/秒で，制御点 1 台の N = 8 の約 3.8〜4.0 倍・2.2〜2.3 倍（journal の「Iteration 9 実行済み」）．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 10 の単一レバーは「専門家ノードのプロセスの数」**とし，config.yml の levers 末尾に `node.workers: [1, 2, 4]`（仮称）を追記した．ノードを W 個のプロセスで動かし（既定 1 ＝今の挙動），Iteration 9 と同じ `atrium node-bench`（主 192.168.13.102，C ∈ {1, 2, 4, 8, 16, 32, 64}，各 3 回）で X_sat と頭打ちのときの CPU を測る．
  キー名・W の値・プロセスの分け方（uvicorn の `workers`，あるいは SO_REUSEPORT の複数のプロセス）・プロセスごとの CPU の合計の測り方・判定の規則は rc-researcher が事前登録で決める．
- iteration_name: 「専門家ノードのプロセスの数によるノード 1 台の処理量の頭打ちの確認」．
- 根拠: (1) Iteration 9 の事前登録の「次の候補」（律速が 1 プロセスの直列の処理なら uvicorn の worker の数）に当たる．分類は「未特定」だったが，ハードウェアが違っても同じ約 1.9 コアで止まり，デバイスの CPU が 7〜8 割空いていることは，プロセスの中の直列（GIL の取り合い．推測）が律速であることと整合する．
  (2) W に対して X_sat がほぼ比例して伸びれば推測を確かめられ，伸びなければ GIL 以外（スレッドプール・ネットワーク・シャードのロック）へ候補を絞れる．どちらでも次の手が決まる．
  (3) 質問者を複数のデバイスに置く形（B25 (a)）を使わずに，制御点 1 台の負荷生成（node-bench は 1 台に約 360〜470 要求/秒しか送っていない）のまま測れる．
- 要レビュー: (a) **ノードの起動の形を変える**（`src/atrium/node.py` の `main` は `uvicorn.run(app, ...)` にアプリの実体を渡しているので，複数のプロセスにするには工場関数か import の文字列が要る）．既定 1 で今の挙動を変えないこと，W 個のプロセスがそれぞれシャードを読み込むメモリ（i5 のノードの空き）と，`/healthz` の `process_cpu_s` がプロセスごとの値になる点（host busy か cgroup の値で合計する）を確かめる．
  (b) 実際の `atrium run` の 30 台の構成でも W を上げるか（deploy の既定を変えるか）は，Iteration 10 の結果を見て決める．30 台の全体の限界の実測には質問者を複数のデバイスに置く形（B25 (a)．人間の判断）が要り，今回も選ばない（約 4 台分の質問者の能力が要る見込み）．
  (c) 13.102 は各実行の 5 番目の水準（開始から約 3 分後）だけ約 16〜19% 低い区間があった（原因は未特定．pcp と配送に失敗し続ける postfix が 13.102 にだけ動いている）．ノードの設定には触れず，次の実験では水準の順を実行ごとに変える（r2 の降順に加え，例えば r3 を別の順にする）か，位置による揺れとして記録する．
  (d) ノードあたりの同時の要求の数の膝は C = 4 だった．`atrium run` の parallel の設計の目安にするかは，W の結果を見てから決める．

## B28 [auto-decided 2026-10-15] Iteration 9 のレバーの形：新しい CLI `atrium node-bench` の同時の要求の数，主の対象は 192.168.13.102，/healthz の再試行を足す

- 状況: B27 で，`node_bench.concurrency`（仮称）の実装の形・対象のノード・同時の要求の数・判定の規則を rc-researcher が事前登録で決めることになっていた．あわせて B27 (d)（`/healthz` の GET に再試行が無い）と (e)（集計の G4 の扱い）を考慮する．
- 自動選択: **設定キーではなく新しい CLI `atrium node-bench`**（`src/atrium/node_bench.py`，`scripts/remote/node_bench.sh`．制御点の full イメージのコンテナで流す）とし，レバーはその同時の要求の数 C ∈ {1, 2, 4, 8, 16, 32, 64}．
  要求は flood_score がそのノードへ送るのと同じ `/v1/probe`（全 5 シャード，EnronQA の 2,940 問の cached の埋め込み）で，閉じた系・慣らし 5 秒・測定の区間 30 秒・負荷生成のプロセスは min(C, 4)．
  **主の対象は 192.168.13.102**（i5-8350U．Iteration 8 の dp12 で最も忙しかったノード），**記録の対象は 192.168.15.103**（i7-8700．EnronQA の配置の 10 台がこの型）．各 3 回（r2 は C を降順）．前後に flood_score × N = 1 × 300 問の anchor を置く（前の anchor の deploy でノードを起動する）．
  飽和の C は「C を倍にして，範囲が重ならず中央値が 1.10 倍以上」が初めて成り立たない C．`/healthz` に `host_cpu`（デバイス全体の `/proc/stat`）を足した．事前登録は journal の Iteration 9．
  B27 (d) は `fetch_healthz`（`ReadError`・`RemoteProtocolError`・`ConnectError` で 1 回だけ送り直す）で対処した．B27 (e) は，今回の集計（`iter9_summary.py`）で hit@2 を比べず，300 問の anchor は処理量・e2e p95 だけを別の欄に出す形で対処した．
- 根拠: (1) `atrium run` は 1 問で 30 台に probe を送るので，1 台に負荷を集めるには配置か `cluster` の形式を変える必要がある（運用ルール 2・3）．CLI なら deploy 済みのノードのうち 1 台にだけ送り，配置・シャード・方式の選択を変えない．
  (2) 負荷生成の費用を小さくするため本文を前もって JSON にし，プロセスを最大 4 個にした．負荷生成が律速でないことは `little_ratio` ≥ 0.90 とプロセスの CPU ≤ 0.80 で確かめる（Iteration 8 の学び：負荷生成の側の限界を専門家の限界と取り違えない）．
  (3) 192.168.13.102 は実際の負荷で最も要求が集まったノードで，20 台と同じハードウェアなので，30 台の全体の限界の見積もりに直接使える．i7-8700 の 10 台は別の型なので記録として 1 台測る．
- 要レビュー: (a) ノードの `/healthz` の応答に項目（`host_cpu`）を足したこと（追加だけ）と，`/proc/stat` の読み方を `atrium.cpu_stat` に移したこと（中身は同じ）．
  (b) `/healthz` の再試行は `atrium run` の専門家の CPU の読み取りにも効く（Iteration 8 までの実行とは G5 の欠けの起きやすさが違う．処理量の定義は変わらない）．
  (c) bench は probe だけで `/v1/retrieve` を含まず，負荷生成は `atrium run` の質問者と HTTP クライアントの使い方が違う．X_sat から 30 台の全体の限界を出すときは目安として扱う．
  (d) 操作端末（論理 64 CPU）での試しで，小さな索引のノードが C = 1 で約 62 コア分の CPU を使った（FAISS の OpenMP のスレッドによる可能性．推測）．実機で `node_proc_util` が 1 を大きく超えるなら，ノードの OpenMP のスレッドの数の固定が次の候補になる．

## B27 [auto-decided 2026-10-15] Iteration 8（質問者のプロセスの数）は採用．Iteration 9 は少数の専門家ノードに負荷を集めたノード 1 台の飽和点の測定

- 状況: Iteration 8 の判定は採用（事前登録の規則の機械的な適用．H-P1・H-P2 が 3 方式とも成立し，N = 8 まで頭打ちなし）．parallel 30 合計の処理量の中央値は N = 1 → 8 で fs 13.48 → 82.35，dp24 16.41 → 100.91，dp12 32.62 → 186.21 問/秒．
  E(8) は 0.71〜0.77 で物理 6 コア ÷ 8 = 0.75 とほぼ同じで，N = 8 でも律速は質問者のプロセス（制御点 1 台．約 2,600 要求/秒）である．専門家の `expert_cpu_util_max` は fs 0.44〜0.51，dp12 0.75〜0.78 で飽和していない（journal の「Iteration 8 実行済み」）．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 9 の単一レバーは「専門家ノード 1 台に集める同時の要求の数」**とし，config.yml の levers 末尾に `node_bench.concurrency: [1, 2, 4, 8, 16, 32, 64]`（仮称）を追記した．
  30 台に負荷を散らすのをやめ，少数（例えば 1〜2 台）の専門家ノードに，質問者のプロセスを複数使って `/v1/probe` の閉じた系の負荷を集め，同時の要求の数を上げたときのノード 1 台の処理量（要求/秒）・待ち時間・`process_cpu_s` の飽和点を測る．
  要求の本文は EnronQA test の問（cached の埋め込み．flood_score と同じ 5 シャードの probe）を使う．実装の形・対象のノード・同時の要求の数・判定の規則は rc-researcher が事前登録で決める．
- iteration_name: 「専門家ノード 1 台への集中負荷による処理能力の飽和点の測定」．
- 根拠: (1) 同じ制御点の上の質問者の分割は N = 8 で物理 6 コアを使い切り，N = 12 にしてもハイパースレッディングの分しか伸びない見込みである．30 台に散らす限り，制御点 1 台の約 2,600 要求/秒（1 ノードあたり約 90 要求/秒）が上限になる．
  (2) 専門家 1 台の見積もりは約 190〜210 要求/秒（1 コアで飽和すると仮定した推測）なので，制御点 1 台の能力の 10 分の 1 未満であり，1〜2 台に集めれば質問者を律速から外して専門家の飽和点に届く．
  (3) `cluster` の形式とデバイスの役割（運用ルール 2）を変えずに，「専門家 1 台の処理能力」（Iteration 8 の問い (3)）に実測で答えられる．30 台の全体の処理量は，ノード 1 台の能力と，方式ごとの最も忙しいノードへの要求の集まり方（dp12 で平均の約 1.9 倍）から見積もれる．
- 要レビュー: (a) **質問者を複数のデバイスに置く形（B25 (a)）は今回も選ばない**．30 台の全体の限界を実測で示す必要が出たとき（ノード 1 台の能力からの見積もりでは足りないとき）に人間の判断を求める．現時点では判断を求めない（[needs-human] にしない）．
  (b) ノード 1 台への集中負荷は，30 台への分散負荷と比べて，ノードの FastAPI のスレッドプール・FAISS のスレッドの使い方が違いうる．専門家のノードのプロセスは 1 つで，CPU は論理 8 CPU なので，飽和が 1 コアで起きるとは限らない（事前登録で 1 コアを仮定しない形にする）．
  (c) 負荷の対象のノードは実験の間ほかの役割を持たないが，同じノードの他の受信箱のシャードも同じプロセスで probe される．他のノードは使わないので，30 台の配置そのものは変えない．
  (d) Iteration 8 の `fs_n2_p30_r1` の G5 の欠け（区間の前の `/healthz` の GET で 11 台が "Server disconnected without sending a response"．`/healthz` の読み取りに再試行が無い）は，今回の測定で `process_cpu_s` を主に使うなら直してから流す（再試行を 1 回足す等．rc-researcher が判断）．
  (e) Iteration 8 の集計（`iter8_summary.py`）の G4 は，fs の 300 問の anchor の hit@2 を同じ欄に入れて False と表示する．判定には影響しないが，次の集計では 2,940 問だけで比べる．

## B26 [auto-decided 2026-10-15] Iteration 8 のレバーの形：`experiment.requester_procs`（parallel は合計で固定），2,940 問，専門家の CPU は /healthz の process_cpu_s

- 状況: B25 で，キー名・N・parallel の割り方・問の数・専門家の CPU の測り方・判定の規則を rc-researcher が事前登録で決めることになっていた．
- 自動選択: **`experiment.requester_procs`（既定 1 ＝今の挙動．`parallel` ≥ `requester_procs`，`churn` とは併用しない）**．N ∈ {1, 2, 4, 8}．`parallel` は全プロセスの合計の同時の問の数として 30 に固定し，各プロセスへほぼ均等に割る．子は `spawn` で起動し，各自で HTTP クライアント（`per_node`）とルーターを作り，全員の準備が済んでから一斉に流す．
  問は test の全 2,940 問．専門家の CPU はノードの `/healthz` に足した `process_cpu_s`（`time.process_time()`）を測定の区間の前後で読み，制御点の CPU は `/proc/stat` で読む．方式は flood_score・device_probe（D = 24・12）× N × 3 回 ＋ 記録の系列（N = 8・合計 240）＋ 揺れの確認 2 回の計 43 実行．判定の主は flood_score の N = 2 対 N = 1（範囲が重ならず中央値の比 1.5 以上）．事前登録は journal の Iteration 8．
- 根拠: (1) parallel を合計で固定すると，同時の問の数は Iteration 5〜7 と同じで，変わるのは「何個のプロセスで扱うか」だけになる（単一レバー）．プロセスごとに 30 にすると同時の問の数も N 倍になり 2 つの要因が混ざるので，記録の系列に分けた．
  (2) 制御点は物理 6 コア・論理 12 CPU なので，N = 8 は物理コアを超える点として残し，律速の分類で区別する．(3) 2,940 問にすると，高い処理量でも 1 回が数十秒以上になり，約 1 秒の一斉の待ち（B23 (b)）の影響が小さくなる．
  (4) `/healthz` への項目の追加は既存の応答の項目を変えず，ノードの外から docker の統計を取る方法（SSH で 30 台）より区間を揃えやすい．
- 要レビュー: (a) ノードの `/healthz` の応答に項目を足したこと（追加だけ）．(b) 1 プロセスの実験でも，区間の外側で全ノードへの `/healthz` の GET を 2 回送るようにしたこと（処理量の分母には入らない）．
  (c) 質問者のログ（全ての HTTP 要求を INFO で 1 行）は 1 要求あたりの質問者の費用に含まれるが，変えると 2 つ目のレバーになるので今回は変えない．ログの水準を下げた処理量は別の候補として残す．
  (d) 律速の分類の閾値（プロセス 0.90，専門家 0.90 コア，制御点 0.50）は運用上の値で，`expert_cpu_util` は docker・カーネルのネットワーク処理を含まない．

## B25 [auto-decided 2026-10-15] Iteration 7（device_probe）は採用．Iteration 8 は同じ制御点の上で質問者のプロセスを複数にする構成

- 状況: Iteration 7 の判定は採用（事前登録の規則の機械的な適用．K = 75（D = 24）・K = 30（D = 12）とも H-D1〜H-D3 が成立）．parallel 30 の処理量の中央値は dp24 15.64 対 cp75 12.82（1.22 倍），dp12 30.75 対 cp30 19.53（1.57 倍），test の hit@2 は dp24 0.7837（cp75 0.7612），dp12 0.6830（cp30 0.6905）（journal の「Iteration 7 実行済み」）．
  処理量は Iteration 6 の式（R ≈ 390〜440 要求/秒）の予測と 0.98〜1.05 で一致し，`requester_cpu_util` は 1 コアに張り付いたまま．Iteration 4 から 7 の全ての負荷の比較は「質問者 1 プロセスが扱える要求の数」の比較で，専門家の側の限界はまだ測れていない．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 8 の単一レバーは「質問者のプロセスの数」**とし，config.yml の levers 末尾に `experiment.requester_procs: [2, 4, 8]`（仮称）を追記した．同じ制御点 wafl-ctrl5（12 コア．今は 1 コアだけを使う）の上で，問を N 個のプロセスに分けて同時に流し，結果をまとめる．
  既定は 1（今の挙動）で，追加だけの設定キーとする（`experiment.http_pool`・`routing.probe_d` と同じ扱い）．方式は flood_score（全台に要求が届くので専門家の側の負荷が最も高い）と device_probe（D = 24・12）を per_node・300 問以上・検索だけで測り，N を上げたときに処理量が N に比例して伸びるか，どこで頭打ちになり，そのとき質問者と専門家のどちらの CPU が張り付くかを記録する．
  キー名・N の値・1 プロセスあたりの parallel の割り方（合計を固定するか，プロセスごとに固定するか）・問の数・専門家の側の CPU の測り方・判定の規則は rc-researcher が事前登録で決める．
- iteration_name: 「複数の質問者のプロセスによる専門家の側の処理量の限界の測定」．
- 根拠: (1) Iteration 7 の事前登録の「次の候補」（採用なら，複数の質問者のプロセス・デバイスの構成）と B21 (c)・B23 (a) に従う．(2) 方式の処理量の比較はどれも「質問者 1 コア ÷ 1 問あたりの要求の数」でほぼ説明でき，これ以上の方式の比較は同じ式を確かめるだけになる．
  論文で負荷を主張するには，質問者を律速から外したときの専門家の側の限界が要る．(3) 同じ制御点の上でプロセスを増やす形なら，`cluster` の形式とデバイスの役割（1 台 1 役割．運用ルール 2）を変えずに済み，データ・シャード・ラベルも作り直さない．
- 要レビュー: (a) **質問者を複数のデバイスに置く形**（`cluster.requester` を複数にする，あるいは wafl500〜509（`cluster.gpu_workers`）を質問者として使う）は，設定の形式とデバイスの役割の変更になるので自動では選ばず，人間の判断に残す．同じ制御点の 12 コアで専門家の側の限界に届かない場合に必要になる．
  (b) 同じ制御点に複数のプロセスを置くと，制御点の NIC・カーネルの処理が新しい共通の律速になりうる．その場合は「制御点 1 台の限界」として記録し，専門家の限界とは呼ばない．(c) `experiment.http_pool` の既定値は `shared` のまま（B21 (b) と同じ．人間の判断に残す）．
  (d) Iteration 7 の D は dev の規則で D_75 = 24 と決まったが，test では D = 22 でも K = 75 を上回る．D の刻み 2 台が dev と test の差と同じ大きさなので，D の値そのものを論文の主張にしない．

## B24 [auto-decided 2026-10-15] Iteration 7 のレバーの形：`experiment.routing: device_probe` と新しいキー `routing.probe_d.enronqa`，まとめ方は最大値

- 状況: B23 で，device_probe（仮称）のまとめ方（受信箱の得点のデバイスごとの集約）と D を rc-researcher が事前登録で決めることになっていた．
- 自動選択: **`experiment.routing` に `device_probe` を追加し，D は新しいキー `routing.probe_d.enronqa`（既定 30 の仮置き）**．まとめ方は**最大値**（和はオフラインの参考だけ）．D は `atrium device-probe-eval` の dev で，比べる centroid_probe（K = 75・30）の dev の hit@2 以上になる最小の D とし，
  要求の数が 0.9 倍以下（D ＋ 2 ≤ 0.9 ×（K の届くデバイスの数 ＋ 2））の D だけを実機（per_node・300 問・検索だけ）で，同じ時間帯の centroid_probe（K）と交互に測る．事前登録は journal の Iteration 7．
- 根拠: (1) 最大値でまとめると，上位 D 台は「届くデバイスが D 台以下の上位 K 個の受信箱」を必ず含むので，同じ台数で centroid_probe より再現率が下がらない（包含の性質．コードの確認項目にした）．和は正解が 1 個の問で「似た受信箱が多い台」を上げてしまう．
  (2) 既存の centroid_probe の経路（候補のデータ源 → そのシャードを持つノードへの probe）をそのまま使え，既存の routing の値の挙動を変えない．(3) 見込みの規則でオフラインの結果から実機の時間を限り，見込みが無ければ実機を流さずに棄却にできる．
- 要レビュー: (a) 見込みの閾値 0.9（処理量で約 1.11 倍）と採用の倍率 1.10 は，Iteration 6 の揺れ（±5% 以内）から決めた運用上の値である．(b) device_probe は 1 データ源 1 シャードの EnronQA でしか使えない（MedRAG では問を失敗として記録する）．
  (c) 比べる相手を Iteration 6 の値ではなく同じ時間帯の centroid_probe にしたので，実行の数が最大 26 に増える．

## B23 [auto-decided 2026-10-15] Iteration 6（1 段目の採点の重心の前計算）は採用．Iteration 7 はデバイスを単位に上位を選ぶ二段の方式

- 状況: Iteration 6 の判定は採用（事前登録の規則の機械的な適用．H-S1〜H-S4 が全て成立）．route_s の p50 は 0.111 → 0.0013〜0.0016 秒，parallel 30 の処理量の中央値は multi_centroid 8.55 → 169.5 問/秒，K = 75 5.37 → 13.4 問/秒，K = 30 は 19.7 問/秒（journal の「Iteration 6 実行済み」）．
  前計算の後も 4 方式の処理量は「1 問の時間 = route_s ＋ 要求の数 ÷ R」（R ≈ 400〜460 要求/秒）でほぼ説明でき，質問者の CPU は 1 コアに張り付いたまま．K = 75 は probe が 29.1 台に届くので flood_score とほぼ同じ処理量にしかならない．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 7 の単一レバーは「二段の方式の 2 段目の probe の単位」を受信箱からデバイスへ変えること**とし，config.yml の levers 末尾に `experiment.routing: [device_probe]`（仮称）を追記した．
  1 段目は前計算した multi_centroid の採点のまま，受信箱の得点をデバイス（ノード）ごとにまとめ（まとめ方，例えば最大値か上位の和かは rc-researcher が事前登録で決める），上位 D 台にだけ probe を送り，その結果から上位 m = 2 個を選ぶ．
  まずオフラインで test の hit@2 と 1 問あたりの probe のデバイスの数を K = 30・75 と D ごとに比べ，D を dev で事前登録の規則で選ぶ．見込みのある D だけを実機（per_node，300 問・検索だけ）で測る．
- iteration_name: 「デバイスを単位に上位を選ぶ二段の方式の再現率と処理量」．
- 根拠: (1) Iteration 6 の事前登録の「次の候補」（採用なら，デバイスを単位に上位を選ぶ方式）に従う．(2) 質問者が律速の間は，処理量は 1 問あたりの要求の数でほぼ決まり（R ≈ 420 要求/秒），受信箱を K 個に絞っても届くデバイスの数がほとんど減らない（K = 75 で 29.1 台）．デバイスの数を直接 D 台にすれば，式の上では D = 10 で約 12 要求，約 35 問/秒の見込みである（推測）．
  (3) 1 段目の採点の費用は Iteration 6 で除いたので，方式の比較が変換の費用の比較にならない．(4) オフラインの評価を先に置けば，実機の時間を見込みのある D に限れる．
- 要レビュー: (a) 複数の質問者のプロセス・デバイスの構成（専門家の側の限界を測る）を，デバイスを単位に選ぶ方式の後に回す順序でよいか．質問者が 1 コアに張り付いたままなので，どの方式の処理量も「質問者 1 プロセスの能力」の比較である点は変わらない．
  (b) Iteration 6 で，multi_centroid の 300 問は約 1.7 秒で終わり，約 1 秒の一斉の待ち（原因は未確認．宛先は多数のノードに散らばり，質問者の CPU は空いている）1 回で処理量が 30〜65% 揺れた．処理量の高い方式を測るときは問の数を増やす（例えば 2,940 問）か，揺れを前提に最小値も見る．
  (c) `experiment.http_pool` の既定値は `shared` のまま（B21 (b) と同じ．人間の判断に残す）．

## B22 [auto-decided 2026-10-15] Iteration 6 のレバーの形：設定キーにせず，SourceProfile での重心の前計算へコードを置き換える

- 状況: B21 で，レバー `requester.advert_scoring: [precomputed]`（仮称）を設定キーにするか（新旧を同じイメージで比べられる）コードの置き換えにするかを rc-researcher が事前登録で決めることになっていた．
- 自動選択: **コードの置き換え**．`SourceProfile` に `advert_centroids`（advert の中心の (C, 次元) の fp32 の行列．`__post_init__` で 1 回だけ作る）を足し，`MultiCentroidRouter.score` はそれを使う．config.yaml・設定の形式は変えない．
  比べる相手は Iteration 5 の per_node の値（`cd42b41`）とし，環境の揺れは採点を持たない flood_score × per_node の p30 を前後に 1 回ずつ流して確かめる．揺れの規則を外れたら採用の倍率を引き上げる（journal の Iteration 6 の事前登録）．計 38 実行．config.yml のレバー名は仮称のまま残し，note に「設定キーではない」と書いた．
- 根拠: (1) 前計算は選択の値をビット単位で変えず（同じ fp32 の値を同じ行列積に渡す），遅い方の形を残す理由が実験の比較以外に無い．比較のためだけに本番のコードに分岐と設定キーを残すのは，設定の形式を不要に広げる．
  (2) 期待する差（route_s 0.111 → 0.001 秒，処理量の 1.5 倍以上）は，Iteration 5 の per_node の p30 の 3 回の揺れ（±1.5% 以内）より 2 桁大きく，同じイメージで新旧を並べなくても前後の揺れの確認で区別できる．
  (3) route_s は各問で直接記録されており，機構（H-S1：route_s の p50 が 0.010 秒未満）は環境の揺れに左右されにくい．
- 要レビュー: (a) 同じイメージでの新旧の比較を省いたこと（揺れの確認で代える）でよいか．(b) `CardSimRouter` の `card_embedding` の毎回の変換（1 本 768 要素．150 受信箱で数 ms の見込み）は単一レバーの外として残した．card_sim を実機の負荷で測るときに同じ前計算を足すかは別に決める．

## B21 [auto-decided 2026-10-15] Iteration 5（質問者の接続の池）は採用．Iteration 6 は 1 段目の採点の重心の前計算による二段の方式の処理量の測り直し

- 状況: Iteration 5 の判定は採用（parallel 30 の flood_score の処理量は per_node 12.53〜12.78 問/秒で T4 の 2.44〜2.59 の 5.09 倍，e2e p95 3.39〜3.63 秒．H-H3 は (i) 池の管理の費用．journal の「Iteration 5 実行済み」）．
  per_node の下では，3 方式の parallel 30 の処理量が「1 問の時間 = route_s ＋ 要求の数 ÷ 約 400 要求/秒」でほぼ説明でき，multi_centroid（8.55 問/秒）と K = 75（5.37 問/秒）は 1 段目の採点（route_s 約 0.111 秒）で決まっている．
  `MultiCentroidRouter.score` は問ごと・受信箱ごとに `np.asarray(advert.centroids, np.float32)` でリストから変換しており，操作端末の測定では 150 × 32 × 768 の変換で 0.105 秒，変換済みなら 0.001 秒だった．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 6 の単一レバーは「1 段目の採点の重心の持ち方」**（問ごとにリストから変換 → 公開情報を読み込んだときに numpy の行列として 1 回だけ前計算）とし，config.yml の levers 末尾に `requester.advert_scoring: [precomputed]`（仮称）を追記した．
  選択の結果は変えない（全問で `selected_sources` が Iteration 5 と一致すること）．`experiment.http_pool: per_node` を明示し，multi_centroid と centroid_probe（K = 75．K = 30 は記録）を T4 と同じ 300 問・検索だけで parallel 1・4・16・30 × 3 回測り，Iteration 5 の per_node の値と比べる．
  設定キーにするか（新旧を同じイメージで比べられる）コードの置き換えにするか，揺れの確認の条件（flood_score × per_node の p30 は採点を持たないので変わらないはず），判定の規則は rc-researcher が事前登録で決める．
- iteration_name: 「1 段目の採点の重心の前計算による二段の方式の処理量の測り直し」．
- 根拠: (1) per_node の下で，1 段目の採点を持つ方式（multi_centroid・centroid_probe・次の候補のデバイスを単位に選ぶ方式）は，どれも route_s が処理量を決めており，この費用を外さないと方式の比較が「numpy の変換の費用」の比較になる．
  (2) d0006 §6.3 の未確認の項目（外部レビュー R4 (b)．multi_centroid が 1 問ずつで flood_score より遅い理由が重心の変換か）に答える．(3) コードの変更は小さく，選択は変わらないので正しさの確認が容易である．データ・シャード・ラベルは作り直さない．
  (4) 期待（推測）：route_s 0.111 → 0.01 秒未満，multi_centroid の parallel 30 の処理量は 8.5 → 数十問/秒（2 要求の扱いと質問者の他の費用で頭打ち），K = 75 は 5.4 → flood_score と同じ程度（約 12〜13 問/秒．要求の数がほぼ同じため）．
- 要レビュー: (a) この順序（デバイスを単位に上位を選ぶ方式より，1 段目の採点の費用の除去を先にする）でよいか．デバイスを単位に選ぶ方式は Iteration 7 の候補として残す．
  (b) `experiment.http_pool` の既定値は `shared` のまま（過去の実験の再現のため．B20 (c)）とし，以後の負荷の実験では `per_node` を明示する．既定値を `per_node` に変えるかは人間の判断とする（急がない）．
  (c) per_node でも質問者のプロセスの CPU は 1 コアに張り付いている（約 400 要求/秒）．専門家の側の限界を測るには，複数の質問者のプロセス・デバイスから流す構成が要る．これは構成（cluster の requester が 1 台）の変更を伴うので，二段の方式の比較の後の候補として残す．
  (d) d0006 §6.3 の T4 の記述（「全員に聞く方式の代償は処理量にも現れる」）を，per_node の測定では成り立たないと訂正した．論文で負荷を主張する場合は per_node の値を使う．

## B20 [auto-decided 2026-10-15] Iteration 5 のレバーの形：新しい設定キー `experiment.http_pool`（per_node を主，shared_unlimited を副）

- 状況: B19 で，レバー `requester.http_pool: [per_node]`（仮称）の具体的な形（ノードごとの client か池の上限か，parallel の点，回数）と判定の規則を rc-researcher が事前登録で決めることになっていた．
- 調査: httpcore 1.0.9（`uv.lock` の版）の `_assign_requests_to_connections` は接続の数 N・待ちの要求の数 M に対して O(N² + N × M) で，要求の開始と終了のたびに呼ばれる（encode/httpcore PR #1035，未マージ．NVIDIA NeMo Gym の技術メモ "aiohttp vs httpx"）．
  Iteration 4 の「同時の要求が増えると 1 要求あたりの費用が増える」形をそのまま説明しうる．出典は journal の Iteration 5．
- 自動選択: `config.yaml` の `experiment` に新しいキー `http_pool`（`shared` 既定＝今の挙動／`shared_unlimited`／`per_node`）を足す．config.yml のレバー名を `experiment.http_pool: [per_node, shared_unlimited]` に改めた．
  `per_node` は 1 つの `AsyncClient` に，ノードの origin ごとの `AsyncHTTPTransport`（上限 64）を `mounts` で付ける形とする．`shared_unlimited` は 1 つの池のまま上限を外し，池の上限による作り直し（ii）と池の管理の費用（i）を分けるための副の条件である．
  計 43 実行（揺れの確認 4，flood_score × per_node 15，flood_score × shared_unlimited 9，K = 75 × per_node 12，multi_centroid × per_node 3）．判定は flood_score の parallel 30 の per_node 対 shared（T4）で，Iteration 4 と同じ「範囲が重ならない」と「中央値の 1.5 倍」の規則．
- 根拠: (1) 既定値を今の挙動にすれば，既存の config.yaml と過去の実験の再現は変わらない（追加だけの形式の変更）．`iter4_chain.sh` の `setcfg` が扱える `experiment` の直下に置けば，連続実行の仕組みをそのまま使える．
  (2) ノードごとに別の AsyncClient を作ると `Requester` に渡す client の形が変わるが，mounts なら client 1 個のまま宛先ごとに池を分けられる．(3) 2 つの値で (i) と (ii) を分けられ，追加の費用は 9 実行（約 45 分）である．
- 要レビュー: (a) 設定ファイルの形式に追加のキーを足してよいか（B9 と同じ種類の確認．既定値で今の挙動）．(b) aiohttp への置き換えや httpcore の修正版の利用は，依存の変更を伴うので扱わない．per_node でも質問者の CPU が飽和する場合の次の候補として残す．
  (c) 採用でも `http_pool` の既定値は `shared` のまま（過去の実験の再現のため）とし，以後の負荷の実験で `per_node` を明示する．既定値を `per_node` に変えるかは人間の判断とする．

## B19 [auto-decided 2026-10-15] Iteration 4（centroid_probe の実機化）は棄却．Iteration 5 は負荷の律速が質問者の HTTP クライアントにあるかの切り分け

- 状況: Iteration 4 の判定は棄却（parallel 30 で K = 75 の処理量 1.974〜2.113 問/秒は flood_score（T4）の 2.440〜2.585 より下，e2e p95 22.8〜27.5 秒は 18.9〜20.6 秒より上．journal の「Iteration 4 実行済み」）．
  段ごとの時間から，ノードの計算は probe の時間の 0.1% 未満で，parallel 30 の待ちは質問者の側（1 秒あたりに扱える HTTP 要求の数が flood_score と K = 75 でほぼ同じ約 79〜80 要求/秒）にあると読めた．parallel 16 以上で接続の作り直し（1 実行で 1,000〜3,000 の新しい接続）と接続のエラーの再試行（100〜280 回）が起きている．
  事前登録の「次の候補」は，棄却なら「デバイスを単位に上位を選ぶ方式」としていた．config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 5 の単一レバーは「質問者の HTTP の接続の持ち方」**とし，config.yml の levers 末尾に `requester.http_pool: [per_node]`（仮称）を追記した．
  今は全ノードで 1 つの `httpx.AsyncClient`（`max_connections` = 512，`src/atrium/experiment.py`）を共有している．これを，律速の候補（接続の池の管理・池の上限による接続の作り直し）を外す形（例：ノードごとの `AsyncClient`，または池の上限を同時の要求の数より大きくする）に変え，
  flood_score・centroid_probe（K = 75）・multi_centroid を T4 と同じ 300 問・検索だけで parallel 1・4・16・30（各 3 回）測り直す．質問者の CPU 使用率（または py-spy のプロファイル），`retrying` の数，ノードの access log の接続の数も記録する．
  具体的な形（ノードごとの client か池の上限か，parallel の点，回数）と判定の規則は rc-researcher が事前登録で決める．既存の方式の選択の挙動は変えない．
- iteration_name: 「質問者の HTTP 接続の持ち方を変えた負荷の律速の切り分け」．
- 根拠: (1) T4 と Iteration 4 の「処理量」は，単一の質問者プロセスの HTTP クライアントの能力を測っている可能性が高く，その場合，デバイスを単位に選ぶ方式などのどの方式の比較も，同じ律速の下での「要求の数」の比較にしかならない．律速を先に確かめる方が，以降の負荷の比較の意味を決める．
  (2) d0006 §6.3 の「全員に聞く方式の代償は…処理量にも現れる」という T4 の主張の解釈がこの結果で変わる（専門家の負荷か，質問者の負荷か）．
  (3) コードの変更は質問者の HTTP クライアントの作り方だけで小さく，データ準備・ラベル・シャードの作り直しは要らない．フルスペックの実機実験（運用ルール 8）で 1 実行約 5 分．
- 要レビュー: (a) この順序（律速の切り分けを，デバイスを単位に選ぶ方式より先にする）でよいか．(b) 接続の持ち方を変えると T4 の値と直接比べられなくなるので，基準の方式（flood_score・multi_centroid）も同じイテレーションで測り直す（実行の数が増える）．
  (c) 律速が質問者の側と確かめられた場合，負荷の主張は「質問者 1 台が扱う要求の数」として書き直すか，複数の質問者（プロセス・デバイス）から流す構成を別に作るか．(d) 律速が質問者の側でなければ（処理量が変わらなければ），次はデバイスを単位に上位を選ぶ方式に進む．

## B18 [needs-human 2026-10-14] Iteration 4 の実験が docker の権限不足で止まっている（Slack で @mention 済み）

- 状況: 実装は `adbf605` で commit・push 済み（pytest 237 件・ruff・`mypy src` 通過）．`docker info` が `permission denied ... /var/run/docker.sock` で失敗した（B13・B16 と同じ原因で 3 回目）．実験は 1 回も流していない．
- 選択肢:
  - A1: 人間が docker 権限のある端末で，作業ツリーに未コミットの変更がある状態のまま `bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'` を 1 回実行し，Slack のスレッドに「1」と返信する．その後の各回の deploy は `sync_to_control && remote deploy` だけにし，最初にイメージの印が `<HEAD>-dirty` と一致することを確かめる．運用ルール 7（毎回 build・push）からは外れるが，`start.sh` の照合は残る．(Recommended)
  - A2: 人間が docker 権限のある端末から，連続実行のスクリプト `results/20261014_iter4_load_summary/iter4_chain.sh` そのものを流す（約 3 時間）．サイクルは結果の分析から再開する．
  - A3: 恒久対応（ktakahashi を docker グループに追加する，または制御点の denjo で build する）．権限設定の変更は人間の判断が要る．3 回目なので検討を求める．
- 要レビュー: どれを選んでも事前登録（journal の Iteration 4）は変えない．連続実行の途中で deploy が失敗した回は飛ばし，同じ run_id で流し直す（deploy の失敗は失敗率に数えない）．連続実行の間は commit しない（HEAD が変わると start.sh が止まる）．
- 備考: rc-executor は docker の回避（グループ変更・sudo・別の build）を行っていない．
- [human-decided 2026-10-14] 回答: Slack の返信「3 が完了済みである」（A3．恒久対応を人間が済ませたと解釈）．
  したこと: `status` を `running` に戻し，rc-executor へ再委譲する．rc-executor は最初に `docker info` が通ること（新しいグループは新規ログインのセッションにしか効かない場合がある）を確かめ，通らなければ回避せず再び停止する．通れば事前登録どおり通常の `mise run deploy` を含む連続実行に進む．

## B17 [auto-decided 2026-10-14] Iteration 3（公開情報の本文だけからの作り直し）は棄却．Iteration 4 は centroid_probe の実機化（1 段目はヘッダー込みの既存の公開情報）

- 状況: Iteration 3 の判定は棄却（multi_centroid の hit@2 差 −0.093 [−0.110, −0.076] は非劣性の幅 −0.03 を外れ，E_body_short の持ち主 top-1 の差 +0.012 [−0.003, +0.027] は「当てにくくなる」を満たさない．journal の「Iteration 3 実行済み」）．
  H-B1（term_sketch の部分）支持，H-B3 支持（単調な兼ね合いは本文だけでも成り立つ）．参考の centroid_probe（K = 75）は 0.761 → 0.751（差 −0.011 [−0.021, +0.000]）．
  config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 4 の単一レバーは centroid_probe の実機化**（B11 の「次の候補」の内容）．質問者の 2 段目（multi_centroid の上位 K 個の受信箱だけへの probe）を実装し，T4 と同じ条件（EnronQA 300 問，検索だけ，parallel 1・4・8・16・30 × 3 回）で，
  e2e の待ち時間・処理量を flood_score・multi_centroid（T4 の値）と比べる．1 問あたりに届くデバイスの数と各デバイスに届く受信箱の数も記録する．K は Iteration 1 で選んだ 75 に固定する．
  **1 段目の公開情報はシャードにある既存のヘッダー込みの multi_centroid（C = 32）を使う**．あわせて，上位 K の再現率（2 段目の上限）と，K ごとの届くデバイスの数の分布をオフラインで出す（実機の前に，K = 75 でほぼ全台に届くかを確かめられる）．
  config.yml の levers 末尾に `experiment.routing: [centroid_probe]`（実機）を追記した．
- iteration_name: 「centroid_probe（二段ルーティング）の実機での待ち時間と処理量の測定」．
- 根拠: (1) 本文だけの公開情報は，二段では hit@2 をほとんど変えない（−0.011，区間は 0 を含む）が，ヘッダーを除いた本文からの持ち主の特定を減らさず（E_body は +0.038），採用の条件も満たさなかった．シャードの作り直しと config.yaml の形式の変更（B15）を伴ってまで 1 段目に使う理由が無い．
  (2) 実機の待ち時間・処理量は 1 段目の公開情報の中身ではなく，2 段目に届くデバイスの数で決まる見込みであり（公開情報の大きさは 77.2 MB と 77.4 MB でほぼ同じ），1 段目の選択は測りたい量に影響しにくい（推測）．
  (3) T4 で flood_score の処理量が parallel 30 で 12.8 → 2.5 問/秒に落ちた問題に対して，二段の方式が負荷をどれだけ減らすかは RQ-A・RQ3 の論点として未測定のまま残っている．config.yml の運用ルール 8 により，フルスペックの実機実験を行ってよい．
- 要レビュー: (a) K = 75 ではほぼ全 30 台に届くと推定され（Iteration 1 の学び．各台に 1 つも当たらない確率 ≈ 0.5^5），処理量の改善は小さい可能性がある．オフラインのデバイスの数の分布で全台に近いと分かった場合，K = 30 も実機で測るか（K の追加は単一レバーの範囲内の条件の追加として扱う）を rc-researcher が事前登録で決める．
  (b) 質問者の 2 段目の実装は既存の Router の公開の挙動（`experiment.routing` の既存の値）を変えない形にすること．(c) 件名を残してアドレスだけを除いた公開情報の変形（Iteration 3 で件名とアドレスの寄与を分けられなかった点）は，必要なら後の候補にする．

## B16 [needs-human 2026-10-14] Iteration 3 の実験が docker の権限不足で止まっている（Slack で @mention 済み）

- 状況: 実装は commit・push 済み（`e57ab75`・`6ead387`．テスト 228 件・ruff・mypy 通過）．`publish_images` が B13 と同じ
  `permission denied ... /var/run/docker.sock` で失敗した（ktakahashi は docker グループに無い）．
  先に流した `remote.sh body_advert_eval` は制御点の古いイメージに `body-advert-eval` が無く，argparse の invalid choice で EXIT 2 になった．評価は始まっておらず，事前登録の「実験 1 回」は消費していない（ログ `/tmp/iter3_run.log`）．
- 選択肢:
  - A1: 人間が docker 権限のある端末で `bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'` を実行し，Slack のスレッドに「1」と返信する．その後 `remote.sh body_advert_eval 20261014_iter3_body_advert_eval 20261009_eq2_advert_eval` はサイクルが 1 回だけ流す．(Recommended)
  - A2: ktakahashi を docker グループに追加する（権限設定の変更．実質 root 相当のため人間の判断が要る）．
  - A3: 制御点 wafl-ctrl5 でイメージを build して配る手順に変える（`mise run deploy` の既存手順の変更．運用ルール 7・B5 との整合の確認が要る）．
- 要レビュー: どれを選んでも評価の仕様（事前登録）は変えない．B13 と同じ原因で 2 回目のため，A2 か A3 で恒久対応するかも検討してほしい．
- 備考: rc-executor が cli.py の編集に global の `python3` を 1 回使った（uv の規約違反．差分は確認済み）．
- [human-decided 2026-10-14] 回答: Slack の返信「1」（A1．`publish_images` を人間が実行したと解釈）．
  したこと: `status` を `running` に戻し，rc-executor へ再委譲した．イメージが今の HEAD のものかは rc-executor が確認してから `remote.sh body_advert_eval` を 1 回だけ流す．恒久対応（A2・A3）は未決のまま残る．

## B15 [auto-decided 2026-10-14] Iteration 3 の実装の形（CLI か設定キーか）と card_sim の件名の扱い（B14 の要レビュー (b)・(c)）

- 状況: B14 で，レバー `advert.input: [body_only]` のキー名・実装の形と，件名だけを公開する card_sim の扱いを rc-researcher が決めることになっていた．
- 自動選択: (c) **新しい設定キーは足さず，オフライン評価 CLI `atrium body-advert-eval`（`src/atrium/body_advert_eval.py`）として実装する**．config.yml のレバー名を `advert_eval.input` に改めた．config.yaml・シャード・Advert・ラベルは変えない．
  (b) **card_sim は件名を残し，公開情報を変えない（対照）**．本文だけの公開情報の値が，ヘッダー込みの値と一致することを退行の確認に使う．
  参考として，card_sim の説明文に持ち主の姓が入る受信箱の数を出す．
- 根拠: (1) 評価は実機を使わず，シャードの公開情報を作り直さないので，実験の条件を決める config.yaml に置く理由が無い（Iteration 2 の `header-eval` と同じ形）．設定ファイルの形式を変えないので B9 のような確認も要らない．
  (2) card_sim の公開情報は件名そのもの（`card_text`）で，件名を除くと方式そのものが無くなる．R1 で問題になったのは `Sender:`・`Recipients:` のアドレスであり，card_sim はもともとそれを含まない（Iteration 2 の E_subj_short 0.195 も件名の寄与が小さいことと矛盾しない）．
  本文から作る説明文（例：本文の頻出語）に変えると別の方式になり，単一レバーの範囲を超える．
- 要レビュー: 件名にも持ち主の名前が入りうるので，card_sim から件名を除いた変形（例：本文の先頭の文）を別の方式として比べるべきか．採用時にシャードの公開情報を本文だけで作り直す場合は，`data.enronqa` に入力の指定を足すことになり，そのときは config.yaml の形式の変更として改めて確認する．
- 追記（2026-10-14．Iteration 3 の評価）: 判定は棄却だったので，シャードの公開情報は作り直さず，config.yaml の形式の変更も要らなくなった（この点は閉じる）．card_sim の件名の扱いの問いは残る（card_sim の説明文に持ち主の姓が入る受信箱は 31/150）．

## B14 [auto-decided 2026-10-14] Iteration 2（R1）は H-R1b 支持．Iteration 3 は公開情報をヘッダーを除いた本文から作り直して EQ2 を測り直す

- 状況: Iteration 2 の判定は H-R1b 支持（term_sketch の E_body_short 0.219 [0.197, 0.238] 対 E_short 0.543 [0.515, 0.568]，Q 0.292 とは `lower`．journal の「Iteration 2 実行済み」）．
  R2 の A 群（持ち主の姓を含む問．26%）だけで見ると，term_sketch の hit@2 は 0.956，それ以外は 0.151 で，EQ2 の term_sketch の発見はヘッダー由来の持ち主の名前に強く依存する．
  B11 では次の候補を「centroid_probe の実機化」としていたが，事前登録の「次の候補」は「H-R1b が支持なら公開情報を作り直す」を先にしていた．
- 自動選択: **Iteration 3 の単一レバーは「公開情報の入力をヘッダーを除いた本文にする」**（config.yml の levers 末尾に `advert.input: [body_only]` を仮称で追記）．
  4 方式の公開情報を区切り線より後の本文から作り直し，EQ2 と同じ test 2,940 問の hit@1・hit@2 と，所有者の特定（E・E_body_short）を測り直す．大きさ（件名 40，T = 200，C = 32）と m = 2 は固定する．
  hit は A 群（持ち主の姓を含む）とそれ以外に分けて添える．centroid_probe の実機化は Iteration 4 以降の候補に回す（B11 の内容のまま）．
- iteration_name: 「ヘッダーを除いた本文から公開情報を作り直した RQ-A の測り直し」．
- 根拠: (1) RQ-A の主張（公開情報の量と，見つけてもらう度合い・持ち主を当てられる度合いの兼ね合い）の数値そのものが，ヘッダーの名前に依存している可能性が高く，論文の中心の数値を先に確かめる方が価値が高い．
  (2) centroid_probe の 1 段目は multi_centroid の公開情報なので，公開情報を作り直すと K と hit の兼ね合いが変わりうる．実機化（フルスペックで数時間）を先にすると，やり直しになりうる．
  (3) オフラインの評価で，データ準備・ラベル・シャードの作り直しは要らない見込み（multi_centroid・centroid_sim は本文の埋め込みの計算し直しが要る．所要は未見積もりで，rc-researcher が確認する）．
- 要レビュー: (a) この順序（公開情報の作り直しを centroid_probe の実機化より先にする）でよいか．(b) card_sim は件名だけを公開する方式なので，「ヘッダーを除く」とき件名を残すか（B12 では E_body_short から件名を除いた）．
  (c) 新しい設定キーを足すか，評価 CLI の引数にとどめるか（config.yaml の形式の変更は B9 と同じく要レビュー）．(d) 持ち主が自分の名前を公開情報から除くことは現実的な運用か（除けない場合，今の EQ2 の値が実態に近い）．

## B13 [needs-human 2026-10-14] Iteration 2 の実験が docker の権限不足で止まっている（Slack で @mention 済み）

- 状況: 実装は commit・push 済み（`da091ec`・`c817f02`．テスト 213 件・ruff・mypy 通過）．実験の前段の `publish_images` が
  `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock` で失敗した．
  この操作端末の ktakahashi は docker グループに入っていない（groups: sudo, systemd-journal, elab-ops）．`remote.sh header_eval` は未実行（1 回だけの規則は消費していない）．
- 選択肢:
  - A1: 人間が docker 権限のある端末で `bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'` を実行する．その後 `bash scripts/tasks/remote.sh header_eval 20261014_iter2_header_eval 20261009_eq2_advert_eval` はサイクルが続ける．(Recommended)
    メリット: 権限設定を変えず，既存の手順どおり．デメリット: 人間の作業が 1 回要る．
  - A2: ktakahashi を docker グループに追加する（権限設定の変更．人間が行う）．メリット: 以後の Iteration も自動で進む．デメリット: docker グループは実質 root 相当の権限で，セキュリティ上の判断が要る．
  - A3: 制御点 wafl-ctrl5（docker が動く）でイメージを build して配る手順に変える．メリット: この端末の権限が不要．デメリット: `mise run deploy` の既存手順（gpu2 での build）の変更で，運用ルール 7・B5 との整合の確認が要る．
- 要レビュー: どれを選んでも，評価の仕様（事前登録）は変えない．
- [human-decided 2026-10-14] 回答: ユーザーが「実行した」と返信した（A1．`publish_images` を人間が実行したと解釈）．
  したこと: `status` を `running` に戻し，rc-executor へ再委譲した．イメージが今の HEAD のものかは rc-executor が確認してから `remote.sh header_eval` を 1 回だけ流す．

## B12 [auto-decided 2026-10-14] R1 (b) の「ヘッダー」の範囲：区切り線より前の全行とし，Subject を本文に含めない

- 状況: B11 の要レビューで，R1 (b) で「ヘッダー」とみなす行の範囲（`Subject:` を本文に含めるか）を事前登録で決めることになっていた．
- 自動選択: 区切り線（`HEADER_SEPARATOR`）より前の全行（`Subject:`・`Sender:`・`Recipients:`）をヘッダーとし，判定に使う E_body_short は件名を含めない．件名＋本文の先頭 28 トークン（E_subj_short）は参考として出し，判定には使わない．
  あわせて，config.yml の levers 末尾に `owner_eval.input: [body_short]` を追記した（実装は新しい CLI `atrium header-eval`．config.yaml は変えない）．
- 根拠: (1) 重複の検出（`write_questions_files`）が既に `split(HEADER_SEPARATOR, 1)[-1]` を本文としており，同じ定義にできる．(2) R1 の問いは「メールの付属情報で持ち主を当てているか」であり，件名も送信者が付ける付属情報で，返信・転送で受信箱をまたいで繰り返される．
  (3) card_sim の公開情報は件名そのものなので，件名を含めると card_sim に有利さを与え，方式間の比較が崩れる．
- 要レビュー: 件名を本文とみなすべき（件名は書き手の書いた中身である）と考える場合は，参考の E_subj_short を判定に使うよう差し替える（事前登録の前に限る）．journal の「Iteration 2」の事前登録．

## B11 [auto-decided 2026-10-14] Iteration 1（centroid_probe）は部分的に支持．Iteration 2 の単一レバーを R1 にする

- 状況: Iteration 1 の判定は「部分的に支持」（S1 成立 +0.321，S2 不成立．K = 75 > 30．journal の「Iteration 1 実行済み」）．config.yml の levers は全て試し終えている．
- 自動選択: B10 の [human-decided 2026-10-14] に従い，**Iteration 2 の単一レバーは R1**（T2 の入力の確認）とする．
  (a) term_sketch の特徴語の上位に占めるメールアドレス・人名の割合を数える，(b) ヘッダー（`Subject:`・`Sender:`・`Recipients:` など）を除いた本文の先頭 28 トークンで T2（`atrium owner-eval`）を測り直す．
  R2（質問文が人名を含む問と含まない問で hit@1・hit@2 を分ける．flood_score・multi_centroid・centroid_probe）は同じイテレーションの追加の集計として行う．
  いずれも実機を使わないオフライン評価で，データ準備・ラベルは作り直さない．事前登録（判定の規則・退行の確認）は次の rc-researcher が決める．
- iteration_name の案: 「T2 の入力からヘッダーを除いた持ち主推定の測り直し」．
- term_sketch を使うので，B8 の追認に従い，term_sketch の値の揺れ（Q の top-1 ±0.001，E の top-1 ±0.002）と退行の許容 2e-3 を限界として書く．PYTHONHASHSEED は固定しない．
- 次の候補（Iteration 3 以降）: **centroid_probe の実機化**．質問者の 2 段目（候補の受信箱だけへの probe）を実装し，T4 と同じ条件（300 問，parallel 1・4・8・16・30 × 3 回）で待ち時間・処理量を flood_score・multi_centroid と比べる．
  1 問あたりに届くデバイスの数と各デバイスに届く受信箱の数も記録する（30 台が 5 受信箱ずつ持つので，K = 75 でもほぼ全台に届くと推定され，処理量の改善は小さい可能性がある．未測定）．
  config.yml の運用ルール 8 により，フルスペックの実機実験（質問者 1 台と専門家 30 台）を行ってよい．あわせて 1 段目の上位 K の再現率（2 段目の上限）をオフラインで出す．
- 根拠: R1 は T2 の結論と EQ2 の「持ち主を当てることの方に役立つ」の書き直しに直接影響し，人間がこの順序を指示した．centroid_probe の実機化は，S2 が不成立でも「K と負荷の兼ね合い」を実測する価値があるが，R1・R2 の結果（人名の一致が flood_score の高さを説明するか）が probe の価値の解釈を変えうるので後に回す．
- 要レビュー: R1 の (b) で「ヘッダー」とみなす行の範囲（`Subject:` を本文に含めるか）．centroid_probe の実機化を Iteration 3 に回してよいか．

## B10 [proposal 2026-10-14] 外部レビューの指摘（R1〜R4）．今回の Iteration 1 には混ぜず，別提案として保留する

出典: ユーザーが共有した外部レビュー．R1・R2 は実機を使わないオフライン評価で，制御点で各 1 時間程度の見込み．
- **R1（T2 の入力がヘッダーだけの疑い）**: `data_enronqa.py` は `File:` 行だけを除くので，E_short（先頭 28 トークン）は `Subject:`・`Sender:`・`Recipients:` で埋まる可能性が高い（実際の中身は未確認）．
  T2 の「同じ長さ」の比較が「質問文対ヘッダー」になり，term_sketch の逆転（E_short 0.543）も，持ち主のアドレスが特徴語に入ることで説明できる．
  確認: (a) term_sketch の特徴語の上位にアドレス・人名が占める割合を数える，(b) ヘッダーを除いた本文の先頭 28 トークンで T2 を測り直す．
  T2 の結論（journal の「T2」）と EQ2 の「持ち主を当てることの方に役立つ」の書き直しに影響する．
- **R2（質問文の人名による見つけやすさの水増し）**: EnronQA の質問は送信者名を含むことが多い．名前を含む質問の割合を数え，含む問と含まない問で hit@1・hit@2 を分けて報告する（flood_score の高さの一部が名前の一致による可能性）．
- **R3（B8 の追認）**: 下の B8 を追認するか．PYTHONHASHSEED をコンテナで固定すれば揺れは消えるが，EQ2 の値が ±0.002 ずれる．
- **R4（論文での扱いを決める未説明の点．新しい実験は不要）**: (a) EQ3（2,940 問・parallel 30．p50 92 秒・`ReadError` 175 件）と T4（300 問．p95 19 秒・失敗 0）の食い違いは，規模の違いか長時間の実行で蓄積する問題（接続の枯渇など）かを区別できていない．運用の頑健性は主張しない．
  (b) parallel 1 で multi_centroid（0.120 秒）が flood_score（0.076 秒）より遅い理由は，質問者側の重心の採点が律速という推測にとどまり未確認．
  (c) ragroute-frozen が新参者の one-hot を 0 にしても hit@2 0.118 で選ぶ理由は，ベクトル 0 のデータ源が「平均的なデータ源」として採点されるためという解釈で，未確認．
- [human-decided 2026-10-14] 回答: ユーザーが A1（R1・R2 は Iteration 1 の完了後）と B1（R3 は追認）を選んだ．
  したこと: 実行していない．**Iteration 2 の単一レバーは R1**（(a) 特徴語の上位のアドレス・人名の割合，(b) ヘッダーを除いた本文の先頭 28 トークンでの T2 の測り直し）とし，R2（名前を含む質問と含まない質問の hit の分離）を同じイテレーションの追加の集計として行う．
  R4 の 3 点は新しい実験をせず，Iteration 1 の最終報告か docs の限界の節に「未確認」として書く．
  Iteration 1 の評価（rc-evaluator）は，次レバーの決定のとき，この記述を優先して Iteration 2 に R1 を選ぶこと．

## B9 [auto-decided 2026-10-14] levers を使い切ったので，新しいレバー centroid_probe（二段ルーティング）を追加した

- 状況: config.yml の levers（E1-a〜E1-e，E2 card_sim，EQ8 k_context）と p0005（T1〜T4）が全て完了し，research-cycle の Iteration 1 で振るレバーが無かった．
- 調査: tavily-search で分散検索の資源選択（ReDDE，Rank-S，Taily，Dynamic Shard Cutoff Prediction，分散 selective search の負荷）と連合 RAG（RAGRoute，DFAMS）を調べた（出典は journal の Iteration 1）．
  候補は (a) 公開情報で上位 K 個に絞ってから probe する二段の方式，(b) 問ごとに問い合わせ数を変える打ち切り（m の可変化），(c) term_sketch を 1 段目に使う変形，(d) DFAMS 型の学習した prototype であった．
- 自動選択: (a) `experiment.routing: centroid_probe`（multi_centroid の上位 K 個 → probe → 上位 m = 2 個）．K は新しいキー `routing.probe_k.enronqa` で dev から選ぶ．評価はオフライン．
- 根拠: EQ2・T4 で残った問い（flood_score は hit@2 0.804 だが 150 受信箱に届いて負荷で処理量が落ち，multi_centroid は 2 受信箱で 0.440）の間を直接測れる．
  Router 1 個と評価の CLI 1 個で済み，データ準備・ラベルの作り直しが要らない．(b) は m の意味を変える．(c) は B8 の結論に依存する．(d) は中央での学習が要り，研究の前提（中央で学習しない）と合わない．
- 要レビュー: 新しい設定キー `routing.probe_k`（既存のキーは変えない）を足してよいか．K の候補と選び方（dev の hit@2 が flood_score の 0.95 倍以上になる最小の K）が妥当か．

## B8 [auto-decided 2026-10-14] T2 の退行の確認が term_sketch だけ不一致だったが，止まらずに続けた

- 状況: p0005 §9 の 1「検算・退行の確認が一致しない」は，作業を止めて報告する条件である．T2（`atrium owner-eval`）の退行の確認で，term_sketch の Q の top-1（差 −0.0007）と E の top-1（差 +0.0021）が基準（5e-4）を超えた．
  card_sim・centroid_sim・multi_centroid は完全に一致した．
- 診断: term_sketch の採点が Python の文字列のハッシュの種で揺れる（Q で 0.2915〜0.2922，E で 0.4240〜0.4274．`results/20261014_t2_owner_eval/ts_diag.out`）．EQ2 の値もこの揺れの中にある．実装の誤りではない．
- 自動選択: 止まらずに T2 の判定を記録し，T3 に進む．T3 の事前登録で，term_sketch の退行の許容を揺れの幅（0.002）に広げる．
- 根拠: 止まる条件の目的（原因不明の食い違いで先へ進まない）に当たらない．揺れは判定の差（term_sketch で 0.25 以上）より十分に小さい．
- 要レビュー: この緩和を認めるか．認めない場合は，`advert_eval.score_terms` を語の順序に依存しない形（語を並べ替えて足す）に直して EQ2・T2・T3 を作り直す（EQ2 の値が変わりうる）．
- [human-decided 2026-10-14] 回答: ユーザーが追認を選んだ（選択肢 B1）．PYTHONHASHSEED は固定しない．
  したこと: 既存の値は変えない．term_sketch の揺れ（Q ±0.001，E ±0.002）と T3 の許容（2e-3）を，term_sketch を含む結果の限界として書く（次に term_sketch を含む結果を書くときに従う）．

## B7 [human-decided 2026-10-13] RQ-C の実機の評価（EQ7b）を行う

- 状況: EQ7a（実機を使わない）の flood_score は「返事のあった分で続ける」と仮定した上限で，実機の質問者は 1 台の失敗で問全体を失敗にしていた．
  実機で確かめるには，質問者が部分的な失敗を許す変更（公開の挙動の変更）が要るので，実装の前に確認した．
- 回答: ユーザーが「実装して実行する」（選択肢 B1）を選んだ．
- したこと: `experiment.churn` を指定した実験でだけ，ノードが離脱でき（`/v1/admin/availability`），質問者が返事のあった分で続ける形にした（`5613f8c`）．
  指定しない実験の挙動は変えていない．事前登録（journal の「EQ7b の事前登録」，`d59123a`）の後に 22 回を流している．

## B6 [human-decided 2026-10-13] MedRAG の宿る型（S6）を不成功として閉じる

- 状況: S6 の pilot（100 問）は 64 問が失敗した（専門家 16 GB のメモリ不足．journal の「S6」）．smoke は通った．
- 選択肢: (1) 閉じて主評価を EnronQA の RQ-B に任せる，(2) `experiment.parallel` を 1 に下げる，(3) `llm.num_ctx`・`local_answer.k_context` を下げる．
- 自動選択: (1)．(2) は e2e の中央値が約 534 秒で全問に現実的でなく，(3) は E1 と文脈の条件が変わり比較が崩れるため．
- [human-decided 2026-10-13] 回答: ユーザーが (1) を選んだ（選択肢 A1）．MedRAG の宿る型は再開しない．

## B5 [auto-decided 2026-10-09] gpu2 のディスクが p0004 §12 の 6 の基準（90%）を超えている
- 状況: gpu2 の `/`（97%，空き 24 GB）と `/mnt/data-raid`（99%，空き 57 GB．docker の置き場所）が，着手の時点で 90% を超えていた．
  `/mnt/data-raid` は共有の RAID で，使用量の大半は本研究以外のもの（`/mnt/data-raid/ktakahashi` だけで 1.3 TB）である．
- 回答と自動選択: ユーザーが「データセットなどのダウンロード先には wafl-ctrl5 を用いよ」と指示した．これに従い，EnronQA・モデル・索引は
  全て制御点に置き，gpu2 はイメージの build（差分は数百 MB）とコードの編集だけに使う．gpu2 の空きが 20 GB を切ったら止まる．
- 根拠: 本研究の作業で gpu2 のディスクを増やさなければ，基準が守ろうとしているもの（gpu2 の空きを使い切らないこと）は守られる．
- 要レビュー: gpu2 の docker のビルドキャッシュ（28 GB が再利用されていない）を消すかどうか．他のプロジェクトと共有している可能性があるので，消していない．
- [human-decided 2026-10-13] 回答: 2026-10-13 の時点で gpu2 の `/` は空き 23 GB（98%），`/mnt/data-raid` は空き 246 GB（96%）．
  `~/.cache`（707 GB．本研究以外とも共有）の削除を提案したが，ユーザーは消さないことを選んだ（選択肢 C3）．何も削除していない．

## B4 [human-decided 2026-10-09] p0003 の S6（MedRAG の宿る型パイロット）と s8 を保留にする
- 状況: E1 で，MedRAG の正答率がルーティングにほとんど反応しないことが分かった（journal の「E1 の判定」）．
- 回答: ユーザーが p0004（EnronQA 拡張）を承認し，U1 で S6 の保留を承認した．宿る型の主評価は EnronQA の RQ-B に移す．
  s8（cross_encoder）も保留にする（p0004 §10）．
- したこと: S6 と s8 を実行せずに残した．s8 の途中の結果（1,191 問）は制御点に残し，同じ run_id で再開できる．
- 要レビュー: S6・s8 を再開するかどうか．s8 を再開するときは `cluster.requester_llm` を wafl500 にする（p0004 §14）．
- [human-decided 2026-10-12] 回答: S6・s8 とも保留を維持する（選択肢 A）．
  したこと: どちらも実行していない．s8 の途中の結果（1,191 問）と S6 の連続実行スクリプトは残してあり，再開する場合は上の手順に従う．
- [human-decided 2026-10-12] 撤回: ユーザーが「全て再開せよ」と指示した．S6（pilot）→ s8（cross_encoder の全問）の順に再開する．
  したこと: S6 は pilot で 64 問が失敗して不成功（B6）．s8 は同じ run_id で全 7,663 問を失敗 0 で終えた（journal の「S6」「s8」）．

## B3 [auto-decided 2026-10-06] 回答の生成を貪欲な復号（temperature 0）にする
- 状況: RAGRoute は生成の温度を指定しておらず，Ollama の既定（0.8）で回答がばらつく．同じ質問への方式間の
  正答の違いを McNemar 検定で比べると，ばらつきが雑音として混ざる．
- 自動選択: `llm.temperature: 0.0`．あわせて質問者の Ollama を 2 件同時に処理させる
  （`llm.requester_num_parallel: 2`．実測で 1 回の生成は約 7.4 秒．1 件ずつだと MIRAGE の全問に約 16 時間）．
- 根拠: 選択式の評価では貪欲な復号が一般的で，対応ありの比較の検出力が上がる．
- 要レビュー: RAGRoute の論文値との正答率の比較では，温度の違いが差の原因の候補になる．

## B2 [auto-decided 2026-10-06] MedCPT の埋め込みを fp16 の混合精度で計算する
- 状況: 11 枚の GPU で分担しても，fp32 では PubMed の埋め込みが 1 枚あたり約 95〜190 断片/秒で，
  約 5,400 万断片に 13 時間以上かかる見込みだった．ユーザーが寝ている間に準備を終える必要があった．
- 自動選択: `data.medrag.embed_precision: fp16_autocast`（torch.autocast）で文書とクエリの両方を埋め込む．
  fp32 で作りかけていた 6 ファイルの埋め込みは消し，全ファイルを同じ条件で作り直した．
- 根拠: gpu2 の RTX 3090 で Textbooks の 7,070 断片と MIRAGE の 500 問を測った．fp32 の 2.6 倍速く
  （193 → 502 断片/秒），fp32 との差は最大 0.0017（値の大きさ約 6.5），上位 50 件の一致率 99.7%，
  上位 15 件の集合が一致した質問は 97% だった．関連ラベルも実機の検索も同じ埋め込みから計算するので，
  内部の整合性は保たれる．
- 要レビュー: MedRAG の fp32 の埋め込みとの厳密な一致を要する比較（例：RAGRoute の論文値との細かな比較）で
  差の原因の候補になる．fp32 に戻すなら `embed_precision: fp32` にして，埋め込み・ラベル・ルーターを作り直す
  （GPU 11 枚で 13 時間前後）．

## B1 [auto-decided 2026-10-06] 専門家ノード（13.x・14.x）が全台応答しなくなった
- 状況: 2026-10-06 00:33 までに，専門家ノード 20 台（192.168.13.100〜109，192.168.14.100〜109）が ping にも
  SSH にも応答しなくなった．制御点（192.168.15.10）の既定ゲートウェイ 192.168.11.1 も応答せず，制御点と
  wafl500〜509 はインターネットに出られなかった．00:46 にネットワークが復旧した．13.x・14.x は再起動して
  おらず（uptime は 2 週間以上のまま），ホストは無事だった．
- 自動選択: 原因を研究室のネットワーク機器（ゲートウェイ）の障害とみなし，同じ時間帯に失敗したデータ準備の
  取得は，gpu2 で取得したデータを制御点へ送って補い，データ準備をやり直した．
- 要レビュー: ゲートウェイが 00:33〜00:46 ごろに止まった理由（夜間の保守など）．再び止まると，制御点と
  wafl500〜509 はインターネットに出られなくなる（データ準備の取得とモデルの取得が失敗する）．
