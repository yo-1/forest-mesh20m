"""平面直角座標系の系番号と EPSG コードの対応（QGIS 非依存）。

EPSG コードは次の規則による（作成者の知識にもとづく。実機で isValid() と description() を
確認すること。README の「EPSG の確認手順」を参照）:
    JGD2011 : 6668 + 系番号  (系I = 6669 ... 系XIX = 6687)
    JGD2000 : 2442 + 系番号  (系I = 2443 ... 系XIX = 2461)
JGD2024 は EPSG コードを確認できていないため未対応。
"""
from __future__ import annotations

from typing import List

from .zukaku import check_system

DATUM_JGD2011 = "JGD2011"
DATUM_JGD2000 = "JGD2000"
DATUMS = (DATUM_JGD2011, DATUM_JGD2000)

_EPSG_BASE = {DATUM_JGD2011: 6668, DATUM_JGD2000: 2442}

_ROMAN = (
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX",
)

# 系ごとの適用区域（国土地理院の区分。UI の選択肢ラベル用）
_AREAS = (
    "長崎, 鹿児島県の一部",
    "福岡, 佐賀, 熊本, 大分, 宮崎, 鹿児島県の一部",
    "山口, 島根, 広島",
    "香川, 愛媛, 徳島, 高知",
    "兵庫, 鳥取, 岡山",
    "京都, 大阪, 福井, 滋賀, 三重, 奈良, 和歌山",
    "石川, 富山, 岐阜, 愛知",
    "新潟, 長野, 山梨, 静岡",
    "東京都(小笠原村を除く), 福島, 栃木, 茨城, 埼玉, 千葉, 群馬, 神奈川",
    "青森, 秋田, 山形, 岩手, 宮城",
    "北海道 西部",
    "北海道 中央部",
    "北海道 東部",
    "東京都の一部(聟島列島, 父島列島, 母島列島, 硫黄島)",
    "沖縄県 中央部",
    "沖縄県 西部",
    "沖縄県 東部",
    "東京都の一部(沖ノ鳥島)",
    "東京都の一部(南鳥島)",
)


def epsg_code(system: int, datum: str = DATUM_JGD2011) -> int:
    system = check_system(system)
    if datum not in _EPSG_BASE:
        raise ValueError("未対応の測地系です: %r（対応: %s）" % (datum, ", ".join(DATUMS)))
    return _EPSG_BASE[datum] + system


def epsg_authid(system: int, datum: str = DATUM_JGD2011) -> str:
    return "EPSG:%d" % epsg_code(system, datum)


def system_labels() -> List[str]:
    """UI 用の選択肢ラベル。index 0 が系 1。例: 'IX (9) 系: 東京都(...)'"""
    return ["%s (%d) 系: %s" % (_ROMAN[i], i + 1, _AREAS[i]) for i in range(19)]
