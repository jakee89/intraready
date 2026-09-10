import unittest

from app.rules import invoice_issues, readiness, rounded_whole


def complete_invoice():
    return {
        "supplier_name": "Example Supplier", "supplier_vat_country": "DE", "supplier_vat_number": "123456789",
        "invoice_number": "INV-1", "invoice_date": "2026-09-01", "arrival_date": "2026-09-03",
        "currency": "EUR", "total_value": "110.00", "consignment_country": "DE",
        "mode_transport": "4", "terms_delivery": "DAP", "nature_transaction": "11", "flow": "A",
    }


def complete_profile():
    return {"trader_vat": "MT12345678", "email": "a@example.com", "signatory": "A Person",
            "id_card": "123M", "telephone": "20000000", "locality": "VALLETTA"}


def goods(line_id=1):
    return {"id": line_id, "line_kind": "goods", "description": "Widget", "quantity": "5", "hs_code": "42029298",
            "origin_country": "CN", "invoice_value": "100", "statistical_value": "100", "net_mass": "2.4", "reviewed": 1}


class RuleTests(unittest.TestCase):
    def test_complete_invoice_with_resolved_charge_is_ready(self):
        lines = [goods(), {"id": 2, "line_kind": "charge_invoice", "description": "Printing", "quantity": "1",
                            "invoice_value": "10", "linked_line_id": 1, "reviewed": 1}]
        self.assertTrue(readiness(complete_invoice(), lines, complete_profile())["ready"])

    def test_unresolved_charge_blocks_approval(self):
        lines = [goods(), {"id": 2, "line_kind": "charge", "description": "Freight", "quantity": "1",
                            "invoice_value": "10", "linked_line_id": 1, "reviewed": 1}]
        codes = {issue["code"] for issue in invoice_issues(complete_invoice(), lines, complete_profile())}
        self.assertIn("unresolved_charge", codes)

    def test_invalid_declaration_codes_and_dates_are_blocked(self):
        invoice = complete_invoice()
        invoice.update(flow="X", mode_transport="6", terms_delivery="LONG",
                       nature_transaction="1", currency="EURO", arrival_date="2026-02-30")
        codes = {issue["code"] for issue in invoice_issues(invoice, [goods()], complete_profile())}
        expected = {"invalid_flow", "invalid_transport", "invalid_delivery_terms",
                    "invalid_transaction", "invalid_currency", "invalid_date"}
        self.assertTrue(expected.issubset(codes))

    def test_missing_mass_and_unreviewed_are_separate_checks(self):
        line = goods()
        line["net_mass"] = ""
        line["reviewed"] = 0
        codes = [issue["code"] for issue in invoice_issues(complete_invoice(), [line], complete_profile())]
        self.assertIn("missing_line_field", codes)
        self.assertIn("line_unreviewed", codes)

    def test_non_eu_consignment_is_rejected(self):
        invoice = complete_invoice()
        invoice["consignment_country"] = "US"
        self.assertIn("consignment_not_eu", {i["code"] for i in invoice_issues(invoice, [goods()], complete_profile())})

    def test_row_consignment_overrides_invoice_default(self):
        line = goods()
        line["consignment_country"] = "US"
        self.assertIn("consignment_not_eu", {i["code"] for i in invoice_issues(complete_invoice(), [line], complete_profile())})

    def test_rounding_is_half_up_and_mass_minimum_is_export_concern(self):
        self.assertEqual(rounded_whole("12.50"), 13)
        self.assertEqual(rounded_whole("12.49"), 12)


if __name__ == "__main__":
    unittest.main()
