"""検証用パラメータ。

本番の experimental.yaml とは**独立**させる。ここの値を変えても
本番スクリーニングには一切影響しない（逆も同様）。

分類のしきい値は「観察のためのラベル付け」であって売買ルールではない。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from swing_screener.config import Params

# --- 比較する max_position_in_range -----------------------------------------
# None = 制限なし。RESEARCH_DESIGN §5
SWEEP_THRESHOLDS: tuple[float | None, ...] = (0.50, 0.60, 0.65, 0.70, 0.80, None)

PRODUCTION_THRESHOLD = 0.65  # 現行の本番値（比較の基準点として表示に使う）


def threshold_label(threshold: float | None) -> str:
    return "制限なし" if threshold is None else f"{threshold:.2f}"


def threshold_tag(threshold: float | None) -> str:
    """ファイル名用のタグ。"""
    return "none" if threshold is None else f"{threshold:.2f}".replace(".", "")


# --- 形状分類のしきい値（RESEARCH_DESIGN §7）--------------------------------


@dataclass(frozen=True)
class ShapeParams:
    """形状ラベルの区切り。売買ルールではなく観察用のラベル。"""

    ideal_max_position: float = 0.65
    ideal_max_days_from_touch: int = 3
    late_max_position: float = 0.80
    near_upper_max_position: float = 0.95


@dataclass(frozen=True)
class ResearchConfig:
    months: int = 6
    horizons: tuple[int, ...] = (5, 10)
    # warmup: MA25 / swing.lookback_bars / range.max_days を賄える本数。
    # 実際には experimental.yaml の値から算出し、これは下限として使う。
    min_warmup_bars: int = 70
    shape: ShapeParams = field(default_factory=ShapeParams)
    # 代表チャートを各カテゴリ何件出すか
    charts_per_category: int = 4
    business_days_per_month: int = 21

    @property
    def max_horizon(self) -> int:
        return max(self.horizons)


DEFAULT = ResearchConfig()


def required_warmup(cfg: Params, exp: Params, research: ResearchConfig = DEFAULT) -> int:
    """判定に必要な最小本数。これ未満の日はリプレイ対象にしない。"""
    candidates = [
        int(cfg.ma.period),
        int(cfg.range.max_days),
        int(exp.get("swing.lookback_bars", 60)),
        int(cfg.data.min_bars),
        research.min_warmup_bars,
    ]
    return max(candidates)


def with_position_threshold(exp: Params, threshold: float | None) -> Params:
    """experimental を**メモリ上でのみ**差し替える。ファイルは書き換えない。"""
    data = copy.deepcopy(exp.as_dict())
    node: dict[str, Any] = data.setdefault("near", {})
    node["max_position_in_range"] = threshold
    return Params(data)
