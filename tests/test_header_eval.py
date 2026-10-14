"""T2 の入力からヘッダーを除いた測り直し（atrium.header_eval）の仕様：本文の切り出し，アドレスの語の抽出，R2 の群の分け方，判定の規則．"""

from __future__ import annotations

import numpy as np

from atrium.data_enronqa import HEADER_SEPARATOR, clean_email
from atrium.header_eval import (
    address_words,
    classify_question,
    group_hit_stats,
    judge_header_effect,
    named_easier,
    owner_surname,
    split_header_body,
    subject_value,
    weighted_total,
)

RAW = (
    "Subject: FW: Ameren\\n"
    "Sender: stephanie.panus@enron.com\\n"
    "Recipients: ['a.smith@enron.com', 'bob.jones@example.org']\\n"
    "File: phanis-s/sent_items/4.\\n"
    f"{HEADER_SEPARATOR}\\n"
    "Please forward this message."
).replace("\\n", "\n")


def test_body_starts_after_the_separator_and_excludes_the_subject() -> None:
    _, text = clean_email(RAW)
    header, body, has_separator = split_header_body(text)
    assert has_separator
    assert "Subject: FW: Ameren" in header
    assert body.strip() == "Please forward this message."
    assert "Ameren" not in body


def test_email_without_separator_is_entirely_the_body() -> None:
    header, body, has_separator = split_header_body("just a body")
    assert (header, body, has_separator) == ("", "just a body", False)


def test_empty_body_after_the_separator_is_blank() -> None:
    _, body, _ = split_header_body(f"Subject: x\n{HEADER_SEPARATOR}\n")
    assert body.strip() == ""


def test_subject_value_is_the_text_after_the_subject_prefix() -> None:
    header, _, _ = split_header_body(clean_email(RAW)[1])
    assert subject_value(header) == "FW: Ameren"
    assert subject_value("Sender: a@b.com") == ""


def test_address_words_split_domain_from_name_and_drop_short_or_function_words() -> None:
    domains, names = address_words(
        [clean_email(RAW)[1], "Sender: to.me@x.net\n" + HEADER_SEPARATOR]
    )
    assert {"enron", "com", "example", "org", "x", "net"} <= domains
    assert {"stephanie", "panus", "smith", "bob", "jones"} <= names
    assert "to" not in names and "me" not in names
    assert "enron" not in names


def test_addresses_in_the_body_are_not_collected() -> None:
    domains, names = address_words([f"Subject: s\n{HEADER_SEPARATOR}\nmail carol.king@evil.com"])
    assert domains == set() and names == set()


def test_owner_surname_is_the_part_before_the_first_hyphen_in_lower_case() -> None:
    assert owner_surname("Lay-k") == "lay"


def test_question_naming_the_owner_surname_as_a_word_is_group_a() -> None:
    assert classify_question("Did Panus approve it?", "panus", {"smith"}) == ("A", [])
    # 姓が語の一部にしか現れない場合は A ではない
    assert classify_question("Was it panuscorp?", "panus", set())[0] == "C"


def test_question_with_a_capitalized_address_name_is_group_b_and_returns_the_words() -> None:
    label, words = classify_question(
        "Which deal did Smith and Jones discuss?", "lay", {"smith", "jones"}
    )
    assert label == "B"
    assert words == ["smith", "jones"]


def test_lower_case_name_is_not_matched_and_question_without_names_is_group_c() -> None:
    assert classify_question("which smith deal was it?", "lay", {"smith"}) == ("C", [])


def test_short_surname_never_makes_group_a() -> None:
    assert classify_question("Did Li approve it?", "li", set())[0] == "C"


def test_group_stats_weighted_total_equals_the_overall_hit() -> None:
    ranks = np.array([1, 2, 3, 1, 5, 1], dtype=np.int64)
    groups = ["A", "A", "B", "C", "C", "C"]
    stats = group_hit_stats(ranks, groups, 2, seed=12)
    assert [stats[g]["n"] for g in "ABC"] == [2, 1, 3]
    assert stats["A+B"]["n"] == 3
    assert abs(weighted_total(stats, "hit@2") - np.mean(ranks <= 2)) < 1e-12
    assert abs(weighted_total(stats, "hit@1") - np.mean(ranks <= 1)) < 1e-12


def test_empty_group_reports_only_its_size_and_named_easier_is_false() -> None:
    stats = group_hit_stats(np.array([1, 1], dtype=np.int64), ["C", "C"], 2, seed=12)
    assert stats["A"] == {"n": 0}
    assert named_easier(stats, 2) is False


def test_named_easier_requires_the_intervals_to_be_separated() -> None:
    stats = {
        "A+B": {"n": 10, "hit@2_ci95": (0.80, 0.90)},
        "C": {"n": 10, "hit@2_ci95": (0.60, 0.79)},
    }
    assert named_easier(stats, 2) is True
    stats["C"]["hit@2_ci95"] = (0.60, 0.85)
    assert named_easier(stats, 2) is False


def test_h_r1b_is_supported_only_when_body_is_below_short_and_not_higher_than_q() -> None:
    short = {"top1_ci95": (0.50, 0.60)}
    q = {"top1_ci95": (0.27, 0.31)}
    assert judge_header_effect(short, {"top1_ci95": (0.26, 0.33)}, q) == {
        "h_r1b": "supported",
        "h_o1_body_short_vs_q": "overlap",
    }
    assert judge_header_effect(short, {"top1_ci95": (0.35, 0.45)}, q) == {
        "h_r1b": "partially_supported",
        "h_o1_body_short_vs_q": "higher",
    }
    assert judge_header_effect(short, {"top1_ci95": (0.45, 0.55)}, q)["h_r1b"] == "rejected"
    assert judge_header_effect(short, {"top1_ci95": (0.61, 0.70)}, q)["h_r1b"] == "above"
