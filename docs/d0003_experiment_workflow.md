# d0003 実験の手順：mise タスク・所要時間・失敗時の確認先

全てのタスクは操作端末（gpu2）のリポジトリの直下で実行する．各タスクは最初に `config.yaml` から
`artifacts/cluster.env` を作り，リポジトリを制御点（`ssh wafl-ctrl5`）の `/home/denjo/atrium` へ同期する．

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
4. 制御点で `scripts/remote/prepare_data.sh` を `setsid nohup` で起動し，データ準備をバックグラウンドで始める
   （SSH が切れても続く）．MedCPT の埋め込みは制御点と wafl500〜509 の GPU 11 枚で分担する

`mise run setup -- medrag` のように，データ準備の対象を絞れる（既定は `feb4rag medrag` の順）．
データ準備の各段は冪等であり，成果物があれば飛ばす．止まった場合は再び `mise run setup` を実行すれば
続きから再開する（中断した GPU PC に残った埋め込みも回収してから分担し直す）．
ログは制御点の `/home/denjo/atrium-data/logs/prepare.log`（全体）と `data-feb4rag.log`（FeB4RAG），
GPU PC ごとの埋め込みのログは制御点の `/home/denjo/atrium/artifacts/logs/embed/<host>.log` にある．
状態は `prepare.status`（`running` / `done` / `failed (line N)`）に書かれる．

### data-status（データ準備の進み具合）

データ準備の状態，各データセットの成果物の有無，MedRAG の埋め込み済みファイル数（制御点に回収済みのもの），
GPU PC ごとのログの末尾，ディスクの空きを表示する．

### deploy（実験準備）

`experiment.kind=e1_routing` のとき:

1. データ準備の完了を確かめる（`manifest.json`・`labels.json`・`split.json`，`ragroute` なら `router.pt`）
2. `artifacts/<dataset>/placement.json` を作る（シャードを `expert_hosts` の先頭から割り当てる）
3. `cluster.release_hugepages` が true なら，各専門家で予約されたまま使われていない hugepages を解放し
   （`sysctl vm.nr_hugepages=0`．起動時の設定で 1 GB × 13 が予約され，通常のメモリが約 1.7 GB しか残らないため．
   実行時の値だけを変えるので再起動で戻る），
   シャード・`config.yaml`・compose を配り，`docker compose up -d --force-recreate` する．
   `answer_mode=local_answer` なら `llm.expert_model` を取得する
4. 質問者へ質問・manifest・クエリ埋め込み・ルーター・qrels・配置を配り，Ollama を起動する．
   `answer_mode=snippet_return` なら `llm.requester_model` を取得する
5. 配置から外れた専門家のコンテナを止める
6. 全専門家の `/healthz` が応答するまで待つ（最長 10 分．PubMed のシャードは索引の組み立てに数分かかる）

`experiment.kind=e0_measure` のとき: 対象ホストの専門家のコンテナを止めて hugepages を解放し，`atrium-full`・llama.cpp・iperf3 の
イメージと GGUF を用意する．

各ホストの出力は制御点の `/home/denjo/atrium/artifacts/logs/deploy/<host>.log` に残る．

### start（実験実行）

`mise run start [run_id]`．run_id を省略すると現在時刻（`YYYYMMDD_HHMMSS`）になる．

- `e1_routing`：質問者でコンテナ `atrium-run-<run_id>` を起動し，1 分ごとに処理済みの問数を表示して待つ．
  SSH が切れてもコンテナは動き続ける．同じ run_id で `mise run start <run_id>` を実行すると待ち直す．
  終了コードが 2 の場合は一部の質問が失敗したことを表し，結果は残る（各行の `error` を見る）．
- `e0_measure`：対象ホストを 1 台ずつ実測し，続けてホストの組ごとに RTT と iperf3 を測る．
  1 つの計測が失敗しても残りは続け，失敗は `e0/<host>/errors.txt` と `e0_summary.md` に残る．

結果は `results/<run_id>/` に回収される（失敗した場合も途中までの結果を回収する）．

### analyze（結果の分析）

`mise run analyze [run_id]`．run_id を省略すると最新の実行を分析する．

- `e1_routing`：制御点から `labels/`（ラベルと分割）を `artifacts/<dataset>/` へ取得し，
  `metrics.json` と `analysis_report.md` を作る．専門家のログを `results/<run_id>/logs/` に集める．
- `e0_measure`：`e0_summary.md` を作る．

2 つの実行の正答率は `uv run atrium compare results/<A> results/<B>` で比べる（McNemar の正確検定）．
research-cycle の `metrics_cmd` は `uv run atrium metrics --json`（最新の `metrics.json` を 1 行で出す）である．

### stop / clean

- `mise run stop`：全ノードのコンテナを止める（削除しない）．実験の後に実行し，専門家のメモリと
  質問者の VRAM を解放する．次の実験は `mise run deploy` から始める．
- `mise run clean`：全ノードのコンテナを削除する．`mise run clean -- --full` はさらに各ノードの
  `/home/denjo/atrium` と Ollama のモデルを削除する（破壊的）．制御点のデータディレクトリは消さない．

## 3. 所要時間の目安

2026-10-05 に gpu2 で StatPearls と Textbooks だけの縮小構成（約 43 万断片）を実行して測った値と，
それに基づく見積もりである．制御点の RTX 3060 での値は未実測である．

| 処理 | 値 | 根拠 |
|---|---|---|
| MedCPT による文書の埋め込み | 約 200 断片/秒（実測） | RTX 3090（他のプロセスと共有），バッチ 64，Textbooks |
| StatPearls（約 30 万断片，9,651 ファイル）の埋め込み | 約 39 分（実測） | 1 ファイルが数十断片と小さく，ファイルごとの処理の費用が大きい |
| 約 5,400 万断片の埋め込み（GPU 1 枚） | 3〜4 日（見積もり） | 上の速さのまま全断片を処理した場合 |
| 同（GPU 11 枚で分担，fp16 の混合精度） | 5〜6 時間（見積もり） | 混合精度で約 2.6 倍速くなる（gpu2 で実測）．これに断片の配布（約 110 GB，30 分ほど）とイメージの取得（10 分ほど）を足す |
| MedRAG の関連ラベル（7,663 問 × 全断片の内積） | 数時間（見積もり） | 縮小構成では数分．断片数に比例する |
| FeB4RAG の準備（SGPT-5.8B を CPU で 790 問と 500 文書） | 数時間 | 5.8B を fp32 で CPU 推論 |
| MIRAGE 全 7,663 問（retrieval_only） | 数時間 | 1 問あたり各シャードで fp16 の総当たり検索 |
| MIRAGE 全 7,663 問（snippet_return，8B） | 10 時間前後 | 1 問あたり数秒の生成 |

E0 の実測値が得られたら，この表と `.claude/research/config.yml` の `timeout_min` を見直す．

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
| FeB4RAG の取得（codeload.github.com）が極端に遅い | 制御点からの転送が 100 KB/s 程度まで落ちることがある（2026-10-05）．操作端末で同じコミットを clone し，`dataset/` を制御点の `atrium-data/feb4rag/repo/dataset` へ rsync すれば，取得の段は飛ばされる |
| 選択肢を抽出できなかった割合が高い | `results.jsonl` の `answer`．`llm.num_predict` が足りずに JSON が途中で切れていないか |
