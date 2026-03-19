# -*- coding: utf-8 -*-

import multiprocessing as mp

from paths import setup_runtime_environment, clear_temp_work_dir
from font_runtime import register_private_fonts, unregister_private_fonts
from gui import SubtitleGUI

import ctypes

def _enable_dpi_awareness():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

def main():
    mp.freeze_support()
    setup_runtime_environment()
    register_private_fonts()
    try:
        app = SubtitleGUI()
        app.mainloop()
    finally:
        try:
            clear_temp_work_dir(remove_root=True)
        except Exception:
            pass
        try:
            unregister_private_fonts()
        except Exception:
            pass

if __name__ == "__main__":
    main()
