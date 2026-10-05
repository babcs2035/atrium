"""検索結果の統合と，宿る型の回答の統合（いずれも純粋関数）．"""

from __future__ import annotations

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
