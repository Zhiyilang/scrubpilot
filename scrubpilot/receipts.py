"""Receipts: structured JSON + a self-contained HTML report.

The HTML report is the shareable artifact: one file, inline CSS, no JS, no
external assets — email it to whoever asked for the clean CSV.
"""

from __future__ import annotations

import html
import json
from string import Template

_CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:#f6f7f9;color:#1c2333;margin:0;padding:24px;line-height:1.5}
.card{max-width:880px;margin:0 auto;background:#fff;border-radius:14px;
padding:28px 32px;box-shadow:0 1px 3px rgba(16,24,40,.08)}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#5b6478;font-size:13px;margin-bottom:18px}
.badge{display:inline-block;border-radius:999px;padding:3px 12px;font-size:12px;
font-weight:600;margin-bottom:16px}
.badge.clean{background:#e8f5e9;color:#1b5e20}
.badge.stopped{background:#fff3e0;color:#e65100}
.badge.budget{background:#fdecea;color:#b3261e}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
th{background:#f2f4f8;text-align:left;padding:8px 10px;border-bottom:2px solid #e3e7ef}
td{padding:7px 10px;border-bottom:1px solid #edf0f5;vertical-align:top}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
.rej{color:#b3261e;font-weight:600}
.acc{color:#1b5e20;font-weight:600}
.note{font-size:12px;color:#5b6478;margin-top:22px;border-top:1px solid #edf0f5;
padding-top:12px}
"""

_PAGE = Template("""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ScrubPilot report</title>
<style>$css</style></head><body><div class="card">
<h1>ScrubPilot — CSV cleaning report</h1>
<div class="sub">brain: $brain · outcome: <span class="badge $badge_class">$outcome</span> ·
$steps steps · $rejected rejected proposal(s)</div>
<h2>Before / after</h2>
<table><tr><th>metric</th><th>before</th><th>after</th></tr>
$stats_rows</table>
<h2>Agent trace</h2>
<table><tr><th>stage</th><th>detail</th><th>ms</th></tr>
$trace_rows</table>
<div class="note">The brain can only <em>propose</em>. Every proposal passed a
deterministic verifier before it was applied: whitelist check, no-op check,
anti-mass-destruction check (≥60% cell loss rejected), regression check.
ScrubPilot is pure Python standard library — your CSV never left this machine.</div>
</div></body></html>""")


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def build_receipt(result: dict) -> dict:
    """result is what agent.scrub() returns."""
    return {
        "tool": "ScrubPilot",
        "brain": result["receipt"]["brain"],
        "outcome": result["outcome"],
        "rejected_actions": result["rejected_actions"],
        "profile_before": result["profile_before"],
        "profile_after": result["profile_after"],
        "steps": result["receipt"]["steps"],
        "cleaned_preview": result["cleaned"][:2000],
    }


def render_html(receipt: dict) -> str:
    before, after = receipt["profile_before"], receipt["profile_after"]
    stats = [
        ("rows", before.get("row_count", 0), after.get("row_count", 0)),
        ("columns", before.get("column_count", 0), after.get("column_count", 0)),
        ("issues", len(before.get("issues", [])), len(after.get("issues", []))),
    ]
    stats_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{_esc(b)}</td><td>{_esc(a)}</td></tr>"
        for k, b, a in stats
    )
    trace_rows = ""
    for s in receipt["steps"]:
        verdict = ""
        if s.get("accepted") is True:
            verdict = ' <span class="acc">✓</span>'
        elif s.get("accepted") is False:
            verdict = ' <span class="rej">✗ rejected</span>'
        trace_rows += (
            f"<tr><td class=\"mono\">{_esc(s['stage'])}</td>"
            f"<td>{_esc(s['detail'])}{verdict}</td>"
            f"<td class=\"mono\">{_esc(s.get('ms', ''))}</td></tr>"
        )
    outcome = receipt["outcome"] or "unknown"
    badge = {"clean": "clean", "stopped": "stopped", "budget-exhausted": "budget"}.get(
        outcome, "stopped"
    )
    return _PAGE.substitute(
        css=_CSS,
        brain=_esc(receipt["brain"]),
        outcome=_esc(outcome),
        badge_class=badge,
        steps=len(receipt["steps"]),
        rejected=receipt["rejected_actions"],
        stats_rows=stats_rows,
        trace_rows=trace_rows,
    )


def receipt_json(receipt: dict) -> str:
    return json.dumps(receipt, ensure_ascii=False, indent=2)
