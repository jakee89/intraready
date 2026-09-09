# Invoice Extraction and Manual Mapping Repair Plan

## Goal

Make invoice processing feel simple:

1. Upload an invoice.
2. IntraReady extracts the supplier, invoice details, goods, charges, weights, CN/TARIC codes, and origins.
3. The user reviews highlighted suggestions and corrects anything uncertain.
4. Confirmed corrections improve later invoices from that supplier.
5. IntraReady prepares the correct Intrastat rows and XML.

The normal user must not need to understand OCR, AI models, prompts, coordinates, or supplier templates.

## Problems to Fix

- The app does not clearly prove whether Ollama, the configured model, or GPU acceleration is working.
- AI errors are reduced to messages such as `HTTPError`, which are not useful for diagnosis.
- The current AI is expected to understand a whole invoice without enough reliable layout information.
- Extracted values do not consistently distinguish SKU, barcode/EAN, CN/TARIC code, description, and charge lines.
- AI output can become application data before sufficient validation.
- Supplier learning is not clear or dependable.
- The manual mapper uses unreliable screen coordinates. Boxes can appear in a corner, fail to follow the pointer, or disappear.
- Mapping repeating product rows one field at a time is too difficult.

These parts should be rebuilt as one extraction workflow rather than receiving more isolated patches.

## Required User Experience

### Upload

After upload, show one clear processing state:

- `Reading PDF`
- `Applying saved supplier layout`
- `Reading with local AI`
- `Checking extracted values`
- `Ready for review`
- `Needs manual help`

Processing must run as a background job. The page must remain responsive.

### Review

Show the invoice beside the extracted fields and goods rows. Selecting a suggested value should highlight its source area on the invoice.

Use three confidence states:

- **Confident**: supported by clear invoice evidence and validation.
- **Check**: plausible but uncertain.
- **Missing**: the user must supply it.

Never mark an invoice reviewed automatically. Re-extraction must not overwrite fields the user has already confirmed unless they explicitly choose to replace them.

### One-click preparation

`Prepare invoice` should:

1. Apply a confirmed supplier template when one matches.
2. Use deterministic PDF extraction and OCR layout data.
3. Use local vision AI only for fields or rows still unresolved.
4. Apply confirmed product memory using supplier plus SKU.
5. classify charges and link them to goods.
6. calculate total net mass and supplementary quantities.
7. validate totals and display the result for review.

## AI Health and Diagnostics

Add an `/api/ai/status` endpoint and a small status indicator in the app.

The endpoint should report:

- whether Ollama is reachable;
- Ollama version;
- configured model name;
- whether that model is installed;
- whether the model is loaded;
- CPU or GPU processing, when Ollama reports it;
- response time;
- the last extraction error, including HTTP status and response body;
- setup/download progress when the model is being installed.

User-facing states:

- `Local AI ready`
- `Loading local AI`
- `Using CPU — slower`
- `Local AI unavailable`

Provide a `Test local AI` button that processes one small test image and reports success, time, and processor. Do not expose model configuration in the normal invoice workflow.

The current GTX 1060 6 GB should use a small vision model by default. Accuracy must come from combining PDF text, OCR positions, templates, validation, and AI rather than asking a small model to solve the entire invoice alone.

## Extraction Architecture

### 1. Build a canonical document layout

For every PDF page, collect:

- native PDF words and their bounding boxes;
- OCR words and bounding boxes when native text is missing or unreliable;
- rendered page image;
- detected lines, columns, and table regions;
- page dimensions and rotation.

Store all coordinates in PDF page coordinates. Screen size, zoom, scrolling, and device pixel ratio must never change saved coordinates.

### 2. Use extraction methods in this order

1. Confirmed supplier template.
2. Reliable native PDF table/text extraction.
3. Known deterministic supplier adapter.
4. Vision AI using the page image plus positioned text.
5. Manual review or manual mapping for unresolved data.

A method may fill only the fields it understands. It must not erase stronger results from an earlier method.

### 3. Give AI a strict invoice task

The AI request must describe the business purpose and required schema. It must be told to:

- identify supplier fields and invoice fields;
- preserve each source invoice line before deciding its Intrastat treatment;
- distinguish SKU/article code, EAN/barcode, CN/TARIC code, and description;
- distinguish goods, printing/engraving/setup charges, freight, insurance, tax, discount, and unrelated services;
- return `null` when evidence is absent;
- provide page number and source bounding boxes for every suggested value;
- avoid inventing CN codes, origins, weights, quantities, or prices;
- return structured JSON matching a versioned schema.

AI output is a candidate result. It must pass deterministic validation before appearing as prepared data.

### 4. Validate and reconcile

Validation must check:

- quantity × unit price against line amount where available;
- sum of lines, discounts, freight, tax, and charges against invoice totals;
- plausible date, currency, country, VAT, and code formats;
- CN/TARIC length and existence in the configured annual nomenclature;
- whether the CN code requires supplementary quantity and unit;
- grams-to-kilograms conversion;
- total net kg = quantity × unit net kg;
- duplicate or repeated lines;
- confusion between SKU, EAN, and CN code;
- charge links and allocation;
- invoice value and statistical value reconciliation.

When validation fails, perform at most one focused AI repair request containing only the failed fields and validation messages. Then send unresolved fields to review.

## Canonical Invoice Data

### Header fields

- supplier name;
- supplier VAT country and number;
- invoice reference;
- invoice date;
- currency;
- invoice total;
- dispatch/arrival date when present;
- country of consignment;
- delivery terms;
- mode of transport;
- nature of transaction.

### Source line fields

- source line number;
- line type;
- SKU/article code;
- barcode/EAN as a separate field;
- description;
- quantity and unit;
- unit price;
- line amount;
- CN/TARIC code;
- country of origin;
- unit net mass and its original unit;
- source page and bounding boxes;
- extraction method and confidence.

### Line types

- goods;
- printing, engraving, setup, or packaging charge;
- freight;
- insurance;
- discount;
- tax;
- other service;
- unknown.

## Intrastat Preparation Rules

These rules must produce reviewable suggestions and keep their source lines for audit:

- Printing, engraving, setup, and similar costs for goods supplied already decorated should normally be linked to the goods and included in their invoice value.
- VAT should not be included in Intrastat value.
- Freight and insurance treatment must be shown separately and allocated according to the configured Intrastat rule.
- Charges must never become fake goods rows.
- Variants may be combined only when all declaration dimensions match, including CN code, origin, transaction nature, delivery terms, transport, and supplementary unit requirements.
- Product memory should store unit net kg. Exported net mass should use quantity × unit net kg.
- Supplementary quantity must be requested only where the selected annual CN dataset requires it.
- Suggested CN codes must remain unconfirmed until reviewed. The system must show why each candidate was suggested.

## Supplier and Product Learning

Identify a supplier primarily by VAT number, with reviewed aliases as a fallback.

For each supplier, save versioned templates containing:

- a document fingerprint;
- page size and orientation;
- anchor text and relative positions;
- fixed-field regions;
- table region, header position, column boundaries, and row rules;
- charge labels and treatments;
- last successful use and success rate.

Do not assume every invoice from a supplier has the same layout. Match the fingerprint and anchors first. If the layout changed, use AI and ask the user whether to save a new template version.

Learn product data only after confirmation. The strongest product key is supplier plus SKU. Description matching may suggest a product but must not silently replace it.

## Manual Mapping Rebuild

The current mapper should be replaced rather than patched again.

### Rendering

Use one HTML canvas for both the invoice page and every overlay. The canvas must:

- draw the page image;
- draw the live rectangle in bright red while the pointer moves;
- draw saved rectangles with a solid red border and translucent fill;
- show the mapped field label on each box;
- redraw after zoom, resize, page change, scroll, and reload.

Pointer coordinates must be calculated from `canvas.getBoundingClientRect()` and transformed into the canvas's intrinsic coordinate system. Saved regions must then be converted to PDF page coordinates. Account for CSS scaling and `devicePixelRatio` exactly once.

Support mouse, touch, and pen with Pointer Events and pointer capture.

### Fixed fields

The user chooses a field, drags a visible box, and immediately sees:

- the saved red rectangle;
- extracted preview text;
- `Accept`, `Redraw`, and `Delete` actions.

A tiny accidental selection must be rejected instead of being stored in a corner.

### Product table

Do not require the user to draw every row. Use this flow:

1. Draw one rectangle around the complete product table, excluding totals.
2. Mark the header row.
3. Add or drag vertical column boundaries for SKU, description, quantity, unit price, amount, CN code, origin, and weight.
4. Show an immediate row preview below the mapper.
5. Let the user correct column assignments and ignored rows.
6. Test the template on the current invoice before saving.

Allow multi-page table regions and page-specific mappings.

### Template activation

A template cannot become active until:

- all required mapped fields have readable previews;
- the table preview contains at least one plausible goods row;
- validation completes;
- the user confirms the preview.

Offer an optional test against a second invoice from the same supplier before activation.

## Proposed Backend Functions

```text
inspect_ai_runtime()
build_document_layout(invoice_id)
fingerprint_invoice_layout(document_layout)
match_supplier_template(supplier, fingerprint)
extract_with_supplier_template(document_layout, template)
extract_with_native_layout(document_layout)
extract_with_vision(document_layout, unresolved_fields)
validate_extraction(candidate)
repair_failed_fields(candidate, validation_errors)
prepare_intrastat_rows(candidate, product_memory, cn_dataset)
learn_from_confirmed_invoice(invoice_id)
preview_supplier_template(template, invoice_id)
activate_supplier_template(template_id)
```

Suggested routes:

```text
GET  /api/ai/status
POST /api/ai/test
POST /api/invoices/{id}/extract
GET  /api/invoices/{id}/extraction-status
GET  /api/invoices/{id}/extraction-runs
POST /api/supplier-templates/preview
POST /api/supplier-templates
POST /api/supplier-templates/{id}/activate
```

## Data to Store

Add versioned records for:

- extraction runs and processing time;
- method used for each field;
- candidate values, confidence, page, and source boxes;
- validation messages;
- supplier template versions and fingerprints;
- template fields, table regions, and columns;
- user corrections and confirmations;
- product memory keyed by supplier and SKU.

Keep the original invoice and original extracted lines unchanged for audit. Prepared Intrastat rows should reference their source invoice lines.

## Performance and Reliability

- Queue AI jobs and allow only one GPU inference at a time on this server.
- Cache page rendering, OCR, and canonical layout so retries do not repeat expensive work.
- Skip AI completely when a confirmed template produces a valid result.
- Apply AI only to unresolved pages or fields.
- Set clear timeouts and preserve the full error response.
- If AI is unavailable, continue with deterministic extraction and manual review.
- Never leave an invoice permanently in `Processing`; jobs need timeout and recovery states.

## Implementation Order

1. Add AI health, exact error reporting, job status, and processing telemetry.
2. Build the canonical PDF/OCR layout and provenance model.
3. Add the strict structured AI schema and deterministic validator.
4. Replace the mapper with the single-canvas implementation and table preview.
5. Add versioned supplier templates and fingerprint matching.
6. Connect confirmed corrections to supplier and product learning.
7. Run the full acceptance set before adding more extraction features.

## Acceptance Tests

The rebuild is complete only when all of these pass:

### AI operation

- The status screen proves whether Ollama, the model, and GPU are working.
- A failed request displays the real cause rather than only `HTTPError`.
- An unknown supplier invoice produces useful header and line candidates without a saved template.

### Invoice understanding

- SKU, EAN/barcode, CN/TARIC code, and description are stored in separate fields.
- Quantities, prices, line totals, currency, origins, and weights are taken from the correct columns.
- A unit weight shown in grams is converted to unit kg, and total net kg is calculated correctly.
- Printing/engraving/setup rows are linked to the goods instead of exported as goods.
- Freight is recognized and its treatment is visible for review.
- Invoice totals reconcile or a clear issue is raised.

### Manual mapping

- The red rectangle begins under the pointer, follows it smoothly, and remains visible after release.
- The saved rectangle stays over the same invoice content after zooming, resizing, scrolling, changing page, and reloading.
- Undo, delete, redraw, and clear work without creating duplicate boxes.
- The table mapper produces a correct row preview before saving.
- A confirmed template extracts a second matching supplier invoice without AI.
- A changed supplier layout is detected and does not silently apply the wrong boxes.

### Data safety

- Re-extraction does not overwrite confirmed values.
- No invented value can be marked confident without invoice evidence or confirmed memory.
- Every exported row can be traced back to its invoice lines and highlighted source regions.

## Definition of Done

The feature is done when a normal user can upload a known or unknown supplier invoice, see that the local AI is operational, receive a mostly prepared invoice, correct uncertain fields with visible evidence, and save a working supplier template through a reliable mapper. The next matching invoice should use that template quickly and avoid AI unless validation finds a problem.

