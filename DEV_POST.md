# Draft — DEV post for Hacktoberfest Open-Source AI Challenge, Week 3

Publish **Oct 19–25** at https://dev.to/new. Required tags: `#devchallenge` +
`#hf26challenge` plus the week's unique tag (shown on the challenge page when Week 3
launches). **Read the revealed theme first** and add 2–3 sentences near the top tying
ScrubPilot to it — the theme is a scored criterion and it stays secret until launch day.

Opt-in category to tick on the DEV submission form: **Best Use of Render** (hosting the
agent's front end on `render.yaml`).

---

## Title

`I gave an AI agent a CSV and one rule: you may propose, you may not decide`

## Subtitle / first line

*Every agent framework I tried was a black box you prompt and hope. So I built one where
the entire decision layer is 80 lines you can audit in one sitting — and the model is
forbidden from touching the data.*

---

## What I built

[ScrubPilot](https://github.com/Zhiyilang/scrubpilot) cleans messy CSVs. Not glamorous.
That's the point: messy CSVs are the thing that actually eats an afternoon in every job I've
had.

You hand it a file with mixed date formats, `"$1,234.50"` amounts, `(89.00)` for refunds,
duplicate rows, blank rows, headers like `Customer Name` and ` Item Qty `, and a column that
is entirely empty. It runs a six-stage loop and hands back a clean file plus a receipt.

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
# before
Order Date,Customer Name, Item Qty ,Total Amount,Notes
2026-01-03,  Acme Corp  ,3,"$1,234.50",
01/05/2026,Globex LLC,1,(89.00),
3/7/26,Umbrella Inc,5,45.5,
2026-01-03,  Acme Corp  ,3,"$1,234.50",        <- duplicate
,,,,,                                          <- blank row

# after
order_date,customer_name,item_qty,total_amount
2026-01-03,Acme Corp,3,1234.50
2026-01-05,Globex LLC,1,-89.00
2026-03-07,Umbrella Inc,5,45.50
```

`(89.00)` became `-89.00`, `3/7/26` became `2026-03-07`, `45.5` became `45.50`, and the
all-empty `Notes` column was dropped. Every one of those is a decision I can point at in the
receipt and say *why*.

---

## Why open innovation is the whole point here

The interesting claim in this project is not "an AI cleaned my spreadsheet." It's that
**you can audit it**.

The decision layer — the part that decides what to do next — is about 80 lines in
[`scrubpilot/brains.py`](https://github.com/Zhiyilang/scrubpilot/blob/main/scrubpilot/brains.py).
The loop is 150 lines in `harness.py`. The tools are plain functions in `tools_csv.py`. No
framework, no orchestration library, no hidden planner. If you can read this post you can
read the agent. Try doing that with a hosted agent product.

Three concrete things that only worked because everything is open and local:

**1. It runs with no model at all.** The default brain is deterministic. `python -m scrubpilot
scrub file.csv` with no API key, no network, no account — and it does the whole job. That is
not a demo mode; it's the default. The LLM is an *upgrade path*, not a dependency.

**2. The data never leaves the machine.** My test file isn't real customer data, but the CSVs
people actually need cleaned are. "Upload your customer list to our API" is a non-starter for
a lot of that work, and no amount of "we don't train on it" fixes the part where the data
crosses a network boundary at all.

**3. I could read the source when it surprised me.** When a fix behaved unexpectedly, the
answer was in a function I wrote, not in a vendor's changelog. That sounds obvious. It is also
the single biggest practical difference between building on open code and building on an API.

The open-model path is there too: point `SCRUBPILOT_LLM_BASE` at any OpenAI-compatible
endpoint — Ollama, llama.cpp, vLLM, whatever you're running — and the same loop uses it as the
proposer. If it errors, times out, or returns garbage, the run visibly falls back to
`offline-fallback` and the receipt says so. Swap models, change behaviour, no vendor lock.

---

## The design: the brain proposes, the verifier decides

Here's the rule I wrote first and never broke:

> **A brain can only propose. Only the verifier decides.**

The brain — deterministic or LLM — outputs an action. It does not get to execute it. Before
anything touches the file, a deterministic verifier with no model in it checks five things:

1. **Allowlist** — is this action one of the known-good operations?
2. **No-op guard** — does it actually change anything? (An agent that "fixes" nothing forever
   is a real failure mode.)
3. **Damage ceiling** — does it change fewer than ~60% of cells? above that it's a rewrite,
   not a fix, and it's rejected.
4. **Regression check** — did the issue count go down, or at least stay flat? if it went up,
   roll back.
5. **Preconditions** — does the fix's prerequisite hold for this file?

Anything failing is rejected, and the rejection is recorded with its reason. The loop has a
step budget and a rejection budget, so a confused agent stops instead of thrashing.

This is why the LLM being wrong is not catastrophic. It cannot write to your file. The worst
it can do is propose something the verifier refuses — and you get to read the refusal.

`python eval.py` runs 57 assertions, including ones where I deliberately make the brain
misbehave: a rogue brain proposing destructive actions, a local stub HTTP server standing in
for the LLM path, and tools that throw mid-run to prove the rollback works.

---

## What broke, and what I'd do differently

**Ambiguous dates are genuinely undecidable.** `01/05/2026` is 5 January or 1 May depending on
which side of the Atlantic you're on. I pick a convention and say which one I picked in the
receipt. The lesson I keep relearning: for ambiguous data, *declare the assumption loudly*
rather than guess quietly.

**"(89.00)" is not one problem, it's two.** It's a minus sign *and* a format. My first
implementation handled the parens and left the formatting, which produced a clean-looking
column of values that were subtly wrong. Negative amounts are where cleaning tools quietly
destroy meaning.

**Duplicates need a definition, not a heuristic.** Exact-duplicate rows are safe to drop.
"Probably the same customer" is not, and I don't do it.

**The verifier rejected a lot of my own early fixes**, which was annoying and correct. A guard
that never fires is decoration.

---

## Try it

```bash
git clone https://github.com/Zhiyilang/scrubpilot && cd scrubpilot
python eval.py                                  # 57 assertions, no dependencies
python -m scrubpilot scrub examples/messy_orders.csv
python app.py                                   # Gradio UI (reads PORT)
```

Pure Python standard library — no `pip install`. It deploys on Render from the included
`render.yaml`, which is why I'm entering it for **Best Use of Render**: the hosted front end
runs the same auditable loop, and you can still download the receipt and read exactly what it
did to your file.

**Repo:** https://github.com/Zhiyilang/scrubpilot · MIT

If you build agents, steal the one rule: let the model propose, and put something you can read
in charge of saying yes.

---

## Tags

```
#devchallenge #hf26challenge #<week3-unique-tag> #ai #python #opensource #csv
```

## Submission checklist

- [ ] Post published **Oct 19–25**
- [ ] `#devchallenge` + `#hf26challenge` + Week 3 unique tag
- [ ] Theme revealed on launch day → add the theme paragraph near the top
- [ ] Tick **Best Use of Render** on the DEV submission form
- [ ] Repo public, MIT licence visible
- [ ] Share the post link (community response is part of selection)
