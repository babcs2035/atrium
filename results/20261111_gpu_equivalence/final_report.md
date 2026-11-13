# GPU ノード同等性検証 最終報告（wafl500〜509）

計画書: [plans/p0008_gpu_node_equivalence.md](../../plans/p0008_gpu_node_equivalence.md)
引き継ぎ: [plans/p0008_gpu_node_equivalence_handover.md](../../plans/p0008_gpu_node_equivalence_handover.md)
manifest: `manifest_20261111_gpu_equivalence.json`（許容幅・中止値・source hash を測定前に固定）

## 1. 結論（要約）

**10 台を構成同一・性能同等のノード群として扱うことはできない。** 以下の 3 点が独立に成立する。

1. **構成は同一でない**（§2）。GPU の vbios（8 種）、gpu_part / PCI device ID（wafl507 が別 Revision）、PCIe gen（wafl508/509 が gen4）、max SM clock（wafl502 が 2160 MHz）、power_limit（wafl502 が 180 W）、CPU（4 種）、RAM（2 種）、カーネル（2 種）、イメージ digest（4 種）に差がある。
2. **制御下短時間性能に有意な差がある**（§3）。fp32・h2d・d2h・LLM 同時 4 並列（short_c4）で組の性能比が許容幅 0.95〜1.05 を逸脱。fp16・VRAM 帯域・embed/score・LLM 同時 1（short_c1 / long_c1）は同等。
3. **熱的に限界のある 6 台と安定な 4 台に二分する**（§5）。制御負荷で 80 °C に達して中止した 5 台（wafl500/501/504/505/509）と LLM 実験で中止した wafl502 の計 6 台は熱的に限界。残る 4 台（wafl503/506/507/508）は 30 分の持続生成・採点で熱減速なし。

計画書 §5 の「構成の一致と性能の同等性は別々に報告する」「中止したホストも 10 台の結果に含め，残った台だけの結論を全体へ広げない」に従い，構成・性能・熱の 3 点を別々に報告する。性能比は中止した 6 台を除外した 4 台（一部は 5 台，§3 参照）で計算し，10 台全体への一般化は行わない。

## 2. 構成比較（10 台）

一次記録 `inventory/config_comparison.md` を参照。判定は「一致」以外を構成不一致とする（UUID・シリアルは個体識別用で不一致に計上しない）。

| 区分 | 属性 | 結果 |
|---|---|---|
| GPU | driver / cuda / product / VRAM | 一致（595.99.02 / CUDA 13.2 / RTX 3060 / 12288 MiB） |
| GPU | vbios | **不一致（8 種）** |
| GPU | gpu_part / pci_device_id | **不一致（2 種，wafl507 が 2503-300-A1 / 250310DE，他が 2504-302-A1 / 250410DE）** |
| GPU | pcie_max_gen | **不一致（2 種，wafl508/509 が gen4，他 gen3）** |
| GPU | clocks_max_sm | **不一致（3 種，wafl502: 2160 / wafl501: 2130 / 他 2100 MHz）** |
| 電力 | power_limit | **不一致（2 種，wafl502 が 180 W，他 170 W）** |
| 電力 | power_max | 不一致（4 種） |
| PC | cpu_model | **不一致（4 種：i7-8700 / i7-9700 / i7-9700K / i5-12400F）** |
| PC | ram_total | 不一致（2 種：15 GiB / 31 GiB） |
| 実行環境 | rt_kernel | 不一致（2 種：7.0.0-42 / 6.8.0-147） |
| 実行環境 | rt_full_image_id | **不一致（4 種，wafl500/501 は digest 固定なし，wafl508/509 は別 digest）** |
| 占有 | ct_containers / ct_compute_apps | 一致（全 10 台で常駐なし，占有可） |

**判定: 構成は同一でない。** 特に GPU の vbios・Revision・PCIe gen・clock・電力設定に差があり，これらが §3 の性能差と対応する（§7）。

## 3. 制御下短時間性能（10 ラウンド，乱数入れ替え，Bonferroni 同時 95% CI）

許容幅 0.95〜1.05。中止したホストは性能比の対象から外し「熱による中止」として報告する。

### 3.1 マイクロベンチマーク（対象 5 台：wafl502/503/506/507/508，中止 5 台）

マイクロベンチマーク実行時は wafl502 が未中止だったため対象 5 台（10 組）。`benchmarks/analysis.md` を参照。

| 指標 | 判定 | 主な差 |
|---|---|---|
| fp32 | **performance_difference** | wafl502 が wafl506/507 より ~6% 高速（power_limit 180 W + max SM 2160 MHz と対応） |
| fp16 | equivalent | — |
| vram_bandwidth | equivalent | — |
| h2d | **performance_difference** | wafl508 が ~10% 低速（PCIe gen4 構成差と対応） |
| d2h | **performance_difference** | wafl506 が ~31% 低速，wafl508 が ~11% 低速 |
| vram_check | equivalent | — |

### 3.2 LLM 生成（対象 4 台：wafl503/506/507/508，中止 6 台）

llama3.1:8b，固定 digest イメージ，temperature=0，1 条件 100 要求。wafl502 は LLM 実験のラウンド 10 で熱減速により中止したため除外（`llm/llm_analysis.md` を参照）。

| 条件 | 判定 | 主な差 |
|---|---|---|
| short_c1（短入力・同時 1） | equivalent | — |
| short_c4（短入力・同時 4） | **performance_difference** | wafl503/508 が速く，wafl506/507 が ~6% 低速（2 対 2）．507/508 の組が許容幅を逸脱 |
| long_c1（長入力・同時 1） | equivalent | — |
| long_c4（長入力・同時 4） | undetermined | 同様の 2 対 2 構造で ~5% 差（CI が許容幅と交差） |

生成速度は同時 1 で ~65 tok/s，同時 4 で ~42 tok/s。

**推論性能の熱的検証（2026-11-13 追加）:** LLM 実験中（全 10 ラウンド，monitor.csv 4000+ 標本）に熱減速（sw/hw_thermal）は全 4 台で検出されず，80 °C 中止閾値にも到達していない。short_c4 の ~6% 差の正体は，生成中の SM クロックの個体差（busy 中央値: wafl503: 1800 > wafl508: 1755 > wafl506: 1740 > wafl507: 1732 MHz）と温度差（wafl503: 67 °C vs 他 3 台: 74-76 °C）であり，これが 2 対 2 の構造を生んでいる。c1 では差が ~2.5% に縮んで許容幅内（equivalent）であるため，**推論性能に「著しい差」はない**。並列（c4）では 506/507 がわずかに遅い点に注意。

**出力ハッシュの検証について:** short 入力では全ホスト・全ラウンドで同一出力（1 ハッシュ）であり，モデル・精度・量子化の一致を裏付ける。一方 long 入力では**同一ホスト内でもラウンド間で支配的出力が 2 値（例 `69b9e95b` / `b1e0b309`）を行き来する**非決定性を確認した（ラウンド内は 99〜100/100 が同一）。これはホスト間の精度差ではなく long 生成に内在する非決定性であり，long 入力の「出力ハッシュ不一致」をホスト差の根拠とはしない。なおハッシュ照合は初回ラウンドの支配的ハッシュのみを確認するため，long 入力では完全な検証にはならない。

### 3.3 埋め込み・採点（対象 4 台：wafl503/506/507/508，中止 6 台）

`models/analysis.md` を参照。

| 指標 | 判定 |
|---|---|
| embed | equivalent |
| score | equivalent |

## 4. ネットワーク（制御点 → 各ホスト）

`network/network.json` を参照。100 MiB 転送の帯域は 82〜88 MiB/s（比 ~1.07，一致）。RTT は 183〜256 ms（ProxyJump 経由の変動）。**ネットワークは性能差の混入要因ではない。**

## 5. 持続負荷（30 分 × 3 rep，生成・採点）

基準: 末尾 10 分の速度／開始 5 分の速度 >= 0.95，かつ温度ピーク < 80 °C。中止済み 6 台は拒否（`sustained/sustained_analysis.md` を参照）。

| host | 生成 最小比 | 生成 温度ピーク | 採点 最小比 | 採点 温度ピーク | 判定 |
|---|---|---|---|---|---|
| wafl503 | 0.999 | 69.0 °C | 1.000 | 66.0 °C | sustained_ok |
| wafl506 | 1.000 | 77.0 °C | 1.000 | 76.0 °C | sustained_ok |
| wafl507 | 1.000 | 77.0 °C | 1.000 | 74.0 °C | sustained_ok |
| wafl508 | 0.998 | 77.0 °C | 1.000 | 75.0 °C | sustained_ok |

4 台とも 30 分の持続生成・採点で熱減速なし（速度比 0.998〜1.000，温度ピーク 66〜77 °C）。

## 6. 熱的特性（中止 6 台）

| host | 中止時点 | 中止理由 |
|---|---|---|
| wafl500 | 制御負荷（fp32） | 80 °C 到達 |
| wafl501 | 制御負荷（fp32） | 80 °C 到達 |
| wafl502 | LLM 実験 ラウンド 10 | thermal_slowdown_active |
| wafl504 | 制御負荷（fp32） | 80 °C 到達（ラウンド 1 完了後） |
| wafl505 | 制御負荷（fp32） | 80 °C 到達 |
| wafl509 | 制御負荷（fp32） | 80 °C 到達 |

6 台は熱的に限界があり，制御負荷・持続負荷のいずれも未完遂として扱う。これら 6 台の性能比は計算しない。

## 7. 性能差と構成差の対応

- **wafl502**（power_limit 180 W / max SM 2160 MHz）: fp32 で ~6% 高速。電力・クロック余裕が fp32 演算に効く。
- **wafl508**（PCIe gen4）: h2d で ~10% 低速。PCIe 世代の差が転送速度に影響。
- **wafl506**: d2h で ~31% 低速。PCIe 転送の個体差。
- **wafl506/507 vs wafl503/508**（LLM short_c4）: 同時 4 並列で ~6% 低速。LLM 実験中に熱減速は検出されず，生成中の SM クロック・温度の個体差（冷却の個体差）が要因。c1 では差が ~2.5% に縮む。

構成差（電力・クロック・PCIe）が性能差の要因として機能しており，「構成が同一でない」ことが性能差を生んでいる。

## 8. 最終判定

| 観点 | 判定 |
|---|---|
| 構成の一致 | **同一でない**（§2） |
| 制御下短時間性能 | **同等でない**（fp32 / h2d / d2h / LLM short_c4 に有意差，§3） |
| 持続性能 | 熱的に安定な 4 台は 30 分持続可，熱的に限界の 6 台は未完遂（§5, §6） |

**10 台を同一性能ノード群として扱うことはできない。** 実験では以下の区分で扱うべきである。

- **熱的に安定な 4 台（wafl503/506/507/508）**: fp16・VRAM 帯域・embed/score・LLM 同時 1 は同等だが，fp32・h2d・d2h・LLM 同時 4 に差がある。GPU コア性能（fp16）とメモリ帯域は同等，PCIe 転送と電力依存の fp32 は個体差がある。
- **熱的に限界の 6 台（wafl500/501/502/504/505/509）**: 制御負荷で 80 °C に達するため，長時間・高負荷の実験には不適。冷却改善なしには持続負荷不能。

## 9. 制約と未実施項目

- **A/B 比較（現在 A イメージ vs 制御 B イメージ）**: 時間制約により未実施。本報告の性能判定は制御 digest イメージ（B）で測定した値であり，現在運用中のイメージ（A）との差は未確認。A/B 差が大きい場合，本報告の判定は A には直接適用できない。
- **中止 6 台の性能比**: 熱中止により未測定。冷却改善後に再測定が必要。
- **long 入力の出力ハッシュ検証**: 初回ラウンドの支配的ハッシュのみ確認。long 生成の非決定性のため完全な検証にはならない。
- **温度・クロック・速度の時系列**: 持続負荷の温度ピーク・速度比を報告したが，1 秒間隔の全時系列プロットは未作成（`monitor.csv` に生データあり）。

## 10. 成果物の保存先

- 制御点: `results/20261111_gpu_equivalence/`（manifest，各分析，本報告）
  - `inventory/config_comparison.md`（構成比較表）
  - `benchmarks/analysis.md`（マイクロベンチマーク）
  - `llm/llm_analysis.md`（LLM 生成）
  - `models/analysis.md`（embed/score）
  - `sustained/sustained_analysis.md`（持続負荷）
  - `network/network.json`（ネットワーク）
  - `cpu/cpu_summary.md`（CPU 予備測定，同等性判定には不使用）
- ホスト: `/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111/runs/20261111_gpu_equivalence/`
  - 短時間: `round_<n>/<host>/<metric>/`（workload.jsonl，monitor.csv，status.json）
  - LLM: `round_<n>/<host>/llm/llm_<kind>_c<conc>.jsonl`
  - 持続: `sustained/<host>/<kind>_r<rep>/`
  - 中止: `aborted/<host>`
