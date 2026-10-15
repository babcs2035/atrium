# Iteration 13 の事前の測定：py-spy の raw（積み重ね 1 行 + 数）を，質問者の 1 要求の CPU の項目ごとの割合にまとめる．
# 使い方：python analyze_spy.py <x.spy.txt> ...
# 発見（discover_shards）・CPU の読み取り（/healthz）・起動の標本は除き，問の処理の区間（run_questions の下と，その間のイベントループ）だけを数える．
import sys
from collections import Counter

# 項目は上から順に当てはめ，最初に当たった項目に数える（排他）．標本の積み重ねのどこかにその印があれば当たり
BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("httpcore 接続の池の割り当て（_assign_requests_to_connections）", ("_assign_requests_to_connections",)),
    ("logging（httpx の INFO の 1 要求 1 行など）", ("logging/__init__.py",)),
    ("h11（HTTP/1.1 の組み立てと解析）", ("h11/",)),
    ("pydantic の検証（model_validate_json・model_dump 等）", ("pydantic", "model_validate_json")),
    ("JSON の符号化（json/encoder）", ("json/encoder.py", "encode_json_like_httpx")),
    ("JSON の復号（json/decoder）", ("json/decoder.py",)),
    ("anyio（ソケットの読み書き・属性）", ("anyio/",)),
    ("routing（atrium/routing）", ("atrium/routing/",)),
    ("httpx の要求・応答の組み立て（_client・_models・_urls 等）", ("httpx/",)),
    ("httpcore のその他（接続・同期の部品）", ("httpcore/",)),
    ("asyncio.to_thread の受け渡し（主スレッド側）", ("to_thread", "run_in_executor", "concurrent/futures")),
    ("atrium のその他（記録・統合・JSON 行の書き出し）", ("atrium/",)),
    ("asyncio のイベントループ（選択・タスクの切り替え）", ("asyncio/",)),
]
EXCLUDE = ("discover_shards", "read_node_cpu_seconds", "fetch (atrium/requester.py", "build_requester")


def classify(stack: str) -> str:
    """1 標本の積み重ねを項目に割り当てる．"""
    for name, marks in BUCKETS:
        if any(m in stack for m in marks):
            return name
    return "その他"


def main() -> None:
    """ファイルごとに項目の割合を出す．"""
    for path in sys.argv[1:]:
        main_thread: Counter[str] = Counter()
        threads = 0
        leaf: Counter[str] = Counter()
        total = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                stack, _, n = line.rstrip("\n").rpartition(" ")
                count = int(n)
                if any(e in stack for e in EXCLUDE):
                    continue
                if "run_until_complete" not in stack:
                    # 主スレッドのイベントループの外：to_thread の作業スレッド（埋め込みの取り出し・統合）と起動
                    if "_worker (concurrent/futures/thread.py" in stack:
                        threads += count
                    continue
                main_thread[classify(stack)] += count
                leaf[stack.split(";")[-1]] += count
                total += count
        print(f"== {path}  主スレッドの標本 {total}，作業スレッドの標本 {threads}")
        for name, c in main_thread.most_common():
            print(f"  {100 * c / total:5.1f}%  {name}")
        print("  -- 葉の関数の上位 15")
        for name, c in leaf.most_common(15):
            print(f"  {100 * c / total:5.1f}%  {name}")


if __name__ == "__main__":
    main()
