"""国土基本図図郭ベースの 20m メッシュ計算（QGIS 非依存）。

根拠: 森林情報に関するオープンデータ標準仕様書 Ver.2.1 【航空レーザ森林資源解析データ編】
      参考3）20m メッシュ ID の付与規則

座標の約束（測量座標系。GIS の x/y と逆なので注意）:
    northing_m … 平面直角座標系の X 軸。北が正。QGIS の QgsPointXY.y() に相当。
    easting_m  … 平面直角座標系の Y 軸。東が正。QGIS の QgsPointXY.x() に相当。

メッシュ番号 (row, col):
    系の原点を中心とする範囲の北西端を (0, 0) とする通し番号。
    row は北から南へ、col は西から東へ増える。
    row: 0..29999（20m x 30000 = 南北 600km）
    col: 0..15999（20m x 16000 = 東西 320km）

階層（仕様書 図1〜図5）:
    地図情報レベル 50000 : 30km(南北) x 40km(東西)、A..T(縦20) x A..H(横8)
    地図情報レベル 5000  : 50000 の 10x10 分割 = 3km x 4km、下 2 桁 (行, 列)
    20m メッシュ         : 5000 の 150x200 分割、下 6 桁 (行 3 桁, 列 3 桁)

境界の扱い（仕様書に記載なし。本実装の仮定）:
    メッシュ境界線上の点は、その境界を北辺・西辺とするメッシュ（南側・東側）に含める。
    数式は floor((300000 - northing) / 20)、floor((easting + 160000) / 20) である。
    したがって有効範囲は northing in (-300000, 300000]、easting in [-160000, 160000)。

Python 3.9 以上で動作する（QGIS 3.34 LTR の Windows 版が Python 3.9 のため）。
"""
from __future__ import annotations

import re
from typing import NamedTuple, Tuple

import numpy as np

# --- 仕様から決まる定数 -----------------------------------------------------
CELL_SIZE_M = 20

# 地図情報レベル 50000 の区画（縦 20 x 横 8）
LEVEL50000_ROWS = 20
LEVEL50000_COLS = 8
# 50000 区画 1 枚あたりの 20m メッシュ数（30km/20m, 40km/20m）
MESH_ROWS_PER_50000 = 1500
MESH_COLS_PER_50000 = 2000
# 5000 区画 1 枚あたりの 20m メッシュ数（3km/20m, 4km/20m）
MESH_ROWS_PER_5000 = 150
MESH_COLS_PER_5000 = 200

TOTAL_ROWS = LEVEL50000_ROWS * MESH_ROWS_PER_50000  # 30000
TOTAL_COLS = LEVEL50000_COLS * MESH_COLS_PER_50000  # 16000

# 系原点を中心に南北 ±300km、東西 ±160km（仕様書 図1）
NORTHING_TOP_M = 300_000.0
EASTING_LEFT_M = -160_000.0

MIN_SYSTEM = 1
MAX_SYSTEM = 19

_ROW_LETTERS = "ABCDEFGHIJKLMNOPQRST"  # 20 文字（I, O も含む）
_COL_LETTERS = "ABCDEFGH"  # 8 文字

_ID_PATTERN = re.compile(r"^(\d{2})([A-T])([A-H])(\d)(\d)(\d{3})(\d{3})$")
_FILE_ZUKAKU_PATTERN = re.compile(r"^(\d{2})([A-T])([A-H])([1-4])$")


class MeshAddress(NamedTuple):
    """系番号と全域メッシュ番号の組。"""

    system: int
    row: int
    col: int


# --- 検証 -------------------------------------------------------------------
def check_system(system: int) -> int:
    if not isinstance(system, (int, np.integer)) or isinstance(system, bool):
        raise ValueError("系番号は整数で指定してください: %r" % (system,))
    if not MIN_SYSTEM <= system <= MAX_SYSTEM:
        raise ValueError(
            "系番号は %d..%d の範囲で指定してください: %r" % (MIN_SYSTEM, MAX_SYSTEM, system)
        )
    return int(system)


def _check_rowcol(rows: np.ndarray, cols: np.ndarray) -> None:
    if rows.size and (rows.min() < 0 or rows.max() >= TOTAL_ROWS):
        raise ValueError("row は 0..%d の範囲外です" % (TOTAL_ROWS - 1))
    if cols.size and (cols.min() < 0 or cols.max() >= TOTAL_COLS):
        raise ValueError("col は 0..%d の範囲外です" % (TOTAL_COLS - 1))


# --- 座標 <-> メッシュ番号 ----------------------------------------------------
def in_extent_mask(northing_m, easting_m) -> np.ndarray:
    """系の有効範囲内なら True。NaN/inf は False。大量の点を事前に絞り込む用途。"""
    n = np.asarray(northing_m, dtype=np.float64)
    e = np.asarray(easting_m, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        return (
            np.isfinite(n)
            & np.isfinite(e)
            & (n <= NORTHING_TOP_M)
            & (n > -NORTHING_TOP_M)
            & (e >= EASTING_LEFT_M)
            & (e < -EASTING_LEFT_M)
        )


def xy_to_rowcol(northing_m, easting_m) -> Tuple[np.ndarray, np.ndarray]:
    """平面直角座標 (m) を全域メッシュ番号 (row, col) に変換する。

    スカラーでも配列でも受け付け、int64 の ndarray を返す。
    範囲外・非有限値が 1 点でもあれば ValueError（黙って丸めない）。
    事前に in_extent_mask で絞り込むこと。
    """
    n = np.asarray(northing_m, dtype=np.float64)
    e = np.asarray(easting_m, dtype=np.float64)
    if not np.all(in_extent_mask(n, e)):
        raise ValueError(
            "座標が系の有効範囲外、または非有限値です"
            "（northing は -300000 超 300000 以下、easting は -160000 以上 160000 未満）"
        )
    rows = np.floor((NORTHING_TOP_M - n) / CELL_SIZE_M).astype(np.int64)
    cols = np.floor((e - EASTING_LEFT_M) / CELL_SIZE_M).astype(np.int64)
    return rows, cols


def rowcol_to_bounds(row, col):
    """メッシュの外周 (northing_max, easting_min, northing_min, easting_max) を返す。"""
    r = np.asarray(row, dtype=np.int64)
    c = np.asarray(col, dtype=np.int64)
    _check_rowcol(r, c)
    n_max = NORTHING_TOP_M - r * CELL_SIZE_M
    e_min = EASTING_LEFT_M + c * CELL_SIZE_M
    return n_max, e_min, n_max - CELL_SIZE_M, e_min + CELL_SIZE_M


def rowcol_to_center(row, col):
    """メッシュ中心の (northing_m, easting_m)。行政コード付与などに使う。"""
    n_max, e_min, _, _ = rowcol_to_bounds(row, col)
    half = CELL_SIZE_M / 2.0
    return n_max - half, e_min + half


# --- JF20mID -----------------------------------------------------------------
def format_jf20m_id(system: int, row: int, col: int) -> str:
    """12 桁の 20m メッシュ ID: 系(2) + 50000 区画(英字2) + 5000 区画(数字2) + 20m(数字6)。

    例: 系 9, 東京付近 -> "09LD35149199"
    """
    system = check_system(system)
    row = int(row)
    col = int(col)
    if not 0 <= row < TOTAL_ROWS or not 0 <= col < TOTAL_COLS:
        raise ValueError("row/col が範囲外です: %r, %r" % (row, col))
    return "%02d%s%s%d%d%03d%03d" % (
        system,
        _ROW_LETTERS[row // MESH_ROWS_PER_50000],
        _COL_LETTERS[col // MESH_COLS_PER_50000],
        (row % MESH_ROWS_PER_50000) // MESH_ROWS_PER_5000,
        (col % MESH_COLS_PER_50000) // MESH_COLS_PER_5000,
        row % MESH_ROWS_PER_5000,
        col % MESH_COLS_PER_5000,
    )


def format_jf20m_ids(system: int, rows, cols) -> np.ndarray:
    """format_jf20m_id のベクトル版。dtype '<U12' の ndarray を返す。"""
    system = check_system(system)
    r = np.asarray(rows, dtype=np.int64)
    c = np.asarray(cols, dtype=np.int64)
    _check_rowcol(r, c)
    row_letters = np.array(list(_ROW_LETTERS))
    col_letters = np.array(list(_COL_LETTERS))

    def digits(a: np.ndarray, width: int) -> np.ndarray:
        return np.char.zfill(a.astype(str), width)

    out = np.char.add("%02d" % system, row_letters[r // MESH_ROWS_PER_50000])
    out = np.char.add(out, col_letters[c // MESH_COLS_PER_50000])
    out = np.char.add(out, digits((r % MESH_ROWS_PER_50000) // MESH_ROWS_PER_5000, 1))
    out = np.char.add(out, digits((c % MESH_COLS_PER_50000) // MESH_COLS_PER_5000, 1))
    out = np.char.add(out, digits(r % MESH_ROWS_PER_5000, 3))
    out = np.char.add(out, digits(c % MESH_COLS_PER_5000, 3))
    return out


def parse_jf20m_id(mesh_id: str) -> MeshAddress:
    """format_jf20m_id の逆変換。形式・範囲が不正なら ValueError。"""
    m = _ID_PATTERN.match(mesh_id or "")
    if not m:
        raise ValueError("20m メッシュ ID の形式が不正です: %r" % (mesh_id,))
    system = check_system(int(m.group(1)))
    r50 = _ROW_LETTERS.index(m.group(2))
    c50 = _COL_LETTERS.index(m.group(3))
    r5, c5 = int(m.group(4)), int(m.group(5))
    r20, c20 = int(m.group(6)), int(m.group(7))
    if r20 >= MESH_ROWS_PER_5000 or c20 >= MESH_COLS_PER_5000:
        raise ValueError(
            "20m 区画番号が範囲外です（行 000-149、列 000-199）: %r" % (mesh_id,)
        )
    row = r50 * MESH_ROWS_PER_50000 + r5 * MESH_ROWS_PER_5000 + r20
    col = c50 * MESH_COLS_PER_50000 + c5 * MESH_COLS_PER_5000 + c20
    return MeshAddress(system, row, col)


# --- ファイル単位の図郭（50000 図郭の 4 分割） ---------------------------------
def file_zukaku_code(system: int, row: int, col: int) -> str:
    """ダウンロードデータのファイル単位図郭名。例: "04HE2"。

    50000 図郭（30km x 40km）を 2x2 に分割した番号。
    1=北西, 2=北東, 3=南西, 4=南東（仕様書 図2.3 のインデックスマップの並びによる）。
    """
    system = check_system(system)
    row = int(row)
    col = int(col)
    if not 0 <= row < TOTAL_ROWS or not 0 <= col < TOTAL_COLS:
        raise ValueError("row/col が範囲外です: %r, %r" % (row, col))
    south = (row % MESH_ROWS_PER_50000) >= MESH_ROWS_PER_50000 // 2
    east = (col % MESH_COLS_PER_50000) >= MESH_COLS_PER_50000 // 2
    quadrant = 1 + (2 if south else 0) + (1 if east else 0)
    return "%02d%s%s%d" % (
        system,
        _ROW_LETTERS[row // MESH_ROWS_PER_50000],
        _COL_LETTERS[col // MESH_COLS_PER_50000],
        quadrant,
    )


def format_file_zukaku_codes(system: int, rows, cols) -> np.ndarray:
    """file_zukaku_code のベクトル版。dtype '<U5' の ndarray を返す。"""
    system = check_system(system)
    r = np.asarray(rows, dtype=np.int64)
    c = np.asarray(cols, dtype=np.int64)
    _check_rowcol(r, c)
    south = (r % MESH_ROWS_PER_50000) >= MESH_ROWS_PER_50000 // 2
    east = (c % MESH_COLS_PER_50000) >= MESH_COLS_PER_50000 // 2
    quadrant = 1 + 2 * south.astype(np.int64) + east.astype(np.int64)
    out = np.char.add("%02d" % system, np.array(list(_ROW_LETTERS))[r // MESH_ROWS_PER_50000])
    out = np.char.add(out, np.array(list(_COL_LETTERS))[c // MESH_COLS_PER_50000])
    return np.char.add(out, quadrant.astype(str))


class FileZukaku(NamedTuple):
    system: int
    row_letter_index: int  # 0..19 (A..T)
    col_letter_index: int  # 0..7 (A..H)
    quadrant: int  # 1..4


def parse_file_zukaku(code: str) -> FileZukaku:
    m = _FILE_ZUKAKU_PATTERN.match(code or "")
    if not m:
        raise ValueError("ファイル単位図郭名の形式が不正です（例: 04HE2）: %r" % (code,))
    return FileZukaku(
        check_system(int(m.group(1))),
        _ROW_LETTERS.index(m.group(2)),
        _COL_LETTERS.index(m.group(3)),
        int(m.group(4)),
    )


def gpkg_file_name(zukaku_code: str, year: int) -> str:
    """ダウンロード用ファイル名 fr_mesh20m_{図郭}_{整備年西暦4桁}.gpkg（仕様書 表2.1）。"""
    parse_file_zukaku(zukaku_code)
    if not 1000 <= int(year) <= 9999:
        raise ValueError("整備年は西暦 4 桁で指定してください: %r" % (year,))
    return "fr_mesh20m_%s_%04d.gpkg" % (zukaku_code, int(year))


def gpkg_layer_name(zukaku_code: str, year: int) -> str:
    """GeoPackage 内のレイヤ名。ファイル名から拡張子を除いたもの（仕様書に規定なし・本実装の仮定）。"""
    return gpkg_file_name(zukaku_code, year)[: -len(".gpkg")]
