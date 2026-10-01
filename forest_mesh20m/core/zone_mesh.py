"""領域 1 つ分のメッシュ採用処理（QGIS/GDAL 非依存。ラスタ化は呼び出し側から関数で受け取る）。

QGIS 側は「帯ごとのサンプル配列を返す関数」だけを用意し、被覆率・しきい値・所有規則・
集計はここで行う。実機がなくても numpy の疑似ラスタで挙動を検証できるようにするための分離。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

import numpy as np

from . import coverage as cv
from .grid import MeshRange, iter_row_bands

# samples_fn(zone_index, band) -> (band.n_rows*n, band.n_cols*n) の 0/1 配列
SamplesFn = Callable[[int, MeshRange], np.ndarray]
# mask_fn(band) -> 同形状の 0/1 配列。マスクなしなら None を渡す。
MaskFn = Callable[[MeshRange], np.ndarray]
# neighbors_fn(band) -> その帯に重なる他領域の番号
NeighborsFn = Callable[[MeshRange], Iterable[int]]
# on_band(rows, cols, cover_pct) -> None。採用セルを書き出す。
OnBand = Callable[[np.ndarray, np.ndarray, np.ndarray], None]


@dataclass
class ZoneStats:
    n_adopted: int = 0
    total_samples: int = 0  # 領域（∩マスク）が覆うサンプル総数（しきい値・所有の前）
    adopted_samples: int = 0
    dropped_by_threshold_samples: int = 0
    lost_to_other_zone_samples: int = 0
    histogram: List[int] = field(default_factory=lambda: [0] * 7)
    canceled: bool = False

    def add_histogram(self, values: List[int]) -> None:
        self.histogram = [a + b for a, b in zip(self.histogram, values)]


def process_zone(
    zone_index: int,
    zone_range: MeshRange,
    subsample: int,
    threshold_pct: float,
    exclusive: bool,
    cells_per_band: int,
    samples_fn: SamplesFn,
    mask_fn: Optional[MaskFn],
    neighbors_fn: NeighborsFn,
    on_band: OnBand,
    is_canceled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[], None] = lambda: None,
) -> ZoneStats:
    """領域の範囲を北から帯ごとに処理し、採用セルを on_band に渡す。

    exclusive=True: 複数領域にまたがるセルを 1 領域だけに割り当てる（中心点 → 被覆率 → 番号）。
    exclusive=False: しきい値を満たした領域すべてに重複して入れる。
    """
    n = cv.check_subsample(subsample)
    stats = ZoneStats()
    for band in iter_row_bands(zone_range, cells_per_band):
        if is_canceled():
            stats.canceled = True
            return stats
        mask = mask_fn(band) if mask_fn is not None else None
        if mask is not None and not mask.any():
            on_progress()
            continue

        def masked(samples: np.ndarray) -> np.ndarray:
            return samples if mask is None else (samples & mask)

        own = masked(samples_fn(zone_index, band))
        count = cv.block_count(own, n)
        center = cv.center_flags(own, n)
        ok = cv.eligible(count, n, threshold_pct)
        key = cv.priority_key(center, count, ok)

        if exclusive:
            others = []
            for other in neighbors_fn(band):
                if other == zone_index:
                    continue
                o = masked(samples_fn(other, band))
                o_count = cv.block_count(o, n)
                o_ok = cv.eligible(o_count, n, threshold_pct)
                others.append((other, cv.priority_key(cv.center_flags(o, n), o_count, o_ok)))
            owned = cv.owned_cells(zone_index, key, others)
        else:
            owned = ok

        stats.total_samples += int(count.sum())
        stats.adopted_samples += int(count[owned].sum())
        stats.dropped_by_threshold_samples += int(count[(count > 0) & ~ok].sum())
        stats.lost_to_other_zone_samples += int(count[ok & ~owned].sum())
        stats.add_histogram(cv.coverage_histogram(count, n))

        rows, cols = cv.adopted_rowcol(owned, band)
        if rows.size:
            cover_pct = np.rint(count[owned] * 100.0 / (n * n)).astype(np.int64)
            on_band(rows, cols, cover_pct)
            stats.n_adopted += int(rows.size)
        on_progress()
    return stats


def total_bands(zone_ranges: Iterable[MeshRange], cells_per_band: int) -> int:
    return sum(len(list(iter_row_bands(r, cells_per_band))) for r in zone_ranges)


def cells_per_band_for(subsample: int, max_cells: int = 200_000, max_samples: int = 8_000_000) -> int:
    """1 帯のサンプル数（セル数 x n^2）が max_samples を超えないようにセル数を決める。"""
    return max(1, min(max_cells, max_samples // (subsample * subsample)))
