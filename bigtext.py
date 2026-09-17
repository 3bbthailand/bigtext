"""BigText command line and GUI entry point (Python 3.11+, standard library)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from large_text.core import (Cancelled, MIB, Snapshot, export_utf8, find_next,
                             join_parts, mib_bytes, read_page, split_file)


def main(argv=None):
    args_in = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="BigText: bounded-memory large text viewer and splitter")
    sub = parser.add_subparsers(dest="command")
    gui = sub.add_parser("gui", help="Open the Windows GUI")
    gui.add_argument("file", nargs="?")
    for name in ("info", "page", "search", "split"):
        item = sub.add_parser(name)
        item.add_argument("file", type=Path)
        item.add_argument("--encoding", default="auto")
        if name == "page":
            item.add_argument("--number", type=int, default=1, help="Page number, starting at 1")
            item.add_argument("--size-mib", type=mib_bytes, default=MIB)
            item.add_argument("--out", type=Path, required=True, help="NEW UTF-8 output filename")
        if name == "search":
            item.add_argument("text", help="Case-sensitive literal text")
            item.add_argument("--start-byte", type=int, default=0)
        if name == "split":
            item.add_argument("--out", type=Path, required=True, help="NEW output directory")
            item.add_argument("--size-mib", type=mib_bytes, default=MIB)
            item.add_argument("--exact-size", action="store_true", help="Do not prefer newline boundaries; Unicode is still preserved")
    join = sub.add_parser("join", help="Verify all parts and recreate original bytes")
    join.add_argument("directory", type=Path)
    join.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(args_in)
    if args.command in (None, "gui"):
        from large_text.app import launch
        launch(getattr(args, "file", None))
        return 0
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    def progress(done, total, detail):
        percent = 100 * done / total if total else 100
        print(f"\r{detail}: {percent:6.2f}% ({done:,}/{total:,} bytes)", end="", file=sys.stderr, flush=True)
    try:
        if args.command == "join":
            result = {"output": str(join_parts(args.directory, args.out, progress=progress))}
        else:
            snapshot = Snapshot.inspect(args.file, args.encoding)
            if args.command == "info":
                result = {"file": str(snapshot.path), "bytes": snapshot.size, "encoding": snapshot.encoding,
                          "pages_at_1_mib": snapshot.page_count(MIB)}
            elif args.command == "page":
                view = read_page(snapshot, args.number - 1, args.size_mib)
                result = {"output": str(export_utf8(view, args.out, snapshot.path)), "start": view.start,
                          "end": view.end, "decode_warning": view.decode_warning}
            elif args.command == "search":
                result = {"byte_offset": find_next(snapshot, args.text, args.start_byte, progress=progress)}
            else:
                result = split_file(snapshot, args.out, args.size_mib, prefer_lines=not args.exact_size, progress=progress)
        print(file=sys.stderr)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, Cancelled, KeyboardInterrupt) as error:
        print(f"\nBigText: {error or 'Cancelled'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
