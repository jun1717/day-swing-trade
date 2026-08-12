"""過去日次リプレイ。look-ahead bias 対策の中核（RESEARCH_DESIGN §2）。

各営業日 D について `bars[0..i]`（D を含み、D より後を一切含まない）だけを
`screen_one()` に渡す。`screen_one` は「系列の最終足＝当日」として動作するため、
**スライスするだけで構造的に未来を遮断できる。**

    for i in range(warmup, len(bars)):
        sliced = PriceSeries(code=code, bars=bars[: i + 1])
        result = screen_one(stock, sliced, cfg, exp)

禁止事項:
    - MA25・swing・レンジ判定・ENTRY判定に bars[i+1:] を使わない
    - 「翌営業日始値」は判定に使わず、記録のみに使う（forward.py の責務）

forward の計算だけが bars[i+1:] を参照してよい。それは観察であって判定ではない。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date

from swing_screener.config import Params
from swing_screener.models import PriceSeries, ScreenResult, Stock
from swing_screener.research.config import DEFAULT, ResearchConfig, required_warmup
from swing_screener.screener import screen_one


@dataclass(frozen=True)
class DayResult:
    """ある営業日 D 時点のスクリーニング結果。

    index は元系列（全期間）での位置。forward 計算がこの index を使って
    bars[index+1:] を参照する。
    """

    code: str
    index: int
    date: date
    result: ScreenResult


def replay_stock(
    stock: Stock,
    series: PriceSeries,
    cfg: Params,
    exp: Params,
    *,
    start: date | None = None,
    end: date | None = None,
    warmup: int | None = None,
) -> Iterator[DayResult]:
    """1銘柄を日次でリプレイする。

    start/end は判定日の範囲（両端含む）。warmup 未満の日は判定しない
    （データ不足を ENTRY にしないため）。
    """
    bars = series.bars
    if warmup is None:
        warmup = required_warmup(cfg, exp)

    for i in range(len(bars)):
        if i + 1 < warmup:
            continue
        bar_date = bars[i].date
        if start is not None and bar_date < start:
            continue
        if end is not None and bar_date > end:
            break

        # ここが look-ahead 防止の要。bars[: i + 1] より後は決して渡さない。
        sliced = PriceSeries(code=series.code, bars=bars[: i + 1])
        result = screen_one(stock, sliced, cfg, exp)
        yield DayResult(code=series.code, index=i, date=bar_date, result=result)


def replay_all(
    stocks: list[Stock],
    price_map: dict[str, PriceSeries],
    cfg: Params,
    exp: Params,
    *,
    start: date | None = None,
    end: date | None = None,
    research: ResearchConfig = DEFAULT,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[DayResult]:
    """全銘柄をリプレイする。

    exp は呼び出し側が `with_position_threshold(exp, None)` で
    「制限なし」にしたものを渡す想定（RESEARCH_DESIGN §3）。
    """
    warmup = required_warmup(cfg, exp, research)
    out: list[DayResult] = []
    targets = [s for s in stocks if s.enabled and s.code in price_map]
    total = len(targets)
    for n, stock in enumerate(targets, start=1):
        if progress is not None:
            progress(n, total, stock.code)
        series = price_map[stock.code]
        out.extend(
            replay_stock(
                stock, series, cfg, exp, start=start, end=end, warmup=warmup
            )
        )
    return out


def resolve_window(
    price_map: dict[str, PriceSeries],
    cfg: Params,
    exp: Params,
    *,
    months: int,
    research: ResearchConfig = DEFAULT,
) -> tuple[date | None, date | None, int]:
    """検証期間を決める。

    キャッシュにある最終日から遡って months ヶ月。warmup を確保できない場合は
    確保できる範囲まで自動的に短縮し、実際に使った期間を返す。
    """
    all_dates: set[date] = set()
    for series in price_map.values():
        all_dates.update(b.date for b in series.bars)
    if not all_dates:
        return None, None, 0

    ordered = sorted(all_dates)
    warmup = required_warmup(cfg, exp, research)
    span = months * research.business_days_per_month

    # warmup 本を確保できる最初の位置
    earliest_index = max(0, warmup - 1)
    start_index = max(earliest_index, len(ordered) - span)
    if start_index >= len(ordered):
        return None, None, warmup
    return ordered[start_index], ordered[-1], warmup
