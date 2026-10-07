import unittest

from forest_mesh20m.core import naming


class NamingTest(unittest.TestCase):
    def test_japanese_kept_symbols_replaced(self):
        self.assertEqual(naming.sanitize_layer_name("富士川/笛吹川 流域"), "富士川_笛吹川_流域")

    def test_empty_label_falls_back_to_number(self):
        self.assertEqual(naming.make_layer_names("fr_", [None, "", "  "]), ["fr_zone001", "fr_zone002", "fr_zone003"])

    def test_duplicates_get_suffix_case_insensitive(self):
        self.assertEqual(naming.make_layer_names("", ["A", "a", "A"]), ["A", "a_2", "A_3"])

    def test_length_limited(self):
        self.assertLessEqual(len(naming.sanitize_layer_name("あ" * 200)), naming.MAX_NAME_LENGTH)

    def test_quotes_removed(self):
        self.assertEqual(naming.sanitize_layer_name('a"b\'c'), "a_b_c")


if __name__ == "__main__":
    unittest.main()
