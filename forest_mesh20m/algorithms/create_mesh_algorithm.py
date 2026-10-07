"""Processing アルゴリズム: 森林資源量集計メッシュ (20m) の格子を作成する。

国土基本図図郭ベース（仕様書 参考3）で、系番号と範囲（またはファイル単位図郭名）から
ポリゴン / ポイント / ライン（格子線）のいずれかを出力する。属性の集計は行わない。
"""
from __future__ import annotations

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

from .. import qgis_compat as compat
from ..core import crs as crs_core
from ..core import grid
from . import common, outputs


class CreateMeshAlgorithm(QgsProcessingAlgorithm):
    SYSTEM = "SYSTEM"
    DATUM = "DATUM"
    SHAPE = "SHAPE"
    EXTENT = "EXTENT"
    ZUKAKU = "ZUKAKU"
    MAX_CELLS = "MAX_CELLS"
    OUTPUT = "OUTPUT"

    # --- メタ情報 ---------------------------------------------------------
    def name(self):
        return "create_mesh20m"

    def displayName(self):
        return "20m メッシュを作成（国土基本図図郭ベース）"

    def group(self):
        return "森林資源量メッシュ（20m）"

    def groupId(self):
        return "forest_mesh20m"

    def shortHelpString(self):
        return (
            "森林資源量集計メッシュ（20m）の格子を、国土基本図図郭ベースで作成します。\n"
            "準拠: 森林情報に関するオープンデータ標準仕様書 Ver.2.1（航空レーザ森林資源解析データ編）\n\n"
            "・範囲は「作成範囲」か「ファイル単位図郭名」（例: 04HE2）のどちらか一方を指定します。\n"
            "・ポリゴン/ポイントの属性: JF20mID（12桁）、file_zukaku（ファイル単位図郭名）。属性名は暫定です。\n"
            "・ライン（格子線）は範囲全体を通る線で、メッシュ数に比例しないため広い範囲でも軽く作れます。\n"
            "・ポリゴン/ポイントは件数が多くなります（1 ファイル単位 = 75 万件）。上限を超える場合は中止します。\n"
            "・広い範囲を図郭ごとのファイルに分けて出力するには、「20m メッシュを図郭ごとに出力」を使ってください。\n"
            "・出力の座標系は、選んだ系番号と測地系の平面直角座標系です。"
        )

    def createInstance(self):
        return CreateMeshAlgorithm()

    # --- パラメータ ---------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SYSTEM,
                "平面直角座標系の系番号",
                options=crs_core.system_labels(),
                defaultValue=8,  # index 8 = 系IX
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.DATUM,
                "測地系",
                options=["日本測地系2011 (JGD2011)", "日本測地系2000 (JGD2000)"],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SHAPE, "出力する形状", options=common.SHAPE_LABELS, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(self.EXTENT, "作成範囲 [オプション]", optional=True)
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.ZUKAKU,
                "ファイル単位図郭名 [オプション]（例: 04HE2, 04HE3。カンマ・空白区切りで複数可）",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_CELLS,
                "作成するメッシュ数の上限（ポリゴン/ポイントのみ）",
                type=compat.integer_number_type(),
                defaultValue=1_000_000,
                minValue=1,
            )
        )
        # 出力形状は実行時に決まるため、シンクの型は「任意のジオメトリ」で宣言する。
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, "20m メッシュ", type=compat.sink_geometry_type("any")
            )
        )

    # --- 実行 ---------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        system = self.parameterAsEnum(parameters, self.SYSTEM, context) + 1
        datum = crs_core.DATUMS[self.parameterAsEnum(parameters, self.DATUM, context)]
        shape = common.SHAPES[self.parameterAsEnum(parameters, self.SHAPE, context)]
        max_cells = self.parameterAsInt(parameters, self.MAX_CELLS, context)

        crs = common.make_crs(system, datum, feedback)
        extent_range, zukaku_ranges = common.read_area(
            self, parameters, context, crs, self.EXTENT, self.ZUKAKU, system
        )
        ranges = [extent_range] if extent_range is not None else zukaku_ranges
        total_cells = sum(r.n_cells for r in ranges)
        feedback.pushInfo("対象メッシュ数: %d（範囲 %d 件）" % (total_cells, len(ranges)))

        if shape != "line" and total_cells > max_cells:
            raise QgsProcessingException(
                "作成するメッシュが %d 件で、上限 %d 件を超えています。範囲を狭めるか、"
                "上限を引き上げてください（メモリ使用量に注意）。格子線だけならラインを選んでください。"
                "広い範囲は「20m メッシュを図郭ごとに出力」が使えます。" % (total_cells, max_cells)
            )

        fields = common.build_fields(shape)
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, compat.wkb_type(shape), crs
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        if shape == "line":
            self._write_lines(sink, fields, ranges, feedback)
        else:
            done = [0]

            def on_written(n):
                done[0] += n
                feedback.setProgress(100.0 * done[0] / total_cells)

            for mesh_range in ranges:
                if not common.write_mesh_range(sink, fields, mesh_range, system, shape, feedback, on_written):
                    raise QgsProcessingException("処理がキャンセルされました。出力は不完全です。")

        return {self.OUTPUT: dest_id}

    @staticmethod
    def _write_lines(sink, fields, ranges, feedback):
        for i, mesh_range in enumerate(ranges):
            if feedback.isCanceled():
                raise QgsProcessingException("処理がキャンセルされました。出力は不完全です。")
            features = []
            for ln in grid.grid_lines(mesh_range):
                feature = QgsFeature(fields)
                feature.setGeometry(
                    QgsGeometry.fromPolylineXY(
                        [
                            QgsPointXY(ln.easting_start, ln.northing_start),
                            QgsPointXY(ln.easting_end, ln.northing_end),
                        ]
                    )
                )
                feature.setAttributes([ln.kind, int(ln.boundary_no)])
                features.append(feature)
            outputs.add_features(sink, features)
            feedback.setProgress(100.0 * (i + 1) / len(ranges))
