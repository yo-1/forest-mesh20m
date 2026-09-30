"""メッシュ範囲（MeshRange）の計算と、メモリを抑えたチャンク分割（QGIS 非依存）。

1 ファイル単位（50000 図郭の 1/4 = 15km x 20km）は 750 x 1000 = 75 万メッシュ。
全件を一度に配列化せず、行帯（row band）ごとに処理できるようにしている。
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterator, List, NamedTuple, Optional, Tuple

import numpy as np

from .zukaku import (
    CELL_SIZE_M,
    EASTING_LEFT_M,
    MESH_COLS_PER_50000,
    MESH_ROWS_PER_50000,
    NORTHING_TOP_M,
    TOTAL_COLS,
    TOTAL_ROWS,
    check_system,
    file_zukaku_code,
    format_file_zukaku_codes,
    format_jf20m_ids,
    parse_file_zukaku,
    rowcol_to_bounds,
    rowcol_to_center,
)


@dataclass(frozen=True)
class MeshRange:
    """全域メッシュ番号の矩形範囲。row/col とも半開区間 [start, stop)。"""

    row_start: int
    row_stop: int
    col_start: int
    col_stop: int

    def __post_init__(self) -> None:
        if not (0 <= self.row_start < self.row_stop <= TOTAL_ROWS):
            raise ValueError("row 範囲が不正です: [%r, %r)" % (self.row_start, self.row_stop))
        if not (0 <= self.col_start < self.col_stop <= TOTAL_COLS):
            raise ValueError("col 範囲が不正です: [%r, %r)" % (self.col_start, self.col_stop))

    @property
    def n_rows(self) -> int:
        return self.row_stop - self.row_start

    @property
    def n_cols(self) -> int:
        return self.col_stop - self.col_start

    @property
    def n_cells(self) -> int:
        return self.n_rows * self.n_cols

    def bounds(self) -> Tuple[float, float, float, float]:
        """(northing_max, easting_min, northing_min, easting_max) [m]。"""
        return (
            NORTHING_TOP_M - self.row_start * CELL_SIZE_M,
            EASTING_LEFT_M + self.col_start * CELL_SIZE_M,
            NORTHING_TOP_M - self.row_stop * CELL_SIZE_M,
            EASTING_LEFT_M + self.col_stop * CELL_SIZE_M,
        )

    def intersect(self, other: "MeshRange") -> Optional["MeshRange"]:
        """共通部分。重ならなければ None。"""
        rs, re_ = max(self.row_start, other.row_start), min(self.row_stop, other.row_stop)
        cs, ce = max(self.col_start, other.col_start), min(self.col_stop, other.col_stop)
        if rs >= re_ or cs >= ce:
            return None
        return MeshRange(rs, re_, cs, ce)


def range_from_file_zukaku(code: str) -> Tuple[int, MeshRange]:
    """ファイル単位図郭名（例 "04HE2"）から (系番号, MeshRange) を返す。"""
    z = parse_file_zukaku(code)
    half_rows = MESH_ROWS_PER_50000 // 2
    half_cols = MESH_COLS_PER_50000 // 2
    south = z.quadrant in (3, 4)
    east = z.quadrant in (2, 4)
    row_start = z.row_letter_index * MESH_ROWS_PER_50000 + (half_rows if south else 0)
    col_start = z.col_letter_index * MESH_COLS_PER_50000 + (half_cols if east else 0)
    return z.system, MeshRange(
        row_start, row_start + half_rows, col_start, col_start + half_cols
    )


def range_from_bounds(
    northing_min: float, easting_min: float, northing_max: float, easting_max: float
) -> MeshRange:
    """平面直角座標の矩形と交差するメッシュ全体を覆う MeshRange。

    矩形の北端・西端がちょうどメッシュ境界に一致する場合、その外側のメッシュは含めない
    （面積が交差しないため）。系の有効範囲を超える矩形は ValueError。
    """
    values = (northing_min, easting_min, northing_max, easting_max)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("範囲に非有限値が含まれています")
    if northing_min >= northing_max or easting_min >= easting_max:
        raise ValueError("範囲の最小値は最大値より小さくしてください")
    row_start = math.floor((NORTHING_TOP_M - northing_max) / CELL_SIZE_M)
    row_stop = math.ceil((NORTHING_TOP_M - northing_min) / CELL_SIZE_M)
    col_start = math.floor((easting_min - EASTING_LEFT_M) / CELL_SIZE_M)
    col_stop = math.ceil((easting_max - EASTING_LEFT_M) / CELL_SIZE_M)
    if row_start < 0 or row_stop > TOTAL_ROWS or col_start < 0 or col_stop > TOTAL_COLS:
        raise ValueError("範囲が系の有効範囲（南北±300km、東西±160km）を超えています")
    return MeshRange(row_start, row_stop, col_start, col_stop)


def rowcol_grid(mesh_range: MeshRange) -> Tuple[np.ndarray, np.ndarray]:
    """範囲内の全メッシュの (rows, cols) を 1 次元配列で返す。

    並びは行優先（北から南、各行で西から東）。
    """
    rows = np.arange(mesh_range.row_start, mesh_range.row_stop, dtype=np.int64)
    cols = np.arange(mesh_range.col_start, mesh_range.col_stop, dtype=np.int64)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    return rr.ravel(), cc.ravel()


class MeshArrays(NamedTuple):
    """1 帯分のメッシュ属性・座標。すべて同じ長さの 1 次元 ndarray（行優先）。"""

    ids: np.ndarray  # JF20mID (<U12)
    file_zukaku: np.ndarray  # ファイル単位図郭名 (<U5)
    easting_min: np.ndarray
    northing_min: np.ndarray
    easting_max: np.ndarray
    northing_max: np.ndarray
    easting_center: np.ndarray
    northing_center: np.ndarray


def mesh_band_arrays(system: int, mesh_range: MeshRange) -> MeshArrays:
    """範囲内の全メッシュについて ID・図郭名・外周・中心座標を一括計算する。

    QGIS 側はこの結果を Feature に詰めるだけにして、計算ロジックを持たない。
    """
    rows, cols = rowcol_grid(mesh_range)
    n_max, e_min, n_min, e_max = rowcol_to_bounds(rows, cols)
    n_c, e_c = rowcol_to_center(rows, cols)
    return MeshArrays(
        format_jf20m_ids(system, rows, cols),
        format_file_zukaku_codes(system, rows, cols),
        e_min, n_min, e_max, n_max, e_c, n_c,
    )


class GridLine(NamedTuple):
    """格子線 1 本。kind は 'row'（東西に延びる線）か 'col'（南北に延びる線）。

    boundary_no は全域メッシュ番号でのその線の番号（row 線は北から、col 線は西から。
    row 線 n は row=n のメッシュの北辺）。座標は平面直角座標 [m]。
    """

    kind: str
    boundary_no: int
    easting_start: float
    northing_start: float
    easting_end: float
    northing_end: float


def grid_lines(mesh_range: MeshRange) -> List[GridLine]:
    """範囲を囲む格子線を返す。線数は (n_rows + 1) + (n_cols + 1) で、メッシュ数に比例しない。"""
    n_top, e_left, n_bottom, e_right = mesh_range.bounds()
    lines: List[GridLine] = []
    for r in range(mesh_range.row_start, mesh_range.row_stop + 1):
        n = NORTHING_TOP_M - r * CELL_SIZE_M
        lines.append(GridLine("row", r, e_left, n, e_right, n))
    for c in range(mesh_range.col_start, mesh_range.col_stop + 1):
        e = EASTING_LEFT_M + c * CELL_SIZE_M
        lines.append(GridLine("col", c, e, n_top, e, n_bottom))
    return lines


def parse_file_zukaku_list(text: str, system: int) -> List[MeshRange]:
    """'04HE2, 04HE3' のような文字列を MeshRange のリストにする。

    区切りは半角/全角のカンマ・読点・空白・改行。重複は除く。
    系番号が指定の system と異なるコードが含まれていれば ValueError。

    入力欄向けの緩和として、全角英数字を半角にし（NFKC）、小文字は大文字にしてから解釈する
    （実機で '08le1' が拒否された）。ID/ファイル名を扱う parse_file_zukaku 自体は厳密なまま。
    """
    check_system(system)
    normalized = unicodedata.normalize("NFKC", text or "").upper()
    codes = [t for t in re.split(r"[,、，\s]+", normalized) if t]
    if not codes:
        raise ValueError("ファイル単位図郭名が指定されていません")
    ranges: List[MeshRange] = []
    seen = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        code_system, mesh_range = range_from_file_zukaku(code)
        if code_system != system:
            raise ValueError(
                "図郭名 %s の系番号(%d)が、指定された系番号(%d)と一致しません"
                % (code, code_system, system)
            )
        ranges.append(mesh_range)
    return ranges


def covering_file_zukaku(
    system: int, mesh_range: MeshRange, whole: bool = False
) -> List[Tuple[str, MeshRange]]:
    """範囲と交差するファイル単位図郭を、北から南・西から東の順に (図郭名, MeshRange) で返す。

    whole=False: 各図郭内の、範囲との共通部分（端の図郭は欠けた形になる）。
    whole=True : 交差する図郭の全体（15km x 20km）。
    """
    check_system(system)
    half_rows = MESH_ROWS_PER_50000 // 2
    half_cols = MESH_COLS_PER_50000 // 2
    result: List[Tuple[str, MeshRange]] = []
    for qi in range(mesh_range.row_start // half_rows, (mesh_range.row_stop - 1) // half_rows + 1):
        for qj in range(mesh_range.col_start // half_cols, (mesh_range.col_stop - 1) // half_cols + 1):
            quad = MeshRange(qi * half_rows, (qi + 1) * half_rows, qj * half_cols, (qj + 1) * half_cols)
            code = file_zukaku_code(system, quad.row_start, quad.col_start)
            part = quad if whole else mesh_range.intersect(quad)
            result.append((code, part))
    return result


def iter_row_bands(mesh_range: MeshRange, max_cells: int = 100_000) -> Iterator[MeshRange]:
    """範囲を北から順に、1 帯あたり max_cells 以下のメッシュ数になるよう行方向に分割する。

    1 行が max_cells を超える場合でも、最低 1 行ずつ返す。
    """
    if max_cells < 1:
        raise ValueError("max_cells は 1 以上で指定してください")
    rows_per_band = max(1, max_cells // mesh_range.n_cols)
    row = mesh_range.row_start
    while row < mesh_range.row_stop:
        stop = min(row + rows_per_band, mesh_range.row_stop)
        yield MeshRange(row, stop, mesh_range.col_start, mesh_range.col_stop)
        row = stop
