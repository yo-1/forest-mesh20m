"""GeoPackage のレイヤ名を領域ごとに作る（QGIS 非依存）。"""
from __future__ import annotations

import re
from typing import Iterable, List

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\'`\s\x00-\x1f]+')
MAX_NAME_LENGTH = 60


def sanitize_layer_name(text: str) -> str:
    """テーブル名として安全な文字列にする。日本語はそのまま残し、記号・空白は _ にする。"""
    name = _FORBIDDEN.sub("_", (text or "").strip()).strip("_")
    return name[:MAX_NAME_LENGTH]


def make_layer_names(prefix: str, labels: Iterable[object]) -> List[str]:
    """領域ごとの重複のないレイヤ名を返す。ラベルが空なら zone001 形式の連番にする。

    重複したものは _2, _3 を付ける（大文字小文字違いも SQLite では同名になるため区別しない）。
    """
    names: List[str] = []
    used = set()
    for index, label in enumerate(labels, start=1):
        text = "" if label is None else str(label)
        base = sanitize_layer_name(prefix + (sanitize_layer_name(text) or "zone%03d" % index))
        name, suffix = base, 2
        while name.lower() in used:
            name = "%s_%d" % (base, suffix)
            suffix += 1
        used.add(name.lower())
        names.append(name)
    return names
