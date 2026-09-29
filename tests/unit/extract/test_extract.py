import unittest

from jobs.extract.run import parse_fail_after_rows
from jobs.extract.source_spec import SourceSpec


class ExtractTest(unittest.TestCase):
    def test_source_spec_is_declarative(self) -> None:
        source = SourceSpec(
            name="interactions", table="interactions", primary_key="interaction_id", primary_key_type="bigint"
        )
        self.assertEqual(source.name, "interactions")

    def test_parse_fail_after_rows_empty_is_disabled(self) -> None:
        self.assertIsNone(parse_fail_after_rows(""))
        self.assertIsNone(parse_fail_after_rows("   "))

    def test_parse_fail_after_rows_parses_source_and_count(self) -> None:
        self.assertEqual(parse_fail_after_rows("interactions:5000"), ("interactions", 5000))
        self.assertEqual(parse_fail_after_rows(" products:3 "), ("products", 3))

    def test_parse_fail_after_rows_rejects_malformed_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_fail_after_rows("interactions")
        with self.assertRaises(ValueError):
            parse_fail_after_rows(":5000")


if __name__ == "__main__":
    unittest.main()
