# Iteration 11 の事前調査：localhost の偽のノード 30 個に対して，質問者 1 プロセスの probe の処理量を本文の作り方・ログ・sniffio の有無で比べる．
# 使い方：python bench.py serve <port>（30 個）→ python bench.py {base|pre|base_quiet|pre_quiet} <問の数> [prof]．
# 制御点では run_ctrl.sh を atrium-full のコンテナで流した（sniffio_pkg は uv のキャッシュの sniffio を写したもので，ここには置かない）．
import asyncio
import cProfile
import os
import json
import logging
import pstats
import sys
import time

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI

sys.path.insert(0, "/mnt/data-raid/ktakahashi/workspace/atrium/src")
from atrium.protocol import ProbeRequest, ProbeResponse  # noqa: E402
from atrium.requester import post_with_retry  # noqa: E402

BASE_PORT = 18100
N_NODES = 30
DIM = 768


def serve(port: int) -> None:
    app = FastAPI()

    @app.post("/v1/probe")
    def probe(req: ProbeRequest) -> ProbeResponse:
        return ProbeResponse(node_id=str(port), scores={s: 0.5 for s in req.shard_ids}, duration_s=0.001)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


async def run(variant: str, n_questions: int, parallel: int) -> float:
    mounts = {
        f"http://127.0.0.1:{BASE_PORT + i}": httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=64))
        for i in range(N_NODES)
    }
    rng = np.random.default_rng(0)
    embs = rng.standard_normal((64, DIM)).astype(np.float32)
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=512), mounts=mounts) as client:

        async def ask(url: str, emb: np.ndarray, prefix: bytes | None) -> None:
            shard_ids = [f"s{j}" for j in range(5)]
            if prefix is None:
                req = ProbeRequest(shard_ids=shard_ids, embedding=emb.tolist())
                r = await post_with_retry(client, f"{url}/v1/probe", req.model_dump(), 120.0)
            else:
                body = prefix + json.dumps(shard_ids).encode() + b"}"
                r = await client.post(
                    f"{url}/v1/probe", content=body, headers={"content-type": "application/json"}, timeout=120.0
                )
            r.raise_for_status()
            ProbeResponse.model_validate_json(r.content)

        sem = asyncio.Semaphore(parallel)

        async def question(i: int) -> None:
            async with sem:
                emb = embs[i % 64]
                prefix = None
                if variant.startswith("pre"):
                    prefix = b'{"embedding":' + json.dumps(emb.tolist()).encode() + b',"shard_ids":'
                await asyncio.gather(*(ask(f"http://127.0.0.1:{BASE_PORT + n}", emb, prefix) for n in range(N_NODES)))

        await asyncio.gather(*(question(i) for i in range(20)))  # 慣らし
        t0 = time.perf_counter()
        await asyncio.gather(*(question(i) for i in range(n_questions)))
        return n_questions * N_NODES / (time.perf_counter() - t0)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "serve":
        serve(int(sys.argv[2]))
        sys.exit(0)
    variant = mode
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        filename=f"{os.path.dirname(os.path.abspath(__file__))}/{variant}.log")
    if variant.endswith("_quiet"):
        logging.getLogger("httpx").setLevel(logging.WARNING)
    n = int(sys.argv[2])
    if len(sys.argv) > 3 and sys.argv[3] == "prof":
        pr = cProfile.Profile()
        pr.enable()
        rps = asyncio.run(run(variant, n, 30))
        pr.disable()
        print(variant, "req/s (profiled)", round(rps, 1))
        st = pstats.Stats(pr)
        st.sort_stats("tottime").print_stats(30)
    else:
        print(variant, "req/s", round(asyncio.run(run(variant, n, 30)), 1))
