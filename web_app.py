#!/usr/bin/env python3
"""Browser UI for the parsed CIBIL JSON.

Thin Flask wrapper around the helpers already implemented in `query_cibil.py`
(`_find_latest_json`, `_search`, `_get_record`). The UI is a single static page
served from `templates/index.html` plus `static/app.js` and `static/styles.css`.

Endpoints
---------
GET  /                       static index page
GET  /api/files              list verified output/*.json snapshots (latest first)
GET  /api/data?file=NAME     full JSON dict for the chosen (or latest) file
GET  /api/search?q=&file=    REPL-style universal substring search hits
GET  /api/record/<sec>/<i>?file=
                              single record from a section (sec is one of:
                              accounts, enquiries, addresses, contacts,
                              emails, identification)

Safety
------
By default the server binds to 127.0.0.1 only. The data is real PII (PAN,
account numbers, phone numbers, payment history) so do NOT bind to 0.0.0.0
or expose this port externally.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request

from query_cibil import (
    _DEFAULT_OUTPUT_DIR,
    _find_latest_json,
    _get_record,
    _search,
)

_VALID_SECTIONS = {
    "accounts", "enquiries", "addresses",
    "contacts", "emails", "identification",
}

app = Flask(__name__, static_folder="static", template_folder="templates")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _output_dir() -> Path:
    return Path(app.config["OUTPUT_DIR"]).resolve()


def _resolve_file(name: str | None) -> Path:
    """Return an absolute Path inside output_dir, or 4xx-abort on bad input."""
    out_dir = _output_dir()
    if name:
        # Disallow path separators / traversal: name must be a bare filename
        # that resolves to a file directly inside output_dir.
        if "/" in name or "\\" in name or name.startswith(".."):
            abort(400, "invalid file name")
        candidate = (out_dir / name).resolve()
        try:
            candidate.relative_to(out_dir)
        except ValueError:
            abort(400, "file outside output directory")
        if not candidate.is_file():
            abort(404, f"file not found: {name}")
        if ".UNVERIFIED." in candidate.name:
            abort(400, "refusing to serve unverified output")
        return candidate
    p = _find_latest_json(out_dir)
    if p is None:
        abort(
            404,
            f"no verified *.json found in {out_dir}. "
            "Run `make` first to generate one.",
        )
    return p


def _load(src: Path) -> dict[str, Any]:
    return json.loads(src.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/files")
def list_files():
    out_dir = _output_dir()
    if not out_dir.exists():
        return jsonify([])
    latest = _find_latest_json(out_dir)
    files = [
        p for p in out_dir.glob("*.json")
        if ".UNVERIFIED." not in p.name
    ]
    # Most recent first (filename has a sortable timestamp).
    files.sort(key=lambda p: p.name, reverse=True)
    payload = []
    for p in files:
        payload.append({
            "name": p.name,
            "size": p.stat().st_size,
            "is_latest": bool(latest and p.samefile(latest)),
        })
    return jsonify(payload)


@app.get("/api/data")
def get_data():
    src = _resolve_file(request.args.get("file"))
    return jsonify({"file": src.name, "data": _load(src)})


@app.get("/api/search")
def search():
    src = _resolve_file(request.args.get("file"))
    term = (request.args.get("q") or "").strip()
    if not term:
        return jsonify({"file": src.name, "term": "", "hits": []})
    hits = _search(_load(src), term)
    return jsonify({"file": src.name, "term": term, "hits": hits})


@app.get("/api/record/<section>/<int:index>")
def get_record(section: str, index: int):
    if section not in _VALID_SECTIONS:
        abort(404, f"unknown section: {section}")
    src = _resolve_file(request.args.get("file"))
    data = _load(src)
    try:
        record = _get_record(data, section, index)
    except (IndexError, KeyError):
        abort(404, f"no record at {section}[{index}]")
    return jsonify({"file": src.name, "section": section,
                    "index": index, "record": record})


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default: 127.0.0.1; do NOT use 0.0.0.0).")
    ap.add_argument("--port", type=int, default=5057,
                    help="port to listen on (default: 5057).")
    ap.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR,
                    help=f"folder to scan for output JSONs (default: {_DEFAULT_OUTPUT_DIR}).")
    ap.add_argument("--debug", action="store_true",
                    help="enable Flask debug mode (auto-reload, verbose errors).")
    args = ap.parse_args(argv)

    out_dir = args.output_dir.resolve()
    if not out_dir.exists():
        print(
            f"WARNING: output dir does not exist yet: {out_dir}",
            file=sys.stderr,
        )
    app.config["OUTPUT_DIR"] = str(out_dir)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"WARNING: binding to {args.host}. This server has no auth and "
            "exposes real PII; bind to 127.0.0.1 unless you know what you're doing.",
            file=sys.stderr,
        )

    print(f"CIBIL viewer  http://{args.host}:{args.port}")
    print(f"  output dir: {out_dir}")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
