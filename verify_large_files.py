"""Reproducible bounded-memory verification; sparse fixtures are always removed."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import tracemalloc

from large_text.core import MIB, Snapshot, find_next, join_parts, read_window, split_file


def make_sparse(path, size):
    with path.open("xb") as stream:
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            ioctl = kernel.DeviceIoControl
            ioctl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            ioctl.restype = wintypes.BOOL
            returned = wintypes.DWORD()
            if not ioctl(msvcrt.get_osfhandle(stream.fileno()), 0x000900C4, None, 0, None, 0,
                         ctypes.byref(returned), None):
                raise ctypes.WinError(ctypes.get_last_error())
            # The CRT truncate path can physically zero-fill a large extension.
            # SetEndOfFile changes the length of this already-sparse file.
            seek = kernel.SetFilePointerEx
            seek.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
            seek.restype = wintypes.BOOL
            end = kernel.SetEndOfFile
            end.argtypes = [wintypes.HANDLE]
            end.restype = wintypes.BOOL
            handle = msvcrt.get_osfhandle(stream.fileno())
            if not seek(handle, size, None, 0) or not end(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            stream.truncate(size)
        for offset, text in ((0, "BEGIN ภาษาไทย"), (size // 2, "MIDDLE ภาษาไทย"),
                             (size - 256, "END ภาษาไทย")):
            stream.seek(offset)
            stream.write(text.encode("utf-8"))


def allocated_bytes(path):
    if os.name != "nt":
        return path.stat().st_blocks * 512
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.GetCompressedFileSizeW
    function.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    function.restype = wintypes.DWORD
    high = wintypes.DWORD()
    low = function(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    return (high.value << 32) | low


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(MIB), b""):
            result.update(block)
    return result.hexdigest()


def run(logical_gib=100, real_mib=64, output=None):
    project = Path(__file__).resolve().parent
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "platform": os.name,
              "scope": "Sparse random access and tail search; full streaming search/split/join on a smaller real fixture"}
    with tempfile.TemporaryDirectory(prefix="bigtext-verify-", dir=project) as temporary:
        folder = Path(temporary)
        sparse = folder / "sparse-large.txt"
        size = int(logical_gib * 1024 ** 3)
        make_sparse(sparse, size)
        allocated = allocated_bytes(sparse)
        if allocated > 16 * MIB:
            raise AssertionError("Sparse fixture unexpectedly consumed more than 16 MiB")
        started = time.perf_counter()
        tracemalloc.start()
        try:
            snapshot = Snapshot.inspect(sparse)
            reads = []
            for offset, marker in ((0, "BEGIN"), (size // 2, "MIDDLE"), (size - MIB, "END")):
                before = time.perf_counter()
                view = read_window(snapshot, offset)
                assert marker in view.text
                reads.append({"offset": offset, "read_bytes": len(view.raw),
                              "seconds": time.perf_counter() - before})
            found = find_next(snapshot, "END ภาษาไทย", size - MIB)
            assert found == size - 256
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 16 * MIB, f"Unbounded sparse read allocation: {peak}"
        report["sparse"] = {"logical_bytes": size, "allocated_bytes": allocated, "pages_1_mib": snapshot.page_count(MIB),
                            "read_checks": reads, "tail_match_offset": found,
                            "python_peak_allocated_bytes": peak, "seconds": time.perf_counter() - started}
        print("Sparse random access and tail search passed", flush=True)

        real = folder / "real-text.txt"
        line = "record | สวัสดีประเทศไทย | café | 🌏\r\n".encode("utf-8")
        block = line * (MIB // len(line))
        block += b" " * (MIB - len(block) - 1) + b"\n" if len(block) < MIB else b""
        with real.open("xb") as target:
            for _ in range(real_mib):
                target.write(block)
        before_hash = digest(real)
        tracemalloc.start()
        started = time.perf_counter()
        try:
            snapshot = Snapshot.inspect(real)
            assert find_next(snapshot, "unique-absent-marker-74baaa") is None
            parts = folder / "parts"
            manifest = split_file(snapshot, parts, 8 * MIB)
            joined = join_parts(parts, folder / "joined.txt")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert digest(joined) == before_hash == digest(real)
        assert peak < 16 * MIB, f"Unbounded streaming allocation: {peak}"
        report["streaming"] = {"real_bytes": snapshot.size, "parts": manifest["parts"],
                               "sha256": before_hash, "round_trip_byte_exact": True,
                               "python_peak_allocated_bytes": peak, "seconds": time.perf_counter() - started}
        print("Real-file streaming search, split, and checksum-verified join passed", flush=True)
    report["temporary_fixtures_removed"] = True
    path = Path(output) if output else project / "reports/large_file_verification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logical-gib", type=int, default=100)
    parser.add_argument("--real-mib", type=int, default=64)
    parser.add_argument("--out", type=Path)
    arguments = parser.parse_args()
    if not 1 <= arguments.logical_gib <= 1000 or not 1 <= arguments.real_mib <= 1024:
        parser.error("logical-gib: 1..1000; real-mib: 1..1024")
    run(arguments.logical_gib, arguments.real_mib, arguments.out)
