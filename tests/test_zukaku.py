"""zukaku.py のテスト。標準ライブラリの unittest のみ使用（pytest でも実行可）。"""
import math
import unittest

import numpy as np

from forest_mesh20m.core import zukaku as z


def _approx_survey_xy(lat, lon, lat0, lon0):
    """平面直角座標の概算 (northing_m, easting_m)。

    子午線弧長 110.95km/度、経度 1 度 = 111.32km*cos(lat) の近似。
    誤差はおおむね数百 m。図郭境界から 2km 以上離れた点の所属確認にのみ使う。
    実測値の検証には使えない（GSI の変換式または pyproj で別途確認すること）。
    """
    northing = (lat - lat0) * 110.95 * 1000.0
    easting = (lon - lon0) * 111.32 * math.cos(math.radians(lat)) * 1000.0
    return northing, easting


class TestSpecExamples(unittest.TestCase):
    """仕様書 参考3 の例そのもの。"""

    def test_fig3_fig5_example_09LD35149199(self):
        # 図5: 末尾 149199 は縦に上から 150 番目、横に左から 200 番目のメッシュ。
        addr = z.parse_jf20m_id("09LD35149199")
        self.assertEqual(addr.system, 9)
        # L=11 -> 11*1500, 5000 の行 3 -> 3*150, 20m の行 149
        self.assertEqual(addr.row, 11 * 1500 + 3 * 150 + 149)
        # D=3 -> 3*2000, 5000 の列 5 -> 5*200, 20m の列 199
        self.assertEqual(addr.col, 3 * 2000 + 5 * 200 + 199)
        self.assertEqual(z.format_jf20m_id(*addr), "09LD35149199")

    def test_fig4_row_then_col_order(self):
        # 図4: セル "001000" は 行 001・列 000。行が先、列が後。
        a = z.parse_jf20m_id("09LD35001000")
        b = z.parse_jf20m_id("09LD35000000")
        self.assertEqual(a.row - b.row, 1)
        self.assertEqual(a.col, b.col)
        c = z.parse_jf20m_id("09LD35000001")
        self.assertEqual(c.col - b.col, 1)
        self.assertEqual(c.row, b.row)


class TestCoordinateToMesh(unittest.TestCase):
    def test_origin_is_top_left_of_K_E_tile(self):
        # 図1: 原点は行 K の上端・列 D|E の境界。
        rows, cols = z.xy_to_rowcol(0.0, 0.0)
        self.assertEqual((int(rows), int(cols)), (15000, 8000))
        self.assertEqual(z.format_jf20m_id(9, 15000, 8000), "09KE00000000")

    def test_extent_corners(self):
        rows, cols = z.xy_to_rowcol(300000.0, -160000.0)
        self.assertEqual((int(rows), int(cols)), (0, 0))
        self.assertEqual(z.format_jf20m_id(9, 0, 0), "09AA00000000")
        # 南東端メッシュの中心
        rows, cols = z.xy_to_rowcol(-299990.0, 159990.0)
        self.assertEqual((int(rows), int(cols)), (29999, 15999))
        self.assertEqual(z.format_jf20m_id(9, 29999, 15999), "09TH99149199")

    def test_boundary_belongs_to_south_and_east_mesh(self):
        # 仮定: 境界線上の点は、その境界を北辺/西辺とするメッシュに入る。
        r, c = z.xy_to_rowcol(299980.0, -159980.0)
        self.assertEqual((int(r), int(c)), (1, 1))
        r, c = z.xy_to_rowcol(299980.0001, -159980.0001)
        self.assertEqual((int(r), int(c)), (0, 0))

    def test_boundary_cases_east_west_axis(self):
        # 境界 easting=100020.0（col 13000 と 13001 の境）。境界ちょうどは東側(13001)、-1e-9 は西側(13000)。
        for easting, expected_col in ((100020.0 - 1e-9, 13000), (100020.0, 13001), (100020.0 + 1e-9, 13001)):
            with self.subTest(easting=easting):
                _, c = z.xy_to_rowcol(50000.0 + 10.0, easting)
                self.assertEqual(int(c), expected_col)

    def test_boundary_cases_north_south_axis(self):
        # 境界 northing=50020.0（row 12498 と 12499 の境）。境界ちょうどは南側(12499)、+1e-9 は北側(12498)。
        for northing, expected_row in ((50020.0 + 1e-9, 12498), (50020.0, 12499), (50020.0 - 1e-9, 12499)):
            with self.subTest(northing=northing):
                r, _ = z.xy_to_rowcol(northing, 10.0)
                self.assertEqual(int(r), expected_row)

    def test_boundary_rule_is_consistent_with_cell_bounds_everywhere(self):
        # セルの西辺・北辺の座標をそのまま逆変換すると、必ずそのセル自身に戻る（全域からの抽出）。
        rng = np.random.default_rng(0)
        rows = rng.integers(1, z.TOTAL_ROWS, 20000)
        cols = rng.integers(0, z.TOTAL_COLS, 20000)
        n_max, e_min, _, _ = z.rowcol_to_bounds(rows, cols)
        # 北辺の座標は「その北辺を持つセル」の内側（南側）に属する。わずかに南の点が自分に戻ることを確認する
        r_back, c_back = z.xy_to_rowcol(n_max - 1e-9, e_min)
        self.assertTrue(np.array_equal(r_back, rows))
        self.assertTrue(np.array_equal(c_back, cols))

    def test_cell_coordinates_are_exact_integers(self):
        # 仕様: セル座標は整数の行・列から整数演算で作るため、座標・辺長・面積に浮動小数点誤差が出ない。
        rng = np.random.default_rng(1)
        rows = rng.integers(0, z.TOTAL_ROWS, 200000)
        cols = rng.integers(0, z.TOTAL_COLS, 200000)
        n_max, e_min, n_min, e_max = z.rowcol_to_bounds(rows, cols)
        self.assertTrue(np.array_equal(n_max, 300000 - rows * 20))
        self.assertTrue(np.array_equal(e_min, -160000 + cols * 20))
        self.assertTrue(np.all(n_max == np.floor(n_max)) and np.all(e_min == np.floor(e_min)))
        self.assertTrue(np.all(e_max - e_min == 20) and np.all(n_max - n_min == 20))
        self.assertTrue(np.all((e_max - e_min) * (n_max - n_min) == 400.0))

    def test_out_of_extent_raises(self):
        bad_points = [
            (300000.0001, 0.0),
            (-300000.0, 0.0),  # 南端ちょうどは row=30000 になるため範囲外
            (0.0, 160000.0),  # 東端ちょうどは col=16000 になるため範囲外
            (0.0, -160000.0001),
            (float("nan"), 0.0),
            (0.0, float("inf")),
        ]
        for n, e in bad_points:
            with self.subTest(n=n, e=e):
                with self.assertRaises(ValueError):
                    z.xy_to_rowcol(n, e)

    def test_array_with_one_bad_point_raises(self):
        with self.assertRaises(ValueError):
            z.xy_to_rowcol([0.0, 1.0e7], [0.0, 0.0])

    def test_in_extent_mask(self):
        mask = z.in_extent_mask(
            [0.0, 300000.0001, float("nan"), -299999.9], [0.0, 0.0, 0.0, 159999.9]
        )
        self.assertEqual(mask.tolist(), [True, False, False, True])

    def test_center_round_trip(self):
        rng = np.random.default_rng(20260930)
        rows = rng.integers(0, z.TOTAL_ROWS, 20000)
        cols = rng.integers(0, z.TOTAL_COLS, 20000)
        n, e = z.rowcol_to_center(rows, cols)
        r2, c2 = z.xy_to_rowcol(n, e)
        np.testing.assert_array_equal(rows, r2)
        np.testing.assert_array_equal(cols, c2)

    def test_bounds_are_20m_and_contain_center(self):
        n_max, e_min, n_min, e_max = z.rowcol_to_bounds(5, 7)
        self.assertEqual(n_max - n_min, 20)
        self.assertEqual(e_max - e_min, 20)
        n_c, e_c = z.rowcol_to_center(5, 7)
        self.assertEqual((n_c, e_c), ((n_max + n_min) / 2, (e_min + e_max) / 2))


class TestJf20mId(unittest.TestCase):
    def test_vector_matches_scalar_and_round_trips(self):
        rng = np.random.default_rng(1)
        rows = rng.integers(0, z.TOTAL_ROWS, 3000)
        cols = rng.integers(0, z.TOTAL_COLS, 3000)
        ids = z.format_jf20m_ids(9, rows, cols)
        self.assertEqual(ids.dtype.itemsize // 4, 12)  # '<U12'
        for i in range(0, 3000, 37):
            self.assertEqual(ids[i], z.format_jf20m_id(9, rows[i], cols[i]))
            addr = z.parse_jf20m_id(str(ids[i]))
            self.assertEqual(addr, (9, int(rows[i]), int(cols[i])))

    def test_ids_are_unique_in_a_file_unit(self):
        # 1 ファイル分（75 万メッシュ）で ID が衝突しないこと。
        rows, cols = np.meshgrid(
            np.arange(10500, 11250), np.arange(9000, 10000), indexing="ij"
        )
        ids = z.format_jf20m_ids(4, rows.ravel(), cols.ravel())
        self.assertEqual(len(np.unique(ids)), 750 * 1000)

    def test_id_length_is_always_12(self):
        for row, col in [(0, 0), (29999, 15999), (15000, 8000), (1234, 5678)]:
            self.assertEqual(len(z.format_jf20m_id(9, row, col)), 12)

    def test_parse_rejects_bad_ids(self):
        bad = [
            "",
            None,
            "09LD35150199",  # 行 150 は範囲外
            "09LD35149200",  # 列 200 は範囲外
            "09LD3514919",  # 桁不足
            "09LD351491999",  # 桁過多
            "09ld35149199",  # 小文字
            "09UD35149199",  # 行文字 U は範囲外
            "09LI35149199",  # 列文字 I は範囲外（A-H）
            "00LD35149199",  # 系 0
            "20LD35149199",  # 系 20
        ]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    z.parse_jf20m_id(text)

    def test_system_validation(self):
        for bad in (0, 20, -1, True, 1.5, "9", None):
            with self.subTest(system=bad):
                with self.assertRaises(ValueError):
                    z.format_jf20m_id(bad, 0, 0)
        self.assertEqual(z.format_jf20m_id(np.int64(1), 0, 0), "01AA00000000")

    def test_format_rejects_out_of_range_rowcol(self):
        for row, col in [(-1, 0), (0, -1), (30000, 0), (0, 16000)]:
            with self.subTest(row=row, col=col):
                with self.assertRaises(ValueError):
                    z.format_jf20m_id(9, row, col)
                with self.assertRaises(ValueError):
                    z.format_jf20m_ids(9, [row], [col])


class TestFileZukaku(unittest.TestCase):
    # 04HE: 行 H=7 -> row 10500 起点、列 E=4 -> col 8000 起点
    def test_quadrant_numbering_nw_ne_sw_se(self):
        self.assertEqual(z.file_zukaku_code(4, 10500, 8000), "04HE1")
        self.assertEqual(z.file_zukaku_code(4, 10500, 9000), "04HE2")
        self.assertEqual(z.file_zukaku_code(4, 11250, 8000), "04HE3")
        self.assertEqual(z.file_zukaku_code(4, 11250, 9000), "04HE4")

    def test_quadrant_edges(self):
        self.assertEqual(z.file_zukaku_code(4, 10500 + 749, 9000 + 999), "04HE2")
        self.assertEqual(z.file_zukaku_code(4, 11249, 8999), "04HE1")
        self.assertEqual(z.file_zukaku_code(4, 11999, 9999), "04HE4")

    def test_vector_matches_scalar(self):
        rng = np.random.default_rng(3)
        rows = rng.integers(0, z.TOTAL_ROWS, 5000)
        cols = rng.integers(0, z.TOTAL_COLS, 5000)
        codes = z.format_file_zukaku_codes(9, rows, cols)
        for i in range(0, 5000, 11):
            self.assertEqual(codes[i], z.file_zukaku_code(9, rows[i], cols[i]))

    def test_parse(self):
        p = z.parse_file_zukaku("04HE2")
        self.assertEqual(tuple(p), (4, 7, 4, 2))
        for bad in ("", None, "04HE5", "04HE0", "04HI2", "04UE2", "4HE2", "04he2"):
            with self.subTest(code=bad):
                with self.assertRaises(ValueError):
                    z.parse_file_zukaku(bad)

    def test_gpkg_file_name(self):
        self.assertEqual(z.gpkg_file_name("04HE2", 2019), "fr_mesh20m_04HE2_2019.gpkg")
        with self.assertRaises(ValueError):
            z.gpkg_file_name("04HE2", 19)
        with self.assertRaises(ValueError):
            z.gpkg_file_name("bad", 2019)

    def test_gpkg_layer_name(self):
        self.assertEqual(z.gpkg_layer_name("04HE2", 2019), "fr_mesh20m_04HE2_2019")


class TestKnownPlacesApproximate(unittest.TestCase):
    """既知地点が想定の図郭に入ることの概算確認（誤差数百 m、境界から 2km 以上離れた点のみ）。

    ±160km（東西）の取り違え、行/列の取り違え、X/Y の取り違えを検出するためのもの。
    """

    def _code(self, lat, lon, lat0, lon0, system):
        n, e = _approx_survey_xy(lat, lon, lat0, lon0)
        rows, cols = z.xy_to_rowcol(n, e)
        return z.file_zukaku_code(system, int(rows), int(cols))

    def test_tokyo_station_is_09LD(self):
        # 系IX 原点 36°00'N, 139°50'E。仕様書の例 "09LD35" は東京付近のはず。
        code = self._code(35.6812, 139.7671, 36.0, 139.0 + 50.0 / 60.0, 9)
        self.assertEqual(code[:4], "09LD")

    def test_kochi_station_is_04HE3(self):
        # 系IV 原点 33°00'N, 133°30'E。高知県の図郭インデックス（仕様書 図2.3）に 04HE がある。
        code = self._code(33.5676, 133.5436, 33.0, 133.5, 4)
        self.assertEqual(code, "04HE3")


if __name__ == "__main__":
    unittest.main()
