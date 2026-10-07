"""Behavior tests with injected failing sinks, without claiming a QGIS runtime test."""
import importlib
import pathlib
import sys
import types
import unittest
from unittest.mock import patch
import tempfile
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent / "work"))

qgis = types.ModuleType("qgis")
core = types.ModuleType("qgis.core")
qgis.core = core
sys.modules["qgis"] = qgis
sys.modules["qgis.core"] = core


class QgsProcessingException(Exception):
    pass


class QgsFeature:
    def __init__(self, fields):
        self.fields = fields

    def setGeometry(self, value):
        self.geometry = value

    def setAttributes(self, value):
        self.attributes = value


class QgsGeometry:
    fromRect = staticmethod(lambda rect: rect)
    fromPointXY = staticmethod(lambda point: point)
    fromPolylineXY = staticmethod(lambda points: points)


class FailingSink:
    FastInsert = 2

    def addFeatures(self, features, flags):
        return False

    def lastError(self):
        return "injected disk error"


class SuccessfulSink(FailingSink):
    def addFeatures(self, features, flags):
        return True


class Writer(FailingSink):
    def flushBuffer(self):
        return False

    def errorMessage(self):
        return "secondary error"


for name in (
    "QgsCoordinateReferenceSystem", "QgsFields", "QgsPointXY", "QgsRectangle",
    "QgsProcessingAlgorithm", "QgsProcessingContext", "QgsProcessingParameterBoolean",
    "QgsProcessingParameterEnum", "QgsProcessingParameterExtent",
    "QgsProcessingParameterFeatureSink", "QgsProcessingParameterNumber",
    "QgsProcessingParameterString", "QgsProcessingParameterFolderDestination",
    "QgsProcessingParameterFeatureSource", "QgsProcessingParameterField",
    "QgsProcessingParameterFileDestination", "QgsFeatureRequest",
    "QgsSpatialIndex", "QgsCoordinateTransform", "QgsProcessingUtils",
    "QgsVectorFileWriter",
):
    setattr(core, name, type(name, (), {"__init__": lambda self, *args, **kwargs: None}))
core.QgsFeature = QgsFeature
core.QgsGeometry = QgsGeometry
core.QgsFeatureSink = FailingSink
core.QgsProcessingException = QgsProcessingException

compat = types.ModuleType("forest_mesh20m.qgis_compat")
sys.modules[compat.__name__] = compat

from forest_mesh20m.core.grid import MeshRange

outputs = importlib.import_module("forest_mesh20m.algorithms.outputs")
common = importlib.import_module("forest_mesh20m.algorithms.common")
create = importlib.import_module("forest_mesh20m.algorithms.create_mesh_algorithm")
polygon = importlib.import_module("forest_mesh20m.algorithms.from_polygons_algorithm")


class Feedback:
    def __init__(self, canceled=False):
        self.canceled = canceled

    def isCanceled(self):
        return self.canceled

    def setProgress(self, value):
        pass


class FailureTests(unittest.TestCase):
    def test_failed_add_does_not_count(self):
        count = []
        with self.assertRaisesRegex(QgsProcessingException, "injected disk error"):
            common.write_mesh_range(FailingSink(), None, MeshRange(0, 1, 0, 1), 8,
                                    "polygon", Feedback(), count.append)
        self.assertEqual(count, [])

    def test_success_counts_only_after_add(self):
        count = []
        self.assertTrue(common.write_mesh_range(SuccessfulSink(), None, MeshRange(0, 1, 0, 1),
                                                8, "point", Feedback(), count.append))
        self.assertEqual(count, [1])

    def test_line_fails_loudly(self):
        with self.assertRaisesRegex(QgsProcessingException, "injected disk error"):
            create.CreateMeshAlgorithm._write_lines(FailingSink(), None,
                                                    [MeshRange(0, 1, 0, 1)], Feedback())

    def test_flush_failure_fails_loudly(self):
        with self.assertRaisesRegex(QgsProcessingException, "injected disk error"):
            outputs.close_writer(Writer())

    def test_canceled_band_does_not_count(self):
        count = []
        self.assertFalse(common.write_mesh_range(SuccessfulSink(), None, MeshRange(0, 1, 0, 1),
                                                 8, "polygon", Feedback(True), count.append))
        self.assertEqual(count, [])

    def test_canceled_line_raises(self):
        with self.assertRaisesRegex(QgsProcessingException, "キャンセル"):
            create.CreateMeshAlgorithm._write_lines(SuccessfulSink(), None,
                                                    [MeshRange(0, 1, 0, 1)], Feedback(True))

    def _run_polygon(self, writer):
        class Rect:
            def __init__(self, *args):
                pass

            def intersects(self, other):
                return True

        class Geom:
            def boundingBox(self):
                return Rect()

            def clipped(self, other):
                return self

            def isNull(self):
                return False

            def isEmpty(self):
                return False

            def area(self):
                return 400

        class Context:
            layers = []

            def transformContext(self):
                return None

            def project(self):
                return None

            def addLayerToLoadOnCompletion(self, *args):
                self.layers.append(args)

        class GpkgWriter(Writer):
            def __init__(self, add_ok, flush_ok):
                self.add_ok = add_ok
                self.flush_ok = flush_ok

            def addFeatures(self, features, flags):
                return self.add_ok

            def flushBuffer(self):
                return self.flush_ok

        alg = polygon.CreateMeshFromPolygonsAlgorithm()
        alg.parameterAsEnum = lambda p, name, ctx: p.get(name, 0)
        alg.parameterAsInt = lambda p, name, ctx: p.get(name, 300000)
        alg.parameterAsDouble = lambda p, name, ctx: p.get(name, 0.0)
        alg.parameterAsBoolean = lambda p, name, ctx: p.get(name, False)
        alg.parameterAsString = lambda p, name, ctx: p.get(name, "")
        alg.parameterAsSource = lambda p, name, ctx: object() if name == alg.ZONES else None
        alg.parameterAsFileOutput = lambda p, name, ctx: p[name]
        zone = polygon._Zone(0, "test", Geom(), MeshRange(15000, 15001, 8000, 8001))
        zone.bbox = Rect()
        alg._load_zones = lambda *args: [zone]
        context = Context()
        feedback = Feedback()
        feedback.pushInfo = lambda text: None
        feedback.pushWarning = lambda text: None
        with tempfile.TemporaryDirectory() as folder:
            args = {alg.OUTPUT_GPKG: folder + "/out.gpkg", alg.LOAD: True}
            with patch.object(polygon.common, "make_crs", return_value=object()), \
                 patch.object(polygon.common, "build_fields", return_value=[]), \
                 patch.object(polygon.compat, "make_field", return_value=None, create=True), \
                 patch.object(polygon.compat, "wkb_type", return_value=None, create=True), \
                 patch.object(polygon, "QgsRectangle", Rect), \
                 patch.object(polygon.rasterize, "rasterize_band", side_effect=lambda g,b,n: np.ones((b.n_rows*n,b.n_cols*n),dtype=np.uint8)), \
                 patch.object(polygon.outputs, "open_gpkg_layer", return_value=GpkgWriter(*writer)):
                with self.assertRaisesRegex(QgsProcessingException, "injected disk error"):
                    alg.processAlgorithm(args, context, feedback)
        self.assertEqual(context.layers, [])

    def test_polygon_add_failure_never_loads_incomplete_gpkg(self):
        self._run_polygon((False, True))

    def test_polygon_flush_failure_never_loads_incomplete_gpkg(self):
        self._run_polygon((True, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
