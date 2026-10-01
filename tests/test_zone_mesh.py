import unittest

import numpy as np

from forest_mesh20m.core import zone_mesh as zm
from forest_mesh20m.core.grid import MeshRange

N = 5  # 細分数。1 セル = 5x5 サンプル（1 サンプル = 4m）


def rect_samples(rect, band, n=N):
    """rect = (row0, col0, row1, col1) をセル単位の小数で指定（北西原点）。サンプル中心で判定する。"""
    r0, c0, r1, c1 = rect
    rows = band.row_start + (np.arange(band.n_rows * n) + 0.5) / n
    cols = band.col_start + (np.arange(band.n_cols * n) + 0.5) / n
    rm = (rows >= r0) & (rows < r1)
    cm = (cols >= c0) & (cols < c1)
    return np.outer(rm, cm).astype(np.uint8)


class Harness:
    def __init__(self, rects, mask_rect=None, threshold=0.0, exclusive=True, cells_per_band=10):
        self.rects, self.mask_rect = rects, mask_rect
        self.threshold, self.exclusive, self.cells_per_band = threshold, exclusive, cells_per_band
        self.ranges = [self.range_of(r) for r in rects]
        self.out = [dict() for _ in rects]
        self.stats = []

    @staticmethod
    def range_of(rect):
        r0, c0, r1, c1 = rect
        return MeshRange(int(np.floor(r0)), int(np.ceil(r1)), int(np.floor(c0)), int(np.ceil(c1)))

    def run(self):
        for i, zr in enumerate(self.ranges):
            def on_band(rows, cols, cover, i=i):
                for r, c, v in zip(rows.tolist(), cols.tolist(), cover.tolist()):
                    assert (r, c) not in self.out[i], "同じ領域に同じセルが 2 回出力された"
                    self.out[i][(r, c)] = v

            st = zm.process_zone(
                i, zr, N, self.threshold, self.exclusive, self.cells_per_band,
                samples_fn=lambda k, band: rect_samples(self.rects[k], band),
                mask_fn=(None if self.mask_rect is None else lambda band: rect_samples(self.mask_rect, band)),
                neighbors_fn=lambda band: [k for k, z in enumerate(self.ranges) if z.intersect(band)],
                on_band=on_band,
            )
            self.stats.append(st)
        return self


class ZoneMeshTest(unittest.TestCase):
    def test_single_zone_whole_cells(self):
        h = Harness([(10, 20, 12, 23)]).run()
        self.assertEqual(len(h.out[0]), 6)
        self.assertTrue(all(v == 100 for v in h.out[0].values()))

    def test_boundary_cell_through_middle_goes_to_exactly_one_zone(self):
        # 領域 A は col 0〜10.4、領域 B は col 10.4〜20。cell col 10 は A が 40%、B が 60%。
        # セル中心 (10.5) は B の中にあるので B が所有する。
        h = Harness([(0, 0, 3, 10.4), (0, 10.4, 3, 20)]).run()
        a_cols = {c for (_, c) in h.out[0]}
        b_cols = {c for (_, c) in h.out[1]}
        self.assertEqual(a_cols, set(range(0, 10)))
        self.assertIn(10, b_cols)
        self.assertFalse(a_cols & b_cols)
        self.assertEqual(h.out[1][(0, 10)], 60)

    def test_no_gap_no_duplicate_over_union(self):
        h = Harness([(0, 0, 4, 7.3), (0, 7.3, 4, 15.6), (4, 0, 9, 15.6)], cells_per_band=8).run()
        owners = {}
        for i, cells in enumerate(h.out):
            for k in cells:
                self.assertNotIn(k, owners, "セル %r が複数の領域に出力された" % (k,))
                owners[k] = i
        expected = {(r, c) for r in range(0, 9) for c in range(0, 16)}
        self.assertEqual(set(owners), expected)

    def test_duplicate_mode_puts_cell_in_both(self):
        h = Harness([(0, 0, 3, 10.4), (0, 10.4, 3, 20)], exclusive=False).run()
        self.assertIn((0, 10), h.out[0])
        self.assertIn((0, 10), h.out[1])

    def test_threshold_drops_small_overlap_and_counts_it(self):
        # 40% のセル（col 10）はしきい値 50% で落ちる。
        h = Harness([(0, 0, 3, 10.4)], threshold=50).run()
        self.assertEqual({c for (_, c) in h.out[0]}, set(range(0, 10)))
        self.assertGreater(h.stats[0].dropped_by_threshold_samples, 0)
        self.assertEqual(h.stats[0].n_adopted, 30)

    def test_threshold_zero_adopts_any_overlap(self):
        h = Harness([(0, 0, 3, 10.2)], threshold=0).run()  # 1 サンプル列だけ重なる（20%）
        self.assertIn((0, 10), h.out[0])

    def test_mask_limits_cells(self):
        h = Harness([(0, 0, 5, 10)], mask_rect=(2, 3, 4, 6)).run()
        expected = {(r, c) for r in (2, 3) for c in (3, 4, 5)}
        self.assertEqual(set(h.out[0]), expected)

    def test_mask_outside_zone_gives_empty(self):
        h = Harness([(0, 0, 5, 10)], mask_rect=(50, 50, 60, 60)).run()
        self.assertEqual(h.out[0], {})

    def test_samples_accounting_adds_up(self):
        h = Harness([(0, 0, 3, 10.4), (0, 10.4, 3, 20)], threshold=45).run()
        for st in h.stats:
            self.assertEqual(
                st.total_samples,
                st.adopted_samples + st.dropped_by_threshold_samples + st.lost_to_other_zone_samples,
            )

    def test_band_splitting_does_not_change_result(self):
        rects = [(0, 0, 9, 7.3), (0, 7.3, 9, 15.6)]
        a = Harness(rects, cells_per_band=10).run().out
        b = Harness(rects, cells_per_band=10_000).run().out
        self.assertEqual(a, b)

    def test_cancel(self):
        st = zm.process_zone(
            0, MeshRange(0, 5, 0, 5), N, 0, True, 5,
            lambda k, b: rect_samples((0, 0, 5, 5), b), None, lambda b: [], lambda *a: None,
            is_canceled=lambda: True,
        )
        self.assertTrue(st.canceled)

    def test_cells_per_band_bounds_samples(self):
        for n in (1, 3, 5, 15):
            c = zm.cells_per_band_for(n)
            self.assertLessEqual(c * n * n, 8_000_000)
            self.assertGreaterEqual(c, 1)


if __name__ == "__main__":
    unittest.main()
