"""日足チャートPNG生成（詳細画面・ChatGPTへの貼り付け用）。

DESIGN.md §10, §12.5 に対応する。

方針:
- matplotlib は Agg バックエンド固定（サーバー上で画面なしに描画するため）。
- 「このPNGをスクリーンショットしてChatGPTに貼ればそのまま相談できる」ことが目標なので、
  凡例・価格ラベルを画像内に埋め込み、情報がPNG単体で完結するようにする。
- MA計算はこのモジュール内で単純移動平均として独自に持つ（indicators.ma と同じ定義だが、
  charting は他モジュールの完成を待たずに単体テストできるよう意図的に依存を持たない）。
- result.range_ が None でも描画できる（レンジ関連の要素だけスキップする）。

2 種類を描く:
    render_daily_chart    候補銘柄のスクリーニング結果（レンジ・前日高値・損切り）
    render_holding_chart  保有銘柄（ENTRY価格・初期STOP・買ったときのレンジ・保有後最高値）
"""

from __future__ import annotations

import bisect
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager
from matplotlib import pyplot as plt
from matplotlib.patches import Patch, Rectangle

from swing_screener.models import OHLCVBar, PriceSeries, ScreenResult

# --- 日本語フォント -----------------------------------------------------------
# macOS で利用可能な日本語フォントを優先順に探す。見つからない場合は
# 全テキストを英数字表記へフォールバックし、豆腐（□□□）表示を避ける。

_JP_FONT_CANDIDATES = (
    "Hiragino Sans",
    "Hiragino Kaku Gothic ProN",
    "Apple SD Gothic Neo",
    "Yu Gothic",
    "Meiryo",
    "MS Gothic",
    "Noto Sans CJK JP",
    "Noto Sans JP",
    "IPAexGothic",
    "IPAGothic",
)


def _find_jp_font() -> str | None:
    try:
        available = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:
        return None
    for name in _JP_FONT_CANDIDATES:
        if name in available:
            return name
    return None


_JP_FONT_NAME = _find_jp_font()
if _JP_FONT_NAME:
    plt.rcParams["font.family"] = _JP_FONT_NAME
plt.rcParams["axes.unicode_minus"] = False


def _t(ja: str, en: str) -> str:
    """日本語フォントが使えるときだけ日本語を返す。使えなければ英数字にフォールバック。"""
    return ja if _JP_FONT_NAME else en


# --- 配色 ---------------------------------------------------------------------

UP_COLOR = "#c0392b"  # 陽線（日本式に赤系）
DOWN_COLOR = "#2f8f7f"  # 陰線（青緑系）
MA_COLOR = "#1f6fb2"
PREV_HIGH_COLOR = "#555555"
STOP_COLOR = "#8e1a1a"
UPPER_ZONE_COLOR = "#8e44ad"
LOWER_ZONE_COLOR = "#e08e0b"
RANGE_BOX_COLOR = "#555555"
TOUCH_MARK_COLOR = "#c0630b"
VOLUME_UP_COLOR = "#d9a5a1"
VOLUME_DOWN_COLOR = "#9fc6bf"
RANGE_HIGHLIGHT_COLOR = "#e08e0b"

# 保有銘柄チャート専用
ENTRY_COLOR = "#1a7f37"
EXIT_COLOR = "#6f42c1"
HOLDING_HIGH_COLOR = "#b8860b"


def _calc_ma(bars: Sequence[OHLCVBar], period: int) -> list[float | None]:
    """単純移動平均。indicators.ma.calc_ma_series と同じ定義（終値ベース）。"""
    closes = [b.close for b in bars]
    n = len(closes)
    ma: list[float | None] = [None] * n
    if period <= 0:
        return ma
    running = 0.0
    for i, c in enumerate(closes):
        running += c
        if i >= period:
            running -= closes[i - period]
        if i >= period - 1:
            ma[i] = running / period
    return ma


def _date_ticks(bars: Sequence[OHLCVBar], max_ticks: int = 9) -> tuple[list[int], list[str]]:
    if not bars:
        return [], []
    if len(bars) <= max_ticks:
        positions = list(range(len(bars)))
    else:
        step = max(1, len(bars) // (max_ticks - 1))
        positions = list(range(0, len(bars), step))
        last = len(bars) - 1
        if positions[-1] != last:
            # 最後のティックが直前と近すぎる場合は置き換え、遠ければ追加する
            # （右端でラベルが重なって潰れるのを防ぐ）
            if last - positions[-1] < step * 0.4:
                positions[-1] = last
            else:
                positions.append(last)
    labels = [bars[i].date.strftime("%m/%d") for i in positions]
    return positions, labels


def _volume_formatter(value: float, _pos: int) -> str:
    """出来高を万株単位で表示する（日本の出来高は桁が大きく1e6表記は読みにくいため）。"""
    if abs(value) >= 10_000:
        return f"{value / 10_000:,.0f}万"
    return f"{value:,.0f}"


def _place_right_labels(
    ax, labels: list[tuple[float, str, str]], y_min: float, y_max: float
) -> None:
    """右軸外に価格ラベルを並べる。値が近接する場合は重ならないよう上下にずらし、
    実際の価格からずれた分は細い引き出し線で示す（PNG単体で数値が読み取れることを優先）。
    """
    if not labels:
        return
    y_range = max(y_max - y_min, 1e-9)
    min_gap = y_range * 0.05
    items = sorted(labels, key=lambda t: t[0], reverse=True)
    placed: list[tuple[float, str, str, float]] = []
    for y, text, color in items:
        y_adj = y
        if placed and placed[-1][0] - y_adj < min_gap:
            y_adj = placed[-1][0] - min_gap
        placed.append((y_adj, text, color, y))
    for y_adj, text, color, y_orig in placed:
        if abs(y_adj - y_orig) > min_gap * 0.25:
            ax.plot(
                [1.0, 1.005], [y_orig, y_adj],
                transform=ax.get_yaxis_transform(),
                color=color, linewidth=0.6, alpha=0.55, clip_on=False,
            )
        ax.text(
            1.008, y_adj, text,
            transform=ax.get_yaxis_transform(),
            va="center", ha="left", fontsize=8.3, color=color,
            clip_on=False, fontweight="bold",
        )


def _clamp_index_for_date(dates: list, target) -> int:
    """target 日付に対応する表示範囲内のインデックスを返す（範囲外なら最寄りへクランプ）。"""
    if not dates:
        return 0
    idx = bisect.bisect_left(dates, target)
    return max(0, min(idx, len(dates) - 1))


def render_daily_chart(
    series: PriceSeries,
    result: ScreenResult,
    cfg: Any,
    exp: Any,
    output_path: Path,
    days: int = 120,
) -> Path:
    """日足チャートPNGを生成して output_path に保存する。

    ローソク足・MA25・検出レンジ（上限/下限zoneを帯で表示）・前日高値・損切りライン・
    出来高サブプロットを描画する。result.range_ が None の場合はレンジ関連要素のみ省く。
    """
    bars = list(series.bars)
    if not bars:
        raise ValueError(f"{series.code}: 株価データがありません（bars が空）")

    display_days = max(1, days)
    start_offset = max(0, len(bars) - display_days)
    display_bars = bars[start_offset:]
    n = len(display_bars)
    x = list(range(n))
    dates = [b.date for b in display_bars]

    ma_period = int(cfg.ma.period)
    ma_full = _calc_ma(bars, ma_period)
    ma_display = ma_full[start_offset:]

    # 図の横幅は本数に応じて可変にする（線が細くなって潰れるのを防ぐ）
    fig_width = min(22.0, max(11.0, n * 0.085 + 4.0))
    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(fig_width, 7.8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
        facecolor="white",
    )
    price_ax.set_facecolor("white")
    volume_ax.set_facecolor("white")

    range_ = result.range_
    range_start_disp = range_end_disp = None
    if range_ is not None:
        range_start_disp = _clamp_index_for_date(dates, range_.start_date)
        range_end_disp = _clamp_index_for_date(dates, range_.end_date)
        if range_start_disp > range_end_disp:
            range_start_disp, range_end_disp = range_end_disp, range_start_disp

    # --- レンジ関連の背景要素（ローソク足より先に描いて背面に置く）-------------
    legend_handles: list[Any] = []
    right_labels: list[tuple[float, str, str]] = []  # (y値, テキスト, 色) 。ylim確定後にまとめて配置

    if range_ is not None:
        # 出来高側：検出に使った日数だけをハイライト
        volume_ax.axvspan(
            range_start_disp - 0.5,
            range_end_disp + 0.5,
            color=RANGE_HIGHLIGHT_COLOR,
            alpha=0.16,
            linewidth=0,
            zorder=0,
        )
        # 価格側：同じ期間をごく薄くハイライトして出来高側との対応を示す
        price_ax.axvspan(
            range_start_disp - 0.5,
            range_end_disp + 0.5,
            color=RANGE_HIGHLIGHT_COLOR,
            alpha=0.06,
            linewidth=0,
            zorder=0,
        )

        # 上限zone・下限zoneは一本線ではなく帯（axhspan）として描き分ける
        price_ax.axhspan(
            range_.upper_zone_low, range_.upper_zone_high,
            color=UPPER_ZONE_COLOR, alpha=0.14, linewidth=0, zorder=0.5,
        )
        price_ax.axhspan(
            range_.lower_zone_low, range_.lower_zone_high,
            color=LOWER_ZONE_COLOR, alpha=0.16, linewidth=0, zorder=0.5,
        )
        legend_handles.append(
            Patch(facecolor=UPPER_ZONE_COLOR, alpha=0.35, label=_t("レンジ上限zone", "Range Upper Zone"))
        )
        legend_handles.append(
            Patch(facecolor=LOWER_ZONE_COLOR, alpha=0.4, label=_t("レンジ下限zone", "Range Lower Zone"))
        )

        # 検出したレンジそのものの矩形（形成期間 × 上限〜下限）
        box = Rectangle(
            (range_start_disp - 0.5, range_.lower),
            (range_end_disp - range_start_disp) + 1.0,
            range_.upper - range_.lower,
            facecolor="none",
            edgecolor=RANGE_BOX_COLOR,
            linewidth=1.3,
            linestyle="--",
            zorder=2,
        )
        price_ax.add_patch(box)
        legend_handles.append(
            Patch(
                facecolor="none", edgecolor=RANGE_BOX_COLOR, linestyle="--",
                label=_t(f"検出レンジ ({range_.days}営業日)", f"Detected Range ({range_.days}d)"),
            )
        )

        right_labels.append((range_.upper, _t(f"上限 {range_.upper:,.0f}円", f"Upper {range_.upper:,.0f}"), UPPER_ZONE_COLOR))
        right_labels.append((range_.lower, _t(f"下限 {range_.lower:,.0f}円", f"Lower {range_.lower:,.0f}"), LOWER_ZONE_COLOR))

        # 下限で反応した日にマーカーを立てる
        touch_dates = set(range_.lower_touch_dates)
        if touch_dates:
            touch_x, touch_y = [], []
            for i, b in enumerate(display_bars):
                if b.date in touch_dates:
                    touch_x.append(i)
                    touch_y.append(b.low)
            if touch_x:
                price_ax.scatter(
                    touch_x, touch_y, marker="^", s=48,
                    color=TOUCH_MARK_COLOR, edgecolor="white", linewidth=0.6, zorder=4,
                )
                legend_handles.append(
                    plt.Line2D(
                        [], [], marker="^", linestyle="none", color=TOUCH_MARK_COLOR,
                        markeredgecolor="white",
                        label=_t(f"下限反応 ({range_.lower_touch_count}回)", f"Lower Touch ({range_.lower_touch_count})"),
                    )
                )

    # --- ローソク足 -------------------------------------------------------------
    candle_width = 0.62 if n <= 150 else 0.5
    wick_width = 1.3 if n <= 150 else 0.9
    for idx, bar in enumerate(display_bars):
        color = UP_COLOR if bar.close >= bar.open else DOWN_COLOR
        price_ax.vlines(idx, bar.low, bar.high, color=color, linewidth=wick_width, zorder=3)
        body_low = min(bar.open, bar.close)
        body_height = abs(bar.close - bar.open)
        if body_height <= 0:
            body_height = max(bar.close * 0.0015, 0.01)
            body_low = bar.close - body_height / 2
        price_ax.add_patch(
            Rectangle(
                (idx - candle_width / 2, body_low),
                candle_width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
                zorder=3,
            )
        )

    # --- MA25 --------------------------------------------------------------------
    ma_x = [xi for xi, v in zip(x, ma_display) if v is not None]
    ma_y = [v for v in ma_display if v is not None]
    if ma_x:
        (ma_line,) = price_ax.plot(
            ma_x, ma_y, color=MA_COLOR, linewidth=1.7, zorder=3.5,
            label=_t(f"MA{ma_period}", f"MA{ma_period}"),
        )
        legend_handles.append(ma_line)
        right_labels.append((ma_y[-1], _t(f"MA{ma_period} {ma_y[-1]:,.0f}円", f"MA{ma_period} {ma_y[-1]:,.0f}"), MA_COLOR))

    # --- 前日高値 -----------------------------------------------------------------
    prev_high = result.rebound.prev_high if result.rebound else None
    if prev_high is not None:
        price_ax.axhline(prev_high, color=PREV_HIGH_COLOR, linewidth=1.1, linestyle="-", zorder=2.5)
        legend_handles.append(
            plt.Line2D([], [], color=PREV_HIGH_COLOR, linewidth=1.1, label=_t("前日高値", "Prev. Day High"))
        )
        right_labels.append((prev_high, _t(f"前日高値 {prev_high:,.0f}円", f"PrevHigh {prev_high:,.0f}"), PREV_HIGH_COLOR))

    # --- 損切りライン（目立たせるため破線＋太め） ------------------------------------
    if result.stop_price is not None:
        price_ax.axhline(result.stop_price, color=STOP_COLOR, linewidth=1.8, linestyle="--", zorder=2.6)
        legend_handles.append(
            plt.Line2D([], [], color=STOP_COLOR, linewidth=1.8, linestyle="--", label=_t("損切りライン", "Stop Line"))
        )
        right_labels.append(
            (result.stop_price, _t(f"損切り {result.stop_price:,.0f}円", f"Stop {result.stop_price:,.0f}"), STOP_COLOR)
        )

    # --- y軸の範囲を明示的に決める（axhspanは自動スケールに乗らないため） --------------
    y_values: list[float] = []
    for b in display_bars:
        y_values.append(b.low)
        y_values.append(b.high)
    y_values.extend(ma_y)
    if range_ is not None:
        y_values.append(range_.lower_zone_low)
        y_values.append(range_.upper_zone_high)
    if prev_high is not None:
        y_values.append(prev_high)
    if result.stop_price is not None:
        y_values.append(result.stop_price)
    if y_values:
        y_min, y_max = min(y_values), max(y_values)
        pad = (y_max - y_min) * 0.07 or max(y_max * 0.02, 1.0)
        y_lim_low, y_lim_high = y_min - pad, y_max + pad
        price_ax.set_ylim(y_lim_low, y_lim_high)
    else:
        y_lim_low, y_lim_high = price_ax.get_ylim()

    # 右側の価格ラベルは ylim 確定後にまとめて配置する（近接時の重なりを避けるため）
    _place_right_labels(price_ax, right_labels, y_lim_low, y_lim_high)

    # --- タイトル -----------------------------------------------------------------
    close_val = result.latest_close
    close_txt = f"{close_val:,.0f}円" if close_val is not None else _t("データなし", "N/A")
    close_txt_en = f"{close_val:,.0f}" if close_val is not None else "N/A"
    title = _t(
        f"{result.stock.code}  {result.stock.name}   {result.status}   終値 {close_txt}",
        f"{result.stock.code}   {result.status}   Close {close_txt_en}",
    )
    price_ax.set_title(title, loc="left", fontsize=13.5, fontweight="bold")
    price_ax.set_ylabel(_t("株価 (円)", "Price"))
    price_ax.grid(True, axis="y", color="#e9ecef", linewidth=0.8, zorder=0)
    price_ax.margins(x=0.01)

    # --- 出来高サブプロット ---------------------------------------------------------
    vol_colors = [
        VOLUME_UP_COLOR if b.close >= b.open else VOLUME_DOWN_COLOR for b in display_bars
    ]
    volumes = [b.volume for b in display_bars]
    volume_ax.bar(x, volumes, color=vol_colors, width=candle_width + 0.1, zorder=2)
    volume_ax.set_ylabel(_t("出来高", "Volume"))
    volume_ax.grid(True, axis="y", color="#f1f3f5", linewidth=0.8, zorder=0)
    volume_ax.margins(x=0.01)
    volume_ax.yaxis.set_major_formatter(_volume_formatter)

    tick_positions, tick_labels = _date_ticks(display_bars)
    volume_ax.set_xticks(tick_positions)
    volume_ax.set_xticklabels(tick_labels, rotation=0, ha="center", fontsize=9)
    volume_ax.set_xlim(-1, n)

    # 凡例はチャート本体の外（出来高の下）に横並びで置く。
    # ローソク足・帯・矩形など画面内の描画要素と絶対に重ならないようにするため。
    if legend_handles:
        fig.legend(
            handles=legend_handles,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.02),
            ncol=min(4, len(legend_handles)),
            fontsize=8.6,
            frameon=True,
            framealpha=0.92,
            borderaxespad=0.3,
        )

    fig.tight_layout(rect=(0, 0.05, 1, 1))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=148, bbox_inches="tight")
    plt.close(fig)
    return output_path


# --- 保有銘柄チャート -----------------------------------------------------------


def _draw_candles(ax, display_bars: Sequence[OHLCVBar], candle_width: float, wick_width: float) -> None:
    for idx, bar in enumerate(display_bars):
        color = UP_COLOR if bar.close >= bar.open else DOWN_COLOR
        ax.vlines(idx, bar.low, bar.high, color=color, linewidth=wick_width, zorder=3)
        body_low = min(bar.open, bar.close)
        body_height = abs(bar.close - bar.open)
        if body_height <= 0:
            body_height = max(bar.close * 0.0015, 0.01)
            body_low = bar.close - body_height / 2
        ax.add_patch(
            Rectangle(
                (idx - candle_width / 2, body_low),
                candle_width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
                zorder=3,
            )
        )


def render_holding_chart(
    series: PriceSeries,
    trade: Any,
    cfg: Any,
    output_path: Path,
    days: int = 120,
    as_of: Any = None,
) -> Path:
    """保有銘柄の日足チャートPNGを生成する。

    描くのは「買ったときに決めた線」と「その後どうなったか」だけ:
        ENTRY価格 / ENTRY日 / 初期STOP / 買ったときのレンジ上限・下限 /
        保有後最高値 / （決済済みなら）EXIT日・EXIT価格

    **trail stop や利確ラインは描かない。** v1 では自動化していないため
    （TRADING_RULES.md §7）、あるように見せてはいけない。

    `as_of` を渡すとその日までの足だけで描く。ENTRY 当日の状態や EXIT 当日の
    状態を後から再現するために使う（未来の足を混ぜないこと自体が目的）。
    """
    bars = list(series.bars)
    if as_of is not None:
        bars = [b for b in bars if b.date <= as_of]
    if not bars:
        raise ValueError(f"{series.code}: 描画できる株価データがありません")

    display_days = max(1, days)
    start_offset = max(0, len(bars) - display_days)
    display_bars = bars[start_offset:]
    n = len(display_bars)
    x = list(range(n))
    dates = [b.date for b in display_bars]

    ma_period = int(cfg.ma.period)
    ma_full = _calc_ma(bars, ma_period)
    ma_display = ma_full[start_offset:]

    fig_width = min(22.0, max(11.0, n * 0.085 + 4.0))
    fig, (price_ax, volume_ax) = plt.subplots(
        2, 1, figsize=(fig_width, 7.8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]}, facecolor="white",
    )
    price_ax.set_facecolor("white")
    volume_ax.set_facecolor("white")

    legend_handles: list[Any] = []
    right_labels: list[tuple[float, str, str]] = []

    entry_date = getattr(trade, "entry_date", None)
    exit_date = getattr(trade, "exit_date", None)
    entry_price = getattr(trade, "entry_price", None)
    exit_price = getattr(trade, "exit_price", None)
    initial_stop = getattr(trade, "initial_stop", None)
    range_lower = getattr(trade, "original_range_lower", None)
    range_upper = getattr(trade, "original_range_upper", None)

    entry_x = _clamp_index_for_date(dates, entry_date) if entry_date else None
    exit_x = _clamp_index_for_date(dates, exit_date) if exit_date else None

    # --- 保有期間の背景 ---------------------------------------------------------
    if entry_x is not None:
        end_x = exit_x if exit_x is not None else n - 1
        price_ax.axvspan(entry_x - 0.5, end_x + 0.5, color=ENTRY_COLOR, alpha=0.05, linewidth=0, zorder=0)
        volume_ax.axvspan(entry_x - 0.5, end_x + 0.5, color=ENTRY_COLOR, alpha=0.10, linewidth=0, zorder=0)

    # --- 買ったときのレンジ ------------------------------------------------------
    if range_upper is not None:
        price_ax.axhline(range_upper, color=UPPER_ZONE_COLOR, linewidth=1.2, linestyle=":", zorder=2.2)
        right_labels.append(
            (range_upper, _t(f"元上限 {range_upper:,.0f}円", f"Range Upper {range_upper:,.0f}"), UPPER_ZONE_COLOR)
        )
        legend_handles.append(
            plt.Line2D([], [], color=UPPER_ZONE_COLOR, linewidth=1.2, linestyle=":",
                       label=_t("買ったときのレンジ上限", "Range Upper at Entry"))
        )
    if range_lower is not None:
        price_ax.axhline(range_lower, color=LOWER_ZONE_COLOR, linewidth=1.2, linestyle=":", zorder=2.2)
        right_labels.append(
            (range_lower, _t(f"元下限 {range_lower:,.0f}円", f"Range Lower {range_lower:,.0f}"), LOWER_ZONE_COLOR)
        )
        legend_handles.append(
            plt.Line2D([], [], color=LOWER_ZONE_COLOR, linewidth=1.2, linestyle=":",
                       label=_t("買ったときのレンジ下限", "Range Lower at Entry"))
        )

    # --- ローソク足 --------------------------------------------------------------
    candle_width = 0.62 if n <= 150 else 0.5
    wick_width = 1.3 if n <= 150 else 0.9
    _draw_candles(price_ax, display_bars, candle_width, wick_width)

    # --- MA25 --------------------------------------------------------------------
    ma_x = [xi for xi, v in zip(x, ma_display) if v is not None]
    ma_y = [v for v in ma_display if v is not None]
    if ma_x:
        (ma_line,) = price_ax.plot(ma_x, ma_y, color=MA_COLOR, linewidth=1.7, zorder=3.5,
                                   label=_t(f"MA{ma_period}", f"MA{ma_period}"))
        legend_handles.append(ma_line)
        right_labels.append((ma_y[-1], _t(f"MA{ma_period} {ma_y[-1]:,.0f}円", f"MA{ma_period} {ma_y[-1]:,.0f}"), MA_COLOR))

    # --- ENTRY 価格・初期STOP -----------------------------------------------------
    if entry_price is not None:
        price_ax.axhline(entry_price, color=ENTRY_COLOR, linewidth=1.8, linestyle="-", zorder=2.7)
        right_labels.append((entry_price, _t(f"ENTRY {entry_price:,.0f}円", f"Entry {entry_price:,.0f}"), ENTRY_COLOR))
        legend_handles.append(
            plt.Line2D([], [], color=ENTRY_COLOR, linewidth=1.8, label=_t("ENTRY価格", "Entry Price"))
        )
    if initial_stop is not None:
        price_ax.axhline(initial_stop, color=STOP_COLOR, linewidth=1.8, linestyle="--", zorder=2.6)
        right_labels.append((initial_stop, _t(f"初期STOP {initial_stop:,.0f}円", f"Stop {initial_stop:,.0f}"), STOP_COLOR))
        legend_handles.append(
            plt.Line2D([], [], color=STOP_COLOR, linewidth=1.8, linestyle="--",
                       label=_t("初期STOP（引き上げは自動化しない）", "Initial Stop"))
        )

    # --- ENTRY / EXIT マーカー -----------------------------------------------------
    if entry_x is not None and entry_date is not None:
        y = entry_price if entry_price is not None else display_bars[entry_x].low
        price_ax.scatter([entry_x], [y], marker="^", s=150, color=ENTRY_COLOR,
                         edgecolor="white", linewidth=1.0, zorder=6)
        price_ax.annotate(
            _t(f"ENTRY {entry_date:%m/%d}", f"ENTRY {entry_date:%m/%d}"),
            (entry_x, y), textcoords="offset points", xytext=(0, -18),
            ha="center", fontsize=8.6, color=ENTRY_COLOR, fontweight="bold", zorder=6,
        )
    if exit_x is not None and exit_date is not None:
        y = exit_price if exit_price is not None else display_bars[exit_x].high
        price_ax.scatter([exit_x], [y], marker="v", s=150, color=EXIT_COLOR,
                         edgecolor="white", linewidth=1.0, zorder=6)
        price_ax.annotate(
            _t(f"EXIT {exit_date:%m/%d}", f"EXIT {exit_date:%m/%d}"),
            (exit_x, y), textcoords="offset points", xytext=(0, 12),
            ha="center", fontsize=8.6, color=EXIT_COLOR, fontweight="bold", zorder=6,
        )
        legend_handles.append(
            plt.Line2D([], [], marker="v", linestyle="none", color=EXIT_COLOR,
                       markeredgecolor="white", label=_t("EXIT", "Exit"))
        )

    # --- 保有後最高値 --------------------------------------------------------------
    holding_high = None
    if entry_date is not None:
        held = [b for b in display_bars if b.date >= entry_date and (exit_date is None or b.date <= exit_date)]
        if held:
            high_bar = max(held, key=lambda b: b.high)
            holding_high = high_bar.high
            hh_x = _clamp_index_for_date(dates, high_bar.date)
            price_ax.axhline(holding_high, color=HOLDING_HIGH_COLOR, linewidth=1.0,
                             linestyle="-.", alpha=0.9, zorder=2.4)
            price_ax.scatter([hh_x], [holding_high], marker="*", s=130, color=HOLDING_HIGH_COLOR,
                             edgecolor="white", linewidth=0.6, zorder=6)
            right_labels.append(
                (holding_high, _t(f"保有後最高値 {holding_high:,.0f}円", f"Max High {holding_high:,.0f}"), HOLDING_HIGH_COLOR)
            )
            legend_handles.append(
                plt.Line2D([], [], color=HOLDING_HIGH_COLOR, linewidth=1.0, linestyle="-.",
                           label=_t("保有後最高値", "High since Entry"))
            )

    # --- y軸 -----------------------------------------------------------------------
    y_values: list[float] = []
    for b in display_bars:
        y_values.extend((b.low, b.high))
    y_values.extend(ma_y)
    for v in (entry_price, exit_price, initial_stop, range_lower, range_upper, holding_high):
        if v is not None:
            y_values.append(v)
    if y_values:
        y_min, y_max = min(y_values), max(y_values)
        pad = (y_max - y_min) * 0.07 or max(y_max * 0.02, 1.0)
        y_lim_low, y_lim_high = y_min - pad, y_max + pad
        price_ax.set_ylim(y_lim_low, y_lim_high)
    else:
        y_lim_low, y_lim_high = price_ax.get_ylim()
    _place_right_labels(price_ax, right_labels, y_lim_low, y_lim_high)

    # --- タイトル -------------------------------------------------------------------
    latest = display_bars[-1]
    code = getattr(trade, "code", series.code)
    name = getattr(trade, "name", "") or ""
    if exit_price is not None and entry_price:
        pnl = (exit_price - entry_price) / entry_price * 100.0
        state = _t(f"決済済み {pnl:+.1f}%", f"CLOSED {pnl:+.1f}%")
    elif entry_price:
        pnl = (latest.close - entry_price) / entry_price * 100.0
        state = _t(f"保有中 {pnl:+.1f}%", f"OPEN {pnl:+.1f}%")
    else:
        state = _t("保有中", "OPEN")
    title = _t(
        f"{code}  {name}   {state}   {latest.date:%Y-%m-%d} 終値 {latest.close:,.0f}円",
        f"{code}   {state}   {latest.date:%Y-%m-%d} Close {latest.close:,.0f}",
    )
    price_ax.set_title(title, loc="left", fontsize=13.5, fontweight="bold")
    price_ax.set_ylabel(_t("株価 (円)", "Price"))
    price_ax.grid(True, axis="y", color="#e9ecef", linewidth=0.8, zorder=0)
    price_ax.margins(x=0.01)

    # --- 出来高 ---------------------------------------------------------------------
    vol_colors = [VOLUME_UP_COLOR if b.close >= b.open else VOLUME_DOWN_COLOR for b in display_bars]
    volume_ax.bar(x, [b.volume for b in display_bars], color=vol_colors, width=candle_width + 0.1, zorder=2)
    volume_ax.set_ylabel(_t("出来高", "Volume"))
    volume_ax.grid(True, axis="y", color="#f1f3f5", linewidth=0.8, zorder=0)
    volume_ax.margins(x=0.01)
    volume_ax.yaxis.set_major_formatter(_volume_formatter)

    tick_positions, tick_labels = _date_ticks(display_bars)
    volume_ax.set_xticks(tick_positions)
    volume_ax.set_xticklabels(tick_labels, rotation=0, ha="center", fontsize=9)
    volume_ax.set_xlim(-1, n)

    if legend_handles:
        fig.legend(
            handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, -0.02),
            ncol=min(4, len(legend_handles)), fontsize=8.6, frameon=True,
            framealpha=0.92, borderaxespad=0.3,
        )

    fig.tight_layout(rect=(0, 0.05, 1, 1))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=148, bbox_inches="tight")
    plt.close(fig)
    return output_path
