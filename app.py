"""ScrubPilot web app (Gradio 6).

Upload or paste a messy CSV -> the agent scrubs it -> you get the cleaned CSV,
the before/after stats, and the full agent trace (every proposal, every
verifier verdict).

The app's handlers are also exposed as MCP tools at /gradio_api/mcp/ when
launched with mcp_server=True.

Run:  python app.py            (reads PORT, binds 0.0.0.0)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import gradio as gr

from scrubpilot.agent import scrub
from scrubpilot import receipts

ROOT = Path(__file__).resolve().parent
EXAMPLE_PATH = ROOT / "examples" / "messy_orders.csv"
REPORT_DIR = Path(tempfile.mkdtemp(prefix="scrubpilot-reports-"))

CSS = """
.metric-row{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}
.metric{background:#f2f4f8;border-radius:10px;padding:10px 14px;min-width:130px}
.metric .v{font-size:20px;font-weight:700;color:#1c2333}
.metric .k{font-size:11px;color:#5b6478;text-transform:uppercase}
footer{display:none !important}
"""

HEADER_MD = """# 🧹 ScrubPilot — a local-first agent that cleans messy CSVs

Paste or upload a messy CSV. The agent profiles it, proposes one cleaning step
at a time, and a deterministic verifier checks every proposal before it is
applied — so a brain can propose, but it can never destroy.

Everything runs locally: pure Python standard library, no network, no key.
"""


def _summary_md(result: dict) -> str:
    before, after = result["profile_before"], result["profile_after"]
    outcome = result["outcome"]
    return (
        f"### Outcome: **{outcome}** — brain `{result['receipt']['brain']}`, "
        f"{len(result['receipt']['steps'])} steps, "
        f"{result['rejected_actions']} rejected proposal(s)\n\n"
        f"| issues | rows | columns |\n|---|---|---|\n"
        f"| {len(before['issues'])} → **{len(after['issues'])}** | "
        f"{before['row_count']} → {after['row_count']} | "
        f"{before['column_count']} → {after['column_count']} |\n\n"
        f"Fixed: {', '.join(sorted(set(before['issues']) - set(after['issues']))) or 'nothing'}"
    )


def _scrub(text: str):
    text = (text or "").strip()
    if not text:
        return ("_Paste a CSV or load the demo to begin._", "", {}, {}, None)
    result = scrub(text)
    receipt = receipts.build_receipt(result)
    cleaned_path = REPORT_DIR / "cleaned.csv"
    cleaned_path.write_text(result["cleaned"], encoding="utf-8")
    return (
        _summary_md(result),
        result["cleaned"],
        result["receipt"]["steps"],
        receipt,
        str(cleaned_path),
    )


def scrub_csv_text(csv_text: str):
    """Scrub a pasted CSV. Returns the summary, the cleaned CSV, the agent
    trace and the structured receipt."""
    return _scrub(csv_text)


def scrub_csv_file(file: str = ""):
    """Scrub an uploaded .csv file. Same engine as scrub_csv_text."""
    if not file:
        return ("_Upload a CSV first._", "", {}, {}, None)
    try:
        text = Path(file).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (f"Could not read file: {exc}", "", {}, {}, None)
    return _scrub(text)


def load_demo_csv() -> str:
    return EXAMPLE_PATH.read_text(encoding="utf-8")


with gr.Blocks(title="ScrubPilot — local-first CSV cleaning agent") as demo:
    gr.Markdown(HEADER_MD)

    with gr.Tab("Scrub"):
        with gr.Row():
            file_in = gr.File(label="Messy CSV", file_types=[".csv"])
            text_in = gr.Textbox(
                label="…or paste CSV rows",
                placeholder="Order Date,Customer Name\n2026-01-03,  Acme Corp  \n…",
                lines=6,
            )
        with gr.Row():
            scrub_btn = gr.Button("🧹 Scrub", variant="primary")
            demo_btn = gr.Button("Load demo & scrub", variant="secondary")
        summary_out = gr.Markdown()
        cleaned_out = gr.Textbox(label="Cleaned CSV", lines=8, max_lines=20)
        download_out = gr.DownloadButton("⬇️ Download cleaned CSV", variant="secondary")

    with gr.Tab("Agent trace"):
        gr.Markdown(
            "Every proposal the brain made and every verdict the verifier "
            "returned. `accepted: false` rows are proposals that were rejected "
            "— the data was never touched by them."
        )
        trace_out = gr.JSON(label="trace")

    with gr.Tab("Receipt"):
        receipt_out = gr.JSON(label="structured receipt")

    with gr.Tab("Brains"):
        gr.Markdown(
            """## Two brains, one interface

**`offline-deterministic`** (default) — reads the profile, proposes the next
whitelisted fix whose target issue actually exists. ~30 lines, no network,
converges in a fixed number of steps.

**`openai-compatible`** (optional) — asks any OpenAI-compatible chat endpoint
to propose the next fix as JSON. The endpoint can only *suggest*: the
verifier has the final word. Any failure (unreachable, malformed JSON,
out-of-whitelist proposal, already-resolved fix) degrades to the offline
policy and is recorded as `proposed_by: "offline-fallback"`.

Enable it with three env vars (all optional — the app is fully functional
without them):

```
SCRUBPILOT_LLM_BASE_URL=https://api.openai.com/v1
SCRUBPILOT_LLM_API_KEY=...
SCRUBPILOT_LLM_MODEL=gpt-4o-mini
```

A brain is ~40 lines. You can read the entire decision layer of this agent in
one sitting — that is the point of the project.
"""
        )

    with gr.Tab("How it works"):
        gr.Markdown(
            """## The loop

```
PERCEIVE -> PLAN -> ACT -> OBSERVE -> VERIFY -> (repeat) -> RECEIPT
```

## The verifier (deterministic, no model involved)

1. the fix must be on the whitelist
2. the fix must actually change something (no-ops rejected)
3. the fix may not destroy mass amounts of data (≥60% cell loss rejected)
4. issues must not increase (regressions rejected)
5. `drop_all_empty_column` requires an all-empty column to exist

## The fix whitelist

`trim_whitespace` · `drop_empty_rows` · `drop_duplicate_rows` ·
`normalize_dates` · `normalize_amounts` · `snake_case_headers` ·
`drop_all_empty_column`

Anything else cannot be applied, by construction — and a brain that proposes
it gets a rejection recorded in the trace.
"""
        )

    outputs = [summary_out, cleaned_out, trace_out, receipt_out, download_out]
    scrub_btn.click(scrub_csv_text, inputs=[text_in], outputs=outputs)
    scrub_btn.click(scrub_csv_file, inputs=[file_in], outputs=outputs)
    demo_btn.click(load_demo_csv, inputs=[], outputs=[text_in]).then(
        scrub_csv_text, inputs=[text_in], outputs=outputs
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "7860")),
        mcp_server=True,
        css=CSS,
    )
