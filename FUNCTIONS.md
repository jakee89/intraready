# Function catalogue

These contracts describe the proposed application. Only the small utilities listed as implemented below currently exist. All functions accept any supplier; adapters enrich this common workflow.

## Implemented starter utilities

| Function | Purpose |
|---|---|
| `document_hash(data)` | SHA-256 for detecting identical uploaded file bytes |
| `parse_decimal(text, decimal_separator=...)` | Strict locale-aware number parsing; no ambiguous auto-detection |
| `cn_candidate(raw)` | Convert a printed 8/10-digit code to an 8-digit candidate; does not classify goods or validate annual CN membership |
| `allocate_amount(total, weights)` | Allocate an approved amount in cents without losing remainder; the caller must justify the chosen weights |
| `reconciliation_difference(expected, amounts)` | Exact difference between source total and included amounts in the same currency |
| `extract_pdf_text(path)` | Extract page-labelled native text from arbitrary PDFs; marks empty-text pages for review/OCR |

The utilities do not approve information, determine charge treatment, infer weights, allocate reporting periods, produce XML or upload declarations.

## Planned ingestion and extraction

`ingest_document(upload) -> Document`

Validate file signature, size and page limits. Store original bytes using a generated safe identifier and hash. Return an existing record for exact duplicates. A supplier VAT/invoice number/date/total match is a separate possible-duplicate warning, not permission to delete a document.

`extract_document(document_id) -> ExtractionResult`

Extract all pages and their coordinates, preserve raw text, OCR low-text pages when configured, and store method/version. Persist job state. Parsing failures leave the invoice available for manual work.

`propose_invoice(extraction, optional_adapter, optional_model) -> InvoiceDraft`

Use the common goods/charge/header schema for all suppliers. AI receives document content as untrusted data and can only return candidates with source references. No tools, approvals or external actions. Validate the response and reject unsupported values. Unavailable AI is a supported operating mode.

`reconcile_invoice(invoice_revision) -> list[Issue]`

Account for goods, charges, discounts and tax without counting carried totals. Compare quantities times prices where applicable, allowing documented supplier rounding. A reconciled total is necessary but does not prove every row is correct.

## Planned enrichment and review

`suggest_product_facts(supplier, sku, variant, arrival_date) -> list[Suggestion]`

Read effective-dated verified catalogue entries. Return provenance and conflicts; never override explicit invoice evidence silently.

`save_product_fact(candidate, scope, reviewer, revision) -> ProductFact`

Require a deliberate scope choice; preserve history and annual classification validity. A correction to one invoice does not automatically become a permanent rule.

`allocate_charge(charge_id, targets, method, evidence, revision) -> Allocation`

Prefer explicit line association. Otherwise propose a justified method for review, preserve totals in cents and record its basis. Prevent allocating the same charge twice. This function is separate from weight allocation and statistical-value treatment.

`split_arrival(line_id, shipment_allocations, revision) -> list[Allocation]`

Split goods quantities/values across actual shipments and reporting periods. Enforce that allocated totals do not exceed the original line. Preserve remaining undeclared quantities.

`validate_draft(invoice_revision, profile, rules_version) -> list[Issue]`

Check scope, identities, arrival dates, valid classifications and conditional fields, supported net mass, valuation, currency and reconciliation. Include machine-readable issue codes, severity, source location and a plain-language remedy.

`bulk_edit(row_ids, changes, expected_revisions) -> ReviewEvent`

Preview affected rows, scope edits to selected shipments, reject stale revisions, store old/new values and support undo. Invalidate affected approvals in the same transaction.

`approve_invoice(invoice_id, expected_revision, reviewer) -> Approval`

Approve only the checked revision with zero blocking issues and acknowledged warnings. Store a hash/revision of dependent profile, catalogue and rules. Later dependency changes trigger revalidation before export.

## Planned export and history

`build_declaration(period, shipment_ids, profile_revision) -> Preview`

Select explicit approved arrivals, prevent accidental reuse of submitted quantities, project approved values into the official schema mapping, and retain internal source links separately. Flag incomplete invoices in the period; never silently imply they are included. Snapshot all inputs.

`aggregate_rows(rows, verified_schema_policy) -> list[ExportRow]`

Optional after the first release. Group only on every required declaration dimension and permitted header boundary. Preserve provenance and exact sums. Do not combine solely by CN code or merge unrelated invoice references blindly.

`serialize_xml(preview, schema_version) -> bytes`

Requires verified real XSD. Serialize only its allowed fields, with correct nesting/order/namespaces, using an XML library. Escape text; prevent external-entity/network resolution when parsing XML. Internal invoice/filename/notes fields are excluded by construction.

`validate_xml(xml, xsd, business_rules, source_snapshot) -> ValidationReport`

Run schema and business checks independently and compare output totals/counts to preview. Do not equate XSD validity with NSO acceptance.

`export_declaration(preview_revision) -> ExportSnapshot`

Recheck input revisions, write immutable XML plus review data and hashes, mark exported only. Repeat downloads return the same snapshot. Any amended content creates a new version.

`record_submission(export_id, receipt, declaration_reference) -> Submission`

Record the user's confirmed NSO outcome and link receipt to the exact export. Handle rejected or partially accepted imports without treating all items as submitted. Reconcile portal result before allowing retries.

`prepare_amendment(original_submission, reason, changes) -> AmendmentDraft`

Keep original history. Route credit notes and corrections for the applicable NSO procedure; do not turn a credit note automatically into a new negative arrival.

`backup_data(destination) -> BackupManifest` and `verify_restore(backup) -> Report`

Take a consistent database snapshot with documents, configuration, source versions, exports and receipts; verify hashes and restore to a separate test location.
