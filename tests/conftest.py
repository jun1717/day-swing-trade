"""テスト共通のヘルパー（DESIGN.md §12）。

株価APIには依存させず、合成OHLCVで「ルールの意図」を固定する。
SeriesBuilder で「上昇トレンド + 短期レンジ」を組み立て、各テストは
パラメータを振って使い回す。
"""

from __future__ import annotations

import copy
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from swing_screener.config import Params, load_config, load_experimental  # noqa: E402
from swing_screener.models import (  # noqa: E402
    OHLCVBar,
    PriceSeries,
    Stock,
    ThemeTag,
)

START_DATE = date(2025, 4, 1)  # 火曜日


# --- 設定 -------------------------------------------------------------------


@pytest.fixture
def cfg() -> Params:
    return load_config(ROOT / "config.yaml")


@pytest.fixture
def exp() -> Params:
    return load_experimental(ROOT / "experimental.yaml")


def override(params: Params, updates: dict[str, Any]) -> Params:
    """experimental / config をテスト内で部分的に差し替える。

    元の Params は変更しない。ドット区切りのキーで指定する。
        override(exp, {"near.lookback_days": 0})
    """
    data = copy.deepcopy(params.as_dict())
    for dotted, value in updates.items():
        parts = dotted.split(".")
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return Params(data)


# --- 銘柄 -------------------------------------------------------------------


def make_stock(
    code: str = "1234",
    name: str = "テスト銘柄",
    sector: str = "電気機器",
    asset_type: str = "stock",
    enabled: bool = True,
    theme: str = "テストテーマ",
    priority: str = "A",
    is_leader: bool = True,
) -> Stock:
    return Stock(
        code=code,
        name=name,
        sector=sector,
        asset_type=asset_type,
        enabled=enabled,
        themes=(ThemeTag(theme=theme, is_leader=is_leader, watch_priority=priority),),
    )


# --- 合成OHLCV --------------------------------------------------------------


def _next_business_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:  # 土日を飛ばす
        nxt += timedelta(days=1)
    return nxt


class SeriesBuilder:
    """合成日足を組み立てる。日付は営業日を自動採番する。

    使い方::

        series = (
            SeriesBuilder()
            .uptrend_to(days=70, end_close=5000, step=15)
            .flat_range(days=6, lower=4900, upper=5100, touch_days=(1, 4))
            .build()
        )
    """

    def __init__(
        self,
        code: str = "1234",
        start_date: date = START_DATE,
        default_volume: int = 100_000,
    ) -> None:
        self.code = code
        self._date = start_date
        self.default_volume = default_volume
        self.bars: list[OHLCVBar] = []

    # -- 低レベル --------------------------------------------------------
    def add(
        self,
        close: float,
        *,
        open: float | None = None,
        high: float | None = None,
        low: float | None = None,
        volume: int | None = None,
    ) -> "SeriesBuilder":
        """1本追加する。省略した値は自然な形になるよう補完する。"""
        prev_close = self.bars[-1].close if self.bars else close
        o = open if open is not None else prev_close
        if open is None:
            # 始値を省略した場合は前日終値を使うが、指定された高値/安値の
            # 外に出るとローソク足の形が壊れるので内側へ収める
            hi_ref = high if high is not None else max(o, close) * 1.004
            lo_ref = low if low is not None else min(o, close) * 0.996
            o = min(max(o, lo_ref), hi_ref)
        h = high if high is not None else max(o, close) * 1.004
        low_ = low if low is not None else min(o, close) * 0.996
        # 整合性を保証（高値は必ず最大、安値は必ず最小）
        h = max(h, o, close)
        low_ = min(low_, o, close)
        bar = OHLCVBar(
            date=self._date,
            open=round(o, 2),
            high=round(h, 2),
            low=round(low_, 2),
            close=round(close, 2),
            volume=int(volume if volume is not None else self.default_volume),
        )
        self.bars.append(bar)
        self._date = _next_business_day(self._date)
        return self

    def add_rows(self, rows: Iterable[Sequence[float]]) -> "SeriesBuilder":
        """(open, high, low, close, volume) の並びをそのまま追加する。"""
        for row in rows:
            o, h, l, c = row[0], row[1], row[2], row[3]
            v = int(row[4]) if len(row) > 4 else self.default_volume
            self.add(c, open=o, high=h, low=l, volume=v)
        return self

    # -- 高レベル --------------------------------------------------------
    def uptrend_to(
        self,
        days: int,
        end_close: float,
        step: float = 15.0,
        *,
        volume: int | None = None,
    ) -> "SeriesBuilder":
        """end_close で終わる直線的な上昇トレンドを days 本作る。

        MA25 は必ず上向きになり、終値は MA25 より上になる。
        """
        start_close = end_close - step * (days - 1)
        for i in range(days):
            close = start_close + step * i
            self.add(close, volume=volume)
        return self

    def downtrend_to(
        self, days: int, end_close: float, step: float = 15.0, *, volume: int | None = None
    ) -> "SeriesBuilder":
        """end_close で終わる下降トレンド（OUT ケース用）。"""
        start_close = end_close + step * (days - 1)
        for i in range(days):
            self.add(start_close - step * i, volume=volume)
        return self

    def flat_range(
        self,
        days: int,
        lower: float,
        upper: float,
        *,
        touch_days: Sequence[int] = (),
        upper_day: int | None = 0,
        volume: int | None = None,
    ) -> "SeriesBuilder":
        """[lower, upper] の短期レンジを days 本作る。

        touch_days に指定した位置の足は安値がちょうど lower に触れる。
        upper_day の足は高値がちょうど upper になる（既定は先頭）。
        """
        width = upper - lower
        touch = set(touch_days)
        for i in range(days):
            if i in touch:
                low = lower
                high = lower + width * 0.55
                close = lower + width * 0.25
            else:
                # 下限zone(既定±0.7%)より確実に上に置き、反応と数えられないようにする
                low = max(lower + width * 0.25, lower * 1.015)
                high = upper - width * 0.15
                close = (low + high) / 2
            if upper_day is not None and i == upper_day:
                high = upper
            self.add(close, high=high, low=low, volume=volume)
        return self

    def build(self) -> PriceSeries:
        return PriceSeries(code=self.code, bars=tuple(self.bars))


def uptrend_with_range(
    *,
    trend_days: int = 70,
    trend_end: float = 5000.0,
    trend_step: float = 15.0,
    range_days: int = 6,
    range_lower: float = 4950.0,
    range_upper: float = 5150.0,
    touch_days: Sequence[int] = (1, 4),
    upper_day: int | None = 0,
    trend_volume: int = 120_000,
    range_volume: int = 70_000,
    code: str = "1234",
) -> SeriesBuilder:
    """「上昇トレンド + 短期レンジ」の標準形。builder を返すので追記できる。"""
    builder = SeriesBuilder(code=code, default_volume=trend_volume)
    builder.uptrend_to(trend_days, trend_end, trend_step, volume=trend_volume)
    builder.flat_range(
        range_days,
        range_lower,
        range_upper,
        touch_days=touch_days,
        upper_day=upper_day,
        volume=range_volume,
    )
    return builder


@pytest.fixture
def ideal_series() -> PriceSeries:
    """典型的な「上昇トレンド + 良質な短期レンジ」。"""
    return uptrend_with_range().build()
