"""Dark and light colours for the BigText window. Dark is the default."""
from __future__ import annotations

import os
import tkinter

THEMES = ("dark", "light")
DEFAULT = "dark"

PALETTES = {
    "dark": {
        "window": "#181c23", "text": "#e8edf4", "muted": "#aab4c3", "disabled": "#6f7988",
        "header": "#0d121b", "header_text": "#ffffff", "tagline": "#b4c3d9",
        "switch": "#243041", "switch_active": "#324257", "switch_on": "#2f6fd0",
        "button": "#2a313d", "button_active": "#374150", "button_pressed": "#20262f",
        "border": "#56606f", "light": "#3a4351", "dark": "#1f252d",
        "accent": "#2f6fd0", "accent_active": "#3f80e2", "accent_text": "#ffffff",
        "field": "#10141a", "tab": "#232a34", "trough": "#20262f", "thumb": "#4b5565",
        "page": "#10141a", "page_text": "#e8edf4", "select": "#2d4f80", "select_text": "#ffffff",
        "cursor": "#8ab8ff", "found": "#f2c14e", "found_text": "#1a1300",
    },
    "light": {
        "window": "#f3f5f8", "text": "#162235", "muted": "#4a5a70", "disabled": "#8a94a1",
        "header": "#14243c", "header_text": "#ffffff", "tagline": "#c5d5ec",
        "switch": "#2a3f60", "switch_active": "#35507a", "switch_on": "#2458b8",
        "button": "#e2e6ec", "button_active": "#eef1f5", "button_pressed": "#cbd2dc",
        "border": "#9ba4b1", "light": "#ffffff", "dark": "#c3c9d2",
        "accent": "#2458b8", "accent_active": "#174799", "accent_text": "#ffffff",
        "field": "#ffffff", "tab": "#d9dee6", "trough": "#dfe3e9", "thumb": "#c3c9d2",
        "page": "#ffffff", "page_text": "#1b283c", "select": "#c9defc", "select_text": "#1b283c",
        "cursor": "#2458b8", "found": "#ffe299", "found_text": "#382500",
    },
}


def configure_ttk(style, p):
    """Colour every ttk widget the window uses. The clam theme draws its own
    borders, so the bevel colours (light/dark/border) are set as well."""
    style.theme_use("clam")
    style.configure(".", background=p["window"], foreground=p["text"], bordercolor=p["border"],
                    lightcolor=p["light"], darkcolor=p["dark"], troughcolor=p["trough"],
                    selectbackground=p["select"], selectforeground=p["select_text"],
                    fieldbackground=p["field"], insertcolor=p["text"], arrowcolor=p["text"])
    style.map(".", background=[("disabled", p["window"]), ("active", p["button_active"])],
              foreground=[("disabled", p["disabled"])],
              selectbackground=[("!focus", p["select"])], selectforeground=[("!focus", p["select_text"])])
    style.configure("TFrame", background=p["window"])
    style.configure("TLabel", background=p["window"], foreground=p["text"])
    style.configure("TButton", padding=(10, 6), background=p["button"], foreground=p["text"])
    style.map("TButton", background=[("disabled", p["window"]), ("pressed", p["button_pressed"]),
                                     ("active", p["button_active"])],
              lightcolor=[("pressed", p["button_pressed"])], darkcolor=[("pressed", p["button_pressed"])],
              foreground=[("disabled", p["disabled"])])
    style.configure("Accent.TButton", background=p["accent"], foreground=p["accent_text"],
                    lightcolor=p["accent_active"], darkcolor=p["accent"])
    style.map("Accent.TButton", background=[("disabled", p["window"]), ("pressed", p["accent"]),
                                            ("active", p["accent_active"])],
              foreground=[("disabled", p["disabled"])])
    style.configure("TNotebook", background=p["window"], bordercolor=p["border"])
    style.configure("TNotebook.Tab", foreground=p["text"])
    style.map("TNotebook.Tab", background=[("selected", p["window"]), ("", p["tab"])],
              lightcolor=[("selected", p["light"]), ("", p["dark"])])
    style.configure("TEntry", fieldbackground=p["field"], foreground=p["text"])
    style.map("TEntry", fieldbackground=[("disabled", p["window"])], bordercolor=[("focus", p["accent"])],
              foreground=[("disabled", p["disabled"])])
    style.configure("TCombobox", fieldbackground=p["field"], foreground=p["text"], background=p["button"])
    style.map("TCombobox", fieldbackground=[("readonly", "focus", p["select"]), ("readonly", p["button"]),
                                            ("disabled", p["window"])],
              foreground=[("readonly", "focus", p["select_text"]), ("disabled", p["disabled"])],
              background=[("active", p["button_active"]), ("pressed", p["button_active"])],
              arrowcolor=[("disabled", p["disabled"])])
    style.configure("TCheckbutton", background=p["window"], foreground=p["text"],
                    indicatorbackground=p["field"], indicatorforeground=p["text"],
                    upperbordercolor=p["border"], lowerbordercolor=p["border"])
    style.map("TCheckbutton", background=[("active", p["window"])],
              indicatorbackground=[("disabled", p["window"]), ("pressed", p["button_pressed"])],
              foreground=[("disabled", p["disabled"])])
    style.configure("TScrollbar", background=p["thumb"], troughcolor=p["trough"])
    style.map("TScrollbar", background=[("pressed", p["accent"]), ("active", p["button_active"])])
    style.configure("TProgressbar", background=p["accent"], troughcolor=p["trough"])


def title_bar(root, dark):
    """Ask Windows 10/11 to draw this window's title bar dark or light."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    try:
        hwnd = wintypes.HWND(int(root.wm_frame(), 16))
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        # 20 is DWMWA_USE_IMMERSIVE_DARK_MODE; builds before 20H1 used 19.
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        if root.winfo_ismapped():
            # Windows 10 keeps drawing the old title bar (SWP_FRAMECHANGED does not
            # help); flipping WM_NCACTIVATE there and back repaints it, focus unchanged.
            user = ctypes.windll.user32
            user.GetForegroundWindow.restype = wintypes.HWND
            user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            active = user.GetForegroundWindow() == hwnd.value
            user.SendMessageW(hwnd, 0x0086, int(not active), 0)
            user.SendMessageW(hwnd, 0x0086, int(active), 0)
    except (AttributeError, OSError, ValueError, tkinter.TclError):
        pass
