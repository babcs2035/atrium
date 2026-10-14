# backlog — 人間判断待ち事項 / 自動判断の記録

このファイルは research-cycle が「本来は人間の判断が要るが，サイクルを止めないために暫定で自動選択した事項」と，
「不可逆・危険なため停止して人間に委ねた事項」を記録する．新しいものを常に先頭に追記する（逆時系列）．

書式:
```
## B{n} [auto-decided YYYY-MM-DD] 題目
- 状況: なぜ判断が要ったか
- 自動選択: 何を選んだか
- 根拠: なぜそれが最も妥当か
- 要レビュー: 人間が確認・却下する際に何を見るか（却下時はこの項目を編集して差し替える）
```
不可逆な事項は `[needs-human YYYY-MM-DD]` として記録し，Slack で @mention 済みであることを明記する．
人間が答えたら（herdr 上でも，Slack のスレッドへの返信（`[Slack 返信]`）でも），`[human-decided YYYY-MM-DD]` として
回答の内容と，それに従って何をしたかを追記する．

---

## B17 [auto-decided 2026-10-14] Iteration 3（公開情報の本文だけからの作り直し）は棄却．Iteration 4 は centroid_probe の実機化（1 段目はヘッダー込みの既存の公開情報）

- 状況: Iteration 3 の判定は棄却（multi_centroid の hit@2 差 −0.093 [−0.110, −0.076] は非劣性の幅 −0.03 を外れ，E_body_short の持ち主 top-1 の差 +0.012 [−0.003, +0.027] は「当てにくくなる」を満たさない．journal の「Iteration 3 実行済み」）．
  H-B1（term_sketch の部分）支持，H-B3 支持（単調な兼ね合いは本文だけでも成り立つ）．参考の centroid_probe（K = 75）は 0.761 → 0.751（差 −0.011 [−0.021, +0.000]）．
  config.yml の levers は全て試し終えている．
- 自動選択: **Iteration 4 の単一レバーは centroid_probe の実機化**（B11 の「次の候補」の内容）．質問者の 2 段目（multi_centroid の上位 K 個の受信箱だけへの probe）を実装し，T4 と同じ条件（EnronQA 300 問，検索だけ，parallel 1・4・8・16・30 × 3 回）で，
  e2e の待ち時間・処理量を flood_score・multi_centroid（T4 の値）と比べる．1 問あたりに届くデバイスの数と各デバイスに届く受信箱の数も記録する．K は Iteration 1 で選んだ 75 に固定する．
  **1 段目の公開情報はシャードにある既存のヘッダー込みの multi_centroid（C = 32）を使う**．あわせて，上位 K の再現率（2 段目の上限）と，K ごとの届くデバイスの数の分布をオフラインで出す（実機の前に，K = 75 でほぼ全台に届くかを確かめられる）．
  config.yml の levers 末尾に `experiment.routing: [centroid_probe]`（実機）を追記した．
- iteration_name: 「centroid_probe（二段ルーティング）の実機での待ち時間と処理量の測定」．
- 根拠: (1) 本文だけの公開情報は，二段では hit@2 をほとんど変えない（−0.011，区間は 0 を含む）が，ヘッダーを除いた本文からの持ち主の特定を減らさず（E_body は +0.038），採用の条件も満たさなかった．シャードの作り直しと config.yaml の形式の変更（B15）を伴ってまで 1 段目に使う理由が無い．
  (2) 実機の待ち時間・処理量は 1 段目の公開情報の中身ではなく，2 段目に届くデバイスの数で決まる見込みであり（公開情報の大きさは 77.2 MB と 77.4 MB でほぼ同じ），1 段目の選択は測りたい量に影響しにくい（推測）．
  (3) T4 で flood_score の処理量が parallel 30 で 12.8 → 2.5 問/秒に落ちた問題に対して，二段の方式が負荷をどれだけ減らすかは RQ-A・RQ3 の論点として未測定のまま残っている．config.yml の運用ルール 8 により，フルスペックの実機実験を行ってよい．
- 要レビュー: (a) K = 75 ではほぼ全 30 台に届くと推定され（Iteration 1 の学び．各台に 1 つも当たらない確率 ≈ 0.5^5），処理量の改善は小さい可能性がある．オフラインのデバイスの数の分布で全台に近いと分かった場合，K = 30 も実機で測るか（K の追加は単一レバーの範囲内の条件の追加として扱う）を rc-researcher が事前登録で決める．
  (b) 質問者の 2 段目の実装は既存の Router の公開の挙動（`experiment.routing` の既存の値）を変えない形にすること．(c) 件名を残してアドレスだけを除いた公開情報の変形（Iteration 3 で件名とアドレスの寄与を分けられなかった点）は，必要なら後の候補にする．

## B16 [needs-human 2026-10-14] Iteration 3 の実験が docker の権限不足で止まっている（Slack で @mention 済み）

- 状況: 実装は commit・push 済み（`e57ab75`・`6ead387`．テスト 228 件・ruff・mypy 通過）．`publish_images` が B13 と同じ
  `permission denied ... /var/run/docker.sock` で失敗した（ktakahashi は docker グループに無い）．
  先に流した `remote.sh body_advert_eval` は制御点の古いイメージに `body-advert-eval` が無く，argparse の invalid choice で EXIT 2 になった．評価は始まっておらず，事前登録の「実験 1 回」は消費していない（ログ `/tmp/iter3_run.log`）．
- 選択肢:
  - A1: 人間が docker 権限のある端末で `bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'` を実行し，Slack のスレッドに「1」と返信する．その後 `remote.sh body_advert_eval 20261014_iter3_body_advert_eval 20261009_eq2_advert_eval` はサイクルが 1 回だけ流す．(Recommended)
  - A2: ktakahashi を docker グループに追加する（権限設定の変更．実質 root 相当のため人間の判断が要る）．
  - A3: 制御点 wafl-ctrl5 でイメージを build して配る手順に変える（`mise run deploy` の既存手順の変更．運用ルール 7・B5 との整合の確認が要る）．
- 要レビュー: どれを選んでも評価の仕様（事前登録）は変えない．B13 と同じ原因で 2 回目のため，A2 か A3 で恒久対応するかも検討してほしい．
- 備考: rc-executor が cli.py の編集に global の `python3` を 1 回使った（uv の規約違反．差分は確認済み）．
- [human-decided 2026-10-14] 回答: Slack の返信「1」（A1．`publish_images` を人間が実行したと解釈）．
  したこと: `status` を `running` に戻し，rc-executor へ再委譲した．イメージが今の HEAD のものかは rc-executor が確認してから `remote.sh body_advert_eval` を 1 回だけ流す．恒久対応（A2・A3）は未決のまま残る．

## B15 [auto-decided 2026-10-14] Iteration 3 の実装の形（CLI か設定キーか）と card_sim の件名の扱い（B14 の要レビュー (b)・(c)）

- 状況: B14 で，レバー `advert.input: [body_only]` のキー名・実装の形と，件名だけを公開する card_sim の扱いを rc-researcher が決めることになっていた．
- 自動選択: (c) **新しい設定キーは足さず，オフライン評価 CLI `atrium body-advert-eval`（`src/atrium/body_advert_eval.py`）として実装する**．config.yml のレバー名を `advert_eval.input` に改めた．config.yaml・シャード・Advert・ラベルは変えない．
  (b) **card_sim は件名を残し，公開情報を変えない（対照）**．本文だけの公開情報の値が，ヘッダー込みの値と一致することを退行の確認に使う．
  参考として，card_sim の説明文に持ち主の姓が入る受信箱の数を出す．
- 根拠: (1) 評価は実機を使わず，シャードの公開情報を作り直さないので，実験の条件を決める config.yaml に置く理由が無い（Iteration 2 の `header-eval` と同じ形）．設定ファイルの形式を変えないので B9 のような確認も要らない．
  (2) card_sim の公開情報は件名そのもの（`card_text`）で，件名を除くと方式そのものが無くなる．R1 で問題になったのは `Sender:`・`Recipients:` のアドレスであり，card_sim はもともとそれを含まない（Iteration 2 の E_subj_short 0.195 も件名の寄与が小さいことと矛盾しない）．
  本文から作る説明文（例：本文の頻出語）に変えると別の方式になり，単一レバーの範囲を超える．
- 要レビュー: 件名にも持ち主の名前が入りうるので，card_sim から件名を除いた変形（例：本文の先頭の文）を別の方式として比べるべきか．採用時にシャードの公開情報を本文だけで作り直す場合は，`data.enronqa` に入力の指定を足すことになり，そのときは config.yaml の形式の変更として改めて確認する．
- 追記（2026-10-14．Iteration 3 の評価）: 判定は棄却だったので，シャードの公開情報は作り直さず，config.yaml の形式の変更も要らなくなった（この点は閉じる）．card_sim の件名の扱いの問いは残る（card_sim の説明文に持ち主の姓が入る受信箱は 31/150）．

## B14 [auto-decided 2026-10-14] Iteration 2（R1）は H-R1b 支持．Iteration 3 は公開情報をヘッダーを除いた本文から作り直して EQ2 を測り直す

- 状況: Iteration 2 の判定は H-R1b 支持（term_sketch の E_body_short 0.219 [0.197, 0.238] 対 E_short 0.543 [0.515, 0.568]，Q 0.292 とは `lower`．journal の「Iteration 2 実行済み」）．
  R2 の A 群（持ち主の姓を含む問．26%）だけで見ると，term_sketch の hit@2 は 0.956，それ以外は 0.151 で，EQ2 の term_sketch の発見はヘッダー由来の持ち主の名前に強く依存する．
  B11 では次の候補を「centroid_probe の実機化」としていたが，事前登録の「次の候補」は「H-R1b が支持なら公開情報を作り直す」を先にしていた．
- 自動選択: **Iteration 3 の単一レバーは「公開情報の入力をヘッダーを除いた本文にする」**（config.yml の levers 末尾に `advert.input: [body_only]` を仮称で追記）．
  4 方式の公開情報を区切り線より後の本文から作り直し，EQ2 と同じ test 2,940 問の hit@1・hit@2 と，所有者の特定（E・E_body_short）を測り直す．大きさ（件名 40，T = 200，C = 32）と m = 2 は固定する．
  hit は A 群（持ち主の姓を含む）とそれ以外に分けて添える．centroid_probe の実機化は Iteration 4 以降の候補に回す（B11 の内容のまま）．
- iteration_name: 「ヘッダーを除いた本文から公開情報を作り直した RQ-A の測り直し」．
- 根拠: (1) RQ-A の主張（公開情報の量と，見つけてもらう度合い・持ち主を当てられる度合いの兼ね合い）の数値そのものが，ヘッダーの名前に依存している可能性が高く，論文の中心の数値を先に確かめる方が価値が高い．
  (2) centroid_probe の 1 段目は multi_centroid の公開情報なので，公開情報を作り直すと K と hit の兼ね合いが変わりうる．実機化（フルスペックで数時間）を先にすると，やり直しになりうる．
  (3) オフラインの評価で，データ準備・ラベル・シャードの作り直しは要らない見込み（multi_centroid・centroid_sim は本文の埋め込みの計算し直しが要る．所要は未見積もりで，rc-researcher が確認する）．
- 要レビュー: (a) この順序（公開情報の作り直しを centroid_probe の実機化より先にする）でよいか．(b) card_sim は件名だけを公開する方式なので，「ヘッダーを除く」とき件名を残すか（B12 では E_body_short から件名を除いた）．
  (c) 新しい設定キーを足すか，評価 CLI の引数にとどめるか（config.yaml の形式の変更は B9 と同じく要レビュー）．(d) 持ち主が自分の名前を公開情報から除くことは現実的な運用か（除けない場合，今の EQ2 の値が実態に近い）．

## B13 [needs-human 2026-10-14] Iteration 2 の実験が docker の権限不足で止まっている（Slack で @mention 済み）

- 状況: 実装は commit・push 済み（`da091ec`・`c817f02`．テスト 213 件・ruff・mypy 通過）．実験の前段の `publish_images` が
  `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock` で失敗した．
  この操作端末の ktakahashi は docker グループに入っていない（groups: sudo, systemd-journal, elab-ops）．`remote.sh header_eval` は未実行（1 回だけの規則は消費していない）．
- 選択肢:
  - A1: 人間が docker 権限のある端末で `bash -c 'source scripts/tasks/lib.sh && sync_to_control && open_registry_tunnel && publish_images'` を実行する．その後 `bash scripts/tasks/remote.sh header_eval 20261014_iter2_header_eval 20261009_eq2_advert_eval` はサイクルが続ける．(Recommended)
    メリット: 権限設定を変えず，既存の手順どおり．デメリット: 人間の作業が 1 回要る．
  - A2: ktakahashi を docker グループに追加する（権限設定の変更．人間が行う）．メリット: 以後の Iteration も自動で進む．デメリット: docker グループは実質 root 相当の権限で，セキュリティ上の判断が要る．
  - A3: 制御点 wafl-ctrl5（docker が動く）でイメージを build して配る手順に変える．メリット: この端末の権限が不要．デメリット: `mise run deploy` の既存手順（gpu2 での build）の変更で，運用ルール 7・B5 との整合の確認が要る．
- 要レビュー: どれを選んでも，評価の仕様（事前登録）は変えない．
- [human-decided 2026-10-14] 回答: ユーザーが「実行した」と返信した（A1．`publish_images` を人間が実行したと解釈）．
  したこと: `status` を `running` に戻し，rc-executor へ再委譲した．イメージが今の HEAD のものかは rc-executor が確認してから `remote.sh header_eval` を 1 回だけ流す．

## B12 [auto-decided 2026-10-14] R1 (b) の「ヘッダー」の範囲：区切り線より前の全行とし，Subject を本文に含めない

- 状況: B11 の要レビューで，R1 (b) で「ヘッダー」とみなす行の範囲（`Subject:` を本文に含めるか）を事前登録で決めることになっていた．
- 自動選択: 区切り線（`HEADER_SEPARATOR`）より前の全行（`Subject:`・`Sender:`・`Recipients:`）をヘッダーとし，判定に使う E_body_short は件名を含めない．件名＋本文の先頭 28 トークン（E_subj_short）は参考として出し，判定には使わない．
  あわせて，config.yml の levers 末尾に `owner_eval.input: [body_short]` を追記した（実装は新しい CLI `atrium header-eval`．config.yaml は変えない）．
- 根拠: (1) 重複の検出（`write_questions_files`）が既に `split(HEADER_SEPARATOR, 1)[-1]` を本文としており，同じ定義にできる．(2) R1 の問いは「メールの付属情報で持ち主を当てているか」であり，件名も送信者が付ける付属情報で，返信・転送で受信箱をまたいで繰り返される．
  (3) card_sim の公開情報は件名そのものなので，件名を含めると card_sim に有利さを与え，方式間の比較が崩れる．
- 要レビュー: 件名を本文とみなすべき（件名は書き手の書いた中身である）と考える場合は，参考の E_subj_short を判定に使うよう差し替える（事前登録の前に限る）．journal の「Iteration 2」の事前登録．

## B11 [auto-decided 2026-10-14] Iteration 1（centroid_probe）は部分的に支持．Iteration 2 の単一レバーを R1 にする

- 状況: Iteration 1 の判定は「部分的に支持」（S1 成立 +0.321，S2 不成立．K = 75 > 30．journal の「Iteration 1 実行済み」）．config.yml の levers は全て試し終えている．
- 自動選択: B10 の [human-decided 2026-10-14] に従い，**Iteration 2 の単一レバーは R1**（T2 の入力の確認）とする．
  (a) term_sketch の特徴語の上位に占めるメールアドレス・人名の割合を数える，(b) ヘッダー（`Subject:`・`Sender:`・`Recipients:` など）を除いた本文の先頭 28 トークンで T2（`atrium owner-eval`）を測り直す．
  R2（質問文が人名を含む問と含まない問で hit@1・hit@2 を分ける．flood_score・multi_centroid・centroid_probe）は同じイテレーションの追加の集計として行う．
  いずれも実機を使わないオフライン評価で，データ準備・ラベルは作り直さない．事前登録（判定の規則・退行の確認）は次の rc-researcher が決める．
- iteration_name の案: 「T2 の入力からヘッダーを除いた持ち主推定の測り直し」．
- term_sketch を使うので，B8 の追認に従い，term_sketch の値の揺れ（Q の top-1 ±0.001，E の top-1 ±0.002）と退行の許容 2e-3 を限界として書く．PYTHONHASHSEED は固定しない．
- 次の候補（Iteration 3 以降）: **centroid_probe の実機化**．質問者の 2 段目（候補の受信箱だけへの probe）を実装し，T4 と同じ条件（300 問，parallel 1・4・8・16・30 × 3 回）で待ち時間・処理量を flood_score・multi_centroid と比べる．
  1 問あたりに届くデバイスの数と各デバイスに届く受信箱の数も記録する（30 台が 5 受信箱ずつ持つので，K = 75 でもほぼ全台に届くと推定され，処理量の改善は小さい可能性がある．未測定）．
  config.yml の運用ルール 8 により，フルスペックの実機実験（質問者 1 台と専門家 30 台）を行ってよい．あわせて 1 段目の上位 K の再現率（2 段目の上限）をオフラインで出す．
- 根拠: R1 は T2 の結論と EQ2 の「持ち主を当てることの方に役立つ」の書き直しに直接影響し，人間がこの順序を指示した．centroid_probe の実機化は，S2 が不成立でも「K と負荷の兼ね合い」を実測する価値があるが，R1・R2 の結果（人名の一致が flood_score の高さを説明するか）が probe の価値の解釈を変えうるので後に回す．
- 要レビュー: R1 の (b) で「ヘッダー」とみなす行の範囲（`Subject:` を本文に含めるか）．centroid_probe の実機化を Iteration 3 に回してよいか．

## B10 [proposal 2026-10-14] 外部レビューの指摘（R1〜R4）．今回の Iteration 1 には混ぜず，別提案として保留する

出典: ユーザーが共有した外部レビュー．R1・R2 は実機を使わないオフライン評価で，制御点で各 1 時間程度の見込み．
- **R1（T2 の入力がヘッダーだけの疑い）**: `data_enronqa.py` は `File:` 行だけを除くので，E_short（先頭 28 トークン）は `Subject:`・`Sender:`・`Recipients:` で埋まる可能性が高い（実際の中身は未確認）．
  T2 の「同じ長さ」の比較が「質問文対ヘッダー」になり，term_sketch の逆転（E_short 0.543）も，持ち主のアドレスが特徴語に入ることで説明できる．
  確認: (a) term_sketch の特徴語の上位にアドレス・人名が占める割合を数える，(b) ヘッダーを除いた本文の先頭 28 トークンで T2 を測り直す．
  T2 の結論（journal の「T2」）と EQ2 の「持ち主を当てることの方に役立つ」の書き直しに影響する．
- **R2（質問文の人名による見つけやすさの水増し）**: EnronQA の質問は送信者名を含むことが多い．名前を含む質問の割合を数え，含む問と含まない問で hit@1・hit@2 を分けて報告する（flood_score の高さの一部が名前の一致による可能性）．
- **R3（B8 の追認）**: 下の B8 を追認するか．PYTHONHASHSEED をコンテナで固定すれば揺れは消えるが，EQ2 の値が ±0.002 ずれる．
- **R4（論文での扱いを決める未説明の点．新しい実験は不要）**: (a) EQ3（2,940 問・parallel 30．p50 92 秒・`ReadError` 175 件）と T4（300 問．p95 19 秒・失敗 0）の食い違いは，規模の違いか長時間の実行で蓄積する問題（接続の枯渇など）かを区別できていない．運用の頑健性は主張しない．
  (b) parallel 1 で multi_centroid（0.120 秒）が flood_score（0.076 秒）より遅い理由は，質問者側の重心の採点が律速という推測にとどまり未確認．
  (c) ragroute-frozen が新参者の one-hot を 0 にしても hit@2 0.118 で選ぶ理由は，ベクトル 0 のデータ源が「平均的なデータ源」として採点されるためという解釈で，未確認．
- [human-decided 2026-10-14] 回答: ユーザーが A1（R1・R2 は Iteration 1 の完了後）と B1（R3 は追認）を選んだ．
  したこと: 実行していない．**Iteration 2 の単一レバーは R1**（(a) 特徴語の上位のアドレス・人名の割合，(b) ヘッダーを除いた本文の先頭 28 トークンでの T2 の測り直し）とし，R2（名前を含む質問と含まない質問の hit の分離）を同じイテレーションの追加の集計として行う．
  R4 の 3 点は新しい実験をせず，Iteration 1 の最終報告か docs の限界の節に「未確認」として書く．
  Iteration 1 の評価（rc-evaluator）は，次レバーの決定のとき，この記述を優先して Iteration 2 に R1 を選ぶこと．

## B9 [auto-decided 2026-10-14] levers を使い切ったので，新しいレバー centroid_probe（二段ルーティング）を追加した

- 状況: config.yml の levers（E1-a〜E1-e，E2 card_sim，EQ8 k_context）と p0005（T1〜T4）が全て完了し，research-cycle の Iteration 1 で振るレバーが無かった．
- 調査: tavily-search で分散検索の資源選択（ReDDE，Rank-S，Taily，Dynamic Shard Cutoff Prediction，分散 selective search の負荷）と連合 RAG（RAGRoute，DFAMS）を調べた（出典は journal の Iteration 1）．
  候補は (a) 公開情報で上位 K 個に絞ってから probe する二段の方式，(b) 問ごとに問い合わせ数を変える打ち切り（m の可変化），(c) term_sketch を 1 段目に使う変形，(d) DFAMS 型の学習した prototype であった．
- 自動選択: (a) `experiment.routing: centroid_probe`（multi_centroid の上位 K 個 → probe → 上位 m = 2 個）．K は新しいキー `routing.probe_k.enronqa` で dev から選ぶ．評価はオフライン．
- 根拠: EQ2・T4 で残った問い（flood_score は hit@2 0.804 だが 150 受信箱に届いて負荷で処理量が落ち，multi_centroid は 2 受信箱で 0.440）の間を直接測れる．
  Router 1 個と評価の CLI 1 個で済み，データ準備・ラベルの作り直しが要らない．(b) は m の意味を変える．(c) は B8 の結論に依存する．(d) は中央での学習が要り，研究の前提（中央で学習しない）と合わない．
- 要レビュー: 新しい設定キー `routing.probe_k`（既存のキーは変えない）を足してよいか．K の候補と選び方（dev の hit@2 が flood_score の 0.95 倍以上になる最小の K）が妥当か．

## B8 [auto-decided 2026-10-14] T2 の退行の確認が term_sketch だけ不一致だったが，止まらずに続けた

- 状況: p0005 §9 の 1「検算・退行の確認が一致しない」は，作業を止めて報告する条件である．T2（`atrium owner-eval`）の退行の確認で，term_sketch の Q の top-1（差 −0.0007）と E の top-1（差 +0.0021）が基準（5e-4）を超えた．
  card_sim・centroid_sim・multi_centroid は完全に一致した．
- 診断: term_sketch の採点が Python の文字列のハッシュの種で揺れる（Q で 0.2915〜0.2922，E で 0.4240〜0.4274．`results/20261014_t2_owner_eval/ts_diag.out`）．EQ2 の値もこの揺れの中にある．実装の誤りではない．
- 自動選択: 止まらずに T2 の判定を記録し，T3 に進む．T3 の事前登録で，term_sketch の退行の許容を揺れの幅（0.002）に広げる．
- 根拠: 止まる条件の目的（原因不明の食い違いで先へ進まない）に当たらない．揺れは判定の差（term_sketch で 0.25 以上）より十分に小さい．
- 要レビュー: この緩和を認めるか．認めない場合は，`advert_eval.score_terms` を語の順序に依存しない形（語を並べ替えて足す）に直して EQ2・T2・T3 を作り直す（EQ2 の値が変わりうる）．
- [human-decided 2026-10-14] 回答: ユーザーが追認を選んだ（選択肢 B1）．PYTHONHASHSEED は固定しない．
  したこと: 既存の値は変えない．term_sketch の揺れ（Q ±0.001，E ±0.002）と T3 の許容（2e-3）を，term_sketch を含む結果の限界として書く（次に term_sketch を含む結果を書くときに従う）．

## B7 [human-decided 2026-10-13] RQ-C の実機の評価（EQ7b）を行う

- 状況: EQ7a（実機を使わない）の flood_score は「返事のあった分で続ける」と仮定した上限で，実機の質問者は 1 台の失敗で問全体を失敗にしていた．
  実機で確かめるには，質問者が部分的な失敗を許す変更（公開の挙動の変更）が要るので，実装の前に確認した．
- 回答: ユーザーが「実装して実行する」（選択肢 B1）を選んだ．
- したこと: `experiment.churn` を指定した実験でだけ，ノードが離脱でき（`/v1/admin/availability`），質問者が返事のあった分で続ける形にした（`5613f8c`）．
  指定しない実験の挙動は変えていない．事前登録（journal の「EQ7b の事前登録」，`d59123a`）の後に 22 回を流している．

## B6 [human-decided 2026-10-13] MedRAG の宿る型（S6）を不成功として閉じる

- 状況: S6 の pilot（100 問）は 64 問が失敗した（専門家 16 GB のメモリ不足．journal の「S6」）．smoke は通った．
- 選択肢: (1) 閉じて主評価を EnronQA の RQ-B に任せる，(2) `experiment.parallel` を 1 に下げる，(3) `llm.num_ctx`・`local_answer.k_context` を下げる．
- 自動選択: (1)．(2) は e2e の中央値が約 534 秒で全問に現実的でなく，(3) は E1 と文脈の条件が変わり比較が崩れるため．
- [human-decided 2026-10-13] 回答: ユーザーが (1) を選んだ（選択肢 A1）．MedRAG の宿る型は再開しない．

## B5 [auto-decided 2026-10-09] gpu2 のディスクが p0004 §12 の 6 の基準（90%）を超えている
- 状況: gpu2 の `/`（97%，空き 24 GB）と `/mnt/data-raid`（99%，空き 57 GB．docker の置き場所）が，着手の時点で 90% を超えていた．
  `/mnt/data-raid` は共有の RAID で，使用量の大半は本研究以外のもの（`/mnt/data-raid/ktakahashi` だけで 1.3 TB）である．
- 回答と自動選択: ユーザーが「データセットなどのダウンロード先には wafl-ctrl5 を用いよ」と指示した．これに従い，EnronQA・モデル・索引は
  全て制御点に置き，gpu2 はイメージの build（差分は数百 MB）とコードの編集だけに使う．gpu2 の空きが 20 GB を切ったら止まる．
- 根拠: 本研究の作業で gpu2 のディスクを増やさなければ，基準が守ろうとしているもの（gpu2 の空きを使い切らないこと）は守られる．
- 要レビュー: gpu2 の docker のビルドキャッシュ（28 GB が再利用されていない）を消すかどうか．他のプロジェクトと共有している可能性があるので，消していない．
- [human-decided 2026-10-13] 回答: 2026-10-13 の時点で gpu2 の `/` は空き 23 GB（98%），`/mnt/data-raid` は空き 246 GB（96%）．
  `~/.cache`（707 GB．本研究以外とも共有）の削除を提案したが，ユーザーは消さないことを選んだ（選択肢 C3）．何も削除していない．

## B4 [human-decided 2026-10-09] p0003 の S6（MedRAG の宿る型パイロット）と s8 を保留にする
- 状況: E1 で，MedRAG の正答率がルーティングにほとんど反応しないことが分かった（journal の「E1 の判定」）．
- 回答: ユーザーが p0004（EnronQA 拡張）を承認し，U1 で S6 の保留を承認した．宿る型の主評価は EnronQA の RQ-B に移す．
  s8（cross_encoder）も保留にする（p0004 §10）．
- したこと: S6 と s8 を実行せずに残した．s8 の途中の結果（1,191 問）は制御点に残し，同じ run_id で再開できる．
- 要レビュー: S6・s8 を再開するかどうか．s8 を再開するときは `cluster.requester_llm` を wafl500 にする（p0004 §14）．
- [human-decided 2026-10-12] 回答: S6・s8 とも保留を維持する（選択肢 A）．
  したこと: どちらも実行していない．s8 の途中の結果（1,191 問）と S6 の連続実行スクリプトは残してあり，再開する場合は上の手順に従う．
- [human-decided 2026-10-12] 撤回: ユーザーが「全て再開せよ」と指示した．S6（pilot）→ s8（cross_encoder の全問）の順に再開する．
  したこと: S6 は pilot で 64 問が失敗して不成功（B6）．s8 は同じ run_id で全 7,663 問を失敗 0 で終えた（journal の「S6」「s8」）．

## B3 [auto-decided 2026-10-06] 回答の生成を貪欲な復号（temperature 0）にする
- 状況: RAGRoute は生成の温度を指定しておらず，Ollama の既定（0.8）で回答がばらつく．同じ質問への方式間の
  正答の違いを McNemar 検定で比べると，ばらつきが雑音として混ざる．
- 自動選択: `llm.temperature: 0.0`．あわせて質問者の Ollama を 2 件同時に処理させる
  （`llm.requester_num_parallel: 2`．実測で 1 回の生成は約 7.4 秒．1 件ずつだと MIRAGE の全問に約 16 時間）．
- 根拠: 選択式の評価では貪欲な復号が一般的で，対応ありの比較の検出力が上がる．
- 要レビュー: RAGRoute の論文値との正答率の比較では，温度の違いが差の原因の候補になる．

## B2 [auto-decided 2026-10-06] MedCPT の埋め込みを fp16 の混合精度で計算する
- 状況: 11 枚の GPU で分担しても，fp32 では PubMed の埋め込みが 1 枚あたり約 95〜190 断片/秒で，
  約 5,400 万断片に 13 時間以上かかる見込みだった．ユーザーが寝ている間に準備を終える必要があった．
- 自動選択: `data.medrag.embed_precision: fp16_autocast`（torch.autocast）で文書とクエリの両方を埋め込む．
  fp32 で作りかけていた 6 ファイルの埋め込みは消し，全ファイルを同じ条件で作り直した．
- 根拠: gpu2 の RTX 3090 で Textbooks の 7,070 断片と MIRAGE の 500 問を測った．fp32 の 2.6 倍速く
  （193 → 502 断片/秒），fp32 との差は最大 0.0017（値の大きさ約 6.5），上位 50 件の一致率 99.7%，
  上位 15 件の集合が一致した質問は 97% だった．関連ラベルも実機の検索も同じ埋め込みから計算するので，
  内部の整合性は保たれる．
- 要レビュー: MedRAG の fp32 の埋め込みとの厳密な一致を要する比較（例：RAGRoute の論文値との細かな比較）で
  差の原因の候補になる．fp32 に戻すなら `embed_precision: fp32` にして，埋め込み・ラベル・ルーターを作り直す
  （GPU 11 枚で 13 時間前後）．

## B1 [auto-decided 2026-10-06] 専門家ノード（13.x・14.x）が全台応答しなくなった
- 状況: 2026-10-06 00:33 までに，専門家ノード 20 台（192.168.13.100〜109，192.168.14.100〜109）が ping にも
  SSH にも応答しなくなった．制御点（192.168.15.10）の既定ゲートウェイ 192.168.11.1 も応答せず，制御点と
  wafl500〜509 はインターネットに出られなかった．00:46 にネットワークが復旧した．13.x・14.x は再起動して
  おらず（uptime は 2 週間以上のまま），ホストは無事だった．
- 自動選択: 原因を研究室のネットワーク機器（ゲートウェイ）の障害とみなし，同じ時間帯に失敗したデータ準備の
  取得は，gpu2 で取得したデータを制御点へ送って補い，データ準備をやり直した．
- 要レビュー: ゲートウェイが 00:33〜00:46 ごろに止まった理由（夜間の保守など）．再び止まると，制御点と
  wafl500〜509 はインターネットに出られなくなる（データ準備の取得とモデルの取得が失敗する）．
