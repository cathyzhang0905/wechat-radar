#!/usr/bin/env python3
"""Write a readable report of learned Cubox preference signals."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cubox_preferences


def main() -> int:
    report = cubox_preferences.build_markdown_report()
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "cubox-preference-profile.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
