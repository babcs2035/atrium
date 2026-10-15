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

