"""判定理由テキストの生成（DESIGN.md §10 / CODEX_HANDOFF §27）。

このツールの中心要件は「機械がなぜその判定をしたか」を人間に見せることなので、
表示用の数値フォーマットもここに集約している。Judgement.detail を作るのは
indicators / rules 側だが、書式がバラバラになると詳細画面が読みにくくなるため
フォーマッタは 1 箇所に置く。

出力はそのままスクリーンショットして ChatGPT に貼れる読みやすさを目指す。
"""

from __future__ import annotations

from datetime import date

from .models import Judgement, ScreenResult

# --- 数値フォーマット -------------------------------------------------------

DASH = "－"


def fmt_price(value: float | None, unit: str = "円") -> str:
    """株価。1000円以上は 5,580円 のように整数、小さい値は小数1桁。"""
    if value is None:
        return DASH
    if abs(value) >= 100:
        return f"{value:,.0f}{unit}"
    return f"{value:,.1f}{unit}"


def fmt_pct(value: float | None, signed: bool = True, digits: int = 1) -> str:
    """パーセント。signed=True なら +3.1% のように符号を付ける。"""
    if value is None:
        return DASH
    if signed:
        return f"{value:+.{digits}f}%"
    return f"{value:.{digits}f}%"


def fmt_ratio(value: float | None, digits: int = 2) -> str:
    """倍率（0.72 / 1.31 など）。"""
    if value is None:
        return DASH
    return f"{value:.{digits}f}"


def fmt_volume(value: float | None) -> str:
    if value is None:
        return DASH
    return f"{value:,.0f}株"


def fmt_md(d: date | None) -> str:
    """月日のみ（08/04）。判定理由は当年の日足を見るので年は省く。"""
    if d is None:
        return DASH
    return f"{d.month:02d}/{d.day:02d}"


def fmt_dates(dates: tuple[date, ...] | list[date]) -> str:
    return ", ".join(fmt_md(d) for d in dates)


def ok_mark(ok: bool | None) -> str:
    if ok is True:
        return "OK"
    if ok is False:
        return "NG"
    return "判定不能"


# --- Judgement のグルーピング -----------------------------------------------

# (表示名, key の接頭辞) の順。DESIGN.md §12.5 の並びを守る。
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("上昇トレンド", ("trend",)),
    ("レンジ", ("range",)),
    ("出来高", ("volume",)),
    ("反発", ("rebound",)),
    ("前提・状態", ("filter", "status")),
)


def judgement_groups(result: ScreenResult) -> list[tuple[str, list[Judgement]]]:
    """判定を表示グループに分ける。空のグループは返さない。"""
    groups: list[tuple[str, list[Judgement]]] = []
    for title, prefixes in _GROUPS:
        items = [j for j in result.judgements if j.key.split(".")[0] in prefixes]
        if items:
            groups.append((title, items))
    return groups


def _by_key(result: ScreenResult) -> dict[str, Judgement]:
    return {j.key: j for j in result.judgements}


# --- §10 の判定理由テキスト -------------------------------------------------


def explain_lines(result: ScreenResult) -> list[str]:
    """DESIGN.md §10 の書式で判定理由テキストを返す（1要素 = 1行）。"""
    js = _by_key(result)
    lines: list[str] = []

    # 見出し
    head = f"【{result.stock.code} {result.stock.name}】"
    if result.as_of is not None:
        head += f" {result.as_of.isoformat()}"
    if result.latest_close is not None:
        head += f" 終値 {fmt_price(result.latest_close)}"
    lines.append(head)
    lines.append("")

    # 価格フィルタ（落ちた理由になりうるので常に出す）
    price_j = js.get("filter.price")
    if price_j is not None:
        lines.append(f"株価フィルタ：{ok_mark(price_j.ok)} — {price_j.detail}")
        lines.append("")

    # 上昇トレンド
    t = result.trend
    if t is not None:
        lines.append(f"上昇トレンド：{ok_mark(t.is_uptrend)}")
        for key, label in (
            ("trend.ma_direction", "25日線"),
            ("trend.close_above_ma", "株価 > MA"),
            ("trend.higher_highs", "高値切り上げ"),
            ("trend.higher_lows", "安値切り上げ"),
        ):
            j = js.get(key)
            if j is None:
                continue
            label = j.label if key != "trend.ma_direction" else label
            mark = "" if key == "trend.ma_direction" else f"{ok_mark(j.ok)} — "
            lines.append(f"{label}：{mark}{j.detail}")
        strength = js.get("trend.strength")
        if strength is not None:
            lines.append(f"トレンド強度：{strength.detail}")
        lines.append("")

    # レンジ
    r = result.range_
    if r is not None:
        lines.append(
            f"レンジ：{r.days}営業日 ({fmt_md(r.start_date)}〜{fmt_md(r.end_date)})"
        )
        lines.append(
            f"下限：{fmt_price(r.lower)} "
            f"(zone {fmt_price(r.lower_zone_low)}〜{fmt_price(r.lower_zone_high)})"
        )
        lines.append(
            f"上限：{fmt_price(r.upper)} "
            f"(zone {fmt_price(r.upper_zone_low)}〜{fmt_price(r.upper_zone_high)})"
        )
        touch = f"下限反応：{r.lower_touch_count}回"
        if r.lower_touch_dates:
            touch += f" ({fmt_dates(r.lower_touch_dates)})"
        lines.append(touch)
        lines.append(f"値幅：{fmt_pct(r.width_pct, signed=False)}")
        lines.append(
            f"値幅の推移：後半/前半 = {fmt_ratio(r.volatility_change)}"
            f"（1.00未満が収縮）"
        )
        lines.append(f"レンジ品質：{fmt_ratio(r.quality)}")
        lines.append("")
        if result.distance_to_lower_pct is not None:
            lines.append(f"下限まで：{fmt_pct(result.distance_to_lower_pct)}")
            if result.days_since_lower_touch is not None:
                lines.append(
                    f"直近の下限zone接触：{result.days_since_lower_touch}営業日前"
                )
            lines.append("")

    # 出来高
    v = result.volume
    if v is not None:
        detail = v.state_label
        if v.range_vs_pre_ratio is not None:
            detail += f" (レンジ平均/レンジ前平均 = {fmt_ratio(v.range_vs_pre_ratio)})"
        lines.append(f"出来高：{detail}")
        lines.append(
            f"　当日 {fmt_volume(v.latest)} / 5日平均 {fmt_volume(v.avg5)}"
            f" / 20日平均 {fmt_volume(v.avg20)}"
        )
        lines.append("")

    # 反発
    b = result.rebound
    if b is not None:
        j = js.get("rebound.confirmed")
        detail = j.detail if j else ""
        lines.append(
            f"反発確認：{'成立' if b.confirmed else '未成立'}"
            + (f" ({detail})" if detail else "")
        )
        extras = [
            label
            for label, flag in (
                ("陽線", b.bullish_candle),
                ("長い下ヒゲ", b.long_lower_wick),
                ("出来高回復", b.volume_recovered),
            )
            if flag
        ]
        if extras:
            lines.append(f"　加点材料：{' / '.join(extras)}（単独では判定しない）")
        lines.append("")

    # 状態
    lines.append(f"状態：{result.status}")
    if result.out_reason:
        lines.append(f"落選理由：{result.out_reason}")
    if result.stop_price is not None:
        lines.append(f"損切り候補：{fmt_price(result.stop_price)}（レンジ下限の0.5%下）")

    # 不採用だった window（なぜこのレンジになったか）
    rejected = js.get("range.candidates")
    if rejected is not None:
        lines.append("")
        lines.append(f"レンジ候補の検討：{rejected.detail}")

    while lines and lines[-1] == "":
        lines.pop()
    return lines


def explain_text(result: ScreenResult) -> str:
    """explain_lines を 1 つの文字列にしたもの（CLI 表示用）。"""
    return "\n".join(explain_lines(result))
