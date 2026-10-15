# Iteration 13 の事前の測定：本物の atrium run（質問者 1 プロセス）を変種の差し替えを当てて 1 回流し，1 要求あたりの質問者の CPU を出す．
# 使い方：python run_one.py <config> <placement> <out_dir> <variant> [prof]
#   variant：cur（今の最良の構成のまま）/ quiet（httpx の INFO のログを止める）/ ka64（per_node の池の max_keepalive_connections を 64）/
#            nothread（埋め込みと統合の asyncio.to_thread を同期の呼び出しにする）
#   prof を付けると cProfile の結果を <out_dir>.prof に書く（主スレッドだけ）．
import asyncio
import cProfile
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

import atrium.experiment as experiment
import atrium.requester as requester
from atrium.cli import main


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
    if variant == "ka64":
        def build(http_pool: str, placement: Any) -> httpx.AsyncClient:
            mounts: dict[str, httpx.AsyncBaseTransport] = {
                origin: httpx.AsyncHTTPTransport(
                    limits=httpx.Limits(max_connections=64, max_keepalive_connections=64)
                )
                for origin in {
                    str(httpx.URL(n.url).copy_with(path="/")).rstrip("/") for n in placement.nodes
                }
            }
            return httpx.AsyncClient(limits=httpx.Limits(max_connections=512), mounts=mounts)

        experiment.build_http_client = build  # type: ignore[assignment]
        return
    if variant.startswith("cap"):
        # per_node の池の上限（max_connections）を 64 から小さくし，池が走査する接続の数を減らす
        cap = int(variant[3:])

        def build_cap(http_pool: str, placement: Any) -> httpx.AsyncClient:
            mounts: dict[str, httpx.AsyncBaseTransport] = {
                origin: httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=cap))
                for origin in {
                    str(httpx.URL(n.url).copy_with(path="/")).rstrip("/") for n in placement.nodes
                }
            }
            return httpx.AsyncClient(limits=httpx.Limits(max_connections=512), mounts=mounts)

        experiment.build_http_client = build_cap  # type: ignore[assignment]
        return
    if variant in ("linear", "linearonly"):
        # 割り当ての掃除の段の二重の走査（待機中の接続ごとに全接続の is_idle のリストを作る．httpcore PR #1000・#1035 の Bug 1）を
        # 1 回の走査に直す．keepalive の上限が sys.maxsize（httpx の Limits で max_keepalive_connections を省略）の下では
        # 閉じる接続は元と同じ．要求の割り当ての段は元のまま．linear は fastread も併せて当てる
        import httpcore

        def assign(self: Any) -> list[Any]:
            closing: list[Any] = []
            kept: list[Any] = []
            for connection in self._connections:
                if connection.is_closed():
                    continue
                if connection.has_expired():
                    closing.append(connection)
                    continue
                kept.append(connection)
            excess = sum(c.is_idle() for c in kept) - self._max_keepalive_connections
            if excess > 0:
                survivors = []
                for connection in kept:
                    if excess > 0 and connection.is_idle():
                        closing.append(connection)
                        excess -= 1
                    else:
                        survivors.append(connection)
                kept = survivors
            self._connections[:] = kept
            for pool_request in [r for r in self._requests if r.is_queued()]:
                origin = pool_request.request.url.origin
                available = next(
                    (c for c in self._connections if c.can_handle_request(origin) and c.is_available()),
                    None,
                )
                if available is not None:
                    pool_request.assign_to_connection(available)
                elif len(self._connections) < self._max_connections:
                    connection = self.create_connection(origin)
                    self._connections.append(connection)
                    pool_request.assign_to_connection(connection)
                else:
                    idle = next((c for c in self._connections if c.is_idle()), None)
                    if idle is not None:
                        self._connections.remove(idle)
                        closing.append(idle)
                        connection = self.create_connection(origin)
                        self._connections.append(connection)
                        pool_request.assign_to_connection(connection)
            return closing

        httpcore.AsyncConnectionPool._assign_requests_to_connections = assign  # type: ignore[method-assign]
        if variant == "linearonly":
            return
        variant = "fastread"
    if variant == "fastread":
        # 待機中の接続の生存の確かめ（has_expired → get_extra_info("is_readable")）で，anyio の extra_attributes の辞書の
        # 組み立てと getpeername を通らずに，生のソケットへ直接 poll する（判定の中身は同じ is_socket_readable）
        from httpcore._backends.anyio import AnyIOStream
        from httpcore._utils import is_socket_readable

        orig_get = AnyIOStream.get_extra_info

        def get_extra_info(self: Any, info: str) -> Any:
            if info == "is_readable":
                return is_socket_readable(self._stream._raw_socket)
            return orig_get(self, info)

        AnyIOStream.get_extra_info = get_extra_info  # type: ignore[method-assign]
        return
    if variant == "nothread":
        async def to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        requester.asyncio.to_thread = to_thread  # type: ignore[assignment]  # requester の asyncio は標準のモジュールそのもの
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
