#!/usr/bin/env python3
"""Interactive query tool for the parsed CIBIL JSON.

Reads the most recent verified `output/*.json` by default (ignoring
`*.UNVERIFIED.json`) and drops into a REPL that supports:

  <term>         universal substring search across accounts, enquiries,
                 addresses, contacts, emails, identification
  score          show CIBIL score section
  personal       show personal details
  summary        high-level inventory (same as the verification summary)
  help           list commands
  quit / q       exit

At a results-list prompt:
  <number>       show detail for that item
  b              back to the main query prompt
  q              quit

At an account detail view:
  full-history   dump every payment-history row (otherwise last 24 months)
  <enter>        back to the results list
  q              quit
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "output"

_TS_RE = re.compile(r"\.(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.json$")
_FLAT_MONTH_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{4})$")
_MONTHS_ASC = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_TO_NUM = {m: i for i, m in enumerate(_MONTHS_ASC, start=1)}


# ---------------------------------------------------------------------------
# file discovery
# ---------------------------------------------------------------------------

def _find_latest_json(folder: Path) -> Path | None:
    """Find the most recent *.json in `folder` that isn't *.UNVERIFIED.json.

    Prefers the max timestamp parsed from the filename; falls back to mtime.
    """
    if not folder.exists():
        return None
    candidates = [p for p in folder.glob("*.json")
                  if ".UNVERIFIED." not in p.name]
    if not candidates:
        return None

    def sort_key(p: Path) -> tuple[str, float]:
        m = _TS_RE.search(p.name)
        return (m.group(1) if m else "", p.stat().st_mtime)

    return max(candidates, key=sort_key)


# ---------------------------------------------------------------------------
# table rendering (stdlib only)
# ---------------------------------------------------------------------------

def _visible_len(s: str) -> int:
    # strip ANSI escapes if any (we don't add them, but be safe)
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _visible_len(s))


def _render_kv_table(pairs: list[tuple[str, str]], max_value_width: int = 60) -> str:
    """Render a list of (label, value) pairs as a simple 2-column table."""
    if not pairs:
        return "  (empty)"
    label_w = max(_visible_len(k) for k, _ in pairs)
    lines = []
    for k, v in pairs:
        v = v if v else "-"
        # wrap long values on commas / spaces
        if _visible_len(v) <= max_value_width:
            lines.append(f"  {_pad(k, label_w)}  {v}")
        else:
            lines.append(f"  {_pad(k, label_w)}  {v[:max_value_width]}")
            rem = v[max_value_width:]
            while rem:
                lines.append(f"  {' ' * label_w}  {rem[:max_value_width]}")
                rem = rem[max_value_width:]
    return "\n".join(lines)


def _render_rows_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a list of rows as an ASCII grid without fancy borders."""
    if not rows:
        return "  (no rows)"
    cols = len(headers)
    widths = [_visible_len(h) for h in headers]
    for r in rows:
        for i in range(cols):
            widths[i] = max(widths[i], _visible_len(r[i] if i < len(r) else ""))
    sep = "  "
    out = [sep.join(_pad(h, widths[i]) for i, h in enumerate(headers))]
    out.append(sep.join("-" * w for w in widths))
    for r in rows:
        out.append(sep.join(
            _pad(r[i] if i < len(r) else "", widths[i]) for i in range(cols)))
    return "\n".join("  " + ln for ln in out)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _match(term: str, *fields: str) -> bool:
    t = term.lower()
    return any(t in (f or "").lower() for f in fields)


def _search(data: dict[str, Any], term: str) -> list[dict[str, Any]]:
    """Return a flat list of hits with section, index, and a one-liner summary."""
    hits: list[dict[str, Any]] = []

    for i, a in enumerate(data.get("accounts", [])):
        if _match(term, a.get("member_name"), a.get("account_number"),
                   a.get("account_type"), a.get("status")):
            ad = a.get("account_details", {})
            lim = ad.get("credit_limit") or ad.get("sanctioned_amount") or "-"
            hits.append({
                "section": "accounts",
                "index": i,
                "summary": [
                    a.get("member_name", "-"),
                    a.get("account_type", "-"),
                    a.get("status", "-"),
                    f"#{a.get('account_number','-')}",
                    f"opened {ad.get('date_opened_disbursed','-')}",
                    f"{lim} lim",
                ],
            })

    for i, e in enumerate(data.get("enquiry_details", [])):
        if _match(term, e.get("member_name"), e.get("enquiry_purpose")):
            hits.append({
                "section": "enquiries",
                "index": i,
                "summary": [
                    e.get("member_name", "-"),
                    e.get("enquiry_purpose", "-"),
                    e.get("date_of_enquiry", "-"),
                ],
            })

    for i, ad in enumerate(data.get("address_details", [])):
        if _match(term, ad.get("address"), ad.get("category")):
            summary = ad.get("address", "-")
            hits.append({
                "section": "addresses",
                "index": i,
                "summary": [
                    ad.get("category", "-"),
                    summary[:60] + ("..." if len(summary) > 60 else ""),
                    f"reported {ad.get('date_reported','-')}",
                ],
            })

    for i, c in enumerate(data.get("contact_details", [])):
        if _match(term, c.get("telephone_number"), c.get("telephone_number_type")):
            hits.append({
                "section": "contacts",
                "index": i,
                "summary": [
                    c.get("telephone_number_type", "-"),
                    c.get("telephone_number", "-"),
                    f"ext {c.get('telephone_extension','-')}",
                ],
            })

    for i, em in enumerate(data.get("email_details", [])):
        if _match(term, em):
            hits.append({
                "section": "emails",
                "index": i,
                "summary": [em],
            })

    for i, idn in enumerate(data.get("identification_details", [])):
        if _match(term, idn.get("identification_type"), idn.get("id_number")):
            hits.append({
                "section": "identification",
                "index": i,
                "summary": [
                    idn.get("identification_type", "-"),
                    idn.get("id_number", "-"),
                ],
            })

    return hits


# ---------------------------------------------------------------------------
# detail views
# ---------------------------------------------------------------------------

def _detail_account(a: dict[str, Any], show_full_history: bool = False) -> str:
    ad = a.get("account_details", {})
    header = (
        f"{'=' * 72}\n"
        f"ACCOUNT  {a.get('member_name','-')} · {a.get('account_type','-')} "
        f"· {a.get('status','-').upper()}\n"
        f"{'=' * 72}"
    )
    top_pairs = [
        ("Member", a.get("member_name", "-")),
        ("Account Type", a.get("account_type", "-")),
        ("Ownership", a.get("ownership", "-")),
        ("Account Number", a.get("account_number", "-")),
        ("Status", a.get("status", "-")),
    ]
    # Render all account_details in insertion order (preserves HTML order).
    ad_pairs = [(_human_label(k), v) for k, v in ad.items()]

    ps = a.get("payment_status", {})
    ps_pairs = [(_human_label(k), v) for k, v in ps.items()]

    parts = [
        header,
        "",
        "Identity",
        "-" * 30,
        _render_kv_table(top_pairs),
        "",
        "Account Details",
        "-" * 30,
        _render_kv_table(ad_pairs),
    ]
    if ps_pairs:
        parts.extend([
            "",
            "Payment Status",
            "-" * 30,
            _render_kv_table(ps_pairs),
        ])
    # Payment history
    ph = a.get("payment_history", [])
    parts.extend([
        "",
        f"Payment History  ({'full dump' if show_full_history else 'last 24 months'})",
        "-" * 30,
        _render_payment_history(ph, full=show_full_history),
    ])
    return "\n".join(parts)


def _human_label(key: str) -> str:
    return key.replace("_", " ").title()


def _render_payment_history(ph: list[dict[str, str]], full: bool) -> str:
    if not ph:
        return "  (no payment history)"
    # Build a {year: {month: value}} grid.
    grid: dict[int, dict[str, str]] = {}
    for entry in ph:
        m = _FLAT_MONTH_RE.match(entry.get("month", ""))
        if not m:
            continue
        yr, mn = int(m.group(2)), m.group(1)
        grid.setdefault(yr, {})[mn] = entry.get("value", "")
    if not grid:
        return "  (unparsable payment history)"

    years = sorted(grid.keys(), reverse=True)
    if not full:
        years = years[:3]  # newest 3 years ~ 24-36 months

    headers = ["Year"] + _MONTHS_ASC
    rows: list[list[str]] = []
    for y in years:
        row = [str(y)]
        for mon in _MONTHS_ASC:
            row.append(grid[y].get(mon, ""))
        rows.append(row)

    return _render_rows_table(headers, rows)


def _detail_enquiry(e: dict[str, Any]) -> str:
    return (
        f"{'=' * 50}\nENQUIRY\n{'=' * 50}\n"
        + _render_kv_table([
            ("Member", e.get("member_name", "-")),
            ("Date", e.get("date_of_enquiry", "-")),
            ("Purpose", e.get("enquiry_purpose", "-")),
        ])
    )


def _detail_address(a: dict[str, Any]) -> str:
    return (
        f"{'=' * 50}\nADDRESS\n{'=' * 50}\n"
        + _render_kv_table([(_human_label(k), v) for k, v in a.items()])
    )


def _detail_contact(c: dict[str, Any]) -> str:
    return (
        f"{'=' * 50}\nCONTACT\n{'=' * 50}\n"
        + _render_kv_table([(_human_label(k), v) for k, v in c.items()])
    )


def _detail_email(em: str) -> str:
    return (f"{'=' * 50}\nEMAIL\n{'=' * 50}\n  {em}")


def _detail_identification(idn: dict[str, Any]) -> str:
    return (
        f"{'=' * 50}\nIDENTIFICATION\n{'=' * 50}\n"
        + _render_kv_table([(_human_label(k), v) for k, v in idn.items()])
    )


_DETAIL_DISPATCH: dict[str, Callable[..., str]] = {
    "accounts": _detail_account,
    "enquiries": _detail_enquiry,
    "addresses": _detail_address,
    "contacts": _detail_contact,
    "emails": _detail_email,
    "identification": _detail_identification,
}


def _get_record(data: dict[str, Any], section: str, index: int) -> Any:
    key_map = {
        "accounts": "accounts",
        "enquiries": "enquiry_details",
        "addresses": "address_details",
        "contacts": "contact_details",
        "emails": "email_details",
        "identification": "identification_details",
    }
    return data[key_map[section]][index]


# ---------------------------------------------------------------------------
# top-level summary commands (score / personal / summary)
# ---------------------------------------------------------------------------

def _show_score(data: dict[str, Any]) -> None:
    s = data.get("cibil_score", {})
    print()
    print("=" * 50)
    print("CIBIL SCORE")
    print("=" * 50)
    print(_render_kv_table([
        ("Score", str(s.get("score", "-"))),
        ("As Of", s.get("as_of_date", "-")),
    ]))
    print()


def _show_personal(data: dict[str, Any]) -> None:
    p = data.get("personal_details", {})
    emp = data.get("employment_details", {})
    print()
    print("=" * 50)
    print("PERSONAL DETAILS")
    print("=" * 50)
    print(_render_kv_table([(_human_label(k), str(v) if v else "-")
                            for k, v in p.items()]))
    print()
    print("=" * 50)
    print("EMPLOYMENT DETAILS")
    print("=" * 50)
    print(_render_kv_table([(_human_label(k), str(v) if v else "-")
                            for k, v in emp.items()]))
    print()


def _show_summary(data: dict[str, Any]) -> None:
    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    rep = data.get("report", {})
    score = data.get("cibil_score", {})
    p = data.get("personal_details", {})
    pairs = [
        ("Consumer", p.get("name", "-")),
        ("Date of Birth", p.get("date_of_birth", "-")),
        ("Report Date", rep.get("date", "-")),
        ("Control Number", rep.get("control_number", "-")),
        ("CIBIL Score", f"{score.get('score','-')} (as of {score.get('as_of_date','-')})"),
        ("Total Accounts", str(len(data.get("accounts", [])))),
        ("  Open", str(sum(1 for a in data.get("accounts", []) if a.get("status") == "open"))),
        ("  Closed", str(sum(1 for a in data.get("accounts", []) if a.get("status") == "closed"))),
        ("Enquiries", str(len(data.get("enquiry_details", [])))),
        ("Addresses", str(len(data.get("address_details", [])))),
        ("Contacts", str(len(data.get("contact_details", [])))),
        ("Emails", str(len(data.get("email_details", [])))),
    ]
    print(_render_kv_table(pairs))

    # Per-type account breakdown
    breakdown: dict[tuple[str, str], int] = {}
    for a in data.get("accounts", []):
        key = (a.get("status", "?"), a.get("account_type", "?"))
        breakdown[key] = breakdown.get(key, 0) + 1
    if breakdown:
        print()
        print("Account breakdown (status / type):")
        rows = [[s, t, str(n)] for (s, t), n in sorted(breakdown.items())]
        print(_render_rows_table(["Status", "Type", "Count"], rows))
    print()


# ---------------------------------------------------------------------------
# interactive REPL
# ---------------------------------------------------------------------------

_HELP = """Commands:
  <term>          universal substring search (e.g. 'sbi', 'hdfc', 'credit card')
  score           show CIBIL score
  personal        show personal + employment details
  summary         high-level inventory
  help            show this message
  quit / q        exit
"""


def _print_hit_list(hits: list[dict[str, Any]]) -> None:
    by_section: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for gi, h in enumerate(hits, start=1):
        by_section.setdefault(h["section"], []).append((gi, h))
    for section in ["accounts", "enquiries", "addresses",
                     "contacts", "emails", "identification"]:
        items = by_section.get(section, [])
        if not items:
            continue
        print(f"\n{section.upper()}  ({len(items)} match{'es' if len(items)!=1 else ''})")
        for gi, h in items:
            line = f"  {gi:2d}. " + "  ".join(str(x) for x in h["summary"])
            print(line)


def _handle_selection(data: dict[str, Any], hits: list[dict[str, Any]]) -> None:
    """Inner loop: user picks a number, we show detail, then back to the list."""
    while True:
        raw = input("\nSelect [1-{n}], 'b' back, 'q' quit > ".format(n=len(hits))).strip()
        if raw in ("q", "quit", "exit"):
            raise SystemExit(0)
        if raw in ("", "b", "back"):
            return
        if not raw.isdigit():
            print("  (enter a number, 'b', or 'q')")
            continue
        n = int(raw)
        if not (1 <= n <= len(hits)):
            print(f"  (out of range; expected 1..{len(hits)})")
            continue
        hit = hits[n - 1]
        record = _get_record(data, hit["section"], hit["index"])
        renderer = _DETAIL_DISPATCH[hit["section"]]
        print()
        print(renderer(record))
        # Account has an additional sub-command: full-history.
        if hit["section"] == "accounts":
            while True:
                sub = input("\n[enter] back, 'full-history' dump all, 'q' quit > ").strip()
                if sub in ("q", "quit"):
                    raise SystemExit(0)
                if sub in ("", "b", "back"):
                    break
                if sub in ("full-history", "full", "fh"):
                    print()
                    print(renderer(record, show_full_history=True))
                    continue
                print("  (unknown; try 'full-history', 'b', or 'q')")
        else:
            input("\n[enter] to continue ")
        # Re-print the hit list so the user has context for the next pick
        # (after a long detail view the original list will have scrolled off).
        print()
        _print_hit_list(hits)


def _repl(data: dict[str, Any], source: Path) -> None:
    print(f"\nLoaded {source}")
    print(f"  {len(data.get('accounts', []))} accounts, "
          f"{len(data.get('enquiry_details', []))} enquiries, "
          f"CIBIL score {data.get('cibil_score',{}).get('score','-')}")
    print("Type 'help' for commands, 'q' to quit.\n")

    while True:
        try:
            raw = input("query > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            continue
        cmd = raw.lower()
        if cmd in ("q", "quit", "exit"):
            return
        if cmd in ("help", "?"):
            print(_HELP)
            continue
        if cmd == "score":
            _show_score(data)
            continue
        if cmd == "personal":
            _show_personal(data)
            continue
        if cmd == "summary":
            _show_summary(data)
            continue
        # Fallthrough = substring search
        hits = _search(data, raw)
        if not hits:
            print(f"  (no matches for {raw!r})")
            continue
        _print_hit_list(hits)
        try:
            _handle_selection(data, hits)
        except SystemExit:
            return


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--file", type=Path, default=None,
        help=("path to the parsed CIBIL JSON to query. "
              "Defaults to the most recent output/*.json."),
    )
    ap.add_argument(
        "--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR,
        help=f"folder to scan for latest JSON (default: {_DEFAULT_OUTPUT_DIR}).",
    )
    args = ap.parse_args(argv)

    if args.file is not None:
        src = args.file.resolve()
        if not src.exists():
            print(f"ERROR: not found: {src}", file=sys.stderr)
            return 2
    else:
        src = _find_latest_json(args.output_dir.resolve())
        if src is None:
            print(f"ERROR: no verified *.json files found in {args.output_dir}.",
                  file=sys.stderr)
            print("       Run `make` first to generate one.", file=sys.stderr)
            return 2

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: {src} is not valid JSON: {e}", file=sys.stderr)
        return 2

    _repl(data, src)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
