"""ScrubPilot assertion suite — run: python eval.py

Covers the profile, every fix, the offline policy, the verifier's rejection
paths (including a rogue brain), the OpenAI-compatible brain against a local
stub server, the CLI, and receipt hygiene.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scrubpilot.agent import make_agent, make_verifier, scrub
from scrubpilot.brains import Brain, OfflineBrain, OpenAIBrain
from scrubpilot.harness import Action, Harness
from scrubpilot.tools_csv import FIX_WHITELIST, apply_fix, profile_csv
from scrubpilot import receipts

PASSED = FAILED = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label} {extra}")


def section(title: str) -> None:
    print(f"\n== {title} " + "=" * (58 - len(title)))


MESSY = (ROOT / "examples" / "messy_orders.csv").read_text(encoding="utf-8")
CLEAN_CSV = (
    "order_date,customer_name,item_qty,total_amount\n"
    "2026-01-03,Acme Corp,3,1234.50\n"
    "2026-01-05,Globex LLC,1,-89.00\n"
)

# ---------------------------------------------------------------- A. profile
section("A. profile")
p = profile_csv({"text": MESSY})
expected_issues = {
    "header_whitespace", "header_not_snake_case", "cell_whitespace",
    "empty_rows", "duplicate_rows", "mixed_date_formats", "messy_amounts",
    "all_empty_column:Notes",
}
check("all 8 planted issues detected", set(p["issues"]) == expected_issues,
      f"got {p['issues']}")
check("profile counts 8 data rows / 5 cols",
      p["row_count"] == 8 and p["column_count"] == 5)
check("profile is a pure read (no mutation)",
      profile_csv({"text": MESSY}) == p)
pc = profile_csv({"text": CLEAN_CSV})
check("clean csv -> ok, no issues", pc["ok"] and pc["issues"] == [])
pe = profile_csv({"text": ""})
check("empty text -> ok, no crash", pe["ok"] and pe["row_count"] == 0)

# ------------------------------------------------------------------ B. fixes
section("B. individual fixes")
st = {"text": MESSY}
trimmed = apply_fix(st, fix="trim_whitespace")["text"]
check("trim removes cell whitespace",
      "  Acme Corp  " not in trimmed and "Acme Corp" in trimmed)
check("trim does not mutate the input state", st["text"] == MESSY)
no_empties = apply_fix({"text": trimmed}, fix="drop_empty_rows")["text"]
check("drop_empty_rows removes blank rows",
      len([r for r in no_empties.splitlines() if r.strip()]) == 9)
no_dupes = apply_fix({"text": no_empties}, fix="drop_duplicate_rows")["text"]
check("drop_duplicate_rows removes exact duplicates",
      no_dupes.count("2026-01-03,Acme Corp,3,") == 1,
      f"count={no_dupes.count('2026-01-03,Acme Corp,3,')}")
snaked = apply_fix({"text": no_dupes}, fix="snake_case_headers")["text"]
check("snake_case_headers canonicalizes",
      snaked.splitlines()[0] == "order_date,customer_name,item_qty,total_amount,notes",
      snaked.splitlines()[0])
dated = apply_fix({"text": snaked}, fix="normalize_dates")["text"]
check("normalize_dates -> ISO", "2026-01-05,Globex" in dated and "2026-03-07,Umbrella" in dated)
amounted = apply_fix({"text": dated}, fix="normalize_amounts")["text"]
check("normalize_amounts: $1,234.50 -> 1234.50", "1234.50" in amounted)
check("normalize_amounts: (89.00) -> -89.00", "-89.00" in amounted)
check("normalize_amounts: USD 12 -> 12.00", ",12.00," in amounted)
check("normalize_amounts: 45.5 -> 45.50", ",45.50," in amounted)
dropped = apply_fix({"text": amounted}, fix="drop_all_empty_column")["text"]
check("drop_all_empty_column removes notes", "notes" not in dropped.splitlines()[0])
try:
    apply_fix(st, fix="rm -rf /")
    check("unknown fix raises", False)
except KeyError:
    check("unknown fix raises KeyError", True)
check("whitelist has 7 fixes", len(FIX_WHITELIST) == 7)

# ------------------------------------------------------------- C. offline run
section("C. offline agent run")
result = scrub(MESSY)
r = result["receipt"]
check("outcome clean", result["outcome"] == "clean", result["outcome"])
check("zero rejected proposals", result["rejected_actions"] == 0)
check("issues 8 -> 0",
      len(result["profile_before"]["issues"]) == 8
      and len(result["profile_after"]["issues"]) == 0)
check("rows 8 -> 6 (2 empty + 2 dupes removed)",
      result["profile_before"]["row_count"] == 8
      and result["profile_after"]["row_count"] == 6)
check("every accepted fix is whitelisted",
      all(s["action"]["args"]["fix"] in FIX_WHITELIST
          for s in r["steps"] if s.get("action")))
check("brain recorded in receipt", r["brain"] == "offline-deterministic")
stages = [s["stage"] for s in r["steps"]]
check("trace starts with PERCEIVE and cycles the 4 loop stages",
      stages[0] == "PERCEIVE"
      and stages.count("PLAN") == stages.count("ACT") == stages.count("OBSERVE"))
check("final observation is clean", r["final_state"]["ok"] is True)
lines = result["cleaned"].splitlines()
check("cleaned header is snake_case",
      lines[0] == "order_date,customer_name,item_qty,total_amount", lines[0])
check("cleaned has exactly 6 data rows", len(lines) == 7)
check("cleaned preserves values with evidence lineage",
      "2026-03-12,Stark Industries,1,9999.00" in result["cleaned"])
check("already-clean csv stays clean",
      scrub(CLEAN_CSV)["outcome"] == "clean"
      and scrub(CLEAN_CSV)["rejected_actions"] == 0)
check("empty input does not crash", scrub("")["outcome"] in ("clean", "stopped"))

# ------------------------------------------------------------- D. verifier
section("D. verifier rejection paths")
verifier = make_verifier()

ok, reason = verifier(
    Action(tool="apply_fix", args={"fix": "rm -rf"}, reason="rogue"),
    {"text": MESSY}, {"text": MESSY},
)
check("verifier rejects non-whitelisted fix", not ok and "whitelist" in reason, reason)

ok, reason = verifier(
    Action(tool="apply_fix", args={"fix": "trim_whitespace"}, reason="noop"),
    {"text": CLEAN_CSV}, {"text": CLEAN_CSV},
)
check("verifier rejects no-op", not ok and "no-op" in reason, reason)

big = "a,b\n" + "\n".join(f"{i},{i}" for i in range(100))
tiny = "a,b\n1,2\n"
ok, reason = verifier(
    Action(tool="apply_fix", args={"fix": "drop_duplicate_rows"}, reason="wipe"),
    {"text": big}, {"text": tiny},
)
check("verifier rejects mass destruction", not ok and "mass-destruction" in reason, reason)

ok, reason = verifier(
    Action(tool="apply_fix", args={"fix": "drop_all_empty_column"}, reason="rogue"),
    {"text": "a,b,c\n1,2,3\n"}, {"text": "a,b\n1,2\n"},
)
check("verifier rejects dropping when no all-empty column exists",
      not ok and "no all-empty column" in reason, reason)

# ------------------------------------------------------------- E. rogue brain
section("E. rogue brain (the safety story)")


class RogueBrain(Brain):
    """Proposes an unknown tool, then a no-op fix, then gives up."""

    name = "rogue"

    def __init__(self):
        self.calls = 0

    def perceive(self, task, context):
        return OfflineBrain().perceive(task, context)

    def plan(self, observation):
        self.calls += 1
        if self.calls == 1:
            return Action(tool="delete_everything", args={},
                          reason="trust me", proposed_by=self.name)
        if self.calls == 2:
            return Action(tool="apply_fix", args={"fix": "trim_whitespace"},
                          reason="already done, but I insist", proposed_by=self.name)
        return None


rogue = make_agent(RogueBrain())
# A half-dirty input: whitespace already trimmed, so the rogue's second
# proposal (trim_whitespace) is a guaranteed no-op, and its first proposal
# names a tool that does not exist. Neither may ever touch the data.
half_dirty = apply_fix({"text": MESSY}, fix="trim_whitespace")["text"]
rogue_receipt = rogue.run("scrub this csv", {"text": half_dirty})
rd = rogue_receipt.to_dict()
check("rogue brain never crashes the harness", True)
check("unknown tool rejected", rogue_receipt.rejected_actions >= 2,
      f"rejected={rogue_receipt.rejected_actions}")
check("no-op fix rejected",
      any("no-op" in s.get("rejection_reason", "") for s in rd["steps"]))
check("harness stopped without applying anything destructive",
      rogue_receipt.final_state["text"] == half_dirty)
check("rogue run ends stopped (not clean)", rogue_receipt.outcome == "stopped")


class ThrowBrain(Brain):
    name = "thrower"

    def perceive(self, task, context):
        return OfflineBrain().perceive(task, context)

    def plan(self, observation):
        return Action(tool="apply_fix", args={"fix": "normalize_dates"},
                      reason="boom", proposed_by=self.name)


thrown = make_agent(ThrowBrain())
import scrubpilot.tools_csv as tc


def _raiser(state):
    raise RuntimeError("boom")


original_fixer = tc._FIXERS["normalize_dates"]
tc._FIXERS["normalize_dates"] = _raiser
try:
    throw_receipt = thrown.run("scrub this csv", {"text": MESSY})
    check("a tool that raises becomes a rejected action",
          throw_receipt.rejected_actions >= 1
          and throw_receipt.final_state["text"] == MESSY)
finally:
    tc._FIXERS["normalize_dates"] = original_fixer

# ------------------------------------------------------- F. OpenAI brain
section("F. OpenAI-compatible brain (local stub server)")


class _Stub(BaseHTTPRequestHandler):
    response_body = b'{"choices":[{"message":{"content":"{\\"fix\\": \\"normalize_dates\\", \\"reason\\": \\"dates are mixed\\"}"}}]}'

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = self.response_body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _stub_server(body: bytes):
    class Stub(_Stub):
        response_body = body

    srv = HTTPServer(("127.0.0.1", 0), Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


good_body = b'{"choices":[{"message":{"content":"{\\"fix\\": \\"normalize_dates\\", \\"reason\\": \\"dates are mixed\\"}"}}]}'
srv, port = _stub_server(good_body)
try:
    brain = OpenAIBrain(f"http://127.0.0.1:{port}/v1", "test-key", "test-model")
    obs = brain.perceive("scrub", {"text": MESSY})
    action = brain.plan(obs)
    check("llm brain proposes a whitelisted fix",
          action is not None and action.args["fix"] == "normalize_dates"
          and action.proposed_by == "openai-compatible",
          str(action))
    result_llm = scrub(MESSY, brain=brain)
    check("llm-driven run reaches clean", result_llm["outcome"] == "clean")
    check("llm proposals recorded in the receipt",
          any(s.get("action", {}).get("proposed_by") == "openai-compatible"
              for s in result_llm["receipt"]["steps"]))
finally:
    srv.shutdown()

garbage_body = b'{"choices":[{"message":{"content":"I am not JSON at all"}}]}'
srv, port = _stub_server(garbage_body)
try:
    brain = OpenAIBrain(f"http://127.0.0.1:{port}/v1", "test-key", "test-model")
    obs = brain.perceive("scrub", {"text": MESSY})
    action = brain.plan(obs)
    check("garbage llm output degrades to offline policy",
          action is not None and action.proposed_by == "offline-fallback"
          and action.args["fix"] in FIX_WHITELIST,
          str(action))
    result_fb = scrub(MESSY, brain=brain)
    check("degraded run still reaches clean", result_fb["outcome"] == "clean")
finally:
    srv.shutdown()

evil_body = b'{"choices":[{"message":{"content":"{\\"fix\\": \\"rm -rf\\", \\"reason\\": \\"why not\\"}"}}]}'
srv, port = _stub_server(evil_body)
try:
    brain = OpenAIBrain(f"http://127.0.0.1:{port}/v1", "test-key", "test-model")
    obs = brain.perceive("scrub", {"text": MESSY})
    action = brain.plan(obs)
    check("out-of-whitelist llm proposal degrades to offline",
          action is not None and action.proposed_by == "offline-fallback",
          str(action))
finally:
    srv.shutdown()

dead = OpenAIBrain("http://127.0.0.1:1/v1", "k", "m", timeout=2)
obs = dead.perceive("scrub", {"text": MESSY})
action = dead.plan(obs)
check("unreachable llm endpoint degrades to offline",
      action is not None and action.proposed_by == "offline-fallback")
check("offline brain is the default with no env vars",
      isinstance(make_agent().brain, OfflineBrain))

# --------------------------------------------------------------- G. receipts
section("G. receipts")
receipt = receipts.build_receipt(result)
html = receipts.render_html(receipt)
check("html mentions before/after stats", "issues" in html and "rows" in html)
check("html records rejections section", "rejected" in html.lower())
check("html is self-contained (no script tags)", "<script" not in html.lower())
check("html has no external assets", "http://" not in html and "src=" not in html)
check("json receipt round-trips", json.loads(receipts.receipt_json(receipt))["tool"]
      == "ScrubPilot")

# ------------------------------------------------------------------- H. CLI
section("H. CLI")
cli = subprocess.run(
    [sys.executable, "-m", "scrubpilot", "scrub", "examples/messy_orders.csv", "--json"],
    capture_output=True, text=True, timeout=120, cwd=str(ROOT),
)
check("cli scrub exits 0", cli.returncode == 0, cli.stderr[-200:])
try:
    payload = json.loads(cli.stdout)
    ok = payload["outcome"] == "clean"
except Exception as exc:  # noqa: BLE001
    ok = False
    print("   ", exc)
check("cli --json prints the receipt", ok)

demo = subprocess.run(
    [sys.executable, "-m", "scrubpilot", "demo"],
    capture_output=True, text=True, timeout=120, cwd=str(ROOT),
)
check("cli demo exits 0", demo.returncode == 0, demo.stderr[-200:])

# ------------------------------------------------------------------ summary
print(f"\n{'=' * 62}")
print(f"RESULT: {PASSED} passed, {FAILED} failed")
print("=" * 62)
sys.exit(1 if FAILED else 0)
