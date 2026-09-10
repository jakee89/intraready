import unittest

from app.extractors import _base_draft, _mapped_value, _parse_midocean, _parse_stricker


class ExtractorTests(unittest.TestCase):
    def test_mapped_labels_are_reduced_to_valid_codes(self):
        self.assertEqual(_mapped_value("terms_delivery", "Incoterm: DAP Suema"), "DAP")
        self.assertEqual(_mapped_value("currency", "Invoice currency EUR"), "EUR")
        self.assertEqual(_mapped_value("currency", "R R R"), "")

    def test_midocean_layout_extracts_goods_and_linked_charges(self):
        lines = [[
            "INVOICE FAKTURA VAT", "Jake Borg Invoice 120502660", "Date 07.09.2026", "Incoterm DAP Msida",
            "VAT no. MOB PL5263139749", "3 in 1 cable set 8544 4290 90 250 PC 3,81 PC EUR 952,50",
            "WMO2150-40 /20056543 CN", "Setting-up costs 20,00", "Total net value excl. VAT: EUR 972,50",
        ]]
        text = "\n".join(lines[0])
        draft = _parse_midocean(_base_draft("invoice.pdf", text, 1), lines, text)
        self.assertEqual(draft["supplier_vat_country"], "PL")
        self.assertEqual(draft["lines"][0]["hs_code"], "85444290")
        self.assertEqual(draft["lines"][1]["linked_position"], 1)

    def test_stricker_repeated_goods_are_aggregated(self):
        page = [
            "Fatura FCI FCI-PT10126/1", "01-09-2026 02-09-2026", "TOTAL 20,00 EUR", "Incoterm: DAP",
            "ABC PD-00000001 Widget blue 2UN 5,000 10,00 0,0", "TARIC Code: 1234567890 Product Origin: China",
            "ABC PD-00000001 Widget blue 2UN 5,000 10,00 0,0", "TARIC Code: 1234567890 Product Origin: China",
            "NIF- PT501888640",
        ]
        text = "\n".join(page)
        draft = _parse_stricker(_base_draft("invoice.pdf", text, 1), [page], text)
        self.assertEqual(len(draft["lines"]), 1)
        self.assertEqual(draft["lines"][0]["quantity"], "4")
        self.assertEqual(draft["lines"][0]["invoice_value"], "20.00")
        self.assertEqual(draft["lines"][0]["sku"], "ABC")
        self.assertIn("PD-00000001", draft["lines"][0]["notes"])


if __name__ == "__main__":
    unittest.main()
