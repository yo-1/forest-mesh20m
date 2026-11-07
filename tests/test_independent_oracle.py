"""実装から独立した期待値による検証（v0.4.2 追加検証）。QGIS/GDAL 不要。

- JF20mID・file_zukaku・セル範囲は、仕様書 3.1〜3.3 の式を本ファイルで再実装して照合する。
- 交差面積は有理数（Fraction）の Sutherland-Hodgman クリップで求め、process_zone_exact の
  採否（しきい値・極小交差・線/点接触・マスク・領域間重複）を照合する。
  面積の計算そのもの（GEOS）は QGIS 側の責務で、ここでは検証できない（QGIS 実機スクリプトで検証）。
"""
import itertools
import random
import unittest
from fractions import Fraction as F

import numpy as np

from forest_mesh20m.core import zukaku
from forest_mesh20m.core.grid import MeshRange
from forest_mesh20m.core.zone_mesh import process_zone_exact

TOP, LEFT, CELL = 300000, -160000, 20
ROWL, COLL = "ABCDEFGHIJKLMNOPQRST", "ABCDEFGH"


def jf_id(system, row, col):
    return "%02d%s%s%d%d%03d%03d" % (
        system, ROWL[row // 1500], COLL[col // 2000], (row % 1500) // 150, (col % 2000) // 200, row % 150, col % 200)


def file_zukaku(system, row, col):
    return "%02d%s%s%d" % (system, ROWL[row // 1500], COLL[col // 2000], 1 + ((col % 2000) // 1000) + 2 * ((row % 1500) // 750))


def cell_rect(row, col):
    x0, y1 = LEFT + col * CELL, TOP - row * CELL
    return (F(x0), F(y1 - CELL), F(x0 + CELL), F(y1))


def _clip(pts, inside, inter):
    out = []
    if not pts:
        return out
    prev = pts[-1]
    for cur in pts:
        if inside(cur):
            if not inside(prev):
                out.append(inter(prev, cur))
            out.append(cur)
        elif inside(prev):
            out.append(inter(prev, cur))
        prev = cur
    return out


def clip_area(ring, x0, y0, x1, y1):
    ix = lambda x: (lambda p, q: (F(x), p[1] + (q[1] - p[1]) * (x - p[0]) / (q[0] - p[0])))  # noqa: E731
    iy = lambda y: (lambda p, q: (p[0] + (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1]), F(y)))  # noqa: E731
    pts = _clip(ring, lambda p: p[0] >= x0, ix(x0))
    pts = _clip(pts, lambda p: p[0] <= x1, ix(x1))
    pts = _clip(pts, lambda p: p[1] >= y0, iy(y0))
    pts = _clip(pts, lambda p: p[1] <= y1, iy(y1))
    if len(pts) < 3:
        return F(0)
    s = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return abs(s) / 2


def rect(x0, y0, x1, y1):
    return [(F(x0), F(y0)), (F(x1), F(y0)), (F(x1), F(y1)), (F(x0), F(y1))]


def exact_area_fn(ring, mask_rect=None):
    """band -> 各セルの厳密な交差面積（独立実装）。mask_rect は (x0, y0, x1, y1)。"""
    def fn(band):
        out = np.zeros((band.n_rows, band.n_cols))
        for i in range(band.n_rows):
            for j in range(band.n_cols):
                c = cell_rect(band.row_start + i, band.col_start + j)
                if mask_rect is not None:
                    c = (max(c[0], mask_rect[0]), max(c[1], mask_rect[1]), min(c[2], mask_rect[2]), min(c[3], mask_rect[3]))
                    if c[0] >= c[2] or c[1] >= c[3]:
                        continue
                out[i, j] = float(clip_area(ring, *c))
        return out
    return fn


def run_exact(ring, mesh_range, threshold, mask_rect=None, cells_per_band=100):
    got = {}
    stats = process_zone_exact(
        mesh_range, cells_per_band, threshold, exact_area_fn(ring, mask_rect),
        lambda rows, cols, cover: got.update({(r, c): v for r, c, v in zip(rows.tolist(), cols.tolist(), cover.tolist())}),
    )
    return got, stats


def expected(ring, mesh_range, threshold, mask_rect=None):
    fn = exact_area_fn(ring, mask_rect)
    # fn は float を返すが、Fraction で再計算して境界の同値を厳密に判定する
    out = {}
    for row in range(mesh_range.row_start, mesh_range.row_stop):
        for col in range(mesh_range.col_start, mesh_range.col_stop):
            c = cell_rect(row, col)
            if mask_rect is not None:
                c = (max(c[0], mask_rect[0]), max(c[1], mask_rect[1]), min(c[2], mask_rect[2]), min(c[3], mask_rect[3]))
                if c[0] >= c[2] or c[1] >= c[3]:
                    continue
            a = clip_area(ring, *c)
            if a > 0 and a * 100 / 400 >= F(threshold):
                out[(row, col)] = a * 100 / 400
    return out


# 格子に合う原点: easting 1000 -> col 8050, northing 2000 -> row 14900
R0, C0 = 14900, 8050
RANGE = MeshRange(R0 - 8, R0 + 2, C0 - 1, C0 + 9)


class IdAndZukakuOracleTest(unittest.TestCase):
    def test_ids_match_independent_formula(self):
        rng = random.Random(20261106)
        rows = [0, 1, 149, 150, 1499, 1500, 29999, 14900, 12499, 12498]
        cols = [0, 1, 199, 200, 1999, 2000, 15999, 8050, 13000, 13001]
        pairs = list(itertools.product(rows, cols)) + [(rng.randrange(30000), rng.randrange(16000)) for _ in range(2000)]
        for system in (1, 8, 9, 19):
            r = np.array([p[0] for p in pairs])
            c = np.array([p[1] for p in pairs])
            ids = zukaku.format_jf20m_ids(system, r, c).tolist()
            zks = zukaku.format_file_zukaku_codes(system, r, c).tolist()
            for k, (row, col) in enumerate(pairs):
                self.assertEqual(ids[k], jf_id(system, row, col), (system, row, col))
                self.assertEqual(zks[k], file_zukaku(system, row, col), (system, row, col))

    def test_cell_bounds_match_independent_formula(self):
        rng = random.Random(7)
        pairs = [(rng.randrange(30000), rng.randrange(16000)) for _ in range(500)] + [(0, 0), (29999, 15999)]
        r = np.array([p[0] for p in pairs])
        c = np.array([p[1] for p in pairs])
        n_max, e_min, n_min, e_max = zukaku.rowcol_to_bounds(r, c)
        for k, (row, col) in enumerate(pairs):
            x0, y0, x1, y1 = cell_rect(row, col)
            self.assertEqual((e_min[k], n_min[k], e_max[k], n_max[k]), (x0, y0, x1, y1))

    def test_no_letter_is_skipped_in_row_letters(self):
        # 行英字は A〜T の 20 文字（I・O を含む）
        for k, letter in enumerate(ROWL):
            self.assertEqual(zukaku.format_jf20m_ids(8, np.array([k * 1500]), np.array([0])).tolist()[0][2], letter)


class ExactAdoptionOracleTest(unittest.TestCase):
    def assertMatches(self, ring, threshold, mask_rect=None, mesh_range=RANGE):
        got, stats = run_exact(ring, mesh_range, threshold, mask_rect)
        exp = expected(ring, mesh_range, threshold, mask_rect)
        self.assertEqual(set(got), set(exp), "threshold=%r" % threshold)
        for k, p in exp.items():
            # cover_pct は四捨五入（.5 は切り上げ）の整数。独立に Fraction で求める
            self.assertEqual(got[k], (p + F(1, 2)).__floor__(), (k, float(p)))
        return got, stats

    def test_threshold_boundaries_0_1_40_50_100(self):
        # 列 C0+0 は 100%、C0+5 は 50%、行 R0-5 は 40%、角は 20%、細い列は 1.25%
        ring = rect(1000, 2000, 1110, 2108)
        for threshold in (0, 1, 20, 40, 50, 50.0001, 100):
            self.assertMatches(ring, threshold)

    def test_exact_tie_at_threshold_is_adopted(self):
        # 面積 5 ㎡ = 1.25%。しきい値 1.25 はちょうど（採用）、1.2500001 は不採用
        ring = rect(1000, 2000, 1020, "2000.25")
        got, _ = run_exact(ring, RANGE, 1.25)
        self.assertEqual(set(got), {(R0 - 1, C0)})
        got, _ = run_exact(ring, RANGE, 1.2500001)
        self.assertEqual(got, {})

    def test_threshold_100_requires_full_cell(self):
        ring = rect(1000, 2000, 1110, 2108)
        got, _ = run_exact(ring, RANGE, 100)
        self.assertTrue(all(v == 100 for v in got.values()))
        self.assertEqual(len(got), 5 * 5)

    def test_minimal_sliver_is_adopted_with_cover_zero(self):
        ring = rect(1000, 2000, "1000.0078125", "2000.0078125")  # 6.1e-5 ㎡
        got, _ = run_exact(ring, RANGE, 0)
        self.assertEqual(got, {(R0 - 1, C0): 0})
        got, _ = run_exact(ring, RANGE, 0.0001)
        self.assertEqual(got, {})

    def test_line_and_point_contact_only_are_excluded(self):
        # 領域は格子線上の辺だけで隣のセルに接する（辺・角とも面積 0）
        ring = rect(1000, 2000, 1100, 2100)
        got, _ = run_exact(ring, MeshRange(R0 - 8, R0 + 2, C0 - 2, C0 + 8), 0)
        cols = {c for _, c in got}
        rows = {r for r, _ in got}
        self.assertEqual(cols, set(range(C0, C0 + 5)))
        self.assertEqual(rows, set(range(R0 - 5, R0)))
        self.assertEqual(len(got), 25)

    def test_triangle_and_concave_shape(self):
        tri = [(F(1000), F(2000)), (F(1130), F(2000)), (F(1000), F(2090))]
        lshape = [(F(1000), F(2000)), (F(1100), F(2000)), (F(1100), F(2050)), (F(1050), F(2050)), (F(1050), F(2100)), (F(1000), F(2100))]
        for ring in (tri, lshape):
            for threshold in (0, 10, 50):
                self.assertMatches(ring, threshold)

    def test_mask_intersection_and_outside(self):
        ring = rect(1000, 2000, 1200, 2100)
        for mask in ((F(1050), F("2020.5"), F(1150), F("2080.25")), (F(5000), F(5000), F(6000), F(6000))):
            for threshold in (0, 1, 50):
                self.assertMatches(ring, threshold, mask_rect=mask)

    def test_same_cell_in_two_zones_is_output_to_both(self):
        # 共有辺 x=1107.25 をまたぐセル（列 C0+5）は両領域に出力される。重複出力が既定
        left, _ = run_exact(rect(1000, 2000, "1107.25", 2090), RANGE, 0)
        right, _ = run_exact(rect("1107.25", 2000, 1200, 2090), RANGE, 0)
        shared = set(left) & set(right)
        self.assertEqual({c for _, c in shared}, {C0 + 5})
        self.assertEqual(len(shared), 5)
        self.assertEqual(set(left), set(expected(rect(1000, 2000, "1107.25", 2090), RANGE, 0)))
        self.assertEqual(set(right), set(expected(rect("1107.25", 2000, 1200, 2090), RANGE, 0)))

    def test_band_splitting_is_invariant(self):
        ring = [(F(1000), F(2000)), (F(1130), F(2000)), (F(1000), F(2090))]
        a, _ = run_exact(ring, RANGE, 0, cells_per_band=1)
        b, _ = run_exact(ring, RANGE, 0, cells_per_band=10_000)
        self.assertEqual(a, b)

    def test_cover_pct_half_rounds_up(self):
        # 方針（PR #18、2026-11-07）: 非負値の通常の四捨五入。ちょうど .5 は切り上げ（偶数丸めではない）
        for height, expected_pct in (("2000.5", 3), ("2000.1015625", 1), ("2000.75", 4), ("2000.25", 1)):
            # 面積 = 20 * (height-2000): 10㎡=2.5%→3、2.03125㎡=0.508%→1、15㎡=3.75%→4、5㎡=1.25%→1
            got, _ = run_exact(rect(1000, 2000, 1020, height), RANGE, 0)
            self.assertEqual(got[(R0 - 1, C0)], expected_pct, height)
        # 面積 2 ㎡ = 0.5% → 1、面積 6 ㎡ = 1.5% → 2、面積 30 ㎡ = 7.5% → 8、面積 398 ㎡ = 99.5% → 100
        for area, expected_pct in ((2, 1), (6, 2), (30, 8), (398, 100)):
            got, _ = run_exact(rect(1000, 2000, 1020, 2000 + F(area, 20)), RANGE, 0)
            self.assertEqual(got[(R0 - 1, C0)], expected_pct, area)

    def test_selection_uses_unrounded_coverage(self):
        # 丸め後の cover_pct がしきい値に届いても、丸め前が届かなければ採用しない
        # 面積 3.9 ㎡ 相当（0.975%）: 丸めると 1 だが、しきい値 1% には届かない
        area_fn = lambda band: np.full((band.n_rows, band.n_cols), 3.9)  # noqa: E731
        got = []
        process_zone_exact(MeshRange(0, 1, 0, 1), 10, 1, area_fn, lambda r, c, v: got.append(v.tolist()))
        self.assertEqual(got, [])
        got = []
        process_zone_exact(MeshRange(0, 1, 0, 1), 10, 0, area_fn, lambda r, c, v: got.append(v.tolist()))
        self.assertEqual(got, [[1]])  # しきい値 0 なら採用され、cover_pct は丸めて 1
        # 面積 199.6 ㎡（49.9%）: 丸めると 50 だが、しきい値 50% には届かない
        area_fn = lambda band: np.full((band.n_rows, band.n_cols), 199.6)  # noqa: E731
        got = []
        process_zone_exact(MeshRange(0, 1, 0, 1), 10, 50, area_fn, lambda r, c, v: got.append(v.tolist()))
        self.assertEqual(got, [])
        # 面積 0.4 ㎡（0.1%）: 丸めると 0 だが、面積が正なのでしきい値 0 で採用される（cover_pct は 0）
        area_fn = lambda band: np.full((band.n_rows, band.n_cols), 0.4)  # noqa: E731
        got = []
        process_zone_exact(MeshRange(0, 1, 0, 1), 10, 0, area_fn, lambda r, c, v: got.append(v.tolist()))
        self.assertEqual(got, [[0]])

    def test_round_half_up_helper(self):
        from forest_mesh20m.core.coverage import round_half_up_pct
        values = [0, 0.4999, 0.5, 1.5, 2.5, 12.5, 49.5, 99.5, 100]
        self.assertEqual(round_half_up_pct(np.array(values)).tolist(), [0, 0, 1, 2, 3, 13, 50, 100, 100])
        self.assertEqual(round_half_up_pct(np.array(values)).dtype, np.int64)
        with self.assertRaises(ValueError):
            round_half_up_pct(np.array([-0.1]))

    def test_cancel_during_last_band_is_reported(self):
        # 回帰: area_fn が打ち切られた（未計算セルが 0 のまま返る）状態で最終帯に達しても canceled になる
        state = {"cancel": False}
        written = []

        def area_fn(band):
            state["cancel"] = True
            return np.zeros((band.n_rows, band.n_cols))

        stats = process_zone_exact(RANGE, 10_000, 0, area_fn, lambda r, c, v: written.append(1), is_canceled=lambda: state["cancel"])
        self.assertTrue(stats.canceled)
        self.assertEqual(written, [])

    def test_invalid_area_arrays_are_rejected(self):
        for bad in (np.full((RANGE.n_rows, RANGE.n_cols), np.nan), np.full((RANGE.n_rows, RANGE.n_cols), -1.0), np.zeros((1, 1))):
            with self.assertRaises(ValueError):
                process_zone_exact(RANGE, 10_000, 0, lambda band, bad=bad: bad, lambda *a: None)

    def test_threshold_range_validation(self):
        for bad in (-0.1, 100.1):
            with self.assertRaises(ValueError):
                process_zone_exact(RANGE, 10, bad, lambda band: np.zeros((band.n_rows, band.n_cols)), lambda *a: None)


if __name__ == "__main__":
    unittest.main()
