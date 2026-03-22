# -*- coding: utf-8 -*-

import os
import sys
import ctypes
import multiprocessing as mp

from paths import setup_runtime_environment, clear_temp_work_dir
from font_runtime import register_private_fonts, unregister_private_fonts
from gui import SubtitleGUI


def _enable_dpi_awareness():
    if os.name != "nt":
        return


def main():
    mp.freeze_support()
    _enable_dpi_awareness()
    setup_runtime_environment()
    register_private_fonts()
    try:
        try:
            from gui import SubtitleGUI
        except ModuleNotFoundError as exc:
            if exc.name == "PySide6":
                raise RuntimeError(
                    "새 UI는 PySide6 기반입니다. 실행 환경에 PySide6가 설치되어 있어야 합니다."
                ) from exc
            raise

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
