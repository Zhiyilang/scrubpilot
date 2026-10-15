# 🧹 ScrubPilot — a local-first agent that cleans messy CSVs

**The whole agent is one readable file. The brain can only propose — a
deterministic verifier decides.**

ScrubPilot takes a messy CSV (mixed date formats, `$1,234.50` amounts,
duplicate rows, blank rows, `Customer Name` headers, an all-empty column) and
cleans it through a six-stage agent loop:

```
PERCEIVE → PLAN → ACT → OBSERVE → VERIFY → (repeat) → RECEIPT
```

```bash
python -m scrubpilot scrub examples/messy_orders.csv
```

```
ScrubPilot — brain=offline-deterministic outcome=clean (29 steps, 0 rejected)
  issues: 8 -> 0
  rows  : 8 -> 6

  [PERCEIVE] brain offline-deterministic: 8 rows x 5 cols, 8 issue(s):
             header_whitespace, header_not_snake_case, cell_whitespace,
             empty_rows, duplicate_rows, mixed_date_formats, messy_amounts,
             all_empty_column:Notes
  [PLAN    ] propose apply_fix({'fix': 'trim_whitespace'}) — profile shows [...]
  [ACT     ] apply_fix applied (proposed by offline-deterministic)
  [VERIFY  ] ok: issues 8 -> 6, cells 57 -> 57
  ...
```

Before → after:

```csv
# before (excerpt)
Order Date,Customer Name, Item Qty ,Total Amount,Notes
2026-01-03,  Acme Corp  ,3,"$1,234.50",
01/05/2026,Globex LLC,1,(89.00),
3/7/26,Umbrella Inc,5,45.5,
2026-01-03,  Acme Corp  ,3,"$1,234.50",        <- duplicate
,,,,,                                            <- blank row

# after
order_date,customer_name,item_qty,total_amount
2026-01-03,Acme Corp,3,1234.50
2026-01-05,Globex LLC,1,-89.00
2026-03-07,Umbrella Inc,5,45.50
```

## The idea

Agent frameworks are usually black boxes: you prompt something and hope. This
one is the opposite — **the entire decision layer is ~80 lines you can read in
one sitting** (`scrubpilot/brains.py`), the loop is 150 (`harness.py`), and the
tools are plain functions (`tools_csv.py`). If you can read this README you can
audit the agent.

The safety property that matters: **a brain can only propose.** Every proposed
action passes a deterministic verifier — no model involved — before it is
applied:

| check | rejects |
|---|---|
| whitelist | any fix not on the 7-item whitelist |
| no-op | a fix that changes nothing |
| mass-destruction | a fix that loses ≥60% of cells |
| regression | a fix that increases the issue count |
| preconditions | `drop_all_empty_column` without an all-empty column |

A rogue brain (see `eval.py` section E) that proposes a nonexistent tool, then
a no-op, gets both rejected and the data is never touched. The rejections are
first-class events in the receipt.

## Two brains, one interface

**`offline-deterministic`** (default): reads the profile, proposes the next
whitelisted fix whose target issue actually exists. No network, no key,
converges in a fixed number of steps. This is what runs in the demo.

**`openai-compatible`** (optional): asks any OpenAI-compatible chat endpoint
to propose the next fix as JSON. It can only *suggest* — the verifier has the
final word. Any failure (unreachable endpoint, malformed JSON,
out-of-whitelist proposal, a fix whose work is already done) degrades to the
offline policy, recorded in the trace as `proposed_by: "offline-fallback"`.

```bash
SCRUBPILOT_LLM_BASE_URL=... SCRUBPILOT_LLM_API_KEY=... SCRUBPILOT_LLM_MODEL=... \
  python -m scrubpilot scrub your.csv
```

Swap in a local open-weight model (Ollama, vLLM, llama.cpp server — anything
OpenAI-compatible) and the same tools get a smarter proposer with zero code
changes. That is the open-innovation bet: **the brain is a plug, not a
prison.**

## Why offline

A CSV is often the whole customer list, the whole ledger, the whole payroll.
ScrubPilot is pure Python standard library: no network call, no telemetry, no
account, no API key. It runs on an air-gapped laptop. The HTML report is a
single self-contained file.

## Web app

```bash
pip install -r requirements.txt
python app.py        # reads PORT; handlers also exposed as MCP tools
```

Upload or paste a CSV → cleaned CSV + before/after stats + the full agent
trace + the receipt. One click on **“Load demo & scrub”** shows the whole
loop on the bundled example.

## Self-verification

```bash
python eval.py        # 57 assertions
```

Covers: the profile (all 8 planted issue types), every fix, the offline
policy's convergence, the verifier's rejection paths, a **rogue brain**
(unknown tool + no-op proposals rejected, data untouched), a brain whose tool
raises (rejected, state restored), the OpenAI-compatible brain against a
**local stub server** (valid proposal applied; garbage/out-of-whitelist/
unreachable all degrade to offline and still reach clean), receipts (no
script tags, no external assets), and the CLI.

## Layout

```
scrubpilot/
  harness.py     the loop (150 lines — read this first)
  brains.py      OfflineBrain + OpenAIBrain (~80 lines)
  tools_csv.py   profile / 7 whitelisted fixes / verify
  agent.py       wiring: tools + verifier + observe + done
  receipts.py    JSON receipt + self-contained HTML report
  cli.py         scrub / demo
app.py           Gradio 6 web app
eval.py          57 assertions
examples/        messy_orders.csv (8 planted issues)
```

## License

MIT — see `LICENSE`.
