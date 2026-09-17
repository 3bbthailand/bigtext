import json
from pathlib import Path
import re
import string
import tempfile
import unittest

from large_text import i18n, theme
from large_text.app import MAX_RUNS_PER_LINE, RUN_BYTES, _joins_previous, run_spans

ROOT = Path(__file__).resolve().parents[1]


def tcl_bytes(text):
    return sum(1 if ord(c) < 0x80 else 2 if ord(c) < 0x800 else 3 if ord(c) < 0x10000 else 6 for c in text)


def draw_runs(text, spans):
    """Split each line of text at the tag boundaries, as Tk would draw it."""
    lines = text.split("\n")
    cuts = {}
    for index in spans:
        line, column = map(int, index.split("."))
        # "line.column" counts a supplementary character as two columns
        chars, units = 0, 0
        while units < column:
            units += 2 if ord(lines[line - 1][chars]) > 0xFFFF else 1
            chars += 1
        cuts.setdefault(line, set()).add(chars)
    runs = []
    for number, line in enumerate(lines, 1):
        bounds = sorted({0, len(line)} | cuts.get(number, set()))
        runs += [line[a:b] for a, b in zip(bounds, bounds[1:])]
    return runs


class RunSpanTests(unittest.TestCase):
    def check_runs(self, text):
        spans = run_spans(text)
        runs = draw_runs(text, spans)
        self.assertEqual("".join(runs), text.replace("\n", ""))
        for run in runs:
            self.assertLessEqual(tcl_bytes(run), 200, run)
            self.assertFalse(_joins_previous(run[0]), run)
        return spans

    def test_long_thai_line_starts_every_run_on_a_base_character(self):
        sentence = "ใช้เมื่อต้องการส่งต่อหรือเปิดในโปรแกรมอื่น การอ่านในแท็บแรกไม่ต้องใช้พื้นที่สำเนาเพิ่ม "
        self.assertTrue(self.check_runs("สั้น\n" + sentence * 40 + "\nจบ\n" + "ภาษาไทยไม่มีช่องว่าง" * 50))

    def test_emoji_columns_count_twice(self):
        self.assertTrue(self.check_runs(("สวัสดี 🌏👍🏽 ก้ำ " * 60) + "\n" + "😀" * 90))

    def test_short_lines_need_no_tags(self):
        self.assertEqual(run_spans("hello\nสวัสดีครับ\n" * 1000), [])

    def test_one_huge_line_is_capped(self):
        spans = run_spans("ภาษาไทยไม่มีช่องว่าง" * 20000)
        self.assertLessEqual(len(spans) // 2, MAX_RUNS_PER_LINE // 2 + 1)
        self.assertGreater(RUN_BYTES, 0)


class LanguageTests(unittest.TestCase):
    def test_every_string_has_three_languages_with_the_same_placeholders(self):
        fields = lambda s: sorted(name for _, name, _, _ in string.Formatter().parse(s) if name)
        for key, forms in i18n.TEXT.items():
            self.assertEqual(len(forms), len(i18n.CODES), key)
            self.assertTrue(all(forms), key)
            self.assertEqual(fields(forms[1]), fields(forms[0]), key)
            self.assertEqual(fields(forms[2]), fields(forms[0]), key)

    def test_thai_label_lines_stay_under_tk_draw_piece(self):
        thai = i18n.CODES.index("th")
        for key, forms in i18n.TEXT.items():
            for line in re.sub(r"\{\w+\}", "", forms[thai]).split("\n"):
                self.assertLessEqual(len(line.encode("utf-8")), 200, key)

    def test_core_messages_are_translated(self):
        source = (ROOT / "large_text" / "core.py").read_text(encoding="utf-8")
        found = re.findall(r'raise \w+\("([^"]+)"', source)
        found += re.findall(r'notify\.send\(.*, f?"([^"]+)"(?:, True)?\)$', source, re.M)
        self.assertGreater(len(found), 15)
        for raw in found:
            sample = re.sub(r"\{[^}]+\}", "12", raw)
            if raw.endswith(": "):
                sample += "x"
            if raw.startswith(("Invalid part", "Part checksum", "Missing or unexpected", "Only a complete",
                               "Output already exists", "Part ended", "Joining part", "Join verified")):
                continue  # join is command-line only
            for code in ("th", "ja"):
                self.assertNotEqual(i18n.message(code, sample), sample, (code, raw))

    def test_python_number_errors_become_friendly(self):
        for code in i18n.CODES:
            text = i18n.message(code, "could not convert string to float: 'abc'")
            self.assertNotIn("float", text)
        self.assertEqual(i18n.message("en", "Operation cancelled"), "Operation cancelled")

    def test_language_setting_round_trip_defaults_to_english(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "BigText" / "settings.json"
            self.assertEqual(i18n.load_language(path), "en")
            i18n.save_language("ja", path)
            self.assertEqual(i18n.load_language(path), "ja")
            path.write_text(json.dumps({"language": "xx"}), encoding="utf-8")
            self.assertEqual(i18n.load_language(path), "en")
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(i18n.load_language(path), "en")

    def test_theme_setting_keeps_language(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            self.assertEqual(i18n.load_setting("theme", theme.THEMES, theme.DEFAULT, path), "dark")
            i18n.save_language("th", path)
            i18n.save_setting("theme", "light", path)
            self.assertEqual(i18n.load_language(path), "th")
            self.assertEqual(i18n.load_setting("theme", theme.THEMES, theme.DEFAULT, path), "light")


def contrast(first, second):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class ThemeTests(unittest.TestCase):
    def test_palettes_define_the_same_colours(self):
        keys = set(theme.PALETTES["dark"])
        for name in theme.THEMES:
            self.assertEqual(set(theme.PALETTES[name]), keys, name)
            for key, color in theme.PALETTES[name].items():
                self.assertRegex(color, r"^#[0-9a-f]{6}$", (name, key))

    def test_text_is_easy_to_read_in_both_themes(self):
        # WCAG AAA (7:1) for reading text, AA (4.5:1) for coloured buttons.
        pairs = {("text", "window"): 7, ("text", "button"): 7, ("page_text", "page"): 7, ("text", "field"): 7,
                 ("found_text", "found"): 7, ("header_text", "header"): 7, ("tagline", "header"): 7,
                 ("accent_text", "accent"): 4.5, ("header_text", "switch_on"): 4.5,
                 ("header_text", "switch"): 7, ("select_text", "select"): 4.5}
        for name in theme.THEMES:
            palette = theme.PALETTES[name]
            for (fore, back), minimum in pairs.items():
                self.assertGreaterEqual(contrast(palette[fore], palette[back]), minimum, (name, fore, back))


if __name__ == "__main__":
    unittest.main()
