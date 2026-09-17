from __future__ import annotations

import codecs
import hashlib
import itertools
import json
from pathlib import Path
import tempfile
import threading
import tracemalloc
import unittest
from unittest.mock import patch

from large_text import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bigtext-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source.txt"

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, data, encoding="auto"):
        self.source.write_bytes(data)
        return core.Snapshot.inspect(self.source, encoding)

    def test_paging_preserves_multibyte_characters_and_every_byte(self):
        text = "สวัสดี 🌏 café 日本語\r\n" * 60
        cases = [("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
                 ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
                 ("utf-32-le", codecs.BOM_UTF32_LE), ("utf-32-be", codecs.BOM_UTF32_BE)]
        for encoding, bom in cases:
            for size in (64, 67, 71, 128):
                with self.subTest(encoding=encoding, size=size):
                    data = bom + text.encode(encoding)
                    snapshot = self.snapshot(data)
                    pages = [core.read_page(snapshot, n, size) for n in range(snapshot.page_count(size))]
                    self.assertEqual(b"".join(p.raw for p in pages), data)
                    self.assertEqual("".join(p.text for p in pages), text)
                    self.assertFalse(any(p.decode_warning for p in pages))
                    self.assertTrue(all(len(p.raw) <= size + 3 for p in pages))

    def test_windows_thai_encoding(self):
        text = "ภาษาไทย สวัสดี\r\n" * 40
        snapshot = self.snapshot(text.encode("cp874"), "cp874")
        pages = [core.read_page(snapshot, n, 67) for n in range(snapshot.page_count(67))]
        self.assertEqual("".join(p.text for p in pages), text)

    def test_empty_file(self):
        snapshot = self.snapshot(b"")
        self.assertEqual(snapshot.page_count(core.MIB), 1)
        self.assertEqual(core.read_page(snapshot).text, "")
        self.assertIsNone(core.find_next(snapshot, "hello"))
        result = core.split_file(snapshot, self.root / "parts")
        self.assertEqual(result["parts"], 0)
        output = core.join_parts(self.root / "parts", self.root / "restored.txt")
        self.assertEqual(output.read_bytes(), b"")

    def test_invalid_bytes_remain_available_without_silent_cleaning(self):
        snapshot = self.snapshot(b"hello\xffworld")
        view = core.read_page(snapshot)
        self.assertTrue(view.decode_warning)
        self.assertEqual(view.raw, b"hello\xffworld")

    def test_source_changes_are_detected(self):
        snapshot = self.snapshot(b"old")
        self.source.write_bytes(b"new data")
        with self.assertRaises(core.SourceChanged):
            core.read_page(snapshot)
        with self.assertRaises(core.SourceChanged):
            core.find_next(snapshot, "data")

    def test_page_and_size_validation(self):
        snapshot = self.snapshot(b"hello")
        for size in (0, -1, 63, core.MAX_VIEW + 1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                core.read_page(snapshot, 0, size)
        for number in (-1, 1, 100):
            with self.subTest(number=number), self.assertRaises(ValueError):
                core.read_page(snapshot, number)
        for size in ("nan", "inf", "-1", "0", "0.0000001"):
            with self.subTest(size=size), self.assertRaises(ValueError):
                core.mib_bytes(size)

    def test_literal_search_crosses_blocks(self):
        for encoding in ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "cp874"):
            with self.subTest(encoding=encoding):
                text = "abcdef สวัสดี xyz สวัสดี end"
                data = text.encode(encoding)
                snapshot = self.snapshot(data, encoding)
                needle = "สวัสดี".encode(encoding)
                for size in (1, 7, 16):
                    first = core.find_next(snapshot, "สวัสดี", block_size=size)
                    self.assertEqual(first, data.find(needle))
                    second = core.find_next(snapshot, "สวัสดี", first + snapshot.unit, block_size=size)
                    self.assertEqual(second, data.find(needle, first + 1))
                    self.assertIsNone(core.find_next(snapshot, "MISSING", block_size=size))

    def test_utf16_search_ignores_unaligned_byte_matches(self):
        snapshot = self.snapshot(b"\x00A\x00\x00" + "A".encode("utf-16-le"), "utf-16-le")
        self.assertEqual(core.find_next(snapshot, "A", block_size=3), 4)

    def test_search_is_case_sensitive_and_cancellable(self):
        snapshot = self.snapshot(b"Hello hello")
        self.assertEqual(core.find_next(snapshot, "hello"), 6)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(core.Cancelled):
            core.find_next(snapshot, "hello", cancel=cancel)
        with self.assertRaises(ValueError):
            core.find_next(snapshot, "")

    def test_split_and_join_are_byte_exact_for_all_encodings(self):
        text = "สวัสดี 🌏 abcdefghijklmnop\r\n" * 40
        for encoding, bom in (("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
                              ("utf-16-le", b""), ("utf-16-be", codecs.BOM_UTF16_BE),
                              ("utf-32-le", codecs.BOM_UTF32_LE), ("utf-32-be", b"")):
            for prefer in (True, False):
                with self.subTest(encoding=encoding, bom=bool(bom), prefer=prefer):
                    data = bom + text.encode(encoding)
                    snapshot = self.snapshot(data, encoding)
                    folder = self.root / f"parts-{encoding}-{int(bool(bom))}-{prefer}"
                    result = core.split_file(snapshot, folder, 128, prefer_lines=prefer)
                    self.assertEqual(result["status"], "complete")
                    self.assertEqual(self.source.read_bytes(), data)
                    files = sorted(folder.glob("group_*/*.txt"))
                    self.assertTrue(all(p.stat().st_size <= 128 for p in files))
                    for part in files:
                        decoder = "utf-16" if encoding.startswith("utf-16") else "utf-32" if encoding.startswith("utf-32") else "utf-8-sig"
                        part.read_bytes().decode(decoder)
                    output = core.join_parts(folder, self.root / (folder.name + ".joined"))
                    self.assertEqual(output.read_bytes(), data)

    def test_prefer_lines_preserves_crlf_records(self):
        data = b"x" * 18 + b"\r\n"
        snapshot = self.snapshot(data * 100)
        folder = self.root / "parts"
        core.split_file(snapshot, folder, 128)
        for path in folder.glob("group_*/*.txt"):
            self.assertTrue(path.read_bytes().endswith(b"\r\n"))

    def test_very_long_line_stays_bounded(self):
        snapshot = self.snapshot(b"A" * (4 * core.MIB + 17))
        folder = self.root / "long-line"
        tracemalloc.start()
        try:
            result = core.split_file(snapshot, folder, 2 * core.MIB)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 8 * core.MIB)
        self.assertEqual(result["hard_splits"], 2)
        self.assertEqual(result["parts"], 3)

    def test_cancel_preserves_completed_parts_and_removes_partial(self):
        snapshot = self.snapshot(b"X" * (3 * core.MIB))
        folder = self.root / "cancelled"
        cancel = threading.Event()
        def notify(done, total, detail):
            if done > core.MIB:
                cancel.set()
        with patch.object(core.time, "monotonic", side_effect=itertools.count()), self.assertRaises(core.Cancelled):
            core.split_file(snapshot, folder, core.MIB, cancel=cancel, progress=notify)
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "cancelled")
        self.assertEqual(manifest["parts"], 1)
        self.assertEqual(len(list(folder.glob("group_*/*.txt"))), 1)
        self.assertFalse(list(folder.glob("group_*/*.partial")))
        with self.assertRaises(ValueError):
            core.join_parts(folder, self.root / "incomplete.txt")

    def test_existing_destinations_are_not_overwritten(self):
        snapshot = self.snapshot(b"data")
        folder = self.root / "existing"
        folder.mkdir()
        sentinel = folder / "keep.txt"
        sentinel.write_bytes(b"KEEP")
        with self.assertRaises(FileExistsError):
            core.split_file(snapshot, folder)
        self.assertEqual(sentinel.read_bytes(), b"KEEP")
        with self.assertRaises(ValueError):
            core.export_utf8(core.read_page(snapshot), self.source, self.source)

    def test_checksum_failure_removes_join_output(self):
        snapshot = self.snapshot(b"data\n" * 100)
        folder = self.root / "parts"
        core.split_file(snapshot, folder, 128)
        first = next(folder.glob("group_*/*.txt"))
        content = first.read_bytes()
        first.write_bytes(b"!" + content[1:])
        output = self.root / "restored.txt"
        with self.assertRaisesRegex(ValueError, "checksum"):
            core.join_parts(folder, output)
        self.assertFalse(output.exists())

    def test_disk_space_checked_before_creating_output(self):
        snapshot = self.snapshot(b"data")
        disk = core.shutil.disk_usage(self.root)._replace(free=0)
        with patch.object(core.shutil, "disk_usage", return_value=disk), self.assertRaises(OSError):
            core.split_file(snapshot, self.root / "no-space")
        self.assertFalse((self.root / "no-space").exists())

    def test_groups_are_limited_to_one_thousand_parts(self):
        snapshot = self.snapshot(b"x" * (64 * 1001))
        folder = self.root / "groups"
        core.split_file(snapshot, folder, 64, prefer_lines=False)
        self.assertEqual(len(list((folder / "group_00001").glob("*.txt"))), 1000)
        self.assertEqual(len(list((folder / "group_00002").glob("*.txt"))), 1)

    def test_exported_page_is_utf8_with_bom(self):
        snapshot = self.snapshot("ภาษาไทย".encode("cp874"), "cp874")
        target = self.root / "export.txt"
        core.export_utf8(core.read_page(snapshot), target, self.source)
        self.assertEqual(target.read_text(encoding="utf-8-sig"), "ภาษาไทย")
        with self.assertRaises(FileExistsError):
            core.export_utf8(core.read_page(snapshot), target)


if __name__ == "__main__":
    unittest.main()
