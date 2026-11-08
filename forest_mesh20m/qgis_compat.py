"""QGIS のバージョン差を吸収する薄い層。

QGIS 3.x の途中で列挙型や QgsField のコンストラクタが変わっているため、
バージョン依存の分岐をこのファイルだけに閉じ込める。
最低対応版は QGIS 3.38（QgsField の QMetaType コンストラクタ導入版）。
"""
from __future__ import annotations

from qgis.core import Qgis, QgsField, QgsProcessing, QgsProcessingParameterNumber



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
        "polygon": QgsWkbTypes.Type.Polygon,
        "line": QgsWkbTypes.Type.LineString,
        "point": QgsWkbTypes.Type.Point,
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
        "polygon": QgsProcessing.SourceType.TypeVectorPolygon,
        "line": QgsProcessing.SourceType.TypeVectorLine,
        "point": QgsProcessing.SourceType.TypeVectorPoint,
        "any": QgsProcessing.SourceType.TypeVectorAnyGeometry,
    }[kind]


def integer_number_type():
    if hasattr(Qgis, "ProcessingNumberParameterType"):
        return Qgis.ProcessingNumberParameterType.Integer
    return QgsProcessingParameterNumber.Type.Integer


def make_field(name: str, kind: str, length: int = 0) -> QgsField:
    """kind: 'string' | 'int'

    QgsField の QMetaType コンストラクタ（QGIS 3.38 で導入）だけを使う。
    Qt6 チェック（QGS402）が対象にする QVariant 側の型指定の
    旧分岐は、最低版を 3.38 に上げて廃止した。
    """
    from qgis.PyQt.QtCore import QMetaType

    qtype = QMetaType.Type.QString if kind == "string" else QMetaType.Type.Int
    return QgsField(name, qtype, "", length)


def double_number_type():
    if hasattr(Qgis, "ProcessingNumberParameterType"):
        return Qgis.ProcessingNumberParameterType.Double
    return QgsProcessingParameterNumber.Type.Double


def field_any_type():
    """QgsProcessingParameterField の type 引数（全型の属性を選べる）。"""
    if hasattr(Qgis, "ProcessingFieldParameterDataType"):
        return Qgis.ProcessingFieldParameterDataType.Any
    from qgis.core import QgsProcessingParameterField

    return QgsProcessingParameterField.DataType.Any
