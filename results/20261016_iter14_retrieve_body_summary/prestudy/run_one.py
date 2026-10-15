# Iteration 14 の事前の測定：本物の atrium run（質問者 1 プロセス）を変種の差し替えを当てて 1 回流し，1 要求あたりの質問者の CPU を出す．
# 使い方：python run_one.py <config> <placement> <out_dir> <variant> [prof]
#   variant：cur（今の最良の構成のまま．config の pool_assign: lean を含む）/
#            rbody（/v1/retrieve の本文の埋め込みの JSON を問ごと〔配列ごと〕に 1 回だけ作り，shard_id・k・query_id だけを差し替えた bytes を送る）/
#            rshared（rbody に加えて，probe の pre_encoded の本文と同じ埋め込みの JSON を共有する．問ごとに埋め込みの符号化は 1 回）/
#            quiet（httpx の INFO のログを止める．比較のための記録）
#   prof を付けると cProfile の結果を <out_dir>.prof に書く（主スレッドだけ）．
# 本番のコードは変えない（差し替えはこのプロセスの中だけ）．rbody・rshared は最初の BODY_CHECKS 本の本文を json= の本文とバイト単位で比べ，違えば止める．
import cProfile
import json
import logging
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx

import atrium.requester as requester
from atrium.cli import main
from atrium.protocol import RetrieveRequest, RetrieveResponse

BODY_CHECKS = 200
CACHE_SIZE = 1024  # parallel 30 の同時の問の埋め込みを十分に覆う数


class EncodedEmbeddingCache:
    """配列（問ごとの埋め込み）の id から埋め込みの JSON の bytes を引く．配列への参照を持つので，載っている間は id が使い回されない．"""

    def __init__(self) -> None:
        """空の表を作る．"""
        self._table: OrderedDict[int, tuple[Any, bytes]] = OrderedDict()

    def encoded(self, array: Any) -> bytes:
        """配列の埋め込みの JSON を返す（無ければ作って載せる）．"""
        hit = self._table.get(id(array))
        if hit is not None and hit[0] is array:
            return hit[1]
        data = requester.encode_json_like_httpx(array.tolist())
        self._table[id(array)] = (array, data)
        if len(self._table) > CACHE_SIZE:
            self._table.popitem(last=False)
        return data


def _build_retrieve_body(cache: EncodedEmbeddingCache, shard_id: str, k: int, embedding: Any, qid: str) -> bytes:
    """RetrieveRequest(...).model_dump() を httpx の json= で送るときと同じ bytes を，埋め込みの JSON を使い回して作る．"""
    enc = requester.encode_json_like_httpx
    return (
        b'{"shard_id":' + enc(shard_id) + b',"k":' + enc(k) + b',"embedding":' + cache.encoded(embedding)
        + b',"query_id":' + enc(qid) + b"}"
    )


def patch_retrieve(shared: bool) -> None:
    """Requester._retrieve を，本文を使い回す版に差し替える．shared なら probe の本文とも共有する．"""
    cache = EncodedEmbeddingCache()
    checked = [0]

    async def retrieve(self: Any, source: Any, shard_id: str, embedding: Any, question: Any) -> Any:
        if source.kind != "faiss":
            raise SystemExit("rbody は faiss のシャードだけを想定する")
        k = self._k_ret()
        body = _build_retrieve_body(cache, shard_id, k, embedding, question.source_qid)
        if checked[0] < BODY_CHECKS:
            ref = RetrieveRequest(shard_id=shard_id, k=k, embedding=embedding.tolist(), query_id=question.source_qid)
            if body != requester.encode_json_like_httpx(ref.model_dump()):
                raise SystemExit(f"retrieve の本文が json= と一致しない: {shard_id}")
            checked[0] += 1
        start = time.perf_counter()
        url = f"{self._url_of_shard[shard_id]}/v1/retrieve"
        response = await requester.post_with_retry(self.client, url, body, requester.RETRIEVE_TIMEOUT_S)
        response.raise_for_status()
        parsed = RetrieveResponse.model_validate_json(response.content)
        stats = {
            "shard_id": shard_id,
            "source": source.source,
            "bytes": len(response.content),
            "n_docs": len(parsed.docs),
            "node_s": parsed.duration_s,
            "rtt_s": time.perf_counter() - start,
        }
        return [requester.SourcedDoc(source.source, shard_id, d) for d in parsed.docs], stats

    requester.Requester._retrieve = retrieve  # type: ignore[method-assign]
    if not shared:
        return

    # probe の pre_encoded は encode_json_like_httpx(embeddings[encoder].tolist()) で埋め込みの JSON を作る．
    # その tolist() の戻り値に，同じ配列の使い回しの bytes を持たせ，符号化を表引きに置き換える
    class EncodedList(list):  # type: ignore[type-arg]
        """埋め込みの JSON を持つリスト（probe の本文の組み立てだけで使う）．"""

        encoded: bytes

    class ArrayHandle:
        """probe に渡す埋め込みの代わり．tolist() だけを持つ．"""

        def __init__(self, array: Any) -> None:
            """元の配列を持つ．"""
            self.array = array

        def tolist(self) -> EncodedList:
            """表から埋め込みの JSON を引いて持たせたリストを返す（中身は空．符号化にしか使わない）．"""
            out = EncodedList()
            out.encoded = cache.encoded(self.array)
            return out

    orig_encode = requester.encode_json_like_httpx

    def encode(value: object) -> bytes:
        if isinstance(value, EncodedList):
            return value.encoded
        return orig_encode(value)

    orig_probe = requester.Requester._probe

    async def probe(self: Any, embeddings: dict[str, Any], record: dict[str, Any], targets: Any = None) -> Any:
        return await orig_probe(self, {k: ArrayHandle(v) for k, v in embeddings.items()}, record, targets)

    requester.encode_json_like_httpx = encode  # type: ignore[assignment]
    # cache.encoded は元の符号化を呼ぶ（requester.encode_json_like_httpx を差し替えた後も EncodedList は渡らない）
    requester.Requester._probe = probe  # type: ignore[method-assign]


def patch_variant(variant: str) -> None:
    """変種の差し替えを当てる（事前の測定だけ．本番のコードは変えない）．"""
    if variant == "cur":
        return
    if variant == "quiet":
        orig_basic = logging.basicConfig

        def basic(**kw: Any) -> None:
            orig_basic(**kw)
            logging.getLogger("httpx").setLevel(logging.WARNING)

        logging.basicConfig = basic  # type: ignore[assignment]
        return
    if variant in ("rbody", "rshared"):
        patch_retrieve(shared=variant == "rshared")
        return
    raise SystemExit(f"unknown variant {variant}")


def summarize(out_dir: Path) -> dict[str, Any]:
    """run_meta.json と results.jsonl から 1 要求あたりの質問者の CPU と処理量を出す．"""
    meta = json.loads((out_dir / "run_meta.json").read_text())
    rows = [json.loads(line) for line in (out_dir / "results.jsonl").read_text().splitlines()]
    n_req = sum(r.get("probe_recipients", 0) + len(r.get("shard_stats", [])) for r in rows)
    ok = sum(r["error"] is None for r in rows)
    wall = meta["requester_cpu_s"] / meta["requester_cpu_util"]  # 問の処理の区間の壁時計
    return {
        "n_questions": len(rows),
        "failed": len(rows) - ok,
        "n_requests": n_req,
        "cpu_ms_per_req": 1000 * meta["requester_cpu_s"] / n_req,
        "cpu_user_s": meta["requester_cpu_user_s"],
        "cpu_sys_s": meta["requester_cpu_sys_s"],
        "cpu_util": meta["requester_cpu_util"],
        "qps": ok / wall,
        "req_per_s": n_req / wall,
        "pool_assign": meta.get("pool_assign"),
    }


if __name__ == "__main__":
    cfg, placement, out, variant = sys.argv[1:5]
    prof = len(sys.argv) > 5 and sys.argv[5] == "prof"
    patch_variant(variant)
    argv = ["--config", cfg, "run", "--data-dir", "/data", "--placement", placement,
            "--out-dir", out, "--ollama-url", "http://127.0.0.1:1"]
    if prof:
        pr = cProfile.Profile()
        pr.enable()
        code = main(argv)
        pr.disable()
        pr.dump_stats(out + ".prof")
    else:
        code = main(argv)
    print(json.dumps({"variant": variant, "cfg": Path(cfg).name, "prof": prof, "code": code,
                      **summarize(Path(out))}), flush=True)
