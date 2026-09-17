"""All file operations are bounded; no full-file read, mmap, or line index."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

MIB = 1024 * 1024
IO_BLOCK = MIB
MAX_VIEW = 4 * MIB
LINE_LOOKBACK = 64 * 1024
ENCODINGS = ("auto", "utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be",
             "cp874", "cp1252", "latin-1")
BOMS = ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"))


class Cancelled(Exception):
    """A user-requested cancellation, with committed output left intact."""


class SourceChanged(OSError):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("Operation cancelled")


def mib_bytes(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 1024 * 1024:
        raise ValueError("Size must be greater than zero and at most 1 TiB")
    result = int(number * MIB)
    if result < 64:
        raise ValueError("Part size must be at least 64 bytes")
    return result


def signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@dataclass(frozen=True)
class Snapshot:
    path: Path
    size: int
    encoding: str
    bom: bytes
    identity: tuple

    @classmethod
    def inspect(cls, path, encoding="auto"):
        path = Path(path).expanduser().resolve(strict=True)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Choose a regular file")
        if encoding not in ENCODINGS:
            raise ValueError("Unsupported encoding: " + encoding)
        with path.open("rb") as source:
            head = source.read(4)
            if signature(os.fstat(source.fileno())) != signature(info):
                raise SourceChanged("File changed while opening; reopen it")
        detected = next(((bom, name) for bom, name in BOMS if head.startswith(bom)), (b"", "utf-8"))
        actual = detected[1] if encoding == "auto" else encoding
        bom = detected[0] if actual == detected[1] else b""
        return cls(path, info.st_size, actual, bom, signature(info))

    @property
    def unit(self):
        return 4 if self.encoding.startswith("utf-32") else 2 if self.encoding.startswith("utf-16") else 1

    def check(self, source):
        if signature(os.fstat(source.fileno())) != self.identity:
            raise SourceChanged("Source size or timestamp changed; reopen the file before continuing")

    def page_count(self, page_bytes):
        validate_page_bytes(page_bytes)
        return max(1, (self.size + page_bytes - 1) // page_bytes)


def validate_page_bytes(page_bytes):
    if not isinstance(page_bytes, int) or not 64 <= page_bytes <= MAX_VIEW:
        raise ValueError("Viewer page size must be 64 bytes to 4 MiB")


def aligned_offset(source, offset, snapshot):
    """Move a boundary back at most four bytes to avoid splitting a character."""
    offset = max(0, min(int(offset), snapshot.size))
    if offset in (0, snapshot.size):
        return offset
    if snapshot.encoding == "utf-8":
        for _ in range(3):
            source.seek(offset)
            value = source.read(1)
            if not value or value[0] & 0xC0 != 0x80:
                break
            offset -= 1
        return offset
    offset -= offset % snapshot.unit
    if snapshot.unit == 2 and offset >= 2:
        source.seek(offset - 2)
        previous = int.from_bytes(source.read(2), "little" if snapshot.encoding.endswith("le") else "big")
        if 0xD800 <= previous <= 0xDBFF:
            offset -= 2
    return offset


@dataclass(frozen=True)
class PageView:
    start: int
    end: int
    text: str
    decode_warning: bool
    raw: bytes


def read_window(snapshot, offset=0, page_bytes=MIB):
    validate_page_bytes(page_bytes)
    if not isinstance(offset, int) or not 0 <= offset <= snapshot.size:
        raise ValueError("Byte offset is outside the file")
    with snapshot.path.open("rb") as source:
        snapshot.check(source)
        start = aligned_offset(source, offset, snapshot)
        end = aligned_offset(source, min(snapshot.size, offset + page_bytes), snapshot)
        source.seek(start)
        raw = source.read(end - start)
        snapshot.check(source)
        if len(raw) != end - start:
            raise SourceChanged("Source ended unexpectedly")
    payload = raw[len(snapshot.bom):] if start == 0 and snapshot.bom else raw
    warning = False
    try:
        text = payload.decode(snapshot.encoding)
    except UnicodeDecodeError:
        text = payload.decode(snapshot.encoding, errors="replace")
        warning = True
    return PageView(start, end, text, warning, raw)


def read_page(snapshot, number=0, page_bytes=MIB):
    if not isinstance(number, int) or not 0 <= number < snapshot.page_count(page_bytes):
        raise ValueError("Page is outside the file")
    return read_window(snapshot, number * page_bytes, page_bytes)


class Progress:
    def __init__(self, callback):
        self.callback = callback
        self.last = 0.0

    def send(self, done, total, detail="", force=False):
        now = time.monotonic()
        if self.callback and (force or now - self.last >= 0.1):
            self.callback(done, total, detail)
            self.last = now


def find_next(snapshot, text, start=0, *, cancel=None, progress=None, block_size=IO_BLOCK):
    """Find a case-sensitive literal, including matches spanning IO blocks."""
    if not text:
        raise ValueError("Enter text to search for")
    needle = text.encode(snapshot.encoding)
    if len(needle) > 64 * 1024:
        raise ValueError("Search text is limited to 64 KiB")
    if not 0 <= start <= snapshot.size or not 1 <= block_size <= 8 * MIB:
        raise ValueError("Invalid search range or buffer size")
    notify = Progress(progress)
    tail, position = b"", start
    with snapshot.path.open("rb") as source:
        snapshot.check(source)
        source.seek(start)
        while position < snapshot.size:
            check_cancel(cancel)
            block = source.read(min(block_size, snapshot.size - position))
            if not block:
                raise SourceChanged("Source ended unexpectedly")
            data = tail + block
            base = position - len(tail)
            at = data.find(needle)
            while at >= 0:
                absolute = base + at
                if absolute % snapshot.unit == 0:
                    snapshot.check(source)
                    notify.send(absolute - start, snapshot.size - start, "Found", True)
                    return absolute
                at = data.find(needle, at + 1)
            position += len(block)
            tail = data[-(len(needle) - 1):] if len(needle) > 1 else b""
            snapshot.check(source)
            notify.send(position - start, snapshot.size - start, "Searching")
        check_cancel(cancel)
        snapshot.check(source)
    notify.send(snapshot.size - start, snapshot.size - start, "Not found", True)
    return None


def _line_end(source, start, end, snapshot):
    """Prefer a newline in the last 64 KiB, never scan an unbounded line."""
    low = max(start, end - LINE_LOOKBACK)
    low += (-low) % snapshot.unit
    source.seek(low)
    data = source.read(end - low)
    newline = "\n".encode(snapshot.encoding)
    at = data.rfind(newline)
    while at >= 0:
        if (low + at) % snapshot.unit == 0:
            return low + at + len(newline)
        at = data.rfind(newline, 0, at)
    return None


def _write_json(path, value):
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
    os.replace(temp, path)


def split_file(snapshot, destination, part_bytes=MIB, *, prefer_lines=True, cancel=None,
               progress=None, check_space=True):
    """Stream into a NEW directory. Completed parts survive cancellation.

    UTF-16/32 parts after the first receive a BOM for independent viewing.
    Per-part metadata records this prefix separately, so join_parts restores
    exactly the original bytes. All other bytes are copied without decoding.
    """
    if not isinstance(part_bytes, int) or not 64 <= part_bytes <= 1024 ** 4:
        raise ValueError("Part size must be 64 bytes to 1 TiB")
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    estimate = snapshot.size + ((snapshot.size + part_bytes - 1) // part_bytes + 1) * 1024
    if check_space and shutil.disk_usage(destination.parent).free < estimate:
        raise OSError("Not enough free space for all parts; use the viewer without splitting")
    check_cancel(cancel)
    destination.mkdir(exist_ok=False)
    manifest = {"version": 1, "source": str(snapshot.path), "source_size": snapshot.size,
                "encoding": snapshot.encoding, "part_bytes": part_bytes, "prefer_lines": prefer_lines,
                "created_at": datetime.now(timezone.utc).isoformat(), "status": "in_progress",
                "parts": 0, "bytes_copied": 0, "hard_splits": 0, "index": "parts.jsonl"}
    manifest_path = destination / "manifest.json"
    notify = Progress(progress)
    partial = None
    try:
        _write_json(manifest_path, manifest)
        with snapshot.path.open("rb") as source, (destination / "parts.jsonl").open("x", encoding="utf-8") as index:
            snapshot.check(source)
            position, number = 0, 0
            while position < snapshot.size:
                check_cancel(cancel)
                prefix = next((bom for bom, name in BOMS if name == snapshot.encoding), b"") if snapshot.unit > 1 and (number or not snapshot.bom) else b""
                end = aligned_offset(source, min(snapshot.size, position + part_bytes - len(prefix)), snapshot)
                if end <= position:
                    raise ValueError("Part size is too small for this encoding")
                hard_split = False
                if prefer_lines and end < snapshot.size:
                    line_end = _line_end(source, position, end, snapshot)
                    if line_end is not None and line_end > position:
                        end = line_end
                    else:
                        hard_split = True
                number += 1
                group = destination / f"group_{(number - 1) // 1000 + 1:05d}"
                group.mkdir(exist_ok=True)
                target = group / f"part_{number:08d}.txt"
                partial = target.with_suffix(".partial")
                digest = hashlib.sha256()
                copied = 0
                source.seek(position)
                with partial.open("xb") as output:
                    output.write(prefix)
                    while copied < end - position:
                        check_cancel(cancel)
                        data = source.read(min(IO_BLOCK, end - position - copied))
                        if not data:
                            raise SourceChanged("Source ended unexpectedly")
                        output.write(data)
                        digest.update(data)
                        copied += len(data)
                        snapshot.check(source)
                        notify.send(position + copied, snapshot.size, f"Part {number:,}")
                check_cancel(cancel)
                snapshot.check(source)
                os.replace(partial, target)
                partial = None
                record = {"file": target.relative_to(destination).as_posix(), "offset": position,
                          "length": copied, "prefix_bytes": len(prefix), "sha256": digest.hexdigest()}
                index.write(json.dumps(record) + "\n")
                index.flush()
                position = end
                manifest.update(parts=number, bytes_copied=position,
                                hard_splits=manifest["hard_splits"] + int(hard_split))
                if number % 100 == 0:
                    _write_json(manifest_path, manifest)
            check_cancel(cancel)
            snapshot.check(source)
        manifest["status"] = "complete"
    except BaseException as error:
        manifest["status"] = "cancelled" if isinstance(error, (Cancelled, KeyboardInterrupt)) else "error"
        manifest["error"] = str(error)
        if partial is not None:
            partial.unlink(missing_ok=True)
        try:
            _write_json(manifest_path, manifest)
        except OSError:
            pass
        raise
    _write_json(manifest_path, manifest)
    notify.send(snapshot.size, snapshot.size, f"Completed {manifest['parts']:,} parts", True)
    return manifest


def join_parts(directory, target, *, cancel=None, progress=None):
    """Verify hashes and restore the original bytes into a NEW file."""
    directory = Path(directory).resolve(strict=True)
    target = Path(target).resolve()
    with (directory / "manifest.json").open(encoding="utf-8") as source:
        manifest = json.load(source)
    if manifest.get("version") != 1 or manifest.get("status") != "complete":
        raise ValueError("Only a complete version 1 split can be joined")
    if target.exists():
        raise FileExistsError("Output already exists; choose a new filename")
    notify = Progress(progress)
    position, number = 0, 0
    # Claim the destination exclusively; remove only the file this call created.
    output = target.open("xb")
    try:
        with output, (directory / "parts.jsonl").open(encoding="utf-8") as index:
            for line in index:
                check_cancel(cancel)
                part = json.loads(line)
                path = (directory / part["file"]).resolve(strict=True)
                if not path.is_relative_to(directory) or part["offset"] != position:
                    raise ValueError("Invalid part path or byte order")
                prefix, length = part["prefix_bytes"], part["length"]
                if prefix not in (0, 2, 4) or length <= 0 or path.stat().st_size != prefix + length:
                    raise ValueError("Invalid part size")
                digest = hashlib.sha256()
                copied = 0
                with path.open("rb") as source:
                    source.seek(prefix)
                    while copied < length:
                        check_cancel(cancel)
                        data = source.read(min(IO_BLOCK, length - copied))
                        if not data:
                            raise OSError("Part ended unexpectedly")
                        output.write(data)
                        digest.update(data)
                        copied += len(data)
                        notify.send(position + copied, manifest["source_size"], f"Joining part {number + 1:,}")
                if digest.hexdigest() != part["sha256"]:
                    raise ValueError("Part checksum mismatch: " + part["file"])
                position += length
                number += 1
            if position != manifest["source_size"] or number != manifest["parts"]:
                raise ValueError("Missing or unexpected parts")
            check_cancel(cancel)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    notify.send(position, position, "Join verified", True)
    return target


def new_output_directory(parent, stem):
    # Keep names short enough for ordinary Windows paths, even for long sources.
    return Path(parent) / (stem[:48] + "_parts_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])


def export_utf8(view, target, source_path=None):
    target = Path(target).resolve()
    if source_path is not None and target == Path(source_path).resolve():
        raise ValueError("The original file is read-only; choose a different filename")
    if target.exists():
        raise FileExistsError("Choose a new filename; existing files are not overwritten")
    with target.open("xb") as output:
        # A UTF-8 BOM makes exported pages unambiguous to Windows editors.
        output.write(view.text.encode("utf-8-sig"))
    return target
