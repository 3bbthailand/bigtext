"""English / Thai / Japanese strings for the BigText window. English is the default."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

DEFAULT = "en"
LANGUAGES = (("en", "English"), ("th", "ไทย"), ("ja", "日本語"))
CODES = tuple(code for code, _ in LANGUAGES)
_INDEX = {code: number for number, code in enumerate(CODES)}

# Meiryo UI suits Japanese but draws Thai as tiny fallback glyphs, so it is only
# used for the fixed interface text; anything that can hold file content or a
# file name stays on Tahoma in every language.
CONTENT_FAMILY = "Tahoma"
UI_FAMILIES = {"en": ("Tahoma",), "th": ("Tahoma",), "ja": ("Meiryo UI", "Yu Gothic UI", "Tahoma")}


class Message(str):
    """An English message from core.py, translated when it is displayed."""


# key: (English, Thai, Japanese). Thai lines stay short: Tk on Windows draws a
# label line in ~200-byte pieces and a piece that starts on a Thai mark breaks.
TEXT = {
    "title": ("BigText | Large text file reader", "BigText | อ่านไฟล์ข้อความขนาดใหญ่", "BigText | 大容量テキストビューア"),
    "tagline": ("Reads big files piece by piece • never loads the whole file into RAM",
                "อ่านไฟล์ใหญ่ทีละส่วน • ไม่โหลดทั้งไฟล์เข้า RAM",
                "大きなファイルを少しずつ読み込み • 全体を RAM に読み込みません"),
    "open": ("Open file…", "เปิดไฟล์…", "ファイルを開く…"),
    "no_file": ("No file selected", "ยังไม่ได้เลือกไฟล์", "ファイルが選択されていません"),
    "info_idle": ("Reads from disk one page at a time, no need to split first",
                  "อ่านจากดิสก์ทีละหน้า โดยไม่ต้องแบ่งไฟล์ก่อน",
                  "分割しなくてもディスクから 1 ページずつ読み込みます"),
    "file_info": ("{size} • {bytes} bytes • {encoding}", "{size} • {bytes} bytes • {encoding}",
                  "{size} • {bytes} バイト • {encoding}"),
    "encoding": ("Encoding", "Encoding", "文字コード"),
    "reload": ("Reload", "โหลดใหม่", "再読み込み"),
    "theme_dark": ("☾ Dark", "☾ มืด", "☾ ダーク"),
    "theme_light": ("☀ Light", "☀ สว่าง", "☀ ライト"),
    "tab_read": ("Read / Search", "อ่าน / ค้นหา", "閲覧 / 検索"),
    "tab_split": ("Split into files", "แบ่งเป็นไฟล์ย่อย", "ファイル分割"),
    "first": ("|◀", "|◀", "|◀"),
    "prev": ("◀ Previous", "◀ ก่อนหน้า", "◀ 前へ"),
    "next": ("Next ▶", "ถัดไป ▶", "次へ ▶"),
    "last": ("▶|", "▶|", "▶|"),
    "page_of": ("Page {page} / {count}", "หน้า {page} / {count}", "ページ {page} / {count}"),
    "page_none": ("Page – / –", "หน้า – / –", "ページ – / –"),
    "go_page": ("Go to page", "ไปหน้า", "ページへ移動"),
    "mib_per_page": ("MiB/page", "MiB/หน้า", "MiB/ページ"),
    "search_label": ("Search (exact)", "ค้นหา (ตรงตัว)", "検索（完全一致）"),
    "find_next": ("Find next", "ค้นหาถัดไป", "次を検索"),
    "go_percent": ("Go to %", "ไป %", "% へ移動"),
    "wrap": ("Wrap lines to window", "ตัดบรรทัดตามหน้าต่าง", "ウィンドウ幅で折り返す"),
    "export": ("Save this page as UTF-8…", "บันทึกหน้านี้เป็น UTF-8…", "このページを UTF-8 で保存…"),
    "split_title": ("Create smaller text files", "สร้างไฟล์ข้อความขนาดเล็ก", "小さなテキストファイルを作成"),
    "split_intro": ("Use this when the parts must be sent on or opened in another program.\n"
                    "Reading in the first tab needs no extra disk space.",
                    "ใช้เมื่อต้องการส่งต่อหรือเปิดในโปรแกรมอื่น\n"
                    "การอ่านในแท็บแรกไม่ต้องใช้พื้นที่สำเนาเพิ่ม",
                    "パーツを他の人に渡す、または別のプログラムで開く場合に使います。\n"
                    "最初のタブで読むだけなら追加のディスク容量は不要です。"),
    "part_size": ("Maximum size per file (MiB)", "ขนาดสูงสุดต่อไฟล์ (MiB)", "1 ファイルの最大サイズ (MiB)"),
    "mib_note": ("1 MiB = 1,048,576 bytes", "1 MiB = 1,048,576 bytes", "1 MiB = 1,048,576 バイト"),
    "output_folder": ("Output folder", "โฟลเดอร์ปลายทาง", "出力フォルダー"),
    "choose": ("Browse…", "เลือก…", "参照…"),
    "prefer_lines": ("End each file at a line break when one is near the size limit",
                     "เลือกจบบรรทัดก่อนถึงขนาดที่กำหนด เมื่อหาได้ใกล้จุดแบ่ง",
                     "サイズ上限の近くに改行があれば、そこでファイルを区切る"),
    "long_lines_note": ("Very long lines are cut at the size limit without breaking Unicode characters.\n"
                        "The original file is never modified.",
                        "ถ้าบรรทัดยาวมาก จะตัดตามขนาดโดยรักษาขอบเขต Unicode\n"
                        "ไฟล์ต้นฉบับจะไม่ถูกแก้ไข",
                        "非常に長い行は Unicode 文字を壊さずにサイズ上限で区切ります。\n"
                        "元のファイルは変更されません。"),
    "estimate": ("About {count} files or more • needs about {size} of extra disk space",
                 "ประมาณ {count} ไฟล์ขึ้นไป • ใช้พื้นที่สำเนาประมาณ {size} เพิ่มเติม",
                 "約 {count} ファイル以上 • 追加で約 {size} のディスク容量が必要です"),
    "open_first": ("Open a file before splitting", "เปิดไฟล์ก่อนเริ่มแบ่ง", "分割する前にファイルを開いてください"),
    "bad_size": ("Enter a valid file size", "กรอกขนาดไฟล์ที่ถูกต้อง", "正しいファイルサイズを入力してください"),
    "start_split": ("Start splitting", "เริ่มแบ่งไฟล์", "分割を開始"),
    "open_output": ("Open output folder", "เปิดโฟลเดอร์ผลลัพธ์", "出力フォルダーを開く"),
    "split_footer": ("Parts are grouped 1,000 per folder, with a manifest and SHA-256.\n"
                     "If you stop, finished parts are kept, but an incomplete set is never joined back.",
                     "ผลลัพธ์จัดกลุ่มละ 1,000 ไฟล์ พร้อม manifest และ SHA-256\n"
                     "หากหยุดงาน ไฟล์ที่เสร็จแล้วจะยังอยู่\n"
                     "แต่ชุดที่ไม่ครบจะไม่ถูกรวมกลับ",
                     "パーツは 1 フォルダーに 1,000 個ずつまとめ、manifest と SHA-256 を付けます。\n"
                     "途中で止めても完成したパーツは残りますが、不完全なセットは結合しません。"),
    "stop": ("Stop", "หยุดงาน", "停止"),
    "ready": ("Ready • open a file to start reading", "พร้อมใช้งาน • เปิดไฟล์เพื่อเริ่มอ่าน",
              "準備完了 • ファイルを開いて読み始めてください"),
    "working": ("Working… you can press Stop", "กำลังทำงาน… สามารถกดหยุดได้", "処理中… 「停止」で中止できます"),
    "progress": ("{detail} • {done} / {total}", "{detail} • {done} / {total}", "{detail} • {done} / {total}"),
    "error_status": ("Error: {error}", "เกิดข้อผิดพลาด: {error}", "エラー: {error}"),
    "cancelled": ("Stopped • finished parts were kept", "หยุดงานแล้ว • ไฟล์ย่อยที่เสร็จแล้วถูกเก็บไว้",
                  "停止しました • 完成したパーツは残してあります"),
    "not_found": ("Not found before the end of the file • go back to the first page to search from the start",
                  "ไม่พบข้อความจนถึงท้ายไฟล์ • กลับไปหน้าแรกเพื่อค้นหาตั้งแต่ต้น",
                  "ファイルの末尾まで見つかりませんでした • 先頭から探すには最初のページに戻ってください"),
    "found_at": ("Found at byte {offset} • showing the text around it", "พบที่ byte {offset} • แสดงบริบทใกล้ผลค้นหา",
                 "{offset} バイト目で見つかりました • 前後の文脈を表示しています"),
    "split_done_status": ("Done: {parts} files • {folder}", "เสร็จแล้ว {parts} ไฟล์ • {folder}",
                          "完了: {parts} ファイル • {folder}"),
    "split_done_title": ("Split finished", "แบ่งไฟล์เสร็จแล้ว", "分割が完了しました"),
    "split_done_body": ("Created {parts} files\n{folder}\n\nLong lines cut in the middle: {hard}",
                        "สร้าง {parts} ไฟล์\n{folder}\n\nแบ่งกลางบรรทัดยาว {hard} ครั้ง",
                        "{parts} 個のファイルを作成しました\n{folder}\n\n長い行を途中で区切った回数: {hard}"),
    "exported": ("Saved as UTF-8: {path}", "บันทึก UTF-8 แล้ว: {path}", "UTF-8 で保存しました: {path}"),
    "page_status": ("Bytes {start} to {end} • read {read}{note}", "byte {start} ถึง {end} • อ่าน {read}{note}",
                    "{start} ～ {end} バイト • {read} 読み込み{note}"),
    "decode_note": (" • some characters could not be decoded, try another encoding",
                    " • พบอักขระที่ถอดรหัสไม่ได้ ลองเปลี่ยน Encoding",
                    " • 読めない文字があります。文字コードを変えてみてください"),
    "filetype_logs": ("Text / logs", "ข้อความ / log", "テキスト / ログ"),
    "filetype_all": ("All files", "ทุกไฟล์", "すべてのファイル"),
    "filetype_text": ("Text", "ข้อความ", "テキスト"),
    "page_out_of_range": ("Page number is outside the file", "เลขหน้าอยู่นอกช่วงไฟล์", "ページ番号がファイルの範囲外です"),
    "percent_range": ("Percent must be between 0 and 100", "เปอร์เซ็นต์ต้องอยู่ระหว่าง 0 และ 100",
                      "パーセントは 0 から 100 の間で指定してください"),
    "search_limit": ("Enter search text of at most 4 KiB", "กรอกข้อความค้นหาไม่เกิน 4 KiB",
                     "検索テキストは 4 KiB 以内で入力してください"),
    "query_encoding": ("The search text cannot be written in this file's encoding",
                       "ข้อความค้นหามีอักขระที่ encoding ของไฟล์นี้ไม่รองรับ",
                       "検索テキストにこのファイルの文字コードで表せない文字があります"),
    "choose_output_first": ("Choose an output folder", "เลือกโฟลเดอร์ปลายทาง", "出力フォルダーを選んでください"),
    "closing": ("Stopping the job and closing…", "กำลังหยุดงานและปิดไฟล์…", "処理を停止して閉じています…"),
}

# Exceptions and progress details from core.py are English. Exact messages map to
# (Thai, Japanese); patterns map to (English, Thai, Japanese) with {0} for the match.
MESSAGES = {
    "Operation cancelled": ("ยกเลิกงานแล้ว", "処理を中止しました"),
    "Size must be greater than zero and at most 1 TiB": ("ขนาดต้องมากกว่า 0 และไม่เกิน 1 TiB",
                                                         "サイズは 0 より大きく 1 TiB 以下にしてください"),
    "Part size must be at least 64 bytes": ("ขนาดไฟล์ย่อยต้องอย่างน้อย 64 bytes", "パーツのサイズは 64 バイト以上にしてください"),
    "Choose a regular file": ("เลือกไฟล์ปกติ ไม่ใช่โฟลเดอร์", "通常のファイルを選んでください"),
    "File changed while opening; reopen it": ("ไฟล์เปลี่ยนระหว่างเปิด กรุณาเปิดใหม่",
                                              "開いている間にファイルが変更されました。開き直してください"),
    "Source size or timestamp changed; reopen the file before continuing": (
        "ขนาดหรือเวลาแก้ไขของไฟล์ต้นฉบับเปลี่ยน กรุณาเปิดไฟล์ใหม่ก่อนทำต่อ",
        "元ファイルのサイズまたは更新日時が変わりました。続ける前に開き直してください"),
    "Viewer page size must be 64 bytes to 4 MiB": ("ขนาดหน้าต้องอยู่ระหว่าง 64 bytes ถึง 4 MiB",
                                                    "ページサイズは 64 バイトから 4 MiB の範囲にしてください"),
    "Byte offset is outside the file": ("ตำแหน่ง byte อยู่นอกไฟล์", "バイト位置がファイルの範囲外です"),
    "Source ended unexpectedly": ("ไฟล์ต้นฉบับสิ้นสุดก่อนที่คาดไว้", "元ファイルが途中で終わっています"),
    "Page is outside the file": ("หน้านี้อยู่นอกไฟล์", "ページがファイルの範囲外です"),
    "Enter text to search for": ("กรอกข้อความที่ต้องการค้นหา", "検索するテキストを入力してください"),
    "Search text is limited to 64 KiB": ("ข้อความค้นหายาวได้ไม่เกิน 64 KiB", "検索テキストは 64 KiB までです"),
    "Invalid search range or buffer size": ("ช่วงค้นหาหรือขนาดบัฟเฟอร์ไม่ถูกต้อง", "検索範囲またはバッファサイズが不正です"),
    "Part size must be 64 bytes to 1 TiB": ("ขนาดไฟล์ย่อยต้องอยู่ระหว่าง 64 bytes ถึง 1 TiB",
                                            "パーツのサイズは 64 バイトから 1 TiB の範囲にしてください"),
    "Not enough free space for all parts; use the viewer without splitting": (
        "พื้นที่ว่างไม่พอสำหรับทุกไฟล์ย่อย ให้อ่านทีละหน้าแทนการแบ่งไฟล์",
        "すべてのパーツを保存する空き容量がありません。分割せずにビューアで読んでください"),
    "Part size is too small for this encoding": ("ขนาดไฟล์ย่อยเล็กเกินไปสำหรับ encoding นี้",
                                                 "この文字コードにはパーツのサイズが小さすぎます"),
    "The original file is read-only; choose a different filename": ("ห้ามเขียนทับไฟล์ต้นฉบับ เลือกชื่อไฟล์อื่น",
                                                                    "元のファイルには書き込めません。別のファイル名を選んでください"),
    "Choose a new filename; existing files are not overwritten": ("เลือกชื่อไฟล์ใหม่ โปรแกรมไม่เขียนทับไฟล์ที่มีอยู่",
                                                                  "新しいファイル名を選んでください。既存のファイルは上書きしません"),
    "Found": ("พบแล้ว", "見つかりました"),
    "Searching": ("กำลังค้นหา", "検索中"),
    "Not found": ("ไม่พบ", "見つかりません"),
}
PATTERNS = (
    (re.compile(r"Unsupported encoding: (.*)", re.S),
     ("Unsupported encoding: {0}", "ไม่รองรับ encoding: {0}", "対応していない文字コード: {0}")),
    (re.compile(r"Part ([\d,]+)"), ("Part {0}", "ไฟล์ย่อย {0}", "パーツ {0}")),
    (re.compile(r"Completed ([\d,]+) parts"), ("Completed {0} parts", "เสร็จ {0} ไฟล์ย่อย", "{0} 個のパーツが完了")),
    (re.compile(r"(?:could not convert string to float|invalid literal for int\(\)).*", re.S),
     ("Enter a valid number", "กรอกตัวเลขให้ถูกต้อง", "正しい数値を入力してください")),
)


def text(language, key, **values):
    return TEXT[key][_INDEX.get(language, 0)].format(**values)


def message(language, raw):
    raw = str(raw)
    number = _INDEX.get(language, 0)
    for pattern, forms in PATTERNS:
        match = pattern.fullmatch(raw)
        if match:
            return forms[number].format(*match.groups())
    if number and raw in MESSAGES:
        return MESSAGES[raw][number - 1]
    return raw


def ui_family(language, available):
    for family in UI_FAMILIES.get(language, UI_FAMILIES[DEFAULT]):
        if family in available:
            return family
    return CONTENT_FAMILY


def settings_path():
    return Path(os.environ.get("APPDATA") or Path.home()) / "BigText" / "settings.json"


def _read_settings(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_setting(key, allowed, default, path=None):
    value = _read_settings(path or settings_path()).get(key)
    return value if isinstance(value, str) and value in allowed else default


def save_setting(key, value, path=None):
    """Update one key, keeping the other saved settings."""
    path = path or settings_path()
    data = _read_settings(path)
    data[key] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_language(path=None):
    return load_setting("language", CODES, DEFAULT, path)


def save_language(code, path=None):
    save_setting("language", code, path)
