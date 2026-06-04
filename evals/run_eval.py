#!/usr/bin/env python3
"""Convenience wrapper so `python evals/run_eval.py` works from the repo root.
The logic lives in app/eval/harness.py (also runnable as `python -m app.eval.harness`)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.eval.harness import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
