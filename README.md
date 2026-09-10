# IntraReady

## Supplier learning and CN checks

- On an invoice, use **Remember supplier** after checking the shipment defaults. Approval also saves them automatically. Future PDFs are matched by supplier VAT/name and reuse flow, currency, consignment country, transport mode, Incoterm and transaction nature.
- Confirmed supplier layouts use the fast local reader and consume no API credits. Uploading never calls an external AI. For an unknown or changed layout, the user can explicitly click **Learn layout with AI**, approve sending that PDF to OpenAI, and save a new versioned supplier template.
- CN codes are checked against the 2026 EU Combined Nomenclature. The required supplementary unit is filled automatically; piece/pair quantities are copied from the goods quantity and all other required quantities remain visible for manual entry.
- The bundled reference comes from the Spanish Tax Agency's EU CN 2026 workbook: https://sede.agenciatributaria.gob.es/static_files/Sede/Tema/Aduanas/Comercio_exterior/Nomenclaturas/2026/CN2026_Structure.xlsx

IntraReady is a private, browser-based workspace for turning supplier invoices into reviewed Intrastat declaration data. It accepts invoices from any supplier. Known layouts can improve extraction, while unfamiliar PDFs always remain usable through the editable review screen.

The app intentionally stops before government submission. It prepares and validates the file; the user uploads it to the Malta NSO portal and records the receipt reference.

## Included now

- Multiple PDF upload with file-signature, size and exact-duplicate checks
- Native PDF text extraction and safe manual fallback
- Automatic local PDF/OCR extraction plus OpenAI PDF understanding for unfamiliar supplier layouts
- Tested layout adapters for the supplied Stricker and midocean examples
- Side-by-side PDF and editable invoice/line review
- Blocking issue list, row filters, tooltips and in-app guide
- Separate supplier VAT country, origin country and consignment country
- Row-level country of consignment for invoices containing orders shipped from different EU countries
- Charge decisions: add to invoice value, statistical value only, or exclude
- Charge allocation preview with value, weight, quantity, equal or single-product splitting
- Exact reconciliation of goods/charges against the invoice total
- Approval invalidation after edits or organisation-profile changes
- Effective product memory keyed by supplier VAT and SKU
- Remembered unit net weight with automatic total-row weight calculation
- Safe combination of equivalent colour or description variants
- Arrival and dispatch flows with period preview based on the actual movement date
- Supplementary quantity and special commodity fields where the Malta format requires them
- Export history with a place to record the NSO portal receipt reference
- Internal review CSV containing invoice references
- XML generation only after an official XSD passes inspection; every XML is validated against that XSD
- Immutable stored export copies and audit events
- Optional HTTP Basic Authentication for a private server
- Secure owner setup, Argon2id passwords, server-side sessions and CSRF protection
- Organisation-scoped accounts with owner, administrator, preparer, reviewer and viewer roles
- Platform administration dashboard with account approval, usage visibility and closed/approval-only registration
- Inactive billing plans and entitlement storage; this version cannot create payments or charges
- Encrypted in-app OpenAI key storage, model selection, budget controls and token/cost dashboard
- Supplier and Layout Centre with extraction history, reusable defaults, mapped fields and one-click version rollback

CN-list importing, receipt-file upload and automatic NSO portal submission are not included. Manual review remains required. The XML schema is not bundled because the NSO download returned a Cloudflare block page during development; use the one-time schema upload in Organisation settings.

## Run locally

Python 3.12 is recommended.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). Persistent files are placed in `data/`, which is ignored by Git.

Run all tests:

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python -m unittest discover -s starter -v
```

## Docker and Portainer

Copy this project folder to a local disk on the server. Do not place the active SQLite database on OneDrive, SMB or NFS storage.

Create a `.env` file beside `compose.yaml` using `.env.example` and choose a long password. Then build and start:

```sh
docker compose up -d --build
```

Open `http://SERVER-IP:8088`. On the first visit, create the platform-owner account. This account is attached to organisation 1, so invoices already stored in the existing data volume remain available. The one-time setup code is `INTRASTAT_BOOTSTRAP_TOKEN`; upgraded installations may use the existing `INTRASTAT_APP_PASSWORD` value instead. After the owner exists, neither value is accepted as a login password.

Keep registration **Closed** until the app is behind HTTPS and you are ready to approve account requests. When HTTPS is configured, set `INTRASTAT_AUTH_COOKIE_SECURE=true`. Leaving it false is required only for a private plain-HTTP LAN installation.

Create an API key at https://platform.openai.com/api-keys, then save and test it under **Organisation → AI and usage**. The key is encrypted with an installation key held in the persistent data volume and is never returned to the browser. `INTRASTAT_OPENAI_API_KEY` remains available as an environment fallback for upgrades.

For Portainer, either deploy `compose.yaml` from a Git repository (so its build context is available), or build the image on the server first with `docker build -t intraready:0.1.0 .` and create a stack from the same Compose definition after removing its `build:` block. The named `intraready_data` volume contains the database, source PDFs, schema and exports.

For Portainer, deploy `compose.portainer.yaml` from the Git repository so the Docker build context is available. The volume has the stable name `intraready_intraready_data`; set `INTRASTAT_DATA_VOLUME` only if an older installation uses another existing volume name.

Before exposing the service outside the LAN, put it behind an HTTPS reverse proxy or VPN and enable secure cookies. Public SaaS launch still requires verified email delivery, MFA/passkeys, password recovery, PostgreSQL, object storage, background jobs, monitoring, tested tenant isolation and the documented privacy/security gate.

## First-use checklist

1. Open **Organisation** and complete the trader/declarant details.
2. Download the current “XML Schema File” from the official Malta NSO downloads page and upload the `.xsd` in the app. A Cloudflare HTML page will be rejected.
3. Upload PDFs. Open every draft and confirm its flow, actual movement date and country of consignment.
4. Review every goods row. Enter supported net mass and resolve each charge. Mark rows reviewed.
5. Approve the invoice. Open **Declarations**, choose the movement month and inspect the rounded preview.
6. Download the validated XML, validate/upload it on the NSO portal, and retain the portal receipt.

Use one XML accepted by the portal as a final end-to-end compatibility check before depending on the app for routine filing. The app's XSD validation cannot reproduce every portal business rule.

## Backups

Back up the entire `intraready_data` Docker volume, including the SQLite database, PDFs, XSD and export snapshots. Stop the container for a simple fully consistent volume copy, or use SQLite's online backup API in an operational backup job. Perform a restore test to a separate volume before relying on the backup.

## Project structure

```text
app/
  main.py          HTTP API, access controls and workflow commands
  db.py            SQLite schema and transaction helpers
  extractors.py    supplier-independent extraction plus optional adapters
  rules.py         review and declaration-readiness checks
  exporter.py      declaration projection, CSV and XSD-validated XML
  templates/       accessible application shell
  static/          responsive interface and client behaviour
tests/             business-rule, extraction and XML tests
starter/           original low-level utilities retained for reference
compose.yaml       local Docker/Portainer deployment
```

See [INTRASTAT_SYSTEM_PLAN.md](INTRASTAT_SYSTEM_PLAN.md) for the reasoning and future roadmap, and [FUNCTIONS.md](FUNCTIONS.md) for the larger function contract.
