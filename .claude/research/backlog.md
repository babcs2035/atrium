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
