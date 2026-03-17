# -*- coding: utf-8 -*-
import io
import sys


class _NullTextIO(io.TextIOBase):
    def write(self, s):
        if s is None:
            return 0
        try:
            return len(s)
        except Exception:
            return 0

    def flush(self):
        return None

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _NullTextIO()

if sys.stderr is None:
    sys.stderr = _NullTextIO()
