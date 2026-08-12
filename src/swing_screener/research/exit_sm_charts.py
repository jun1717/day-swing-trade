"""EXIT 状態機械（exit_state_machine.py）検証用の注釈付きチャート（spec §18）。

`research/exit_charts.py`（前回の exit_study 用）とは表示要素が違うため
別実装にする。本番の charting.py には一切触れない。

表示するもの:
    ENTRYシグナル日 / 仮想ENTRY日・翌日始値 / 元レンジ上限・下限 / 初期STOP /
    MA25 / 上限終値突破日（INITIAL_HOLD→TREND_HOLD） /
    holding_high・active_stop のステップ推移（active_stop が最重要） /
    WARNING エピソードごとの警戒足・warning_low・reference_high・
    warning_low割れ（CASE2候補）・REHIGH_CONFIRMED・押し安値確定 /
    active_stop 引き上げの注記 / CASE1〜3 の仮想EXIT。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

from swing_screener.models import OHLCVBar, PriceSeries  # noqa: E402
from swing_screener.research.exit_state_machine import (  # noqa: E402
    CASE1,
    CASE2,
    CASE3,
    CASES,
    PATH_LABELS_JA,
    SMEvent,
    X_TRAIL_STOP,
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


def _ma(bars: list[OHLCVBar], period: int) -> list[float | None]:
    closes = [b.close for b in bars]
    return [
        None if i + 1 < period else sum(closes[i + 1 - period : i + 1]) / period
        for i in range(len(closes))
    ]


CASE_SHORT = {CASE1: "CASE1", CASE2: "CASE2", CASE3: "CASE3"}


def render(
    ev: SMEvent,
    series: PriceSeries,
    cfg,
    out_path: Path,
    *,
    before: int = 35,
    after_pad: int = 8,
) -> Path:
    """1件の状態機械追跡結果を、シグナル前から追跡終端後まで描く。"""
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
        2, 1, figsize=(14, 8.6), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]}
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

    # 上限を終値突破（INITIAL_HOLD -> TREND_HOLD）
    if ev.upper_close_break_date and ev.upper_close_break_date in by_date:
        bx = by_date[ev.upper_close_break_date]
        price = ev.upper_close_break_price
        if price is not None:
            ax.plot([bx], [price], marker="^", markersize=11, color="#7b5ea7",
                    zorder=8, label=_t("終値で上限突破（TREND_HOLDへ）",
                                       "Close broke range high (-> TREND_HOLD)"))

    # holding_high / active_stop の日次ステップ推移（ev.daily 由来）
    if ev.daily:
        hh_x: list[int] = []
        hh_y: list[float] = []
        as_x: list[int] = []
        as_y: list[float] = []
        for ds in ev.daily:
            xi = by_date.get(ds.date)
            if xi is None:
                continue
            hh_x.append(xi)
            hh_y.append(ds.holding_high)
            as_x.append(xi)
            as_y.append(ds.active_stop)
        # 最後の値を描画範囲の右端まで伸ばし、その水準が続いていることを見せる
        if hh_x and hh_x[-1] < len(window) - 1:
            hh_x.append(len(window) - 1)
            hh_y.append(hh_y[-1])
        if as_x and as_x[-1] < len(window) - 1:
            as_x.append(len(window) - 1)
            as_y.append(as_y[-1])
        if hh_x:
            ax.step(hh_x, hh_y, where="post", color="#495057", linewidth=1.0,
                    alpha=0.85, zorder=5,
                    label=_t("保有中の最高値（holding_high）", "Holding high (running max)"))
        if as_x:
            # 最重要: 実際に有効なトレーリングSTOP。太く目立たせる。
            ax.step(as_x, as_y, where="post", color="#c2255c", linewidth=2.4,
                    zorder=7,
                    label=_t("active_stop（実際に有効なトレーリングSTOP）",
                             "active_stop (trailing stop actually in effect)"))

    # WARNING エピソード
    labelled: set[str] = set()
    for w in ev.warnings:
        wx = by_date.get(w.date)
        if wx is None:
            continue
        end_date = w.low_break_date or w.resolved_date
        wx_end = by_date.get(end_date) if end_date else None
        if wx_end is None:
            wx_end = len(window) - 1

        ax.plot([wx], [w.high * 1.004], marker="v", markersize=8, color="#e08a1e",
                zorder=8,
                label=(_t("警戒陰線（TREND_HOLD中の最初の陰線）",
                          "Warning candle (first bearish bar in TREND_HOLD)")
                       if "warning_v" not in labelled else None))
        labelled.add("warning_v")

        ax.hlines(w.low, wx, wx_end, color="#e08a1e", linewidth=1.0, linestyle=":",
                  alpha=0.85, zorder=6,
                  label=(_t("warning_low（警戒陰線の安値）", "warning_low")
                         if "warning_low_line" not in labelled else None))
        labelled.add("warning_low_line")

        ax.hlines(w.reference_high, wx, wx_end, color="#0b7285", linewidth=1.0,
                  linestyle="-.", alpha=0.8, zorder=6,
                  label=(_t("reference_high（再高値更新に必要な水準）",
                            "reference_high (level needed for REHIGH)")
                         if "reference_high_line" not in labelled else None))
        labelled.add("reference_high_line")

        if w.low_break_date is not None:
            lbx = by_date.get(w.low_break_date)
            if lbx is not None:
                ax.plot([lbx], [w.low], marker="x", markersize=9, color="#e08a1e",
                        markeredgewidth=2, zorder=9,
                        label=(_t("warning_low割れ（CASE2の利確候補）",
                                  "warning_low broken (CASE2 exit candidate)")
                               if "low_break_x" not in labelled else None))
                labelled.add("low_break_x")

        if w.rehigh_date is not None:
            rhx = by_date.get(w.rehigh_date)
            if rhx is not None:
                ax.plot([rhx], [w.reference_high], marker="^", markersize=10,
                        color="#0b7285", zorder=9,
                        label=(_t("REHIGH_CONFIRMED（reference_high再突破）",
                                  "REHIGH_CONFIRMED")
                               if "rehigh_tri" not in labelled else None))
                labelled.add("rehigh_tri")

        if w.new_swing_low_date is not None and w.new_swing_low_candidate is not None:
            slx = by_date.get(w.new_swing_low_date)
            if slx is not None:
                ax.plot([slx], [w.new_swing_low_candidate], marker="o", markersize=6,
                        markerfacecolor="white", markeredgecolor="#495057",
                        markeredgewidth=1.4, zorder=9,
                        label=(_t("押し安値確定（new_swing_low_candidate）",
                                  "Confirmed pullback low")
                               if "swing_low_o" not in labelled else None))
                labelled.add("swing_low_o")

    # active_stop 引き上げ（StopUpdate）の注記
    for su in ev.stop_updates:
        sux = by_date.get(su.stop_update_date)
        if sux is None:
            continue
        ax.plot([sux], [su.new_stop], marker="D", markersize=6, color="#c2255c",
                zorder=9,
                label=(_t("active_stop引き上げ確定日", "Stop raise confirmed")
                       if "stop_raise_d" not in labelled else None))
        labelled.add("stop_raise_d")
        ax.annotate(f"→{su.new_stop:.1f}", xy=(sux, su.new_stop),
                    xytext=(3, 6), textcoords="offset points", fontsize=7,
                    color="#c2255c", zorder=11,
                    bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=1))

    # CASE1〜3 の仮想EXIT
    case_markers = {CASE1: ("s", "#b2242f"), CASE2: ("D", "#e08a1e"), CASE3: ("*", "#c2255c")}
    for c in CASES:
        r = ev.cases.get(c)
        if r is None or r.exit_date is None or r.exit_reference_price is None:
            continue
        cx = by_date.get(r.exit_date)
        if cx is None:
            continue
        marker, color = case_markers[c]
        ax.plot([cx], [r.exit_reference_price], marker=marker, markersize=13,
                color=color, markeredgecolor="#222", markeredgewidth=0.6, zorder=10,
                label=f"{CASE_SHORT[c]} EXIT: {r.exit_type}")

    # 保有区間を淡くシェード
    if ev.entry_price is not None:
        hold_right = min(len(window) - 1, entry_i - start + ev.bars_tracked - 1)
        ax.axvspan(entry_i - start, hold_right, color="#4a90d9", alpha=0.07, zorder=1)

    for i, b in enumerate(window):
        vax.bar(i, b.volume, width=0.62,
                color=(up_c if b.close >= b.open else down_c), alpha=0.55)
    vax.axvline(sig_x, color="#111", linewidth=1.3, alpha=0.6)

    def _fmt_ret(v: float | None) -> str:
        return f"{v:+.1f}%" if v is not None else "－"

    ret_line = " / ".join(
        f"{CASE_SHORT[c]} {_fmt_ret(ev.cases[c].approximate_return_pct)}" for c in CASES
    )
    path_txt = PATH_LABELS_JA.get(ev.path_label, ev.path_label)
    if _JP_FONT:
        title = (f"{ev.code} {ev.name}　シグナル {ev.signal_date}　{path_txt}　{ret_line}")
        note = ("※検証用チャート。EXITルールを状態機械として再現できるかの検証であり、"
                "CASE2/CASE3は現行の文章ルールの読み方であって正式ルールではない。"
                "確定ルールとして変更していないのは initial_stop = range_lower×0.995 のみ。")
    else:
        title = f"{ev.code}  signal {ev.signal_date}  {ev.path_label}  {ret_line}"
        note = ("Research verification of whether the written EXIT rules can be "
                "reproduced as a state machine. CASE2/CASE3 are readings of the "
                "rules, not official rules. Only initial_stop = range_lower*0.995 "
                "is a confirmed rule.")

    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold")
    ax.set_title(note, loc="left", fontsize=8, color="#555555")
    ax.grid(alpha=0.25)
    # 凡例は価格パネルの外（出来高パネルの下）へ。要素数が多く、パネル内に置くと
    # ローソク足やMA・ステップ線に重なってしまうため。
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, fontsize=8,
               framealpha=0.92, bbox_to_anchor=(0.5, 0.012))
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

    fig.tight_layout(rect=(0, 0.16, 1, 0.96))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=105, facecolor="white")
    plt.close(fig)
    return out_path


# --- 代表例の選定（spec §18）---------------------------------------------------

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("no_breakout", "上限突破前に失敗した例"),
    ("warning_low_break", "上限突破 → WARNING → warning_low割れ"),
    ("rehigh", "上限突破 → WARNING → 再高値更新"),
    ("trail_exit", "再高値更新 → trail引き上げ → EXIT"),
    ("multi_trail", "trail stopを複数回引き上げた例"),
    ("case23_protects", "CASE1では吐き出すがCASE2/3なら守れる例"),
    ("warning_too_early", "WARNINGが早すぎて伸ばせない例"),
    ("ambiguous", "warning_lowとreference_highを同日に突破した曖昧例"),
)

# 「吐き出す」を「CASE2/3の方がCASE1より有利」の絶対差として扱うが、
# 誤差レベルの差をノイズとして拾わないよう最低限の閾値を置く（売買判定ではない）。
_CASE23_MEANINGFUL_DIFF_PT = 1.0


def _case23_protect_diff(ev: SMEvent) -> float | None:
    c1 = ev.cases.get(CASE1)
    if c1 is None or c1.max_gain_pct is None or c1.max_gain_pct < 5.0:
        return None
    if c1.approximate_return_pct is None:
        return None
    others = [
        ev.cases[c].approximate_return_pct for c in (CASE2, CASE3)
        if ev.cases.get(c) is not None and ev.cases[c].approximate_return_pct is not None
    ]
    if not others:
        return None
    diff = max(others) - c1.approximate_return_pct
    return diff if diff >= _CASE23_MEANINGFUL_DIFF_PT else None


def _warning_too_early_left_on_table(ev: SMEvent) -> float | None:
    c3 = ev.cases.get(CASE3)
    c2 = ev.cases.get(CASE2)
    if c3 is None or c2 is None:
        return None
    if c3.max_gain_pct is None or c2.approximate_return_pct is None:
        return None
    return c3.max_gain_pct - c2.approximate_return_pct


def select_representatives(
    events: list[SMEvent], per_category: int = 3
) -> dict[str, list[SMEvent]]:
    """カテゴリごとの代表例。銘柄が偏らないよう1銘柄1件に絞る。"""
    entered = [e for e in events if e.entry_available]

    def pick(cands: list[SMEvent], key=None, limit: int = per_category):
        ordered = sorted(cands, key=key) if key else cands
        out: list[SMEvent] = []
        seen: set[str] = set()
        for e in ordered:
            if len(out) >= limit:
                break
            if e.code in seen:
                continue
            out.append(e)
            seen.add(e.code)
        return out

    sel: dict[str, list[SMEvent]] = {}

    sel["no_breakout"] = pick(
        [e for e in entered if not e.reached_trend_hold],
        key=lambda e: (
            e.cases[CASE3].exit_day_offset
            if e.cases.get(CASE3) is not None and e.cases[CASE3].exit_day_offset is not None
            else 10**9
        ),
    )

    sel["warning_low_break"] = pick(
        [
            e for e in entered
            if any(
                w.resolution == "low_break"
                or (w.low_break_date is not None and w.rehigh_date is None)
                for w in e.warnings
            )
        ],
    )

    sel["rehigh"] = pick([e for e in entered if e.rehigh_count >= 1])

    sel["trail_exit"] = pick(
        [
            e for e in entered
            if e.stop_raise_count >= 1
            and e.cases.get(CASE3) is not None
            and e.cases[CASE3].exit_type == X_TRAIL_STOP
        ],
    )

    sel["multi_trail"] = pick([e for e in entered if e.stop_raise_count >= 2])

    case23 = [e for e in entered if _case23_protect_diff(e) is not None]
    sel["case23_protects"] = pick(case23, key=lambda e: -_case23_protect_diff(e))

    warning_too_early = [
        e for e in entered
        if "WARNING_TOO_EARLY" in e.flags and _warning_too_early_left_on_table(e) is not None
    ]
    sel["warning_too_early"] = pick(
        warning_too_early, key=lambda e: -_warning_too_early_left_on_table(e)
    )

    sel["ambiguous"] = pick(
        [e for e in entered if e.ambiguous_warning_days]
        + [e for e in entered if e.ambiguous_stop_days]
    )

    return sel


def render_all(
    events: list[SMEvent], price_map: dict, cfg, out_dir: Path,
    per_category: int = 3,
) -> dict[str, list[tuple[SMEvent, Path]]]:
    charts_dir = out_dir / "representative_charts"
    selection = select_representatives(events, per_category)
    result: dict[str, list[tuple[SMEvent, Path]]] = {}
    for key, _label in CATEGORIES:
        picked = selection.get(key, [])
        rendered: list[tuple[SMEvent, Path]] = []
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
