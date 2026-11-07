"""QGIS 実機検証スクリプト（v0.4.2 追加検証）。pytest / unittest の対象外（QGIS が必要）。

使い方（QGIS の Python コンソール、または qgis-dev-probe の execute_code）:
    REPO = r"C:\\...\\forest-mesh20m"   # forest_mesh20m パッケージの親フォルダ
    OUT = r"C:\\...\\out"               # 出力用の空フォルダ（存在してよい。中身は消さない）
    MODE = "functional"                 # "functional" / "perf" / "cancel"
    PERF_RADIUS = 3000                  # MODE="perf" のときの円領域の半径 [m]
    exec(open(r"...\\verify_v042_in_qgis.py", encoding="utf-8").read())
    # 結果は変数 result（dict）に入る

方針:
- 期待値は実装から独立に作る。メッシュ座標・JF20mID・file_zukaku は仕様書 3.2/3.3 の式から、
  交差面積は有理数（Fraction）の Sutherland-Hodgman クリップから求める。
- 座標は 2 進小数（dyadic）で与え、GEOS の演算が厳密になるようにする（同値の境界を作るため）。
- プラグインは QGIS のプロファイルへインストールせず、REPO を sys.path に足してプロバイダを一時登録する。
  終了時にプロバイダを解除する。ユーザーのプロジェクトには触れない（読み込みは別の QgsProject へ）。
"""
import ctypes
import hashlib
import math
import os
import sqlite3
import sys
import threading
import time
import traceback
from fractions import Fraction as F

REPO = globals().get("REPO")
OUT = globals().get("OUT")
MODE = globals().get("MODE", "functional")
PERF_RADIUS = globals().get("PERF_RADIUS", 3000)

TOP, LEFT, CELL = 300000, -160000, 20
ROWL, COLL = "ABCDEFGHIJKLMNOPQRST", "ABCDEFGH"
SYSTEM = 8  # 系VIII (EPSG:6676, JGD2011)
SYSTEM_ENUM = SYSTEM - 1
ALG = "forest_mesh20m:"


# ---------- 実装から独立した期待値 ----------
def jf_id(system, row, col):
    return "%02d%s%s%d%d%03d%03d" % (
        system, ROWL[row // 1500], COLL[col // 2000], (row % 1500) // 150, (col % 2000) // 200, row % 150, col % 200)


def file_zukaku(system, row, col):
    branch = 1 + ((col % 2000) // 1000) + 2 * ((row % 1500) // 750)
    return "%02d%s%s%d" % (system, ROWL[row // 1500], COLL[col // 2000], branch)


def decode_id12(text):
    # 12 桁: 系2 + 行英字 + 列英字 + 5000行1 + 5000列1 + 行3 + 列3
    assert len(text) == 12, text
    system = int(text[0:2])
    row = ROWL.index(text[2]) * 1500 + int(text[4]) * 150 + int(text[6:9])
    col = COLL.index(text[3]) * 2000 + int(text[5]) * 200 + int(text[9:12])
    return system, row, col


def cell_rect(row, col):
    x0 = LEFT + col * CELL
    y1 = TOP - row * CELL
    return (F(x0), F(y1 - CELL), F(x0 + CELL), F(y1))


def _clip(pts, inside, inter):
    out = []
    if not pts:
        return out
    prev = pts[-1]
    for cur in pts:
        if inside(cur):
            if not inside(prev):
                out.append(inter(prev, cur))
            out.append(cur)
        elif inside(prev):
            out.append(inter(prev, cur))
        prev = cur
    return out


def sh_clip(pts, x0, y0, x1, y1):
    def ix(x):
        return lambda p, q: (F(x), p[1] + (q[1] - p[1]) * (x - p[0]) / (q[0] - p[0]))

    def iy(y):
        return lambda p, q: (p[0] + (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1]), F(y))

    pts = _clip(pts, lambda p: p[0] >= x0, ix(x0))
    pts = _clip(pts, lambda p: p[0] <= x1, ix(x1))
    pts = _clip(pts, lambda p: p[1] >= y0, iy(y0))
    pts = _clip(pts, lambda p: p[1] <= y1, iy(y1))
    return pts


def poly_area(pts):
    n = len(pts)
    if n < 3:
        return F(0)
    s = F(0)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def rect_isect(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def fr(v):
    return F(v)


def zone_cell_area(parts, cell, mask):
    """parts: 単純多角形（頂点リスト）のリスト。互いに内部で重ならないこと。"""
    c = cell
    if mask is not None:
        c = rect_isect(cell, mask["outer"])
        if c is None:
            return F(0)
    total = F(0)
    for ring in parts:
        total += poly_area(sh_clip(ring, *c))
        for h in (mask or {}).get("holes", []):
            ch = rect_isect(c, h)
            if ch is not None:
                total -= poly_area(sh_clip(ring, *ch))
    return total


def expected_cells(parts, mask, threshold):
    xs = [p[0] for ring in parts for p in ring]
    ys = [p[1] for ring in parts for p in ring]
    r0 = int(math.floor((TOP - max(ys)) / CELL))
    r1 = int(math.ceil((TOP - min(ys)) / CELL))
    c0 = int(math.floor((min(xs) - LEFT) / CELL))
    c1 = int(math.ceil((max(xs) - LEFT) / CELL))
    out = {}
    for row in range(r0, r1 + 1):
        for col in range(c0, c1 + 1):
            area = zone_cell_area(parts, cell_rect(row, col), mask)
            if area > 0 and area * 100 / 400 >= F(threshold).limit_denominator(10 ** 9):
                out[(row, col)] = area * 100 / 400
    return out


def half_up(p):
    return int(math.floor(p + F(1, 2)))


# ---------- QGIS 側 ----------
from qgis.core import (  # noqa: E402
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsGeometry,
    QgsProcessingContext, QgsProcessingFeedback, QgsProject, QgsVectorLayer, QgsAggregateCalculator,
    QgsWkbTypes,
)
import processing  # noqa: E402

sys.path.insert(0, REPO)
for _name in [k for k in sys.modules if k.startswith("forest_mesh20m")]:
    del sys.modules[_name]
from forest_mesh20m.algorithms.provider import ForestMeshProvider  # noqa: E402

registry = QgsApplication.processingRegistry()
_provider = registry.providerById("forest_mesh20m")
_added_provider = False
if _provider is None:
    _provider = ForestMeshProvider()
    registry.addProvider(_provider)
    _added_provider = True


class FB(QgsProcessingFeedback):
    def __init__(self):
        super().__init__()
        self.log = []

    def pushInfo(self, m):
        self.log.append(str(m))

    def pushWarning(self, m):
        self.log.append("W:" + str(m))

    def reportError(self, m, fatalError=False):
        self.log.append("E:" + str(m))


def wkt_poly(rings):
    return "POLYGON(" + ",".join("(" + ",".join("%r %r" % (float(x), float(y)) for x, y in r + [r[0]]) + ")" for r in rings) + ")"


def rect_ring(x0, y0, x1, y1):
    return [(F(x0), F(y0)), (F(x1), F(y0)), (F(x1), F(y1)), (F(x0), F(y1))]


def make_layer(name, geoms, crs="EPSG:6676", labels=None):
    layer = QgsVectorLayer("Polygon?crs=%s&field=name:string(40)" % crs, name, "memory")
    feats = []
    for i, g in enumerate(geoms):
        f = QgsFeature(layer.fields())
        f.setGeometry(g)
        f.setAttributes([labels[i] if labels else "z%d" % (i + 1)])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


def read_gpkg_layer(path, name):
    layer = QgsVectorLayer("%s|layername=%s" % (path.replace("\\", "/"), name), name, "ogr")
    return layer


def gpkg_tables(path):
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [r[0] for r in con.execute("select table_name from gpkg_contents where data_type='features' order by table_name")]
        geom = {r[0]: (r[1], r[2]) for r in con.execute("select table_name, geometry_type_name, srs_id from gpkg_geometry_columns")}
    finally:
        con.close()
    return integrity, tables, geom


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_layer(layer, expected, tol=1e-9):
    """出力レイヤと期待値(dict[(row,col)] -> 厳密な被覆率[%])を照合する。"""
    res = {"valid": layer.isValid()}
    if not layer.isValid():
        return res
    got, dup, bad_geom, bad_zk, bad_sys, bad_pct, tie_ok = {}, 0, 0, 0, 0, [], 0
    for f in layer.getFeatures():
        text = f["JF20mID"]
        system, row, col = decode_id12(text)
        if system != SYSTEM:
            bad_sys += 1
        if (row, col) in got:
            dup += 1
        got[(row, col)] = int(f["cover_pct"])
        if f["file_zukaku"] != file_zukaku(system, row, col):
            bad_zk += 1
        bb = f.geometry().boundingBox()
        r = cell_rect(row, col)
        geom_ok = (
            f.geometry().isGeosValid()
            and abs(f.geometry().area() - 400) < 1e-6
            and (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()) == tuple(float(v) for v in r)
            and jf_id(system, row, col) == text
        )
        if not geom_ok:
            bad_geom += 1
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    for k in set(expected) & set(got):
        p = expected[k]
        hu = half_up(p)
        if got[k] == hu:
            continue
        # 斜辺など厳密でない境界の丸め誤差で、.5 のごく近傍（1e-9 以内）の場合だけ ±1 を許容して件数を記録する
        if abs(float(p) - math.floor(p) - 0.5) < 1e-9 and (p - math.floor(p)) != F(1, 2) and got[k] in (hu, hu - 1):
            tie_ok += 1
            continue
        bad_pct.append((k, float(p), got[k]))
    res.update(
        crs=layer.crs().authid(), wkb=QgsWkbTypes.displayString(layer.wkbType()), count=len(got), expected=len(expected),
        dup_in_layer=dup, missing=missing[:5], n_missing=len(missing), extra=extra[:5], n_extra=len(extra),
        bad_geometry_or_id=bad_geom, bad_file_zukaku=bad_zk, bad_system=bad_sys, bad_pct=bad_pct[:5], n_bad_pct=len(bad_pct),
        half_tie_near_noise=tie_ok,
        fields=[(fld.name(), fld.typeName(), fld.length()) for fld in layer.fields()],
    )
    res["ok"] = (res["valid"] and dup == 0 and not missing and not extra and bad_geom == 0 and bad_zk == 0
                 and bad_sys == 0 and not bad_pct and res["crs"] == "EPSG:6676")
    return res


def run_polygons(case, zones, thr=0.0, mask=None, labels=None, exclusive=False, subsample=1, name_field=True,
                 expect_parts=None, crs_in="EPSG:6676", geoms=None, check=True):
    """zones: list of list-of-ring(parts)。expect_parts は期待値の元になる parts（既定は zones）。"""
    path = os.path.join(OUT, "%s.gpkg" % case)
    if os.path.exists(path):
        raise RuntimeError("出力が既にあります: " + path)
    geoms = geoms or [QgsGeometry.fromWkt(wkt_poly([r for r in z])) if len(z) == 1 else QgsGeometry.fromWkt(
        "MULTIPOLYGON(" + ",".join(wkt_poly([r])[7:] for r in z) + ")") for z in zones]
    zl = make_layer("zones", geoms, crs_in, labels)
    params = {
        "SYSTEM": SYSTEM_ENUM, "DATUM": 0, "ZONES": zl, "NAME_FIELD": "name" if name_field else None,
        "THRESHOLD": thr, "SUBSAMPLE": subsample, "EXCLUSIVE": 0 if exclusive else 1, "SHAPE": 0, "OUTPUT_MODE": 0,
        "OUTPUT_GPKG": path, "LAYER_PREFIX": "t_", "LOAD": False,
    }
    if mask is not None:
        rings = [rect_ring(*mask["outer"])] + [rect_ring(*h)[::-1] for h in mask["holes"]]
        ml = make_layer("mask", [QgsGeometry.fromWkt(wkt_poly(rings))], "EPSG:6676", ["m"])
        params["MASK"] = ml
    fb = FB()
    t0 = time.time()
    out = {"case": case, "thr": thr, "exclusive": exclusive, "mask": mask is not None}
    try:
        processing.run(ALG + "create_mesh20m_from_polygons", params, feedback=fb)
    except Exception as exc:  # noqa: BLE001
        out["exception"] = repr(exc)[:300]
        out["log_tail"] = fb.log[-4:]
        return out
    out["seconds"] = round(time.time() - t0, 2)
    integrity, tables, geomtab = gpkg_tables(path)
    out["gpkg"] = {"integrity": integrity, "tables": tables, "geom_types": geomtab}
    out["warnings"] = [m for m in fb.log if m.startswith("W:")][:4]
    if check:
        parts_list = expect_parts or zones
        zres = {}
        for i, parts in enumerate(parts_list):
            label = labels[i] if labels else "z%d" % (i + 1)
            exp = expected_cells(parts, mask, thr)
            zres[label] = compare_layer(read_gpkg_layer(path, "t_" + label), exp)
        out["zones"] = zres
        out["ok"] = all(z.get("ok") for z in zres.values())
    return out


def summarize(r):
    """結果を短く整える。"""
    s = {k: r[k] for k in ("case", "thr", "exclusive", "mask", "ok", "seconds", "exception") if k in r}
    if "zones" in r:
        s["zones"] = {z: {k: v for k, v in d.items() if k in (
            "count", "expected", "n_missing", "n_extra", "n_bad_pct", "dup_in_layer", "bad_geometry_or_id",
            "bad_file_zukaku", "half_tie_near_noise", "ok", "crs", "missing", "extra", "bad_pct")} for z, d in r["zones"].items()}
    if "gpkg" in r:
        s["gpkg_integrity"] = r["gpkg"]["integrity"]
        s["tables"] = len(r["gpkg"]["tables"])
    return s


def functional():
    cases = []
    P = lambda *rings: [list(r) for r in rings]  # noqa: E731
    # A: 隣接 2 領域。共有辺が格子線上にない
    zA = [[rect_ring(1000, 2000, "1107.25", 2090)], [rect_ring("1107.25", 2000, 1200, 2090)]]
    cases.append(run_polygons("A_adjacent_off_grid", zA))
    # B: 辺・点で接するだけ（格子線上）。重複も欠落もないこと
    zB = [[rect_ring(1000, 3000, 1100, 3100)], [rect_ring(1100, 3000, 1200, 3100)], [rect_ring(1200, 3100, 1300, 3200)]]
    cases.append(run_polygons("B_touch_only", zB))
    # C: 被覆率しきい値 0/1/1.25/1.3/20/40/50/100
    zC = [[rect_ring(1000, 4000, 1110, 4108)], [rect_ring(1000, 4200, 1020, "4200.25")],
          [rect_ring(1100, 4300, "1100.0078125", "4300.0078125")]]
    for thr in (0, 1, 1.25, 1.3, 20, 40, 50, 100):
        cases.append(run_polygons("C_thr_%s" % str(thr).replace(".", "p"), zC, thr=thr, labels=["T", "S", "X"]))
    # D: 斜辺・凹形・マスク（穴あり）
    tri = [(F(1000), F(5000)), (F(1300), F(5000)), (F(1000), F(5200))]
    lshape = [(F(1400), F(5000)), (F(1600), F(5000)), (F(1600), F(5100)), (F(1500), F(5100)), (F(1500), F(5300)), (F(1400), F(5300))]
    zD = [[tri], [lshape]]
    maskD = {"outer": (F(1050), F("5020.5"), F(1550), F("5250.25")), "holes": [(F(1100), F(5050), F(1160), F("5090.5"))]}
    cases.append(run_polygons("D_shapes_nomask", zD, labels=["tri", "L"]))
    cases.append(run_polygons("D_shapes_mask", zD, mask=maskD, labels=["tri", "L"]))
    cases.append(run_polygons("D_shapes_mask_thr50", zD, thr=50, mask=maskD, labels=["tri", "L"]))
    # E: 旧方式（排他割当）。レイヤ間で重複しないこと（近似なので欠落の有無は参考）
    rE = run_polygons("E_exclusive_A", zA, exclusive=True, subsample=1, check=False)
    if "exception" not in rE:
        ids = []
        for lab in ("z1", "z2"):
            lyr = read_gpkg_layer(os.path.join(OUT, "E_exclusive_A.gpkg"), "t_" + lab)
            ids.append({f["JF20mID"] for f in lyr.getFeatures()})
        exact = [set(jf_id(SYSTEM, r, c) for (r, c) in expected_cells(p, None, 0)) for p in zA]
        rE["shared_ids_between_layers"] = len(ids[0] & ids[1])
        rE["union_missing_vs_exact"] = len((exact[0] | exact[1]) - (ids[0] | ids[1]))
        rE["counts"] = [len(ids[0]), len(ids[1])]
        rE["ok"] = rE["shared_ids_between_layers"] == 0
    cases.append(rE)
    # F: 日本語の領域名・重複名・NULL 名
    gF = [QgsGeometry.fromWkt(wkt_poly([rect_ring(1000, 6000, 1040, 6040)])) for _ in range(3)]
    rF = run_polygons("F_names", None, labels=["流域A", "流域A", None], geoms=gF, check=False)
    if "exception" not in rF:
        rF["tables"] = rF["gpkg"]["tables"]
    cases.append(rF)
    # G: 無効ジオメトリ（蝶ネクタイ）
    bow = QgsGeometry.fromWkt("POLYGON((1000 7000,1100 7100,1100 7000,1000 7100,1000 7000))")
    parts_bow = [[(F(1000), F(7000)), (F(1050), F(7050)), (F(1000), F(7100))],
                 [(F(1050), F(7050)), (F(1100), F(7100)), (F(1100), F(7000))]]
    cases.append(run_polygons("G_invalid_bowtie", None, labels=["bow"], geoms=[bow], expect_parts=[parts_bow]))
    # H: 他座標系の領域（EPSG:6668）。格子線上の矩形を往復変換した微小誤差の影響を見る
    tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:6676"), QgsCoordinateReferenceSystem("EPSG:6668"), QgsProject.instance())
    gH = QgsGeometry.fromWkt(wkt_poly([rect_ring(1000, 8000, 1100, 8100)]))
    gH.transform(tr)
    zH = [[rect_ring(1000, 8000, 1100, 8100)]]
    for thr in (0, 1):
        cases.append(run_polygons("H_epsg6668_aligned_thr%d" % thr, zH, thr=thr, labels=["h"], geoms=[QgsGeometry(gH)], crs_in="EPSG:6668"))
    # B1: 上書き防止・失敗系
    base = os.path.join(OUT, "A_adjacent_off_grid.gpkg")
    before = sha256(base)
    fb = FB()
    try:
        processing.run(ALG + "create_mesh20m_from_polygons", {
            "SYSTEM": SYSTEM_ENUM, "DATUM": 0, "ZONES": make_layer("z", [QgsGeometry.fromWkt(wkt_poly(zA[0]))]), "OUTPUT_MODE": 0,
            "OUTPUT_GPKG": base, "THRESHOLD": 0, "SUBSAMPLE": 1, "EXCLUSIVE": 1, "SHAPE": 0}, feedback=fb)
        overwrite = {"stopped": False}
    except Exception as exc:  # noqa: BLE001
        overwrite = {"stopped": True, "message": repr(exc)[:200], "file_unchanged": sha256(base) == before}
    fail_paths = {}
    blocker = os.path.join(OUT, "not_a_dir.txt")
    with open(blocker, "w") as fh:
        fh.write("x")
    for label, target in (("parent_is_file", os.path.join(blocker, "x.gpkg")), ("missing_drive", "Q:\\no_such\\x.gpkg")):
        if label == "missing_drive" and os.path.exists("Q:\\"):
            continue
        try:
            processing.run(ALG + "create_mesh20m_from_polygons", {
                "SYSTEM": SYSTEM_ENUM, "DATUM": 0, "ZONES": make_layer("z", [QgsGeometry.fromWkt(wkt_poly(zA[0]))]), "OUTPUT_MODE": 0,
                "OUTPUT_GPKG": target, "THRESHOLD": 0, "SUBSAMPLE": 1, "EXCLUSIVE": 1, "SHAPE": 0}, feedback=FB())
            fail_paths[label] = {"error_raised": False, "file_exists": os.path.exists(target)}
        except Exception as exc:  # noqa: BLE001
            fail_paths[label] = {"error_raised": True, "message": repr(exc)[:200], "file_exists": os.path.exists(target)}
    # LOAD（別 QgsProject へ。ユーザーのプロジェクトは触らない）
    ctx = QgsProcessingContext()
    proj = QgsProject()
    ctx.setProject(proj)
    loadpath = os.path.join(OUT, "LOAD_test.gpkg")
    try:
        processing.runAndLoadResults(ALG + "create_mesh20m_from_polygons", {
            "SYSTEM": SYSTEM_ENUM, "DATUM": 0, "ZONES": make_layer("z", [QgsGeometry.fromWkt(wkt_poly(g)) for g in zA]),
            "NAME_FIELD": "name", "OUTPUT_MODE": 0, "OUTPUT_GPKG": loadpath, "THRESHOLD": 0, "SUBSAMPLE": 1, "EXCLUSIVE": 1,
            "SHAPE": 0, "LOAD": True, "LAYER_PREFIX": "t_"}, feedback=FB(), context=ctx)
        loaded = {"layers": [(l.name(), l.isValid(), l.featureCount()) for l in proj.mapLayers().values()]}
    except Exception as exc:  # noqa: BLE001
        loaded = {"exception": repr(exc)[:300]}
    # ② 図郭ごと出力
    zdir = os.path.join(OUT, "zukaku_out")
    zparams = {"SYSTEM": SYSTEM_ENUM, "DATUM": 0, "SHAPE": 0, "EXTENT": "-140100,-139900,284900,285100 [EPSG:6676]",
               "YEAR": "2026", "OUTPUT_DIR": zdir, "WHOLE": False, "LOAD": False}
    fbz = FB()
    z2 = {}
    try:
        processing.run(ALG + "create_mesh20m_by_zukaku", zparams, feedback=fbz)
        files = sorted(os.listdir(zdir))
        z2["files"] = files
        exp_all = {}
        for r in range(int((TOP - 285100) / CELL), int((TOP - 284900) / CELL)):
            for c in range(int((-140100 - LEFT) / CELL), int((-139900 - LEFT) / CELL)):
                exp_all.setdefault(file_zukaku(SYSTEM, r, c), {})[(r, c)] = F(100)
        z2["expected_files"] = sorted("fr_mesh20m_%s_2026.gpkg" % k for k in exp_all)
        z2["per_file"] = {}
        for k, cells in exp_all.items():
            p = os.path.join(zdir, "fr_mesh20m_%s_2026.gpkg" % k)
            lyr = QgsVectorLayer("%s|layername=fr_mesh20m_%s_2026" % (p.replace("\\", "/"), k), "x", "ogr")
            got, bad = set(), 0
            if lyr.isValid():
                for f in lyr.getFeatures():
                    s, r, c = decode_id12(f["JF20mID"])
                    got.add((r, c))
                    if f["file_zukaku"] != k or file_zukaku(s, r, c) != k or abs(f.geometry().area() - 400) > 1e-6:
                        bad += 1
            z2["per_file"][k] = {"valid": lyr.isValid(), "count": len(got), "expected": len(cells), "same_cells": got == set(cells),
                                 "bad": bad, "crs": lyr.crs().authid(), "integrity": gpkg_tables(p)[0]}
        z2["ok"] = (z2["files"] == z2["expected_files"] and all(v["valid"] and v["same_cells"] and v["bad"] == 0 for v in z2["per_file"].values()))
        before = {f: sha256(os.path.join(zdir, f)) for f in z2["files"]}
        try:
            processing.run(ALG + "create_mesh20m_by_zukaku", zparams, feedback=FB())
            z2["overwrite_stopped"] = False
        except Exception as exc:  # noqa: BLE001
            z2["overwrite_stopped"] = True
            z2["overwrite_message"] = repr(exc)[:160]
        z2["unchanged_after_overwrite_attempt"] = before == {f: sha256(os.path.join(zdir, f)) for f in z2["files"]}
    except Exception as exc:  # noqa: BLE001
        z2["exception"] = repr(exc)[:300]
    # ① 範囲作成（一時レイヤ）
    o1 = {}
    try:
        res = processing.run(ALG + "create_mesh20m", {"SYSTEM": SYSTEM_ENUM, "DATUM": 0, "SHAPE": 0,
                             "EXTENT": "-140100,-139900,284900,285100 [EPSG:6676]", "OUTPUT": "memory:"}, feedback=FB())
        lyr = res["OUTPUT"]
        ids = [f["JF20mID"] for f in lyr.getFeatures()]
        exp = sorted(jf_id(SYSTEM, r, c) for r in range(int((TOP - 285100) / CELL), int((TOP - 284900) / CELL))
                     for c in range(int((-140100 - LEFT) / CELL), int((-139900 - LEFT) / CELL)))
        o1 = {"count": len(ids), "expected": len(exp), "same_ids": sorted(ids) == exp, "crs": lyr.crs().authid()}
    except Exception as exc:  # noqa: BLE001
        o1 = {"exception": repr(exc)[:300]}
    return {"cases": [summarize(c) for c in cases], "overwrite_3": overwrite, "write_failures": fail_paths,
            "load_option": loaded, "alg2_zukaku": z2, "alg1_extent": o1}


class MemSampler(threading.Thread):
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    def __init__(self):
        super().__init__(daemon=True)
        self.stop_flag = False
        self.max_ws = 0
        k32, ps = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(self.PMC), ctypes.c_ulong]
        self._h = k32.GetCurrentProcess()
        self._ps = ps

    def snapshot(self):
        pmc = self.PMC()
        pmc.cb = ctypes.sizeof(pmc)
        self._ps.GetProcessMemoryInfo(self._h, ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize, pmc.PeakWorkingSetSize

    def run(self):
        while not self.stop_flag:
            ws, _ = self.snapshot()
            self.max_ws = max(self.max_ws, ws)
            time.sleep(0.05)


def circle_ring(cx, cy, r, n=720):
    return [(F(cx + r * math.cos(2 * math.pi * i / n)), F(cy + r * math.sin(2 * math.pi * i / n))) for i in range(n)]


def perf(radius):
    cx, cy = 1010.3, 2010.7
    ring = circle_ring(cx, cy, radius)
    geom = QgsGeometry.fromWkt(wkt_poly([ring]))
    path = os.path.join(OUT, "PERF_r%d.gpkg" % radius)
    zl = make_layer("zone", [geom], labels=["circle"])
    params = {"SYSTEM": SYSTEM_ENUM, "DATUM": 0, "ZONES": zl, "NAME_FIELD": "name", "THRESHOLD": 0, "SUBSAMPLE": 1, "EXCLUSIVE": 1,
              "SHAPE": 0, "OUTPUT_MODE": 0, "OUTPUT_GPKG": path, "LAYER_PREFIX": "p_"}
    sampler = MemSampler()
    ws0, peak0 = sampler.snapshot()
    sampler.start()
    fb = FB()
    t0 = time.time()
    out = {"radius": radius, "poly_area_m2": float(geom.area())}
    try:
        processing.run(ALG + "create_mesh20m_from_polygons", params, feedback=fb)
        out["seconds"] = round(time.time() - t0, 1)
    except Exception as exc:  # noqa: BLE001
        out["exception"] = repr(exc)[:300]
        out["seconds"] = round(time.time() - t0, 1)
    sampler.stop_flag = True
    time.sleep(0.1)
    ws1, peak1 = sampler.snapshot()
    out.update(ws_before_mb=round(ws0 / 1e6), ws_after_mb=round(ws1 / 1e6), sampled_max_ws_mb=round(sampler.max_ws / 1e6),
               peak_before_mb=round(peak0 / 1e6), peak_after_mb=round(peak1 / 1e6))
    if os.path.exists(path):
        lyr = read_gpkg_layer(path, "p_circle")
        out["file_mb"] = round(os.path.getsize(path) / 1e6, 1)
        out["valid"] = lyr.isValid()
        out["count"] = lyr.featureCount()
        s = lyr.aggregate(QgsAggregateCalculator.Sum, "cover_pct")[0]
        out["sum_cover_area_m2"] = s * 4
        out["area_rel_error"] = (s * 4 - out["poly_area_m2"]) / out["poly_area_m2"]
        out["integrity"] = gpkg_tables(path)[0]
    out["log_tail"] = [m for m in fb.log if "メッシュ" in m or m.startswith("W:")][-3:]
    return out


def cancel_test():
    radius = 3000
    ring = circle_ring(1010.3, 2010.7, radius)
    geom = QgsGeometry.fromWkt(wkt_poly([ring]))
    path = os.path.join(OUT, "CANCEL_r%d.gpkg" % radius)
    params = {"SYSTEM": SYSTEM_ENUM, "DATUM": 0, "ZONES": make_layer("zone", [geom], labels=["circle"]), "NAME_FIELD": "name",
              "THRESHOLD": 0, "SUBSAMPLE": 1, "EXCLUSIVE": 1, "SHAPE": 0, "OUTPUT_MODE": 0, "OUTPUT_GPKG": path, "LAYER_PREFIX": "c_"}
    fb = FB()
    threading.Timer(4.0, fb.cancel).start()
    out = {}
    try:
        processing.run(ALG + "create_mesh20m_from_polygons", params, feedback=fb)
        out["exception"] = None
    except Exception as exc:  # noqa: BLE001
        out["exception"] = repr(exc)[:300]
    out["file_exists"] = os.path.exists(path)
    if out["file_exists"]:
        lyr = read_gpkg_layer(path, "c_circle")
        out["partial_count"] = lyr.featureCount() if lyr.isValid() else None
    out["log_tail"] = fb.log[-3:]
    return out


try:
    os.makedirs(OUT, exist_ok=True)
    env = {"qgis": QgsApplication.applicationFullName(), "python": sys.version.split()[0], "repo": REPO}
    if MODE == "functional":
        result = {"env": env, "result": functional()}
    elif MODE == "perf":
        result = {"env": env, "result": perf(PERF_RADIUS)}
    elif MODE == "cancel":
        result = {"env": env, "result": cancel_test()}
    else:
        result = {"error": "unknown MODE"}
except Exception:  # noqa: BLE001
    result = {"fatal": traceback.format_exc()[-1500:]}
finally:
    if _added_provider and MODE != "keep":
        registry.removeProvider(_provider)
