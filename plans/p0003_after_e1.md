# p0003 E1 完了後の手順書：E1 の判定 → E2 → E3 パイロット

- 作成日：2026-10-07（14:53〜15:21 の観測に基づく）
- 前提：`plans/p0001_initialization.md`（研究計画書），`plans/p0002_implementation.md`（実装計画）
- 状態：確定．ユーザーが A1・B1・C1 を採用した（§7）．この文書を承認することは，§5 の変更・`git commit`・`git push` の事前承認を含む
- 想定する読み手：E1 の全問実行（`run_full.sh`）の後を引き継ぐ Claude Code セッション．
  **完全に自律的に，上のステージから順に最後まで遂行する**（§1）

## 1. 方針

**この手順書は，完全に自律的に遂行する．** ユーザーへの質問・確認・承認待ちを行わず，ユーザーの介入なしに，
待機から S6 まで進む．commit と push も，ユーザーの指示を待たずに，§5 の規則に従って自分で行う．
確認が要りそうな判断は，§4 の既定と §5 の事前承認で，先に決めてある．決めていない事態が起きたときだけ，§6 に従って止まる．

1. **実験が終わるまで待つ．** `run_full.sh` が全段を終えるまで，実装・コミット・設定の変更・実機の操作を行わない（§3）．
   作業ツリーの `src/` からイメージを build する `deploy` と，コミットを照合する `start` が，実験の途中で影響を受けるためである
2. **終了後は，ステージ S1〜S6 を順に実行する**（§4）．各ステージに，手順・完了条件・失敗時の扱いを書いた．
   各ステージの完了時に，`commit` して `push` する（§5.2）
3. **止まる条件は §6 に限る．** 該当したら，作業を止めて報告し，終了する（質問して待たない）．§6 に無いことは，この文書の既定に従って進める
4. **範囲は，E2 の方式 1・2 の評価と，E3 のパイロットまでである．** 方式 3・4（要約・面接），E3 の本実験，E4 は含めない

## 2. 現状（2026-10-07 14:53 の観測）

E1 の全問実行は，背景ジョブのスクリプト `~/.claude/jobs/0b6384d1/tmp/full/run_full.sh` が，
8 つの条件を順に処理している．各段は `deploy` → `start` → `analyze` である．

| ID | データセット | routing | answer_mode | merge | 状態 |
|---|---|---|---|---|---|
| s1 | feb4rag | all | retrieval_only | qrels_oracle | 完了 |
| s2 | feb4rag | ragroute | retrieval_only | qrels_oracle | 完了 |
| s3 | medrag | all | retrieval_only | score | 完了（ラベル一致 0.999） |
| s4 | medrag | all | snippet_return | score | 実行中（6,668 / 7,663 問，03:56 開始） |
| s5 | medrag | ragroute | snippet_return | score | 待機 |
| s6 | medrag | random | snippet_return | score | 待機 |
| s7 | medrag | none | snippet_return | score | 待機 |
| s8 | medrag | all | snippet_return | cross_encoder | 待機 |

- s4 の進み方は約 10 問/分（進捗の線形外挿）で，16:30 前後に終わる見込みである．
  s5〜s8 は，同じ `snippet_return` で，合わせて 40 時間以上かかりうる（外挿であり，保証はない．s7 は検索が無いので短い可能性がある）．
- 作業ツリーの `config.yaml` が git 上で変更扱いなのは，`run_full.sh` が各段の前に 5 つのキーを書き換えているためである．
  スクリプトは終了時に `config.yaml.orig`（開始前の内容）で全体を戻す．
- この一括実行は research-cycle を通っていない．`.claude/research/journal.md` は 2026-10-06 22:18 から更新されておらず，
  採否の判定や記録は自動では行われない．
- 実験の開始（03:56）の後に，別の作業者が 2 つのコミットを作っている．`bc0126f`（04:03．s1〜s3 の結果の記録と `analysis` の修正），
  `0d7ed6b`（06:02．`src/atrium/requester.py` に，検索要求の接続エラーの再試行を追加）．
  s1〜s3 の `git_head` は `d238f5fdf1ac`（s1・s2 は `-dirty`），s4 は `d238f5fdf1ac-dirty` である．
  s5〜s8 は `deploy` のたびに作業ツリーから build するので，`0d7ed6b` 以降のコードで動く可能性がある．
  再試行は接続エラーのときだけ働くので，結果の値は変えないはずだが，s4 のイメージが再試行を含んでいたかは確かめていない（S2 で記録する）
- `results/` のうち，`results.jsonl`・`metrics.json`・`analysis_report.md`・`placement.json`・`run_meta.json` は，研究の一次記録として
  Git で追跡する．`logs/`・`requester.log`・`start.log` は `.gitignore` で除外されている．s3 の `results.jsonl` は約 23 MB である

## 3. 待機（実験の実行中）

**待つ対象**：`~/.claude/jobs/0b6384d1/tmp/full/progress.log` に `ALL DONE` が出て，`pgrep -f run_full.sh` が空になること．

**確認の方法**：次の読み取りだけを，30 分以上の間隔で行う．

```bash
tail -n 3 ~/.claude/jobs/0b6384d1/tmp/full/progress.log
pgrep -f run_full.sh
ssh wafl-ctrl5 'tail -n 1 /home/denjo/workspace/ktakahashi/atrium/results/<run_id>/start.log'
```

**待機中に行わないこと**

- 作業ツリー（`/mnt/data-raid/ktakahashi/workspace/atrium`）のファイルの編集（`config.yaml` を含む）と，`git commit`・`checkout`・`stash`
- `mise run` の全てのタスク（`stop`・`clean`・`data-status` も，制御点へ `rsync --delete` で同期するので行わない）
- 制御点・専門家への書き込み，`/home/denjo/atrium-data` の操作

**待機の終わり**：`ALL DONE` が出て，`run_full.sh` が終了したら，S1 に進む．
`ALL DONE` が出ないまま `run_full.sh` が消えた場合は，§6 の 3 に従う．

## 4. ステージ

### S1 後始末と確認（実機に触れる）

1. `git diff --stat` の出力が空であることを確かめる．`config.yaml` に差分が残っていれば，
   `~/.claude/jobs/0b6384d1/tmp/full/config.yaml.orig` と `git diff` の内容を比べ，開始前の内容（`git show HEAD:config.yaml` と同じ）に戻す
2. `~/.claude/jobs/0b6384d1/tmp/full/summary.tsv` を読み，s1〜s8 の全てが `start=0` または `start=2`（一部の質問が失敗）かを確かめる
3. `mise run stop` を実行する．`clean` は実行しない
4. 操作端末の `results/` に，s1〜s8 の `analysis_report.md` と `metrics.json` があるかを確かめる

**失敗した段の再実行**（`deploy_failed`，`start` の終了コードが 0・2 以外，結果が欠けている段がある場合）：
その段について，1 回だけ，次を行う．

1. `config.yaml` を，`run_full.sh` の `step` の引数と同じ 5 つのキー（`dataset`・`routing`・`answer_mode`・`merge`・`question_limit`）に書き換える
2. `mise run deploy` → `mise run start <同じ run_id>`（途中の結果から再開する）→ `mise run analyze <run_id>`
3. `git checkout -- config.yaml` で元に戻す

再実行しても失敗するときは，§6 の 2 に従う．

**完了**：s1〜s8 の結果が揃い，`git status` に `config.yaml` の変更が無く，各ノードのコンテナが止まっている．

### S2 E1 の判定と記録

**確認**（各実行の `analysis_report.md` と `metrics.json`）

- 失敗率，`unparsed_choice_rate`，正答率とその 95% 信頼区間，s3 のラベル一致

**比較**（s4 を基準に，同じ質問に対する正誤の組で比べる）

```bash
for r in s5_med_ragroute s6_med_random s7_med_none s8_med_all_ce; do
  uv run atrium compare results/20261007_s4_med_all_snip results/20261007_$r
done
```

**判定**（`.claude/research/config.yml` の `success_criteria` に対応）

| 項目 | 基準 | 満たさないとき |
|---|---|---|
| ラベル一致 | s3 が 0.99 以上 | §6 の 1 |
| 失敗率 | 各実行で 1% 以下 | §6 の 2 |
| `ragroute` の再現 | s2・s5 の test の再現率と削減率を，RAGRoute の報告値（`plans/p0001` §7.2 の FeB4RAG の平均 9.20 エンジンなど，リポジトリ内で確認できるもの）と並べて記録する．リポジトリ内に報告値が無い項目は「比較できない」と記録する | 差は，原因の候補（fp16 の埋め込み，温度 0，LLM の違い，ラベルの再計算．`plans/p0002` §7）とともに記録するだけにし，先へ進める．E2 の評価は，この基盤の中の相対比較であり，論文値との一致を前提にしない |
| 方式間の差 | McNemar の正確検定で p < 0.05 のときだけ「差がある」と書く | － |
| 実行条件の揃い | s1〜s8 の `run_meta.json` の `git_head` を一覧にし，異なるものは，`git log`・`git diff` で，コードの違いを調べる | 止まらずに先へ進める．違いの内容（例：`0d7ed6b` の再試行）と，結果の値への影響の見込みを記録する |

待ち時間は，各条件 1 回の実測なので，主張に使わない（研究計画書 §9 は 3 回以上の繰り返しを求める）．

**s4 が中央集約 RAG と同じ検索結果を返すことの確認**：s3（`retrieval_only`）と s4 の `top_doc_ids` が，全問で同じことを確かめる．
s3 と s4 は，同じ質問・同じ検索・同じ統合なので，一致するはずである．不一致があれば，その件数と例を記録する．

**記録**：`.claude/research/journal.md` に，s1〜s8 の条件・結果・判定を書く．

**コミット**（1 つの意味的な変更ごとに分ける．英語．先頭に絵文字．本文に理由を書く）

1. `📝 Add p0003 plan for the work after the E1 full runs`（`plans/p0003_after_e1.md`）
2. `🧪 Record E1 full runs s4-s8`（`results/20261007_s4_*`〜`s8_*` の追跡対象のファイル）
3. `🧪 Record the E1 judgement`（`.claude/research/journal.md`）

その後，`push` する（§5.2）．

**完了**：判定の数値（失敗率・`unparsed_choice_rate`・正答率と信頼区間・McNemar の p 値）が，記録から追え，3 つのコミットが `origin/main` にある．

### S3 E2 の実装（作業ツリー．実機は使わない）

この時点で実験は全て終わっているので，作業ツリーで直接作業する．

**追加する方式（B1：MedRAG は 4 データ源のまま）**

| 名前 | 方式 | 入力 | 学習 |
|---|---|---|---|
| `desc_sim` | 自己紹介文の類似度 | クエリ埋め込みと，自己紹介文の埋め込みの内積 | 使わない |
| `centroid_sim` | 代表文書（重心）の類似度 | クエリ埋め込みと，`SourceProfile.centroid` の内積 | 使わない |

どちらも，内積の高い順に上位 `k` 個のデータ源を選ぶ．

**設計の既定**（実装者が確認なしに採る．採った理由は，コードの docstring に書く）

| 項目 | 既定 | 理由 |
|---|---|---|
| 自己紹介文の埋め込み | MedRAG は `ncbi/MedCPT-Article-Encoder`（文書側） | MedCPT は，クエリ側と文書側で別のモデルを使い，クエリ埋め込みとの内積で検索する．重心も文書側の平均なので，同じ空間に置く |
| `k` の選び方 | MedRAG は {1, 2, 3}，FeB4RAG は {5, 7, 9, 11, 13} から，`train`・`val` の質問で F1 が最大のものを選ぶ．新参源の関連ラベルは，`k` の選択にも使わない | 新参源の情報を学習に混ぜないため．MedRAG の関連源は平均 1.78 個（4 源中），FeB4RAG は平均 9.4 個（13 源中）なので，この範囲を探せば足りる |
| 再学習しない `ragroute`（`ragroute-frozen`） | 新参源を除いた N−1 個で学習し（one-hot の長さは N−1），推論のときに新参源の one-hot を 0 にして入力する | 「中央の分類器を新参源のために再学習しない場合」を表す |
| 上限（`ragroute-upper`） | 既存の `router.pt`（全データ源で学習済み）をそのまま使う | 再学習した場合の上限．追加の学習が要らない |
| 他の基準 | `all`・`none`・`random`（`routing.random_k`，`experiment.seed`） | E1 と同じ |
| 評価の対象 | MedRAG は 4 通り，FeB4RAG は 13 通りの除外．ただし FeB4RAG は `centroid_sim` のみ | FeB4RAG の自己紹介文の埋め込みには，各エンジンの検索器（SGPT 5.8B を含む）が要り，重い．FeB4RAG は多源条件の確認用で，主評価は MedRAG（研究計画書 §10） |
| 評価に使う質問 | 評価は `test`，`k` の選択は `train`・`val` | E1 と同じ分割（`labels.py`） |

**変更の内容**（§5 で事前承認済み）

1. `src/atrium/routing/` に `desc_sim`・`centroid_sim` の `Router` を追加し，`make_router()` に登録する
2. `config.py` の `RoutingName` に 2 つの名前を足す．`RoutingConfig` に，`similarity_top_k: dict[DatasetName, int]` を既定値付きで足し，`config.yaml` と `docs/d0002_configuration.md` を更新する
3. `train_router.py` の `train_router` に，除外するデータ源と出力先のディレクトリを引数で足す（既定値は今までの動作と同じ）．
   出力先を `paths.router` 以外にできるようにする
4. 新しいモジュール `src/atrium/loso.py` と，CLI のサブコマンド `atrium loso` を追加する．
   ラベル・重心・自己紹介文・クエリ埋め込みから，LOSO の指標（適合率・再現率・F1・問い合わせ数，新参源の再現率）を計算する．実機は使わない
5. 実行の `top_doc_ids` を，参照の実行（s3）と比べる関数を `loso.py` に足す（S5 の下流確認で使う）
6. 上の全てについて，`tests/` にテストを書く．DB・実機が要らない軽量なテストにする

**データの準備**（読み取りだけ）

```bash
rsync -a wafl-ctrl5:/home/denjo/atrium-data/medrag/{manifest.json,queries,router} artifacts/medrag/
rsync -a wafl-ctrl5:/home/denjo/atrium-data/feb4rag/{manifest.json,queries,router} artifacts/feb4rag/
uv run python -c "from huggingface_hub import snapshot_download; snapshot_download('ncbi/MedCPT-Article-Encoder')"
```

`artifacts/` は Git で追跡しない．転送の前に `du -sh` で大きさを確かめ，100 MB を超えるときは §6 の 4 に従う．
LOSO の学習の出力先は，操作端末の `artifacts/` 以下にする．制御点の `router/` には書かない．

**検証**：`mise run check`（テスト・lint・型検査）が通ること．

**コミット**（意味ごとに分ける．各コミットで `mise run check` が通ること）

1. `train_router` の引数の拡張とテスト
2. `desc_sim`・`centroid_sim` の `Router`・設定・テスト・`d0002`
3. `loso.py`・`atrium loso`・テスト・`d0001` §5 と `d0004` の更新

3 つのコミットが済んだら，`push` する（§5.2）．

**完了**：`mise run check` が通り，`atrium loso` が `train`・`val` で動く（`test` はまだ評価しない）．コミットが `origin/main` にある．

### S4 E2 の評価（実機は使わない）

**成功条件**（本計画で固定する．E1 の結果を見て変えない．S4 の評価の前に，`journal.md` に写して commit する）

MedRAG の 4 通りの除外について，次の 2 つを**同時に**満たす方式が 1 つ以上あること．

- (a) 1 問あたりの平均問い合わせ数が，3.0 以下（`all` の 4.00 に対する削減率 25% 以上）
- (b) 新参源の再現率が，`ragroute-upper` の新参源の再現率の 80% 以上

新参源の再現率は，4 通りの除外で，（質問，新参源）の組を合わせて計算する．4 通りそれぞれの値も併記する．
FeB4RAG の結果は参考にとどめ，判定には使わない（もともと関連源が多く，削減の余地が小さいため）．
80% は，研究計画書 §8 E2 の「上限の一定割合以上」を数値にした，暫定の基準である．この計画を作った時点で固定する．

**手順**

1. 成功条件を `journal.md` に書き，commit し，**評価の前に push する**（`📝 Pre-register the E2 success criteria`）．
   登録の記録を，評価の結果より先に，リモートへ残すためである
2. `uv run atrium loso --split test` を，MedRAG・FeB4RAG で **1 回だけ** 実行する．結果を `results/e2_loso_<YYYYMMDD_HHMMSS>/` に書く（`metrics.json` と `analysis_report.md`）
3. 判定を `journal.md` に書く．結果を見て成功条件や `k` を変えない
4. commit し，push する（`🧪 Record the E2 LOSO evaluation`）

**分岐**

- 方式 1・2 のどちらかが成功 → S5 に進む．成功した方式が複数あるときは，新参源の再現率が最大のものを 1 つ選ぶ（同点なら平均問い合わせ数が小さい方）
- どちらも不成功 → S5 を飛ばして S6 に進む．原因の候補（MedRAG の関連ラベルが検索結果から作られていること，重心が粗いこと，データ源が 4 つしかないこと）を `journal.md` に書く．
  これは研究の結果であり，失敗ではない．データ源の粒度を変える案（`plans/p0001` §10）は，ユーザーの判断が要るので，報告に書くだけで，実行しない

### S5 E2 の下流確認（実機．S4 で成功した方式がある場合だけ）

**条件**：`dataset: medrag`，`routing: <選んだ方式>`，`answer_mode: retrieval_only`，`merge: score`，`question_limit: null`．
所要時間は，S3 の全問と同程度（約 2.5 時間）以下の見込みである．

**手順**

1. `config.yaml` を上の条件に書き換える．`mise run deploy` → `mise run start 20261008_e2_<方式名>`（`run_id` は，実際の日付に合わせる）→ `mise run analyze <run_id>` → `mise run stop`
2. `git checkout -- config.yaml` で元に戻す
3. 確認する値：失敗率，データ源選択の指標（`test`），1 問あたりの平均問い合わせ数，`top_doc_ids` の s3 との一致率（S3 の 5 の関数）
4. `journal.md` に記録する．commit は，`mise run stop` の後に，次の 2 つに分けて行い，push する（§5.2）
   - `🧪 Record the E2 downstream run of <方式名>`（`results/<run_id>/` の追跡対象のファイル）
   - `🧪 Record the E2 downstream check of <方式名>`（`journal.md`）

**完了**：失敗率が 1% 以下で，上の値が記録されている．コミットが `origin/main` にある．

### S6 E3 のパイロット（実機．C1）

**目的**：宿る型（`local_answer`）の所要時間・失敗の有無・傾向を，小さい標本で測る．

**質問の抽出**：`question_limit` は，コードの上では問題集ごとの上限である（`load_questions(limit_per_bank)`）．
`question_limit: 20` で，5 つの問題集（medqa・medmcqa・pubmedqa・bioasq・mmlu）の先頭 20 問ずつ，計 100 問になる．
無作為抽出ではなく，先頭からの抽出なので，結論には「先頭の標本」であることを書く．設定やコードの変更は要らない．

`run_id` の日付（以下の `20261008`）は，実際に実行する日付に合わせる．

**参照の実験**：s4（`snippet_return`）と s7（`none`）は，温度 0 で全問を実行済みなので，同じ質問の部分集合を，再実行せずに使う．
`atrium compare` は，2 つの実行に共通の質問だけを比べる．

**条件**：`dataset: medrag`，`routing: all`，`answer_mode: local_answer`，`merge: score`．
`routing: all` にするのは，回答方式だけを変えて s4 と比べるためである（s4 も `all`）．

**手順**

1. **動作確認**：`question_limit: 2`（10 問）で，`mise run deploy` → `mise run start 20261008_e3_smoke` → `mise run analyze`．
   MedRAG の宿る型は，まだ検証していない（検証済みなのは FeB4RAG の 5 問）．失敗率 0 を確かめる．失敗した場合は，原因を専門家のログ（`results/<run_id>/logs/`）と `requester.log` から調べ，
   コードの不具合を直せるなら 1 回だけ直して再実行する．直らなければ §6 の 2 に従う
2. **パイロット**：`question_limit: 20`（100 問）で，`mise run start 20261008_e3_pilot` → `mise run analyze`
3. **比較**：同じ質問の部分集合で，次を行う．
   ```bash
   uv run atrium compare results/20261007_s4_med_all_snip results/20261008_e3_pilot
   uv run atrium compare results/20261007_s7_med_none results/20261008_e3_pilot
   ```
   `n_shared` が 100 であることを確かめる．p 値は，標本が小さい間は，検出できる差が大きいことを添えて書く
4. **標本の追加**（C1 の「最初の結果を見てから増やす」を，次の基準で決める）：
   パイロットの失敗率が 0 で，所要時間が 3 時間以内なら，`question_limit: 50`（250 問）で 1 回だけ追加で実行する（`20261008_e3_pilot50`）．
   それ以外は，追加しない．追加しても，その先（200 問以上，繰り返し）は，この計画に含めない
5. `mise run stop` → `git checkout -- config.yaml` → `journal.md` に記録する．commit は，次の 2 つに分けて行い，push する（§5.2）
   - `🧪 Record the E3 pilot runs of local_answer`（smoke・pilot（・pilot50）の `results/<run_id>/` の追跡対象のファイル）
   - `🧪 Record the E3 pilot judgement`（`journal.md`）

待ち時間は，各条件 1 回の実測なので，主張に使わない．所要時間は，次の計画の見積もりのために，数値として記録する．

**完了**：失敗率・正答率と信頼区間・所要時間（全体・1 問あたり）・s4 と s7 との比較が，記録から追える．コミットが `origin/main` にある．

### 完了後の文書の更新

S6 の後に，次を更新して commit し，push する（`📝 Update the status after E1-E3 pilot`）．

- `README.md` と `docs/d0006_research_overview.md` の「現状」
- `.claude/research/config.yml` の `levers` に，E2 の方式を足す（単一レバー原則で，次の research-cycle が使える形にする）

最後に，チャットで，S1〜S6 の結果・確認できなかった項目・実行したコマンド・未確認のリスクを報告する．

## 5. 事前承認する変更と，commit・push の規則

### 5.1 事前承認する変更

この文書の承認をもって，次の変更を事前に承認したものとする．

| 変更 | 範囲 |
|---|---|
| `config.py` の `RoutingName` への名前の追加 | `desc_sim`・`centroid_sim` |
| `RoutingConfig` へのキーの追加 | `similarity_top_k`．既定値を持たせ，既存の `config.yaml` がそのまま読めること |
| CLI のサブコマンドの追加 | `atrium loso` |
| `train_router` の引数の追加 | 既定値は今までの動作と同じ |
| 新しいモジュール・テスト・文書の追加と更新 | `loso.py`，`tests/`，`d0001`・`d0002`・`d0004`・`d0006`，`README.md` |
| `git commit` | §5.2 の規則に従う |
| `git push` | `origin`（`git@github.com:babcs2035/atrium.git`）の `main` へ．§5.2 の規則に従う |
| 実機の操作 | S1・S5・S6 の `deploy`・`start`・`analyze`・`stop` |

**承認しない変更**：`results.jsonl` の既存のフィールドの名前や意味の変更，既存の設定キーの削除や意味の変更，
`retrieval.k_ret`・`k_rerank`・`cluster.shard_budget_gb`・`data.medrag.embed_precision` の変更，
データ準備の再実行，制御点のデータディレクトリの書き込み，`mise run clean`（`--full` を含む），
`git push --force` などの履歴の書き換え．

### 5.2 commit と push の規則

ユーザーの指示を待たずに，次の規則で，自分で commit と push を行う．

**commit**

- 1 つのコミットに，1 つの意味的な変更だけを入れる．メッセージは英語で，1 行目の最初に適切な絵文字を置き，本文に変更の理由を書く
- コミットの前に，`git status` と `git diff` で差分を確かめる．今回の変更に関係するファイルだけを，ファイル名を指定して stage する
  （`git add -A`・`git add .` は使わない）．作業前から存在した未コミットの変更には，触らず，コミットに混ぜない
- コードを含むコミットは，その前に `mise run check` が通っていること
- `results/` は，`.gitignore` で除外されていないファイル（`results.jsonl`・`metrics.json`・`analysis_report.md`・`placement.json`・`run_meta.json`）だけを入れる
- 実験の実行中（`mise run deploy` の開始から，`mise run start` の終了まで）は，commit しない（`start.sh` がコミットを照合するため）

**push**

- 宛先は，`git push origin main` である．ブランチは作らない
- 時機は，ステージの完了時である（S2・S3・S4・S5・S6・文書の更新）．ステージの中のコミットを，まとめて 1 回で push する．
  例外として，S4 の成功条件の事前登録は，評価の前に push する
- push の前に，次を確かめる
  1. `git status` に，意図しない変更が無い
  2. `git diff --stat origin/main..HEAD` が，そのステージの変更だけである
  3. 秘密情報の検査：`git diff origin/main..HEAD | grep -iE 'api[_-]?key|secret|token|passw|BEGIN [A-Z ]*PRIVATE KEY'` の該当を調べる．
     実際の秘密（トークン・鍵・パスワード・個人情報）なら push せず，§6 の 8 に従う．単語だけの偽陽性なら，`journal.md` に記録して進める
- `git push` が拒否された（他の作業者のコミットが先に `origin/main` にある）ときは，`git pull --rebase origin main` を 1 回行い，もう一度 push する．
  rebase で衝突したら，`git rebase --abort` で戻し，§6 の 7 に従う
- 認証・ネットワークの失敗は，1 回だけ再試行する．それでも失敗したら，§6 の 7 に従う
- `--force`・`--force-with-lease`・`--no-verify` は使わない．push 済みのコミットを，書き換えない（`amend`・`rebase -i`・`reset --hard`）
- `git config` を変更しない

## 6. 止まる条件

次のどれかに該当したら，作業を止め，「状況・実行したコマンド・出力の要点・考えられる原因・次の選択肢（記号付き．推奨に `(Recommended)`）」を報告して終了する．

1. s3 のラベル一致が 0.99 未満，または s4 が s3 と同じ検索結果を返さない件数が全体の 1% を超える（シャードの配布か索引に誤りがあり，以降の比較が成立しない）
2. S1 の再実行，S5，S6 の動作確認で，失敗が解消しない，または失敗率が 1% を超える
3. `ALL DONE` が出ないまま `run_full.sh` が消えた，または `progress.log` が 3 時間以上更新されない（`start.log` で，実験が動いているかを確かめてから報告する）
4. 制御点・専門家のディスクの使用率が 90% を超える，または S3 のデータ複製が 100 MB を超える
5. §5 で承認していない変更が必要になる，または `clean`・データの削除・秘密情報の扱いが必要になる
6. 同じコマンドが，原因の分析なしに 2 回失敗する（原因を分析せずに繰り返さない）
7. `git push` が，§5.2 の再試行（`pull --rebase` を 1 回，認証・ネットワークの再試行を 1 回）で解決しない，または rebase で衝突する
8. push の対象の差分に，秘密情報（トークン・鍵・パスワード・個人情報）が含まれる疑いがある
9. commit の対象のファイルが，1 つで 50 MB を超える（GitHub の 1 ファイル 100 MB の制限に近いため）

## 7. 決定事項（2026-10-07，ユーザー）

| ID | 決定 | 理由と見直しの条件 |
|---|---|---|
| A1 | E2 を先に進める．実験の実行中は，実装せずに待つ | CPU だけで進められ，実機を占有しない．実験の実行中に作業ツリーを変えると，`deploy` が作るイメージと，コミットの照合に影響する |
| B1 | MedRAG の LOSO は 4 データ源のまま行う | RAGRoute と同じラベル定義で比べられ，データの作り直しが要らない．除外の通りが 4 つしかないので，統計の力は弱い．結果の差が判断できないときは，PubMed・Wikipedia をシャード単位に分けて最大 16 個にする案がある．ラベルの定義が変わり，ラベルの再計算とルーターの再学習が要るので，この計画に含めず，報告で提案する |
| C1 | E3 のパイロットは，問題集ごとに 20 問（100 問）で始める | 失敗と所要時間を早く見つけられる．標本の追加は，S6 の基準（失敗率 0，所要時間 3 時間以内）で，250 問まで 1 回だけ |

## 8. 完了の条件

- S1〜S6 が，この順に実行されている．飛ばしたステージは，理由が `journal.md` にある（S5 は S4 の分岐による）
- 各ステージの完了条件を満たしている．満たさなかった項目は，そのまま報告されている
- `mise run check` が通り，作業ツリーに，意図しない変更が無い（`git status`）
- 実機のコンテナが止まっている．`config.yaml` が元に戻っている
- 作業前から存在した未コミットの変更を，触らず，コミットに混ぜていない
- 全てのコミットが `origin/main` にある（`git status -sb` が `ahead` を示さない）．実験の実行中に commit していない

## 9. 参照

- [d0001 構成](../docs/d0001_architecture.md)：役割の分担，`Router` の差し替え口（§5）
- [d0003 実験の手順](../docs/d0003_experiment_workflow.md)：mise タスクの手順・失敗時の確認先
- [d0004 指標](../docs/d0004_metrics.md)：結果の書式と指標の定義
- [d0005 research-cycle](../docs/d0005_research_cycle.md)：サイクルの進め方
- [d0006 研究概要](../docs/d0006_research_overview.md)：研究の目的・理論・現状
- `.claude/research/config.yml`：levers，成功条件，守ること
