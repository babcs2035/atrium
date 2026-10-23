"""検索結果の統合と，宿る型の回答の統合（いずれも純粋関数）．

owner_answer（p0007 I2）の統合の規則（max_score・abstain・agree）もここに置く．llm_select は質問者の LLM を呼ぶので
atrium.requester が行い，LLM が選べなかったときの代替に max_score を使う．
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from atrium.protocol import DocOut


@dataclass(frozen=True)
class SourcedDoc:
    """どのデータ源・シャードから来たかを付けた断片．"""

    source: str
    shard_id: str
    doc: DocOut


def top_per_source(docs: Iterable[SourcedDoc], k_ret: int) -> list[SourcedDoc]:
    """データ源ごとにシャードの結果を合わせ，上位 k_ret 件に絞る．

    データ源を複数のシャードに分けても，各シャードの上位 k_ret 件の和集合からスコア順に
    k_ret 件を取れば，分けない場合の上位 k_ret 件と一致する．
    """
    by_source: dict[str, list[SourcedDoc]] = defaultdict(list)
    for d in docs:
        by_source[d.source].append(d)
    out: list[SourcedDoc] = []
    for source in sorted(by_source):
        out.extend(sorted(by_source[source], key=lambda d: d.doc.score, reverse=True)[:k_ret])
    return out


def merge_by_score(docs: Sequence[SourcedDoc], k: int) -> list[SourcedDoc]:
    """検索スコアの降順で上位 k 件を返す（同じ検索器を使うデータ源同士でのみ意味がある）．"""
    return sorted(docs, key=lambda d: d.doc.score, reverse=True)[:k]


def merge_by_qrels(
    docs: Sequence[SourcedDoc], relevance: Mapping[str, int], k: int
) -> list[SourcedDoc]:
    """結果統合用ラベルの関連度の降順で上位 k 件を返す（RAGRoute の rerank_feb4rag と同じ）．

    ラベルのない文書は後ろへ回し，同じ関連度の中では元の順序を保つ．
    """
    # ラベルのない文書は関連度 -1 とみなす（ラベル上の関連度は 0 以上）．sorted は安定ソートである
    return sorted(docs, key=lambda d: -relevance.get(d.doc.doc_id, -1))[:k]


def contributing_sources(docs: Iterable[SourcedDoc]) -> list[str]:
    """統合後の断片を 1 件以上出したデータ源（関連ラベルの定義に使う）．"""
    return sorted({d.source for d in docs})


@dataclass(frozen=True)
class NodeVote:
    """宿る型で 1 台の専門家が出した選択肢．"""

    node_id: str
    choice: str | None
    top_score: float | None


def vote(answers: Sequence[NodeVote]) -> str | None:
    """選択肢の多数決．同数なら，その選択肢を出したノードの最高検索スコアが高い方を採る．"""
    counts = Counter(a.choice for a in answers if a.choice is not None)
    if not counts:
        return None
    best_score: dict[str, float] = {}
    for a in answers:
        if a.choice is not None:
            score = a.top_score if a.top_score is not None else float("-inf")
            best_score[a.choice] = max(best_score.get(a.choice, float("-inf")), score)
    return max(counts, key=lambda c: (counts[c], best_score[c]))


@dataclass(frozen=True)
class OwnerCandidate:
    """owner_answer で 1 つの受信箱が出した回答．answer は "Answer:" の後ろだけを取り出した文．"""

    source: str
    answer: str
    top_score: float | None
    unknown: bool
    # aggregate=cross_encoder（p0007 F-6）：質問と文脈の上位 5 通の cross-encoder の関連度の最大（文脈が無ければ None）
    ce_score: float | None = None


@dataclass(frozen=True)
class OwnerChoice:
    """統合で採った候補の番号（候補の並びの 0 始まり）と，票の組．

    vote_groups は agree のときだけ，同じ回答とみなした候補の番号の組（採った組が先頭）．fell_back は abstain で全ての回答が
    UNKNOWN だったため max_score に切り替えたことを表す．
    """

    index: int
    fell_back: bool = False
    vote_groups: tuple[tuple[int, ...], ...] = ()


_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_answer(answer: str) -> str:
    """agree の一致の判定用に回答を正規化する（Unicode の正規化・小文字化・英数字以外を空白 1 つにする）．"""
    folded = unicodedata.normalize("NFKC", answer).casefold()
    return _NON_ALNUM.sub(" ", folded).strip()


def _score_of(candidate: OwnerCandidate) -> float:
    return candidate.top_score if candidate.top_score is not None else float("-inf")


def _best_index(candidates: Sequence[OwnerCandidate], indices: Sequence[int]) -> int:
    """indices のうち検索得点が最高の候補の番号（同点は候補の並びの先の方）．"""
    return max(indices, key=lambda i: (_score_of(candidates[i]), -i))


def choose_max_score(candidates: Sequence[OwnerCandidate]) -> OwnerChoice:
    """検索の最高得点の受信箱の回答を採る（既存の B4 の統合と同じ．同点は候補の並びの先の方）．"""
    if not candidates:
        raise ValueError("no candidates to choose from")
    return OwnerChoice(_best_index(candidates, range(len(candidates))))


def choose_cross_encoder(candidates: Sequence[OwnerCandidate]) -> OwnerChoice:
    """cross-encoder の得点が最高の候補を採る（得点の無い候補は最低．同点は候補の並びの先の方．オフラインの R3′ と同じ）．"""
    if not candidates:
        raise ValueError("no candidates to choose from")

    def key(i: int) -> tuple[float, int]:
        score = candidates[i].ce_score
        return (score if score is not None and math.isfinite(score) else float("-inf"), -i)

    return OwnerChoice(max(range(len(candidates)), key=key))


def choose_abstain(candidates: Sequence[OwnerCandidate]) -> OwnerChoice:
    """UNKNOWN でない回答のうち得点が最高のものを採る．全て UNKNOWN なら max_score に切り替える．"""
    answered = [i for i, c in enumerate(candidates) if not c.unknown]
    if not answered:
        return OwnerChoice(choose_max_score(candidates).index, fell_back=True)
    return OwnerChoice(_best_index(candidates, answered))


def choose_agree(candidates: Sequence[OwnerCandidate]) -> OwnerChoice:
    """正規化した回答の文字列が一致する候補の数で票を数え，最多の組から得点最高の候補を採る（同票は組の中の最高得点で決める）．

    正規化すると空になる回答（記号だけなど）は，他と一致したとみなさず 1 票ずつ別の組にする．
    """
    if not candidates:
        raise ValueError("no candidates to choose from")
    groups: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(candidates):
        groups[normalize_answer(c.answer) or f"\0empty{i}"].append(i)
    ranked = sorted(
        groups.values(),
        key=lambda g: (len(g), _score_of(candidates[_best_index(candidates, g)])),
        reverse=True,
    )
    return OwnerChoice(
        _best_index(candidates, ranked[0]), vote_groups=tuple(tuple(g) for g in ranked)
    )
