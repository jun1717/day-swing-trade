"""`reference_high` の決め方の比較（reference_high_study.py）用のチャート（§16 / §17）。

1 枚に 5 案を重ねて描く。値動きも ENTRY も警戒足の出方も（RH-A の警戒足を基準に）
同じで、違うのは「どの水準を超えたら調整終了と見なすか」だけ。そこだけが
読み取れるように、5 つの reference_high 候補を別色の水平線で並べる。

表示するもの（§17）:
    ENTRY / 元レンジ上下限 / 初期STOP / BREAKOUT / WARNING / warning_low /
    RH-A〜RH-E の reference_high / 案ごとの REHIGH / new_swing_low_candidate /
    案ごとの trail stop（階段） / 案ごとの仮想EXIT / その後の最高値

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
from swing_screener.research import reference_high_study as rhs  # noqa: E402

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
    sm.RH_HOLDING: {"color": "#8a8f98", "lw": 3.6, "alpha": 0.9, "marker": "s",
                    "ls": (0, (6, 2))},
    sm.RH_WARNING_HIGH: {"color": "#d64545", "lw": 2.6, "alpha": 0.95, "marker": "X",
                         "ls": (0, (5, 2))},
    sm.RH_PRE_CLOSE: {"color": "#2b6cb0", "lw": 1.8, "alpha": 0.95, "marker": "P",
                      "ls": (0, (4, 2))},
    sm.RH_WARNING_OPEN: {"color": "#2e8b74", "lw": 1.1, "alpha": 1.0, "marker": "*",
                         "ls": (0, (3, 2))},
    sm.RH_PRE_HIGH: {"color": "#b8860b", "lw": 0.8, "alpha": 1.0, "marker": "D",
                     "ls": (0, (2, 2))},
}

# RefHighSnapshot 上での 5 案の値の取り出し方
_LEVEL_ATTR = {
    sm.RH_HOLDING: "holding_high",
    sm.RH_WARNING_HIGH: "warning_high",
    sm.RH_PRE_CLOSE: "pre_warning_close_high",
    sm.RH_WARNING_OPEN: "warning_open",
    sm.RH_PRE_HIGH: "pre_warning_high",
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
    """同一イベントの 5 案を 1 枚に重ねて描く。"""
    base = evs[sm.RH_HOLDING]
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
        2, 1, figsize=(15, 9.8), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]}
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
    ax.axhline(base.range_upper, color="#7b5ea7", linewidth=1.4, zorder=4,
               label=_t("元レンジ上限", "Range high"))
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

    # --- 警戒足と warning_low（今回は 5 案とも同じ定義。変更していない）---
    for w in base.warnings:
        wx = by_date.get(w.date)
        if wx is None:
            continue
        ax.plot([wx], [w.high + span * 0.015], marker="v", markersize=10, color="#111",
                markeredgecolor="#fff", markeredgewidth=0.6, zorder=10)
        ax.hlines(w.low, wx, len(window) - 1, color="#111", linewidth=1.2,
                  linestyle=":", alpha=0.8, zorder=6)
        ax.annotate(f"warning_low {w.low:.1f}",
                    xy=(wx, w.low), xytext=(4, -12), textcoords="offset points",
                    fontsize=7, color="#111", zorder=12,
                    bbox=dict(facecolor="white", alpha=0.8, edgecolor="none", pad=1))

    # --- §17 の中心: 5 案の reference_high を同じ警戒足の上に並べる ---
    # 値はすべて RH-A の記録に入っている（案に依存しない観測値）。
    for s in base.ref_highs:
        wx = by_date.get(s.warning_date)
        if wx is None:
            continue
        levels = {r: getattr(s, _LEVEL_ATTR[r]) for r in rhs.RULES}
        # 同じ高さに重なった案はラベルをまとめる
        groups: dict[float, list[str]] = {}
        for r, v in levels.items():
            hit = next((k for k in groups if abs(k - v) < span * 0.002), None)
            groups.setdefault(hit if hit is not None else v, []).append(r)
        for v, rules in groups.items():
            top = rules[0]
            st = RULE_STYLE[top]
            ax.hlines(v, wx, len(window) - 1, color=st["color"], linewidth=1.5,
                      linestyle=st["ls"], alpha=0.9, zorder=7)
            tag = "/".join(sm.RH_RULE_SHORT_JA[r].split()[0] for r in rules)
            ax.annotate(f"{tag} {v:.1f}", xy=(len(window) - 1, v), xytext=(-4, 3),
                        textcoords="offset points", fontsize=7, ha="right",
                        color=st["color"], zorder=12,
                        bbox=dict(facecolor="white", alpha=0.85, edgecolor="none",
                                  pad=1))
        if s.order_ambiguous:
            ax.annotate(
                _t("同日にREHIGHと終値割れ（順序不明）", "same-day rehigh/exit"),
                xy=(wx, s.warning_low - span * 0.05), fontsize=7.5, color="#c026d3",
                ha="center", zorder=14,
                bbox=dict(facecolor="white", alpha=0.9, edgecolor="#c026d3",
                          linewidth=0.5, pad=1.5),
            )

    legend_extra: list[Line2D] = []
    for rule in rhs.RULES:
        ev = evs.get(rule)
        if ev is None:
            continue
        st = RULE_STYLE[rule]
        col, lw = st["color"], st["lw"]

        # active_stop の階段（その案が降りた日まで）
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

        # REHIGH と押し安値候補
        for s in ev.ref_highs:
            if s.rehigh_date in by_date and s.rehigh_high is not None:
                ax.plot([by_date[s.rehigh_date]], [s.rehigh_high], marker="^",
                        markersize=9, color=col, markeredgecolor="#222",
                        markeredgewidth=0.6, zorder=12)
            if s.new_swing_low_date in by_date and s.new_swing_low_candidate is not None:
                sx = by_date[s.new_swing_low_date]
                ax.plot([sx], [s.new_swing_low_candidate], marker="_", markersize=14,
                        markeredgewidth=2.4, color=col, zorder=12)

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
        exd = r.exit_date.strftime("%m/%d") if r.exit_date else "－"
        rehighs = sum(1 for s in ev.ref_highs if s.rehigh_date is not None)
        legend_extra.append(
            Line2D([0], [0], color=col, linewidth=lw, marker=st["marker"],
                   markersize=8, markeredgecolor="#222", markeredgewidth=0.4,
                   label=(f"{sm.RH_RULE_SHORT_JA[rule]}: "
                          f"REHIGH {rehighs} / "
                          f"{_t('STOP引上', 'raises')} {ev.stop_raise_count} / "
                          f"{_t('EXIT', 'exit')} {exd} {r.exit_type} / "
                          + (f"{ret:+.1f}%" if ret is not None else "－")))
        )

    # --- 最も早く降りた案の EXIT 後に付けた最高値（§17「その後の最高値」）---
    offs = [
        evs[r].path_result.exit_day_offset for r in rhs.RULES
        if r in evs and evs[r].path_result.exit_day_offset is not None
    ]
    if offs and base.entry_index is not None:
        first_off = min(offs)
        lo_i = base.entry_index + first_off + 1
        hi_i = min(end_i, base.entry_index + tracked - 1 + after_pad)
        post = [b for b in bars[lo_i : hi_i + 1] if b.date in by_date]
        if post:
            best = max(post, key=lambda b: b.high)
            bx = by_date[best.date]
            ax.plot([bx], [best.high], marker="^", markersize=12, color="#c026d3",
                    markeredgecolor="#222", markeredgewidth=0.6, zorder=13)
            ax.annotate(
                f"{_t('最初のEXIT後の最高値', 'max high after first exit')} {best.high:.1f}",
                xy=(bx, best.high), xytext=(-6, 12), textcoords="offset points",
                fontsize=7.5, color="#c026d3", ha="right", zorder=14,
                bbox=dict(facecolor="white", alpha=0.86, edgecolor="#c026d3",
                          linewidth=0.4, pad=1.5),
            )

    shape_legend = [
        Line2D([0], [0], color="#111", marker="v", linestyle="none", markersize=9,
               markerfacecolor="#111", label=_t("警戒陰線", "Warning candle")),
        Line2D([0], [0], color="#111", linestyle=":", label="warning_low"),
        Line2D([0], [0], color="#555", marker="^", linestyle="none", markersize=8,
               label=_t("REHIGH_CONFIRMED（案ごと）", "Rehigh (per rule)")),
        Line2D([0], [0], color="#555", marker="_", linestyle="none", markersize=12,
               markeredgewidth=2.4,
               label=_t("new_swing_low_candidate", "Swing low candidate")),
        Line2D([0], [0], color="#555", marker="D", linestyle="none", markersize=6,
               label=_t("trail stop 引き上げ（翌営業日から有効）", "Stop raise")),
        Line2D([0], [0], color="#c026d3", marker="^", linestyle="none", markersize=9,
               label=_t("最初のEXIT後に付けた最高値", "max high after first exit")),
    ]
    for rule in rhs.RULES:
        st = RULE_STYLE[rule]
        shape_legend.append(
            Line2D([0], [0], color=st["color"], linestyle=st["ls"], linewidth=1.5,
                   label=f"reference_high {sm.RH_RULE_SHORT_JA[rule]}")
        )

    if base.entry_price is not None:
        hold_right = min(len(window) - 1, entry_i - start + tracked - 1)
        ax.axvspan(entry_i - start, hold_right, color="#4a90d9", alpha=0.06, zorder=1)

    for i, b in enumerate(window):
        vax.bar(i, b.volume, width=0.62,
                color=(up_c if b.close >= b.open else down_c), alpha=0.55)
    vax.axvline(sig_x, color="#111", linewidth=1.2, alpha=0.6)

    if _JP_FONT:
        title = (f"{base.code} {base.name}　シグナル {base.signal_date}　"
                 f"reference_high の決め方の比較（RH-A/B/C/D/E）")
        note = ("※検証用チャート。5案の違いは「何を超えたら調整終了・上昇再開と見なすか」だけ。"
                "ENTRY・WARNING開始条件（VARIANT A）・warning_low の扱い（CLOSE_BREAK）・\n"
                "　押し安値・trail=押し安値×0.995・初期STOPは5案とも同一。"
                "STOPは下げず、引き上げは翌営業日から有効。いずれも正式ルールではない。")
    else:
        title = f"{base.code}  signal {base.signal_date}  reference_high rules"
        note = ("Research chart. Only the reference_high definition differs. "
                "None of them is an official rule.")

    fig.suptitle(title, x=0.01, ha="left", fontsize=12.5, fontweight="bold")
    ax.set_title(note, loc="left", fontsize=8, color="#555555")
    ax.grid(alpha=0.22)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles + legend_extra + shape_legend,
               labels + [h.get_label() for h in legend_extra + shape_legend],
               loc="lower center", ncol=3, fontsize=7.4, framealpha=0.93,
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

    fig.tight_layout(rect=(0, 0.29, 1, 0.965))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=105, facecolor="white")
    plt.close(fig)
    return out_path


# --- 代表例の選定（§16）--------------------------------------------------------

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("a_no_rehigh_b_does",
     "1. RH-A では REHIGH しないが RH-B では自然に再上昇を拾える例"),
    ("a_stop_never_raised",
     "2. RH-A では STOP が初期値のまま利益を吐き出す例"),
    ("b_trail_too_early",
     "3. RH-B では trail 成立するが、早すぎてその後さらに大きく上昇する例"),
    ("c_natural", "4. RH-C が終値ベースで自然に見える例"),
    ("d_too_loose", "5. RH-D が緩すぎる例"),
    ("no_rehigh_any", "6. どの reference_high でも REHIGH しない例"),
    ("gain10", "7. +10%以上まで伸びた例"),
    ("multi_trail", "8. trail stop を複数回引き上げられる例"),
    ("loss_to_profit", "9. reference_high 変更で損失EXITから利益EXITへ変わる例"),
    ("profit_shrunk", "10. reference_high 変更で逆に利益を小さくしてしまう例"),
    ("ambiguous_order", "11. 同日 REHIGH / warning_low 終値割れで順序不明の例"),
    ("hard_to_judge", "12. 人間が見てもどの reference_high が自然か判断しにくい例"),
)


def select_representatives(
    runs: dict[str, rhs.RHRun],
    frames: dict[rhs.EventKey, rhs.Frame],
    per_category: int = 2,
) -> dict[str, list[rhs.EventKey]]:
    a = runs[sm.RH_HOLDING].by_key
    b = runs[sm.RH_WARNING_HIGH].by_key
    c = runs[sm.RH_PRE_CLOSE].by_key
    d = runs[sm.RH_WARNING_OPEN].by_key
    avail = rhs.available_max_gain(frames)
    keys = [k for k, e in a.items() if e.entry_available]

    def ret(run: dict, k: rhs.EventKey) -> float | None:
        ev = run.get(k)
        return ev.path_result.approximate_return_pct if ev else None

    def best_worst(k: rhs.EventKey) -> tuple[float, float]:
        vals = [
            v for v in (ret(runs[r].by_key, k) for r in rhs.RULES) if v is not None
        ]
        return (max(vals), min(vals)) if vals else (0.0, 0.0)

    def pick(cands: list[rhs.EventKey], key=None, limit: int = per_category):
        ordered = sorted(cands, key=key) if key else list(cands)
        out: list[rhs.EventKey] = []
        seen: set[str] = set()
        for k in ordered:
            if len(out) >= limit:
                break
            if k[0] in seen:
                continue
            out.append(k)
            seen.add(k[0])
        return out

    sel: dict[str, list[rhs.EventKey]] = {}

    sel["a_no_rehigh_b_does"] = pick(
        [k for k in keys if a[k].rehigh_count == 0 and b[k].rehigh_count >= 1],
        key=lambda k: -(avail.get(k) or 0.0),
    )
    sel["a_stop_never_raised"] = pick(
        [
            k for k in keys
            if a[k].stop_raise_count == 0 and (avail.get(k) or 0.0) >= 5.0
        ],
        key=lambda k: -(avail.get(k) or 0.0),
    )
    sel["b_trail_too_early"] = pick(
        [
            k for k in keys
            if b[k].path_result.exit_type == sm.X_TRAIL_STOP
            and (avail.get(k) or 0.0) - (ret(b, k) or 0.0) >= 5.0
        ],
        key=lambda k: -((avail.get(k) or 0.0) - (ret(b, k) or 0.0)),
    )
    sel["c_natural"] = pick(
        [
            k for k in keys
            if c[k].stop_raise_count >= 1
            and (ret(c, k) or -99) >= max(
                (ret(runs[r].by_key, k) or -99) for r in rhs.RULES
            ) - 1e-9
        ],
        key=lambda k: -((ret(c, k) or 0.0) - (ret(a, k) or 0.0)),
    )
    sel["d_too_loose"] = pick(
        [
            k for k in keys
            if d[k].rehigh_count > a[k].rehigh_count
            and (ret(d, k) or 0.0) < (ret(a, k) or 0.0) - 1e-9
        ],
        key=lambda k: ((ret(d, k) or 0.0) - (ret(a, k) or 0.0)),
    ) or pick(
        [k for k in keys if d[k].rehigh_count > a[k].rehigh_count],
        key=lambda k: -(d[k].rehigh_count - a[k].rehigh_count),
    )
    sel["no_rehigh_any"] = pick(
        [k for k in keys if all(runs[r].by_key[k].rehigh_count == 0 for r in rhs.RULES)],
        key=lambda k: -(avail.get(k) or 0.0),
    )
    sel["gain10"] = pick(
        [k for k in keys if (avail.get(k) or 0.0) >= 10.0],
        key=lambda k: -(avail.get(k) or 0.0),
    )
    sel["multi_trail"] = pick(
        [k for k in keys if max(runs[r].by_key[k].stop_raise_count for r in rhs.RULES) >= 2],
        key=lambda k: -max(runs[r].by_key[k].stop_raise_count for r in rhs.RULES),
    )
    sel["loss_to_profit"] = pick(
        [k for k in keys if best_worst(k)[0] > 0 > best_worst(k)[1]],
        key=lambda k: -(best_worst(k)[0] - best_worst(k)[1]),
    )
    sel["profit_shrunk"] = pick(
        [
            k for k in keys
            if (ret(a, k) or 0.0) > 0
            and min(
                (ret(runs[r].by_key, k) or 0.0) for r in rhs.RULES
            ) < (ret(a, k) or 0.0) - 1e-9
        ],
        key=lambda k: (
            min((ret(runs[r].by_key, k) or 0.0) for r in rhs.RULES) - (ret(a, k) or 0.0)
        ),
    )
    sel["ambiguous_order"] = pick(
        [
            k for k in keys
            if any(runs[r].by_key[k].ambiguous_rehigh_exit_count for r in rhs.RULES)
        ],
        key=lambda k: -sum(
            runs[r].by_key[k].ambiguous_rehigh_exit_count for r in rhs.RULES
        ),
    )
    # 判断しにくい = 5 案の結果が割れているのに、どれも大きくは取れていない
    sel["hard_to_judge"] = pick(
        [
            k for k in keys
            if best_worst(k)[0] - best_worst(k)[1] >= 1.0
            and best_worst(k)[0] < (avail.get(k) or 0.0) * 0.4
        ],
        key=lambda k: -(avail.get(k) or 0.0),
    ) or pick(keys, key=lambda k: -(avail.get(k) or 0.0))

    return sel


def render_all(
    runs: dict[str, rhs.RHRun],
    frames: dict[rhs.EventKey, rhs.Frame],
    price_map: dict,
    cfg,
    out_dir: Path,
    per_category: int = 2,
) -> dict[str, list[tuple[dict[str, sm.SMEvent], Path]]]:
    charts_dir = out_dir / "representative_charts"
    selection = select_representatives(runs, frames, per_category)
    by_rule = {r: runs[r].by_key for r in rhs.RULES if r in runs}
    result: dict[str, list[tuple[dict[str, sm.SMEvent], Path]]] = {}
    for key, _label in CATEGORIES:
        rendered: list[tuple[dict[str, sm.SMEvent], Path]] = []
        for k in selection.get(key, []):
            evs = {r: m[k] for r, m in by_rule.items() if k in m}
            if sm.RH_HOLDING not in evs:
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
