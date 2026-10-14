# atrium 研究ジャーナル

研究サイクル（research-cycle）の各イテレーションの記録．新しいイテレーションを先頭に追記する（逆時系列）．
末尾の「準備段階の記録」は，サイクルを始める前に行った環境構築・データ準備・実機での検証の要約である．

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
