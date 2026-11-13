# GPU ノード同等性検証スクリプト

wafl500〜509（RTX 3060）の構成と性能の同等性を検証するスクリプト群である．計画書は
[plans/p0008_gpu_node_equivalence.md](../../plans/p0008_gpu_node_equivalence.md)，引き継ぎは
[plans/p0008_gpu_node_equivalence_handover.md](../../plans/p0008_gpu_node_equivalence_handover.md)．

## 実行の区分

スクリプトは「制御点側」（このマシンで実行）と「ホスト側」（wafl50x で SSH 経由実行）の 2 種がある．
ホスト側スクリプトは `distribute.py` で全 10 台へ配布し，sha256 を manifest と照合する．
GPU 負荷は必ず `monitor.py`（80 °C・熱減速・Xid で stop ファイルを書く）を並行して起動すること．

## 制御点側スクリプト

| スクリプト | 役割 |
|---|---|
| `collect.py` | 全台の構成情報（inventory）を並列収集 |
| `prepare.py` | 固定 digest イメージの取得とスクリプト配布 |
| `check_state.py` | 全台の稼働状態・占有可否の確認 |
| `make_manifest.py` | 対象・パラメータ・許容幅・中止値・source hash を manifest に固定 |
| `distribute.py` | ホスト側スクリプトを全台へ再配布し hash を照合 |
| `run_benchmarks.py` | マイクロベンチマーク（fp32/fp16/VRAM/PCIe/VRAM 照合）と embed/score の 10 ラウンド実行 |
| `run_llm_benchmarks.py` | LLM 生成（Ollama）の 10 ラウンド実行 |
| `run_sustained_benchmarks.py` | 持続負荷（30 分×3 回，生成・採点）の実行 |
| `run_network_test.py` | 制御点→各ホストの RTT・帯域測定 |
| `analyze_inventory.py` | 構成比較表の生成 |
| `analyze_cpu.py` | CPU 予備測定の集計 |
| `analyze_benchmarks.py` | マイクロ・embed/score の全組比と同時信頼区間（Bonferroni） |
| `analyze_llm.py` | LLM 生成の全組比と出力ハッシュ照合 |
| `analyze_sustained.py` | 持続負荷の熱減速検出（末尾 10 分／開始 5 分） |
| `analyze_hw_characterization.py` | 持続負荷中の温度・ファン・クロック・電力のハードウェア特性集約 |

## ホスト側スクリプト

| スクリプト | 役割 |
|---|---|
| `workload.py` | コンテナ内で CUDA 演算・転送・VRAM 照合・embed/score を測定 |
| `safety.py` | 中止判定（80 °C 以上・熱減速）の純粋関数 |
| `monitor.py` | 1 秒間隔の GPU・CPU・RAM・競合プロセス監視と stop ファイル作成 |
| `run_host.py` | 単一ホストで 1 指標を監視付き実行（マイクロ・embed/score） |
| `llm_client.py` | Ollama への固定プロンプト生成要求と速度・TTFT・出力ハッシュ記録 |
| `run_llm_host.py` | 単一ホストで Ollama を起動し LLM 生成の 1 ラウンドを実行 |
| `run_sustained_host.py` | 単一ホストで 30 分の持続負荷（生成・採点）を実行 |

## 結果の保存先

- 制御点: `results/20261111_gpu_equivalence/`（manifest，分析結果，ラウンドごとの状態）
- ホスト: `/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111/runs/<run_id>/`
  - 短時間: `round_<n>/<host>/<metric>/`（workload.jsonl，monitor.csv，status.json）
  - LLM: `round_<n>/<host>/llm/llm_<kind>_c<conc>.jsonl`
  - 持続: `sustained/<host>/<kind>_r<rep>/`
  - 中止: `aborted/<host>`（ホスト単位で以降の負荷を拒否するマーカー）
