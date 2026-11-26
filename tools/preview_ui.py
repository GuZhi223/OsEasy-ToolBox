"""Exercise the desktop UI in explicit demo mode and save its own window."""
import sys
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oseasy_auto.ui import Desktop

root = tk.Tk()
app = Desktop(root, demo=True)
errors = []
root.report_callback_exception = lambda *error: errors.append(str(error))
app.toggle()


def finish():
    try:
        assert app.active and len(app.tree.get_children()) == 4
        assert app.main.cget("text") == "停止演示"
        assert app.teacher_address == "192.0.2.10"
        app.copy_teacher_ip()
        assert root.clipboard_get() == "192.0.2.10"
        app.show_teacher({"state": "ambiguous", "address": "", "addresses": ["192.0.2.10", "192.0.2.20"]})
        assert app.copy_teacher.cget("state") == "disabled"
        app.show_teacher({"state": "resolved", "address": "192.0.2.10", "source": "模拟控制连接 · 示例地址"})
        root.update_idletasks()
        destination = Path("artifacts/ui-preview.png")
        destination.parent.mkdir(exist_ok=True)
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(destination)
        app.toggle_fullscreen()
        app.toggle()
        assert not app.active
        app.close()
    except Exception as error:
        errors.append(str(error))
        app.destroy()


root.after(2700, finish)
root.mainloop()
if errors:
    raise SystemExit("; ".join(errors))
print("UI demo start/events/toggle/stop/close passed; artifacts/ui-preview.png")
