# Deploying ScrubPilot

## Local (zero dependencies)

```bash
python -m scrubpilot scrub examples/messy_orders.csv
python -m scrubpilot scrub your.csv -o cleaned.csv --html report.html
python -m scrubpilot demo
```

The engine is pure Python standard library — Python 3.8+, nothing to install.

## Web app

```bash
pip install -r requirements.txt
python app.py                 # http://localhost:7860 (or $PORT)
```

## Render (blueprint)

`render.yaml` is included. In Render: **New → Blueprint**, connect this repo,
and it builds as a web service running `python app.py`. No secrets required —
the optional LLM brain vars (`SCRUBPILOT_LLM_*`) can be set as environment
variables later, and the app works fully without them.

## Hugging Face Space

Create a Gradio Space, upload the repo (or connect the GitHub repo), and set
the Space README front-matter to:

```yaml
---
title: ScrubPilot
emoji: 🧹
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: mit
---
```

## Environment variables (all optional)

| variable | purpose |
|---|---|
| `PORT` | web app port (default 7860) |
| `SCRUBPILOT_LLM_BASE_URL` | OpenAI-compatible base URL (local Ollama/vLLM works) |
| `SCRUBPILOT_LLM_API_KEY` | key for the above |
| `SCRUBPILOT_LLM_MODEL` | model id for the above |

## Verify any deployment

```bash
python eval.py          # 57 assertions, no network needed
```
