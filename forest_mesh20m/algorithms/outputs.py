"""出力先（GeoPackage への複数レイヤ書き込み／一時レイヤ）の共通処理。QGIS 依存。

未検証: 同一 GeoPackage への複数テーブル追加は、実機の QGIS ではまだ動かしていない。
"""
from __future__ import annotations

from typing import List

import numpy as np
from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsRectangle,
    QgsVectorFileWriter,
)

from .. import qgis_compat as compat


def _enum_value(enum_name: str, value_name: str):
    """QGIS 3.x の列挙型は、スコープ付き（Enum.Value）と旧式（Class.Value）が混在するため両方試す。"""
    enum = getattr(QgsVectorFileWriter, enum_name, None)
    if enum is not None and hasattr(enum, value_name):
        return getattr(enum, value_name)
    return getattr(QgsVectorFileWriter, value_name)


def open_gpkg_layer(path, layer_name, fields, shape, crs, transform_context, first_layer):
    """GeoPackage にレイヤ（テーブル）を 1 つ作る writer を返す。

    first_layer=True: ファイルを新規作成（呼び出し側が既存ファイルなしを確認済みであること）。
    first_layer=False: 既存ファイルにレイヤを追加する（同名レイヤがあれば置き換える）。
    """
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = _enum_value(
        "ActionOnExistingFile", "CreateOrOverwriteFile" if first_layer else "CreateOrOverwriteLayer"
    )
    writer = QgsVectorFileWriter.create(path, fields, compat.wkb_type(shape), crs, transform_context, options)
    if writer.hasError() != _enum_value("WriterError", "NoError"):
        raise QgsProcessingException(
            "GeoPackage にレイヤ %r を作成できません: %s" % (layer_name, writer.errorMessage())
        )
    return writer


def close_writer(writer) -> None:
    """writer を閉じてファイルへ確定させる（参照を手放すことで閉じる）。"""
    writer.flushBuffer()
    del writer


def open_temporary_layer(context, fields, shape, crs):
    """一時（メモリ）レイヤの sink と、完了時の読み込みに使う ID を返す。"""
    sink, dest_id = QgsProcessingUtils.createFeatureSink("memory:", context, fields, compat.wkb_type(shape), crs)
    if sink is None:
        raise QgsProcessingException("一時レイヤを作成できません。")
    return sink, dest_id


def build_features(
    fields: QgsFields, system: int, shape: str, rows: np.ndarray, cols: np.ndarray, cover_pct: np.ndarray
) -> List[QgsFeature]:
    """採用セルの (row, col) から Feature を作る。属性は JF20mID・file_zukaku・cover_pct の順。"""
    from ..core import zukaku

    n_max, e_min, n_min, e_max = zukaku.rowcol_to_bounds(rows, cols)
    ids = zukaku.format_jf20m_ids(system, rows, cols).tolist()
    files = zukaku.format_file_zukaku_codes(system, rows, cols).tolist()
    covers = cover_pct.tolist()
    features = []
    if shape == "polygon":
        x0, y0, x1, y1 = e_min.tolist(), n_min.tolist(), e_max.tolist(), n_max.tolist()
        for i in range(len(ids)):
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x0[i], y0[i], x1[i], y1[i])))
            feature.setAttributes([ids[i], files[i], int(covers[i])])
            features.append(feature)
    else:
        n_c, e_c = zukaku.rowcol_to_center(rows, cols)
        xs, ys = e_c.tolist(), n_c.tolist()
        for i in range(len(ids)):
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(xs[i], ys[i])))
            feature.setAttributes([ids[i], files[i], int(covers[i])])
            features.append(feature)
    return features


def add_features(sink, features: List[QgsFeature]) -> bool:
    ok = sink.addFeatures(features, QgsFeatureSink.FastInsert)
    return bool(ok) if ok is not None else True
