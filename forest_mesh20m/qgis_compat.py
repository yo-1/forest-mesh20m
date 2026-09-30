"""QGIS のバージョン差を吸収する薄い層。

QGIS 3.x の途中で列挙型や QgsField のコンストラクタが変わっているため、
バージョン依存の分岐をこのファイルだけに閉じ込める。
未検証: 実機の QGIS ではまだ動かしていない。QGIS 3.34 / 3.40 / 3.44 で要確認。
"""
from __future__ import annotations

from qgis.core import Qgis, QgsField, QgsProcessing, QgsProcessingParameterNumber

_NEW_FIELD_TYPES = Qgis.versionInt() >= 33800  # QgsField(name, QMetaType.Type) は 3.38 で導入


def wkb_type(kind: str):
    """kind: 'polygon' | 'line' | 'point'"""
    if hasattr(Qgis, "WkbType"):
        return {
            "polygon": Qgis.WkbType.Polygon,
            "line": Qgis.WkbType.LineString,
            "point": Qgis.WkbType.Point,
        }[kind]
    from qgis.core import QgsWkbTypes  # 古い QGIS 用

    return {
        "polygon": QgsWkbTypes.Polygon,
        "line": QgsWkbTypes.LineString,
        "point": QgsWkbTypes.Point,
    }[kind]


def sink_geometry_type(kind: str):
    """QgsProcessingParameterFeatureSink の type 引数。kind: polygon/line/point/any"""
    if hasattr(Qgis, "ProcessingSourceType"):
        return {
            "polygon": Qgis.ProcessingSourceType.VectorPolygon,
            "line": Qgis.ProcessingSourceType.VectorLine,
            "point": Qgis.ProcessingSourceType.VectorPoint,
            "any": Qgis.ProcessingSourceType.VectorAnyGeometry,
        }[kind]
    return {
        "polygon": QgsProcessing.TypeVectorPolygon,
        "line": QgsProcessing.TypeVectorLine,
        "point": QgsProcessing.TypeVectorPoint,
        "any": QgsProcessing.TypeVectorAnyGeometry,
    }[kind]


def integer_number_type():
    if hasattr(Qgis, "ProcessingNumberParameterType"):
        return Qgis.ProcessingNumberParameterType.Integer
    return QgsProcessingParameterNumber.Integer


def make_field(name: str, kind: str, length: int = 0) -> QgsField:
    """kind: 'string' | 'int'"""
    if _NEW_FIELD_TYPES:
        from qgis.PyQt.QtCore import QMetaType

        qtype = QMetaType.Type.QString if kind == "string" else QMetaType.Type.Int
    else:
        from qgis.PyQt.QtCore import QVariant

        qtype = QVariant.String if kind == "string" else QVariant.Int
    return QgsField(name, qtype, "", length)
