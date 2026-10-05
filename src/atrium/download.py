"""HTTP のダウンロード（データ中継点のデータ準備で使う）．

urllib の既定は読み取りに timeout が無く，相手が応答を止めると永久に待つ（codeload.github.com で実際に
起きた）．そこで timeout を付けて一時ファイルへ少しずつ書き，失敗したら最初からやり直す．
"""

from __future__ import annotations

import logging
import shutil
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

TIMEOUT_S = 60
RETRIES = 5
RETRY_WAIT_S = 10
CHUNK_BYTES = 1 << 20


def download(url: str, dest: Path) -> None:
    """url を dest に保存する．途中で失敗すれば RETRIES 回までやり直し，それでも失敗すれば例外を投げる．"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, RETRIES + 1):
        try:
            logger.info("downloading %s (attempt %d)", url, attempt)
            with (
                urllib.request.urlopen(url, timeout=TIMEOUT_S) as response,  # noqa: S310 (URL は設定の固定値)
                tmp.open("wb") as out,
            ):
                shutil.copyfileobj(response, out, CHUNK_BYTES)
                expected = response.headers.get("Content-Length")
            # 相手が途中で接続を閉じると例外にならずに短いファイルができる（NCBI で実際に起きた）ので，
            # Content-Length があれば受信したバイト数と照合する
            received = tmp.stat().st_size
            if expected is not None and received != int(expected):
                raise OSError(f"incomplete download: {received} of {expected} bytes")
            tmp.replace(dest)
            return
        except OSError as exc:  # URLError・タイムアウト・接続断はすべて OSError の派生
            logger.warning("download failed: %s", exc)
            if attempt == RETRIES:
                raise
            time.sleep(RETRY_WAIT_S)
