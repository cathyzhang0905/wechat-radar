#!/usr/bin/env python3
"""Backward-compatible entrypoint for the feedback loop report."""
from analyze_feedback_loop import main


if __name__ == "__main__":
    raise SystemExit(main())
