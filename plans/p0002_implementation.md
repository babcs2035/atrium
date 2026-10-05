# p0002 実装計画：分散連合 RAG の実験基盤（E0・E1）

- 作成日：2026-10-05
- 前提：`plans/p0001_initialization.md`（研究計画書）
- 状態：実装済み（実機での実行は未着手）

## 1. 目的

研究計画書の E0（実機の性能実測）と E1（RAGRoute の再現）を，`mise run setup / deploy / start / analyze`
の 4 コマンドで実行できるようにする．あわせて，E2 以降の手法（RQ1〜RQ3）を research-cycle の
レバーとして 1 か所の変更で追加できる差し替え口を用意する．

## 2. ユーザーと合意した判断

| ID | 判断 | 理由 |
|---|---|---|
| A1 | 今回は「分散ノード基盤＋E0＋E1」まで．E2〜E4 の手法は差し替え口だけ用意する | 比較の土台を確実に作る |
| B1 | RAGRoute は取り込まず，複数台向けに再実装する．ルーターの MLP・学習手順・プロンプト・回答の抽出は MIT 表記付きで移植する | RAGRoute は 1 台の中で ZMQ の IPC により連携する設計で，複数台へ広げる改修が大きい |
| C1 | wafl-ctrl5 をデータ中継点にする | gpu2 の空きは 237 GB で足りない |
| D1 | 各ノードでは Docker とローカル registry を使う | expert-mesh で実績のある手順を流用し，61 台で環境を揃える |
| E1 | MedCPT の埋め込みは全て自前で再計算する | MedRAG が配布していた埋め込み（SharePoint）が全て 403 を返す（2026-10-05 確認） |
| F1 | FeB4RAG のデータ源は，配布されたエンジンごとの検索結果（要求あたり上位 1,000 件）を返す | 13 コーパス全体の再埋め込みは数週間規模になる |
| G1 | MedCPT の埋め込みは，制御点と wafl500〜509 の GPU 11 枚で分担する（2026-10-05 のユーザーの指示） | GPU 1 枚では 3〜4 日かかる（実測した約 200 断片/秒からの見積もり） |

## 3. 役割の分担

```
gpu2（操作端末）: mise・分析・テスト
  │ ssh wafl-ctrl5
  └─ wafl-ctrl5（制御点＝データ中継点）: build・registry(5002)・コーパス取得・埋め込み・ラベル・学習
       │ ssh denjo@<IP>
       ├─ 192.168.15.100 = wafl500（質問者）: クエリ埋め込み・ルーティング・統合・回答生成（Ollama/GPU）
       └─ 192.168.13.100〜109, 14.100〜109（専門家 20 台）: シャードの検索・宿る型の回答（Ollama/CPU）
```

専門家には，研究計画書 §6.1 の L480 ではなく，制御点から SSH できる上記の 20 台（i5-8350U，16 GB，
GPU なし）を使う（2026-10-05 のユーザーの指示．台数を増やすときは config.yaml の cluster.expert_hosts に
足す）．gpu2 は各ノードへ直接 SSH しない．
各ノードの操作は，gpu2 がリポジトリを制御点へ rsync したうえで，制御点の `scripts/remote/*.sh` が行う．
シャードは制御点から各専門家へ `rsync -L`（symlink の実体を送る）で配る．

## 4. データの流れ

1. 中継点が MedRAG の断片（`chunk/*.jsonl`）を取得し，MedRAG の `embed()` と同じ手順
   （MedCPT-Article-Encoder，入力は `[title, content]`，CLS pooling）で埋め込みを計算して，
   断片ファイルごとに fp16 の `emb/<name>.f16.npy` として保存する．
2. 断片ファイルを名前順のまま，fp16 で約 6 GB ごとに束ねてシャードにする（`manifest.json`）．
3. 中継点が全質問のクエリ埋め込みを計算し，全シャードを GPU で総当たり検索して関連ラベル
   （統合後の上位 15 件に断片を出したデータ源）を作る．
4. 中継点がルーター（RAGRoute の MLP）を学習する．
5. deploy がシャードを専門家へ配り，専門家は起動時に fp16 の FAISS 平坦索引
   （`IndexScalarQuantizer(QT_fp16, METRIC_INNER_PRODUCT)`）を組み立てる．
6. start で質問者が各質問を処理し，`results.jsonl` を書く．
7. analyze がラベルと突き合わせて指標を計算する．

## 5. 専門家ノードの API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/.well-known/agent-card.json` | A2A の Agent Card（自己紹介） |
| GET | `/v1/profile` | シャードごとの文書数・重心・検索器 |
| POST | `/v1/retrieve` | 断片返却型：上位 k 件の断片を返す |
| POST | `/v1/answer` | 宿る型：自分の断片と LLM で回答し，回答文だけを返す |
| GET | `/healthz` | 起動状態 |

エラーは RFC 9457 の `application/problem+json` で返す．

## 6. 差し替え口（E2 以降のレバー）

ルーティング方式は `atrium.routing` の `Router` プロトコルを実装して登録する．

```python
class Router(Protocol):
    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]: ...
```

`SourceProfile` には各データ源の自己紹介文・重心・検索器が入る．RQ1 の「自己紹介文の類似度」
「代表文書の要約」「面接方式」は，この入力だけで実装できる．

## 7. RAGRoute との差分（再現性に影響するもの）

| 項目 | RAGRoute | 本実装 | 理由 |
|---|---|---|---|
| MedCPT の文書埋め込み | MedRAG 配布の fp32 | 同じ手順で再計算した fp16 | 配布元が 403．fp16 は L480 のメモリに収めるため |
| 索引 | `IndexFlatIP`（fp32） | `IndexScalarQuantizer`（fp16） | 同上 |
| 統合（再ランク） | 既定は bge-reranker-v2-m3 | 既定は検索スコア（`retrieval.merge` で切替可） | ラベル定義（スコアで統合した上位 15 件）と揃えるため |
| LLM の出力上限 | 40,960〜131,072 トークン | `llm.num_predict`（既定 2,048） | 打ち切りのない生成で待ち時間が発散するのを避ける |
| FeB4RAG のデータ源 | FAISS で検索 | 配布の検索結果を返す | F1 |
| FeB4RAG の重心 | 全文書の平均 | 無作為抽出（既定 2,000 件）の平均 | 同上 |
| 選択肢の書式 | liquid で dict を描画 | MedRAG と同じ `A. ...` の行 | MedRAG の原典に合わせる |
