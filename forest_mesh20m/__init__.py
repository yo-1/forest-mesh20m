"""森林資源量集計メッシュ (20m) 作成ツール。

core パッケージは QGIS 非依存。QGIS から読み込まれるときだけ classFactory が
QGIS 依存の plugin モジュールを import する（テスト時に qgis を要求しないため）。
"""
__version__ = "0.3.2"


def classFactory(iface):  # noqa: N802 (QGIS の規約名)
    from .plugin import ForestMeshPlugin

    return ForestMeshPlugin(iface)
