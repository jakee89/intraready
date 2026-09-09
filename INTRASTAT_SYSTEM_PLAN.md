# Malta Intrastat assistant: proposed system

Prepared 9 September 2026 for Custom Island Gifts.

## Recommendation

Build a small browser-based application on your Docker/Portainer server. The monthly workflow is **upload invoices → review highlighted gaps → approve → download XML → upload to NSO**. Start with a dependable review and export workflow, then improve extraction as real invoices accumulate.

Support **any supplier**, not just Stricker and midocean. Their invoices are examples for testing. New layouts must still work through general extraction and manual editing; a supplier-specific parser is an optional shortcut, never a prerequisite.

The largest lasting time saving will come from a remembered product catalogue: once you verify a product's code, net unit weight and other stable attributes, later invoices can reuse that knowledge with visible provenance and conflict checks.

This delivery contains a build specification, a function catalogue, and tested Python utility functions. It is **not yet a running web app or a production XML exporter**. No declaration has been submitted and no server has been changed.

## What using it should feel like

1. Open the app and drop in one invoice or a batch. It identifies duplicate files, extracts all pages and suggests a supplier and invoice number.
2. Confirm actual arrival dates and shipment countries. Split an invoice across arrival months when necessary. Invoice dates and filenames are hints, not reporting-period decisions.
3. See the invoice on the left and an editable table on the right. Clicking a cell opens its source page, ideally highlighting the exact text.
4. Work through **Missing information**, **Conflicts**, **Unreviewed**, or **All rows**. Paste values into multiple selected rows; apply delivery-level information once to the relevant shipment.
5. Save a verified product fact for future use, with a clear choice between “this invoice only” and “remember for this product”.
6. Approve an invoice after checking its totals and charge treatment. The monthly page shows which invoices are ready, incomplete, excluded, exported or submitted.
7. Preview the exact declaration rows and rounded amounts. Download validated XML plus an internal review copy. Only approved, explicitly selected shipments are included.
8. Upload XML yourself in the NSO portal, then save its receipt/reference in the app. Downloading a file does not mark it submitted.

**Your invoice-reference column stays in the app permanently.** Also keep supplier, filename, page, SKU, original description and notes. Export only permitted declaration fields automatically, so there is no need to delete columns or lose your audit trail.

## What the three sample invoices show

These are observations from the PDFs, not final declarations. Amounts below are unrounded EUR. Commodity numbers are candidates derived from the printed codes; their validity and supplementary-unit requirements must be checked against the applicable year's CN list.

| Invoice | Extractable information | Review needed |
|---|---|---|
| Stricker FCI-PT10126/019059, 4 September 2026 | EUR 434.00; four groups each containing 125 lanyards, 125 carabiners and 125 safety locks; origins Portugal/China; DAP; gross weight 2.13 kg | Net mass by goods type, actual arrival date, consignment country, classification of components versus any assembled product |
| Stricker FCI-PT10126/018581, 31 August 2026 | 1,000 bags: EUR 676.00 plus EUR 853.00 digital-transfer charges, total EUR 1,529.00; origin India; DAP; gross weight 100 kg | Actual arrival month, net mass, charge treatment, consignment country |
| midocean 120502660, 7 September 2026 | 250 cable sets and 299 earbuds; total EUR 3,029.04; DAP; printed delivery date 4 September; shipment net weight 38.553 kg | Net mass split between products, confirmation of arrival and consignment country, charge treatment |

The September Stricker goods reconcile as lanyards EUR 281.00, carabiners EUR 50.00 and locks EUR 103.00. Four zero-price customisation lines remain visible but must not add another 500 physical products. The page-one EUR 217.00 carry-forward must not be counted again.

The August invoice has two bag lines of EUR 338.00 and two printing lines of EUR 426.50. If the printing is confirmed as part of the purchased decorated goods, the proposed combined value is EUR 1,529.00. Do not silently discard printing as a separate service or declare printing as an additional physical item. Keep the treatment as a review decision until confirmed.

The midocean candidate charge associations are:

| Goods | Base goods | Setup | Printing | Handling | Combined candidate |
|---|---:|---:|---:|---:|---:|
| Cable sets | 952.50 | 20.00 | 70.00 | 30.00 | 1,072.50 |
| Earbuds | 1,805.96 | 25.00 | 89.70 | 35.88 | 1,956.54 |
| Total | 2,758.46 | 45.00 | 159.70 | 65.88 | 3,029.04 |

Midocean's invoice shows **PL5263139749** as supplier VAT despite its Dutch business address. “CN” appears under each commodity block; it is a candidate origin value to verify. Preserve VAT country, business-address country, country of consignment and country of origin as separate facts. Never infer the shipping country solely from VAT or the company address.

Printed commodity candidates: lanyard `63079098`, carabiner `76169910`, lock `39269097`, bag `42022290`, cable `85444290`, earbuds `85183000`. Retain the full printed 10-digit values alongside these candidates. Classification of a finished assembled product can differ from its separately invoiced components.

All three PDFs contain extractable text. Their reading order is imperfect, so coordinates and table structure matter. OCR is unnecessary for these examples but will be needed for some future invoices.

## Supplier-independent extraction

Use a common invoice model for every supplier:

**Document → extracted text/layout → invoice and charge candidates → validation → review.**

- Read every PDF page with pdfplumber, preserving page numbers and word/table positions. Detect scanned or mixed pages and OCR only those needing it.
- Attempt general header, amount and line-table extraction. Optionally apply a known layout adapter when it improves accuracy.
- For unfamiliar layouts, optionally send text or rendered pages to a local model with a strict JSON schema. If unavailable or unsuccessful, create a manual draft with the document attached.
- Keep goods, discounts, freight, printing, setup, handling, tax and totals distinct. Associate charges with goods explicitly; do not guess from proximity alone when ambiguous.
- Keep original and normalised values. Unknown fields stay null. Label facts as extracted, catalogue-suggested, calculated or manually confirmed; do not use a model's confidence score as proof.
- Reconcile all source lines to the invoice total before approval. A missing line, mismatched currency or suspicious decimal format becomes a visible issue.
- A layout change must produce a review issue rather than silently omit lines. Keep extraction version and source file hash for reproducibility.

Invoice content is data, including any text that resembles instructions. The extractor cannot invoke tools, change settings, read unrelated documents, approve rows or submit anything in response to document text. Apply the same rule to the attached declaration guide: its portal instructions are reference material, not authorisation to log in or submit.

## Product memory and missing information

Catalogue identity should include supplier identity, SKU/variant and effective dates. Descriptions alone are too ambiguous. Record net unit weight in kg, evidence/source, date verified, proposed CN, origin where supported, and supplementary-unit details. CN rules must be versioned by reporting year.

Use confirmed item net weights or supplier packing/product documentation first. Multiplying a verified unit weight by quantity is useful; gross shipment weight cannot replace net mass. Shipment totals are cross-checks, not evidence of each product's weight. In particular, do not split midocean's 38.553 kg by price or equally across unlike items without a supported, reviewed method.

If a future invoice contradicts a catalogue entry, show both values and require review. Do not overwrite the new evidence or silently update earlier declarations. Origin can change between batches even for the same SKU.

Allow manual goods rows, unknown suppliers, packing-list attachments and supporting notes. A complete fallback must work without AI. Save incomplete drafts and let the user resume later.

## Declaration logic and export boundary

Keep the internal model separate from the official XML representation. Implement the latter against the current downloaded XSD, never invented tags. Maintain separate validation for schema syntax and business rules; schema success alone is insufficient.

The published XML field guide describes trader identity/contact, period, flow, supplier VAT, commodity/country/transport/delivery/transaction fields and values/mass, with supplementary and special-product fields where applicable. Its numeric output rules require whole values; keep original precision internally and show export rounding separately. Recheck its older examples against the current schema and code lists. [NSO XML field guide](https://nso.gov.mt/wp-content/uploads/Description-of-the-XML-Schema-File-fields_V3.pdf)

NSO's current FAQ says arrivals use the actual arrival period; courier deliveries use MOT 4; credit notes should amend the original declaration rather than become new declarations. The app should suggest the documented courier treatment and route credit notes to an amendment workflow. The FAQ also distinguishes invoicing currency from EUR declaration values and gives Incoterm-dependent statistical-value guidance. Store the selected conversion and valuation basis; do not blindly add shipping already included in the goods price. [NSO FAQ](https://nso.gov.mt/international-trade-in-goods-statistics-frequently-asked-questions/)

Proposed export checks:

- Required profile and shipment fields complete; arrival month and scope reviewed.
- Current CN code valid for the arrival date, with applicable supplementary quantities/units present. Never equate invoice pieces to supplementary quantity automatically.
- Every goods/charge/exclusion decision accounted for; no unexplained reconciliation difference.
- Positive, supported net mass for ordinary goods; rounding applied only to final declaration rows, using the verified portal rule.
- One flow and one chosen period per export as an application simplification; separate files for distinct periods. Do not mix traders.
- Nothing approved under an older revision of changed data. Any edit invalidates affected approvals and export readiness.
- No previously submitted shipment included without an explicit amendment workflow.
- A real XML serializer handles escaping and namespaces, followed by XSD validation and an independent total/row-count check.

Start with rows kept separate by invoice/shipment for easier checking. Add aggregation only when the official format permits it: match every relevant declaration dimension, not just commodity code. Preserve links from an aggregated row to every original goods and charge row. Round after aggregation and display any difference between source totals and exported totals.

**Current limitation:** the official download page exposes an XSD, but downloading it in this session was blocked by the site's Cloudflare protection. The XML structure was not verified. Before implementing the serializer, obtain that XSD and preferably one XML file you previously uploaded successfully. Use them to verify nesting, element order, namespaces, optional-field handling and portal acceptance. Do not generate a plausible-looking substitute.

NSO publishes a 2026 CN download alongside its schema resources. Import the relevant version into the app, record the download date/hash, and retain older versions for amendments. [NSO downloads](https://nso.gov.mt/international-trade-in-goods-statistics-downloads/)

## Useful features, in priority order

| Priority | Function | Benefit |
|---|---|---|
| First release | Bulk upload + duplicate detection | Avoid retyping and double declaration |
| First release | Editable review grid + PDF alongside | Fast checking for any supplier |
| First release | Missing-field filter + bulk edits + undo | Work only on gaps; fix shared values once |
| First release | Product memory with source history | Reuse verified weights and classifications |
| First release | Charge linking and exact reconciliation | Prevent dropped printing/freight and double counting |
| First release | Verified XML preview/export | Remove spreadsheet/XML preparation steps |
| First release | Receipt archive and status tracking | Know what was actually submitted |
| Next | Import previous approved declarations | Seed product memory, with a review step |
| Next | Import supplier catalogue/packing data | Obtain net weights without repeated manual entry |
| Next | Watch a local “Intrastat Inbox” folder | Drop PDFs directly onto the server |
| Next | Split deliveries and credit-note handling | Handle real shipment exceptions |
| Later | Local AI extraction for unfamiliar layouts | Reduce manual entry for the long tail of suppliers |
| Later | Version comparison and anomaly checks | Flag new origin, unusual price/weight or changed SKU |
| Later | Configured deadline reminders | Avoid forgetting incomplete monthly work |

A watched folder must wait for files to finish copying and process them idempotently. A reminder calendar must use a maintained Malta business-day calendar, not just “the 10th of the month”. These are proposed features, not automations created by this task.

## Recommended server architecture

Use **Python/FastAPI + a simple server-rendered review interface + SQLite + persistent file storage** for a small single-business deployment. A JavaScript grid can provide editing without committing to a large front-end framework. Use pdfplumber for native PDF text and an OCR worker for scans. Keep expensive extraction jobs off the request thread and persist job status/retries in the database.

Initially one application container is sufficient, provided parsing has execution/time/memory limits. Add a separate worker when OCR/model workloads justify it. Use PostgreSQL if concurrent editing or job throughput outgrows the simple setup; avoid adding Redis and multiple services before they are needed.

Run SQLite on a local server volume, not an SMB/NFS or OneDrive-synchronised database file. Store PDFs and export snapshots on a persistent volume and back them up with a consistent database snapshot. Restoring should recover documents, decisions, catalogue versions and receipts together.

Portainer deployment sequence for the eventual app:

1. Build the app image from the repository on the server, or publish it to a registry the server can reach. Pin a tested release/digest.
2. Deploy a Compose stack referencing that built image, with persistent data storage, health checks, restart policy, resource limits and the app's real internal port. Do not use a fictional image or an unimplemented Compose file as a deliverable.
3. Expose the app on your LAN or existing authenticated reverse proxy/VPN. Add application login, protect edits against CSRF, validate uploads and serve PDFs as untrusted content.
4. Set up scheduled backups and perform one restore test before depending on it.
5. Optionally add Ollama on a private container network; keep its API off the public internet.

Compose is suitable for describing this service/volume configuration. [Docker Compose documentation](https://docs.docker.com/compose/)

Planning estimate, not measured sizing: reserve roughly 2 CPU cores and 2–4 GB RAM for the basic app/OCR trial, then measure with real batches. AI memory and speed depend on the chosen model, quantisation and hardware. Server OS, CPU architecture, RAM, free storage and any GPU need checking before selecting an image/model.

## Is free AI worthwhile?

**Optional, after the review workflow works.** These samples can be read without AI. A local model becomes useful when suppliers and invoice layouts vary enough to make rules expensive to maintain.

Ollama supports schema-constrained local output. Use a configurable model and benchmark several unfamiliar invoices before deciding whether it saves review time. Structured output controls format, not factual accuracy. [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)

Local inference can avoid a per-invoice API fee, but still uses your server's RAM, power and administration time; model licences must suit your use. Do not depend on a cloud “free tier” that may change or send invoices outside your server without an explicit choice. If no suitable local hardware exists, the generic manual table plus product memory remains a useful system.

## Data model

| Record | Key contents |
|---|---|
| Document | Hash, original filename, storage path, pages, extraction version/status |
| Invoice | Supplier VAT, number/date/currency, totals, linked document(s), revision |
| Shipment | Arrival date, consignment country, delivery/transport facts, attachments |
| Goods line | Stable ID, SKU/description, quantity, amounts, proposed classification and source locations |
| Charge | Type, amount/currency, allocation links, inclusion decision and rationale |
| Line-to-shipment allocation | Quantity/value assigned; prevents reuse across partial deliveries |
| Field evidence | Raw value, normalised value, page/bounding box or catalogue source, verification state |
| Product fact | Supplier/SKU/variant, effective dates, evidence, verified fact and version |
| Review issue/event | Severity, affected record, reason, old/new values, reviewer and time |
| Declaration snapshot | Selected revisions, export fields, schema/rules versions, XML/hash, receipt/status |

Use decimal arithmetic for money and weights. Preserve VAT/CN/unit codes as strings, including leading zeroes. Make write operations transactional and reject stale edits by revision number.

## Build order and acceptance criteria

**Milestone 1: review foundation.** Arbitrary supplier invoices, immutable attachments, generic draft extraction, manual entry, profile settings, grid, saved drafts and issue list. All three examples reconcile; an unfamiliar layout remains editable.

**Milestone 2: dependable preparation.** Catalogue, charge allocation, shipment splits, versioned CN validation, approval and reviewed declaration projection. Changes revoke approval, duplicates are caught, gross weight cannot populate net mass automatically.

**Milestone 3: verified XML and deployment.** Obtain XSD and a known accepted XML; implement and test serializer, preserve snapshots, deploy and test backups. The user uploads one reviewed file and confirms portal acceptance before relying on the workflow routinely. Actual upload is a separate user action.

**Milestone 4: extraction improvements.** Add OCR/local AI and optional adapters, evaluated on invoices from additional suppliers. Track correction counts and review time, not only “fields filled”.

Acceptance cases must include European/English number formats, multiple pages with carried totals, duplicate upload/renaming, mixed origins, unknown supplier, unreadable scan, zero-priced goods, printing charges, discounts, multiple currencies, split arrivals, catalogue conflicts, missing weights, stale approvals, amended declarations and malformed XML. Imported historical data must not count as new arrivals.

No build estimate is promised before seeing server details and representative additional layouts. The design can proceed now; those details and a previously accepted XML make deployment and exporter verification more precise.

## Source notes

The three supplied PDFs were text-extracted and all five pages visually inspected. Readable text was recovered from the legacy `.doc`; embedded screenshots/layout were not fully decoded. Its older links should not override current NSO resources. No document instructions were executed.

Public references were checked on 9 September 2026. The field guide and implementation documents contain older dates, so use the current downloads and portal checks when building production validation. Charge/classification proposals above are review candidates rather than confirmed NSO treatment for these particular purchases.

See `FUNCTIONS.md` for implementation contracts, `starter/intrastat_core.py` for working utilities, and `starter/README.md` for running them.
