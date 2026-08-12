"""OHLCV価格キャッシュの読み書き (DESIGN.md §3 / §12.5)。

`cache/prices/{code}.csv` に `date,open,high,low,close,volume` で保存する。
`screen` コマンドがネットワークに触れず何度でも再実行できることが設計上
重要なため、読み書きは標準の csv モジュールのみで完結させる（pandas 不要）。

破損ファイル・空ファイルを読んだ場合は例外を投げず None を返す。
呼び出し側（cli.py の fetch/screen）がまとめて警告表示できるようにするため。
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from swing_screener.models import OHLCVBar, PriceSeries

_FIELDNAMES = ["date", "open", "high", "low", "close", "volume"]
_META_FILENAME = ".meta.json"


def price_path(code: str, cfg) -> Path:
    """cache/prices/{code}.csv のパスを返す。"""
    return Path(cfg.data.cache_dir) / f"{code}.csv"


def _meta_path(cfg) -> Path:
    return Path(cfg.data.cache_dir) / _META_FILENAME


def save_prices(series: PriceSeries, cfg) -> Path:
    """PriceSeries を CSV に保存する（date 昇順、既存ファイルは上書き）。"""
    path = price_path(series.code, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars = sorted(series.bars, key=lambda b: b.date)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_FIELDNAMES)
        for bar in bars:
            writer.writerow(
                [bar.date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume]
            )
    return path


def load_prices(code: str, cfg) -> PriceSeries | None:
    """CSV から PriceSeries を読む。

    ファイルが存在しない・空・破損している場合は例外を投げず None を返す。
    """
    path = price_path(code, cfg)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return None
            bars: list[OHLCVBar] = []
            for row in reader:
                bars.append(
                    OHLCVBar(
                        date=date.fromisoformat(row["date"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(float(row["volume"])),
                    )
                )
        if not bars:
            return None
        bars.sort(key=lambda b: b.date)
        return PriceSeries(code=code, bars=tuple(bars))
    except Exception:
        # 破損ファイルは例外を投げず None を返す（呼び出し側が警告表示する）。
        return None


def cached_codes(cfg) -> list[str]:
    """キャッシュ済み銘柄コード一覧（アルファベット順、.meta.json は含まない）。"""
    cache_dir = Path(cfg.data.cache_dir)
    if not cache_dir.exists():
        return []
    codes = [p.stem for p in cache_dir.glob("*.csv") if p.stem and not p.name.startswith(".")]
    return sorted(codes)


def last_fetch_at(cfg) -> str | None:
    """cache/prices/.meta.json に記録した最終取得日時（ISO文字列）。無ければ None。"""
    meta_path = _meta_path(cfg)
    if not meta_path.exists():
        return None
    try:
        with meta_path.open(encoding="utf-8") as f:
            data = json.load(f)
        value = data.get("last_fetch_at")
        return str(value) if value else None
    except Exception:
        return None


def record_fetch(cfg) -> None:
    """現在時刻を last_fetch_at として cache/prices/.meta.json に記録する。"""
    meta_path = _meta_path(cfg)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"last_fetch_at": datetime.now().astimezone().isoformat()}
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
