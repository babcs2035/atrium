"""d0009 B1（Iteration 36）：wafl500〜509 の GPU の実行環境のゲート（G-GPU-pre・G-GPU-post）を確かめて JSON に残す．

使い方（操作端末から wafl500〜509 へ直接 ssh する．読み取りと Ollama への 1 回の生成だけで，他のプロセスには触れない）:
    uv run python scripts/d0009/b1_gpu_gate.py pre  --out results/<run_id>/gpu_gate_pre.json
    uv run python scripts/d0009/b1_gpu_gate.py post --out results/<run_id>/gpu_gate_post.json \
        --pre results/<run_id>/gpu_gate_pre.json --since <epoch 秒> --until <epoch 秒>
終了コード 0：ゲートを満たす／1：満たさない／2：pre で (a) だけを満たさない（b1_chain が Ollama の再起動を 1 回だけ許す場合の判別）．
- pre：各機で (a) GPU の使用率が 5 秒おきの 3 回とも 0%，(b) Clocks Event Reasons の SW Thermal Slowdown・HW Slowdown が Not Active，
  (c) Ollama（container atrium-ollama-1 の 11434．host には公開されていないので container の IP へ）へ `/api/generate`（llama3.1:8b・固定の短い英文・num_predict 128・温度 0・主構成と同じ num_ctx）を 1 回送り
  `eval_count / eval_duration` ≥ 40 トークン/秒．
- post：各機の Ollama の log（`docker logs -t atrium-ollama-1 --since --until`）から，1 分ごとの生成の速さ（その分の eval のトークン数 ÷ 秒数）を出し，
  生成が 1 件以上ある全ての分で ≥ 40 トークン/秒，かつ (a) が 0%．SW Thermal Slowdown の累計の前後の差（pre の JSON があれば）を記録する．
  (a) は，質問者が打ち切った問の生成が残っている間の待ちとして，最大 POST_IDLE_GRACE_S 秒の間 POLL_S ごとに確かめ直す（待った秒数を記録する）．
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HOSTS = [f"wafl50{i}" for i in range(10)]
MIN_TOKENS_PER_S = 40.0  # 通常 55〜57，Iteration 35 の減速時 10〜14 の間（journal の Iteration 36）
UTIL_SAMPLES = 3
UTIL_INTERVAL_S = 5
POST_IDLE_GRACE_S = 120
POLL_S = 10
NUM_CTX = 16384  # config.yaml の llm.num_ctx（主構成）
NUM_PREDICT = 128
MODEL = "llama3.1:8b"
PROMPT = (
    "Explain in detail how a bicycle works, covering the frame, wheels, drivetrain, and brakes."
)
SSH = ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
SSH_TIMEOUT_S = 180
# Ollama の llama-server の log の行．例: `... slot print_timing: id  0 | task 18 |        eval time =    1153.40 ms /    67 tokens (...)`
EVAL_LINE = re.compile(
    r"^(\d{4}-\d\d-\d\dT\d\d:\d\d):\d\d\.\d+Z .*print_timing.*\|\s+eval time =\s+([\d.]+) ms /\s+(\d+) tokens"
)
EVENT_LINE = re.compile(r"^\s*(SW Thermal Slowdown|HW Slowdown)\s*:\s*(Not Active|Active)\s*$")
COUNTER_LINE = re.compile(r"^\s*SW Thermal Slowdown\s*:\s*(\d+) us\s*$")


def ssh_out(host: str, command: str) -> str:
    """host で command を実行して標準出力を返す（失敗は例外．ssh の警告は標準エラーなので混ざらない）．"""
    done = subprocess.run(
        [*SSH, host, command], capture_output=True, text=True, timeout=SSH_TIMEOUT_S, check=True
    )
    return done.stdout


def sample_utilization(host: str) -> list[int]:
    """GPU の使用率（%）を UTIL_INTERVAL_S 秒おきに UTIL_SAMPLES 回読む．"""
    command = (
        f"for i in $(seq {UTIL_SAMPLES}); do nvidia-smi --query-gpu=utilization.gpu "
        f"--format=csv,noheader,nounits; if [ $i -lt {UTIL_SAMPLES} ]; then sleep {UTIL_INTERVAL_S}; fi; done"
    )
    return [int(line) for line in ssh_out(host, command).split()]


def read_clock_events(host: str) -> dict[str, Any]:
    """Clocks Event Reasons の SW Thermal Slowdown・HW Slowdown の状態と，SW Thermal Slowdown の累計（マイクロ秒）．"""
    state: dict[str, bool] = {}
    counter: int | None = None
    for line in ssh_out(host, "nvidia-smi -q -d PERFORMANCE").splitlines():
        if m := EVENT_LINE.match(line):
            state[m.group(1)] = m.group(2) == "Active"
        elif m := COUNTER_LINE.match(line):
            counter = int(m.group(1))
    return {
        "sw_thermal_active": state.get("SW Thermal Slowdown"),
        "hw_slowdown_active": state.get("HW Slowdown"),
        "sw_thermal_counter_us": counter,
    }


def generation_speed(host: str) -> float:
    """Ollama へ `/api/generate` を 1 回送り，eval_count / eval_duration（トークン/秒）を返す．"""
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": PROMPT,
            "stream": False,
            "options": {"temperature": 0, "num_predict": NUM_PREDICT, "num_ctx": NUM_CTX},
        }
    )
    # Ollama の port は host に公開されていない（compose の内部網だけ）ので，container の IP へ直接送る
    out = ssh_out(
        host,
        f'ip=$(docker inspect -f "{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}" atrium-ollama-1); '
        f"curl -s --max-time 120 http://$ip:11434/api/generate -d '{body}'",
    )
    reply = json.loads(out)
    return float(reply["eval_count"]) / (float(reply["eval_duration"]) / 1e9)


def per_minute_speeds(host: str, since: int, until: int) -> dict[str, float]:
    """host の Ollama の log の eval 行から，分ごとの生成の速さ（トークン数 ÷ 秒数．その分に生成が無ければ含めない）を出す．"""
    log = ssh_out(
        host,
        f"docker logs -t atrium-ollama-1 --since {since} --until {until} 2>&1 | grep 'eval time'",
    )
    tokens: dict[str, int] = defaultdict(int)
    millis: dict[str, float] = defaultdict(float)
    for line in log.splitlines():
        if "prompt eval time" in line:
            continue
        if m := EVAL_LINE.match(line):
            tokens[m.group(1)] += int(m.group(3))
            millis[m.group(1)] += float(m.group(2))
    return {
        minute: tokens[minute] / (millis[minute] / 1000.0)
        for minute in sorted(tokens)
        if millis[minute] > 0
    }


def check_pre(host: str) -> dict[str, Any]:
    """G-GPU-pre の 1 台分．"""
    util = sample_utilization(host)
    events = read_clock_events(host)
    speed = generation_speed(host)
    a_ok = all(u == 0 for u in util)
    b_ok = events["sw_thermal_active"] is False and events["hw_slowdown_active"] is False
    c_ok = speed >= MIN_TOKENS_PER_S
    return {
        "host": host,
        "utilization": util,
        **events,
        "tokens_per_s": speed,
        "a_idle": a_ok,
        "b_clocks": b_ok,
        "c_speed": c_ok,
        "ok": a_ok and b_ok and c_ok,
    }


def check_post(host: str, since: int, until: int, pre: dict[str, Any] | None) -> dict[str, Any]:
    """G-GPU-post の 1 台分（(a) は POST_IDLE_GRACE_S 秒まで待ち直す）．"""
    speeds = per_minute_speeds(host, since, until)
    waited = 0
    util = sample_utilization(host)
    while not all(u == 0 for u in util) and waited < POST_IDLE_GRACE_S:
        time.sleep(POLL_S)
        waited += POLL_S + UTIL_INTERVAL_S * (UTIL_SAMPLES - 1)
        util = sample_utilization(host)
    events = read_clock_events(host)
    before = None if pre is None else pre.get(host, {}).get("sw_thermal_counter_us")
    after = events["sw_thermal_counter_us"]
    a_ok = all(u == 0 for u in util)
    speed_ok = bool(speeds) and all(v >= MIN_TOKENS_PER_S for v in speeds.values())
    return {
        "host": host,
        "per_minute_tokens_per_s": speeds,
        "min_tokens_per_s": min(speeds.values()) if speeds else None,
        "speed_ok": speed_ok,
        "utilization": util,
        "idle_wait_s": waited,
        "a_idle": a_ok,
        "sw_thermal_counter_us": after,
        "sw_thermal_counter_delta_us": None if before is None or after is None else after - before,
        "ok": speed_ok and a_ok,
    }


def main() -> int:
    """pre・post のどちらかを全 10 台で並列に確かめ，結果を JSON に書いて終了コードで合否を返す．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["pre", "post"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--pre")
    parser.add_argument("--since", type=int)
    parser.add_argument("--until", type=int)
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        if args.phase == "pre":
            rows = list(pool.map(check_pre, HOSTS))
        else:
            pre_json = json.loads(Path(args.pre).read_text("utf-8")) if args.pre else None
            pre_by_host = None if pre_json is None else {r["host"]: r for r in pre_json["hosts"]}
            rows = list(
                pool.map(lambda h: check_post(h, args.since, args.until, pre_by_host), HOSTS)
            )
    passed = all(r["ok"] for r in rows)
    result: dict[str, Any] = {
        "phase": args.phase,
        "checked_at": time.time(),
        "pass": passed,
        "hosts": rows,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), "utf-8")
    print(
        json.dumps(
            {
                "phase": args.phase,
                "pass": passed,
                "failed": [r["host"] for r in rows if not r["ok"]],
            }
        )
    )
    if passed:
        return 0
    only_a = args.phase == "pre" and all(
        r["b_clocks"] and r["c_speed"] for r in rows if not r["ok"]
    )
    return 2 if only_a else 1


if __name__ == "__main__":
    sys.exit(main())
