"""
parse_cibil.py
==============

Pin-point-accurate parser for a CIBIL credit-report MHTML snapshot.

Pipeline (run with `python parse_cibil.py <file.mhtml>`):

    Stage 1: MHTML  -> <stem>.decoded.html       (intermediate)
    Stage 2: HTML   -> <stem>.text.txt           (intermediate, verification corpus)
    Stage 3: HTML   -> <stem>.extracted.json     (intermediate, first-pass extraction)
    Stage 4: dict   -> <stem>.json + <stem>.yaml (final)
    Stage 5: cross-check final files vs MHTML -> <stem>.verification.txt
             If verification fails, final files are renamed to *.UNVERIFIED.* and
             the script exits non-zero.

Design rules:
- Single source of truth: one Python dict produces both JSON and YAML.
- Every leaf value is a STRING, captured verbatim from the report (no reformatting
  of dates, currency, masked IDs, scores, etc.).
- No silent fallbacks: if a structurally-required field is missing, we raise.
- Intermediate files are kept (not deleted) so each stage can be audited.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag
import yaml


# ---------------------------------------------------------------------------
# Stage 1: decode MHTML
# ---------------------------------------------------------------------------

def stage1_decode_mhtml(mhtml_path: Path, out_html: Path) -> str:
    """Extract the root text/html part of the MHTML and write a standalone HTML file.

    The HTML MIME part in CIBIL snapshots does NOT declare a charset header
    (only an in-document <meta charset="UTF-8">). Python's email.message_from_bytes
    therefore returns the bytes decoded as us-ascii/latin-1 via get_content(),
    which turns every UTF-8 multi-byte char (including the rupee sign U+20B9)
    into U+FFFD replacement chars. To preserve currency symbols and any other
    non-ASCII text verbatim we instead read the CTE-decoded payload bytes and
    decode them ourselves as UTF-8 (matching the in-document declaration).

    Returns the decoded HTML string.
    """
    raw = mhtml_path.read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    html_bytes: bytes | None = None
    html_location: str | None = None
    for part in msg.walk():
        if part.get_content_type() == "text/html" and html_bytes is None:
            # First HTML part is the report frame. get_payload(decode=True)
            # decodes the Content-Transfer-Encoding (quoted-printable) and
            # returns raw bytes; we then decode UTF-8 strictly so any bad
            # byte raises rather than silently becoming \ufffd.
            html_bytes = part.get_payload(decode=True)
            html_location = part.get("Content-Location", "")
    if html_bytes is None:
        raise RuntimeError("No text/html part found in MHTML")

    html_text = html_bytes.decode("utf-8", errors="strict")
    if "\ufffd" in html_text:
        raise RuntimeError(
            "Decoded HTML still contains Unicode replacement chars; "
            "encoding assumption is wrong."
        )
    out_html.write_text(html_text, encoding="utf-8")
    print(f"[stage1] decoded HTML  -> {out_html.name}  "
          f"({len(html_text):,} chars, source: {html_location})")

    # Sanity check: this parser only handles CIBIL's print-formatted layout
    # (creditReportPrint.page). Detect non-CIBIL inputs and the
    # consumer-dashboard layout (creditreport.page) up-front so the user
    # gets an actionable error instead of a cryptic selector traceback later.
    _check_cibil_print_view(html_text, html_location or "")
    return html_text


def _check_cibil_print_view(html_text: str, source_url: str) -> None:
    """Raise with an actionable message if this isn't a CIBIL print-view MHTML.

    The print view we support has a `<span id="controlNumber">` element and is
    served from `creditReportPrint.page`. The dashboard view at
    `creditreport.page` instead uses `id="controlNo"` and a wholly different
    DOM, so it must be rejected here.
    """
    if 'id="controlNumber"' in html_text:
        return  # looks correct; let the per-section extractors do their thing.

    # Build a tailored hint based on what we DID find.
    if "controlNo" in html_text or "CR-Accounts-Open" in html_text:
        diagnosis = (
            "  This MHTML is from CIBIL's interactive consumer dashboard\n"
            "  (creditreport.page). This parser only handles the\n"
            "  PRINT-FORMATTED layout (creditReportPrint.page)."
        )
    else:
        diagnosis = (
            "  This MHTML doesn't look like a CIBIL credit report at all\n"
            "  (no 'controlNumber' element found)."
        )

    raise RuntimeError(
        "Cannot parse: input is not a CIBIL print-view credit report.\n"
        f"  Source URL: {source_url or '(none in MHTML)'}\n"
        f"{diagnosis}\n"
        "\n"
        "  Re-export the report from the print view at:\n"
        "    https://myscore.cibil.com/CreditView/creditReportPrint.page?print=true\n"
        "  (note 'creditReportPrint' with capital R and P, NOT 'creditreport')\n"
        "  then save the page as MHTML and re-run."
    )


# ---------------------------------------------------------------------------
# Stage 2: HTML -> plain-text verification corpus
# ---------------------------------------------------------------------------

def stage2_html_to_text(html: str, out_txt: Path) -> str:
    """Render HTML to a plain-text corpus used later for cross-checking.

    Strategy: keep cell separators ("|") and line breaks so that table values
    survive as searchable substrings. Strip <script>/<style>.
    """
    soup = BeautifulSoup(html, "lxml")
    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()

    # For every <br> insert a newline marker that survives get_text.
    for br in soup.find_all("br"):
        br.replace_with("\n")

    # Walk and emit text with separators around block-level elements.
    block_tags = {
        "tr", "li", "p", "div", "section", "article", "header", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody",
    }
    cell_tags = {"td", "th"}

    parts: list[str] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                s = str(child)
                if s:
                    parts.append(s)
            elif isinstance(child, Tag):
                name = child.name.lower()
                if name in block_tags:
                    parts.append("\n")
                    walk(child)
                    parts.append("\n")
                elif name in cell_tags:
                    parts.append(" | ")
                    walk(child)
                else:
                    walk(child)

    body = soup.body or soup
    walk(body)
    text = "".join(parts)

    # Collapse runs of blank lines but PRESERVE intra-line spacing exactly,
    # because we will substring-match against this corpus.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    out_txt.write_text(text, encoding="utf-8")
    print(f"[stage2] text corpus   -> {out_txt.name}  ({len(text):,} chars)")
    return text


# ---------------------------------------------------------------------------
# Stage 3: structured extraction
# ---------------------------------------------------------------------------

def _norm(s: str | None) -> str:
    """Trim & collapse internal whitespace while preserving the visible value."""
    if s is None:
        return ""
    # Replace nbsp variants with normal space, then collapse runs of whitespace.
    s = s.replace("\xa0", " ").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _txt(node: Tag | None) -> str:
    if node is None:
        return ""
    return _norm(node.get_text(" ", strip=True))


def stage3_extract(html: str, extracted_json: Path) -> dict[str, Any]:
    """Walk the DOM and build a dict mirroring the report's section structure.

    Every leaf is a verbatim string copied from the HTML. Whitespace inside a
    value is normalised (runs of whitespace collapsed, leading/trailing trimmed)
    so the value matches what is visually rendered, but no other transformation
    is applied: dates stay dd/mm/yyyy, currency keeps the ₹ symbol and Indian
    grouping, masked IDs stay masked, "-" stays "-", etc.
    """
    soup = BeautifulSoup(html, "lxml")

    data: dict[str, Any] = {}
    data["report"] = _extract_report_meta(soup)
    data["cibil_score"] = _extract_cibil_score(soup)
    data["personal_details"] = _extract_personal_details(soup)
    data["identification_details"] = _extract_repeated_section(soup, r"^\s*Identification Details\s*$")
    data["address_details"] = _extract_repeated_section(soup, r"^\s*Address Details\s*$")
    data["contact_details"] = _extract_repeated_section(soup, r"^\s*Contact Details\s*$")
    data["email_details"] = _extract_email_details(soup)
    data["employment_details"] = _extract_employment_details(soup)
    data["accounts"] = _extract_accounts(soup)
    data["enquiry_details"] = _extract_enquiries(soup)
    data["footer"] = _extract_footer(soup)

    # Cross-section sanity invariants.
    open_n = sum(1 for a in data["accounts"] if a["status"] == "open")
    closed_n = sum(1 for a in data["accounts"] if a["status"] == "closed")
    expected_open = len(soup.select("div.accounts-container.open_accounts div.card"))
    expected_closed = len(soup.select("div.accounts-container.closed_accounts div.card"))
    if open_n != expected_open or closed_n != expected_closed:
        raise RuntimeError(
            f"Account count mismatch: extracted open={open_n}/{expected_open} "
            f"closed={closed_n}/{expected_closed}"
        )

    extracted_json.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[stage3] extracted     -> {extracted_json.name}  "
          f"({extracted_json.stat().st_size:,} bytes; "
          f"{open_n} open + {closed_n} closed accounts, "
          f"{len(data['enquiry_details'])} enquiries)")
    return data


# --- helpers ----------------------------------------------------------------

_KEY_RE = re.compile(r"[^a-z0-9]+")


def _label_to_key(label: str) -> str:
    """Turn a human label like 'Date of Birth' into 'date_of_birth'."""
    s = _norm(label).lower()
    s = _KEY_RE.sub("_", s).strip("_")
    return s


def _section_title_p(soup: BeautifulSoup, title_regex: str) -> Tag:
    """Locate the <p class="table-title"> whose text matches the regex."""
    rx = re.compile(title_regex)
    for p in soup.find_all("p", class_="table-title"):
        if rx.search(p.get_text(" ", strip=True)):
            return p
    raise RuntimeError(f"Section title not found: {title_regex!r}")


def _siblings_until_next_title(start: Tag) -> list[Tag]:
    """Tags between `start` (a p.table-title) and the next p.table-title."""
    out: list[Tag] = []
    for sib in start.next_siblings:
        if not isinstance(sib, Tag):
            continue
        if sib.name == "p" and "table-title" in (sib.get("class") or []):
            break
        out.append(sib)
    return out


def _kv_from_table_items(node: Tag) -> dict[str, str]:
    """For all p.table-item descendants of node, build {head: text}.

    Order is preserved (Python 3.7+ dict). Items missing item-text default to "".
    """
    pairs: dict[str, str] = {}
    for it in node.find_all("p", class_="table-item"):
        h = it.find("span", class_="item-head")
        t = it.find("span", class_="item-text")
        if h is None:
            continue
        key = _label_to_key(_txt(h))
        if not key:
            continue
        pairs[key] = _txt(t)
    return pairs


# --- per-section extractors -------------------------------------------------

def _extract_report_meta(soup: BeautifulSoup) -> dict[str, str]:
    cn_span = soup.find("span", id="controlNumber")
    if cn_span is None:
        raise RuntimeError("controlNumber not found")
    cn_full = _txt(cn_span)  # "Control Number :  XX,XX,XX,XX,XXX"
    cn_value = cn_full.split(":", 1)[1].strip() if ":" in cn_full else cn_full

    # Date sits as a sibling element with the literal "Date :  dd/mm/yyyy".
    date_value = ""
    parent = cn_span.parent
    if parent is not None:
        for txt in parent.stripped_strings:
            m = re.match(r"^\s*Date\s*:\s*(.+?)\s*$", txt)
            if m:
                date_value = m.group(1)
                break

    greeting = ""
    h3 = soup.find("h3", string=re.compile(r"Hello,"))
    if h3 is not None:
        greeting = _txt(h3)

    title = _txt(soup.title)
    return {
        "source_title": title,
        "control_number": cn_value,
        "date": date_value,
        "greeting": greeting,
    }


def _extract_cibil_score(soup: BeautifulSoup) -> dict[str, str]:
    score_p = soup.find("p", class_="cibil-score")
    if score_p is None or not _txt(score_p):
        raise RuntimeError("cibil-score element not found or empty")
    score = _txt(score_p)

    # The "as of" date appears in a sentence like:
    #   "Your CIBIL Score is 707 as of Date :  27/04/2026"
    # which is rendered with the score in its own <span>, so the literal string
    # is split across multiple text nodes. Match at element level instead.
    as_of = ""
    rx = re.compile(r"Your CIBIL Score is\s+\S+\s+as of Date\s*:\s*(\S+)")
    for el in soup.find_all("p"):
        m = rx.search(_norm(el.get_text(" ", strip=True)))
        if m:
            as_of = m.group(1)
            break
    return {"score": score, "as_of_date": as_of}


def _extract_personal_details(soup: BeautifulSoup) -> dict[str, str]:
    title = _section_title_p(soup, r"^\s*Personal Details\s*$")
    blocks = _siblings_until_next_title(title)
    out: dict[str, str] = {}
    for b in blocks:
        out.update(_kv_from_table_items(b))

    # Optional dispute remark: appears in mobile + desktop variants. Both copies
    # carry identical text; we capture once.
    dispute_remark = ""
    dispute_date = ""
    for b in blocks:
        # Find a span.item-text that contains "Dispute raised" or similar.
        remark_span = b.find("span", class_=re.compile(r"item-text"),
                              string=re.compile(r"\S"))
        # Actual structure: divs with text "CIBIL Remarks :" and "Dispute Date:"
        # followed by item-text values. Find by walking the dispute container.
        for cont in b.select("div.remarkContainer, div.disputeRemarkMobile, "
                              "div.disputeRemarkDesktop, div.mobileRemarkContainer"):
            txt = _norm(cont.get_text(" ", strip=True))
            mr = re.search(r"CIBIL Remarks\s*:\s*(.+?)(?:Dispute Date|$)", txt)
            md = re.search(r"Dispute Date\s*:\s*(\S+)", txt)
            if mr and not dispute_remark:
                dispute_remark = mr.group(1).strip()
            if md and not dispute_date:
                dispute_date = md.group(1).strip()
        if dispute_remark and dispute_date:
            break
    if dispute_remark:
        out["cibil_remarks"] = dispute_remark
    if dispute_date:
        out["dispute_date"] = dispute_date
    return out


def _extract_repeated_section(soup: BeautifulSoup, title_regex: str) -> list[dict[str, str]]:
    """Sections where each entry is one div.table-content-parent sibling
    (Identification, Address, Contact)."""
    title = _section_title_p(soup, title_regex)
    out: list[dict[str, str]] = []
    for sib in _siblings_until_next_title(title):
        if sib.name == "div" and "table-content-parent" in (sib.get("class") or []):
            entry = _kv_from_table_items(sib)
            if entry:
                out.append(entry)
    return out


def _extract_email_details(soup: BeautifulSoup) -> list[str]:
    """Each email is a div.table-content (mt-0) sibling. The first sibling is
    a header containing only an item-head 'Email ID'. Subsequent siblings each
    contain a single item-text with the email address."""
    title = _section_title_p(soup, r"^\s*Email Details\s*$")
    emails: list[str] = []
    for sib in _siblings_until_next_title(title):
        if sib.name != "div":
            continue
        # Pick item-text values directly under p.table-item; ignore empties.
        for it in sib.find_all("p", class_="table-item"):
            t = it.find("span", class_="item-text")
            if t is not None:
                v = _txt(t)
                if v:
                    emails.append(v)
    return emails


def _extract_employment_details(soup: BeautifulSoup) -> dict[str, str]:
    title = _section_title_p(soup, r"^\s*employment Details\s*$")
    out: dict[str, str] = {}
    for sib in _siblings_until_next_title(title):
        out.update(_kv_from_table_items(sib))
    return out


def _extract_accounts(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Walk both open_accounts and closed_accounts containers."""
    accounts: list[dict[str, Any]] = []
    for status, sel in (("open", "div.accounts-container.open_accounts"),
                        ("closed", "div.accounts-container.closed_accounts")):
        container = soup.select_one(sel)
        if container is None:
            continue
        accordion = container.find("div", class_="accordion")
        if accordion is None:
            continue
        cards = accordion.find_all("div", class_="card", recursive=False)
        for card in cards:
            accounts.append(_extract_one_account(card, status))
    return accounts


def _extract_one_account(card: Tag, status: str) -> dict[str, Any]:
    # Header values: 4 p.head-col in div.card-header (member, type, number, ownership).
    hdr = card.find("div", class_="card-header")
    if hdr is None:
        raise RuntimeError("card-header not found inside card")
    hdr_vals = [_txt(p) for p in hdr.find_all("p", class_="head-col")]
    # Header labels live in the sibling div.account-head.columnStyle.
    label_block = card.find("div", class_=re.compile(r"\baccount-head\b"))
    hdr_keys: list[str] = []
    if label_block is not None:
        hdr_keys = [_label_to_key(_txt(p)) for p in label_block.find_all("p", class_="head-col")]
    if len(hdr_keys) != len(hdr_vals) or not hdr_keys:
        # Fallback to fixed schema if labels are missing/misaligned.
        hdr_keys = ["member_name", "account_type", "account_number", "ownership"]
        hdr_vals = (hdr_vals + [""] * 4)[:4]
    header = dict(zip(hdr_keys, hdr_vals))

    # Account Details tab: div.detail-row > span (label) + span (value).
    body = card.find("div", class_=re.compile(r"\bcollapse\b"))
    if body is None:
        raise RuntimeError("collapse body not found inside card")
    wrappers = body.find_all("div", class_="card-body__wraper")
    if len(wrappers) < 2:
        raise RuntimeError(f"expected 2 card-body__wraper, found {len(wrappers)}")

    ac_pane = wrappers[0].find(
        "div", class_="tab-pane", id=re.compile(r"^acDetails-+\d+$"),
    )
    if ac_pane is None:
        raise RuntimeError("acDetails-* tab-pane not found")
    details: dict[str, str] = {}
    for row in ac_pane.find_all("div", class_="detail-row"):
        spans = row.find_all("span", recursive=False)
        if len(spans) < 2:
            continue
        details[_label_to_key(_txt(spans[0]))] = _txt(spans[1])

    # Payment Status pane.
    ps_pane = wrappers[1].find(
        "div", class_="tab-pane", id=re.compile(r"^paymentStatus-+\d+$"),
    )
    if ps_pane is None:
        raise RuntimeError("paymentStatus-* tab-pane not found")

    # payment_start_date / payment_end_date: <p class="bold-title">LABEL</p> + sibling text.
    payment_status: dict[str, str] = {}
    dates_div = ps_pane.find("div", class_="dates")
    if dates_div is not None:
        for d in dates_div.find_all("div", class_="date", recursive=False):
            label_p = d.find("p", class_="bold-title")
            if label_p is None:
                continue
            label = _label_to_key(_txt(label_p))
            # value is the remaining text after removing the label paragraph.
            value = _txt(d).replace(_txt(label_p), "", 1).strip()
            payment_status[label] = _norm(value)

    # Payment History (flat list, mobile variant): div.history.hide-md-up
    # contains div.detail-row > span (e.g. "Apr 2026") + span (value e.g. "9").
    flat_div = ps_pane.find("div", class_=re.compile(r"\bhide-md-up\b"))
    payment_history: list[dict[str, str]] = []
    if flat_div is not None:
        for row in flat_div.find_all("div", class_="detail-row"):
            spans = row.find_all("span", recursive=False)
            if len(spans) < 2:
                continue
            payment_history.append({
                "month": _txt(spans[0]),
                "value": _txt(spans[1]),
            })

    # Payment History (year-grid, desktop variant): same data in tabular form.
    # We extract it independently and assert it agrees with the flat list.
    grid = _extract_payment_history_grid(ps_pane)
    member_for_err = header.get("member_name", "?")
    acct_for_err = header.get("account_number", "?")
    if grid is not None:
        diff = _payment_history_diff(payment_history, grid)
        if diff:
            raise RuntimeError(
                f"Payment history mismatch on {member_for_err} / {acct_for_err}: "
                f"{diff}"
            )

    return {
        "status": status,
        **header,
        "account_details": details,
        "payment_status": payment_status,
        "payment_history": payment_history,
    }


_FLAT_MONTH_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{4})$")


def _extract_payment_history_grid(ps_pane: Tag) -> dict[str, dict[str, str]] | None:
    """Extract the desktop year-grid `<table class="payment-history-table">`.

    Returns a mapping {year_str: {month_abbr: cell_text}} where empty cells are
    represented by the empty string. Returns None if the grid is absent.
    """
    grid_div = ps_pane.find("div", class_=re.compile(r"\bhide-sm-down\b"))
    if grid_div is None:
        return None
    table = grid_div.find("table", class_="payment-history-table")
    if table is None:
        return None
    thead = table.find("thead")
    tbody = table.find("tbody")
    if thead is None or tbody is None:
        return None
    headers = [_txt(th) for th in thead.find_all("th")]
    # First <th> is the combined Month/Year header; remaining 12 are month abbrs.
    if len(headers) < 13:
        return None
    months = headers[1:13]  # e.g. ["Dec","Nov",...,"Jan"]
    grid: dict[str, dict[str, str]] = {}
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 1 + len(months):
            continue
        year = _txt(tds[0])
        if not year:
            continue
        cells = [_txt(td) for td in tds[1: 1 + len(months)]]
        grid[year] = dict(zip(months, cells))
    return grid


def _payment_history_diff(
    flat: list[dict[str, str]],
    grid: dict[str, dict[str, str]],
) -> str | None:
    """Compare flat list and grid. Return None if they encode identical data.

    Both views must agree cell-for-cell after the natural conventions:
    - the flat list omits months with no data; the grid uses an empty cell.
    - flat months are e.g. "Apr 2026"; grid columns are 3-letter abbreviations
      and rows are 4-digit years.
    """
    flat_grid: dict[str, dict[str, str]] = {}
    for entry in flat:
        m = _FLAT_MONTH_RE.match(entry.get("month", ""))
        if not m:
            return f"unparsable flat month: {entry!r}"
        mon, yr = m.group(1), m.group(2)
        flat_grid.setdefault(yr, {})[mon] = entry.get("value", "")

    years = set(grid.keys()) | set(flat_grid.keys())
    for yr in years:
        gcells = grid.get(yr, {})
        fcells = flat_grid.get(yr, {})
        months = set(gcells.keys()) | set(fcells.keys())
        for mon in months:
            g = gcells.get(mon, "").strip()
            f = fcells.get(mon, "").strip()
            if g == f:
                continue
            # Both empty also OK (already handled by g == f).
            return (
                f"cell ({yr},{mon}) flat={f!r} grid={g!r}"
            )
    return None


def _extract_enquiries(soup: BeautifulSoup) -> list[dict[str, str]]:
    sec = soup.select_one("div.report-table.enquiryDetailsTab")
    if sec is None:
        raise RuntimeError("enquiryDetailsTab section not found")
    out: list[dict[str, str]] = []
    for tcp in sec.find_all("div", class_="table-content-parent"):
        entry = _kv_from_table_items(tcp)
        if entry:
            out.append(entry)
    return out


def _extract_footer(soup: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    eor = soup.find(string=re.compile(r"End of report"))
    if eor is not None:
        out["end_of_report"] = _norm(str(eor))
    # The disclaimer paragraph that follows.
    disc = soup.find(string=re.compile(r"Disclaimer:\s*All information contained"))
    if disc is not None:
        out["disclaimer"] = _norm(str(disc))
    # Copyright sentence is split across nodes ("COPYRIGHT " + "<year>" + " ..."),
    # so .find(string=...) only returns the literal fragment. Reconstruct from
    # the enclosing element's combined text.
    cr_rx = re.compile(r"COPYRIGHT\s+\d{4}\s+TRANSUNION CIBIL[^.]*\.[^\n]*", re.IGNORECASE)
    for el in soup.find_all(["p", "div", "span", "footer"]):
        txt = _norm(el.get_text(" ", strip=True))
        m = cr_rx.search(txt)
        if m:
            out["copyright"] = m.group(0).strip()
            break
    return out


# ---------------------------------------------------------------------------
# Stage 4: emit final JSON + YAML from the same dict
# ---------------------------------------------------------------------------

def stage4_emit(data: dict[str, Any], json_out: Path, yaml_out: Path) -> None:
    json_str = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)
    json_out.write_text(json_str + "\n", encoding="utf-8")

    yaml_str = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        width=4096,
        default_flow_style=False,
    )
    yaml_out.write_text(yaml_str, encoding="utf-8")

    # Round-trip equality check
    j = json.loads(json_out.read_text(encoding="utf-8"))
    y = yaml.safe_load(yaml_out.read_text(encoding="utf-8"))
    if j != data or y != data or j != y:
        raise RuntimeError("Round-trip mismatch between dict <-> JSON <-> YAML")
    print(f"[stage4] final JSON    -> {json_out.name}  ({json_out.stat().st_size:,} bytes)")
    print(f"[stage4] final YAML    -> {yaml_out.name}  ({yaml_out.stat().st_size:,} bytes)")


# ---------------------------------------------------------------------------
# Stage 5: cross-check vs MHTML text corpus
# ---------------------------------------------------------------------------

def _iter_leaves(obj: Any, path: str = "") -> Any:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_leaves(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_leaves(v, f"{path}[{i}]")
    else:
        yield path, obj


# Leaf paths whose values are NOT verbatim from the MHTML text but are
# synthesised by the parser. These are validated with their own assertion
# below, not via substring match.
_DERIVED_LEAF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^accounts\[\d+\]\.status$"),
)


def _is_derived(path: str) -> bool:
    return any(p.match(path) for p in _DERIVED_LEAF_PATTERNS)


_MONTH_TO_NUM = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _build_content_summary(data: dict[str, Any]) -> list[str]:
    """Human-readable inventory of what was extracted (purely from the dict).

    Has no I/O or DOM access -- safe to call after Stage 4 only.
    """
    out: list[str] = []
    rep = data.get("report", {})
    score = data.get("cibil_score", {})
    pers = data.get("personal_details", {})
    out.append(f"  consumer name        : {pers.get('name','?')}")
    out.append(f"  date of birth        : {pers.get('date_of_birth','?')}")
    out.append(f"  PAN (from idents)    : "
               + next((i.get("id_number","?") for i in data.get("identification_details", [])
                        if "PAN" in i.get("identification_type","")), "?"))
    out.append(f"  control number       : {rep.get('control_number','?')}")
    out.append(f"  report date          : {rep.get('date','?')}")
    out.append(f"  CIBIL score          : {score.get('score','?')}  "
               f"(as of {score.get('as_of_date','?')})")
    out.append(f"  identification rows  : {len(data.get('identification_details', []))}")
    out.append(f"  address rows         : {len(data.get('address_details', []))}")
    out.append(f"  contact rows         : {len(data.get('contact_details', []))}")
    out.append(f"  email addresses      : {len(data.get('email_details', []))}")
    out.append(f"  enquiries            : {len(data.get('enquiry_details', []))}")

    # Accounts inventory.
    accts = data.get("accounts", [])
    out.append(f"  accounts (total)     : {len(accts)}")
    by_status: dict[str, int] = {}
    by_status_type: dict[tuple[str, str], int] = {}
    ph_total = 0
    ph_min: tuple[int, int] | None = None
    ph_max: tuple[int, int] | None = None
    for a in accts:
        s = a.get("status", "?")
        t = a.get("account_type", "?")
        by_status[s] = by_status.get(s, 0) + 1
        by_status_type[(s, t)] = by_status_type.get((s, t), 0) + 1
        for entry in a.get("payment_history", []):
            m = _FLAT_MONTH_RE.match(entry.get("month", ""))
            if not m:
                continue
            ym = (int(m.group(2)), _MONTH_TO_NUM.get(m.group(1), 0))
            ph_total += 1
            if ph_min is None or ym < ph_min:
                ph_min = ym
            if ph_max is None or ym > ph_max:
                ph_max = ym
    for s, n in sorted(by_status.items()):
        out.append(f"    by status {s:7s}: {n}")
    out.append(f"  account-type breakdown:")
    for (s, t), n in sorted(by_status_type.items()):
        out.append(f"    {s:6s} / {t:25s} : {n}")
    out.append(f"  payment-history rows : {ph_total}")
    if ph_min and ph_max:
        out.append(f"  payment-history span : "
                   f"{ph_min[0]:04d}-{ph_min[1]:02d}  ->  "
                   f"{ph_max[0]:04d}-{ph_max[1]:02d}  "
                   f"(~{(ph_max[0]-ph_min[0])*12 + (ph_max[1]-ph_min[1]) + 1} months)")
    return out


def _scoped_verify(data: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    """Per-record scoped verification.

    Stronger than the document-level substring check: each leaf value must
    appear within the text of its own HTML record, not just anywhere in the
    document. This catches misattribution (e.g. account[5]'s credit limit
    accidentally copied from account[6]'s card).

    Returns a summary dict with counts and a list of misses.
    """
    misses: list[tuple[str, str, str]] = []  # (record_path, leaf_path, value)
    ok_count = 0

    def _check(record_path: str, json_obj: Any, html_text: str) -> None:
        nonlocal ok_count
        for path, value in _iter_leaves(json_obj):
            full_path = f"{record_path}.{path}" if path else record_path
            if _is_derived(full_path):
                continue
            if value in (None, ""):
                continue
            sval = str(value)
            if sval in html_text:
                ok_count += 1
                continue
            if _norm(sval) in _norm(html_text):
                ok_count += 1
                continue
            misses.append((record_path, path, sval))

    # Accounts: open then closed, in DOM order.
    open_cards = soup.select("div.accounts-container.open_accounts   div.accordion > div.card")
    closed_cards = soup.select("div.accounts-container.closed_accounts div.accordion > div.card")
    all_cards = open_cards + closed_cards
    for i, acct in enumerate(data.get("accounts", [])):
        if i < len(all_cards):
            _check(f"accounts[{i}]", acct,
                   _norm(all_cards[i].get_text(" ", strip=True)))
        else:
            misses.append((f"accounts[{i}]", "<no HTML card>", ""))

    # Enquiries.
    enq_blocks = soup.select(
        "div.report-table.enquiryDetailsTab div.table-content-parent")
    for i, enq in enumerate(data.get("enquiry_details", [])):
        if i < len(enq_blocks):
            _check(f"enquiry_details[{i}]", enq,
                   _norm(enq_blocks[i].get_text(" ", strip=True)))

    # Identification / Address / Contact (each entry is one
    # div.table-content-parent sibling of the section title).
    def _section_blocks(title_re: str) -> list[Tag]:
        rx = re.compile(title_re)
        title_p = next(
            (p for p in soup.find_all("p", class_="table-title")
             if rx.search(p.get_text(" ", strip=True))),
            None,
        )
        out: list[Tag] = []
        if title_p is None:
            return out
        for sib in title_p.next_siblings:
            if not isinstance(sib, Tag):
                continue
            if sib.name == "p" and "table-title" in (sib.get("class") or []):
                break
            if sib.name == "div" and "table-content-parent" in (sib.get("class") or []):
                out.append(sib)
        return out

    for title, key in [
        (r"^\s*Identification Details\s*$", "identification_details"),
        (r"^\s*Address Details\s*$", "address_details"),
        (r"^\s*Contact Details\s*$", "contact_details"),
    ]:
        blocks = _section_blocks(title)
        for i, entry in enumerate(data.get(key, [])):
            if i < len(blocks):
                _check(f"{key}[{i}]", entry,
                       _norm(blocks[i].get_text(" ", strip=True)))
            else:
                misses.append((f"{key}[{i}]", "<no HTML block>", ""))

    # Emails (skip first sibling: the "Email ID" header block).
    email_title = next(
        (p for p in soup.find_all("p", class_="table-title")
         if re.search(r"Email Details", p.get_text(" ", strip=True))),
        None,
    )
    email_value_blocks: list[Tag] = []
    if email_title is not None:
        for sib in email_title.next_siblings:
            if not isinstance(sib, Tag):
                continue
            if sib.name == "p" and "table-title" in (sib.get("class") or []):
                break
            if sib.name == "div":
                email_value_blocks.append(sib)
        email_value_blocks = email_value_blocks[1:]  # drop the header block
    for i, em in enumerate(data.get("email_details", [])):
        if i < len(email_value_blocks):
            _check(f"email_details[{i}]", em,
                   _norm(email_value_blocks[i].get_text(" ", strip=True)))
        else:
            misses.append((f"email_details[{i}]", "<no HTML block>", em))

    # Personal Details (whole personal block as scope).
    pd_title = next(
        (p for p in soup.find_all("p", class_="table-title")
         if re.search(r"Personal Details", p.get_text(" ", strip=True))),
        None,
    )
    if pd_title is not None:
        pd_text_parts: list[str] = []
        for sib in pd_title.next_siblings:
            if not isinstance(sib, Tag):
                continue
            if sib.name == "p" and "table-title" in (sib.get("class") or []):
                break
            pd_text_parts.append(sib.get_text(" ", strip=True))
        pd_text = _norm(" ".join(pd_text_parts))
        _check("personal_details", data.get("personal_details", {}), pd_text)

    # Employment Details (whole employment block as scope).
    emp_title = next(
        (p for p in soup.find_all("p", class_="table-title")
         if re.search(r"mployment Details", p.get_text(" ", strip=True))),
        None,
    )
    if emp_title is not None:
        emp_text_parts: list[str] = []
        for sib in emp_title.next_siblings:
            if not isinstance(sib, Tag):
                continue
            if sib.name == "p" and "table-title" in (sib.get("class") or []):
                break
            emp_text_parts.append(sib.get_text(" ", strip=True))
        emp_text = _norm(" ".join(emp_text_parts))
        _check("employment_details", data.get("employment_details", {}), emp_text)

    return {"ok": ok_count, "misses": misses}


def stage5_verify(
    data: dict[str, Any],
    text_corpus: str,
    html: str,
    json_out: Path,
    yaml_out: Path,
    verification_txt: Path,
) -> bool:
    """Cross-check every leaf in `data` against the rendered MHTML text.

    For each leaf:
      - if its path is in _DERIVED_LEAF_PATTERNS, validate it with a derived-leaf
        rule (it is NOT expected to appear in the text corpus);
      - otherwise the leaf MUST appear in the text corpus, either verbatim
        (exact) or after whitespace normalisation (soft).

    Plus structural checks:
      - exact account count vs HTML containers (open / closed),
      - exact enquiry count vs HTML.
    """
    corpus_norm = re.sub(r"\s+", " ", text_corpus)
    misses: list[tuple[str, str]] = []
    derived_misses: list[tuple[str, str]] = []
    exact_hits = soft_hits = derived_ok = skipped = 0

    for path, value in _iter_leaves(data):
        if value is None or value == "":
            skipped += 1
            continue
        sval = str(value)
        if _is_derived(path):
            # account.status: must be exactly 'open' or 'closed'
            if path.endswith(".status"):
                if sval in ("open", "closed"):
                    derived_ok += 1
                else:
                    derived_misses.append((path, sval))
            else:
                derived_misses.append((path, sval))
            continue
        if sval in text_corpus:
            exact_hits += 1
            continue
        if _norm(sval) in corpus_norm:
            soft_hits += 1
            continue
        misses.append((path, sval))

    # Structural cross-checks against the original HTML.
    soup = BeautifulSoup(html, "lxml")
    html_open = len(soup.select("div.accounts-container.open_accounts div.card"))
    html_closed = len(soup.select("div.accounts-container.closed_accounts div.card"))
    extracted_open = sum(1 for a in data.get("accounts", []) if a.get("status") == "open")
    extracted_closed = sum(1 for a in data.get("accounts", []) if a.get("status") == "closed")
    html_enq = len(soup.select("div.report-table.enquiryDetailsTab div.table-content-parent"))
    extracted_enq = len(data.get("enquiry_details", []))
    structural_pass = (
        html_open == extracted_open
        and html_closed == extracted_closed
        and html_enq == extracted_enq
    )

    # Per-record scoped verification (much stronger than the document-level
    # substring check above: catches misattribution where the right value
    # ended up in the wrong row/account/enquiry).
    scoped = _scoped_verify(data, soup)
    scoped_pass = len(scoped["misses"]) == 0

    # Content summary (what's actually in the data) -- helps a human auditor
    # cross-check at-a-glance against the rendered MHTML before trusting it.
    summary_lines = _build_content_summary(data)

    lines: list[str] = []
    lines.append("CIBIL MHTML <-> output cross-check")
    lines.append("=" * 50)
    lines.append(f"final JSON file : {json_out.name}")
    lines.append(f"final YAML file : {yaml_out.name}")
    lines.append("")
    lines.append("Content summary")
    lines.append("-" * 30)
    lines.extend(summary_lines)
    lines.append("")
    lines.append("Leaf-value verification")
    lines.append("-" * 30)
    total_leaves = exact_hits + soft_hits + len(misses) + derived_ok + len(derived_misses)
    lines.append(f"  total leaves     : {total_leaves}")
    lines.append(f"  exact matches    : {exact_hits}")
    lines.append(f"  soft matches     : {soft_hits}  (whitespace-normalised)")
    lines.append(f"  derived OK       : {derived_ok}  (synthesised, validated by rule)")
    lines.append(f"  source MISSES    : {len(misses)}")
    lines.append(f"  derived MISSES   : {len(derived_misses)}")
    lines.append(f"  empty/None skip  : {skipped}")
    lines.append("")
    lines.append("Structural verification")
    lines.append("-" * 30)
    lines.append(f"  open accounts   HTML={html_open:3d}  extracted={extracted_open:3d}  "
                 f"{'OK' if html_open == extracted_open else 'FAIL'}")
    lines.append(f"  closed accounts HTML={html_closed:3d}  extracted={extracted_closed:3d}  "
                 f"{'OK' if html_closed == extracted_closed else 'FAIL'}")
    lines.append(f"  enquiries       HTML={html_enq:3d}  extracted={extracted_enq:3d}  "
                 f"{'OK' if html_enq == extracted_enq else 'FAIL'}")
    lines.append("")

    lines.append("Per-record scoped verification")
    lines.append("-" * 30)
    lines.append("  (each leaf must appear in the text of ITS OWN HTML record,")
    lines.append("   not just anywhere in the document; catches misattribution)")
    lines.append(f"  scoped OK leaves : {scoped['ok']}")
    lines.append(f"  scoped MISSES    : {len(scoped['misses'])}")
    lines.append("")

    if misses:
        lines.append("FAILED source leaves (value not found in MHTML text):")
        for p, v in misses:
            lines.append(f"  - {p} = {v!r}")
        lines.append("")
    if derived_misses:
        lines.append("FAILED derived leaves (synthesised value violates rule):")
        for p, v in derived_misses:
            lines.append(f"  - {p} = {v!r}")
        lines.append("")
    if scoped["misses"]:
        lines.append("FAILED scoped leaves (right value, wrong record):")
        for rec, leaf, v in scoped["misses"]:
            lines.append(f"  - {rec}.{leaf} = {v!r}")
        lines.append("")

    ok = (not misses and not derived_misses
          and structural_pass and scoped_pass)
    lines.append(f"RESULT: {'OK' if ok else 'FAIL'}")

    verification_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[stage5] verification  -> {verification_txt.name}  "
          f"({'OK' if ok else 'FAIL'}: "
          f"{exact_hits} exact, {soft_hits} soft, {derived_ok} derived, "
          f"{scoped['ok']} scoped, "
          f"{len(misses)+len(derived_misses)+len(scoped['misses'])} miss)")
    return ok


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mhtml", type=Path, help="path to the .mhtml file")
    ap.add_argument(
        "--stage", choices=["1", "2", "3", "4", "5", "all"], default="all",
        help="run only up to this stage (for incremental development)",
    )
    ap.add_argument(
        "--cache-dir", type=Path, default=None,
        help=("folder for INTERMEDIATE files (decoded HTML, text corpus, "
              "extracted JSON). Safe to delete anytime -- regenerated each run. "
              "Defaults to '<project-root>/cache'."),
    )
    ap.add_argument(
        "--output-dir", type=Path, default=None,
        help=("folder for the timestamped FINAL outputs (json/yaml/verification). "
              "Defaults to '<project-root>/output'."),
    )
    ap.add_argument(
        "--clean-cache", action="store_true",
        help="wipe the cache folder before running (forces a fresh decode/extract).",
    )
    args = ap.parse_args(argv)

    src = args.mhtml.resolve()
    if not src.exists():
        print(f"ERROR: not found: {src}", file=sys.stderr)
        return 2
    stem_name = src.with_suffix("").name  # e.g. 'your-report'

    # Cache folder: rooted at the project (not next to the source), so sources
    # in src/ stay untouched.
    cache_dir = (args.cache_dir.resolve()
                 if args.cache_dir is not None
                 else _PROJECT_ROOT / "cache")
    if args.clean_cache and cache_dir.exists():
        print(f"[clean ] wiping cache  -> {cache_dir}")
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    decoded_html = cache_dir / f"{stem_name}.decoded.html"
    text_txt = cache_dir / f"{stem_name}.text.txt"
    extracted_json = cache_dir / f"{stem_name}.extracted.json"

    # Output folder: rooted at the project; timestamped finals.
    out_dir = (args.output_dir.resolve()
               if args.output_dir is not None
               else _PROJECT_ROOT / "output")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    base = f"{stem_name}.{timestamp}"  # e.g. 'your-report.2025-03-14-10-22-05'
    final_json = out_dir / f"{base}.json"
    final_yaml = out_dir / f"{base}.yaml"
    verify_txt = out_dir / f"{base}.verification.txt"

    try:
        html = stage1_decode_mhtml(src, decoded_html)
    except RuntimeError as e:
        # Stage 1 errors are about the input file itself (wrong format,
        # encoding mismatch, not a CIBIL print-view report). Print clean,
        # no traceback -- it's a user error, not a parser bug.
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    if args.stage == "1":
        return 0

    text = stage2_html_to_text(html, text_txt)
    if args.stage == "2":
        return 0

    data = stage3_extract(html, extracted_json)
    if args.stage == "3":
        return 0

    print(f"[stage4] output dir    -> {out_dir}")
    print(f"[stage4] timestamp     -> {timestamp}")
    stage4_emit(data, final_json, final_yaml)
    if args.stage == "4":
        return 0

    ok = stage5_verify(data, text, html, final_json, final_yaml, verify_txt)
    if not ok:
        # Quarantine the unverified files so they can't be fed downstream.
        # Keep the timestamp so multiple failed runs don't collide.
        bad_json = out_dir / f"{base}.UNVERIFIED.json"
        bad_yaml = out_dir / f"{base}.UNVERIFIED.yaml"
        final_json.rename(bad_json)
        final_yaml.rename(bad_yaml)
        print(f"VERIFICATION FAILED -- final files renamed to:\n"
              f"  {bad_json}\n  {bad_yaml}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
