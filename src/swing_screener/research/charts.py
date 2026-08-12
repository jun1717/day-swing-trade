"""検証用の注釈付きチャート（RESEARCH_DESIGN §10）。

本番の charting.py とは別実装にする（表示要素が異なるため）。本番側は編集しない。

このチャートは**シグナル日より後の値動きも表示する**。検証用であり、
判定には未来を使っていない（look-ahead 防止は replay.py が担保）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

from swing_screener.models import OHLCVBar, PriceSeries  # noqa: E402
from swing_screener.research.classify import (  # noqa: E402
    OUTCOME_LABELS_JA,
    SHAPE_LABELS_JA,
)

_JP_CANDIDATES = ("Hiragino Sans", "Hiragino Maru Gothic Pro", "Yu Gothic",
                  "Noto Sans CJK JP", "IPAexGothic", "AppleGothic")


def _find_jp_font() -> str | None:
    try:
        available = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:  # noqa: BLE001
        return None
    for name in _JP_CANDIDATES:
        if name in available:
            return name
    return None


_JP_FONT = _find_jp_font()
if _JP_FONT:
    plt.rcParams["font.family"] = _JP_FONT
plt.rcParams["axes.unicode_minus"] = False


def _t(ja: str, en: str) -> str:
    return ja if _JP_FONT else en


@dataclass(frozen=True)
class ChartCategory:
    key: str
    title_ja: str
    title_en: str


CATEGORIES = (
    ChartCategory("worked", "うまく機能した例", "Worked"),
    ChartCategory("stopped", "損切りになった例", "Stopped out"),
    ChartCategory("late", "ENTRYが遅かった例", "Late entry"),
    ChartCategory("excluded_by_guard", "0.65で除外され0.80で拾われる例", "Excluded by 0.65"),
    ChartCategory("upper_zone", "制限なしで拾われる上限付近の例", "Near range top"),
)


def _ma(bars: list[OHLCVBar], period: int) -> list[float | None]:
    out: list[float | None] = []
    closes = [b.close for b in bars]
    for i in range(len(closes)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(closes[i + 1 - period : i + 1]) / period)
    return out


def render_event_chart(
    event,
    series: PriceSeries,
    cfg,
    out_path: Path,
    *,
    before: int = 45,
    after: int = 15,
) -> Path:
    """1件のENTRYイベントを、シグナル日の前後を含めて描く。"""
    bars = list(series.bars)
    idx = int(event["signal_index"]) if isinstance(event, dict) else event.signal_index
    get = (lambda k: event[k]) if isinstance(event, dict) else (lambda k: getattr(event, k))

    def num(k):
        v = get(k)
        return float(v) if v not in ("", None) else None

    start = max(0, idx - before)
    end = min(len(bars), idx + after + 1)
    window = bars[start:end]
    if not window:
        raise ValueError("描画範囲に足がありません")

    period = int(cfg.ma.period)
    ma_all = _ma(bars, period)
    ma_win = ma_all[start:end]
    x = list(range(len(window)))
    signal_x = idx - start

    fig, (price_ax, vol_ax) = plt.subplots(
        2, 1, figsize=(13, 7.2), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    fig.patch.set_facecolor("white")

    up_c, down_c = "#d64545", "#2e8b74"
    for i, b in enumerate(window):
        color = up_c if b.close >= b.open else down_c
        price_ax.plot([i, i], [b.low, b.high], color=color, linewidth=0.9, zorder=2)
        lo, hi = sorted((b.open, b.close))
        price_ax.add_patch(
            plt.Rectangle((i - 0.3, lo), 0.6, max(hi - lo, (b.high - b.low) * 0.02 or 0.01),
                          facecolor=color, edgecolor=color, zorder=3)
        )

    price_ax.plot(x, ma_win, color="#2b6cb0", linewidth=1.4,
                  label=_t(f"MA{period}", f"MA{period}"), zorder=4)

    lower = num("range_lower")
    upper = num("range_upper")
    stop = num("initial_stop")
    prev_high = num("prev_high")

    if lower is not None:
        price_ax.axhline(lower, color="#c98a2b", linewidth=1.3, zorder=4,
                         label=_t("レンジ下限", "Range low"))
    if upper is not None:
        price_ax.axhline(upper, color="#7b5ea7", linewidth=1.3, zorder=4,
                         label=_t("レンジ上限", "Range high"))
    if prev_high is not None:
        price_ax.axhline(prev_high, color="#555", linewidth=1.0, linestyle=":", zorder=4,
                         label=_t("前日高値", "Prev high"))
    if stop is not None:
        price_ax.axhline(stop, color="#b2242f", linewidth=1.5, linestyle="--", zorder=4,
                         label=_t("初期損切り", "Initial stop"))

    # ENTRY 判定日
    price_ax.axvline(signal_x, color="#111", linewidth=1.6, alpha=0.75, zorder=5,
                     label=_t("ENTRY判定日", "Signal day"))
    # forward 区間
    for horizon, alpha in ((5, 0.10), (10, 0.06)):
        right = min(len(window) - 1, signal_x + horizon)
        if right > signal_x:
            price_ax.axvspan(signal_x, right, color="#4a90d9", alpha=alpha, zorder=1)

    for i, b in enumerate(window):
        vol_ax.bar(i, b.volume, width=0.62,
                   color=(up_c if b.close >= b.open else down_c), alpha=0.55)
    vol_ax.axvline(signal_x, color="#111", linewidth=1.4, alpha=0.7)

    code = get("code")
    name = get("name")
    shape = get("shape")
    outcome = get("outcome")
    pos = num("position_in_range")
    date_s = get("date")
    date_s = date_s if isinstance(date_s, str) else date_s.isoformat()

    if _JP_FONT:
        title = (f"{code} {name}  シグナル {date_s}  "
                 f"{SHAPE_LABELS_JA.get(shape, shape)} / {OUTCOME_LABELS_JA.get(outcome, outcome)}"
                 f"  レンジ内位置 {pos:.2f}" if pos is not None else "")
        note = "※検証用。シグナル日より右は判定に使っていない未来の値動き"
    else:
        title = f"{code}  signal {date_s}  {shape} / {outcome}  position {pos:.2f}"
        note = "For research. Bars right of the signal were NOT used in the decision."

    price_ax.set_title(f"{title}\n{note}", loc="left", fontsize=12.5, fontweight="bold")
    price_ax.grid(alpha=0.25)
    price_ax.legend(loc="upper left", fontsize=8.5, ncol=3, framealpha=0.9)
    price_ax.set_ylabel(_t("株価（円）", "Price"))
    vol_ax.set_ylabel(_t("出来高", "Volume"))
    vol_ax.grid(alpha=0.2)
    vol_ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(
            lambda v, _p: "0" if v <= 0 else (
                f"{v/1e8:.1f}億" if _JP_FONT and v >= 1e8
                else f"{v/1e4:.0f}万" if _JP_FONT
                else f"{v/1e6:.1f}M"
            )
        )
    )

    step = max(1, len(window) // 9)
    ticks = list(range(0, len(window), step))
    vol_ax.set_xticks(ticks)
    vol_ax.set_xticklabels([window[i].date.strftime("%m/%d") for i in ticks], fontsize=9)
    price_ax.set_xlim(-1, len(window))

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110, facecolor="white")
    plt.close(fig)
    return out_path


def _pick(events: list, predicate, limit: int) -> list:
    out = []
    seen_codes = set()
    # 銘柄が偏らないよう、まず1銘柄1件で集める
    for ev in events:
        if len(out) >= limit:
            break
        code = ev.code if not isinstance(ev, dict) else ev["code"]
        if predicate(ev) and code not in seen_codes:
            out.append(ev)
            seen_codes.add(code)
    return out


def render_event_charts(sweep_result, price_map: dict, cfg, out_dir: Path) -> list[Path]:
    """カテゴリごとに代表例を描く（RESEARCH_DESIGN §10）。"""
    from swing_screener.research.classify import (
        OUTCOME_REACHED_UPPER,
        OUTCOME_STOPPED,
        SHAPE_LATE,
        SHAPE_NEAR_UPPER,
        SHAPE_UPPER_ZONE,
    )
    from swing_screener.research.config import threshold_label

    charts_dir = out_dir / "charts"
    limit = sweep_result.__dict__.get("charts_per_category", 4) if hasattr(
        sweep_result, "__dict__") else 4

    production = sweep_result.by_threshold.get(threshold_label(0.65))
    loose = sweep_result.by_threshold.get(threshold_label(0.80))
    unlimited = sweep_result.by_threshold.get(threshold_label(None))

    prod_events = production.events if production else []
    prod_keys = {(e.code, e.date) for e in prod_events}
    loose_only = (
        [e for e in loose.events if (e.code, e.date) not in prod_keys] if loose else []
    )
    unlimited_events = unlimited.events if unlimited else []

    selections: list[tuple[str, list]] = [
        ("worked", _pick(prod_events, lambda e: e.outcome == OUTCOME_REACHED_UPPER, limit)),
        ("stopped", _pick(prod_events, lambda e: e.outcome == OUTCOME_STOPPED, limit)),
        ("late", _pick(unlimited_events, lambda e: e.shape == SHAPE_LATE, limit)),
        ("excluded_by_guard", _pick(loose_only, lambda e: True, limit)),
        ("upper_zone", _pick(
            unlimited_events,
            lambda e: e.shape in (SHAPE_NEAR_UPPER, SHAPE_UPPER_ZONE), limit)),
    ]

    paths: list[Path] = []
    for category, events in selections:
        for ev in events:
            series = price_map.get(ev.code)
            if series is None:
                continue
            path = charts_dir / f"{category}_{ev.code}_{ev.date.isoformat()}.png"
            try:
                render_event_chart(ev, series, cfg, path)
                paths.append(path)
            except Exception:  # noqa: BLE001 - 1枚の失敗で全体を止めない
                continue
    return paths
