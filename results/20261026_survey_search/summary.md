<!-- d0008 §2.1（調査の方法）と §3.7 の「見当たらない」に添える，RAG 側の文献検索の件数・取捨・分類・反例の探索の記録である． -->

# 文献検索の記録（RAG 側，2026-10-26 実施）

## 0. 使い方

- `docs/d0008_report_outline.md` の §2.1 と §3.7 に書く件数は，このファイルの数値を使う．数値は `stats.json` と同じである．
- 再現：`uv run --no-project python -I scripts/fetch_hits.py <DIR> arxiv`（同様に `s2`，`tavily`）→ `scripts/dedup_hits.py <DIR>` → `scripts/build_tables.py <DIR>`．
- 取捨と分類は人手で行い，`scripts/screening_decisions.txt` と `scripts/coding_input.tsv` に記録した．Semantic Scholar と Tavily の結果は日によって変わる．生の応答は `raw/` にある．

## 1. 検索の条件

- 実施日：2026-10-26．期間：2020-01-01〜2026-10-26．
- データベース：arXiv API，Semantic Scholar Graph API（`/paper/search/bulk`．論理式で，関連度による打ち切りが無い），Tavily（`tavily_venues`：aclanthology.org・dl.acm.org・ieeexplore.ieee.org，`tavily_web`：学術のドメイン 9 個．どちらも depth advanced・上位 20 件）．
- 連合検索・選択的検索の系譜は原典で引用し，件数に含めない．
- 検索語：Q1 federated retrieval-augmented generation，Q2 federated RAG，Q3 distributed retrieval-augmented generation，Q4 decentralized retrieval-augmented generation，Q5 edge RAG／edge-cloud RAG，Q6 peer-to-peer RAG，Q7 privacy-preserving RAG multiple data sources，Q8 RAG data silos．論理式は `scripts/fetch_hits.py` の `QUERIES` にある．

## 2. 件数

| ID | arXiv | Semantic Scholar | Tavily（学術のドメイン） | Tavily（ACL・ACM・IEEE） |
|---|---:|---:|---:|---:|
| Q1 | 61 | 211 | 14 | 15 |
| Q2 | 9 | 21 | 13 | 10 |
| Q3 | 86 | 33 | 20 | 16 |
| Q4 | 29 | 101 | 17 | 15 |
| Q5 | 62 | 136 | 16 | 20 |
| Q6 | 2 | 7 | 20 | 10 |
| Q7 | 9 | 52 | 17 | 20 |
| Q8 | 19 | 65 | 20 | 20 |
| 計 | 277 | 626 | 137 | 126 |

- ヒットの総数は 1,166 件，重複を除くと 734 件（Tavily だけで見つかったものは 104 件）．
- 取捨（題目と要旨）：含める 86 件．除外は，1 主体または集めた文書集合の RAG 259 件，利用者とクラウドの 2 者の秘匿 8 件，主題が違う 205 件，応用の事例 80 件，総説 76 件，検索を持たない連合学習 11 件，生成を持たない連合検索 2 件，論文でないもの 4 件，別版 3 件．
- 含めた 86 件の年：2023 年 1 件，2024 年 10 件，2025 年 31 件，2026 年 43 件，不明 1 件．
- 分類（86 件．「記載なし」は読んだ範囲に書かれていないもの）：
  - データの分け方：話題・分野ごと 20，公開データの人工的な分割 11，端末とクラウドの 2 層 7，持ち主ごと 2，該当しない 6，記載なし 40．
  - 評価の形：模擬・1 台 50，複数の物理的な機器 4，実験なし 10，記載なし 22．
  - 中央との比べ方：比べない 45，LLM の相対勝率 1，正解に対する正誤 9，そのほか 17，記載なし 14．
  - 中央に同じ検索の改良を入れたもの：1（P0540）．
  - 測ったもの：露出 14，待ち時間 30，計算・エネルギー 17．
- 分類の根拠：要旨 67 件，`docs/d0008_report_outline.md` の既知の論文の記述 11 件，全文の抜粋 3 件，題目だけ 5 件．

## 3. 反例の探索

- 探した反例：持ち主で分かれた私的データを複数の物理的な機器に置き，同じ検索の改良を入れた中央と正解に対する正答率で比べ，露出・時間・計算を合わせて測った研究．
- 結果：見当たらなかった．持ち主ごとの分割かつ物理的な機器の研究は 0 件．物理的な機器かつ正誤での中央との比較は GRaSP だけで，公開コーパスの配置であり露出を測っていない．
- 最も近い研究：
  - GRaSP（arXiv:2610.26938）：5 台の CloudLab で 4 地域の WAN を模擬し，Natural Questions の配置（一様・話題ごと）で検索の再現率と待ち時間の兼ね合いを測った．
  - DEFRAG（arXiv:2608.00922）：異なるエッジの機器の試験環境．中央の LLM サービスと比べる．
  - DRAGON（arXiv:2504.11197）：クラウドと端末の 2 台．
  - P0540（Ding ほか，ICICC 2026，DOI 10.1109/ICICC71012.2026.11637874）：SQuAD を 5 クライアントに分け，検索の予算をそろえて中央と連合を比べた．機器・露出・時間は扱わない．
  - SocialGenPod・Campus Assistant：持ち主ごとの分割だが，中央との定量的な比較が無い．
- 追加の検索（EnronQA，cost of decentralization，試験環境・Raspberry Pi・Jetson，メール・受信箱の語．`raw/hunt/`）でも，新しい候補は無かった．

## 4. 限界

- 題目と要旨による 1 人の判定である．全文を読んだのは，既知の論文（d0008 の記述に依る）と反例の候補 3 件だけで，「記載なし」が多い．
- 5 件は題目だけで含めた（P0527 は確かめられなかった）．
- ACL・ACM・IEEE は Tavily の上位 20 件でしか覆っていない．Google Scholar と Scopus は使わず，引用をたどる探索もしていない．
- DFAMS（arXiv:2508.20353）を拾えなかったので，検索語の網羅性に限界がある．
- 同じ作業の途中の報告に，記録と合わない件数（1,617 → 945 → 123 件）が出たが，ディスクの記録（`stats.json`・`dedup.jsonl`・`coding.csv`）と合うのは本ファイルの件数である．
