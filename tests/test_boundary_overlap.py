"""境界セルを複数領域へ収録する厳密面積判定の回帰試験。"""
import numpy as np

from work.forest_mesh20m.core.grid import MeshRange
from work.forest_mesh20m.core.zone_mesh import process_zone_exact


def run(areas, threshold=0):
    written = []
    mesh_range = MeshRange(10, 11, 10, 13)
    stats = process_zone_exact(
        mesh_range, 100, threshold, lambda band: np.array([areas], dtype=float),
        lambda rows, cols, cover: written.extend(zip(rows.tolist(), cols.tolist(), cover.tolist())),
    )
    return written, stats


def test_overlap_and_sliver():
    # 中央セルは両ポリゴンに面積を持って交差する。極細の交差も0%の既定では採用。
    left, left_stats = run([400, 399.999, 0])
    right, right_stats = run([0, 0.001, 400])
    assert [col for _, col, _ in left] == [10, 11]
    assert [col for _, col, _ in right] == [11, 12]
    assert left_stats.n_adopted == right_stats.n_adopted == 2
    assert left[1][2] == 100 and right[0][2] == 0


def test_threshold_and_zero_area():
    written, stats = run([400, 0.001, 0], threshold=1)
    assert len(written) == 1 and written[0][1] == 10
    assert stats.dropped_by_threshold_samples > 0


if __name__ == "__main__":
    test_overlap_and_sliver()
    test_threshold_and_zero_area()
    print("2 boundary overlap tests passed")
