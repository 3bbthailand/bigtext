"""Double-click launcher; errors are shown instead of silently disappearing."""
from pathlib import Path
import sys
import traceback

try:
    from large_text.app import launch
    launch(sys.argv[1] if len(sys.argv) > 1 else None)
except Exception:
    error = traceback.format_exc()
    try:
        Path(__file__).with_name("bigtext_error.log").write_text(error, encoding="utf-8")
    except OSError:
        pass
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, error, "BigText startup error", 0x10)
