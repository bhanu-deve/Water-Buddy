r"""Automated tests. Run from the project folder with:  .\.venv\Scripts\python.exe -m unittest -v

Qt is switched to its "offscreen" mode so the tests never flash windows on your screen.
(Things that only a real Windows desktop can prove - click-through, always-on-top - are
checked separately by hand / by the desktop test described in the README.)
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
