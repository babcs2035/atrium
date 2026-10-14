"""持ち主推定の定義の統一（atrium.owner_eval）の仕様：同じ採点の関数で入力だけを変えること，トークン数での切り詰め．"""

from __future__ import annotations

import numpy as np

from atrium.advert_eval import hit_at, ranks_of_gold
from atrium.owner_eval import (
    compare_inputs,
    median_token_count,
    owner_metrics,
    truncate_to_tokens,
)


class WordTokenizer:
    """単語を 1 トークンとみなす偽物（トークン ID は単語の出現順の番号）．"""

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._words: list[str] = []

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        ids = []
        for w in text.split():
            if w not in self._vocab:
                self._vocab[w] = len(self._words)
                self._words.append(w)
            ids.append(self._vocab[w])
        return ids

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        return " ".join(self._words[i] for i in ids)


def test_owner_top1_is_the_same_number_as_hit_at_one_of_the_ranks() -> None:
    scores = np.array([[0.9, 0.5, 0.1], [0.2, 0.3, 0.8], [0.4, 0.6, 0.5]], np.float32)
    gold = [0, 1, 2]
    metrics = owner_metrics(scores, gold, seed=12)
    # 質問文を入力にしたときの top-1 は，EQ2 の hit@1（正解の受信箱が 1 位に入る割合）と同じ値になる
    assert metrics["top1"] == hit_at(ranks_of_gold(scores, gold), 1)
    assert metrics["n"] == 3


def test_truncation_keeps_the_first_tokens_and_leaves_short_texts_alone() -> None:
    tok = WordTokenizer()
    out = truncate_to_tokens(["a b c d e", "x y"], tok, 3)
    assert out == ["a b c", "x y"]


def test_median_token_count_rounds_up() -> None:
    tok = WordTokenizer()
    assert median_token_count(["a", "a b", "a b c", "a b c d"], tok) == 3


def test_h_o1_is_higher_only_when_the_intervals_do_not_overlap() -> None:
    q = {"top1_ci95": (0.30, 0.40)}
    assert compare_inputs(q, {"top1_ci95": (0.41, 0.50)}) == "higher"
    assert compare_inputs(q, {"top1_ci95": (0.35, 0.45)}) == "overlap"
    assert compare_inputs(q, {"top1_ci95": (0.10, 0.29)}) == "lower"
