# wafl500〜509 の同等性調査を Claude Code へ引き継ぐ

この文書は，2026-11-11 15:33 JST 時点の作業状態と再開手順を記録する．ユーザーが「この codex は停止し，ほかの claude code セッションに引き継ぎたい」と指示したため，追加の実験実行を停止した．次のセッションはこの文書と [計画書](p0008_gpu_node_equivalence.md) を読んで再開する．全実験は未完了である．

## 依頼と権限

ユーザーは計画書の提示後に「全ての実験を遂行せよ」と指示した．対象は wafl500〜509 の全 10 台であり，構成調査，GPU／CPU／RAM／ストレージ／通信，LLM／埋め込み／採点，持続負荷，統計的な同等性判定を行う．今回の停止はセッションの引き継ぎのためである．再開時はこの測定の目的を維持する．

共通規約と Skills の参照先は変更していない．既存の `config.yaml`，`.claude/research/state.json`，journal，backlog も変更していない．コミットしていない．作業前から大量の未追跡結果と `.claude/research/state.json` の変更があったため，今回の成果物以外を戻したりまとめてコミットしたりしないこと．

## 完了した操作と記録

作業場所は `/home/ktakahashi/workspace/atrium`，実際の操作ホストは `gpu4` である．gpu4 の GPU は使用していない．

| 作業 | 状態 | 保存先 |
|---|---|---|
| 全台の構成情報の収集 | 全 10 台で SSH の終了コード 0 | `results/20261111_gpu_equivalence/inventory/` |
| CPU の SHA-256，RAM コピー，専用一時ファイルの書き込み・読み取り | 全 10 台で各 10 回，終了コード 0．解析は未実施 | `results/20261111_gpu_equivalence/cpu/` |
| 固定 digest の CUDA 対応イメージの準備 | 全 10 台で終了コード 0 | `results/20261111_gpu_equivalence/prepare_status.json` と `wafl50*.prepare.log` |
| 中止条件の単体テスト | 5 件通過 | `tests/test_gpu_equivalence_safety.py` |
| CUDA の環境の確認 | wafl502 で torch の import のみ．GPU 演算は未実施 | 会話内の観測：torch `2.14.1+cu130`，CUDA ランタイム `13.0` |

開始済みだったローカルの CPU 測定と配布プロセスは，停止指示後に状態を確認し，両方とも正常終了済みである．セッション ID は CPU 測定 `68121`，配布 `83640` で，いずれも終了コード 0 を確認した．構成収集のセッション `53347` と torch import のセッション `63075` も終了済みである．この作業で継続実行中の測定や負荷用コンテナはない．

## 構成調査で確認した差

全台とも NVIDIA GeForce RTX 3060，VRAM 12288 MiB，ドライバー `595.99.02` だった．ただし，以下の属性が異なる．これは採取した一次記録の結果であり，性能差の判定ではない．

| ホスト | CPU | マザーボード | VBIOS | PCI subsystem ID（nvidia-smi XML の表記） | 現在の power limit |
|---|---|---|---|---|---|
| wafl500 | i7-8700 | PRIME H310M-AT R2.0 | 94.06.2F.40.7D | 88101043 | 170 W |
| wafl501 | i7-8700 | PRIME H310M-AT R2.0 | 94.06.2F.00.F5 | 40721458 | 170 W |
| wafl502 | i7-8700 | PRIME H310M-AT R2.0 | 94.06.25.00.7C | 250410DE | 180 W |
| wafl503 | i7-8700 | PRIME H310M-AT R2.0 | 94.06.25.00.5E | 153610DE | 170 W |
| wafl504 | i7-9700 | Z390 Pro4 | 94.06.25.00.52 | B63019DA | 170 W |
| wafl505 | i7-9700 | Z390 Pro4 | 94.06.25.00.52 | B63019DA | 170 W |
| wafl506 | i7-9700K | Z370 Pro4 | 94.06.25.00.52 | B63019DA | 170 W |
| wafl507 | i7-9700 | TUF Z390-PLUS GAMING | 94.06.14.00.A6 | 250310DE | 170 W |
| wafl508 | i5-12400F | H610M-HVS/M.2 R2.0 | 94.06.2F.40.0E | 88101043 | 170 W |
| wafl509 | i5-12400F | H610M-HVS/M.2 R2.0 | 94.06.25.00.50 | 463019DA | 170 W |

構成はすでに同一ではないと分かっている．性能が同等かどうかは未判定である．RAM，ストレージ，PCIe，冷却などの比較表は一次記録からまだ作成していない．

収集時点では wafl500 と wafl502 で確認した GPU 使用率は 0%，VRAM 使用量は少量だった．各ホストの `running_containers` と `compute_apps` の記録を参照し，負荷開始前には再確認すること．`.claude/research/state.json` は Iteration 43，status `running` と記録されていたため，他の研究セッションが新しい処理を始める可能性に注意する．

## 接続方法と準備した資材

通常の `ssh wafl500` はこの環境では次のエラーになった．

```text
Bad owner or permissions on /etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf
```

システム設定は変更せず，ユーザーの設定ファイルを明示して接続できた．ProxyJump もこの方法で動作した．

```bash
ssh -F /home/ktakahashi/.ssh/config -o BatchMode=yes -o ConnectTimeout=15 wafl500 'hostname'
```

ノード側の既存プロジェクトは `/home/denjo/workspace/ktakahashi/atrium`．今回の専用配布先はその下の `gpu-equivalence-20261111`．各ホストの既存イメージのタグは変更せず，以下の固定参照を `docker image inspect` または `docker pull` で準備した．

```text
localhost:5000/atrium-full@sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f
```

準備前は wafl500・501 に `atrium-full:latest` がなく，wafl508・509 のタグの image ID も他と異なっていた．`RepoDigests` と image ID が一致した形式にならない例があるため，統制した測定では各ホストの実際のライブラリ版と image ID も記録すること．既存タグを揃えたと解釈しない．

モデルの既存配置は以下のとおり．全台の存在・実体ハッシュの照合は未完了．新たな外部ダウンロードや認証操作はしていない．

```text
# 埋め込み：全台の構成収集でモデルの snapshots ディレクトリを確認．revision は wafl502 で確認．
/home/denjo/workspace/ktakahashi/atrium/hf-cache/hub/models--Snowflake--snowflake-arctic-embed-m-v1.5/snapshots/e58a8f756156a1293d763f17e3aae643474e9b8a

# 採点：wafl502 で確認．他の台は追加確認が必要．
/home/denjo/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e

# Ollama：全台で llama3.1/8b の manifest の SHA-256 が一致．blob 本体の照合は未実施．
/home/denjo/workspace/ktakahashi/atrium/ollama/models
```

llama3.1/8b の manifest の SHA-256 は `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`．Ollama のイメージにもホスト間の差があったため，生成試験では版と実体を揃える必要がある．

## 追加したコードと未完成の箇所

すべて未コミットである．

| ファイル | 内容と注意点 |
|---|---|
| `scripts/gpu_equivalence/inventory.sh` | 読み取り専用の構成収集．全台で実行済み．個々の情報取得の終了コードを保存している |
| `scripts/gpu_equivalence/collect.py` | 5 並列の SSH 収集．出力先は `exist_ok=False` なので既存の inventory へ再実行しない |
| `scripts/gpu_equivalence/prepare.py` | 固定 digest の取得と Python ファイルの tar 配布．全台で実行済み |
| `scripts/gpu_equivalence/cpu_probe.py` | stdlib による CPU・RAM・ディスク試験．全台で `taskset -c 0 python3 -` として実行済み |
| `scripts/gpu_equivalence/safety.py` | 80 °C 以上または熱による減速を中止とする判定関数．単体テスト済みだが監視処理へ未接続 |
| `scripts/gpu_equivalence/workload.py` | CUDA 演算・転送・1 GiB の VRAM 照合・埋め込み・採点の下書き．実行・lint・型検査は未実施 |
| `tests/test_gpu_equivalence_safety.py` | 中止値の境界と減速理由の 5 ケース．通過済み |

**GPU 負荷は監視プロセスを実装してから実行すること．** `workload.py` 自体には 80 °C で止める機能がまだない．`safety.py` の存在やテスト通過だけで安全に負荷を開始できる状態ではない．

配布した tar は `cpu_probe.py`・`safety.py`・`workload.py` を作成する前に生成したため，これらの最新版はノードへ未配布である．再配布時は結果ログを上書きせず，新たな配布記録を保存すること．

CPU 試験の 10 回はホスト単位で連続実行したものであり，計画書の「10 台の順序を入れ替えた 10 ラウンド」とは異なる予備測定である．これだけで計画の全反復や同等性判定を完了したと扱わないこと．CPU 試験の出力は 1 台につき環境 1 行＋4 指標×10 回である．

`workload.py` の埋め込みは transformers の GPU forward，採点もトークン化済み入力の GPU forward を測る下書きである．アプリケーション全体の要求時間は別途測定が必要である．埋め込みの pooling・正規化，モデル出力の全台照合，正しい CUDA 時間の集計，VRAM の試験範囲，安定判定の適用範囲をレビューすること．

## 未実施の実験と再開順

1. 追加コードを通読し，lint・型検査・必要なテストを実行する．既存 CPU 結果の妥当性を確認し，構成比較表を作る．
2. 全台の稼働状態を再確認し，対象を占有できることを確認する．現物でしか分からない冷却・電源等は未確認と明記する．
3. 監視と中止処理を実装する．1 秒間隔の GPU／CPU／RAM／I/O／競合プロセスの記録，監視喪失時の中止，自分の負荷コンテナだけの停止，熱による中止をホスト単位で保持する処理が必要である．既存の研究コンテナや他人の処理を止めない．
4. 対象・入力・反復順・許容幅・中止値・digest を manifest に固定する．測定の source hash と環境も保存する．
5. 最新コードと同一モデルを全台へ配布し，代表機の短い試行で測定器と中止処理を検証する．
6. 計画書の GPU 演算，VRAM 帯域，PCIe，VRAM 照合，現状 A／統制 B の比較を実施する．
7. LLM の短／長入力・同時要求 1／4・各 100 要求・10 ラウンド，埋め込み・採点の実験を実施する．Ollama の専用起動と生成時間の収集は未実装である．
8. 制御点とのネットワーク試験と，必要なら P2P の全 45 組の両方向試験を実施する．未実施である．
9. 生成と採点の各 30 分×3 回の単独持続負荷，その後の全台同時負荷を実施する．未実施である．
10. 全 45 組の性能比と同時信頼区間を集計し，必要な追試と最終報告を作る．未実施である．

計画の中止条件は温度 80 °C 以上，熱による減速，ファン異常，Xid，計算の照合不一致である．該当したホストは中止として保存し，原因を解消するまで同じ負荷を繰り返さない．従来の記録にある熱の問題を今回再現したとはまだ言えない．

## 実行環境で確認済みのコマンド

`uv` の既定キャッシュは sandbox から書けないため，このセッションでは次の形で実行した．

```bash
env UV_CACHE_DIR=/tmp/atrium-plan-uv-cache uv run --no-sync pytest -q tests/test_gpu_equivalence_safety.py
```

結果は `5 passed in 0.02s`．`VIRTUAL_ENV` が別プロジェクトを指す警告が出るが，uv はそれを無視して atrium の環境を使う．既存の仮想環境や共通設定は変更していない．

この文書を読んだ Claude Code への再開指示例：

```text
plans/p0008_gpu_node_equivalence_handover.md と計画書を読み，wafl500〜509 の全実験を引き継いで遂行せよ．完了済みの情報収集・予備測定を再利用し，GPU の負荷試験は監視と中止処理を実装・検証してから実施せよ．
```
