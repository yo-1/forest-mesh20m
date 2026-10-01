"""ポリゴンを帯ごとに細かい格子へラスタ化する（GDAL/OGR。QGIS 同梱の osgeo を使う）。

未検証: 実機の QGIS（GDAL 3.13）ではまだ動かしていない。特に OGR のメモリドライバ名
（GDAL 3.11 以降は "MEM" に統合、"Memory" は別名）の解決を実機で確認すること。
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from qgis.core import QgsGeometry, QgsProcessingException

from ..core.grid import CELL_SIZE_M, MeshRange


def _memory_vector_datasource():
    from osgeo import ogr

    # GDAL のバージョンでドライバ名が異なるため、順に試す。
    for name in ("Memory", "MEM"):
        driver = ogr.GetDriverByName(name)
        if driver is None:
            continue
        datasource = driver.CreateDataSource("")
        if datasource is not None:
            return datasource
    raise QgsProcessingException("GDAL のメモリ用ベクタドライバ（Memory/MEM）を利用できません。")


def rasterize_band(geometries: Sequence[QgsGeometry], band: MeshRange, subsample: int) -> np.ndarray:
    """帯の範囲を (n_rows*n, n_cols*n) の uint8 にラスタ化し、ポリゴン内のサンプルを 1 にする。

    GDAL の既定（ALL_TOUCHED=FALSE）はピクセル中心がポリゴン内のときに焼き込むため、
    1 サンプル = ピクセル中心での内外判定になる。座標は平面直角座標系（x=東距、y=北距）。
    """
    try:
        from osgeo import gdal, ogr
    except ImportError as exc:  # pragma: no cover - 実機では QGIS に同梱
        raise QgsProcessingException("osgeo（GDAL の Python バインディング）を読み込めません: %s" % exc)

    height, width = band.n_rows * subsample, band.n_cols * subsample
    n_max, e_min, _, _ = band.bounds()
    pixel = CELL_SIZE_M / subsample

    target = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    if target is None:
        raise QgsProcessingException("ラスタ（%d x %d）をメモリ上に確保できません。" % (width, height))
    target.SetGeoTransform((e_min, pixel, 0.0, n_max, 0.0, -pixel))
    target.GetRasterBand(1).Fill(0)

    datasource = _memory_vector_datasource()
    layer = datasource.CreateLayer("g", None, ogr.wkbUnknown)
    definition = layer.GetLayerDefn()
    for geometry in geometries:
        if geometry is None or geometry.isNull() or geometry.isEmpty():
            continue
        ogr_geometry = ogr.CreateGeometryFromWkb(bytes(geometry.asWkb()))
        if ogr_geometry is None:
            continue
        feature = ogr.Feature(definition)
        feature.SetGeometry(ogr_geometry)
        layer.CreateFeature(feature)
    if layer.GetFeatureCount() > 0:
        result = gdal.RasterizeLayer(target, [1], layer, burn_values=[1], options=["ALL_TOUCHED=FALSE"])
        if result != 0:
            raise QgsProcessingException("GDAL のラスタ化に失敗しました（戻り値 %r）。" % result)
    array = target.GetRasterBand(1).ReadAsArray()
    return np.asarray(array, dtype=np.uint8)
