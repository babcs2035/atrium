"""κ の参照採点を，失敗した要求の本文を記録しながら行う（500 の原因調べ）．judge_items だけを差し替える．"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

from atrium import judge
from atrium.config import load_config
from atrium.paths import dataset_paths

RUNS = ["b1", "b1_flood", "b2", "b2_1p7b", "b3", "b4"]
URLS = ["http://127.0.0.1:11435"] + [f"http://192.168.15.{i}:11434" for i in range(100, 110)]
failures: list[dict] = []


async def judge_items_logged(items, urls, model, cfg, transport=None):
    llm_cfg = judge._judge_llm_config(cfg)
    sems = {u: asyncio.Semaphore(judge.PER_URL_PARALLEL) for u in urls}
    out = {}
    async with httpx.AsyncClient() as client:

        async def one(i, it):
            prompt = judge.JUDGE_PROMPT.format(email=it.email, question=it.question, gold=it.gold, answer=it.answer)
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": llm_cfg.think,
                "options": {"num_predict": llm_cfg.num_predict, "num_ctx": llm_cfg.num_ctx, "temperature": llm_cfg.temperature},
            }
            for attempt in range(3):
                url = urls[(i + attempt) % len(urls)]
                async with sems[url]:
                    r = await client.post(f"{url}/api/chat", json=payload, timeout=llm_cfg.timeout_s)
                if r.status_code == 200:
                    content = r.json()["message"]["content"]
                    out[it.key] = (judge.parse_verdict(content), content)
                    return
                failures.append({"key": it.key, "url": url, "status": r.status_code, "body": r.text[:500], "prompt_chars": len(prompt)})
                print("FAIL", failures[-1], file=sys.stderr, flush=True)
            out[it.key] = (None, "")

        await asyncio.gather(*(one(i, it) for i, it in enumerate(items)))
    return out


judge.judge_items = judge_items_logged
cfg = load_config(Path("/app/config.yaml"))
e = cfg.data.require_enronqa()
paths = dataset_paths(Path("/data"), "enronqa")
run_dirs = [Path(f"/results/20261009_eq5_{r}") for r in RUNS]
result = judge.kappa_against_reference(cfg, paths, run_dirs, URLS, e.judge_reference_model, e.judge_kappa_n)
print(json.dumps({"metrics": result, "failures": failures}, ensure_ascii=False, indent=1))
