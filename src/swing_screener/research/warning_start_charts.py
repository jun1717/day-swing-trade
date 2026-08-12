"""警戒足の有効化タイミング比較（warning_start_study.py）用のチャート（§13）。

1 枚に A/B/C を重ねて描く。3 案は「WARNING へ入る条件」だけが違うので、
同じ値動きの上で警戒足・warning_low・EXIT がどれだけずれるかを直接見たい。

表示するもの:
    ENTRY / 元レンジ上下限 / 初期STOP / BREAKOUT /
    breakout_day_high（B の確認水準） / breakout_day_close（C の確認水準） /
    案ごとの UPTREND_CONFIRMED・WARNING・warning_low・reference_high・
    REHIGH・押し安値・active_stop の推移・CASE2/CASE3 の仮想EXIT。

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
from swing_screener.research import warning_start_study as ws  # noqa: E402

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
VARIANT_STYLE = {
    sm.VARIANT_A: {"color": "#e08a1e", "lw": 3.0, "alpha": 0.9, "dy": 0.010},
    sm.VARIANT_B: {"color": "#2b6cb0", "lw": 1.9, "alpha": 0.95, "dy": 0.020},
    sm.VARIANT_C: {"color": "#2e8b74", "lw": 1.0, "alpha": 1.0, "dy": 0.030},
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
    """同一イベントの A/B/C を 1 枚に重ねて描く。"""
    base = evs[sm.VARIANT_A]
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
    span = max(b.high for b in window) - min(b.low for b in window)

    fig, (ax, vax) = plt.subplots(
        2, 1, figsize=(15, 9.2), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]}
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
    ax.axhline(base.range_upper, color="#7b5ea7", linewidth=1.3, zorder=4,
               label=_t("元レンジ上限", "Range high"))
    ax.axhline(base.range_lower, color="#c98a2b", linewidth=1.3, zorder=4,
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

    # BREAKOUT と、B/C が見ている 2 つの確認水準
    if base.upper_close_break_date and base.upper_close_break_date in by_date:
        bx = by_date[base.upper_close_break_date]
        if base.upper_close_break_price is not None:
            ax.plot([bx], [base.upper_close_break_price], marker="^", markersize=11,
                    color="#7b5ea7", zorder=8,
                    label=_t("BREAKOUT（終値で上限突破）", "Breakout (close)"))
        if base.breakout_day_high is not None:
            ax.hlines(base.breakout_day_high, bx, len(window) - 1, color="#2b6cb0",
                      linewidth=1.2, linestyle=(0, (4, 3)), alpha=0.85, zorder=5,
                      label=_t("breakout_day_high（Bの確認水準）",
                               "breakout_day_high (B threshold)"))
        if base.breakout_day_close is not None:
            ax.hlines(base.breakout_day_close, bx, len(window) - 1, color="#2e8b74",
                      linewidth=1.2, linestyle=(0, (1, 2)), alpha=0.85, zorder=5,
                      label=_t("breakout_day_close（Cの確認水準）",
                               "breakout_day_close (C threshold)"))

    legend_extra: list[Line2D] = []
    for v in sm.VARIANTS:
        ev = evs.get(v)
        if ev is None:
            continue
        st = VARIANT_STYLE[v]
        col, lw, alpha, dy = st["color"], st["lw"], st["alpha"], st["dy"]
        off = span * dy

        # active_stop の推移（3 案を重ねる。太い線が下に来るので全部見える）
        if ev.daily:
            xs, ys = [], []
            for ds in ev.daily:
                xi = by_date.get(ds.date)
                if xi is None:
                    continue
                xs.append(xi)
                ys.append(ds.active_stop)
            if xs:
                if xs[-1] < len(window) - 1:
                    xs.append(len(window) - 1)
                    ys.append(ys[-1])
                ax.step(xs, ys, where="post", color=col, linewidth=lw, alpha=alpha * 0.75,
                        zorder=6, solid_capstyle="butt")

        # UPTREND_CONFIRMED
        if ev.uptrend_confirmed_date in by_date:
            cx = by_date[ev.uptrend_confirmed_date]
            ax.axvline(cx, color=col, linewidth=1.1, linestyle=(0, (5, 4)), alpha=0.55,
                       zorder=5)
            ax.plot([cx], [ev.uptrend_confirmed_price], marker="P", markersize=10,
                    color=col, markeredgecolor="#222", markeredgewidth=0.5, zorder=10)
            # B と C が同日に確認成立することが多いので、注記は上下にずらす。
            # 上端に近い点では下向きに出して、タイトルへめり込ませない。
            y0, y1 = min(b.low for b in window), max(b.high for b in window)
            near_top = (ev.uptrend_confirmed_price - y0) / max(y1 - y0, 1e-9) > 0.75
            step = 12 + 16 * sm.VARIANTS.index(v)
            ax.annotate(
                _t(f"{v}: UPTREND_CONFIRMED", f"{v}: confirmed"),
                xy=(cx, ev.uptrend_confirmed_price),
                xytext=(5, -step if near_top else step),
                textcoords="offset points", fontsize=7, color=col, zorder=12,
                bbox=dict(facecolor="white", alpha=0.85, edgecolor=col,
                          linewidth=0.4, pad=1),
            )

        for w in ev.warnings:
            wx = by_date.get(w.date)
            if wx is None:
                continue
            end_date = w.low_break_date or w.resolved_date
            wx_end = by_date.get(end_date) if end_date else None
            if wx_end is None:
                wx_end = len(window) - 1

            ax.plot([wx], [w.high + off], marker="v", markersize=9, color=col,
                    markeredgecolor="#222", markeredgewidth=0.5, zorder=10)
            ax.annotate(v, xy=(wx, w.high + off), xytext=(-2, 7),
                        textcoords="offset points", fontsize=7.5, color=col,
                        fontweight="bold", zorder=12)
            ax.hlines(w.low, wx, wx_end, color=col, linewidth=1.1, linestyle=":",
                      alpha=0.9, zorder=6)
            ax.hlines(w.reference_high, wx, wx_end, color=col, linewidth=0.9,
                      linestyle="-.", alpha=0.55, zorder=6)
            if w.low_break_date in by_date:
                ax.plot([by_date[w.low_break_date]], [w.low], marker="x", markersize=9,
                        color=col, markeredgewidth=2, zorder=11)
            if w.rehigh_date in by_date:
                ax.plot([by_date[w.rehigh_date]], [w.reference_high], marker="^",
                        markersize=9, color=col, zorder=11)
            if w.new_swing_low_date in by_date and w.new_swing_low_candidate is not None:
                ax.plot([by_date[w.new_swing_low_date]], [w.new_swing_low_candidate],
                        marker="o", markersize=6, markerfacecolor="white",
                        markeredgecolor=col, markeredgewidth=1.4, zorder=11)

        for su in ev.stop_updates:
            sux = by_date.get(su.stop_update_date)
            if sux is None:
                continue
            ax.plot([sux], [su.new_stop], marker="D", markersize=6, color=col, zorder=11)
            ax.annotate(f"{v}→{su.new_stop:.1f}", xy=(sux, su.new_stop),
                        xytext=(3, -10), textcoords="offset points", fontsize=7,
                        color=col, zorder=12,
                        bbox=dict(facecolor="white", alpha=0.78, edgecolor="none", pad=1))

        for case, marker, size in ((sm.CASE2, "X", 12), (sm.CASE3, "*", 16)):
            r = ev.cases.get(case)
            if r is None or r.exit_date is None or r.exit_reference_price is None:
                continue
            cx = by_date.get(r.exit_date)
            if cx is None:
                continue
            ax.plot([cx], [r.exit_reference_price], marker=marker, markersize=size,
                    color=col, markeredgecolor="#222", markeredgewidth=0.6, zorder=12)

        c2 = ev.cases[sm.CASE2].approximate_return_pct
        c3 = ev.cases[sm.CASE3].approximate_return_pct
        wtxt = ev.warnings[0].date.strftime("%m/%d") if ev.warnings else _t("なし", "none")
        ctxt = (ev.uptrend_confirmed_date.strftime("%m/%d")
                if ev.uptrend_confirmed_date else "－")
        legend_extra.append(
            Line2D([0], [0], color=col, linewidth=st["lw"], marker="v",
                   markersize=7, markeredgecolor="#222", markeredgewidth=0.4,
                   label=(f"{v}: {_t('確認', 'confirm')} {ctxt} / "
                          f"{_t('警戒足', 'warning')} {wtxt} / "
                          f"CASE2 {c2:+.1f}% / CASE3 {c3:+.1f}%"
                          if c2 is not None and c3 is not None else f"{v}"))
        )

    shape_legend = [
        Line2D([0], [0], color="#555", marker="P", linestyle="none", markersize=9,
               label=_t("UPTREND_CONFIRMED（B/Cの警戒足有効化日）", "UPTREND_CONFIRMED")),
        Line2D([0], [0], color="#555", marker="v", linestyle="none", markersize=8,
               label=_t("警戒陰線", "Warning candle")),
        Line2D([0], [0], color="#555", linestyle=":", label=_t("warning_low", "warning_low")),
        Line2D([0], [0], color="#555", linestyle="-.",
               label=_t("reference_high（保有中最高値。3案とも同じ定義）",
                        "reference_high")),
        Line2D([0], [0], color="#555", marker="x", linestyle="none", markersize=8,
               label=_t("warning_low割れ（CASE2の利確候補）", "warning_low broken")),
        Line2D([0], [0], color="#555", marker="^", linestyle="none", markersize=8,
               label=_t("REHIGH_CONFIRMED", "REHIGH_CONFIRMED")),
        Line2D([0], [0], color="#555", marker="o", linestyle="none", markersize=6,
               markerfacecolor="white", label=_t("押し安値確定", "Confirmed pullback low")),
        Line2D([0], [0], color="#555", marker="D", linestyle="none", markersize=6,
               label=_t("active_stop引き上げ", "Stop raise")),
        Line2D([0], [0], color="#555", marker="X", linestyle="none", markersize=9,
               label=_t("CASE2 仮想EXIT", "CASE2 exit")),
        Line2D([0], [0], color="#555", marker="*", linestyle="none", markersize=12,
               label=_t("CASE3 仮想EXIT", "CASE3 exit")),
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
                 f"警戒足の有効化タイミング比較（A/B/C）")
        note = ("※検証用チャート。3案の違いは「WARNINGへ入る条件」だけで、"
                "reference_high の定義・warning_low割れ後の扱い・押し安値・"
                "トレーリングは同一。A/B/C はいずれも現行の文章ルールの読み方であって"
                "正式ルールではなく、成績で採否を決めるためのものではない。")
    else:
        title = f"{base.code}  signal {base.signal_date}  warning-start A/B/C"
        note = ("Research chart. Only the WARNING entry condition differs between "
                "A/B/C. None of them is an official rule.")

    fig.suptitle(title, x=0.01, ha="left", fontsize=12.5, fontweight="bold")
    ax.set_title(note, loc="left", fontsize=8, color="#555555")
    ax.grid(alpha=0.22)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles + legend_extra + shape_legend,
               labels + [h.get_label() for h in legend_extra + shape_legend],
               loc="lower center", ncol=4, fontsize=7.6, framealpha=0.93,
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

    fig.tight_layout(rect=(0, 0.235, 1, 0.965))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=105, facecolor="white")
    plt.close(fig)
    return out_path


# --- 代表例の選定（§13）--------------------------------------------------------

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("a_early_bc_rise", "Aは突破翌日の陰線をWARNINGにするが、B/Cならその後も上昇した例"),
    ("b_natural", "Bが自然に機能した例（確認 → 調整 → 押し安値確定）"),
    ("c_natural", "Cが自然に機能した例"),
    ("bc_too_late", "B/CともWARNINGが遅すぎた例（確認前に落ちた）"),
    ("bc_diverge", "BとCでWARNING日が大きく違う例"),
    ("delay_enables_trail", "警戒開始を遅らせたことでtrailが成立した例"),
    ("delay_loses_trail", "逆に、遅らせたことでtrailが成立しなくなった例"),
    ("still_stuck", "遅らせてもSTUCK_IN_WARNINGになる例"),
    ("gain10", "+10%以上伸びた代表ケース"),
    ("back_to_initial_stop", "初期STOPまで戻った代表ケース"),
)


def select_representatives(
    runs: dict[str, ws.VariantRun], per_category: int = 2
) -> dict[str, list[ws.EventKey]]:
    a, b, c = (runs[v].by_key for v in sm.VARIANTS)
    ref = ws.reference_max_gain(runs)
    keys = [k for k, e in a.items() if e.entry_available]

    def pick(cands: list[ws.EventKey], key=None, limit: int = per_category):
        ordered = sorted(cands, key=key) if key else cands
        out: list[ws.EventKey] = []
        seen: set[str] = set()
        for k in ordered:
            if len(out) >= limit:
                break
            if k[0] in seen:
                continue
            out.append(k)
            seen.add(k[0])
        return out

    sel: dict[str, list[ws.EventKey]] = {}

    early = ws.extract_early_warning_cases(runs)
    seen_e: list[ws.EventKey] = []
    for ec in early:
        k = (ec.code, ec.signal_date)
        if k not in seen_e:
            seen_e.append(k)
    sel["a_early_bc_rise"] = pick(seen_e, limit=per_category + 1)

    def natural(run_map: dict[ws.EventKey, sm.SMEvent]) -> list[ws.EventKey]:
        return [
            k for k, e in run_map.items()
            if e.uptrend_confirmed_date is not None and e.warnings
            and (e.rehigh_count >= 1 or e.stop_raise_count >= 1)
        ]

    sel["b_natural"] = pick(natural(b), key=lambda k: -b[k].stop_raise_count)
    sel["c_natural"] = pick(natural(c), key=lambda k: -c[k].stop_raise_count)

    late = ws.extract_late_warning_cases(runs)
    late_keys: list[ws.EventKey] = []
    for lc in late:
        k = (lc.code, lc.signal_date)
        if k not in late_keys:
            late_keys.append(k)
    sel["bc_too_late"] = pick(late_keys)

    diverge = []
    for k in keys:
        eb, ec = b.get(k), c.get(k)
        if eb is None or ec is None:
            continue
        wb = eb.warnings[0].day_offset if eb.warnings else None
        wc = ec.warnings[0].day_offset if ec.warnings else None
        if (wb is None) != (wc is None) or (
            wb is not None and wc is not None and wb != wc
        ):
            diverge.append(k)
    sel["bc_diverge"] = pick(
        diverge,
        key=lambda k: -abs(
            (c[k].warnings[0].day_offset if c[k].warnings else 999)
            - (b[k].warnings[0].day_offset if b[k].warnings else 999)
        ),
    )

    sel["delay_enables_trail"] = pick([
        k for k in keys
        if a[k].stop_raise_count == 0
        and (b[k].stop_raise_count >= 1 or c[k].stop_raise_count >= 1)
    ])
    sel["delay_loses_trail"] = pick(
        [
            k for k in keys
            if a[k].stop_raise_count >= 1
            and (b[k].stop_raise_count == 0 or c[k].stop_raise_count == 0)
        ],
        key=lambda k: -(ref.get(k) or 0.0),
    )

    sel["still_stuck"] = pick([
        k for k in keys
        if "STUCK_IN_WARNING" in b[k].flags or "STUCK_IN_WARNING" in c[k].flags
    ], key=lambda k: -max(
        (w.days_held_in_warning_after_low_break or 0)
        for e in (b[k], c[k]) for w in e.warnings
    ) if (b[k].warnings or c[k].warnings) else 0)

    sel["gain10"] = pick([k for k in keys if (ref.get(k) or 0.0) >= 10.0],
                         key=lambda k: -(ref.get(k) or 0.0))

    sel["back_to_initial_stop"] = pick(
        [
            k for k in keys
            if any(
                run[k].cases[sm.CASE3].exit_type == sm.X_INITIAL_STOP_AFTER_BREAK
                for run in (a, b, c)
            )
            and (ref.get(k) or 0.0) >= 5.0
        ],
        key=lambda k: -(ref.get(k) or 0.0),
    )

    return sel


def render_all(
    runs: dict[str, ws.VariantRun], price_map: dict, cfg, out_dir: Path,
    per_category: int = 2,
) -> dict[str, list[tuple[dict[str, sm.SMEvent], Path]]]:
    charts_dir = out_dir / "representative_charts"
    selection = select_representatives(runs, per_category)
    by_variant = {v: runs[v].by_key for v in sm.VARIANTS if v in runs}
    result: dict[str, list[tuple[dict[str, sm.SMEvent], Path]]] = {}
    for key, _label in CATEGORIES:
        rendered: list[tuple[dict[str, sm.SMEvent], Path]] = []
        for k in selection.get(key, []):
            evs = {v: m[k] for v, m in by_variant.items() if k in m}
            if sm.VARIANT_A not in evs:
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
