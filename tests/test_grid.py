"""grid.py のテスト。"""
import unittest

import numpy as np

from forest_mesh20m.core import grid as g
from forest_mesh20m.core import zukaku as z


class TestMeshRange(unittest.TestCase):
    def test_validation(self):
        bad = [
            (-1, 5, 0, 5),
            (5, 5, 0, 5),  # 空
            (6, 5, 0, 5),
            (0, 30001, 0, 5),
            (0, 5, 0, 16001),
            (0, 5, 5, 5),
        ]
        for args in bad:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    g.MeshRange(*args)

    def test_intersect(self):
        a = g.MeshRange(0, 10, 0, 10)
        self.assertEqual(a.intersect(g.MeshRange(5, 20, 8, 20)), g.MeshRange(5, 10, 8, 10))
        self.assertIsNone(a.intersect(g.MeshRange(10, 20, 0, 10)))  # 辺のみ接する
        self.assertIsNone(a.intersect(g.MeshRange(0, 10, 10, 20)))


class TestFileZukakuRange(unittest.TestCase):
    def test_04HE2(self):
        system, r = g.range_from_file_zukaku("04HE2")
        self.assertEqual(system, 4)
        self.assertEqual(r, g.MeshRange(10500, 11250, 9000, 10000))
        self.assertEqual(r.n_cells, 750_000)
        # 04 系 H 行 (北 90km..60km) の北半分、E 列 (東 0..40km) の東半分。
        self.assertEqual(r.bounds(), (90000.0, 20000.0, 75000.0, 40000.0))

    def test_all_quadrants_tile_the_50000_zukaku_without_overlap(self):
        ranges = [g.range_from_file_zukaku("09LD%d" % q)[1] for q in (1, 2, 3, 4)]
        self.assertEqual(sum(r.n_cells for r in ranges), 1500 * 2000)
        for i, a in enumerate(ranges):
            for b in ranges[i + 1:]:
                self.assertIsNone(a.intersect(b))

    def test_range_corners_map_back_to_same_code(self):
        for code in ("04HE1", "04HE2", "04HE3", "04HE4", "01AA1", "19TH4"):
            with self.subTest(code=code):
                system, r = g.range_from_file_zukaku(code)
                for row, col in [
                    (r.row_start, r.col_start),
                    (r.row_start, r.col_stop - 1),
                    (r.row_stop - 1, r.col_start),
                    (r.row_stop - 1, r.col_stop - 1),
                ]:
                    self.assertEqual(z.file_zukaku_code(system, row, col), code)

    def test_cells_just_outside_belong_to_other_code(self):
        system, r = g.range_from_file_zukaku("04HE2")
        self.assertNotEqual(z.file_zukaku_code(system, r.row_start - 1, r.col_start), "04HE2")
        self.assertNotEqual(z.file_zukaku_code(system, r.row_start, r.col_start - 1), "04HE2")
        self.assertNotEqual(z.file_zukaku_code(system, r.row_stop, r.col_start), "04HE2")

    def test_first_cell_id(self):
        system, r = g.range_from_file_zukaku("04HE2")
        # 行: H 内 0 -> 5000 行 0・20m 行 000。列: E 内 1000 -> 5000 列 5・20m 列 000。
        self.assertEqual(z.format_jf20m_id(system, r.row_start, r.col_start), "04HE05000000")

    def test_invalid_code(self):
        with self.assertRaises(ValueError):
            g.range_from_file_zukaku("04HE9")


class TestRangeFromBounds(unittest.TestCase):
    def test_exact_mesh_boundary_gives_single_mesh(self):
        n_max, e_min, n_min, e_max = z.rowcol_to_bounds(5, 7)
        r = g.range_from_bounds(n_min, e_min, n_max, e_max)
        self.assertEqual(r, g.MeshRange(5, 6, 7, 8))

    def test_slightly_larger_expands_by_one_mesh_each_side(self):
        n_max, e_min, n_min, e_max = z.rowcol_to_bounds(5, 7)
        r = g.range_from_bounds(n_min - 0.5, e_min - 0.5, n_max + 0.5, e_max + 0.5)
        self.assertEqual(r, g.MeshRange(4, 7, 6, 9))

    def test_inner_point_bbox(self):
        r = g.range_from_bounds(62990.0, 4030.0, 63010.0, 4050.0)
        self.assertEqual(r.n_rows, 2)
        self.assertEqual(r.n_cols, 2)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            g.range_from_bounds(10.0, 0.0, 0.0, 10.0)  # 南北が逆
        with self.assertRaises(ValueError):
            g.range_from_bounds(0.0, 10.0, 10.0, 0.0)  # 東西が逆
        with self.assertRaises(ValueError):
            g.range_from_bounds(0.0, 0.0, float("nan"), 10.0)
        with self.assertRaises(ValueError):
            g.range_from_bounds(0.0, 0.0, 300020.0, 10.0)  # 北へはみ出し
        with self.assertRaises(ValueError):
            g.range_from_bounds(0.0, -160020.0, 10.0, 0.0)  # 西へはみ出し
        with self.assertRaises(ValueError):
            g.range_from_bounds(-300020.0, 0.0, 0.0, 10.0)  # 南へはみ出し
        with self.assertRaises(ValueError):
            g.range_from_bounds(0.0, 0.0, 10.0, 160020.0)  # 東へはみ出し

    def test_every_point_in_bbox_is_inside_range(self):
        rng = np.random.default_rng(7)
        n_min, e_min, n_max, e_max = 61234.5, 3456.7, 63999.1, 6789.3
        r = g.range_from_bounds(n_min, e_min, n_max, e_max)
        n = rng.uniform(n_min, n_max, 5000)
        e = rng.uniform(e_min, e_max, 5000)
        rows, cols = z.xy_to_rowcol(n, e)
        self.assertTrue(np.all((rows >= r.row_start) & (rows < r.row_stop)))
        self.assertTrue(np.all((cols >= r.col_start) & (cols < r.col_stop)))


class TestMeshBandArrays(unittest.TestCase):
    def test_arrays_are_consistent(self):
        r = g.MeshRange(10500, 10503, 9000, 9004)
        a = g.mesh_band_arrays(4, r)
        n = r.n_cells
        for arr in a:
            self.assertEqual(len(arr), n)
        np.testing.assert_allclose(a.easting_max - a.easting_min, 20.0)
        np.testing.assert_allclose(a.northing_max - a.northing_min, 20.0)
        np.testing.assert_allclose(a.easting_center, (a.easting_min + a.easting_max) / 2)
        np.testing.assert_allclose(a.northing_center, (a.northing_min + a.northing_max) / 2)
        self.assertEqual(a.ids[0], "04HE05000000")
        self.assertEqual(set(a.file_zukaku.tolist()), {"04HE2"})
        # 中心点を座標変換すると、同じ ID のメッシュに戻る
        rows, cols = z.xy_to_rowcol(a.northing_center, a.easting_center)
        np.testing.assert_array_equal(z.format_jf20m_ids(4, rows, cols), a.ids)

    def test_adjacent_meshes_share_edges(self):
        a = g.mesh_band_arrays(4, g.MeshRange(100, 101, 200, 203))
        np.testing.assert_allclose(a.easting_max[:-1], a.easting_min[1:])


class TestGridLines(unittest.TestCase):
    def test_count_and_extent(self):
        r = g.MeshRange(10, 13, 20, 25)  # 3 行 x 5 列
        lines = g.grid_lines(r)
        self.assertEqual(len(lines), (3 + 1) + (5 + 1))
        n_top, e_left, n_bottom, e_right = r.bounds()
        for ln in lines:
            if ln.kind == "row":
                self.assertEqual(ln.northing_start, ln.northing_end)
                self.assertEqual((ln.easting_start, ln.easting_end), (e_left, e_right))
            else:
                self.assertEqual(ln.easting_start, ln.easting_end)
                self.assertEqual((ln.northing_start, ln.northing_end), (n_top, n_bottom))

    def test_lines_lie_on_mesh_boundaries(self):
        r = g.MeshRange(10, 12, 20, 22)
        rows = {ln.boundary_no: ln.northing_start for ln in g.grid_lines(r) if ln.kind == "row"}
        # row 線 n は row=n のメッシュの北辺
        for n, northing in rows.items():
            if n < r.row_stop:
                self.assertEqual(z.rowcol_to_bounds(n, 20)[0], northing)
        self.assertEqual(sorted(rows), [10, 11, 12])

    def test_size_does_not_scale_with_cells(self):
        _, r = g.range_from_file_zukaku("04HE2")
        self.assertEqual(len(g.grid_lines(r)), 751 + 1001)


class TestParseFileZukakuList(unittest.TestCase):
    def test_separators_and_dedupe(self):
        rs = g.parse_file_zukaku_list("04HE2, 04HE3、04HE2\n04HE4\u3000 04HE1", 4)
        self.assertEqual(len(rs), 4)

    def test_lowercase_and_fullwidth_are_normalized(self):
        expected = g.parse_file_zukaku_list("04HE2", 4)
        for text in ("04he2", "０４ＨＥ２", "04hE2"):
            with self.subTest(text=text):
                self.assertEqual(g.parse_file_zukaku_list(text, 4), expected)
        # 正規化後に重複判定される
        self.assertEqual(len(g.parse_file_zukaku_list("04HE2, 04he2", 4)), 1)

    def test_errors(self):
        for text, system in (("", 4), (None, 4), (" , ", 4), ("04HE2", 9), ("04HE9", 4), ("xx", 4)):
            with self.subTest(text=text, system=system):
                with self.assertRaises(ValueError):
                    g.parse_file_zukaku_list(text, system)


class TestCoveringFileZukaku(unittest.TestCase):
    def test_single_zukaku_range(self):
        _, r = g.range_from_file_zukaku("04HE2")
        self.assertEqual(g.covering_file_zukaku(4, r), [("04HE2", r)])

    def test_range_across_four_zukaku_in_north_south_west_east_order(self):
        r = g.MeshRange(11249, 11251, 9999, 10001)  # 04HE2 / 04HF1 / 04HE4 / 04HF3 の角
        got = g.covering_file_zukaku(4, r)
        self.assertEqual([c for c, _ in got], ["04HE2", "04HF1", "04HE4", "04HF3"])
        self.assertEqual(sum(part.n_cells for _, part in got), r.n_cells)
        for code, part in got:
            self.assertEqual(part.n_cells, 1)
            self.assertEqual(z.file_zukaku_code(4, part.row_start, part.col_start), code)

    def test_whole_returns_full_zukaku(self):
        r = g.MeshRange(11249, 11251, 9999, 10001)
        got = g.covering_file_zukaku(4, r, whole=True)
        self.assertEqual([part.n_cells for _, part in got], [750_000] * 4)
        # 全図郭は互いに重ならず、元の範囲を覆う
        for i, (_, a) in enumerate(got):
            for _, b in got[i + 1:]:
                self.assertIsNone(a.intersect(b))

    def test_across_50000_letter_boundary(self):
        r = g.MeshRange(1499, 1501, 0, 1)  # A 行と B 行の境
        self.assertEqual([c for c, _ in g.covering_file_zukaku(9, r)], ["09AA3", "09BA1"])

    def test_extent_area_matches_sum(self):
        r = g.range_from_bounds(-40000.0, 0.0, 20000.0, 30000.0)
        parts = g.covering_file_zukaku(8, r)
        self.assertEqual(sum(p.n_cells for _, p in parts), r.n_cells)
        codes = [c for c, _ in parts]
        self.assertEqual(len(codes), len(set(codes)))


class TestChunking(unittest.TestCase):
    def test_rowcol_grid_order_is_row_major_north_to_south(self):
        rows, cols = g.rowcol_grid(g.MeshRange(2, 4, 5, 8))
        self.assertEqual(rows.tolist(), [2, 2, 2, 3, 3, 3])
        self.assertEqual(cols.tolist(), [5, 6, 7, 5, 6, 7])

    def test_bands_cover_range_exactly_once(self):
        _, r = g.range_from_file_zukaku("04HE2")
        bands = list(g.iter_row_bands(r, max_cells=100_000))
        self.assertEqual(len(bands), 8)  # 100 行/帯 -> 750 行で 8 帯
        self.assertEqual(sum(b.n_cells for b in bands), r.n_cells)
        self.assertEqual(bands[0].row_start, r.row_start)
        self.assertEqual(bands[-1].row_stop, r.row_stop)
        for prev, nxt in zip(bands, bands[1:]):
            self.assertEqual(prev.row_stop, nxt.row_start)
        for b in bands:
            self.assertLessEqual(b.n_cells, 100_000)
            self.assertEqual((b.col_start, b.col_stop), (r.col_start, r.col_stop))

    def test_band_falls_back_to_one_row_when_row_exceeds_limit(self):
        r = g.MeshRange(0, 3, 0, 1000)
        bands = list(g.iter_row_bands(r, max_cells=10))
        self.assertEqual([b.n_rows for b in bands], [1, 1, 1])

    def test_invalid_max_cells(self):
        with self.assertRaises(ValueError):
            list(g.iter_row_bands(g.MeshRange(0, 1, 0, 1), max_cells=0))


if __name__ == "__main__":
    unittest.main()
