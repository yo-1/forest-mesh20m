"""Processing アルゴリズム: 領域ポリゴン（流域・県など）に合わせて 20m メッシュを作る。

領域ごとに別レイヤ（GeoPackage の 1 テーブル）へ出力する。マスク（地域森林計画対象林など）が
あれば「領域 ∩ マスク」を対象にする。採用は被覆率のしきい値で決め、領域をまたぐセルは
1 つの領域にだけ割り当てる（取りこぼし・重複なし）。手法は「簡易」（細分格子へのラスタ化）。
"""
from __future__ import annotations

import os
from typing import List

import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsRectangle,
    QgsSpatialIndex,
)

from .. import qgis_compat as compat
from ..core import crs as crs_core
from ..core import grid
from ..core import naming
from ..core import zone_mesh as zm
from ..core.grid import CELL_SIZE_M
from . import common, outputs, rasterize

_SHAPE_LABELS = common.SHAPE_LABELS[:2]
SUBSAMPLES = (3, 5, 9, 15)
MODE_GPKG, MODE_TEMP = 0, 1
MAX_AUTO_LOAD_LAYERS = 10  # GeoPackage の出力を自動で読み込むレイヤ数の上限
FIELD_COVER_PCT = "cover_pct"  # 暫定名（Issue #5 で正式名を決める）
HISTOGRAM_LABELS = ("0-10%", "10-25%", "25-50%", "50-75%", "75-90%", "90-100%未満", "100%")


class _Zone:
    def __init__(self, index, label, geom, mesh_range):
        self.index = index
        self.label = label
        self.geom = geom  # 系の座標系に変換済み
        self.bbox = geom.boundingBox()
        self.mesh_range = mesh_range


def _is_null_value(value) -> bool:
    return value is None or (hasattr(value, "isNull") and value.isNull())


class CreateMeshFromPolygonsAlgorithm(QgsProcessingAlgorithm):
    SYSTEM = "SYSTEM"
    DATUM = "DATUM"
    ZONES = "ZONES"
    NAME_FIELD = "NAME_FIELD"
    MASK = "MASK"
    THRESHOLD = "THRESHOLD"
    SUBSAMPLE = "SUBSAMPLE"
    EXCLUSIVE = "EXCLUSIVE"
    SHAPE = "SHAPE"
    OUTPUT_MODE = "OUTPUT_MODE"
    OUTPUT_GPKG = "OUTPUT_GPKG"
    TEMP_MAX = "TEMP_MAX"
    LOAD = "LOAD"
    LAYER_PREFIX = "LAYER_PREFIX"

    def name(self):
        return "create_mesh20m_from_polygons"

    def displayName(self):
        return "20m メッシュを領域ポリゴンに合わせて作成（領域ごとに別レイヤ）"

    def group(self):
        return "森林資源量メッシュ（20m）"

    def groupId(self):
        return "forest_mesh20m"

    def shortHelpString(self):
        return (
            "領域ポリゴン（流域・県・解析範囲など）1 件ごとに、20m メッシュのレイヤを 1 つ作ります。\n"
            "マスク（地域森林計画対象林など）を指定すると、「領域 ∩ マスク」にかかるメッシュだけを作ります。\n\n"
            "【採用の規則（簡易手法）】\n"
            "・各メッシュを細分した格子（細分数 n x n）に領域をラスタ化し、被覆率（メッシュ 400㎡ に対する"
            "対象面積の割合）を近似します。被覆率がしきい値以上のメッシュを採用します"
            "（0 は 1 サンプルでも重なれば採用。検出の分解能は 1/(n x n)）。\n"
            "・複数の領域にまたがるメッシュは、「中心点を含む領域 → 被覆率が大きい領域 → 領域の並び順」"
            "の優先で 1 つの領域にだけ割り当てます（しきい値を満たした候補の間では欠落も重複もありません。サンプル点にかからない重なりは検出されません）。"
            "「重複して入れる」を選ぶと、しきい値を満たした領域すべてに入れます。\n"
            "・厳密な交差面積ではなく近似です。細分数を上げると精度が上がり、時間も増えます。\n\n"
            "【出力】\n"
            "・GeoPackage（推奨）: 1 つのファイルに領域ごとのレイヤを書き出します。同名ファイルがあれば中止します。\n"
            "・一時レイヤ: 上限（メッシュ数の合計）を超えると中止します。大きな範囲では使わないでください。\n"
            "・座標系は選んだ系の平面直角座標系です。領域レイヤの CRS が異なる場合は変換します。\n"
            "・領域レイヤに「選択地物のみ」を指定できます。\n"
            "・ログに、領域ごとの被覆率の分布、しきい値で落とした面積、他領域へ割り当てた面積を出力します。"
        )

    def createInstance(self):
        return CreateMeshFromPolygonsAlgorithm()

    def initAlgorithm(self, config=None):
        polygon = [compat.sink_geometry_type("polygon")]
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
        self.addParameter(QgsProcessingParameterFeatureSource(self.ZONES, "領域レイヤ（ポリゴン）", polygon))
        self.addParameter(
            QgsProcessingParameterField(
                self.NAME_FIELD,
                "レイヤ名に使う属性 [オプション]（未指定なら zone001 などの連番）",
                parentLayerParameterName=self.ZONES,
                type=compat.field_any_type(),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.MASK, "マスクレイヤ（ポリゴン）[オプション]（例: 地域森林計画対象林）", polygon, optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.THRESHOLD,
                "採用する最小の被覆率 [%]（0 = 重なれば採用）",
                type=compat.double_number_type(),
                defaultValue=0.0,
                minValue=0.0,
                maxValue=100.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SUBSAMPLE,
                "細分数（大きいほど精度が高く遅い）",
                options=["3（粗い・高速）", "5（標準）", "9（高精度）", "15（最高精度・低速）"],
                defaultValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.EXCLUSIVE,
                "複数の領域にまたがるメッシュ",
                options=["1 つの領域にだけ割り当てる（推奨）", "しきい値を満たす領域すべてに重複して入れる"],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(self.SHAPE, "出力する形状", options=_SHAPE_LABELS, defaultValue=0)
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUTPUT_MODE,
                "出力先",
                options=["GeoPackage（推奨）", "一時レイヤ（メモリ。上限あり）"],
                defaultValue=MODE_GPKG,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_GPKG,
                "出力 GeoPackage（出力先が GeoPackage のとき必須）",
                fileFilter="GeoPackage (*.gpkg)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TEMP_MAX,
                "一時レイヤのメッシュ数の上限（合計）",
                type=compat.integer_number_type(),
                defaultValue=300000,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LOAD,
                "GeoPackage 出力後にレイヤを読み込む（%d レイヤまで。一時レイヤは常に読み込む）" % MAX_AUTO_LOAD_LAYERS,
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.LAYER_PREFIX, "レイヤ名の接頭辞", defaultValue="fr_mesh20m_", optional=True
            )
        )

    # --- 入力の読み込み ---------------------------------------------------
    def _load_zones(self, source, name_field, crs, context, feedback) -> List[_Zone]:
        request = QgsFeatureRequest().setDestinationCrs(crs, context.transformContext())
        zones, empty, repaired = [], 0, 0
        for feature in source.getFeatures(request):
            geom = feature.geometry()
            label = None
            if name_field:
                value = feature[name_field]
                label = None if _is_null_value(value) else value
            display = label if label is not None else "FID %s" % feature.id()
            if geom is None or geom.isNull() or geom.isEmpty():
                empty += 1
                continue
            if not geom.isGeosValid():
                fixed = geom.makeValid()
                if fixed is None or fixed.isNull() or fixed.isEmpty():
                    feedback.pushWarning("領域 %s: ジオメトリが無効で修復できないため除外しました。" % display)
                    empty += 1
                    continue
                geom, repaired = fixed, repaired + 1
            bbox = geom.boundingBox()
            try:
                mesh_range = grid.range_from_bounds(
                    northing_min=bbox.yMinimum(),
                    easting_min=bbox.xMinimum(),
                    northing_max=bbox.yMaximum(),
                    easting_max=bbox.xMaximum(),
                )
            except ValueError as exc:
                raise QgsProcessingException(
                    "領域 %s: %s（系の指定が合っているか確認してください）" % (display, exc)
                )
            zones.append(_Zone(len(zones), label, geom, mesh_range))
        if empty:
            feedback.pushWarning("空または修復できないジオメトリの領域を %d 件除外しました。" % empty)
        if repaired:
            feedback.pushInfo("無効なジオメトリを %d 件修復しました（makeValid）。" % repaired)
        if not zones:
            raise QgsProcessingException("有効な領域がありません。")
        return zones

    def _build_mask_reader(self, mask_source, crs, zones, context, feedback):
        """マスクの空間索引（メモリ上。利用者の操作は不要）を作り、帯ごとの取得関数を返す。"""
        tctx = context.transformContext()
        to_mask = QgsCoordinateTransform(crs, mask_source.sourceCrs(), tctx)
        from_mask = QgsCoordinateTransform(mask_source.sourceCrs(), crs, tctx)
        same_crs = crs == mask_source.sourceCrs()

        union = QgsRectangle()
        for zone in zones:
            union.combineExtentWith(zone.bbox)
        search = union if same_crs else to_mask.transformBoundingBox(union)
        index_request = QgsFeatureRequest().setFilterRect(search).setNoAttributes()
        index = QgsSpatialIndex(mask_source.getFeatures(index_request))
        feedback.pushInfo("マスクの空間索引をメモリ上に作成しました。")

        def read(band):
            n_max, e_min, n_min, e_max = band.bounds()
            window = QgsRectangle(e_min, n_min, e_max, n_max)
            rect = window if same_crs else to_mask.transformBoundingBox(window)
            ids = index.intersects(rect)
            if not ids:
                return []
            geoms = []
            for feature in mask_source.getFeatures(QgsFeatureRequest().setFilterFids(ids).setNoAttributes()):
                geom = feature.geometry()
                if geom is None or geom.isNull() or geom.isEmpty():
                    continue
                if not same_crs:
                    geom.transform(from_mask)
                geoms.append(geom)
            return geoms

        return read

    # --- 本体 -------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        system = self.parameterAsEnum(parameters, self.SYSTEM, context) + 1
        datum = crs_core.DATUMS[self.parameterAsEnum(parameters, self.DATUM, context)]
        shape = common.SHAPES[self.parameterAsEnum(parameters, self.SHAPE, context)]
        subsample = SUBSAMPLES[self.parameterAsEnum(parameters, self.SUBSAMPLE, context)]
        threshold = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        exclusive = self.parameterAsEnum(parameters, self.EXCLUSIVE, context) == 0
        mode = self.parameterAsEnum(parameters, self.OUTPUT_MODE, context)
        temp_max = self.parameterAsInt(parameters, self.TEMP_MAX, context)
        load_after = self.parameterAsBoolean(parameters, self.LOAD, context)
        prefix = (self.parameterAsString(parameters, self.LAYER_PREFIX, context) or "").strip()
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context) or None

        zones_source = self.parameterAsSource(parameters, self.ZONES, context)
        if zones_source is None:
            raise QgsProcessingException("領域レイヤを読み込めません。")
        mask_source = self.parameterAsSource(parameters, self.MASK, context)

        gpkg_path = None
        if mode == MODE_GPKG:
            gpkg_path = (self.parameterAsFileOutput(parameters, self.OUTPUT_GPKG, context) or "").strip()
            if not gpkg_path:
                raise QgsProcessingException("出力先が GeoPackage のときは、出力 GeoPackage を指定してください。")
            if not gpkg_path.lower().endswith(".gpkg"):
                gpkg_path += ".gpkg"
            if os.path.exists(gpkg_path):
                raise QgsProcessingException("出力先に同名のファイルが既にあるため中止しました（上書きしません）: %s" % gpkg_path)

        crs = common.make_crs(system, datum, feedback)
        zones = self._load_zones(zones_source, name_field, crs, context, feedback)
        names = naming.make_layer_names(prefix, [z.label for z in zones])
        feedback.pushInfo("領域: %d 件 / 細分数 %d / しきい値 %.1f%% / 重複の扱い: %s / マスク: %s" % (
            len(zones), subsample, threshold, "1 領域に割り当て" if exclusive else "重複して入れる",
            "あり" if mask_source is not None else "なし"))

        read_mask = (
            self._build_mask_reader(mask_source, crs, zones, context, feedback) if mask_source is not None else None
        )

        pixel = CELL_SIZE_M / subsample
        cells_per_band = zm.cells_per_band_for(subsample)
        zero_cache = {}

        def zeros(band):
            key = (band.n_rows, band.n_cols)
            if key not in zero_cache:
                zero_cache.clear()
                zero_cache[key] = np.zeros((band.n_rows * subsample, band.n_cols * subsample), dtype=np.uint8)
            return zero_cache[key]

        def samples_fn(index, band):
            zone = zones[index]
            n_max, e_min, n_min, e_max = band.bounds()
            window = QgsRectangle(e_min, n_min, e_max, n_max)
            if not zone.bbox.intersects(window):
                return zeros(band)
            margin = QgsRectangle(
                e_min - CELL_SIZE_M, n_min - CELL_SIZE_M, e_max + CELL_SIZE_M, n_max + CELL_SIZE_M
            )
            geom = zone.geom.clipped(margin)
            if geom is None or geom.isNull() or geom.isEmpty():
                # 切り取りに失敗した場合に黙って落とさない: 実際に交差するなら元の形で処理する。
                if not zone.geom.intersects(window):
                    return zeros(band)
                geom = zone.geom
            return rasterize.rasterize_band([geom], band, subsample)

        def mask_fn(band):
            return rasterize.rasterize_band(read_mask(band), band, subsample)

        def neighbors_fn(band):
            return [z.index for z in zones if z.mesh_range.intersect(band) is not None]

        total = max(1, zm.total_bands([z.mesh_range for z in zones], cells_per_band))
        progress = [0]

        def on_progress():
            progress[0] += 1
            feedback.setProgress(100.0 * progress[0] / total)

        fields = common.build_fields(shape)
        fields.append(compat.make_field(FIELD_COVER_PCT, "int"))
        written_total = [0]
        to_load = []
        any_incomplete = False

        for zone in zones:
            if feedback.isCanceled():
                any_incomplete = True
                break
            name = names[zone.index]
            if mode == MODE_GPKG:
                sink = outputs.open_gpkg_layer(
                    gpkg_path, name, fields, shape, crs, context.transformContext(), first_layer=(zone.index == 0)
                )
                dest_id = None
            else:
                sink, dest_id = outputs.open_temporary_layer(context, fields, shape, crs)
            feedback.pushInfo("[%d/%d] %s（メッシュ範囲 %d x %d）" % (
                zone.index + 1, len(zones), name, zone.mesh_range.n_rows, zone.mesh_range.n_cols))

            def on_band(rows, cols, cover):
                if mode == MODE_TEMP:
                    written_total[0] += len(rows)
                    if written_total[0] > temp_max:
                        raise QgsProcessingException(
                            "一時レイヤのメッシュ数が上限 %d を超えました。出力先を GeoPackage にしてください。" % temp_max
                        )
                outputs.add_features(sink, outputs.build_features(fields, system, shape, rows, cols, cover))

            try:
                stats = zm.process_zone(
                    zone.index, zone.mesh_range, subsample, threshold, exclusive, cells_per_band,
                    samples_fn, mask_fn if read_mask is not None else None, neighbors_fn, on_band,
                    is_canceled=feedback.isCanceled, on_progress=on_progress,
                )
            finally:
                if mode == MODE_GPKG:
                    outputs.close_writer(sink)
                    del sink

            self._log_stats(feedback, name, stats, pixel, zone, mask_source is not None, threshold)
            if stats.canceled:
                feedback.pushWarning("キャンセルされました。レイヤ %s は不完全です。" % name)
                any_incomplete = True
                break
            if stats.n_adopted == 0:
                feedback.pushWarning("レイヤ %s: 採用されたメッシュが 0 件です（領域とマスクの重なり・しきい値・系を確認）。" % name)
            if mode == MODE_GPKG:
                to_load.append(("%s|layername=%s" % (gpkg_path.replace("\\", "/"), name), name))
            else:
                to_load.append((dest_id, name))

        if mode == MODE_GPKG and load_after and len(to_load) > MAX_AUTO_LOAD_LAYERS:
            feedback.pushWarning(
                "レイヤが %d 件で自動読み込みの上限（%d 件）を超えるため、読み込みません。" % (len(to_load), MAX_AUTO_LOAD_LAYERS)
            )
            load_after = False
        if mode == MODE_TEMP or load_after:
            for source_id, name in to_load:
                context.addLayerToLoadOnCompletion(
                    source_id, QgsProcessingContext.LayerDetails(name, context.project(), name)
                )
        if any_incomplete and gpkg_path:
            feedback.pushWarning("処理が完了しなかったため、%s は不完全です。" % gpkg_path)
        return {self.OUTPUT_GPKG: gpkg_path} if gpkg_path else {}

    @staticmethod
    def _log_stats(feedback, name, stats, pixel, zone, has_mask, threshold):
        area = pixel * pixel  # 1 サンプルの面積 [㎡]
        ha = lambda samples: samples * area / 10000.0  # noqa: E731
        feedback.pushInfo(
            "    採用 %d メッシュ / 採用セル内の対象被覆面積 %.2f ha（近似・平面直角座標上。セル全体の面積は採用数×0.04 ha）" % (stats.n_adopted, ha(stats.adopted_samples))
        )
        feedback.pushInfo(
            "    被覆率の分布（境界セルの件数）: "
            + ", ".join("%s=%d" % (label, v) for label, v in zip(HISTOGRAM_LABELS, stats.histogram))
        )
        if stats.dropped_by_threshold_samples:
            feedback.pushInfo(
                "    しきい値 %.1f%% 未満で採用しなかった面積: %.2f ha" % (threshold, ha(stats.dropped_by_threshold_samples))
            )
        if stats.lost_to_other_zone_samples:
            feedback.pushInfo("    他の領域へ割り当てた面積: %.2f ha" % ha(stats.lost_to_other_zone_samples))
        if not has_mask:
            expected = zone.geom.area()
            covered = stats.total_samples * area
            if expected > 0 and abs(covered - expected) / expected > 0.01:
                feedback.pushWarning(
                    "    領域の面積 %.2f ha とラスタ化した面積 %.2f ha が 1%% 以上ずれています。細分数を上げるか、"
                    "ジオメトリ（無効・極端に細長い形状）を確認してください。" % (expected / 10000.0, covered / 10000.0)
                )
