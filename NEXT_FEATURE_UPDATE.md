# Next feature update: smarter invoice extraction and weights

**Status:** Implemented in IntraReady 0.2.0.

## Objective

Make unfamiliar supplier invoices extract automatically with minimal setup, calculate declaration weight correctly, and combine equivalent product variants while preserving a review trail.

## 1. Unit and total net weight

- Add `unit_net_mass` to every goods row and label the existing `net_mass` field **Total net kg**.
- Calculate `total net kg = quantity × unit net kg` whenever quantity or unit weight changes.
- Allow the reviewer to override the calculated total, visibly mark the override, and never silently replace it.
- Store verified unit weight in Product Memory using supplier VAT + SKU as the key.
- On later invoices, suggest the remembered CN code, origin and unit weight, then calculate total weight automatically.
- Export only total net mass. Round the total for each final declaration row to whole kilograms; export 1 kg when the total is below 1 kg.
- Migrate existing data without treating current total weights as unit weights.

## 2. Automatic extraction for any supplier

Use a layered pipeline so supplier-specific layouts remain useful but unknown invoices do not return an empty draft:

1. Extract native PDF text and tables.
2. Run local OCR on pages with missing or poor text.
3. Apply known deterministic supplier adapters.
4. Send the extracted text/table structure to a free local AI model through Ollama when the generic parser is uncertain.
5. Require structured JSON matching the invoice schema and reject malformed output.
6. Save each value with its source page, extraction method and confidence.
7. Show uncertain or missing fields prominently for manual review; never approve automatically.

The Docker setup should include an optional local-AI profile that is simple to enable. The normal app must still work when AI is unavailable. Invoice text stays on the local server. Treat invoice content as untrusted data and ignore any instructions found inside documents.

Suggested components:

- OCR: PaddleOCR or Tesseract
- Local structured extraction: Ollama with a small instruction model suitable for the server
- One **Extract again** button with progress and a plain error message; no model configuration in the normal user interface

## 3. Combining colours and product variants

After extraction, offer **Combine equivalent rows**. Rows may be combined only when all declaration-defining fields match, including:

- invoice and supplier
- CN8 commodity code
- country of origin
- country of consignment/destination
- flow, nature of transaction, transport mode and delivery terms
- supplementary unit and any special commodity fields

Colour, size or description differences alone do not require separate Intrastat rows when those declaration fields are identical. Sum quantity, invoice value, statistical value and total net mass. Preserve every original SKU/colour/description and source page in expandable audit details. Never combine rows with different CN codes, origins or other declaration fields.

## Acceptance checks

- Entering quantity 167 and unit weight 0.071 calculates total net mass 11.857 kg and exports 12 kg.
- A later invoice with the same supplier VAT and SKU suggests 0.071 kg per unit and recalculates for its new quantity.
- An unknown native-text invoice produces suggested header and goods rows rather than an empty draft.
- A scanned invoice is OCR-processed before local AI extraction.
- Equivalent colour variants can be combined without losing their original source details.
- Every extracted or combined value remains manually reviewable before approval.
