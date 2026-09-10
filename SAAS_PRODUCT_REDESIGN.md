# IntraReady SaaS Product and UX Redesign

## Purpose

Turn IntraReady from a capable single-company tool into a simple, trustworthy Intrastat workspace that can later become a multi-company SaaS product.

The product promise should be:

> Upload invoices, let IntraReady prepare the declaration, review only what needs attention, and export a file accepted by the national portal.

The product must remain useful without AI. AI should accelerate initial supplier setup, classification, and exception handling while every paid action remains visible and controlled.

## Market Research

Research completed September 2026.

### Malta

Malta's NSO provides an online declaration system plus bulk XML upload. Its public documentation says XML can upload many items and suppliers together. The NSO also publishes implementation guidance, an XML schema, an Excel schema template, field descriptions, and CN resources.

Sources:

- [NSO Malta Intrastat overview](https://nso.gov.mt/intrastat/)
- [NSO Malta Intrastat downloads](https://nso.gov.mt/mt/downloads/)
- [NSO online declaration instructions](https://nso.gov.mt/wp-content/uploads/Online-Supplementary-Declarations-INTRASTAT-Instructions.pdf)

The public search did not identify a strong Malta-focused, invoice-first Intrastat SaaS competitor. The visible local workflow is mainly the official portal, XML/Excel preparation, general accounting software, and professional service providers. This creates an opportunity for a Malta-first product, but this conclusion is based on publicly visible offerings rather than a complete private vendor survey.

### European and international products

| Product | Publicly advertised strengths | Lesson for IntraReady |
|---|---|---|
| Digicust | AI extraction, ERP/invoice ingestion, CN enrichment, multi-country output, audit reasoning and automated filing | Keep AI evidence and reasoning visible; build country packs and integrations |
| AEB | Cloud customs platform, adjustable automation, direct filing, regulatory updates, SAP integration and status tracking | Let customers choose automation level and show declaration status clearly |
| Avalara | Multi-country VAT/Intrastat compliance, integrations, managed services and centralized policy | Support multi-entity workflows and an accountant/agent operating model |
| VAT IT Abacus | Multi-entity compliance, approvals, dashboards, self-service SaaS or managed service | Add teams, approvals, portfolio dashboards and optional partner services |
| Conex EUROPSTAT | Prefilled reference data, internal-system imports and European declarations | Maintain official annual reference data and reusable master data |
| InterLAN intraSTAT | CN8 updates, verification, consolidation, freight allocation, corrections, XML and audit records | Add guided freight allocation, amendments and clear consolidation previews |
| Mercator | One-time product CN/weight setup and supplier defaults, followed by automatic declarations | Product and supplier memory should remain central and easy to understand |
| SAP Intrastat | Purchase-order/billing-document selection, product classification and enterprise controls | Add structured imports and integrations after the invoice workflow is strong |
| Intrafakt | Invoice-based service, API access, multi-entity plans, onboarding and managed filing | Potential pricing can scale by invoice volume and service level |

Sources:

- [Digicust AI Intrastat](https://digicust.com/en/solutions/ai-intrastat/)
- [AEB customs platform](https://www.aeb.com/en/customs-software-platform/index.php)
- [Avalara VAT returns and reporting](https://www.avalara.com/gb/en-gb/products/vat-returns-and-reporting.html)
- [VAT IT Abacus](https://vatit.com/abacus/)
- [Conex EUROPSTAT](https://www.conex.net/be/en/solutions/europstat-intrastat-emebi-software/)
- [InterLAN intraSTAT](https://www.interlan.pl/en/produkty/intrastat-2/)
- [Mercator Intrastat](https://www.mercator.eu/en/modules/intrastat-declaration.chtml)
- [SAP Intrastat](https://help.sap.com/docs/SAP_S4HANA_CLOUD/e8fb3e98bfca45c3a25a4d64fe3e9edf/f58ae2570d9d1470e10000000a44147b.html)
- [Intrafakt](https://intrafakt.pl/en)

## Product Positioning

IntraReady should initially target:

1. Maltese SMEs preparing their own Intrastat declarations.
2. Accountants and agents preparing declarations for several Maltese companies.
3. EU SMEs whose accounting system does not provide dependable country-specific Intrastat output.

Its advantage should be ease of use:

- invoice-first rather than ERP-first;
- AI used only when requested;
- one-time supplier layout learning;
- clear evidence for every suggestion;
- product, supplier and layout memory;
- plain-language validation;
- country-specific export packs;
- usable by a business owner without customs-software training.

## New Information Architecture

Replace the current navigation with seven clear areas.

### 1. Home

The operating dashboard.

### 2. Inbox

Uploaded invoices, email imports and files awaiting preparation.

### 3. Review

Invoices with missing or uncertain data, grouped by declaration period.

### 4. Declarations

Monthly arrival/dispatch workspaces, export history, corrections and submission receipts.

### 5. Library

Products, suppliers, saved layouts, CN classifications and freight rules.

### 6. Reports

Value, weight, country, CN chapter, supplier, exceptions and AI usage.

### 7. Settings

Organisation, declaration rules, AI, integrations, team, security and billing.

Keep **Quick guide** permanently available in the header. Add contextual help inside every empty state and error message.

## Redesigned Dashboard

The dashboard should answer four questions immediately:

1. What must I do now?
2. Is this month's declaration ready?
3. What did automation do?
4. What is costing money?

### Top summary

- current reporting month;
- arrivals status;
- dispatches status;
- days until internal deadline;
- invoices awaiting review;
- blocking errors;
- declaration value and net mass totals.

### Primary action card

Show exactly one recommended next action, such as:

- `Review 3 invoices`
- `Resolve 2 unknown CN codes`
- `Confirm freight allocation`
- `Export September arrivals`

### Processing activity

Show recent uploads with stages:

- uploaded;
- read locally;
- saved layout applied;
- AI requested by user;
- prepared;
- reviewed;
- included in declaration.

### AI usage card

Show:

- API provider and model;
- connection status;
- AI calls this month;
- input, output and total tokens;
- estimated cost this month;
- invoices learned with AI;
- invoices processed locally from saved layouts;
- credits saved through layout reuse;
- monthly budget and percentage used;
- link to detailed usage log.

Never expose the complete API key. Display only a label and last four characters.

### Automation effectiveness

- percentage of invoices requiring no AI;
- percentage prepared without corrections;
- average review time;
- most frequent missing fields;
- supplier layouts needing attention.

## Inbox and Upload Redesign

Use a simple three-step upload experience:

1. Add PDF, image, CSV, Excel or supported e-invoice.
2. Read locally and identify supplier/layout.
3. Show the result and recommended action.

An upload must never call paid AI automatically by default.

For an unknown layout show:

> We have not learned this supplier layout yet. Review manually or use AI once to prepare it and save the layout.

Buttons:

- `Learn layout with AI`
- `Map manually`
- `Enter rows manually`

Before an AI call, show:

- provider and model;
- estimated cost range;
- which file will be sent;
- what will be saved;
- confirmation button.

## Invoice Review Redesign

### Layout

Use a three-part workspace:

- left: source invoice viewer;
- centre: prepared header and goods/charge rows;
- right: issues, evidence and activity.

Allow the right panel to collapse so the data table can use the full screen width.

### Evidence

Clicking an extracted value highlights its source on the invoice. Each value shows:

- source: PDF text, supplier layout, AI, product memory or user;
- confidence;
- page;
- last changed time;
- previous value when changed.

### Multiple orders and shipments

Support order groups within one invoice. Each goods row can have its own:

- order/delivery reference;
- country of consignment;
- mode of transport when required;
- arrival/dispatch date when required;
- Incoterm;
- freight allocation group.

Invoice-level values remain optional defaults. Export always uses row-level values.

### Charges and freight

Freight, insurance, printing, engraving, setup, packaging, handling, discounts and tax must remain separate source rows.

Add a charge allocation assistant:

- link one charge to one goods row;
- split equally;
- allocate by invoice value;
- allocate by net weight;
- allocate by quantity;
- select several target rows manually;
- exclude with a required reason.

Show before/after invoice and statistical value totals before applying allocation.

## Supplier and Layout Centre

Each supplier page should show:

- identity and VAT number;
- aliases;
- defaults;
- layouts and versions;
- layout success rate;
- recent invoices;
- products;
- charge rules;
- API calls used;
- errors and corrections.

### Layout versions

Every manual or AI update creates a version. Provide:

- visual preview;
- source invoice;
- creation method;
- fields and columns mapped;
- invoices successfully processed;
- test against another invoice;
- activate;
- compare versions;
- rollback;
- archive.

Never overwrite a working version.

### Layout drift

Detect changes through page geometry, anchor text, headers and extraction validation. When drift is detected:

- keep the previous template;
- process all reliable fields locally;
- clearly list new or missing fields;
- offer `Update layout with AI` as an explicit paid action;
- save the result as a new version only after preview.

## Product Library

Add:

- supplier SKU and aliases;
- product/order code separate from SKU;
- description history;
- CN8/TARIC classification and effective dates;
- origin history by shipment;
- unit net mass with source and effective date;
- supplementary unit rules;
- materials and intended use;
- product image/specification attachment;
- classification confidence and review owner;
- bulk CSV/Excel import and export.

AI CN suggestions must search only current official candidates, explain the reasoning, show alternatives and require confirmation.

## Declaration Workspace

Create one workspace per organisation, country, period and flow.

Show:

- included invoices and rows;
- excluded invoices and reasons;
- totals by CN, country and supplier;
- differences from the previous period;
- unresolved errors;
- schema version;
- export attempts;
- submission receipt;
- amendment history.

Add a declaration lock after export. Later edits should create a correction draft rather than silently changing the exported declaration.

## Settings Inside the App

Most configuration should move out of Portainer.

### Organisation

- legal name;
- VAT registrations;
- declarant details;
- reporting countries;
- thresholds;
- default flow, transport and transaction nature;
- internal deadline;
- currency rules.

### AI and usage

- enable/disable AI;
- provider;
- model;
- API key add/replace/delete;
- `Test connection`;
- manual-only or policy-based usage;
- per-call confirmation;
- monthly budget;
- usage alerts;
- detailed call log;
- data-sharing explanation;
- retention preference where supported.

The current default remains **manual-only with confirmation**.

### Secure API-key storage

For self-hosted installations:

- accept the key through a password input;
- encrypt it before saving;
- use a server-side master encryption key supplied once at installation;
- never return the decrypted key to the browser;
- show provider, status, creation date and last four characters;
- record who replaced or deleted it;
- retain environment-variable configuration as an administrator fallback.

For SaaS:

- use a managed secrets service;
- use platform-managed AI by default;
- optionally support customer-provided keys;
- isolate secrets and usage per tenant.

### Integrations

- email inbox;
- drag-and-drop upload;
- CSV/Excel mapping profiles;
- API/webhooks;
- OneDrive, Google Drive and Dropbox;
- accounting/ERP connectors;
- SFTP watched folder.

### Team and security

- owner, administrator, preparer, reviewer, accountant and read-only roles;
- invite/remove users;
- multi-factor authentication;
- sessions and devices;
- audit log;
- retention and deletion controls;
- data export;
- organisation switching for agents.

### Billing

- plan;
- usage allowance;
- invoices processed;
- AI usage;
- additional entities;
- payment method;
- invoices/receipts;
- upgrade/downgrade.

## AI Control Functions

AI must operate through a provider-independent service.

```text
configure_ai_provider(organisation_id, provider, encrypted_key, model)
test_ai_connection(organisation_id)
estimate_ai_job(invoice_id, action)
authorise_ai_job(user_id, invoice_id, estimate)
run_invoice_layout_learning(invoice_id, template_base_version=None)
run_layout_drift_update(invoice_id, template_id)
suggest_cn_classification(product_id, nomenclature_year)
explain_extraction_value(invoice_id, field_or_line_id)
record_ai_usage(response_id, tokens, estimated_cost, purpose)
enforce_ai_budget(organisation_id)
revoke_ai_key(organisation_id)
```

Every AI call records:

- organisation and user;
- invoice/product;
- purpose;
- provider and model;
- start/end time;
- status and stable error code;
- provider request ID;
- input/output/cached/reasoning tokens when available;
- estimated cost;
- template version created;
- confirmation event.

Suggested error codes:

```text
AI_DISABLED
AI_NOT_CONFIGURED
AI_AUTHENTICATION
AI_PERMISSION
AI_MODEL_NOT_FOUND
AI_BUDGET_EXCEEDED
AI_RATE_LIMIT
AI_FILE_TOO_LARGE
AI_UNSUPPORTED_FILE
AI_TIMEOUT
AI_PROVIDER_ERROR
AI_INVALID_RESPONSE
AI_VALIDATION_FAILED
AI_TEMPLATE_NOT_SAVED
```

## Core Application Functions

```text
create_organisation()
invite_user()
switch_organisation()
create_reporting_profile()
ingest_document()
identify_supplier()
fingerprint_layout()
match_layout_version()
extract_with_saved_layout()
detect_layout_drift()
validate_source_invoice()
group_invoice_orders()
detect_and_classify_charges()
allocate_charge()
prepare_intrastat_rows()
validate_declaration()
lock_declaration_export()
create_declaration_correction()
record_submission_receipt()
import_products()
import_transactions()
sync_reference_data()
```

## Dashboard and Reporting Functions

```text
get_month_status()
get_next_required_action()
get_review_metrics()
get_automation_metrics()
get_ai_usage_summary()
get_ai_usage_log()
get_supplier_layout_health()
get_declaration_comparison()
export_audit_report()
```

## Proposed Data Model Additions

- tenants/organisations;
- users and memberships;
- reporting profiles;
- encrypted integration secrets;
- AI policies and budgets;
- AI usage events;
- background jobs;
- document pages and evidence regions;
- invoice order/shipment groups;
- supplier aliases;
- versioned supplier layouts;
- layout test results;
- product classification versions;
- charge allocation records;
- country rule packs;
- reference-data releases;
- declaration versions;
- submission receipts;
- subscriptions and usage counters.

Every business table must include `organisation_id`. Tenant access must be enforced in the data-access layer, not only in the UI.

## SaaS Technical Direction

Before public launch:

- migrate SQLite to PostgreSQL;
- move PDFs and exports to encrypted object storage;
- use background workers for OCR and AI;
- use tenant-aware authentication and authorization;
- add email verification, password recovery and MFA;
- encrypt secrets with a managed key service;
- add rate limits, malware scanning and file quarantine;
- add backups and tested restore procedures;
- add structured logs, metrics and alerts;
- add regional data hosting and retention controls;
- document subprocessors and data transfers;
- implement subscription billing and usage metering;
- commission security and privacy reviews.

## Country Packs

Do not hard-code Malta rules throughout the application. Define a country-pack interface:

```text
country metadata
annual CN release
required fields
supplementary units
thresholds and flow rules
rounding rules
validation rules
XML/CSV schema versions
portal instructions
deadlines
amendment rules
```

Malta remains the first supported pack. Add another country only after its output passes that authority's validation workflow.

## Delivery Phases

### Phase 1 — Better self-hosted product

- redesigned navigation and dashboard;
- in-app encrypted API-key setup;
- AI token/cost dashboard and budget;
- supplier/layout centre with comparison and rollback;
- row-level shipment/order groups;
- freight allocation assistant;
- clearer activity and error logs.

### Phase 2 — Teams and integrations

- accounts and roles;
- multiple organisations;
- accountant portfolio;
- CSV/Excel profiles;
- email inbox;
- webhooks/API;
- product bulk management.

### Phase 3 — SaaS foundation

- PostgreSQL;
- object storage;
- background job queue;
- managed secrets;
- subscriptions and metering;
- operational monitoring;
- GDPR controls.

### Phase 4 — Multi-country product

- country-pack framework;
- second-country pilot;
- reference-data update service;
- portal submission integrations where legally and technically available;
- partner/managed-service workflow.

## Acceptance Criteria for the Redesign

- A new user understands the next action without reading documentation.
- API keys can be added, tested, replaced and removed inside the app.
- Uploading alone never spends AI credits.
- The user sees an estimate and explicitly confirms every invoice AI call under the default policy.
- Dashboard usage matches provider token totals and records request IDs.
- A learned supplier layout processes later matching invoices locally.
- Layout drift produces a proposed new version and retains the previous active version.
- A user can compare and restore template versions.
- Multiple orders and consignment countries on one invoice export correctly.
- Freight remains visible and can be allocated with a documented method.
- Every exported value links to its invoice, source row and evidence.
- A declaration cannot silently change after export.
- An accountant can operate multiple organisations without data crossing tenants.
- Malta XML continues to validate against the configured official schema.

## Recommended Next Build

Build Phase 1 as one coherent redesign. Begin with the new navigation, dashboard and settings shell, then implement encrypted AI settings and usage logging, followed by the supplier/layout centre and freight allocation assistant. Preserve the current extraction and export engine behind these new screens until each replacement workflow passes its acceptance criteria.
