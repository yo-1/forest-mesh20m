"""crs.py のテスト。EPSG コードの実在確認は QGIS 実機で行う（README 参照）。"""
import unittest

from forest_mesh20m.core import crs


class TestEpsg(unittest.TestCase):
    def test_known_codes(self):
        # 系IX の JGD2011 は EPSG:6677、JGD2000 は EPSG:2451 として広く使われている。
        self.assertEqual(crs.epsg_authid(9, crs.DATUM_JGD2011), "EPSG:6677")
        self.assertEqual(crs.epsg_authid(9, crs.DATUM_JGD2000), "EPSG:2451")

    def test_range_ends(self):
        self.assertEqual(crs.epsg_code(1, crs.DATUM_JGD2011), 6669)
        self.assertEqual(crs.epsg_code(19, crs.DATUM_JGD2011), 6687)
        self.assertEqual(crs.epsg_code(1, crs.DATUM_JGD2000), 2443)
        self.assertEqual(crs.epsg_code(19, crs.DATUM_JGD2000), 2461)

    def test_default_datum_is_jgd2011(self):
        self.assertEqual(crs.epsg_code(4), 6672)

    def test_invalid(self):
        for system in (0, 20):
            with self.assertRaises(ValueError):
                crs.epsg_code(system)
        with self.assertRaises(ValueError):
            crs.epsg_code(9, "Tokyo")


class TestLabels(unittest.TestCase):
    def test_labels(self):
        labels = crs.system_labels()
        self.assertEqual(len(labels), 19)
        self.assertTrue(labels[0].startswith("I (1) 系:"))
        self.assertTrue(labels[8].startswith("IX (9) 系:"))
        self.assertTrue(labels[18].startswith("XIX (19) 系:"))
        self.assertEqual(len(set(labels)), 19)


if __name__ == "__main__":
    unittest.main()
