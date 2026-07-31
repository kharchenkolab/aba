"""Tests for the Record eval runner.

Run from the repo root or from record-eval/:

    python3 -m unittest discover record-eval/runner/tests    # from repo root
    python3 -m unittest discover runner/tests                # from record-eval/

unittest's discovery walks up from the start dir to the first directory
without an __init__.py (record-eval/) and puts it on sys.path, so the tests
import the package as `runner.*`.  The bootstrap below makes the same import
work under any other invocation (e.g. running a single test file).
"""

import os
import sys

_RECORD_EVAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _RECORD_EVAL not in sys.path:
    sys.path.insert(0, _RECORD_EVAL)
