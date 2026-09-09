import unittest
from decimal import Decimal

from intrastat_core import (
    allocate_amount, cn_candidate, document_hash, parse_decimal,
    reconciliation_difference,
)


class CoreTests(unittest.TestCase):
    def test_invoice_number_formats(self):
        for raw, separator, expected in [
            ("1.805,96", ",", "1805.96"), ("1 529,00", ",", "1529"),
            ("38,553", ",", "38.553"), ("0,562", ",", "0.562"),
            ("1,805.96", ".", "1805.96"), ("-25,00", ",", "-25"),
            ("1\u00a0529,00", ",", "1529"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(parse_decimal(raw, decimal_separator=separator), Decimal(expected))

    def test_malformed_numbers_are_not_silently_repaired(self):
        for raw in ["1.80,96", "1,2,3", "EUR 20,00", "NaN", "12 34", "", "1,", "1.234 567,00"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_decimal(raw, decimal_separator=",")

    def test_cn_candidates_preserve_leading_zeroes(self):
        self.assertEqual(cn_candidate("8544 4290 90"), "85444290")
        self.assertEqual(cn_candidate("01012100"), "01012100")

    def test_bad_codes_rejected(self):
        for raw in ["854442", "854442909", "85XX429090"]:
            with self.assertRaises(ValueError):
                cn_candidate(raw)

    def test_remainder_allocation_is_exact(self):
        self.assertEqual(allocate_amount("0.05", [1, 1, 1]),
                         [Decimal("0.02"), Decimal("0.02"), Decimal("0.01")])
        for cents in range(101):
            self.assertEqual(sum(allocate_amount(Decimal(cents) / 100, [3, 7, 0, 19])),
                             Decimal(cents) / 100)

    def test_discount_and_zero_allocation(self):
        self.assertEqual(sum(allocate_amount("-12.01", [2, 3])), Decimal("-12.01"))
        self.assertEqual(allocate_amount("0", [1, 2]), [Decimal(0), Decimal(0)])
        self.assertEqual(allocate_amount("10", [0, 2]), [Decimal(0), Decimal(10)])

    def test_invalid_allocation_inputs(self):
        for total, weights in [("1.001", [1]), ("1", []), ("1", [0]),
                               ("1", [-1, 2]), ("NaN", [1]), (1.2, [1]),
                               ("1", ["Infinity"]), (True, [1])]:
            with self.subTest(total=total, weights=weights):
                with self.assertRaises(ValueError):
                    allocate_amount(total, weights)

    def test_sample_invoice_reconciliation(self):
        self.assertEqual(reconciliation_difference("434", ["281", "50", "103"]), 0)
        self.assertEqual(reconciliation_difference("1529", ["338", "426.50"] * 2), 0)
        self.assertEqual(reconciliation_difference("3029.04", [
            "952.50", "20", "70", "30", "1805.96", "25", "89.70", "35.88"]), 0)

    def test_missing_printing_is_detected(self):
        self.assertEqual(reconciliation_difference("1529", ["676"]), Decimal("853"))

    def test_carried_total_double_count_is_detected(self):
        self.assertEqual(reconciliation_difference("434", ["434", "217"]), Decimal("-217"))

    def test_document_hash_depends_on_bytes(self):
        self.assertEqual(document_hash(b"invoice"), document_hash(b"invoice"))
        self.assertNotEqual(document_hash(b"invoice"), document_hash(b"changed invoice"))


if __name__ == "__main__":
    unittest.main()
