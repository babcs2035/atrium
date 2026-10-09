"""EnronQA の V1〜V5 の検証（p0004 §7.2）．制御点のコンテナで実行し，集計値だけを JSON で出す（本文は出さない）．"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter, defaultdict

import numpy as np
import pyarrow.parquet as pq

RAW = "/data/raw/data"
JACCARD_DUP = 0.9
MINHASH_PERMS = 128
LSH_BANDS = 32
SEED = 12
TOKEN = re.compile(r"[a-z0-9]+")


def load(split: str) -> list[dict]:
    """分割の全行を読む（本文は重複検査にだけ使う）．"""
    rows: list[dict] = []
    for f in sorted(glob.glob(f"{RAW}/{split}-*.parquet")):
        rows.extend(pq.read_table(f).to_pylist())
    return rows


def body(email: str) -> str:
    """ヘッダ（Subject・Sender・Recipients・File）を除いた本文．File の行は受信箱ごとに違うので除く．"""
    parts = email.split("=====================================", 1)
    return parts[1] if len(parts) == 2 else email


def tokens(text: str) -> set[str]:
    return set(TOKEN.findall(text.lower()))


def main() -> None:
    out: dict = {}
    splits = {s: load(s) for s in ("train", "dev", "test")}
    # ── V1：分割の定義
    paths = {s: [r["path"] for r in rows] for s, rows in splits.items()}
    v1: dict = {}
    for s, rows in splits.items():
        q = sum(len(r["questions"]) for r in rows)
        inc = Counter(v for r in rows for v in r["include_email"])
        v1[s] = {
            "rows": len(rows),
            "unique_paths": len(set(paths[s])),
            "questions": q,
            "rows_with_questions": sum(1 for r in rows if r["questions"]),
            "questions_count_matches_len": all(r["questions_count"] == len(r["questions"]) for r in rows),
            "include_email_values": dict(inc),
            "list_lengths_consistent": all(
                len(r[k]) == len(r["questions"])
                for r in rows
                for k in ("rephrased_questions", "gold_answers", "alternate_answers", "incorrect_answers", "include_email")
            ),
        }
    v1["same_paths_in_all_splits"] = paths["train"] == paths["dev"] == paths["test"]
    v1["same_path_sets"] = set(paths["train"]) == set(paths["dev"]) == set(paths["test"])
    test_q = {(r["path"], q) for r in splits["test"] for q in r["questions"]}
    train_q = {(r["path"], q) for r in splits["train"] for q in r["questions"]}
    dev_q = {(r["path"], q) for r in splits["dev"] for q in r["questions"]}
    v1["question_overlap_test_train"] = len(test_q & train_q)
    v1["question_overlap_test_dev"] = len(test_q & dev_q)
    v1["user_matches_path_prefix"] = all(r["path"].split("/")[0] == r["user"] for r in splits["test"])
    out["V1"] = v1

    # ── V4：受信箱ごとのテスト質問の数
    test = splits["test"]
    per_user_q = Counter()
    per_user_mail = Counter()
    for r in test:
        per_user_q[r["user"]] += len(r["questions"])
        per_user_mail[r["user"]] += 1
    qs = np.array(sorted(per_user_q.values()))
    ms = np.array(sorted(per_user_mail.values()))
    out["V4"] = {
        "n_users": len(per_user_mail),
        "users_with_test_questions": int((qs > 0).sum()),
        "users_with_lt20_test_questions": int(sum(1 for u in per_user_mail if per_user_q[u] < 20)),
        "test_questions_per_user": {"min": int(qs.min()), "median": float(np.median(qs)), "max": int(qs.max())},
        "emails_per_user": {"min": int(ms.min()), "median": float(np.median(ms)), "mean": float(ms.mean()), "max": int(ms.max())},
        "sample_size_max20": int(sum(min(20, per_user_q[u]) for u in per_user_mail)),
    }

    # ── V5：別解と誤答の数と形式
    alt = [len(a) for r in test for a in r["alternate_answers"]]
    inc = [len(a) for r in test for a in r["incorrect_answers"]]
    out["V5"] = {
        "alternate_per_question": dict(Counter(alt)),
        "incorrect_per_question": dict(Counter(inc)),
        "questions_with_ge1_alternate": sum(1 for x in alt if x >= 1),
        "questions_with_ge2_incorrect": sum(1 for x in inc if x >= 2),
    }

    # ── V3：受信箱をまたぐ重複（Jaccard ≥ 0.9．MinHash の LSH で候補を出し，正確な Jaccard で確かめる）
    toks = [tokens(body(r["email"])) for r in test]
    rng = np.random.default_rng(SEED)
    prime = (1 << 61) - 1
    a = rng.integers(1, prime, MINHASH_PERMS, dtype=np.uint64)
    b = rng.integers(0, prime, MINHASH_PERMS, dtype=np.uint64)
    vocab: dict[str, int] = {}
    sigs = np.full((len(toks), MINHASH_PERMS), np.iinfo(np.uint64).max, dtype=np.uint64)
    for i, ts in enumerate(toks):
        if not ts:
            continue
        ids = np.array([vocab.setdefault(t, len(vocab)) for t in ts], dtype=np.uint64)
        h = (np.outer(ids, a) + b) % np.uint64(prime)
        sigs[i] = h.min(axis=0)
    rows_per_band = MINHASH_PERMS // LSH_BANDS
    users = [r["user"] for r in test]
    dup = np.zeros(len(toks), dtype=bool)
    checked = 0
    for band in range(LSH_BANDS):
        buckets: dict[bytes, list[int]] = defaultdict(list)
        part = sigs[:, band * rows_per_band : (band + 1) * rows_per_band]
        for i in range(len(toks)):
            if toks[i]:
                buckets[part[i].tobytes()].append(i)
        for members in buckets.values():
            if len(members) < 2 or len({users[m] for m in members}) < 2:
                continue
            for x in members:
                if dup[x]:
                    continue
                for y in members:
                    if users[y] == users[x]:
                        continue
                    checked += 1
                    inter = len(toks[x] & toks[y])
                    if inter / max(1, len(toks[x] | toks[y])) >= JACCARD_DUP:
                        dup[x] = True
                        break
    q_total = sum(len(r["questions"]) for r in test)
    q_dup = sum(len(r["questions"]) for r, d in zip(test, dup) if d)
    out["V3"] = {
        "jaccard_threshold": JACCARD_DUP,
        "test_emails": len(test),
        "test_emails_with_cross_inbox_duplicate": int(dup.sum()),
        "email_rate": float(dup.mean()),
        "test_questions_on_duplicated_emails": q_dup,
        "question_rate": q_dup / q_total,
        "pairs_checked": checked,
    }
    # 除外の対象（事前登録の除外規則で使う．メールの path だけ）
    json.dump(sorted(r["path"] for r, d in zip(test, dup) if d), open("/out/v3_duplicate_paths.json", "w"))
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
