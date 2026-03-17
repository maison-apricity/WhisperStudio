# -*- coding: utf-8 -*-

import multiprocessing as mp

from paths import setup_runtime_environment
from font_runtime import register_private_fonts
from gui import SubtitleGUI

def main():
    mp.freeze_support()
    setup_runtime_environment()
    register_private_fonts()
    app = SubtitleGUI()
    app.mainloop()

if __name__ == "__main__":
    main()