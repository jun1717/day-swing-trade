"""保有銘柄と実トレードの記録（TRADING_RULES.md §7 / フォワード運用フェーズ）。

**このモジュールは売買判定を一切しない。** ユーザーが実際に買った/売ったことを
そのまま記録するだけの台帳である。判定を持たないので、あとから
「ツールがどう表示していたか」と「自分がどう行動したか」を突き合わせられる。

保存先は 1 ファイル（既定 `data/trades.csv`）。保有中も決済済みも同じ行に持ち、
`exit_date` が空なら保有中とする。行を分けないのは、1 トレードの履歴が
2 ファイルに散らばると突き合わせが面倒になるため。

数量はユーザー入力。ポジションサイズは計算しない（TRADING_RULES.md §6）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_TRADES_CSV = "data/trades.csv"

# `exit_reason` の記入例。**自動判定ルールではない**（TRADING_RULES.md §7）。
# ここにない理由を自由記述しても構わない。
EXIT_REASONS: tuple[str, ...] = (
    "initial_stop",       # 初期STOPに当たった
    "scenario_break",     # 買った理由が崩れた
    "warning_candle",     # 警戒陰線まわりの判断
    "support_break",      # 支持帯割れ
    "profit_protection",  # 利益確保
    "discretionary",      # 裁量
    "other",
)

EXIT_REASON_LABELS_JA: dict[str, str] = {
    "initial_stop": "初期STOP",
    "scenario_break": "シナリオ崩れ",
    "warning_candle": "警戒陰線",
    "support_break": "支持帯割れ",
    "profit_protection": "利益確保",
    "discretionary": "裁量",
    "other": "その他",
}


# --- 1 トレード ---------------------------------------------------------------


@dataclass
class Trade:
    """1 回の売買。`exit_date` が None なら保有中。

    `original_range_*` / `initial_stop` は **ENTRY 時点の値を固定して持つ**。
    毎日再計算するとレンジが動いてしまい、「何を根拠に買ったか」が失われるため。
    """

    code: str
    name: str = ""
    entry_date: date | None = None
    entry_price: float | None = None
    quantity: int | None = None

    original_range_lower: float | None = None
    original_range_upper: float | None = None
    initial_stop: float | None = None

    entry_reason: str = ""
    memo: str = ""

    # ENTRY候補として表示された日（journal.signals と突き合わせるためのキー）
    signal_date: date | None = None

    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    exit_memo: str = ""

    @property
    def is_open(self) -> bool:
        return self.exit_date is None

    @property
    def realized_pnl_pct(self) -> float | None:
        """決済済みの損益率(%)。手数料・税は考慮しない。"""
        if self.exit_price is None or not self.entry_price:
            return None
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0

    @property
    def realized_pnl_yen(self) -> float | None:
        if self.exit_price is None or self.entry_price is None or not self.quantity:
            return None
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def holding_days(self) -> int | None:
        """暦日数（営業日ではない）。"""
        if self.entry_date is None or self.exit_date is None:
            return None
        return (self.exit_date - self.entry_date).days

    def unrealized_pnl_pct(self, close: float | None) -> float | None:
        if close is None or not self.entry_price:
            return None
        return (close - self.entry_price) / self.entry_price * 100.0


FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(Trade))


# --- CSV 入出力 ---------------------------------------------------------------


def trades_path(cfg: Any = None, path: Path | str | None = None) -> Path:
    """台帳の保存先。config.yaml に `journal.trades_csv` があればそれを使う。"""
    if path is not None:
        return Path(path)
    if cfg is not None:
        return Path(str(cfg.get("journal.trades_csv", DEFAULT_TRADES_CSV)))
    return Path(DEFAULT_TRADES_CSV)


def _parse_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _parse_float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    return float(text)


def _parse_int(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    return int(float(text))


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        # 価格は小数第2位まで（日本株は整数が多いが、ETF・分割調整で端数が出る）
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _row_to_trade(row: dict[str, str]) -> Trade:
    return Trade(
        code=(row.get("code") or "").strip(),
        name=(row.get("name") or "").strip(),
        entry_date=_parse_date(row.get("entry_date")),
        entry_price=_parse_float(row.get("entry_price")),
        quantity=_parse_int(row.get("quantity")),
        original_range_lower=_parse_float(row.get("original_range_lower")),
        original_range_upper=_parse_float(row.get("original_range_upper")),
        initial_stop=_parse_float(row.get("initial_stop")),
        entry_reason=(row.get("entry_reason") or "").strip(),
        memo=(row.get("memo") or "").strip(),
        signal_date=_parse_date(row.get("signal_date")),
        exit_date=_parse_date(row.get("exit_date")),
        exit_price=_parse_float(row.get("exit_price")),
        exit_reason=(row.get("exit_reason") or "").strip(),
        exit_memo=(row.get("exit_memo") or "").strip(),
    )


def load_trades(cfg: Any = None, path: Path | str | None = None) -> list[Trade]:
    """台帳を読む。ファイルがなければ空リスト（初回起動でも落ちないこと）。"""
    target = trades_path(cfg, path)
    if not target.exists():
        return []
    trades: list[Trade] = []
    with target.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if not (row.get("code") or "").strip():
                continue
            trades.append(_row_to_trade(row))
    return trades


def save_trades(
    trades: Sequence[Trade], cfg: Any = None, path: Path | str | None = None
) -> Path:
    """台帳を書き戻す。列は FIELD_NAMES 固定（列順が変わると差分が読めなくなる）。"""
    target = trades_path(cfg, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(FIELD_NAMES))
        writer.writeheader()
        for t in trades:
            writer.writerow({name: _fmt(getattr(t, name)) for name in FIELD_NAMES})
    return target


# --- 参照 ---------------------------------------------------------------------


def open_trades(trades: Iterable[Trade]) -> list[Trade]:
    """保有中のみ。entry_date 昇順。"""
    return sorted(
        (t for t in trades if t.is_open),
        key=lambda t: (t.entry_date or date.min, t.code),
    )


def closed_trades(trades: Iterable[Trade]) -> list[Trade]:
    """決済済みのみ。exit_date 降順（新しいものが上）。"""
    return sorted(
        (t for t in trades if not t.is_open),
        key=lambda t: (t.exit_date or date.min, t.code),
        reverse=True,
    )


def find_open(trades: Iterable[Trade], code: str) -> Trade | None:
    for t in trades:
        if t.code == code and t.is_open:
            return t
    return None


# --- 更新 ---------------------------------------------------------------------


class PortfolioError(Exception):
    """台帳の操作が矛盾しているとき（同一銘柄の二重保有など）。"""


def add_trade(trades: list[Trade], trade: Trade) -> list[Trade]:
    """保有を追加する。同じ銘柄が既に保有中なら拒否する。

    同一銘柄の複数ポジション（買い増し）は v1 では扱わない。扱えるように
    見せかけると、あとで「どちらの ENTRY 根拠なのか」が追えなくなるため、
    明示的にエラーにする。
    """
    if not trade.code:
        raise PortfolioError("code は必須です。")
    if find_open(trades, trade.code) is not None:
        raise PortfolioError(
            f"{trade.code} は既に保有中です。先に売却を記録するか、"
            "data/trades.csv を直接編集してください（v1 は買い増しを扱いません）。"
        )
    result = list(trades)
    result.append(trade)
    return result


def close_trade(
    trades: list[Trade],
    code: str,
    *,
    exit_date: date,
    exit_price: float,
    exit_reason: str = "",
    exit_memo: str = "",
) -> tuple[list[Trade], Trade]:
    """保有中の 1 件に決済を記録する。"""
    target = find_open(trades, code)
    if target is None:
        raise PortfolioError(f"{code} は保有中ではありません。")
    if target.entry_date is not None and exit_date < target.entry_date:
        raise PortfolioError(
            f"exit_date {exit_date} が entry_date {target.entry_date} より前です。"
        )
    target.exit_date = exit_date
    target.exit_price = exit_price
    target.exit_reason = exit_reason
    target.exit_memo = exit_memo
    return list(trades), target
