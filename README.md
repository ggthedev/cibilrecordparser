# CIBIL MHTML Parser

Parse a CIBIL credit-report MHTML snapshot into verified JSON / YAML,
then search it from a terminal REPL or a local browser UI.

Every leaf value is a **verbatim string** copied from the report —
dates stay in `dd/mm/yyyy`, currency keeps the `₹` symbol and Indian
grouping, masked IDs stay masked, `-` stays `-`. The output JSON and
YAML are always semantically identical (cross-checked on every run),
and each run emits a verification report that fails loudly if any
extracted leaf can't be traced back to the source MHTML.

The repo ships with an anonymized sample report at
`src/cibil-sample.mhtml` so you can try it end-to-end the moment you
clone.

## Requirements

- **macOS or Linux** with `make` and a working `python3` (3.10+).
- Python packages (auto-installed by `make install` into a local
  `.venv/`): `beautifulsoup4`, `lxml`, `PyYAML`, `Flask`.
- The input must be a CIBIL **print-view** MHTML export — saved from
  `https://myscore.cibil.com/CreditView/creditReportPrint.page?print=true`
  in any Chromium-based browser (File → Save → Webpage, Single File).
  Other MHTMLs (the consumer-dashboard view, random web pages, other
  credit bureaus) are rejected at stage 1 with a clear error.

## Quick start

```bash
git clone <this-repo>
cd cibilparser

make install         # create .venv and install dependencies
make run             # parse every src/*.mhtml -> output/*.json / .yaml
make query           # interactive REPL over the latest parsed report
make web             # browser UI on http://127.0.0.1:5057
```

That's it. Out of the box, `make run` parses the bundled
`src/cibil-sample.mhtml` so you can walk through the rest of the
workflow immediately.

## Using your own report

1. Export your own CIBIL report as MHTML from the print view.
2. Copy it into `src/`:

   ```bash
   cp /path/to/your-report.mhtml src/
   ```

3. Run the parser:

   ```bash
   make run                                 # parses every src/*.mhtml
   # or: parse exactly one file (anywhere on disk)
   make file FILE=/path/to/your-report.mhtml
   ```

Each run writes three timestamped files to `output/`:

| File                               | Purpose                                                 |
| ---------------------------------- | ------------------------------------------------------- |
| `<stem>.<ts>.json`                 | Final parsed report.                                    |
| `<stem>.<ts>.yaml`                 | Same data, YAML format (JSON ↔ YAML equality enforced). |
| `<stem>.<ts>.verification.txt`     | Per-leaf audit of the JSON against the MHTML source.    |

`<stem>` is the input filename without extension; `<ts>` is
`yyyy-mm-dd-hh-mm-ss`.

Intermediate files (decoded HTML, text corpus, first-pass JSON) live
in `cache/` and are safe to delete any time via `make clean-cache`.

**Verification gate:** if Stage 5 can't match every leaf back to the
source text, the final files are renamed to `*.UNVERIFIED.json` /
`*.UNVERIFIED.yaml` and the run exits non-zero, so unverified data
can never be picked up accidentally by downstream tools (including
the browser UI, which refuses to load them).

## Querying parsed reports

### Terminal REPL — `make query`

```bash
make query
```

A small interactive console that loads the latest `output/*.json`,
offers a universal substring search across the entire report, and
lets you drill into any matching record. Type `help` inside the
REPL for the full command set.

### Browser UI — `make web`

```bash
make web                  # foreground; Ctrl+C to stop
make web start            # background daemon (writes .web.pid and .web.log)
make web stop
make web status
make web restart
```

Default URL: `http://127.0.0.1:5057`. Override with environment
variables on the command line:

```bash
make web start WEB_PORT=8080
make web start WEB_HOST=127.0.0.1 WEB_PORT=5060
```

The UI has a left-rail with `Summary` / `Personal` / `Accounts` /
`Enquiries` / `Addresses` / `Contacts` / `Emails` / `Identification`,
a top-bar search box, a dropdown to flip between all snapshots in
`output/`, a CIBIL-score badge that colours itself by band, and a
drill-down into each account with a year × month payment-history
grid (DPD values colour-graded).

It's a single Flask process plus a vanilla-JS client over Tailwind
(loaded from a CDN) — no Node toolchain.

## Output schema (top-level keys, in order)

```
report                   { source_title, control_number, date, greeting }
cibil_score              { score, as_of_date }
personal_details         { name, date_of_birth, gender, ... }
identification_details   [ { identification_type, id_number, issue_date, expiry_date }, ... ]
address_details          [ { address, category, residence_code, date_reported }, ... ]
contact_details          [ { telephone_number_type, telephone_number, telephone_extension }, ... ]
email_details            [ "<email>", ... ]
employment_details       { account_type, date_reported, occupation, income, ... }
accounts                 [ {
                             status,                    # "open" | "closed"
                             member_name, account_type, account_number, ownership,
                             account_details:  { credit_limit, current_balance, ... },
                             payment_status:   { payment_start_date, payment_end_date },
                             payment_history:  [ { month, value }, ... ]
                           }, ... ]
enquiry_details          [ { member_name, date_of_enquiry, enquiry_purpose }, ... ]
footer                   { end_of_report, disclaimer, copyright }
```

## All `make` targets

Run `make help` for the same list with one-line descriptions. The
most useful ones:

| Target                    | What it does                                            |
| ------------------------- | ------------------------------------------------------- |
| `make install`            | Create `.venv/` and install dependencies.               |
| `make run` (default)      | Parse every `src/*.mhtml`.                              |
| `make file FILE=<path>`   | Parse a single explicit file (anywhere on disk).        |
| `make query`              | Launch the terminal REPL.                               |
| `make web`                | Serve the browser UI (foreground).                      |
| `make web start` / `stop` / `status` / `restart` | Background-daemon controls. |
| `make clean-cache`        | Wipe `cache/` (safe: regeneratable intermediates).      |
| `make clean-output`       | Wipe `output/` (DESTRUCTIVE: deletes timestamped finals). |
| `make clean`              | `clean-cache` **and** `clean-output`.                   |
| `make distclean`          | `clean` **and** delete `.venv/`.                        |

## Advanced: direct script invocation

`make` is thin wrapper around two scripts. If you want more control:

```bash
.venv/bin/python parse_cibil.py src/your-report.mhtml
```

Flags:

```
--stage {1,2,3,4,5,all}   stop after a given stage (incremental debugging)
--cache-dir <path>        override default cache/ folder
--output-dir <path>       override default output/ folder
--clean-cache             wipe cache/ before running (force a fresh decode)
```

The web server has its own flags too:

```bash
.venv/bin/python web_app.py --port 5057
.venv/bin/python web_app.py --output-dir /tmp/o
.venv/bin/python web_app.py --debug
```

## Privacy & security

- **`src/cibil-sample.mhtml` is a fully anonymized scramble** of a
  real CIBIL report. Name, DOB, PAN / Voter ID / Passport / DL /
  CKYC, mobile numbers, emails, addresses, account numbers, currency
  amounts, lender names and dates are all replaced with deterministic
  synthetic values. CSS / SVG / image MIME parts pass through verbatim
  (CIBIL brand assets, no user-specific bytes). No real PII lives in
  this repo or its git history.
- **`cache/` and `output/` are `.gitignore`d** so anything you
  generate from your own report stays local. `src/` is *not*
  gitignored — if you want your own reports to stay out of git,
  either keep them elsewhere and use `make file FILE=...`, or add
  your filenames to `.gitignore`.
- **The browser UI binds to `127.0.0.1` only** and refuses to serve
  `*.UNVERIFIED.*` files. **Do not** pass `--host 0.0.0.0` or expose
  the port to a network — `output/*.json` contains full PII (PAN,
  account numbers, complete payment history).

## IDE setup

`.vscode/settings.json` pins the Python interpreter to
`.venv/bin/python`, so VS Code / Cursor / Windsurf pick up the venv
automatically when the folder opens. Run `make install` once first
to create `.venv/`.

## License

See [`LICENSE`](./LICENSE).
