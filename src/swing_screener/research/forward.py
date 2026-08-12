"""シグナル後の値動き（RESEARCH_DESIGN §6）。

**これは収益バックテストではない。** シグナル後に価格がどう動いたかを
観察するイベントスタディである。

基準価格を2つ持つ:

1. `from_close`  — シグナル日終値。**実際には約定できない。**
   日足確定後に判定するため、その終値で買うことは不可能。比較用の基準にすぎない。
2. `from_next_open` — シグナル翌営業日の始値。実運用との乖離を見る参考値。
   **「翌日始値で必ず買う」という売買ルールにはしない。**

forward 計算だけが bars[index+1:] を参照してよい（観察であって判定ではない）。
シグナル日の足そのものは forward に含めない（二重計上の防止）。
"""

from __future__ import annotations

from dataclasses import dataclass

from swing_screener.models import OHLCVBar


@dataclass(frozen=True)
class HorizonStats:
    """1つの基準価格・1つの期間についての観察結果。"""

    horizon: int
    base_price: float
    bars_used: int
    complete: bool
    max_gain_pct: float | None
    max_loss_pct: float | None
    days_to_max_gain: int | None
    reached_range_upper: bool
    broke_range_upper: bool
    hit_stop: bool
    days_to_stop: int | None


@dataclass(frozen=True)
class ForwardObservation:
    horizons: tuple[int, ...]
    from_close: dict[int, HorizonStats]
    from_next_open: dict[int, HorizonStats]
    next_open: float | None
    gap_pct: float | None
    bars_available: int
    complete: bool  # 最長 horizon 分のデータが揃っているか


def _future_bars(bars: tuple[OHLCVBar, ...], signal_index: int) -> tuple[OHLCVBar, ...]:
    """シグナル日の**翌足以降**。シグナル日の足は含めない（二重計上の防止）。"""
    return bars[signal_index + 1 :]


def _stats(
    future: tuple[OHLCVBar, ...],
    *,
    horizon: int,
    base_price: float,
    range_upper: float,
    stop_price: float,
    day_offset: int,
) -> HorizonStats:
    """base_price を基準に horizon 営業日分を観察する。

    day_offset は「基準日から数えて future[0] が何日目か」。
    - 終値基準: シグナル日=0日目なので future[0] は 1日目 → offset=1
    - 翌日始値基準: 翌日=0日目（その日の始値で入る）なので future[0] は 0日目 → offset=0
    """
    window = future[:horizon]
    if not window or base_price <= 0:
        return HorizonStats(
            horizon=horizon,
            base_price=base_price,
            bars_used=0,
            complete=False,
            max_gain_pct=None,
            max_loss_pct=None,
            days_to_max_gain=None,
            reached_range_upper=False,
            broke_range_upper=False,
            hit_stop=False,
            days_to_stop=None,
        )

    highest = max(b.high for b in window)
    lowest = min(b.low for b in window)
    max_gain = (highest - base_price) / base_price * 100.0
    max_loss = (lowest - base_price) / base_price * 100.0

    days_to_max_gain = next(
        (i + day_offset for i, b in enumerate(window) if b.high == highest), None
    )

    hit_stop = any(b.low <= stop_price for b in window)
    days_to_stop = next(
        (i + day_offset for i, b in enumerate(window) if b.low <= stop_price), None
    )

    return HorizonStats(
        horizon=horizon,
        base_price=base_price,
        bars_used=len(window),
        complete=len(window) >= horizon,
        max_gain_pct=max_gain,
        max_loss_pct=max_loss,
        days_to_max_gain=days_to_max_gain,
        reached_range_upper=highest >= range_upper,
        broke_range_upper=any(b.close > range_upper for b in window),
        hit_stop=hit_stop,
        days_to_stop=days_to_stop,
    )


def observe(
    bars: tuple[OHLCVBar, ...],
    signal_index: int,
    *,
    signal_close: float,
    range_upper: float,
    stop_price: float,
    horizons: tuple[int, ...] = (5, 10),
) -> ForwardObservation:
    """シグナル後の値動きを観察する。

    bars は全期間の系列。signal_index はシグナル日の位置。
    参照するのは bars[signal_index+1:] だけ。
    """
    future = _future_bars(bars, signal_index)
    next_open = future[0].open if future else None
    gap_pct = (
        (next_open - signal_close) / signal_close * 100.0
        if next_open is not None and signal_close > 0
        else None
    )

    from_close: dict[int, HorizonStats] = {}
    from_next_open: dict[int, HorizonStats] = {}
    for h in horizons:
        from_close[h] = _stats(
            future,
            horizon=h,
            base_price=signal_close,
            range_upper=range_upper,
            stop_price=stop_price,
            day_offset=1,
        )
        if next_open is not None:
            # 翌日始値で入った場合、その日の値動きから観察する
            from_next_open[h] = _stats(
                future,
                horizon=h,
                base_price=next_open,
                range_upper=range_upper,
                stop_price=stop_price,
                day_offset=0,
            )

    max_h = max(horizons)
    return ForwardObservation(
        horizons=tuple(horizons),
        from_close=from_close,
        from_next_open=from_next_open,
        next_open=next_open,
        gap_pct=gap_pct,
        bars_available=len(future),
        complete=len(future) >= max_h,
    )


def closed_below_range_lower(
    bars: tuple[OHLCVBar, ...],
    signal_index: int,
    *,
    range_lower: float,
    horizon: int,
) -> bool:
    """horizon 営業日以内の終値がレンジ下限を割ったか（レンジ崩壊の判定用）。"""
    window = _future_bars(bars, signal_index)[:horizon]
    return any(b.close < range_lower for b in window)
