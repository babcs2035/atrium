"""専用の一時ファイルと固定入力を使い CPU・RAM・ディスクを測定する．"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import tempfile
import time

MIB = 1024 * 1024
SIZE = 128 * MIB
ROUNDS = 10


def main() -> None:
    """10 回の反復を JSON Lines で出力する．"""
    print(
        json.dumps(
            {
                "kind": "environment",
                "python": platform.python_version(),
                "affinity": sorted(os.sched_getaffinity(0)),
            }
        ),
        flush=True,
    )
    source = ctypes.create_string_buffer(b"a" * SIZE)
    destination = ctypes.create_string_buffer(SIZE)
    block = b"a" * (8 * MIB)
    for index in range(ROUNDS):
        start = time.perf_counter()
        for _ in range(32):
            digest = hashlib.sha256(block).hexdigest()
        elapsed = time.perf_counter() - start
        print(
            json.dumps(
                {
                    "kind": "cpu_sha256",
                    "round": index,
                    "seconds": elapsed,
                    "bytes_per_s": 32 * len(block) / elapsed,
                    "digest": digest,
                }
            ),
            flush=True,
        )
        start = time.perf_counter()
        for _ in range(16):
            ctypes.memmove(destination, source, SIZE)
        elapsed = time.perf_counter() - start
        print(
            json.dumps(
                {
                    "kind": "ram_copy",
                    "round": index,
                    "seconds": elapsed,
                    "bytes_per_s": 16 * SIZE / elapsed,
                }
            ),
            flush=True,
        )
        # 他のデータや OS 全体のページキャッシュには触れない．
        with tempfile.TemporaryFile(
            prefix="atrium-gpu-equivalence-", dir="/home/denjo/workspace/ktakahashi/atrium"
        ) as stream:
            start = time.perf_counter()
            for _ in range(16):
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
            elapsed = time.perf_counter() - start
            print(
                json.dumps(
                    {"kind": "disk_write_fsync", "round": index, "bytes_per_s": SIZE / elapsed}
                ),
                flush=True,
            )
            os.posix_fadvise(stream.fileno(), 0, SIZE, os.POSIX_FADV_DONTNEED)
            stream.seek(0)
            start = time.perf_counter()
            read = 0
            while data := stream.read(8 * MIB):
                read += len(data)
            elapsed = time.perf_counter() - start
            print(
                json.dumps(
                    {
                        "kind": "disk_read_advised_cold",
                        "round": index,
                        "bytes_per_s": read / elapsed,
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
