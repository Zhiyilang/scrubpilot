"""CSV tools: profile, apply_fix, verify.

The tools are plain functions over a `state` dict: {"text": str}. Each fix is
one whitelisted transformation; anything not on the list cannot be applied,
by construction. Every transformation returns a NEW text — states are never
mutated in place, so a rejected action can never leave partial damage.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import List, Tuple

FIX_WHITELIST = (
    "trim_whitespace",
    "drop_empty_rows",
    "drop_duplicate_rows",
    "normalize_dates",
    "normalize_amounts",
    "snake_case_headers",
    "drop_all_empty_column",
)

_MONEY_JUNK = re.compile(r"[^\d.\-]")


# --------------------------------------------------------------------------
# row handling (empty rows are DATA — that is one of the things we detect)
# --------------------------------------------------------------------------

def _raw_rows(text: str) -> List[List[str]]:
    return list(csv.reader(io.StringIO(text)))


def _is_empty(row: List[str]) -> bool:
    return not any(c.strip() for c in row)


def _content_rows(text: str) -> List[List[str]]:
    """All rows including empty ones; leading blank lines skipped."""
    rows = _raw_rows(text.replace("\ufeff", ""))   # Excel BOM defense
    i = 0
    while i < len(rows) and _is_empty(rows[i]):
        i += 1
    return rows[i:]


def _to_text(rows: List[List[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def _norm_date(cell: str) -> Tuple[str, bool]:
    """Return (iso_date, changed). Unparseable cells are left untouched."""
    s = cell.strip()
    if not s:
        return cell, False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s, False
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), True
        except ValueError:
            return cell, False
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), True
        except ValueError:
            return cell, False
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, a, b).isoformat(), True
        except ValueError:
            return cell, False
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2})", s)
    if m:
        a, b, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(2000 + yy, a, b).isoformat(), True
        except ValueError:
            return cell, False
    return cell, False


def _norm_amount(cell: str) -> Tuple[str, bool]:
    """'$1,234.50' -> '1234.50' ; '(89.00)' -> '-89.00' ; 'USD 12' -> '12.00'."""
    s = cell.strip()
    if not s:
        return cell, False
    neg = s.startswith("(") and s.endswith(")")
    cleaned = _MONEY_JUNK.sub("", s)
    if not cleaned or cleaned in {"-", "."}:
        return cell, False
    try:
        value = float(cleaned)
    except ValueError:
        return cell, False
    if neg:
        value = -value
    out = f"{value:.2f}"
    return out, out != s


def _snake(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.strip())
    s = re.sub(r"[\s\-]+", "_", s)
    return s.lower().strip("_")


def _looks_like_date_col(col: str, cell: str) -> bool:
    if re.search(r"date|day|time", col, re.I):
        return True
    return bool(re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", cell))


def _looks_like_amount_col(col: str, cell: str) -> bool:
    if re.search(r"amount|price|total|cost|fee|balance|tax|subtotal", col, re.I):
        return True
    return bool(re.search(r"[($€£¥]", cell)) or bool(re.fullmatch(r"-?[\d,]+\.\d{2}", cell))


# --------------------------------------------------------------------------
# profile (pure read)
# --------------------------------------------------------------------------

def profile_csv(state: dict) -> dict:
    """What is wrong with this CSV? Returns a fresh dict, never mutates."""
    text = state["text"]
    rows = _content_rows(text)
    if not rows:
        return {"ok": True, "issues": [], "summary": "empty file",
                "row_count": 0, "column_count": 0}
    header = rows[0]
    data = rows[1:]
    issues: List[str] = []

    # header hygiene
    if any(h != h.strip() for h in header):
        issues.append("header_whitespace")
    if any(re.search(r"[\s\-]", h) for h in header):
        issues.append("header_not_snake_case")
    if len({h.strip() for h in header}) != len(header):
        issues.append("duplicate_headers")

    # cell / row hygiene
    if any(c != c.strip() for r in data for c in r):
        issues.append("cell_whitespace")
    empty_rows = sum(1 for r in data if _is_empty(r))
    if empty_rows:
        issues.append("empty_rows")
    non_empty = [r for r in data if not _is_empty(r)]
    dupes = len(non_empty) - len({tuple(r) for r in non_empty})
    if dupes:
        issues.append("duplicate_rows")

    # cell-format hygiene
    date_changes = amount_changes = 0
    for r in non_empty:
        for i, cell in enumerate(r):
            col = header[i] if i < len(header) else ""
            if _looks_like_date_col(col, cell):
                _, changed = _norm_date(cell)
                date_changes += changed
            elif _looks_like_amount_col(col, cell):
                _, changed = _norm_amount(cell)
                amount_changes += changed
    if date_changes:
        issues.append("mixed_date_formats")
    if amount_changes:
        issues.append("messy_amounts")

    # all-empty columns
    ncols = len(header)
    for i in range(ncols):
        if all((i >= len(r)) or (not r[i].strip()) for r in non_empty):
            issues.append(f"all_empty_column:{header[i].strip()}")
            break

    return {
        "ok": not issues,
        "issues": issues,
        "summary": (
            f"{len(non_empty)} rows x {ncols} cols, {len(issues)} issue(s): "
            + (", ".join(issues) if issues else "clean")
        ),
        "row_count": len(non_empty),
        "column_count": ncols,
        "date_cells_to_normalize": date_changes,
        "amount_cells_to_normalize": amount_changes,
    }


# --------------------------------------------------------------------------
# fixes (each returns NEW text)
# --------------------------------------------------------------------------

def _fix_trim(state: dict) -> str:
    return _to_text([[c.strip() for c in r] for r in _content_rows(state["text"])])


def _fix_drop_empty_rows(state: dict) -> str:
    rows = _content_rows(state["text"])
    return _to_text([rows[0]] + [r for r in rows[1:] if not _is_empty(r)])


def _fix_drop_duplicates(state: dict) -> str:
    rows = _content_rows(state["text"])
    seen, kept = set(), [rows[0]]
    for r in rows[1:]:
        key = tuple(r)
        if key in seen:
            continue
        seen.add(key)
        kept.append(r)
    return _to_text(kept)


def _fix_normalize_dates(state: dict) -> str:
    rows = _content_rows(state["text"])
    if not rows:
        return state["text"]
    header = rows[0]
    out = [header]
    for r in rows[1:]:
        new = list(r)
        for i, cell in enumerate(r):
            col = header[i] if i < len(header) else ""
            if _looks_like_date_col(col, cell):
                new[i], _ = _norm_date(cell)
        out.append(new)
    return _to_text(out)


def _fix_normalize_amounts(state: dict) -> str:
    rows = _content_rows(state["text"])
    if not rows:
        return state["text"]
    header = rows[0]
    out = [header]
    for r in rows[1:]:
        new = list(r)
        for i, cell in enumerate(r):
            col = header[i] if i < len(header) else ""
            if _looks_like_amount_col(col, cell):
                new[i], _ = _norm_amount(cell)
        out.append(new)
    return _to_text(out)


def _fix_snake_headers(state: dict) -> str:
    rows = _content_rows(state["text"])
    if not rows:
        return state["text"]
    header, seen, new_header = rows[0], set(), []
    for h in header:
        s = _snake(h) or "col"
        base, n = s, 2
        while s in seen:
            s = f"{base}_{n}"
            n += 1
        seen.add(s)
        new_header.append(s)
    return _to_text([new_header] + rows[1:])


def _fix_drop_all_empty_column(state: dict, column: str = "") -> str:
    rows = _content_rows(state["text"])
    if not rows:
        return state["text"]
    header = [h.strip() for h in rows[0]]
    if column not in header:
        non_empty = [r for r in rows[1:] if not _is_empty(r)]
        for i, h in enumerate(header):
            if all((i >= len(r)) or (not r[i].strip()) for r in non_empty):
                column = h
                break
    if column not in header:
        return state["text"]
    idx = header.index(column)
    return _to_text([[c for i, c in enumerate(r) if i != idx] for r in rows])


_FIXERS = {
    "trim_whitespace": _fix_trim,
    "drop_empty_rows": _fix_drop_empty_rows,
    "drop_duplicate_rows": _fix_drop_duplicates,
    "normalize_dates": _fix_normalize_dates,
    "normalize_amounts": _fix_normalize_amounts,
    "snake_case_headers": _fix_snake_headers,
    "drop_all_empty_column": _fix_drop_all_empty_column,
}


def apply_fix(state: dict, fix: str = "", **kwargs) -> dict:
    """Apply one whitelisted fix. Unknown fix -> KeyError (harness rejects)."""
    if fix not in _FIXERS:
        raise KeyError(f"fix not in whitelist: {fix}")
    return {"text": _FIXERS[fix](state, **kwargs)}


def verify_csv(state: dict) -> dict:
    return profile_csv(state)


def cell_count(text: str) -> int:
    rows = _content_rows(text)
    return sum(len(r) for r in rows)
