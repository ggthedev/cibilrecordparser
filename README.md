# CIBIL MHTML → JSON / YAML

Pin-point-accurate parser for a CIBIL credit-report MHTML snapshot
(`https://myscore.cibil.com/CreditView/creditReportPrint.page?print=true`).

Single source of truth: one Python `dict` is built from the DOM and emitted as
both JSON and YAML, so the two final files are guaranteed to be semantically
identical. Every leaf value is a verbatim string copied from the report
(dates `dd/mm/yyyy`, currency keeps the `₹` symbol and Indian grouping, masked
IDs stay masked, `-` stays `-`, etc. — no transformations).

## Pipeline (5 stages)

| Stage | Input → Output                                          | Purpose                                                |
| ----- | ------------------------------------------------------- | ------------------------------------------------------ |
| 1     | `*.mhtml` → `*.decoded.html`                            | Decode the MIME / quoted-printable HTML part as UTF-8. |
| 2     | `*.decoded.html` → `*.text.txt`                         | Render to plain-text corpus used as the audit oracle.  |
| 3     | `*.decoded.html` → `*.extracted.json`                   | Section-by-section structured extraction (first pass). |
| 4     | dict → `*.json` and `*.yaml`                            | Final outputs + JSON↔YAML round-trip equality check.   |
| 5     | `*.json`, `*.yaml`, `*.text.txt`  → `*.verification.txt`| Cross-check every leaf vs the MHTML; gate the outputs. |

If Stage 5 finds any leaf whose value is not present in the MHTML text (and
isn't a documented derived field), the final files are renamed to
`*.UNVERIFIED.json` / `*.UNVERIFIED.yaml` and the script exits with a non-zero
status, so you cannot accidentally feed unverified data to a downstream tool.

## Project layout

```
project-root/
├── parse_cibil.py              # the parser
├── query_cibil.py              # interactive REPL over output/*.json
├── web_app.py                  # Flask server backing the browser UI
├── templates/index.html        # browser UI shell
├── static/app.js               # browser UI client logic
├── static/styles.css           # browser UI styles
├── scripts/
│   └── generate_sample_mhtml.py  # synthetic-fixture generator (no PII)
├── fixtures/
│   └── cibil-sample.mhtml      # synthetic fixture for `make demo`
├── docs/ScriptFlow.md          # learning walkthrough of the scripts
├── Makefile                    # `make run`, `make demo`, `make web`, ...
├── README.md
├── requirements.txt
├── .gitignore
├── .vscode/settings.json       # pins venv interpreter (optional)
├── src/                        # <-- put YOUR .mhtml inputs here (gitignored)
│   └── your-report.mhtml
├── cache/                      # intermediates (regeneratable, safe to delete)
│   ├── <stem>.decoded.html
│   ├── <stem>.text.txt
│   └── <stem>.extracted.json
└── output/                     # timestamped FINAL outputs (audit trail)
    ├── <stem>.<timestamp>.json
    ├── <stem>.<timestamp>.yaml
    └── <stem>.<timestamp>.verification.txt
```

The timestamp format is `yyyy-mm-dd-hh-mm-ss`. Each run preserves a fresh
final snapshot in `output/` while overwriting the intermediates in `cache/`.

If verification fails the JSON/YAML are renamed in place to
`output/<stem>.<timestamp>.UNVERIFIED.json` / `.UNVERIFIED.yaml` and the
script exits non-zero, so unverified data can never be picked up by mistake.

## Quick start (using the Makefile)

**Try the synthetic demo** — no CIBIL report needed:

```bash
make install   # create .venv and install deps
make demo      # parse the anonymized fixture, print the verification report
make web       # then browse http://127.0.0.1:5057
```

The committed `fixtures/cibil-sample.mhtml` is an anonymized scramble
of a real CIBIL report (see [Synthetic fixture](#synthetic-fixture-make-sample--make-demo) below).

**With your own report:**

```bash
make install                          # one-time
cp /path/to/your-report.mhtml src/    # drop the MHTML in src/
make                                  # parse every src/*.mhtml
```

Other targets:

```bash
make help                        # list all targets
make file FILE=path/to/x.mhtml   # parssce mblxpsrc/*.mhtml -> licit file (any path)
make query                       # parie the comtittadvfixtuRPL over the latest output JSON
make web                         # serve the browser UI on http://127.0.0.1:5057
make clean-cache                 # wipe cache/  (intermediates only; safe)
make clean-output                # wipe output/ (DESTRUCTIVE: removes timestamped finals)
make clean                       # clean-cache AND clean-output
make distclean                   # clean AND remove .venv/
```

The parser only handles CIBIL **print-view** reports (saved from
`creditReportPrint.page?print=true`). Feeding it any other MHTML — random
web pages, the consumer-dashboard view at `creditreport.page`, or other
credit bureaus — fails fast at Stage 1 with an actionable error.

VS Code / Cursor / Windsurf users: `.vscode/settings.json` pins the
interpreter to `.venv/bin/python`, so opening the folder picks it up
automatically.

## Privacy & data handling

This repo is intentionally PII-free:

- **`cibil-sample.mhtml` is a fully anonymized scramble of a
  real CIBIL report.** Name, DOB, PAN, Voter ID, Passport, DL, CKYC,
  mobile numbers, emails, addresses, account numbers, currency
  amounts, lender names, and dates have all been replaced with
  deterministic synthetic values from fixed pools. CSS / SVG / image
  MIME parts pass through verbatim because they're CIBIL brand assets
  with no user-specific bytes. The CIBIL score itself (a closed-vocab
  integer in 300–900) is preserved.
- **No real CIBIL report lives in this repo, in the git history, or
  on any branch.** The `.gitignore` keeps `src/`, `cache/`, and
  `output/` permanently off-limits to `git add`.

If you fork this repo to run on **your own** report:

- Drop your `.mhtml` into `src/` — gitignored, never tracked.
- The browser UI binds to `127.0.0.1` only and refuses to serve
  `*.UNVERIFIED.*` files. **Do not** pass `--host 0.0.0.0` or
  otherwise expose the port to a network — `output/*.json` contains
  your full PII (PAN, account numbers, payment history, etc.).

## Browser UI (`make web`)

```bash
make web                # foreground (Ctrl+C to stop) -- good for debugging
make web start          # background daemon; writes .web.pid + .web.log
make web stop           # SIGTERM the daemon and clean up
make web status         # is the daemon running?
make web restart        # stop + start
```

The default URL is `http://127.0.0.1:5057`. Override on the command line:

```bash
make web start WEB_PORT=8080
make web start WEB_HOST=127.0.0.1 WEB_PORT=5060
```

`make web start` spawns `web_app.py` with `nohup`, writes the PID to
`.web.pid` and the server's stdout/stderr to `.web.log`. It is idempotent:
running `make web start` twice does **not** spawn a second copy.
`make web stop` sends SIGTERM and falls back to SIGKILL after 0.3 s if the
process doesn't exit. Both files are listed in `.gitignore`.

The UI itself is a small Flask server (`web_app.py`) over the latest
verified `output/*.json` plus a single-page client from `templates/index.html`
+ `static/app.js`. It mirrors the `query_cibil` REPL: a left-rail with
`Summary` / `Personal` / `Accounts` / `Enquiries` / `Addresses` / `Contacts`
/ `Emails` / `Identification`, a top-bar universal search box (same
substring matcher as the REPL), a snapshot-file dropdown to switch between
runs in `output/`, and a CIBIL score badge that colors itself by score band.
Clicking an account row drills into the full account-details + payment-status
+ a year×month payment-history grid (DPD values colour-graded; toggle
"show full history" for the full span).

The page uses Tailwind via CDN and a tiny vanilla-JS client; **no Node
toolchain or build step**.

API surface (all GET, JSON):

```
/api/files                      list snapshot files in output/
/api/data?file=NAME             full JSON dict for a snapshot (or latest)
/api/search?q=TERM&file=NAME    REPL-style substring search hits
/api/record/<section>/<i>       single record from a section
```

**Security**: the server binds to `127.0.0.1` only by default and refuses
to serve `*.UNVERIFIED.*` files. The data contains real PII (PAN, account
numbers, phone numbers, full payment history) so do **not** pass
`--host 0.0.0.0` or otherwise expose this port to a network. Direct flags:

```bash
.venv/bin/python web_app.py --port 5057            # default
.venv/bin/python web_app.py --output-dir /tmp/o    # alternate snapshot dir
.venv/bin/python web_app.py --debug                # auto-reload + tracebacks
```

## Direct script invocation (advanced)

```bash
.venv/bin/python parse_cibil.py src/your-report.mhtml
```

Useful flags:

```bash
--stage {1,2,3,4,5,all}   stop after a given stage (incremental dev)
--cache-dir <path>        override default cache/ folder
--output-dir <path>       override default output/ folder
--clean-cache             wipe cache/ before running (force fresh decode)
```

Examples:

```bash
.venv/bin/python parse_cibil.py src/your-report.mhtml --stage 1
.venv/bin/python parse_cibil.py src/your-report.mhtml --clean-cache
.venv/bin/python parse_cibil.py src/your-report.mhtml \
    --output-dir /tmp/cibil-archive
```

## Output schema (top-level keys, in order)

```
report                   { source_title, control_number, date, greeting }
cibil_score              { score, as_of_date }
personal_details         { name, date_of_birth, gender, [cibil_remarks, dispute_date] }
identification_details   [ { identification_type, id_number, issue_date, expiry_date }, ... ]
address_details          [ { address, category, residence_code, date_reported }, ... ]
contact_details          [ { telephone_number_type, telephone_number, telephone_extension }, ... ]
email_details            [ "<email>", ... ]
employment_details       { account_type, date_reported, occupation, income, ... }
accounts                 [ {
                             status,                 # "open" | "closed"  (DERIVED)
                             member_name, account_type, account_number, ownership,
                             account_details: {
                               credit_limit, high_credit, current_balance, cash_limit,
                               amount_overdue, rate_of_interest, repayment_tenure,
                               emi_amount, payment_frequency, actual_payment_amount,
                               date_opened_disbursed, date_closed, date_of_last_payment,
                               date_reported_and_certified, value_of_collateral,
                               type_of_collateral, suit_filed_wilful_default,
                               credit_facility_status, written_off_amount_total,
                               written_off_amount_principal, settlement_amount
                             },
                             payment_status: { payment_start_date, payment_end_date },
                             payment_history: [ { month, value }, ... ]
                           }, ... ]
enquiry_details          [ { member_name, date_of_enquiry, enquiry_purpose }, ... ]
footer                   { end_of_report, disclaimer, copyright }
```

The only **derived** value (i.e. not literally present in the MHTML text) is
`accounts[*].status`; it is `"open"` if the card lives in
`<div class="accounts-container open_accounts">` and `"closed"` if it lives in
`<div class="accounts-container closed_accounts">`. The verification stage
asserts this invariant explicitly.

## Verification report

`<stem>.<timestamp>.verification.txt` records:

- counts: total leaves, exact text matches, whitespace-soft matches, derived OK,
  source misses, derived misses, empty/None skipped;
- structural counts: extracted vs HTML for open accounts, closed accounts,
  enquiries (must all match exactly);
- a bullet list of every failed leaf (path + value) if any;
- a final `RESULT: OK` / `RESULT: FAIL` line.

## Known design decisions

- All values are strings (per requirement). No date or currency parsing.
- ₹ (`U+20B9`) is preserved end-to-end because Stage 1 reads the raw MIME
  payload bytes and decodes UTF-8 explicitly. The CIBIL MHTML does not declare
  a charset on its `text/html` part, so the default Python decoding produces
  `U+FFFD` replacement characters; Stage 1 explicitly avoids that path and
  raises if any `\ufffd` survives.
- JSON↔YAML round-trip equality is enforced after Stage 4. PyYAML quotes
  ambiguous scalars (CIBIL scores like `'707'`, all-digit account numbers,
  numeric-looking dates, etc.) automatically so the string types survive
  round-trip.
- Account-detail tab-pane IDs differ between open (`acDetails-N`) and closed
  (`acDetails--N`) cards in the source HTML; both forms are handled.
