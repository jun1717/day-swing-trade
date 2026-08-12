"""閾値スイープと集計（RESEARCH_DESIGN §3, §8）。

`near.max_position_in_range` は `rules/status.py` の**最終分類段階にしか
影響しない**。トレンド判定・レンジ検出・出来高・反発確認の計算結果は
閾値に依存しない。したがってリプレイは「制限なし」で1回だけ実行し、
各閾値の status を事後導出する。

ただし**等価性は仮定しない**。`verify_derivation()` が実際に閾値を設定して
`screen_one` を呼んだ結果と突き合わせ、一致しない場合は導出を使わない。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from swing_screener.config import Params
from swing_screener.models import (
    STATUS_ENTRY,
    STATUS_NEAR,
    STATUS_RANGE,
    PriceSeries,
    Stock,
)
from swing_screener.research import classify
from swing_screener.research.config import (
    SWEEP_THRESHOLDS,
    ResearchConfig,
    threshold_label,
    with_position_threshold,
)
from swing_screener.research.events import EntryEvent
from swing_screener.research.replay import DayResult
from swing_screener.screener import screen_one


def derive_status(base_status: str, position: float | None, threshold: float | None) -> str:
    """制限なしで得た status から、閾値適用後の status を導出する。

    rules/status.py の位置ガードと同じ規則:
        position > threshold なら NEAR/ENTRY は RANGE に落ちる。
    """
    if threshold is None or position is None:
        return base_status
    if base_status in (STATUS_ENTRY, STATUS_NEAR) and position > threshold:
        return STATUS_RANGE
    return base_status


def verify_derivation(
    days: list[DayResult],
    stocks_by_code: dict[str, Stock],
    price_map: dict[str, PriceSeries],
    cfg: Params,
    exp: Params,
    *,
    thresholds: tuple[float | None, ...] = SWEEP_THRESHOLDS,
    sample_limit: int = 400,
) -> tuple[bool, list[str]]:
    """導出が実際の再計算と一致するかを検証する（等価性を仮定しない）。

    NEAR/ENTRY を中心に抽出して実際に screen_one を呼び直し、
    導出結果と突き合わせる。戻り値は (一致したか, 不一致の説明).
    """
    interesting = [
        d
        for d in days
        if d.result.status in (STATUS_ENTRY, STATUS_NEAR) and d.result.range_ is not None
    ]
    # 件数が多い場合は均等に間引く（偏らせない）
    if len(interesting) > sample_limit:
        step = len(interesting) / sample_limit
        interesting = [interesting[int(i * step)] for i in range(sample_limit)]

    mismatches: list[str] = []
    for day in interesting:
        stock = stocks_by_code.get(day.code)
        series = price_map.get(day.code)
        if stock is None or series is None:
            continue
        sliced = PriceSeries(code=series.code, bars=series.bars[: day.index + 1])
        rng = day.result.range_
        span = rng.upper - rng.lower
        position = (day.result.latest_close - rng.lower) / span if span > 0 else None

        for threshold in thresholds:
            actual = screen_one(
                stock, sliced, cfg, with_position_threshold(exp, threshold)
            ).status
            derived = derive_status(day.result.status, position, threshold)
            if actual != derived:
                mismatches.append(
                    f"{day.code} {day.date} threshold={threshold_label(threshold)}: "
                    f"導出={derived} 実際={actual} (position={position})"
                )
                if len(mismatches) >= 10:
                    return False, mismatches
    return not mismatches, mismatches


# --- 集計 -------------------------------------------------------------------


def _quantiles(values: list[float]) -> dict[str, float | None]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None,
                "mean": None}
    clean.sort()
    if len(clean) == 1:
        q1 = median = q3 = clean[0]
    else:
        median = statistics.median(clean)
        mid = len(clean) // 2
        lower = clean[:mid]
        upper = clean[mid + 1 :] if len(clean) % 2 else clean[mid:]
        q1 = statistics.median(lower) if lower else clean[0]
        q3 = statistics.median(upper) if upper else clean[-1]
    return {
        "n": len(clean),
        "min": clean[0],
        "q1": q1,
        "median": median,
        "q3": q3,
        "max": clean[-1],
        "mean": statistics.fmean(clean),
    }


@dataclass
class ThresholdResult:
    threshold: float | None
    label: str
    events: list[EntryEvent] = field(default_factory=list)

    @property
    def complete_events(self) -> list[EntryEvent]:
        return [e for e in self.events if e.forward_complete]

    def shape_counts(self) -> dict[str, int]:
        counts = {s: 0 for s in classify.SHAPE_ORDER}
        for e in self.events:
            counts[e.shape] = counts.get(e.shape, 0) + 1
        return counts

    def outcome_counts(self) -> dict[str, int]:
        counts = {o: 0 for o in classify.OUTCOME_ORDER}
        for e in self.events:
            counts[e.outcome] = counts.get(e.outcome, 0) + 1
        return counts

    def cross_counts(self) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for e in self.events:
            out[(e.shape, e.outcome)] = out.get((e.shape, e.outcome), 0) + 1
        return out

    def stop_rate(self) -> float | None:
        """損切り到達率。forward が揃ったイベントのみを母数にする。"""
        complete = self.complete_events
        if not complete:
            return None
        return sum(1 for e in complete if e.fwd10_hit_stop) / len(complete) * 100.0

    def distribution(self, attr: str) -> dict[str, float | None]:
        return _quantiles([getattr(e, attr) for e in self.events])

    def complete_distribution(self, attr: str) -> dict[str, float | None]:
        return _quantiles([getattr(e, attr) for e in self.complete_events])


@dataclass
class SweepResult:
    start: date | None
    end: date | None
    months: int
    warmup: int
    stock_count: int
    trading_days: int
    all_events: list[EntryEvent]
    by_threshold: dict[str, ThresholdResult]
    thresholds: tuple[float | None, ...]
    derivation_verified: bool
    derivation_mismatches: list[str]
    experimental_snapshot: dict[str, Any] = field(default_factory=dict)
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    def ordered(self) -> list[ThresholdResult]:
        return [self.by_threshold[threshold_label(t)] for t in self.thresholds]

    def added_by_loosening(self) -> list[tuple[str, str, list[EntryEvent]]]:
        """1つ厳しい閾値との差分＝「緩めると追加されるイベント」。

        この検証の本題。
        """
        out: list[tuple[str, str, list[EntryEvent]]] = []
        results = self.ordered()
        for prev, cur in zip(results, results[1:]):
            prev_keys = {(e.code, e.date) for e in prev.events}
            added = [e for e in cur.events if (e.code, e.date) not in prev_keys]
            out.append((prev.label, cur.label, added))
        return out


def build_sweep(
    days: list[DayResult],
    stocks_by_code: dict[str, Stock],
    price_map: dict[str, PriceSeries],
    research: ResearchConfig,
    *,
    thresholds: tuple[float | None, ...] = SWEEP_THRESHOLDS,
    build_event_fn=None,
) -> dict[str, ThresholdResult]:
    """制限なしのリプレイ結果から、各閾値の ENTRY イベントを組み立てる。"""
    from swing_screener.research.events import build_event as _build

    build_event_fn = build_event_fn or _build

    # ENTRY になりうる日だけを対象にする（制限なしで ENTRY だったもの）
    entry_days = [d for d in days if d.result.status == STATUS_ENTRY]

    # イベントは閾値に依存しないので1度だけ作る
    cache: dict[tuple[str, date], EntryEvent] = {}
    for day in entry_days:
        stock = stocks_by_code.get(day.code)
        series = price_map.get(day.code)
        if stock is None or series is None:
            continue
        ev = build_event_fn(day, stock, series, research, research.shape)
        if ev is not None:
            cache[(day.code, day.date)] = ev

    out: dict[str, ThresholdResult] = {}
    for threshold in thresholds:
        label = threshold_label(threshold)
        selected = [
            ev
            for (code, d), ev in cache.items()
            if derive_status(STATUS_ENTRY, ev.position_in_range, threshold)
            == STATUS_ENTRY
        ]
        selected.sort(key=lambda e: (e.date, e.code))
        out[label] = ThresholdResult(threshold=threshold, label=label, events=selected)
    return out
