"""複数のアルゴリズムで共有する処理（QGIS 依存）。計算ロジックは core 側に置く。"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingException,
    QgsRectangle,
)

from .. import qgis_compat as compat
from ..core import crs as crs_core
from ..core import grid
from ..core.grid import MeshRange

# 1 回に処理するメッシュ数。メモリ使用量と進捗表示の細かさのバランス。
BAND_CELLS = 50_000

SHAPES = ("polygon", "point", "line")
SHAPE_LABELS = [
    "ポリゴン（メッシュ 1 枚 = 1 フィーチャ）",
    "ポイント（メッシュ中心点）",
    "ライン（格子線。メッシュ ID は持たない）",
]

# 属性名は暫定（正式名は今後決定）。変更する場合はここだけを直す。
FIELD_MESH_ID = "JF20mID"
FIELD_FILE_ZUKAKU = "file_zukaku"
FIELD_LINE_KIND = "kind"
FIELD_LINE_NO = "boundary_no"


def make_crs(system: int, datum: str, feedback) -> QgsCoordinateReferenceSystem:
    authid = crs_core.epsg_authid(system, datum)
    crs = QgsCoordinateReferenceSystem(authid)
    if not crs.isValid():
        raise QgsProcessingException(
            "座標系 %s を QGIS が認識できません（この QGIS の PROJ データを確認してください）" % authid
        )
    feedback.pushInfo("座標系: %s (%s)" % (authid, crs.description()))
    return crs


def build_fields(shape: str) -> QgsFields:
    fields = QgsFields()
    if shape == "line":
        fields.append(compat.make_field(FIELD_LINE_KIND, "string", 3))  # row / col
        fields.append(compat.make_field(FIELD_LINE_NO, "int"))
    else:
        fields.append(compat.make_field(FIELD_MESH_ID, "string", 12))
        fields.append(compat.make_field(FIELD_FILE_ZUKAKU, "string", 5))
    return fields


def read_area(
    algorithm, parameters, context, crs, extent_name: str, zukaku_name: str, system: int
) -> Tuple[Optional[MeshRange], Optional[List[MeshRange]]]:
    """「作成範囲」と「ファイル単位図郭名」のどちらか一方から対象を求める。

    戻り値: (範囲から求めた MeshRange, None) または (None, 図郭名から求めた MeshRange のリスト)。
    """
    zukaku_text = (algorithm.parameterAsString(parameters, zukaku_name, context) or "").strip()
    extent = algorithm.parameterAsExtent(parameters, extent_name, context, crs)
    has_extent = extent is not None and not extent.isNull()

    if has_extent and zukaku_text:
        raise QgsProcessingException(
            "「作成範囲」と「ファイル単位図郭名」は、どちらか一方だけを指定してください。"
        )
    if not has_extent and not zukaku_text:
        raise QgsProcessingException("「作成範囲」か「ファイル単位図郭名」のどちらかを指定してください。")
    try:
        if zukaku_text:
            return None, grid.parse_file_zukaku_list(zukaku_text, system)
        # 測量座標系では northing = Y 軸(QGIS の y)、easting = X 軸(QGIS の x)。
        return (
            grid.range_from_bounds(
                northing_min=extent.yMinimum(),
                easting_min=extent.xMinimum(),
                northing_max=extent.yMaximum(),
                easting_max=extent.xMaximum(),
            ),
            None,
        )
    except ValueError as exc:
        raise QgsProcessingException(str(exc))


def add_features_checked(sink, features) -> None:
    """sink.addFeatures の失敗（False）を検出して Processing 例外にする。

    失敗を無視すると、不完全なファイルが正常完了・自動読込として扱われるため。
    """
    if not features:
        return
    ok = sink.addFeatures(features, QgsFeatureSink.FastInsert)
    if ok is False:
        last_error = getattr(sink, "lastError", None)
        detail = last_error() if callable(last_error) else ""
        raise QgsProcessingException("出力先へのメッシュ追加に失敗しました。%s" % detail)


def write_mesh_range(
    sink,
    fields: QgsFields,
    mesh_range: MeshRange,
    system: int,
    shape: str,
    feedback,
    on_written: Callable[[int], None],
) -> bool:
    """範囲内のメッシュ（ポリゴン/ポイント）を帯単位で sink に書き込む。キャンセルされたら False。"""
    for band in grid.iter_row_bands(mesh_range, BAND_CELLS):
        if feedback.isCanceled():
            return False
        a = grid.mesh_band_arrays(system, band)
        ids, zukaku = a.ids.tolist(), a.file_zukaku.tolist()
        features = []
        if shape == "polygon":
            e0, n0 = a.easting_min.tolist(), a.northing_min.tolist()
            e1, n1 = a.easting_max.tolist(), a.northing_max.tolist()
            for i in range(len(ids)):
                feature = QgsFeature(fields)
                feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(e0[i], n0[i], e1[i], n1[i])))
                feature.setAttributes([ids[i], zukaku[i]])
                features.append(feature)
        else:
            ec, nc = a.easting_center.tolist(), a.northing_center.tolist()
            for i in range(len(ids)):
                feature = QgsFeature(fields)
                feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(ec[i], nc[i])))
                feature.setAttributes([ids[i], zukaku[i]])
                features.append(feature)
        add_features_checked(sink, features)
        on_written(len(features))  # 追加成功後にだけ件数へ加算する
    return True
