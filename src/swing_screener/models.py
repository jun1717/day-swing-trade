"""【契約】データモデル。

DESIGN.md §6 に対応する。実装者はフィールドを削除・改名しないこと（追加は可）。

設計の中心は Judgement。「上昇トレンドか」のような判定は bool を返すだけでは
UI で「なぜその判定なのか」を出せないため、すべての判定は Judgement を伴う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# --- 状態 -------------------------------------------------------------------

STATUS_ENTRY = "ENTRY_CANDIDATE"
STATUS_NEAR = "NEAR"
STATUS_RANGE = "RANGE"
STATUS_OUT = "OUT"

# 並び順（CODEX_HANDOFF §23）
STATUS_ORDER = {STATUS_ENTRY: 0, STATUS_NEAR: 1, STATUS_RANGE: 2, STATUS_OUT: 3}

PRIORITY_ORDER = {"A": 0, "B": 1, "C": 2}


# --- 価格データ -------------------------------------------------------------


@dataclass(frozen=True)
class OHLCVBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def body_pct(self) -> float:
        """始値に対する実体の変化率(%)。陰線は負。"""
        if self.open == 0:
            return 0.0
        return (self.close - self.open) / self.open * 100.0

    @property
    def range_pct(self) -> float:
        """安値に対する日中値幅(%)。"""
        if self.low == 0:
            return 0.0
        return (self.high - self.low) / self.low * 100.0


@dataclass(frozen=True)
class PriceSeries:
    code: str
    bars: tuple[OHLCVBar, ...]  # 日付昇順

    @property
    def latest(self) -> OHLCVBar | None:
        return self.bars[-1] if self.bars else None


# --- 銘柄マスター -----------------------------------------------------------


@dataclass(frozen=True)
class ThemeTag:
    """テーマごとの属性（CODEX_HANDOFF §6）。is_leader / watch_priority はテーマ単位。"""

    theme: str
    is_leader: bool
    watch_priority: str  # "A" | "B" | "C"


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    sector: str
    asset_type: str  # "stock" | "etf"
    enabled: bool = True
    themes: tuple[ThemeTag, ...] = ()

    @property
    def theme_names(self) -> tuple[str, ...]:
        return tuple(t.theme for t in self.themes)

    @property
    def display_priority(self) -> str:
        """全テーマ中の最上位 priority（A > B > C）。売買条件ではない。"""
        if not self.themes:
            return "C"
        return min(
            (t.watch_priority for t in self.themes),
            key=lambda p: PRIORITY_ORDER.get(p, 99),
        )

    @property
    def is_leader_any(self) -> bool:
        """いずれかのテーマで主力銘柄か。買いやすさの根拠には使わない。"""
        return any(t.is_leader for t in self.themes)


# --- 判定 -------------------------------------------------------------------


@dataclass(frozen=True)
class Judgement:
    """1つの判定 = 結果 + 人間向けの根拠。

    ok=None は「評価したが必須条件ではない」「判定不能」を表す。
    detail には必ず具体的な数値を入れる（例: "5,580 > 5,412 (+3.1%)"）。
    """

    key: str
    label: str
    ok: bool | None
    detail: str
    required: bool = False


@dataclass(frozen=True)
class SwingPoint:
    index: int
    date: date
    price: float


@dataclass(frozen=True)
class TrendResult:
    ma: float | None
    ma_deviation_pct: float | None
    ma_direction: str  # "up" | "flat" | "down"
    ma_slope_pct: float | None
    close_above_ma: bool
    higher_highs: bool | None
    higher_lows: bool | None
    swing_highs: tuple[SwingPoint, ...]
    swing_lows: tuple[SwingPoint, ...]
    is_uptrend: bool
    strength: float  # 並び順の第2キー。0〜1想定
    judgements: tuple[Judgement, ...] = ()


@dataclass(frozen=True)
class RangeCandidate:
    """3〜10営業日 window の評価結果。採用されなかった window も保持する。"""

    days: int
    start_index: int
    end_index: int
    start_date: date
    end_date: date

    upper: float
    upper_zone_low: float
    upper_zone_high: float
    lower: float
    lower_zone_low: float
    lower_zone_high: float

    width_pct: float
    lower_touch_count: int
    lower_touch_dates: tuple[date, ...]
    volatility_change: float
    volume_change: float

    quality: float
    accepted: bool
    reject_reasons: tuple[str, ...] = ()
    quality_breakdown: tuple[Judgement, ...] = ()


@dataclass(frozen=True)
class VolumeInfo:
    latest: int
    avg5: float | None
    avg20: float | None
    range_avg: float | None
    pre_range_avg: float | None
    range_vs_pre_ratio: float | None
    latest_vs_avg5_ratio: float | None
    state: str  # "contracting" | "neutral" | "expanding" | "unknown"
    state_label: str  # 日本語表示（例: "レンジ中減少傾向"）
    judgements: tuple[Judgement, ...] = ()


@dataclass(frozen=True)
class ReboundInfo:
    prev_high: float | None
    confirmed: bool  # close > prev_high（確定ルール）
    bullish_candle: bool
    long_lower_wick: bool
    volume_recovered: bool
    judgements: tuple[Judgement, ...] = ()


# --- スクリーニング結果 -----------------------------------------------------


@dataclass(frozen=True)
class ScreenResult:
    stock: Stock
    status: str
    as_of: date | None
    latest_close: float | None

    price_filter_ok: bool
    trend: TrendResult | None = None
    range_: RangeCandidate | None = None
    volume: VolumeInfo | None = None
    rebound: ReboundInfo | None = None

    distance_to_lower_pct: float | None = None
    touched_lower_recently: bool = False
    days_since_lower_touch: int | None = None
    stop_price: float | None = None

    out_reason: str = ""  # OUT の主因（1行）
    judgements: tuple[Judgement, ...] = ()  # 全判定を時系列順に連結したもの
    rejected_ranges: tuple[RangeCandidate, ...] = ()  # 不採用 window（改善分析用）

    @property
    def sort_key(self) -> tuple:
        """CODEX_HANDOFF §23 の並び順。ブラックボックスな総合スコアは使わない。"""
        return (
            STATUS_ORDER.get(self.status, 9),
            self.distance_to_lower_pct if self.distance_to_lower_pct is not None else 9999.0,
            -(self.trend.strength if self.trend else 0.0),
            -(self.range_.quality if self.range_ else 0.0),
            _volume_rank(self.volume),
            PRIORITY_ORDER.get(self.stock.display_priority, 9),
        )


def _volume_rank(vol: VolumeInfo | None) -> int:
    """出来高評価の並び順（contracting を上位に）。"""
    if vol is None:
        return 3
    return {"contracting": 0, "neutral": 1, "expanding": 2}.get(vol.state, 3)


@dataclass
class ScreeningRun:
    """1回のスクリーニング実行結果。output/screening_YYYY-MM-DD.json に対応。"""

    as_of: date | None
    generated_at: str
    results: list[ScreenResult] = field(default_factory=list)
    config_snapshot: dict = field(default_factory=dict)
    experimental_snapshot: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def by_status(self, status: str) -> list[ScreenResult]:
        return [r for r in self.results if r.status == status]

    def counts(self) -> dict[str, int]:
        return {
            s: len(self.by_status(s))
            for s in (STATUS_ENTRY, STATUS_NEAR, STATUS_RANGE, STATUS_OUT)
        }
