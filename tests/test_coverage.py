import unittest

import numpy as np

from forest_mesh20m.core import coverage as cv
from forest_mesh20m.core.grid import MeshRange


def samples_from_cells(cells, n):
    """cells: (rows, cols) の 0/1 配列を n 倍に拡大する。"""
    return np.kron(np.asarray(cells, dtype=np.uint8), np.ones((n, n), dtype=np.uint8))


class BlockCountTest(unittest.TestCase):
    def test_full_and_empty_blocks(self):
        s = samples_from_cells([[1, 0]], 5)
        self.assertEqual(cv.block_count(s, 5).tolist(), [[25, 0]])

    def test_partial_block(self):
        s = np.zeros((5, 5), dtype=np.uint8)
        s[:, :2] = 1  # 左 2 列 = 40%
        self.assertEqual(cv.block_count(s, 5).tolist(), [[10]])

    def test_shape_mismatch_raises(self):
        with self.assertRaises(ValueError):
            cv.block_count(np.zeros((6, 5), dtype=np.uint8), 5)

    def test_even_subsample_rejected(self):
        for bad in (0, 2, 4, 17, -1):
            with self.assertRaises(ValueError):
                cv.check_subsample(bad)


class CenterFlagTest(unittest.TestCase):
    def test_center_sample_only(self):
        s = np.zeros((10, 5), dtype=np.uint8)
        s[2, 2] = 1  # 1 つ目のセルの中央
        self.assertEqual(cv.center_flags(s, 5).tolist(), [[True], [False]])

    def test_corner_sample_is_not_center(self):
        s = np.zeros((5, 5), dtype=np.uint8)
        s[0, 0] = 1
        self.assertFalse(cv.center_flags(s, 5)[0, 0])


class EligibleTest(unittest.TestCase):
    def test_threshold_zero_means_any_overlap(self):
        count = np.array([0, 1, 25])
        self.assertEqual(cv.eligible(count, 5, 0).tolist(), [False, True, True])

    def test_threshold_boundary_is_inclusive(self):
        count = np.array([9, 10, 11])  # 36%, 40%, 44%
        self.assertEqual(cv.eligible(count, 5, 40).tolist(), [False, True, True])

    def test_threshold_100_needs_full(self):
        count = np.array([24, 25])
        self.assertEqual(cv.eligible(count, 5, 100).tolist(), [False, True])

    def test_invalid_threshold(self):
        for bad in (-1, 100.1):
            with self.assertRaises(ValueError):
                cv.eligible(np.array([1]), 5, bad)


class OwnershipTest(unittest.TestCase):
    def key(self, center, count, ok=True):
        return cv.priority_key(np.array([center]), np.array([count]), np.array([ok]))

    def test_center_beats_larger_coverage(self):
        a = self.key(True, 3)
        b = self.key(False, 20)
        self.assertTrue(cv.owned_cells(0, a, [(1, b)])[0])
        self.assertFalse(cv.owned_cells(1, b, [(0, a)])[0])

    def test_larger_coverage_wins_without_center(self):
        a = self.key(False, 10)
        b = self.key(False, 12)
        self.assertFalse(cv.owned_cells(0, a, [(1, b)])[0])
        self.assertTrue(cv.owned_cells(1, b, [(0, a)])[0])

    def test_tie_goes_to_lower_index_exactly_once(self):
        a = self.key(False, 10)
        b = self.key(False, 10)
        owners = [cv.owned_cells(0, a, [(1, b)])[0], cv.owned_cells(1, b, [(0, a)])[0]]
        self.assertEqual(owners, [True, False])

    def test_ineligible_is_never_owned(self):
        a = self.key(True, 3, ok=False)
        self.assertFalse(cv.owned_cells(0, a, [])[0])

    def test_no_cell_is_lost_or_duplicated(self):
        """3 領域がランダムな採用状況でも、採用可能なセルは必ずちょうど 1 領域が所有する。"""
        rng = np.random.default_rng(1)
        shape = (50, 60)
        keys = []
        for _ in range(3):
            center = rng.random(shape) < 0.3
            count = rng.integers(0, 26, shape)
            ok = (count > 0) & (rng.random(shape) < 0.8)
            keys.append(cv.priority_key(center, count, ok))
        owned = [
            cv.owned_cells(i, keys[i], [(j, keys[j]) for j in range(3)]) for i in range(3)
        ]
        total = sum(o.astype(int) for o in owned)
        anyone = np.any([k > 0 for k in keys], axis=0)
        self.assertTrue(np.array_equal(total, anyone.astype(int)))


class AdoptedRowColTest(unittest.TestCase):
    def test_offsets_applied(self):
        band = MeshRange(10, 12, 100, 103)
        mask = np.zeros((2, 3), dtype=bool)
        mask[0, 1] = mask[1, 2] = True
        r, c = cv.adopted_rowcol(mask, band)
        self.assertEqual((r.tolist(), c.tolist()), ([10, 11], [101, 102]))

    def test_shape_mismatch(self):
        with self.assertRaises(ValueError):
            cv.adopted_rowcol(np.zeros((1, 1), dtype=bool), MeshRange(0, 2, 0, 2))


class HistogramTest(unittest.TestCase):
    def test_bins_and_full(self):
        count = np.array([0, 1, 3, 13, 25, 25])  # 0,4,12,52,100,100 %
        hist = cv.coverage_histogram(count, 5)
        self.assertEqual(hist, [1, 1, 0, 1, 0, 0, 2])  # (0,10],(10,25],(25,50],(50,75],(75,90],(90,100)=0, 完全=2
