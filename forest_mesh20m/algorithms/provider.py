"""Processing プロバイダ。"""
from __future__ import annotations

from qgis.core import QgsProcessingProvider

from .create_mesh_algorithm import CreateMeshAlgorithm
from .from_polygons_algorithm import CreateMeshFromPolygonsAlgorithm
from .split_by_zukaku_algorithm import SplitByZukakuAlgorithm


class ForestMeshProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(CreateMeshAlgorithm())
        self.addAlgorithm(SplitByZukakuAlgorithm())
        self.addAlgorithm(CreateMeshFromPolygonsAlgorithm())

    def id(self):
        return "forest_mesh20m"

    def name(self):
        return "森林資源量メッシュ（20m）"

    def longName(self):
        return self.name()
