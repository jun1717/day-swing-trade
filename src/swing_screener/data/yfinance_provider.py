"""yfinance を使った日本株OHLCV取得 (DESIGN.md §12.5)。

`code + cfg.data.suffix`（既定 ".T"）を ticker として
`yf.Ticker(...).history(period=cfg.data.fetch_period)` を呼ぶ。

参考: jp-momentum-monitor の YfinanceProvider を、本設計の契約
（PriceSeries/OHLCVBar・DataProvider Protocol・cfg 経由の設定）に合わせて
書き直したもの。industry マッピング等アプリ固有の付随機能は移植していない
（銘柄名・sector は watchlist.csv 側で管理するため不要）。

取得失敗は例外を投げる。呼び出し側（cli.py の fetch）がまとめて警告表示する。
"""

from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

from swing_screener.models import OHLCVBar, PriceSeries

# yfinance は取得ごとに大量のログ（進捗バー・警告）を出すため抑制する。
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


class YfinanceProvider:
    """DataProvider の yfinance 実装。"""

    def __init__(self, cfg) -> None:
        self.suffix: str = cfg.data.suffix
        self.period: str = cfg.data.fetch_period

    def fetch(self, code: str) -> PriceSeries:
        symbol = f"{code}{self.suffix}"
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=self.period)
        except Exception as e:  # yfinance側の例外型が不安定なため Exception で包む
            raise RuntimeError(f"[{code}] yfinance取得失敗 ({symbol}): {e}") from e

        if df is None or df.empty:
            raise RuntimeError(f"[{code}] yfinanceから空のデータが返されました ({symbol})")

        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df.sort_index()

        bars: list[OHLCVBar] = []
        for ts, row in df.iterrows():
            o, h, low, c, v = (
                row.get("Open"),
                row.get("High"),
                row.get("Low"),
                row.get("Close"),
                row.get("Volume"),
            )
            if any(pd.isna(x) for x in (o, h, low, c, v)):
                continue  # NaN行はスキップ
            try:
                bars.append(
                    OHLCVBar(
                        date=ts.date(),
                        open=float(o),
                        high=float(h),
                        low=float(low),
                        close=float(c),
                        volume=int(v),
                    )
                )
            except (TypeError, ValueError):
                continue

        if not bars:
            raise RuntimeError(f"[{code}] 有効な日足データがありませんでした ({symbol})")

        bars.sort(key=lambda b: b.date)
        return PriceSeries(code=code, bars=tuple(bars))
