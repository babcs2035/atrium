"""ダウンロードの仕様（途中で切れた転送を成功として扱わない）．"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from atrium import download as dl

BODY = b"x" * 1000


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server の規約)
        truncated = self.path == "/truncated"
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        # truncated では宣言より短く送って接続を閉じる
        self.wfile.write(BODY[:100] if truncated else BODY)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_download_saves_complete_body(server: str, tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    dl.download(f"{server}/ok", dest)
    assert dest.read_bytes() == BODY


def test_download_rejects_truncated_transfer(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "RETRY_WAIT_S", 0)
    monkeypatch.setattr(dl, "RETRIES", 2)
    dest = tmp_path / "out.bin"
    with pytest.raises(OSError):
        dl.download(f"{server}/truncated", dest)
    assert not dest.exists()
