# Working starter functions

This is a small tested foundation, not the finished application. It supports any
supplier because its functions concern source text and common arithmetic rather
than specific invoice layouts. It does not extract structured invoice lines yet.

Python 3.11+ is recommended. Arithmetic utilities/tests use the standard library.
Native PDF text extraction additionally needs `pdfplumber`:

```sh
python -m pip install pdfplumber
python -m unittest discover -s starter -v
python starter/intrastat_core.py /path/to/invoice.pdf --output output/invoice-text.json
```

Run these commands from the project root. The output path must not already exist.
JSON contains original page text and a file hash, marked unreviewed. Scans with no
native text are flagged; OCR and field extraction are future implementation work.
Keep invoice outputs private. No network requests or AI calls are made by this code.

In this workspace, Python is available at:

```powershell
& 'C:/Users/jakeb/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m unittest discover -s starter -v
```

The PDF helper was exercised on all three supplied invoices. Core tests cover
their amount formats and reconciliations, incorrect grouping, lost cents,
discounts, invalid codes, omitted printing and duplicated page carry-forwards.
This verifies these utilities only; it does not establish declaration accuracy or
XML/portal compatibility. Pin production dependencies after integration testing.
