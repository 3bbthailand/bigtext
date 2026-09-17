"""Exercise the actual Tk window and capture only this application's window."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from large_text import i18n, theme as themes
from large_text.app import BigTextApp, displayed
from large_text.core import MIB, Cancelled


def spin(root, app, timeout=30):
    deadline = time.monotonic() + timeout
    while app.busy:
        root.update()
        if time.monotonic() >= deadline:
            raise TimeoutError("GUI operation did not finish")
        time.sleep(0.01)
    root.update()


def capture_window(root, path):
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    filename = str(path).replace("'", "''")
    command = r'''
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public class BigTextCapture {
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr dc, uint flags);
  public static void Save(IntPtr hwnd, string path) {
    Rect rect; if (!GetWindowRect(hwnd, out rect)) throw new Exception("GetWindowRect failed");
    using (Bitmap image = new Bitmap(rect.Right - rect.Left, rect.Bottom - rect.Top)) {
      using (Graphics graphics = Graphics.FromImage(image)) {
        IntPtr dc = graphics.GetHdc();
        bool ok;
        try { ok = PrintWindow(hwnd, dc, 0); } finally { graphics.ReleaseHdc(dc); }
        if (!ok) throw new Exception("PrintWindow failed");
      }
      image.Save(path, ImageFormat.Png);
    }
  }
}
'@ -ReferencedAssemblies System.Drawing
'''
    command += f"\n[BigTextCapture]::Save([IntPtr]{hwnd}, '{filename}')\n"
    process = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", command],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Add-Type compiles C# on every call; that alone can pass 20 s on a busy machine.
    deadline = time.monotonic() + 60
    while process.poll() is None:
        # PrintWindow sends WM_PRINT to Tk; keep this UI thread pumping events.
        root.update()
        if time.monotonic() > deadline:
            process.kill()
            process.communicate()
            raise TimeoutError("Window capture timed out")
        time.sleep(0.01)
    _, error = process.communicate()
    if process.returncode:
        raise RuntimeError(error.decode(errors="replace"))


def main():
    project = Path(__file__).resolve().parent
    reports = project / "reports"
    reports.mkdir(exist_ok=True)
    checks = []
    root = tk.Tk()
    root.withdraw()
    app = BigTextApp(root, language="en", theme="dark")
    try:
        with tempfile.TemporaryDirectory(prefix="bigtext-gui-", dir=project) as temp:
            folder = Path(temp)
            source = folder / "gui-test.txt"
            line = "hello สวัสดี 🌏\n".encode("utf-8")
            source.write_bytes(line * (3 * MIB // len(line) + 1))
            def fail(title, detail, **kwargs):
                raise AssertionError(f"GUI error: {title}: {detail}")
            with patch("large_text.app.messagebox.showerror", side_effect=fail), patch("large_text.app.messagebox.showinfo"):
                app.open_file(source)
                spin(root, app)
                assert app.snapshot.path == source and app.page_index == 0
                checks.append("open")
                app.navigate(1)
                spin(root, app)
                assert app.page_index == 1
                checks.append("next_page")
                app.percent.set("100")
                app.jump_percent()
                spin(root, app)
                assert app.page_index == app.snapshot.page_count(app.page_bytes) - 1
                checks.append("jump_to_end")
                app.load_page(0)
                spin(root, app)
                app.query.set("สวัสดี 🌏")
                app.search()
                spin(root, app)
                start, end = app.text.tag_ranges("found")
                assert app.text.get(start, end) == "สวัสดี 🌏"
                checks.append("unicode_search_and_highlight")
                first_match = app.last_match
                app.search()
                spin(root, app)
                start, end = app.text.tag_ranges("found")
                assert app.last_match > first_match
                assert app.text.get(start, end) == "สวัสดี 🌏"
                checks.append("repeated_search_after_emoji")
                exported = folder / "export.txt"
                with patch("large_text.app.filedialog.asksaveasfilename", return_value=str(exported)):
                    app.export()
                    spin(root, app)
                assert exported.read_text(encoding="utf-8-sig")
                checks.append("page_export")
                app.output_parent.set(str(folder))
                app.part_mib.set("1")
                app.split()
                spin(root, app)
                manifest = json.loads((app.split_output / "manifest.json").read_text(encoding="utf-8"))
                assert manifest["status"] == "complete" and manifest["parts"] >= 3
                checks.append("split_from_gui")
                def wait_for_stop(*args, cancel=None, **kwargs):
                    if not cancel.wait(10):
                        raise AssertionError("Stop did not reach the running job")
                    raise Cancelled("Operation cancelled")
                with patch("large_text.app.split_file", side_effect=wait_for_stop):
                    app.split()
                    root.update()
                    app.stop.invoke()
                    spin(root, app)
                assert app.last_status[0] == "cancelled"
                checks.append("stop_button_cancels_current_job")
                thai = folder / "thai-long-line.txt"
                sentence = "ใช้เมื่อต้องการส่งต่อหรือเปิดในโปรแกรมอื่น การอ่านในแท็บแรกไม่ต้องใช้พื้นที่สำเนาเพิ่ม "
                thai.write_text("ภาษาไทยบรรทัดยาว 🌏 " + sentence * 30 + "\n", encoding="utf-8")
                app.open_file(thai)
                spin(root, app)
                assert app.text.get("1.0", "end-1c") == displayed(app.view.text)
                assert len(app.text.tag_ranges("run")) >= 10
                checks.append("thai_long_line_draw_runs")
                app.open_file(project / "examples/demo_thai.txt")
                spin(root, app)
            root.deiconify()
            root.update()
            root.update_idletasks()
            screenshot = reports / "gui_preview.png"
            for code in ("th", "ja", "en"):
                app.set_language(code, remember=False)
                root.update()
                assert app.tabs.tab(0, "text").strip() == i18n.text(code, "tab_read")
                assert app.stop.cget("text") == i18n.text(code, "stop")
                assert app.root.title() == i18n.text(code, "title")
                if os.name == "nt":
                    shot = screenshot if code == "en" else reports / f"gui_preview_{code}.png"
                    capture_window(root, shot)
                    assert shot.is_file()
            checks.append("language_switch_en_th_ja")
            for name in ("light", "dark"):
                app.set_theme(name, remember=False)
                root.update()
                palette = themes.PALETTES[name]
                assert app.text.cget("background") == palette["page"]
                assert str(ttk.Style(root).lookup("TButton", "background")) == palette["button"]
                popdown = root.tk.call("ttk::combobox::PopdownWindow", app.comboboxes[0])
                assert root.tk.call(f"{popdown}.f.l", "cget", "-background") == palette["field"]
                if os.name == "nt" and name == "light":
                    capture_window(root, reports / "gui_preview_light.png")
            checks.append("theme_switch_dark_light")
            if os.name == "nt":
                checks.append("window_capture")
            result = {"checks_passed": checks, "tk": root.tk.call("info", "patchlevel"),
                      "window_size": [root.winfo_width(), root.winfo_height()],
                      "screenshot": screenshot.name if screenshot.exists() else None}
            (reports / "gui_verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, indent=2))
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
