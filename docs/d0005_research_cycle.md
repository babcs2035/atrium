# d0005 research-cycle での研究の進め方

research-cycle skill（`~/.claude/skills/research-cycle/`）は，「調査・計画 → 実装・実験 → 分析・考察」の
サイクルを人間の常時介入なしに繰り返す．本リポジトリの設定は `.claude/research/config.yml` にある．

## 1. 開始と停止

```bash
~/workspace/start_research_cycle.sh atrium          # 開始（短縮名 atrium は repos.tsv に登録済み）
~/workspace/stop_research_cycle.sh atrium           # 停止
```

手動で 1 回だけ回すときは，リポジトリの直下で Claude Code を起動し `/research-cycle start`
（2 回目以降は `/research-cycle continue`）を実行する．

## 2. 1 イテレーションで行うこと

| フェーズ | subagent | 本リポジトリでの具体的な作業 |
|---|---|---|
| 調査・計画 | rc-researcher | `config.yml` の `levers` から単一レバーを選び，仮説・変更点・成功条件を `journal.md` に書く |
| 実装・実験 | rc-executor | `config.yaml` の 1 キー（またはルーティング方式 1 個の追加）を変え，`mise run deploy` → `mise run start` → `mise run analyze` → `uv run atrium metrics --json` を実行し，`mise run stop` で後始末する |
| 分析・考察 | rc-evaluator | `metrics.json` と `analysis_report.md` を読み，採否を判定して journal を確定し，git commit する |

## 3. 初期のレバー（`config.yml` の `levers`）

研究計画書 §8 の順に並べてある．

1. `experiment.kind=e0_measure`：E0．データ準備と並行して進められる
2. `experiment.routing=all`（`retrieval_only`）：ラベル一致で基盤の正しさを確かめる（以降の比較の前提）
3. `experiment.answer_mode=snippet_return`：all 方式の正答率
4. `experiment.routing=ragroute / random / none`：RAGRoute の残りの 3 方式
5. `retrieval.merge=cross_encoder`：RAGRoute の既定の再ランク
6. `experiment.dataset=feb4rag`：多数のデータ源の条件

E1 の再現が成立したら，研究計画書 §5 の RQ1 の方式（自己紹介文の類似度・代表文書の要約・面接方式）を
`atrium.routing` の Router として実装するレバーを追記する（[d0001](d0001_architecture.md) §5）．

## 4. 守ること

- 実験条件は `config.yaml` の差分だけで追跡する．環境変数などで個別に上書きしない．
- データ準備（制御点のコンテナ `atrium-data`）を止めたり，制御点のデータディレクトリを消したりしない．
  再計算に数日かかる．
- `mise run clean -- --full` は人間の判断を要する．
- ルーターの比較は `metrics.json` の `test` で行い，正答率の差は `atrium compare` の McNemar 検定で判断する．
