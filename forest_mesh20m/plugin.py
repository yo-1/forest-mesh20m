"""QGIS プラグイン本体。Processing プロバイダの登録・解除のみを行う。"""
from __future__ import annotations

from qgis.core import QgsApplication

from .algorithms.provider import ForestMeshProvider


class ForestMeshPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initProcessing(self):
        self.provider = ForestMeshProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        # 再読み込み時にプロバイダが二重登録されないよう、必ず解除する。
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
