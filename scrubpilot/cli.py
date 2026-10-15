"""Command line interface.

  python -m scrubpilot scrub FILE.csv [-o cleaned.csv] [--json] [--html report.html]
  python -m scrubpilot demo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import scrub
from . import receipts

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def cmd_scrub(args) -> int:
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    result = scrub(text)
    r = result["receipt"]
    before, after = result["profile_before"], result["profile_after"]

    if args.json:
        print(receipts.receipt_json(receipts.build_receipt(result)))
    else:
        print(f"ScrubPilot — brain={r['brain']} outcome={result['outcome']} "
              f"({len(r['steps'])} steps, {result['rejected_actions']} rejected)")
        print(f"  issues: {len(before['issues'])} -> {len(after['issues'])}")
        print(f"  rows  : {before['row_count']} -> {after['row_count']}")
        print()
        for s in r["steps"]:
            mark = ""
            if s.get("accepted") is True:
                mark = " ok"
            elif s.get("accepted") is False:
                mark = " REJECTED"
            print(f"  [{s['stage']:<8}] {s['detail']}{mark}")
    if args.output:
        Path(args.output).write_text(result["cleaned"], encoding="utf-8")
        print(f"\ncleaned CSV written to {args.output}", file=sys.stderr)
    if args.html:
        Path(args.html).write_text(
            receipts.render_html(receipts.build_receipt(result)), encoding="utf-8"
        )
        print(f"HTML report written to {args.html}", file=sys.stderr)
    return 0 if result["outcome"] == "clean" else 1


def cmd_demo(_args) -> int:
    files = sorted(EXAMPLES_DIR.glob("*.csv")) + sorted(EXAMPLES_DIR.glob("*.txt"))
    if not files:
        print("no example files found", file=sys.stderr)
        return 1
    for f in files:
        print("=" * 72)
        print(f"# {f.name}")
        print("=" * 72)
        result = scrub(f.read_text(encoding="utf-8"))
        before, after = result["profile_before"], result["profile_after"]
        print(f"outcome={result['outcome']} "
              f"issues {len(before['issues'])} -> {len(after['issues'])} "
              f"rows {before['row_count']} -> {after['row_count']} "
              f"rejected={result['rejected_actions']}")
        print()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scrubpilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrub", help="clean a CSV file")
    p.add_argument("file", help="input .csv")
    p.add_argument("-o", "--output", help="write the cleaned CSV here")
    p.add_argument("--json", action="store_true", help="print the JSON receipt")
    p.add_argument("--html", metavar="REPORT.html", help="write an HTML report")
    p.set_defaults(func=cmd_scrub)

    d = sub.add_parser("demo", help="scrub the bundled example files")
    d.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
