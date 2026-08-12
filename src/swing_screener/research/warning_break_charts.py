"""warning_low 割れ後の扱いの比較（warning_break_study.py）用のチャート（§16）。

1 枚に 4 案を重ねて描く。値動きも警戒足も 4 案で完全に同じなので、
違うのは「どの日に降りたか」だけ。そこだけが読み取れるように、
割れの 3 段階（日中割れ / 終値割れ / 上限割れ）を別マーカーで出す。

表示するもの（§16）:
    ENTRY / 元レンジ上下限 / 初期STOP / BREAKOUT / WARNING / warning_low /
    reference_high / warning_low 日中割れ / warning_low 終値割れ /
    元レンジ上限の終値割れ / 案ごとの仮想EXIT / その後の最高値

本番の charting.py には一切触れない。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from swing_screener.models import OHLCVBar, PriceSeries  # noqa: E402
from swing_screener.research import exit_state_machine as sm  # noqa: E402
from swing_screener.research import warning_break_study as wb  # noqa: E402

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


# 案ごとの色。線幅を変えて重なっても下の線が見えるようにする。
RULE_STYLE = {
    sm.BREAK_HOLD: {"color": "#8a8f98", "lw": 3.4, "alpha": 0.85, "marker": "s"},
    sm.BREAK_LOW: {"color": "#d64545", "lw": 2.4, "alpha": 0.95, "marker": "X"},
    sm.BREAK_CLOSE: {"color": "#2b6cb0", "lw": 1.5, "alpha": 0.95, "marker": "P"},
    sm.BREAK_STRUCT: {"color": "#2e8b74", "lw": 0.9, "alpha": 1.0, "marker": "*"},
}


def render_compare(
    evs: dict[str, sm.SMEvent],
    series: PriceSeries,
    cfg,
    out_path: Path,
    *,
    before: int = 30,
    after_pad: int = 8,
) -> Path:
    """同一イベントの 4 案を 1 枚に重ねて描く。"""
    base = evs[sm.BREAK_HOLD]
    bars = list(series.bars)
    sig = base.signal_index
    entry_i = base.entry_index if base.entry_index is not None else sig + 1
    tracked = max(e.bars_tracked for e in evs.values())
    end_i = min(len(bars) - 1, entry_i + max(tracked - 1, 1) + after_pad)
    start = max(0, sig - before)
    window = bars[start : end_i + 1]
    if not window:
        raise ValueError("描画範囲に足がありません")

    period = int(cfg.ma.period)
    ma_win = _ma(bars, period)[start : end_i + 1]
    x = list(range(len(window)))
    by_date = {b.date: i for i, b in enumerate(window)}
    y_lo, y_hi = min(b.low for b in window), max(b.high for b in window)
    span = y_hi - y_lo

    fig, (ax, vax) = plt.subplots(
        2, 1, figsize=(15, 9.4), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]}
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

    ax.plot(x, ma_win, color="#5a6b7a", linewidth=1.2, label=f"MA{period}", zorder=4)
    ax.axhline(base.range_upper, color="#7b5ea7", linewidth=1.5, zorder=4,
               label=_t("元レンジ上限（V3の2つ目の条件）", "Range high (V3 threshold)"))
    ax.axhline(base.range_lower, color="#c98a2b", linewidth=1.2, zorder=4,
               label=_t("元レンジ下限", "Range low"))
    ax.axhline(base.initial_stop, color="#b2242f", linewidth=1.5, linestyle="--",
               zorder=4, label=_t("初期STOP（range_lower×0.995）", "Initial stop"))

    sig_x = sig - start
    ax.axvline(sig_x, color="#111", linewidth=1.4, alpha=0.65, zorder=5,
               label=_t("ENTRYシグナル日", "Signal day"))
    if base.entry_price is not None:
        ent_x = entry_i - start
        ax.axvline(ent_x, color="#111", linewidth=1.0, linestyle=":", alpha=0.55, zorder=5)
        ax.plot([ent_x], [base.entry_price], marker=">", markersize=12, color="#0b7285",
                zorder=8, label=_t("仮想ENTRY（翌営業日始値）", "Virtual entry"))

    if base.upper_close_break_date and base.upper_close_break_date in by_date:
        bx = by_date[base.upper_close_break_date]
        if base.upper_close_break_price is not None:
            ax.plot([bx], [base.upper_close_break_price], marker="^", markersize=11,
                    color="#7b5ea7", zorder=8,
                    label=_t("BREAKOUT（終値で上限突破）", "Breakout (close)"))

    # --- 警戒足と割れの 3 段階（4 案とも同じ観測値なので参考基準の記録を使う）---
    for w in base.warnings:
        wx = by_date.get(w.date)
        if wx is None:
            continue
        ax.plot([wx], [w.high + span * 0.012], marker="v", markersize=10, color="#111",
                markeredgecolor="#fff", markeredgewidth=0.6, zorder=10)
        ax.hlines(w.low, wx, len(window) - 1, color="#111", linewidth=1.2,
                  linestyle=":", alpha=0.8, zorder=6)
        ax.hlines(w.reference_high, wx, len(window) - 1, color="#666", linewidth=0.9,
                  linestyle="-.", alpha=0.5, zorder=6)
        ax.annotate(_t(f"warning_low {w.low:.1f}", f"warning_low {w.low:.1f}"),
                    xy=(wx, w.low), xytext=(4, -12), textcoords="offset points",
                    fontsize=7, color="#111", zorder=12,
                    bbox=dict(facecolor="white", alpha=0.8, edgecolor="none", pad=1))

    for b in base.warning_breaks:
        if b.intraday_break_date in by_date and b.intraday_break_low is not None:
            ax.plot([by_date[b.intraday_break_date]], [b.intraday_break_low],
                    marker="v", markersize=9, color="#d64545",
                    markeredgecolor="#222", markeredgewidth=0.6, zorder=11)
        if b.close_break_date in by_date and b.close_break_close is not None:
            ax.plot([by_date[b.close_break_date]], [b.close_break_close],
                    marker="o", markersize=8, markerfacecolor="none",
                    markeredgecolor="#2b6cb0", markeredgewidth=2.0, zorder=11)
        if b.struct_break_date in by_date and b.struct_break_close is not None:
            ax.plot([by_date[b.struct_break_date]], [b.struct_break_close],
                    marker="s", markersize=9, markerfacecolor="none",
                    markeredgecolor="#7b5ea7", markeredgewidth=2.0, zorder=11)

    legend_extra: list[Line2D] = []
    for rule in wb.RULES:
        ev = evs.get(rule)
        if ev is None:
            continue
        st = RULE_STYLE[rule]
        col, lw = st["color"], st["lw"]

        if ev.daily:
            xs, ys = [], []
            end = ev.path_result.exit_day_offset
            for ds in ev.daily:
                xi = by_date.get(ds.date)
                if xi is None or (end is not None and ds.day_offset > end):
                    continue
                xs.append(xi)
                ys.append(ds.active_stop)
            if xs:
                ax.step(xs, ys, where="post", color=col, linewidth=lw,
                        alpha=st["alpha"] * 0.7, zorder=6, solid_capstyle="butt")

        for su in ev.stop_updates:
            sux = by_date.get(su.stop_update_date)
            if sux is None:
                continue
            ax.plot([sux], [su.new_stop], marker="D", markersize=6, color=col, zorder=11)

        r = ev.path_result
        if r.exit_date in by_date and r.exit_reference_price is not None:
            cx = by_date[r.exit_date]
            ax.plot([cx], [r.exit_reference_price], marker=st["marker"],
                    markersize=15 if st["marker"] == "*" else 12, color=col,
                    markeredgecolor="#222", markeredgewidth=0.6, zorder=13)

        ret = r.approximate_return_pct
        trig = r.trigger_date.strftime("%m/%d") if r.trigger_date else "－"
        exd = r.exit_date.strftime("%m/%d") if r.exit_date else "－"
        legend_extra.append(
            Line2D([0], [0], color=col, linewidth=lw, marker=st["marker"],
                   markersize=8, markeredgecolor="#222", markeredgewidth=0.4,
                   label=(f"{sm.BREAK_RULE_SHORT_JA[rule]}: "
                          f"{_t('トリガー', 'trigger')} {trig} / "
                          f"{_t('約定', 'fill')} {exd} / "
                          f"{r.exit_type} / "
                          + (f"{ret:+.1f}%" if ret is not None else "－")))
        )

    # --- EXIT 後の最高値（§16 の「その後の最高値」）---
    win = [
        ds for ds in base.daily
        if base.path_result.exit_day_offset is None
        or ds.day_offset <= base.path_result.exit_day_offset
    ]
    v1 = evs.get(sm.BREAK_LOW)
    if v1 is not None and v1.path_result.exit_day_offset is not None:
        post = [ds for ds in win if ds.day_offset > v1.path_result.exit_day_offset]
        if post:
            best = max(post, key=lambda ds: ds.high)
            bx = by_date.get(best.date)
            if bx is not None:
                ax.plot([bx], [best.high], marker="^", markersize=12, color="#c026d3",
                        markeredgecolor="#222", markeredgewidth=0.6, zorder=13)
                px = v1.path_result.exit_reference_price
                lbl = (f"{_t('V1のEXIT後の最高値', 'max high after V1 exit')} "
                       f"{best.high:.1f}"
                       + (f"（{(best.high - px) / px * 100:+.1f}%）" if px else ""))
                ax.annotate(lbl, xy=(bx, best.high), xytext=(-6, 12),
                            textcoords="offset points", fontsize=7.5, color="#c026d3",
                            ha="right", zorder=14,
                            bbox=dict(facecolor="white", alpha=0.86,
                                      edgecolor="#c026d3", linewidth=0.4, pad=1.5))

    shape_legend = [
        Line2D([0], [0], color="#111", marker="v", linestyle="none", markersize=9,
               markerfacecolor="#111", label=_t("警戒陰線", "Warning candle")),
        Line2D([0], [0], color="#111", linestyle=":", label="warning_low"),
        Line2D([0], [0], color="#666", linestyle="-.",
               label=_t("reference_high（今回は変更しない）", "reference_high")),
        Line2D([0], [0], color="#d64545", marker="v", linestyle="none", markersize=8,
               label=_t("warning_low を日中に割った日（V1のトリガー）",
                        "intraday break (V1)")),
        Line2D([0], [0], color="#2b6cb0", marker="o", linestyle="none", markersize=8,
               markerfacecolor="none", markeredgewidth=2.0,
               label=_t("warning_low を終値で割った日（V2のトリガー）",
                        "close break (V2)")),
        Line2D([0], [0], color="#7b5ea7", marker="s", linestyle="none", markersize=8,
               markerfacecolor="none", markeredgewidth=2.0,
               label=_t("元レンジ上限も終値で割った日（V3のトリガー）",
                        "structural break (V3)")),
        Line2D([0], [0], color="#555", marker="D", linestyle="none", markersize=6,
               label=_t("active_stop引き上げ", "Stop raise")),
        Line2D([0], [0], color="#c026d3", marker="^", linestyle="none", markersize=9,
               label=_t("V1のEXIT後に付けた最高値", "max high after V1 exit")),
    ]

    if base.entry_price is not None:
        hold_right = min(len(window) - 1, entry_i - start + tracked - 1)
        ax.axvspan(entry_i - start, hold_right, color="#4a90d9", alpha=0.06, zorder=1)

    for i, b in enumerate(window):
        vax.bar(i, b.volume, width=0.62,
                color=(up_c if b.close >= b.open else down_c), alpha=0.55)
    vax.axvline(sig_x, color="#111", linewidth=1.2, alpha=0.6)

    if _JP_FONT:
        title = (f"{base.code} {base.name}　シグナル {base.signal_date}　"
                 f"warning_low 割れ後の扱いの比較（参考/V1/V2/V3）")
        note = ("※検証用チャート。4案の違いは「warning_low を割ったあとどこで降りるか」だけ。"
                "WARNING開始条件・reference_high・押し安値・トレーリング・初期STOPは同一。\n"
                "　仮想EXITはトリガー翌営業日の始値で、warning_low での約定は仮定していない。"
                "いずれも現行の文章ルールの読み方であって正式ルールではない。")
    else:
        title = f"{base.code}  signal {base.signal_date}  warning_low break rules"
        note = ("Research chart. Only the post-break handling differs. "
                "None of them is an official rule.")

    fig.suptitle(title, x=0.01, ha="left", fontsize=12.5, fontweight="bold")
    ax.set_title(note, loc="left", fontsize=8, color="#555555")
    ax.grid(alpha=0.22)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles + legend_extra + shape_legend,
               labels + [h.get_label() for h in legend_extra + shape_legend],
               loc="lower center", ncol=3, fontsize=7.6, framealpha=0.93,
               bbox_to_anchor=(0.5, 0.008))
    ax.set_ylabel(_t("株価（円）", "Price"))
    vax.set_ylabel(_t("出来高", "Volume"))
    vax.grid(alpha=0.2)
    vax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(
            lambda val, _p: "0" if val <= 0 else (
                f"{val/1e8:.1f}億" if _JP_FONT and val >= 1e8
                else f"{val/1e4:.0f}万" if _JP_FONT else f"{val/1e6:.1f}M"
            )
        )
    )
    step = max(1, len(window) // 12)
    ticks = list(range(0, len(window), step))
    vax.set_xticks(ticks)
    vax.set_xticklabels([window[i].date.strftime("%m/%d") for i in ticks], fontsize=8.5)
    ax.set_xlim(-1, len(window))

    fig.tight_layout(rect=(0, 0.255, 1, 0.965))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=105, facecolor="white")
    plt.close(fig)
    return out_path


# --- 代表例の選定（§16）--------------------------------------------------------

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("intraday_recovered_rise",
     "1. 日中に warning_low を割るが終値は回復し、その後大きく上昇した例"),
    ("close_break_then_fall", "2. warning_low を終値で割り、そのまま下落した例"),
    ("held_upper_revived",
     "3. warning_low を終値で割るが元レンジ上限は維持し、その後復活した例"),
    ("struct_break_collapse", "4. 元レンジ上限まで終値で割り、そのまま崩れた例"),
    ("v1_too_early", "5. LOW_BREAK が早売りになった例"),
    ("v2_natural", "6. CLOSE_BREAK が自然に機能した例"),
    ("v3_too_late", "7. STRUCTURAL_BREAK が待ちすぎた例"),
    ("gain10", "8. +10%以上の最大含み益があった例"),
    ("gap_down_exit", "9. ギャップダウンで仮想EXIT価格が悪化した例"),
    ("hard_to_judge", "10. どの案でも判断が難しい例"),
)


def select_representatives(
    runs: dict[str, wb.RuleRun], per_category: int = 2
) -> dict[str, list[wb.EventKey]]:
    hold = runs[sm.BREAK_HOLD].by_key
    v1 = runs[sm.BREAK_LOW].by_key
    v2 = runs[sm.BREAK_CLOSE].by_key
    v3 = runs[sm.BREAK_STRUCT].by_key
    ref = wb.reference_max_gain(runs)
    win = wb.hold_window(runs)
    natural = {(n.code, n.signal_date): n for n in wb.classify_naturalness(runs)}
    keys = [k for k, e in hold.items() if e.entry_available]

    def pick(cands: list[wb.EventKey], key=None, limit: int = per_category):
        ordered = sorted(cands, key=key) if key else cands
        out: list[wb.EventKey] = []
        seen: set[str] = set()
        for k in ordered:
            if len(out) >= limit:
                break
            if k[0] in seen:
                continue
            out.append(k)
            seen.add(k[0])
        return out

    def shape_is(k: wb.EventKey, name: str) -> bool:
        n = natural.get(k)
        return n is not None and n.shape == name

    sel: dict[str, list[wb.EventKey]] = {}

    sel["intraday_recovered_rise"] = pick(
        [k for k in keys if shape_is(k, "recovered_intraday")],
        key=lambda k: -(ref.get(k) or 0.0),
    )
    sel["close_break_then_fall"] = pick(
        [
            k for k in keys
            if any(b.close_break_date is not None for b in hold[k].warning_breaks)
            and (v2[k].path_result.approximate_return_pct or 0) < 0
        ],
        key=lambda k: (v2[k].path_result.approximate_return_pct or 0),
    )
    sel["held_upper_revived"] = pick(
        [
            k for k in keys
            if shape_is(k, "held_upper")
            or (v3[k].path_result.approximate_return_pct or -99)
            > (v2[k].path_result.approximate_return_pct or -99) + 0.01
        ],
        key=lambda k: -((v3[k].path_result.approximate_return_pct or 0)
                        - (v2[k].path_result.approximate_return_pct or 0)),
    )
    sel["struct_break_collapse"] = pick(
        [
            k for k in keys
            if any(b.struct_break_date is not None for b in hold[k].warning_breaks)
            and (v3[k].path_result.approximate_return_pct or 0) < 0
        ],
        key=lambda k: (v3[k].path_result.approximate_return_pct or 0),
    )

    revivals = wb.extract_revivals(runs)
    early: list[wb.EventKey] = []
    for rc in sorted(revivals, key=lambda c: -(c.max_gain_after_break_pct or -999)):
        k = (rc.code, rc.signal_date)
        if k not in early:
            early.append(k)
    sel["v1_too_early"] = pick(early)

    sel["v2_natural"] = pick(
        [k for k in keys if (natural.get(k).category if natural.get(k) else "")
         == "v2_natural"]
        or [
            k for k in keys
            if (v2[k].path_result.approximate_return_pct or -99)
            >= (v1[k].path_result.approximate_return_pct or -99)
            and (v2[k].path_result.approximate_return_pct or 0) > 0
        ],
        key=lambda k: -((v2[k].path_result.approximate_return_pct or 0)
                        - (v1[k].path_result.approximate_return_pct or 0)),
    )

    waited = wb.extract_waited_too_long(runs)
    late: list[wb.EventKey] = []
    for wc in sorted(waited, key=lambda c: (c.diff_pt or 0)):
        k = (wc.code, wc.signal_date)
        if wc.rule == sm.BREAK_STRUCT and k not in late:
            late.append(k)
    sel["v3_too_late"] = pick(late)

    sel["gain10"] = pick([k for k in keys if (ref.get(k) or 0.0) >= 10.0],
                         key=lambda k: -(ref.get(k) or 0.0))

    def worst_gap(k: wb.EventKey) -> float:
        vals = [
            run[k].path_result.fill_gap_pct for run in (v1, v2, v3)
            if run[k].path_result.fill_gap_pct is not None
        ]
        return min(vals) if vals else 0.0

    sel["gap_down_exit"] = pick(
        [k for k in keys if worst_gap(k) < -0.5], key=worst_gap
    )

    # どの案でも判断が難しい = 3 案とも損失で、しかも +5% 以上まで伸びていた
    sel["hard_to_judge"] = pick(
        [
            k for k in keys
            if (natural.get(k).category if natural.get(k) else "") == "all_bad"
            and (ref.get(k) or 0.0) >= 5.0
        ],
        key=lambda k: -(ref.get(k) or 0.0),
    )
    # 予備: 該当が無ければ「EXIT 後にいちばん伸びた件」を出す
    if not sel["hard_to_judge"]:
        sel["hard_to_judge"] = pick(
            [k for k in keys if win.get(k)], key=lambda k: -(ref.get(k) or 0.0)
        )

    return sel


def render_all(
    runs: dict[str, wb.RuleRun], price_map: dict, cfg, out_dir: Path,
    per_category: int = 2,
) -> dict[str, list[tuple[dict[str, sm.SMEvent], Path]]]:
    charts_dir = out_dir / "representative_charts"
    selection = select_representatives(runs, per_category)
    by_rule = {r: runs[r].by_key for r in wb.RULES if r in runs}
    result: dict[str, list[tuple[dict[str, sm.SMEvent], Path]]] = {}
    for key, _label in CATEGORIES:
        rendered: list[tuple[dict[str, sm.SMEvent], Path]] = []
        for k in selection.get(key, []):
            evs = {r: m[k] for r, m in by_rule.items() if k in m}
            if sm.BREAK_HOLD not in evs:
                continue
            series = price_map.get(k[0])
            if series is None:
                continue
            path = charts_dir / f"{key}_{k[0]}_{k[1].isoformat()}.png"
            try:
                render_compare(evs, series, cfg, path)
                rendered.append((evs, path))
            except Exception:  # noqa: BLE001 - 1枚の失敗で全体を止めない
                continue
        result[key] = rendered
    return result
