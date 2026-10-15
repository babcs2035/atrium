# atrium 研究ジャーナル

研究サイクル（research-cycle）の各イテレーションの記録．新しいイテレーションを先頭に追記する（逆時系列）．
末尾の「準備段階の記録」は，サイクルを始める前に行った環境構築・データ準備・実機での検証の要約である．

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
