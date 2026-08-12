"""EXIT スタディ用の注釈付きチャート。

既存の research/charts.py（閾値スイープ用）とは表示要素が違うため別実装にする。
本番の charting.py には触れない。

表示するもの:
    ENTRYシグナル日 / 仮想ENTRY日・翌日始値 / 元レンジ上限・下限 / 初期STOP /
    MA25 / 警戒陰線とその安値 / 上限突破日 / トレーリング候補
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

from swing_screener.models import OHLCVBar, PriceSeries  # noqa: E402
from swing_screener.research.exit_study import TrackedEvent  # noqa: E402

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


def _ma(bars: list[OHLCVBar], period: int) -> list[float | None]:
    closes = [b.close for b in bars]
    return [
        None if i + 1 < period else sum(closes[i + 1 - period : i + 1]) / period
        for i in range(len(closes))
    ]


def render(
    ev: TrackedEvent,
    series: PriceSeries,
    cfg,
    out_path: Path,
    *,
    before: int = 35,
    after_pad: int = 8,
) -> Path:
    """1件の追跡結果を、シグナル前からポジション終了後まで描く。"""
    bars = list(series.bars)
    sig = ev.signal_index
    entry_i = ev.entry_index if ev.entry_index is not None else sig + 1
    end_i = min(len(bars) - 1, entry_i + max(ev.bars_tracked - 1, 1) + after_pad)
    start = max(0, sig - before)
    window = bars[start : end_i + 1]
    if not window:
        raise ValueError("描画範囲に足がありません")

    period = int(cfg.ma.period)
    ma_win = _ma(bars, period)[start : end_i + 1]
    x = list(range(len(window)))
    by_date = {b.date: i for i, b in enumerate(window)}

    fig, (ax, vax) = plt.subplots(
        2, 1, figsize=(14, 7.6), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]}
    )
    fig.patch.set_facecolor("white")
    up_c, down_c = "#d64545", "#2e8b74"

    for i, b in enumerate(window):
        color = up_c if b.close >= b.open else down_c
        ax.plot([i, i], [b.low, b.high], color=color, linewidth=0.9, zorder=2)
        lo, hi = sorted((b.open, b.close))
        ax.add_patch(
            plt.Rectangle((i - 0.3, lo), 0.6,
                          max(hi - lo, (b.high - b.low) * 0.02 or 0.01),
                          facecolor=color, edgecolor=color, zorder=3)
        )

    ax.plot(x, ma_win, color="#2b6cb0", linewidth=1.4, label=f"MA{period}", zorder=4)
    ax.axhline(ev.range_upper, color="#7b5ea7", linewidth=1.4, zorder=4,
               label=_t("元レンジ上限", "Range high"))
    ax.axhline(ev.range_lower, color="#c98a2b", linewidth=1.4, zorder=4,
               label=_t("元レンジ下限", "Range low"))
    ax.axhline(ev.initial_stop, color="#b2242f", linewidth=1.6, linestyle="--", zorder=4,
               label=_t("初期STOP", "Initial stop"))

    # ENTRY シグナル日 / 仮想ENTRY日
    sig_x = sig - start
    ax.axvline(sig_x, color="#111", linewidth=1.5, alpha=0.7, zorder=5,
               label=_t("ENTRYシグナル日", "Signal day"))
    if ev.entry_price is not None:
        ent_x = entry_i - start
        ax.axvline(ent_x, color="#111", linewidth=1.1, linestyle=":", alpha=0.6, zorder=5)
        ax.plot([ent_x], [ev.entry_price], marker=">", markersize=13, color="#0b7285",
                zorder=8, label=_t("仮想ENTRY（翌日始値）", "Virtual entry (next open)"))

    # 上限突破日
    if ev.upper_close_break_date and ev.upper_close_break_date in by_date:
        bx = by_date[ev.upper_close_break_date]
        ax.plot([bx], [window[bx].close], marker="^", markersize=11, color="#7b5ea7",
                zorder=8, label=_t("終値で上限突破", "Close broke range high"))

    # 警戒陰線とその安値
    first = True
    for wc in ev.warning_candles:
        if wc.date not in by_date:
            continue
        wx = by_date[wc.date]
        ax.plot([wx], [wc.high * 1.004], marker="v", markersize=8, color="#e08a1e",
                zorder=8, label=_t("警戒陰線", "Warning candle") if first else None)
        right = by_date.get(wc.broke_low_date, len(window) - 1)
        ax.hlines(wc.low, wx, right, color="#e08a1e", linewidth=1.0, linestyle=":",
                  alpha=0.85, zorder=6,
                  label=_t("警戒陰線の安値", "Warning candle low") if first else None)
        if wc.broke_low_date and wc.broke_low_date in by_date:
            bx = by_date[wc.broke_low_date]
            ax.plot([bx], [wc.low], marker="x", markersize=9, color="#e08a1e",
                    markeredgewidth=2, zorder=8,
                    label=_t("警戒陰線安値割れ（利確候補）", "Warning low broken")
                    if first else None)
        first = False

    # トレーリング候補（参考）
    styles = {"strict": ("#0b7285", "-"), "loose": ("#7fa8b8", "--")}
    labelled: set[str] = set()
    for tc in ev.trail_candidates:
        if not tc.improves_on_initial_stop or tc.armed_date not in by_date:
            continue
        color, ls = styles[tc.variant]
        ax0 = by_date[tc.armed_date]
        ax.hlines(tc.trail_stop_candidate, ax0, len(window) - 1, color=color,
                  linewidth=1.3, linestyle=ls, alpha=0.9, zorder=6,
                  label=(_t(f"trail候補 {tc.variant}（参考）", f"trail {tc.variant} (ref)")
                         if tc.variant not in labelled else None))
        labelled.add(tc.variant)

    # 初期STOP到達日
    if ev.stop_date and ev.stop_date in by_date:
        sx = by_date[ev.stop_date]
        ax.plot([sx], [ev.initial_stop], marker="X", markersize=13, color="#b2242f",
                zorder=9, label=_t("初期STOP到達", "Initial stop hit"))
        ax.axvspan(sx - 0.45, sx + 0.45, color="#b2242f", alpha=0.10, zorder=1)

    # 保有区間
    if ev.entry_price is not None:
        hold_right = min(len(window) - 1, entry_i - start + ev.bars_tracked - 1)
        ax.axvspan(entry_i - start, hold_right, color="#4a90d9", alpha=0.07, zorder=1)

    for i, b in enumerate(window):
        vax.bar(i, b.volume, width=0.62,
                color=(up_c if b.close >= b.open else down_c), alpha=0.55)
    vax.axvline(sig_x, color="#111", linewidth=1.3, alpha=0.6)

    gap = f"{ev.gap_pct:+.2f}%" if ev.gap_pct is not None else "－"
    mg = f"{ev.max_gain_pct:+.1f}%" if ev.max_gain_pct is not None else "－"
    if _JP_FONT:
        title = (f"{ev.code} {ev.name}　シグナル {ev.signal_date}　{ev.type_label}"
                 f"　ギャップ {gap}　最大上昇 {mg}（仮想ENTRY基準）")
        note = ("※検証用。ポジションを閉じる機械判定に使ったのは確定ルールの初期STOPのみ。"
                "警戒陰線・trail候補は未確定ルールの参考表示")
    else:
        title = (f"{ev.code}  signal {ev.signal_date}  {ev.type_label}  "
                 f"gap {gap}  max gain {mg}")
        note = "Research only. Only the initial stop is a confirmed rule."

    ax.set_title(f"{title}\n{note}", loc="left", fontsize=12, fontweight="bold")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=8, ncol=4, framealpha=0.92)
    ax.set_ylabel(_t("株価（円）", "Price"))
    vax.set_ylabel(_t("出来高", "Volume"))
    vax.grid(alpha=0.2)
    vax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(
            lambda v, _p: "0" if v <= 0 else (
                f"{v/1e8:.1f}億" if _JP_FONT and v >= 1e8
                else f"{v/1e4:.0f}万" if _JP_FONT else f"{v/1e6:.1f}M"
            )
        )
    )
    step = max(1, len(window) // 11)
    ticks = list(range(0, len(window), step))
    vax.set_xticks(ticks)
    vax.set_xticklabels([window[i].date.strftime("%m/%d") for i in ticks], fontsize=8.5)
    ax.set_xlim(-1, len(window))

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=105, facecolor="white")
    plt.close(fig)
    return out_path


# --- 代表例の選定 -------------------------------------------------------------

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("immediate_stop", "ENTRY直後に初期STOP"),
    ("upper_then_fade", "上限到達後に失速"),
    ("breakout_run", "上限突破後に大きく上昇"),
    ("warning_exit", "警戒陰線から利確候補"),
    ("trailing", "トレーリングが有効そうな例"),
    ("gap_late", "翌日ギャップアップでENTRYが遅くなった例"),
    ("ambiguous", "日足だけでは先後関係が判断できない例"),
)


def select_representatives(
    events: list[TrackedEvent], per_category: int = 3
) -> dict[str, list[TrackedEvent]]:
    """カテゴリごとの代表例。銘柄が偏らないよう1銘柄1件に絞る。"""
    entered = [e for e in events if e.entry_available]

    def pick(cands: list[TrackedEvent], key=None, limit: int = per_category):
        ordered = sorted(cands, key=key) if key else cands
        out: list[TrackedEvent] = []
        seen: set[str] = set()
        for e in ordered:
            if len(out) >= limit:
                break
            if e.code in seen:
                continue
            out.append(e)
            seen.add(e.code)
        return out

    sel: dict[str, list[TrackedEvent]] = {}
    sel["immediate_stop"] = pick(
        [e for e in entered if e.hit_initial_stop and not e.reached_upper],
        key=lambda e: (e.stop_day_offset if e.stop_day_offset is not None else 99),
    )
    sel["upper_then_fade"] = pick(
        [e for e in entered if e.reached_upper and not e.upper_close_break]
        + [e for e in entered if e.upper_close_break and (e.max_gain_pct or 0) < 3.0],
    )
    sel["breakout_run"] = pick(
        [e for e in entered if e.upper_close_break],
        key=lambda e: -(e.max_gain_pct or 0),
    )
    sel["warning_exit"] = pick(
        [e for e in entered
         if e.warning_candles and e.warning_candles[0].broke_low_date
         and e.warning_candles[0].new_high_vs_candle_high_before_break],
        key=lambda e: -(e.max_gain_pct or 0),
    )
    trail = [
        e for e in entered
        if (e.trail_sim_strict and e.trail_sim_strict.armed)
        or (e.trail_sim_loose and e.trail_sim_loose.armed)
    ]
    sel["trailing"] = pick(
        trail,
        key=lambda e: -((e.trail_sim_loose.exit_return_pct or 0)
                        if e.trail_sim_loose else 0),
    )
    sel["gap_late"] = pick(
        [e for e in entered if "ENTRY_POSITION_ABOVE_GUARD" in e.flags
         or e.entry_above_range_upper],
        key=lambda e: -(e.gap_pct or 0),
    )
    sel["ambiguous"] = pick(
        [e for e in entered if e.ambiguous_days]
        + [e for e in entered
           if e.trail_sim_loose and e.trail_sim_loose.ambiguous_with_initial_stop],
    )
    return sel


def render_all(
    events: list[TrackedEvent], price_map: dict, cfg, out_dir: Path,
    per_category: int = 3,
) -> dict[str, list[tuple[TrackedEvent, Path]]]:
    charts_dir = out_dir / "representative_charts"
    selection = select_representatives(events, per_category)
    result: dict[str, list[tuple[TrackedEvent, Path]]] = {}
    for key, _label in CATEGORIES:
        picked = selection.get(key, [])
        rendered: list[tuple[TrackedEvent, Path]] = []
        for ev in picked:
            series = price_map.get(ev.code)
            if series is None:
                continue
            path = charts_dir / f"{key}_{ev.code}_{ev.signal_date.isoformat()}.png"
            try:
                render(ev, series, cfg, path)
                rendered.append((ev, path))
            except Exception:  # noqa: BLE001 - 1枚の失敗で全体を止めない
                continue
        result[key] = rendered
    return result
