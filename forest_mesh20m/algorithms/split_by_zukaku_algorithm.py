"""Processing アルゴリズム: 20m メッシュをファイル単位図郭ごとに GeoPackage へ出力する。

出力ファイル名は仕様書 表2.1 の fr_mesh20m_{図郭}_{整備年}.gpkg。図郭ごとに 1 ファイルずつ
順次書き出すため、広い範囲でもメモリ使用量は 1 図郭分（最大 75 万メッシュ）に収まる。
"""
from __future__ import annotations

import os
import re

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProcessingUtils,
)

from .. import qgis_compat as compat
from ..core import crs as crs_core
from ..core import grid
from ..core import zukaku as zk
from . import common

# この algorithm で選べる形状（ラインは図郭ファイルに分ける意味が薄いため除く）
_SHAPE_LABELS = common.SHAPE_LABELS[:2]

# 出力後に QGIS へ自動読み込みするファイル数の上限。1 ファイル 75 万フィーチャあり、
# 何十枚も読み込むと描画・操作が著しく重くなるため、超える場合は読み込まずに警告する。
MAX_AUTO_LOAD_FILES = 10


class SplitByZukakuAlgorithm(QgsProcessingAlgorithm):
    SYSTEM = "SYSTEM"
    DATUM = "DATUM"
    SHAPE = "SHAPE"
    EXTENT = "EXTENT"
    ZUKAKU = "ZUKAKU"
    WHOLE = "WHOLE"
    YEAR = "YEAR"
    LOAD = "LOAD"
    MAX_FILES = "MAX_FILES"
    OUTPUT_DIR = "OUTPUT_DIR"

    def name(self):
        return "create_mesh20m_by_zukaku"

    def displayName(self):
        return "20m メッシュを図郭ごとに出力（GeoPackage）"

    def group(self):
        return "森林資源量メッシュ（20m）"

    def groupId(self):
        return "forest_mesh20m"

    def shortHelpString(self):
        return (
            "20m メッシュを、ファイル単位図郭（50000 図郭の 4 分割 = 15km x 20km）ごとに、"
            "別々の GeoPackage へ出力します。\n"
            "ファイル名: fr_mesh20m_{図郭}_{整備年}.gpkg（仕様書 表2.1）。"
            "GeoPackage 内のレイヤ名はファイル名から拡張子を除いたものです。\n\n"
            "・範囲は「作成範囲」か「ファイル単位図郭名」のどちらか一方を指定します。\n"
            "・「作成範囲」を指定した場合、端の図郭は範囲で切り取ります。"
            "図郭全体を出力するには「図郭全体を出力する」をオンにします。\n"
            "・出力先に同名のファイルがある場合は、何も書き込まずに中止します。\n"
            "・「出力後にレイヤを読み込む」をオンにすると、完了後に QGIS へ読み込みます"
            "（%d ファイルまで。1 ファイル 75 万フィーチャあり、描画が重くなります）。\n"
            "・1 図郭あたり最大 75 万メッシュで、図郭ごとに順次書き込みます。\n"
            "・属性は JF20mID（12桁）と file_zukaku（暫定名）です。"
        ) % MAX_AUTO_LOAD_FILES

    def createInstance(self):
        return SplitByZukakuAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SYSTEM, "平面直角座標系の系番号", options=crs_core.system_labels(), defaultValue=8
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
            QgsProcessingParameterEnum(self.SHAPE, "出力する形状", options=_SHAPE_LABELS, defaultValue=0)
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
            QgsProcessingParameterBoolean(
                self.WHOLE,
                "図郭全体を出力する（作成範囲で切り取らない。「作成範囲」指定時のみ有効）",
                defaultValue=False,
            )
        )
        # 整備年は既定値を置かない（誤った年がファイル名に入るのを避けるため必須入力）
        self.addParameter(QgsProcessingParameterString(self.YEAR, "整備年（西暦4桁。ファイル名に使用）"))
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LOAD,
                "出力後にレイヤを読み込む（%d ファイルまで。既定はオフ）" % MAX_AUTO_LOAD_FILES,
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_FILES,
                "作成するファイル数の上限",
                type=compat.integer_number_type(),
                defaultValue=100,
                minValue=1,
            )
        )
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUTPUT_DIR, "出力フォルダ"))

    def processAlgorithm(self, parameters, context, feedback):
        system = self.parameterAsEnum(parameters, self.SYSTEM, context) + 1
        datum = crs_core.DATUMS[self.parameterAsEnum(parameters, self.DATUM, context)]
        shape = common.SHAPES[self.parameterAsEnum(parameters, self.SHAPE, context)]
        whole = self.parameterAsBoolean(parameters, self.WHOLE, context)
        load_after = self.parameterAsBoolean(parameters, self.LOAD, context)
        max_files = self.parameterAsInt(parameters, self.MAX_FILES, context)
        year_text = (self.parameterAsString(parameters, self.YEAR, context) or "").strip()
        if not re.fullmatch(r"\d{4}", year_text):
            raise QgsProcessingException("整備年は西暦 4 桁の数字で指定してください: %r" % year_text)
        year = int(year_text)

        crs = common.make_crs(system, datum, feedback)
        extent_range, zukaku_ranges = common.read_area(
            self, parameters, context, crs, self.EXTENT, self.ZUKAKU, system
        )
        if extent_range is not None:
            targets = grid.covering_file_zukaku(system, extent_range, whole=whole)
        else:
            targets = [(zk.file_zukaku_code(system, r.row_start, r.col_start), r) for r in zukaku_ranges]

        if len(targets) > max_files:
            raise QgsProcessingException(
                "作成するファイルが %d 件で、上限 %d 件を超えています。範囲を狭めるか、上限を引き上げてください。"
                % (len(targets), max_files)
            )

        folder = self.parameterAsFileOutput(parameters, self.OUTPUT_DIR, context)
        if not folder:
            raise QgsProcessingException("出力フォルダを指定してください。")
        plan = [
            (code, mesh_range, os.path.join(folder, zk.gpkg_file_name(code, year)))
            for code, mesh_range in targets
        ]
        # 何も書き込む前に、既存ファイルとの衝突をすべて検出する。
        existing = [path for _, _, path in plan if os.path.exists(path)]
        if existing:
            raise QgsProcessingException(
                "出力先に同名のファイルが既にあるため中止しました（%d 件。上書きしません）:\n%s"
                % (len(existing), "\n".join(existing[:5]) + ("\n..." if len(existing) > 5 else ""))
            )
        if load_after and len(plan) > MAX_AUTO_LOAD_FILES:
            feedback.pushWarning(
                "ファイルが %d 件で自動読み込みの上限（%d 件）を超えるため、レイヤは読み込みません。"
                % (len(plan), MAX_AUTO_LOAD_FILES)
            )
            load_after = False
        os.makedirs(folder, exist_ok=True)

        total_cells = sum(r.n_cells for _, r, _ in plan)
        feedback.pushInfo("対象: %d ファイル / %d メッシュ" % (len(plan), total_cells))

        fields = common.build_fields(shape)
        done = [0]

        def on_written(n):
            done[0] += n
            feedback.setProgress(100.0 * done[0] / total_cells)

        for index, (code, mesh_range, path) in enumerate(plan, start=1):
            if feedback.isCanceled():
                break
            layer_name = zk.gpkg_layer_name(code, year)
            # QGIS のログにも現れる、OGR 出力先の書式。パス中の ' はエスケープする。
            destination = "ogr:dbname='%s' table=\"%s\" (geom)" % (
                path.replace("\\", "/").replace("'", "\\'"),
                layer_name,
            )
            sink, _ = QgsProcessingUtils.createFeatureSink(
                destination, context, fields, compat.wkb_type(shape), crs
            )
            if sink is None:
                raise QgsProcessingException("出力ファイルを作成できません: %s" % path)
            feedback.pushInfo("[%d/%d] %s（%d メッシュ）" % (index, len(plan), path, mesh_range.n_cells))
            completed = common.write_mesh_range(sink, fields, mesh_range, system, shape, feedback, on_written)
            del sink  # 参照を手放して書き込みを確定・ファイルを閉じる
            if not completed:
                feedback.pushWarning("キャンセルされました。%s は不完全です。" % path)
                break
            if load_after:
                # 完了したファイルだけを、処理終了後にプロジェクトへ追加する。
                context.addLayerToLoadOnCompletion(
                    "%s|layername=%s" % (path.replace("\\", "/"), layer_name),
                    QgsProcessingContext.LayerDetails(layer_name, context.project(), layer_name),
                )

        return {self.OUTPUT_DIR: folder}
