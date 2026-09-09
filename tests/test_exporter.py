import tempfile
import unittest
from pathlib import Path

from lxml import etree

from app.exporter import EXPORT_FIELDS, csv_bytes, declaration_rows, inspect_schema, xml_bytes


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"trader_vat": "MT12345678", "email": "a@example.com", "agent_vat": "", "trading_licence": "",
                        "signatory": "A Person", "id_card": "123M", "telephone": "20000000", "locality": "Valletta"}
        self.invoice = {"id": 7, "flow": "A", "supplier_vat_country": "DE", "supplier_vat_number": "999999999",
                        "consignment_country": "DE", "mode_transport": "4", "terms_delivery": "DAP", "currency": "EUR",
                        "nature_transaction": "11", "invoice_number": "INV-7", "supplier_name": "Supplier"}
        self.lines = [
            {"id": 11, "line_kind": "goods", "description": "Widget", "hs_code": "12345678", "origin_country": "CN",
             "invoice_value": "100.49", "statistical_value": "105.49", "net_mass": "0.4", "supp_qty": "", "supp_unit": ""},
            {"id": 12, "line_kind": "charge_invoice", "description": "Printing", "invoice_value": "10", "linked_line_id": 11},
            {"id": 13, "line_kind": "charge_stat", "description": "Freight", "invoice_value": "5", "linked_line_id": 11},
        ]

    def test_projection_allocates_reviewed_charges_and_keeps_internal_reference(self):
        result = declaration_rows([self.invoice], {7: self.lines}, self.profile, "2026-09")
        self.assertEqual(result[0]["INVOICE_VALUE"], "110")
        self.assertEqual(result[0]["STAT_VALUE"], "120")
        self.assertEqual(result[0]["NET_MASS"], "1")
        self.assertEqual(result[0]["_invoice_reference"], "INV-7")

    def test_csv_contains_internal_reference(self):
        result = declaration_rows([self.invoice], {7: self.lines}, self.profile, "2026-09")
        decoded = csv_bytes(result).decode("utf-8-sig")
        self.assertIn("_invoice_reference", decoded)
        self.assertIn("INV-7", decoded)

    def test_charges_are_aggregated_before_whole_euro_rounding(self):
        lines = [dict(self.lines[0], invoice_value="100.20", statistical_value="100.20"),
                 {"id": 12, "line_kind": "charge_invoice", "invoice_value": "0.30", "linked_line_id": 11},
                 {"id": 13, "line_kind": "charge_invoice", "invoice_value": "0.30", "linked_line_id": 11}]
        result = declaration_rows([self.invoice], {7: lines}, self.profile, "2026-09")
        self.assertEqual(result[0]["INVOICE_VALUE"], "101")
        self.assertEqual(result[0]["STAT_VALUE"], "101")

    def test_schema_driven_xml_validates_and_omits_internal_fields(self):
        fields = "\n".join(f'<xs:element name="{name}" type="xs:string" minOccurs="0"/>' for name in EXPORT_FIELDS)
        schema = f'''<?xml version="1.0"?>
        <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
          <xs:element name="Declarations"><xs:complexType><xs:sequence>
            <xs:element name="Decl" maxOccurs="unbounded"><xs:complexType><xs:sequence>{fields}</xs:sequence></xs:complexType></xs:element>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "official.xsd"
            path.write_text(schema, encoding="utf-8")
            self.assertTrue(inspect_schema(path)["valid"])
            result = declaration_rows([self.invoice], {7: self.lines}, self.profile, "2026-09")
            xml = xml_bytes(result, path)
            self.assertNotIn(b"_invoice_reference", xml)
            self.assertEqual(etree.fromstring(xml).tag, "Declarations")

    def test_html_block_page_is_not_a_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blocked.xsd"
            path.write_text("<!doctype html><title>Blocked</title>", encoding="utf-8")
            self.assertFalse(inspect_schema(path)["valid"])


if __name__ == "__main__":
    unittest.main()
