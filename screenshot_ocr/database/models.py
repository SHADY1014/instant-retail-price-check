"""店铺/城市智能匹配数据库 — 数据模型（dataclass）"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Shop:
    shop_id: int
    canonical_name: str
    city: str = ""
    province: str = ""
    status: str = "active"
    confidence: float = 0.0
    source: str = "ocr"
    use_count: int = 0
    correct_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    last_confirmed_at: Optional[str] = None


@dataclass
class Alias:
    alias_id: int
    shop_id: int
    alias: str
    normalized_alias: str
    source: str = "ocr"
    confidence: float = 0.0
    use_count: int = 0
    correct_count: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CityMatch:
    match_id: int
    shop_id: int
    city: str
    province: str = ""
    status: str = "confirmed"
    source: str = "ocr"
    confidence: float = 0.0
    use_count: int = 0
    correct_count: int = 0
    last_confirmed_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Correction:
    correction_id: int
    ocr_shop_name: str
    corrected_shop_name: str
    city: str = ""
    province: str = ""
    shop_id: Optional[int] = None
    batch_id: Optional[int] = None
    operator: str = ""
    created_at: str = ""


@dataclass
class ImportBatch:
    batch_id: int
    filename: str
    file_hash: str
    import_time: str = ""
    total_rows: int = 0
    new_shops: int = 0
    new_aliases: int = 0
    updated_shops: int = 0
    updated_cities: int = 0
    conflicts: int = 0
    ignored_rows: int = 0
    operator: str = ""
    status: str = "ok"


@dataclass
class MatchResult:
    """一次店铺名匹配的结果（L1-L7）"""

    level: int                      # 1-7
    shop_id: Optional[int] = None
    canonical_name: str = ""        # 命中时的标准店铺名
    city: str = ""                  # 命中城市（confirmed 优先）
    province: str = ""
    is_conflict: bool = False       # 该店城市有冲突，需要人工裁决
    candidates: list = field(default_factory=list)  # L5 模糊候选 [(shop_id, canonical, score)]
    raw_name: str = ""              # 输入的 OCR 店名
