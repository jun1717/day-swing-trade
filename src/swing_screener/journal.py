"""日次スナップショットと ENTRY 候補履歴（フォワード検証用のデータ蓄積）。

目的は 1 つだけ:

    **「その日、ツールが何を表示していたか」を後から再現できるようにする。**

そのために 2 種類を保存する。

1. 日次スナップショット `data/journal/daily/YYYY-MM-DD.csv`
   その日の全銘柄の判定。1 日 1 ファイル。
2. ENTRY 候補履歴 `data/journal/signals.csv`
   ENTRY_CANDIDATE が出た日の 1 行。実際に買わなかったものも残す。

**過去の行を未来のデータで書き換えない。** 株価は配当調整で遡って変わるし、
レンジ検出は新しい足が付くたびに動く。あとから再計算した値で上書きすると
「その日に見えていたもの」が消えてしまい、フォワード検証の意味がなくなる。
そのため既存の日付のスナップショットは既定では上書きしない（`force=True` が要る）。
signals.csv は (signal_date, code) が既にあれば追記しない。

「実際に買ったか」はここには保存しない。トレード台帳 `data/trades.csv`
（portfolio.py）との結合で導く。履歴側に後から購入フラグを立てると、
その行が「表示内容の記録」なのか「行動の記録」なのか分からなくなるため。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import STATUS_ENTRY, ScreenResult, ScreeningRun
from .portfolio import Trade

DEFAULT_JOURNAL_DIR = "data/journal"

SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "date",
    "code",
    "name",
    "sector",
    "themes",
    "status",
    "close",
    "ma25",
    "range_days",
    "range_lower",
    "range_upper",
    "range_position",
    "distance_to_lower_pct",
    "lower_touch_count",
    "prev_high",
    "rebound_confirmed",
    "volume_state",
    "initial_stop",
    "reason",
)

SIGNAL_COLUMNS: tuple[str, ...] = (
    "signal_date",
    "code",
    "name",
    "sector",
    "themes",
    "signal_close",
    "ma25",
    "range_days",
    "range_lower",
    "range_upper",
    "range_position",
    "distance_to_lower_pct",
    "lower_touch_count",
    "prev_high",
    "volume_state",
    "initial_stop",
    "signal_reason",
)

FORWARD_COLUMNS: tuple[str, ...] = (
    "signal_date",
    "code",
    "name",
    "signal_close",
    "range_lower",
    "range_upper",
    "initial_stop",
    "purchased",
    "entry_date",
    "entry_price",
    "quantity",
    "exit_date",
    "exit_price",
    "exit_reason",
    "realized_pnl_pct",
    "bars_after_signal",
    "hit_initial_stop",
    "days_to_initial_stop",
    "reached_range_upper",
    "closed_above_range_upper",
    "max_gain_pct",
    "max_loss_pct",
    "days_to_max_gain",
)


# --- パス ---------------------------------------------------------------------


def journal_dir(cfg: Any = None, dir_path: Path | str | None = None) -> Path:
    """記録の置き場所。`dir_path` はディレクトリの上書き指定（テスト用）。"""
    if dir_path is not None:
        return Path(dir_path)
    if cfg is not None:
        return Path(str(cfg.get("journal.dir", DEFAULT_JOURNAL_DIR)))
    return Path(DEFAULT_JOURNAL_DIR)


def daily_dir(cfg: Any = None, dir_path: Path | str | None = None) -> Path:
    return journal_dir(cfg, dir_path) / "daily"


def snapshot_path(as_of: date, cfg: Any = None, dir_path: Path | str | None = None) -> Path:
    return daily_dir(cfg, dir_path) / f"{as_of.isoformat()}.csv"


def signals_path(cfg: Any = None, path: Path | str | None = None) -> Path:
    """ENTRY候補履歴の CSV。`path` は **ファイル** の上書き指定。"""
    if path is not None:
        return Path(path)
    return journal_dir(cfg) / "signals.csv"


def forward_path(cfg: Any = None, path: Path | str | None = None) -> Path:
    """フォワードレビュー用 CSV。`path` は **ファイル** の上書き指定。"""
    if path is not None:
        return Path(path)
    return journal_dir(cfg) / "forward_review.csv"


# --- 整形 ---------------------------------------------------------------------


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def range_position(close: float | None, lower: float | None, upper: float | None) -> float | None:
    """レンジ内位置（0=下限, 1=上限）。rules/status.py のガードと同じ定義。"""
    if close is None or lower is None or upper is None:
        return None
    span = upper - lower
    if span <= 0:
        return None
    return (close - lower) / span


def snapshot_reason(result: ScreenResult) -> str:
    """1 行の判定理由。OUT は落選理由、それ以外は成立している根拠を並べる。"""
    if result.out_reason:
        return result.out_reason
    parts: list[str] = []
    if result.trend is not None:
        parts.append("上昇トレンド" if result.trend.is_uptrend else "トレンド不成立")
    if result.range_ is not None:
        parts.append(f"レンジ{result.range_.days}日")
        parts.append(f"下限反応{result.range_.lower_touch_count}回")
    if result.distance_to_lower_pct is not None:
        parts.append(f"下限まで{result.distance_to_lower_pct:+.1f}%")
    if result.rebound is not None:
        parts.append("反発確認OK" if result.rebound.confirmed else "反発確認まだ")
    return " / ".join(parts)


def _snapshot_row(result: ScreenResult, as_of: date) -> dict[str, str]:
    range_ = result.range_
    trend = result.trend
    rebound = result.rebound
    volume = result.volume
    lower = range_.lower if range_ else None
    upper = range_.upper if range_ else None
    return {
        "date": _fmt(as_of),
        "code": result.stock.code,
        "name": result.stock.name,
        "sector": result.stock.sector,
        "themes": "|".join(result.stock.theme_names),
        "status": result.status,
        "close": _fmt(result.latest_close),
        "ma25": _fmt(trend.ma if trend else None),
        "range_days": _fmt(range_.days if range_ else None),
        "range_lower": _fmt(lower),
        "range_upper": _fmt(upper),
        "range_position": _fmt(range_position(result.latest_close, lower, upper), digits=3),
        "distance_to_lower_pct": _fmt(result.distance_to_lower_pct),
        "lower_touch_count": _fmt(range_.lower_touch_count if range_ else None),
        "prev_high": _fmt(rebound.prev_high if rebound else None),
        "rebound_confirmed": _fmt(rebound.confirmed if rebound else None),
        "volume_state": volume.state if volume else "",
        "initial_stop": _fmt(result.stop_price),
        "reason": snapshot_reason(result),
    }


# --- 日次スナップショット -------------------------------------------------------


def save_daily_snapshot(
    run: ScreeningRun,
    cfg: Any = None,
    *,
    dir_path: Path | str | None = None,
    force: bool = False,
) -> tuple[Path | None, str]:
    """その日の全銘柄の判定を 1 ファイルに保存する。

    戻り値は (保存先, メッセージ)。保存しなかった場合は (None, 理由)。

    既に同じ日付のファイルがある場合、`force=True` でない限り上書きしない。
    株価は遡って調整されるため、後日再実行した結果で過去日を上書きすると
    「その日に見えていたもの」が失われる。
    """
    if run.as_of is None:
        return None, "データ基準日が不明のため保存しません（株価キャッシュが空）。"

    target = snapshot_path(run.as_of, cfg, dir_path)
    if target.exists() and not force:
        return None, f"{target} は既にあります（上書きしません）。"

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(SNAPSHOT_COLUMNS))
        writer.writeheader()
        for result in run.results:
            writer.writerow(_snapshot_row(result, run.as_of))
    return target, f"{len(run.results)}件を保存しました。"


def load_snapshot(
    as_of: date, cfg: Any = None, *, dir_path: Path | str | None = None
) -> list[dict[str, str]]:
    target = snapshot_path(as_of, cfg, dir_path)
    if not target.exists():
        return []
    with target.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def snapshot_dates(cfg: Any = None, *, dir_path: Path | str | None = None) -> list[date]:
    """保存済みスナップショットの日付（昇順）。"""
    directory = daily_dir(cfg, dir_path)
    if not directory.exists():
        return []
    dates: list[date] = []
    for p in directory.glob("*.csv"):
        try:
            dates.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(dates)


# --- ENTRY 候補履歴 -------------------------------------------------------------


def _signal_row(result: ScreenResult, as_of: date) -> dict[str, str]:
    range_ = result.range_
    trend = result.trend
    rebound = result.rebound
    volume = result.volume
    lower = range_.lower if range_ else None
    upper = range_.upper if range_ else None
    return {
        "signal_date": _fmt(as_of),
        "code": result.stock.code,
        "name": result.stock.name,
        "sector": result.stock.sector,
        "themes": "|".join(result.stock.theme_names),
        "signal_close": _fmt(result.latest_close),
        "ma25": _fmt(trend.ma if trend else None),
        "range_days": _fmt(range_.days if range_ else None),
        "range_lower": _fmt(lower),
        "range_upper": _fmt(upper),
        "range_position": _fmt(range_position(result.latest_close, lower, upper), digits=3),
        "distance_to_lower_pct": _fmt(result.distance_to_lower_pct),
        "lower_touch_count": _fmt(range_.lower_touch_count if range_ else None),
        "prev_high": _fmt(rebound.prev_high if rebound else None),
        "volume_state": volume.state if volume else "",
        "initial_stop": _fmt(result.stop_price),
        "signal_reason": snapshot_reason(result),
    }


def load_signals(cfg: Any = None, *, path: Path | str | None = None) -> list[dict[str, str]]:
    target = signals_path(cfg, path)
    if not target.exists():
        return []
    with target.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def record_signals(
    run: ScreeningRun, cfg: Any = None, *, path: Path | str | None = None
) -> list[dict[str, str]]:
    """ENTRY_CANDIDATE を履歴へ追記する。既にある (signal_date, code) は追記しない。

    実際に買ったかどうかはここでは扱わない（トレード台帳との結合で導く）。
    """
    if run.as_of is None:
        return []

    target = signals_path(cfg, path)
    existing = load_signals(cfg, path=target)
    seen = {(r.get("signal_date"), r.get("code")) for r in existing}

    new_rows: list[dict[str, str]] = []
    for result in run.by_status(STATUS_ENTRY):
        row = _signal_row(result, run.as_of)
        if (row["signal_date"], row["code"]) in seen:
            continue
        new_rows.append(row)

    if not new_rows:
        return []

    target.parent.mkdir(parents=True, exist_ok=True)
    write_header = not target.exists()
    with target.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(SIGNAL_COLUMNS))
        if write_header:
            writer.writeheader()
        for row in new_rows:
            writer.writerow(row)
    return new_rows


def latest_signal_for(
    code: str, cfg: Any = None, *, path: Path | str | None = None
) -> dict[str, str] | None:
    """その銘柄の最も新しい ENTRY 候補履歴（`swing buy` の入力補完に使う）。"""
    rows = [r for r in load_signals(cfg, path=path) if r.get("code") == code]
    if not rows:
        return None
    return max(rows, key=lambda r: r.get("signal_date") or "")


# --- フォワードレビュー用の書き出し ----------------------------------------------


@dataclass(frozen=True)
class ForwardRow:
    """ENTRY 候補 1 件のその後。**集計も判定もしない。並べるだけ。**"""

    values: dict[str, str]


def _float(text: str | None) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_forward_rows(
    signals: Sequence[dict[str, str]],
    trades: Sequence[Trade],
    price_map: dict[str, Any],
) -> list[dict[str, str]]:
    """ENTRY 候補履歴 × トレード台帳 × 株価 を 1 行にまとめる（§19 の保存設計）。

    **これは研究ではない。** 新しい閾値も分類もここでは作らない。後日
    「買わなかった候補も含めて、その後どうなったか」を数えられるだけの
    素データを 1 枚の CSV にしておくためのもの。

    シグナル日の足は含めない（forward は翌営業日以降）。株価キャッシュに
    無い銘柄は、その後の列を空欄にして行だけ残す。
    """
    by_code_date: dict[tuple[str, str], Trade] = {}
    by_code: dict[str, list[Trade]] = {}
    for t in trades:
        if t.signal_date is not None:
            by_code_date[(t.code, t.signal_date.isoformat())] = t
        by_code.setdefault(t.code, []).append(t)

    rows: list[dict[str, str]] = []
    for sig in signals:
        code = sig.get("code", "")
        signal_date = sig.get("signal_date", "")
        trade = by_code_date.get((code, signal_date))
        if trade is None:
            # signal_date を紐づけずに登録された場合は、シグナル日以降で最も近い ENTRY を探す
            for t in sorted(
                by_code.get(code, []), key=lambda x: x.entry_date or date.max
            ):
                if t.entry_date is not None and t.entry_date.isoformat() >= signal_date:
                    trade = t
                    break

        row: dict[str, str] = {c: "" for c in FORWARD_COLUMNS}
        row.update(
            {
                "signal_date": signal_date,
                "code": code,
                "name": sig.get("name", ""),
                "signal_close": sig.get("signal_close", ""),
                "range_lower": sig.get("range_lower", ""),
                "range_upper": sig.get("range_upper", ""),
                "initial_stop": sig.get("initial_stop", ""),
                "purchased": "true" if trade is not None else "false",
            }
        )
        if trade is not None:
            row.update(
                {
                    "entry_date": _fmt(trade.entry_date),
                    "entry_price": _fmt(trade.entry_price),
                    "quantity": _fmt(trade.quantity),
                    "exit_date": _fmt(trade.exit_date),
                    "exit_price": _fmt(trade.exit_price),
                    "exit_reason": trade.exit_reason,
                    "realized_pnl_pct": _fmt(trade.realized_pnl_pct),
                }
            )

        series = price_map.get(code)
        stop = _float(sig.get("initial_stop"))
        upper = _float(sig.get("range_upper"))
        base = _float(sig.get("signal_close"))
        if series is not None and series.bars and base:
            bars = list(series.bars)
            idx = next(
                (i for i, b in enumerate(bars) if b.date.isoformat() == signal_date), None
            )
            if idx is not None:
                future = bars[idx + 1 :]
                row["bars_after_signal"] = str(len(future))
                if future:
                    highest = max(b.high for b in future)
                    lowest = min(b.low for b in future)
                    row["max_gain_pct"] = _fmt((highest - base) / base * 100.0)
                    row["max_loss_pct"] = _fmt((lowest - base) / base * 100.0)
                    row["days_to_max_gain"] = str(
                        next(i + 1 for i, b in enumerate(future) if b.high == highest)
                    )
                    if stop is not None:
                        hit = next(
                            (i + 1 for i, b in enumerate(future) if b.low <= stop), None
                        )
                        row["hit_initial_stop"] = "true" if hit else "false"
                        row["days_to_initial_stop"] = str(hit) if hit else ""
                    if upper is not None:
                        row["reached_range_upper"] = (
                            "true" if highest >= upper else "false"
                        )
                        row["closed_above_range_upper"] = (
                            "true" if any(b.close > upper for b in future) else "false"
                        )
        rows.append(row)
    return rows


def write_forward_rows(
    rows: Iterable[dict[str, str]], cfg: Any = None, *, path: Path | str | None = None
) -> Path:
    target = forward_path(cfg, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(FORWARD_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in FORWARD_COLUMNS})
    return target
