# d0003 実験の手順：mise タスク・所要時間・失敗時の確認先

全てのタスクは操作端末（gpu2）のリポジトリの直下で実行する．各タスクは最初に `config.yaml` を検証して読み込み，リポジトリを制御点（`ssh wafl-ctrl5`）の `cluster.remote_dir`（以下 `<remote_dir>`．既定は `/home/denjo/workspace/ktakahashi/atrium`）へ同期する．

## 1. 一連の流れ

```bash
mise run setup                 # 1 回目は数日（データ準備）．2 回目以降はイメージの build だけ
mise run data-status           # データ準備の完了を確かめる
# config.yaml を実験したい条件にする
mise run deploy
mise run start
mise run analyze
mise run stop
```

## 2. 各タスク

### setup（環境構築）

1. 操作端末で `uv sync --extra requester`（分析・テスト用の環境）
2. 制御点で registry（`atrium-registry`，`127.0.0.1:5000`）を起動する
3. 操作端末で `atrium-node` と `atrium-full` のイメージを build し，SSH の転送（操作端末の 15000 番 →
   制御点の 5000 番）を通して registry へ push する．制御点の回線はデータ準備の取得で混み合い，
   制御点で build すると PyPI や Docker Hub からの取得が失敗しやすいため，build は操作端末で行う．
   制御点は push されたイメージを pull して使う
4. 外部のイメージ（ollama・llama.cpp・iperf3）・Ollama のモデル・E0 の GGUF を操作端末で取得し，
   registry の `mirror/` と制御点のデータディレクトリへ送る（`scripts/tasks/fetch_assets.sh`．各ノードは
   インターネットに出ない）
5. 制御点で `scripts/remote/prepare_data.sh` を `setsid nohup` で起動し，データ準備をバックグラウンドで始める
   （SSH が切れても続く）．MedCPT の埋め込みは制御点と wafl500〜509 の GPU 11 枚で分担する

`mise run setup -- medrag` のように，データ準備の対象を絞れる（既定は `feb4rag medrag` の順）．
データ準備の各段は冪等であり，成果物があれば飛ばす．止まった場合は再び `mise run setup` を実行すれば
続きから再開する（中断した GPU PC に残った埋め込みも回収してから分担し直す）．
ログは制御点の `<data_dir>/logs/prepare.log`（全体）と `data-feb4rag.log`（FeB4RAG），
GPU PC ごとの埋め込みのログは制御点の `<remote_dir>/artifacts/logs/embed/<host>.log` にある．
状態は `prepare.status`（`running` / `done` / `failed (exit N)`）に書かれる．

### data-status（データ準備の進み具合）

データ準備の状態，各データセットの成果物の有無，MedRAG の埋め込み済みファイル数（制御点に回収済みのもの），
GPU PC ごとのログの末尾，ディスクの空きを表示する．

### deploy（実験準備）

最初に，操作端末でイメージを build して registry へ push する（setup の 3 と同じ．コードの変更はこれで各ノードに届く）．
制御点と各ノードは push されたイメージを取得し，置き換わって参照されなくなった自前のイメージ
（ラベル `org.atrium.project=atrium` を持つもの）だけを消す．

`experiment.kind=e1_routing` のとき:

1. データ準備の完了を確かめる（`manifest.json`・`labels.json`・`split.json`，`ragroute` なら `router.pt`）．
   さらに `atrium check-data` で，ラベルを作ったときの設定（`labels_meta.json`）と今の `config.yaml` を照合する
   （[d0002](d0002_configuration.md) の retrieval）
2. `artifacts/<dataset>/placement.json` を作る（シャードを `cluster.experts` の先頭から割り当てる）
3. 各専門家へシャード・`config.yaml`・compose（ひな形を `atrium render-compose` で埋めたもの．[d0002](d0002_configuration.md)）を配り，`docker compose up -d --force-recreate` する．
   `answer_mode=local_answer` なら `llm.expert_model` を取得する．
   専門家が自分の LLM で答える方式（`local_answer` は全ての専門家，`delegate_gpu` は GPU の専門家）では，
   最初の要求でのモデルの読み込みを計測に含めないよう，`llm.num_ctx` でモデルを読み込んでおく
4. 質問者へ質問・manifest・クエリ埋め込み・ルーター・qrels・配置を配り，Ollama を起動する．
   `answer_mode=snippet_return` なら `llm.requester_model` を取得する．質問者の Ollama で答える方式
   （`snippet_return` は `llm.requester_model`，`snippet_return_small` は `llm.expert_model`）では，そのモデルを同じく読み込んでおく
5. 配置から外れた専門家のコンテナを止める
6. 全専門家の `/healthz` が応答するまで待つ（最長 10 分．PubMed のシャードは索引の組み立てに数分かかる）

`experiment.kind=e0_measure` のとき: 対象ホストの専門家のコンテナを止め，`atrium-full`・llama.cpp・iperf3 の
イメージと GGUF を用意する．

各ホストの出力は制御点の `<remote_dir>/artifacts/logs/deploy/<host>.log` に残る．

### start（実験実行）

`mise run start [run_id]`．run_id を省略すると現在時刻（`YYYYMMDD_HHMMSS`）になる．

制御点の `scripts/remote/start.sh` を `setsid nohup` で切り離して起動し，終了の印（`results/<run_id>/.exit`）を
1 分ごとに確かめて待つ．操作端末との接続が切れても実験は続き，同じ run_id で `mise run start <run_id>` を
実行すると待ち直す．前回が失敗で終わった run_id（一部の質問が失敗した終了コード 2 を含む）を渡すと続きから再開する（E1 は成功済みの質問を，
E0 は完了済みの計測を飛ばす）．制御点での出力は `results/<run_id>/start.log` にある．

開始の前に，質問者と専門家の `config.yaml` が今のものと同じか，質問者のイメージが今の git のコミットから
作られたものかを確かめ，違えば止まる（`mise run deploy` からやり直す）．

- `e1_routing`：質問者でコンテナ `atrium-run-<run_id>` を起動し，1 分ごとに処理済みの問数を記録して待つ．
  終了コードが 2 の場合は一部の質問が失敗したことを表し，結果は残る（各行の `error` を見る）．
- `e0_measure`：対象ホストを 1 台ずつ実測し，続けてホストの組ごとに RTT と iperf3 を測る．
  1 つの計測が失敗しても残りは続け，失敗は `e0/<host>/errors.txt` と `e0_summary.md` に残る．

結果は `results/<run_id>/` に回収される（失敗した場合も途中までの結果を回収する）．

### analyze（結果の分析）

`mise run analyze [run_id]`．run_id を省略すると最新の実行を分析する．

- `e1_routing`：制御点から `labels/`（ラベルと分割）を `artifacts/<dataset>/` へ取得し，
  `metrics.json` と `analysis_report.md` を作る．専門家のログと配置は，start が実行の直後に
  `results/<run_id>/logs/` と `placement.json` に集めてある．
- `e0_measure`：`e0_summary.md` を作る．

2 つの実行の正答率は `uv run atrium compare results/<A> results/<B>` で比べる（McNemar の正確検定）．
research-cycle の `metrics_cmd` は `uv run atrium metrics --json`（最新の `metrics.json` を 1 行で出す）である．

### stop / clean

- `mise run stop`：全ノードのコンテナを止める（削除しない）．実験の後に実行し，専門家のメモリと
  質問者の VRAM を解放する．次の実験は `mise run deploy` から始める．
- `mise run clean`：全ノード（GPU PC を含む）のコンテナを削除し，registry への逆トンネルを閉じる．
  `mise run clean -- --full` はさらに各ノードの `<remote_dir>` と Ollama のモデルを削除する（破壊的）．
  制御点のデータディレクトリは消さない．データ準備の実行中は何もせずに止まる．

### 同時に実行しないこと

mise のタスクはどれも始めに `config.yaml` を制御点へ同期する．deploy や start の最中に別のタスクを
動かすと，ノードごとに違う設定が配られうる（start はそれを検出して止まる）．タスクは 1 つずつ実行する．
deploy は，別のデータセットのシャードを専門家から消さない（切り替えを速くするため．FeB4RAG のシャードは
数十 MB）．

## 3. 所要時間の目安

データ準備と実験を実際に行ったときの実測値である（gpu2 での縮小構成の値を含む）．

| 処理 | 値 | 根拠 |
|---|---|---|
| MedCPT による文書の埋め込み | 約 200 断片/秒（実測） | RTX 3090（他のプロセスと共有），バッチ 64，Textbooks |
| StatPearls（約 30 万断片，9,651 ファイル）の埋め込み | 約 39 分（実測） | 1 ファイルが数十断片と小さく，ファイルごとの処理の費用が大きい |
| 約 5,400 万断片の埋め込み（GPU 1 枚） | 3〜4 日（見積もり） | 上の速さのまま全断片を処理した場合 |
| 同（GPU 11 枚で分担，fp16 の混合精度） | 約 5 時間 40 分（実測） | RTX 3060 1 枚あたり約 280 断片/秒．断片の配布（約 100 GB）に約 15 分，イメージの取得に約 10 分を含む．最も遅かったのは他の処理と GPU を共有した制御点 |
| シャードの作成（16 シャードの重心の計算） | 約 16 分（実測） | 毎回作り直す |
| MedRAG の関連ラベル（7,663 問 × 全断片の内積） | 約 25 分（実測） | 断片ファイルを 5 万断片ずつ区切って GPU で計算する |
| FeB4RAG の準備（13 エンジン．SGPT-5.8B は CPU） | 約 4 時間（実測） | 大半は SGPT-5.8B での trec-covid の重心（500 文書）と 790 問の埋め込み |
| E0（2 台．llama-bench 4 モデル・FAISS・MedCPT・ネットワーク） | 約 3 時間（実測） | 大半は 8B の pp4096（1 台あたり約 45 分） |
| MIRAGE 全 7,663 問（retrieval_only，routing=all） | 約 4 時間（100 問の実測から見積もり） | 1 問あたり約 1.9 秒．専門家は 1 シャード（約 419 万ベクトル）の検索に約 1.2 秒かかり，要求を順に処理する |
| MIRAGE 全 7,663 問（snippet_return，llama3.1:8b） | 約 14 時間（25 問の実測から見積もり） | 1 問あたり約 6.4 秒．質問者の生成（プロンプト中央値約 4,900 トークン，出力中央値約 280 トークン）が律速 |
| 宿る型（local_answer，qwen3:0.6b） | 1 回の回答に 30〜190 秒（実測） | 専門家の CPU での生成．並列に送ると専門家で待ち行列ができる |
| MIRAGE 全 7,663 問（snippet_return，merge=cross_encoder．質問者の LLM は別の GPU） | 約 17 時間（中断を挟んだ実行の，後半の速さ（約 7.6 問/分）から） | 再ランクが 1 問約 22 秒（p50），生成が約 7 秒．質問者の LLM を別の GPU に置いたので生成は速い |
| MedRAG の宿る型（local_answer，qwen3:0.6b．15 断片） | 1 問約 9 分（実測．成功した問の中央値） | 専門家（16 GB）のメモリ不足で，100 問のうち 64 問が失敗した．全問には現実的でない |
| EnronQA 2,940 問（retrieval_only，parallel 30） | term_sketch 約 1 分，multi_centroid 約 6 分（実測）．flood_score は 30 台への probe が重なり，数時間かかる | parallel 1 で流すと，どの方式も 1 問 0.1 秒前後（2,940 問で数分〜45 分．参加・離脱の再生） |
| EnronQA 2,940 問（断片返却型 B1，B3，B4．parallel 30） | 0.6〜1.7 時間（実測） | 質問者の LLM と GPU の専門家が律速 |
| EnronQA 2,940 問（宿る型 B2，B2'．parallel 30） | 十数時間（実測．再試行を含む） | CPU の専門家での生成が律速．文脈の通数 k を 5 から 1〜3 に減らすと 0.4〜2.7 時間 |
| 条件ごとの deploy | 約 3〜10 分 | 条件ごとに deploy し直す（KV キャッシュを空にするため） |
| EnronQA の採点（判定モデル qwen3:8b．制御点と wafl500〜509） | 約 2 時間（2,940 問 × 数条件） | |

`.claude/research/config.yml` の `timeout_min`（1,200 分）は，この表の MIRAGE 全問の snippet_return が収まる長さにしてある．
宿る型で全問を回すときは，これに収まらないので `question_limit` で絞る．

## 4. 失敗時の確認先

| 症状 | 確認すること |
|---|---|
| deploy が「missing ... data preparation has not finished」で止まる | `mise run data-status`．データ準備が終わるまで待つ |
| deploy の healthcheck が失敗する | 表示された `docker compose logs`．メモリ不足（OOM）なら `cluster.shard_budget_gb` を下げてシャードを作り直す |
| pull で `unexpected commit digest` や `invalid tar header` が出る | registry の blob が壊れている．制御点で `docker rm -f -v atrium-registry` してから `mise run setup` で push し直す．`full` イメージは層を数 GB 以下に分けてあり，push の再試行は 5 分待ってから行う（すぐ送り直すと検証中の upload と重なって壊れた） |
| registry からイメージを取得できない | 制御点で `docker ps` に `atrium-registry` があるか．ノードの `curl http://localhost:5000/v2/` |
| start がすぐ終わる | `results/<run_id>/requester.log`．多くはノードの自己紹介に欠けたデータ源がある（deploy のやり直し） |
| ラベル一致が 1.0 から大きく外れる | シャードの配布漏れ（`rsync -L` の失敗）か，`k_ret`・`k_rerank` をラベルの計算後に変えた |
| データ準備の状態が `failed (exit N)` | `prepare.log` の末尾．取得が途中で切れた場合は `mise run setup` で再開する（取得は Content-Length と照合し，切れたファイルは捨てて取り直す）．NCBI から StatPearls が取れないときは，別の場所で取得した `statpearls_NBK430685.tar.gz` を `atrium-data/medrag/corpus/statpearls/` に置けば取得は飛ばされる |
| FeB4RAG の取得（codeload.github.com）が極端に遅い | 制御点からの転送が 100 KB/s 程度まで落ちることがある．操作端末で同じコミットを clone し，`dataset/` を制御点の `atrium-data/feb4rag/repo/dataset` へ rsync すれば，取得の段は飛ばされる |
| 宿る型で専門家が 502 を返す | 専門家のカーネルログ（`dmesg`・`journalctl -k`）に `Out of memory` があれば，Ollama の `llama-server` が OOM で落ちている．`llm.num_ctx`・`local_answer.k_context`・`experiment.parallel` を下げる．`llm.timeout_s` を超えた生成も 502 になる |
| 連続実行（deploy → start）が `.exit` を見て 2 秒で終わる | 前の手動停止などで残った `results/<run_id>/.exit`（値 0）を，start が「完了済み」と見なす．制御点の該当ファイルを消す |
| 連続実行を別のプロセスから起動したら docker に触れない | プロセスが `docker` グループを持っていない．`sg docker -c '...'` の下で起動する |
| 選択肢を抽出できなかった割合が高い | `results.jsonl` の `answer`．`llm.num_predict` が足りずに JSON が途中で切れていないか |

## 5. EnronQA の採点とモデルの取得（p0004）

EnronQA の回答は自由記述なので，実験の後に判定モデル（`data.enronqa.judge_model`．既定は qwen3:8b）で採点する．
判定のプロンプトは EnronQA の原論文の付録 B.6 と同じで，判定モデルは正解のメールを見て回答が正解と一致するかを判定する．

```bash
bash scripts/tasks/remote.sh fetch_ollama_models qwen3:8b qwen3:14b qwen3:1.7b  # 制御点でモデルを取得する
bash scripts/tasks/judge.sh up qwen3:8b             # 制御点と wafl500〜509 の GPU で判定モデルを起動する
bash scripts/tasks/judge.sh validate <out_name>     # 付属の別解・誤答で判定モデルを検証する
bash scripts/tasks/judge.sh run <run_id>...         # 採点し，results/<run_id>/judgements.jsonl を取得する
mise run analyze <run_id>                           # 正答率に採点の結果を使う
bash scripts/tasks/judge.sh down
```

- データセットとモデルは全て制御点で取得する（gpu2 のディスクは空きが少ない）．
- `judgements.jsonl` には質問 ID・正誤・回答と正解のメールの最長共通部分列の比率だけを書く．メールの本文と判定モデルの出力は
  制御点の `<data_dir>/enronqa/judge_raw/` にだけ残す．
- `judge.sh up` は GPU の PC の専門家のコンテナを止める（GPU を判定モデルに使うため）．実験の後に使う．

## 6. 実機を使わない評価と連続実行

実機のノードを使わず，制御点のコンテナでスコアから評価するもの（メールの本文と埋め込みは制御点にだけあるため）は，次の手順で流す．
各評価の入力・出力・指標は [d0004](d0004_metrics.md) にある．

```bash
# 1. 今の作業ツリーでイメージを build して registry へ push する（deploy の前半だけ）
bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'
# 2. 制御点のコンテナで評価を 1 回流す（出力は制御点の results/<out_name>/）
bash scripts/tasks/remote.sh advert_eval   <out_name>                              # RQ-A（EQ2）
bash scripts/tasks/remote.sh cascade_eval  <out_name> <reference_run>              # 二段ルーティング centroid_probe（Iteration 1）
bash scripts/tasks/remote.sh probe_reach   <out_name> <placement_run>              # centroid_probe の K ごとの届くデバイスの数と再現率（Iteration 4）
bash scripts/tasks/remote.sh device_probe_eval <out_name> <placement_run> <reference_run>  # device_probe の D ごとの hit@2 と D の選択（Iteration 7．reference_run は Iteration 1 の cascade-eval）
bash scripts/tasks/remote.sh churn_eval    <out_name> <placement_run> <reference_run>   # 参加・離脱（EQ7a）
bash scripts/tasks/remote.sh churn_cascade_eval <out_name> <placement_run> <device_probe_run> <churn_run>  # 二段の方式の参加・離脱（Iteration 15．device_probe_run は Iteration 7 の device-probe-eval，churn_run は EQ7a）
bash scripts/tasks/remote.sh churn_join_eval <out_name> <placement_run> <cascade_run>  # 参加の通知の遅れ L ごとの評価（Iteration 16．cascade_run は Iteration 15 の churn-cascade-eval）
bash scripts/tasks/remote.sh churn_leave_eval <out_name> <placement_run> <join_run>  # 離脱の通知の遅れ L_leave ごとの評価（Iteration 17．join_run は Iteration 16 の churn-join-eval）
bash scripts/tasks/remote.sh churn_refill_eval <out_name> <placement_run> <join_run>  # 返事の無い probe の枠を補う変種の評価（Iteration 18．join_run は Iteration 16 の churn-join-eval）
bash scripts/tasks/remote.sh node_bench    <out_name> <placement_run> <target_host> <concurrency> [max_procs]  # 専門家ノード 1 台への負荷試験（Iteration 9．max_procs は負荷生成のプロセスの数の上限で省略時 4．deploy でノードを起動した後．placement_run は同じ deploy の実行）
bash scripts/tasks/remote.sh owner_eval    <out_name> <reference_run>              # 持ち主推定の入力を揃える（p0005 T2）
bash scripts/tasks/remote.sh header_eval   <out_name> <reference_run> [t2_run] [cascade_run]  # T2 の入力からヘッダーを除く（Iteration 2 の R1・R2）
bash scripts/tasks/remote.sh newcomer_eval <out_name> <reference_run> [n_newcomers]  # 150 人での新参者（p0005 T3）
# 3. 結果を操作端末へ取得する
rsync -az wafl-ctrl5:<remote_dir>/results/<out_name>/ results/<out_name>/
```

- `<reference_run>` は退行の確認の基準にする EQ2 の実行（`20261009_eq2_advert_eval`）．評価は実験の前に条件と基準を journal に書いて push し，結果を見て変えずに 1 回だけ流す．
- LOSO（`atrium loso`）と，EQ7b の集計（`atrium churn-live-report`）は操作端末で動く．
- 評価を流している間はリポジトリを編集しない（publish が作業ツリーから build するため）．

条件ごとに deploy し直す実験（EQ8〜EQ10・EQ7b・負荷試験）は，条件の一覧（`<run_id> key=value ...` の行）を読んで，各回の前に deploy → start → analyze を行う連続実行のスクリプトで流す．
実際のスクリプトは `results/20261011_eq8_summary/eq8_chain.sh`（EQ8〜EQ10．採点を含む）と `results/20261014_t4_load_summary/t4_chain.sh`（負荷試験．失敗した問を流し直さず，失敗率が高い条件より上を飛ばす）にある．

```bash
(nohup setsid sg docker -c "bash <chain.sh> <log> <specs>" > <log>.nohup 2>&1 < /dev/null &)
```

- 連続実行の間は，commit しない（HEAD が進むと，deploy で作ったイメージと start の版が合わず止まる）．`config.yaml` は各回の後と終了時に元へ戻る．
- 起動するプロセスが `docker` グループを持たないと docker のソケットに触れないので，`sg docker -c` で起動する．
- 結果のある回は飛ばされるので，止まったら同じ条件の一覧で再実行すればよい．
