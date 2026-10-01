"""被覆率の計算とメッシュの採用・所有規則（QGIS/GDAL 非依存。numpy のみ）。

「簡易手法」: 20m セルを n x n に細分した細かい格子（サンプル）にポリゴンをラスタ化し、
  - セル中心に当たるサンプル（n は奇数。中央のサンプルがセル中心）→ 中心点がポリゴン内か
  - セル内で 1 のサンプル数 → 被覆率（検出の分解能は 1/n^2。n=5 で 4%）
を求める。ラスタ化そのもの（GDAL）は QGIS 側が担当し、ここは配列だけを扱う。
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

import numpy as np

from .grid import MeshRange

MAX_SUBSAMPLE = 15  # 15^2 = 225 < 1000（所有の優先度キーが桁あふれしない上限）


def check_subsample(n: int) -> int:
    if not isinstance(n, (int, np.integer)) or n < 1 or n % 2 == 0 or n > MAX_SUBSAMPLE:
        raise ValueError("細分数は 1〜%d の奇数で指定してください: %r" % (MAX_SUBSAMPLE, n))
    return int(n)


def block_count(samples: np.ndarray, n: int) -> np.ndarray:
    """(rows*n, cols*n) の 0/1 配列を n x n ブロックごとに合計し、(rows, cols) の整数配列にする。"""
    n = check_subsample(n)
    h, w = samples.shape
    if h % n or w % n:
        raise ValueError("配列の形状 %r が細分数 %d の倍数ではありません" % (samples.shape, n))
    return samples.reshape(h // n, n, w // n, n).sum(axis=(1, 3), dtype=np.int32)


def center_flags(samples: np.ndarray, n: int) -> np.ndarray:
    """各セルの中央サンプルが 1 か（セル中心点がポリゴン内か）。n は奇数。"""
    n = check_subsample(n)
    half = n // 2
    return samples[half::n, half::n] != 0


def eligible(count: np.ndarray, n: int, threshold_pct: float) -> np.ndarray:
    """被覆率がしきい値以上のセル。しきい値 0 は「1 サンプルでも重なれば」。"""
    if not 0 <= threshold_pct <= 100:
        raise ValueError("しきい値は 0〜100 [%%] で指定してください: %r" % threshold_pct)
    pct = count * 100.0 / (n * n)
    return (count > 0) & (pct >= threshold_pct)


def priority_key(center: np.ndarray, count: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """所有の優先度。中心点を含む領域が必ず勝ち、同じなら被覆率が大きい方が勝つ。0 は採用不可。"""
    key = center.astype(np.int32) * 1000 + count.astype(np.int32)
    return np.where(ok, key, 0).astype(np.int32)


def owned_cells(own_index: int, own_key: np.ndarray, others: Iterable[Tuple[int, np.ndarray]]) -> np.ndarray:
    """他領域との競合を解決し、この領域が所有するセルの真偽配列を返す。

    優先度キーが大きい領域が勝つ。同点は領域の番号が小さい方が勝つ（決定的）。
    採用不可（キー 0）のセルは常に False。他領域に勝てなかったセルが他のどの領域にも
    採用されない状況（欠落）は起こらない: キー最大の領域は必ず自分が所有者になるため。
    """
    wins = own_key > 0
    for other_index, other_key in others:
        if other_index == own_index:
            continue
        if other_index < own_index:
            wins &= own_key > other_key
        else:
            wins &= own_key >= other_key
    return wins


def adopted_rowcol(mask: np.ndarray, band: MeshRange) -> Tuple[np.ndarray, np.ndarray]:
    """真偽配列 (band.n_rows, band.n_cols) の True の位置を全域の (row, col) にする。行優先。"""
    if mask.shape != (band.n_rows, band.n_cols):
        raise ValueError("mask の形状 %r が帯 %dx%d と一致しません" % (mask.shape, band.n_rows, band.n_cols))
    r, c = np.nonzero(mask)
    return r.astype(np.int64) + band.row_start, c.astype(np.int64) + band.col_start


def coverage_histogram(count: np.ndarray, n: int, edges: Tuple[int, ...] = (0, 10, 25, 50, 75, 90, 100)) -> List[int]:
    """境界セル（0 < 被覆率 < 100%）の被覆率分布。edges で区切った件数を返す。

    区間は (edges[i], edges[i+1]]。最後に「100%（完全被覆）」の件数を付ける。
    しきい値の既定値を決める材料としてログに出す。
    """
    pct = count * 100.0 / (n * n)
    result = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        result.append(int(np.count_nonzero((pct > lo) & (pct <= hi) & (count < n * n))))
    result.append(int(np.count_nonzero(count == n * n)))
    return result
