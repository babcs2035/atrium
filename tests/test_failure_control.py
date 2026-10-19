"""failure_control.sh（止めたノードを再開する安全装置を含む，コンテナの停止・再開の受け口．p0007 I6）の仕様．

ssh は呼ばず，node_compose を差し替えて，どのホストにどの操作が頼まれたかを記録する．
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "remote" / "failure_control.sh"


def _run(
    tmp_path: Path, body: str, failing: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """failure_control.sh を source し，node_compose の呼び出し（"<host> <action>"）を calls に記録して body を実行する．"""
    calls = tmp_path / "calls.txt"
    script = f"""
set -uo pipefail
source {SCRIPT}
CONTROL_POLL_S=0.01
node_compose() {{
  echo "$1 $2" >> {calls}
  [ "$1" != "{failing}" ]
}}
control_init {tmp_path}/ctl h1 h2 h3
{body}
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    lines = calls.read_text("utf-8").splitlines() if calls.exists() else []
    return result, lines


def _request(tmp_path: Path, request_id: str, action: str, hosts: list[str]) -> str:
    path = tmp_path / "ctl" / "req" / f"{request_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": request_id, "action": action, "hosts": hosts}), "utf-8")
    return str(path)


def _ack(tmp_path: Path, request_id: str) -> dict[str, object]:
    ack: dict[str, object] = json.loads(
        (tmp_path / "ctl" / "ack" / f"{request_id}.json").read_text("utf-8")
    )
    return ack


def _stopped(tmp_path: Path) -> list[str]:
    return (tmp_path / "ctl" / "stopped.txt").read_text("utf-8").split()


def test_stop_and_start_run_only_the_node_service_commands_and_track_stopped_hosts(
    tmp_path: Path,
) -> None:
    stop = _request(tmp_path, "a", "stop", ["h1", "h2"])
    result, calls = _run(tmp_path, f"control_handle_request {stop}")
    assert result.returncode == 0, result.stderr
    assert sorted(calls) == ["h1 stop", "h2 stop"]
    ack = _ack(tmp_path, "a")
    assert ack["ok"] is True
    assert set(ack["hosts"]) == {"h1", "h2"}  # type: ignore[arg-type]
    assert sorted(_stopped(tmp_path)) == ["h1", "h2"]


def test_start_removes_the_host_from_the_stopped_list(tmp_path: Path) -> None:
    (tmp_path / "ctl").mkdir(parents=True)
    stop = _request(tmp_path, "a", "stop", ["h1", "h2"])
    start = _request(tmp_path, "b", "start", ["h1"])
    result, calls = _run(tmp_path, f"control_handle_request {stop}\ncontrol_handle_request {start}")
    assert result.returncode == 0, result.stderr
    assert _stopped(tmp_path) == ["h2"]
    assert _ack(tmp_path, "b")["ok"] is True
    assert calls.count("h1 start") == 1


def test_hosts_outside_the_allowed_list_and_other_actions_are_refused_without_running_anything(
    tmp_path: Path,
) -> None:
    unknown_host = _request(tmp_path, "a", "stop", ["h1", "elsewhere"])
    destructive = _request(tmp_path, "b", "down", ["h1"])
    removal = _request(tmp_path, "c", "rm -rf /", ["h1"])
    result, calls = _run(
        tmp_path,
        f"control_handle_request {unknown_host}\ncontrol_handle_request {destructive}\n"
        f"control_handle_request '{removal}'",
    )
    assert result.returncode == 0, result.stderr
    assert (
        calls == []
    )  # 1 台も実行しない（許可のホストの h1 も，同じ頼みに許可外が混ざれば実行しない）
    assert [_ack(tmp_path, i)["ok"] for i in "abc"] == [False, False, False]
    assert _stopped(tmp_path) == []


def test_a_failed_stop_is_reported_but_the_host_is_still_recorded_as_possibly_stopped(
    tmp_path: Path,
) -> None:
    stop = _request(tmp_path, "a", "stop", ["h1", "h2"])
    result, calls = _run(tmp_path, f"control_handle_request {stop}", failing="h2")
    assert result.returncode == 0, result.stderr
    ack = _ack(tmp_path, "a")
    assert ack["ok"] is False
    assert ack["hosts"]["h2"]["rc"] != 0  # type: ignore[index]
    assert sorted(_stopped(tmp_path)) == ["h1", "h2"]


def test_resume_starts_every_host_still_recorded_as_stopped(tmp_path: Path) -> None:
    stop = _request(tmp_path, "a", "stop", ["h1", "h3"])
    result, calls = _run(tmp_path, f"control_handle_request {stop}\ncontrol_resume_stopped")
    assert result.returncode == 0, result.stderr
    assert sorted(c for c in calls if c.endswith("start")) == ["h1 start", "h3 start"]


def test_resume_reports_failure_when_a_host_cannot_be_started(tmp_path: Path) -> None:
    stop = _request(tmp_path, "a", "stop", ["h1", "h2"])
    result, calls = _run(
        tmp_path, f"control_handle_request {stop}\ncontrol_resume_stopped", failing="h2"
    )
    assert result.returncode != 0
    assert "h1 start" in calls  # h2 の失敗が h1 の再開を止めない


def test_watch_handles_requests_until_the_done_marker_appears(tmp_path: Path) -> None:
    stop = _request(tmp_path, "a", "stop", ["h1"])
    del stop
    result, calls = _run(
        tmp_path, "(sleep 0.3; touch " + str(tmp_path) + "/ctl/done) &\ncontrol_watch\nwait"
    )
    assert result.returncode == 0, result.stderr
    assert calls == ["h1 stop"]
    assert _ack(tmp_path, "a")["ok"] is True
