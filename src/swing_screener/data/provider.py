"""株価データプロバイダーの共通インターフェース (DESIGN.md §12.5)。

実装（yfinance 等）を差し替え可能にするための Protocol。
テストでは `fetch(code) -> PriceSeries` を満たす任意のオブジェクトを
DataProvider として渡せる（構造的部分型のため継承は不要）。
"""

from __future__ import annotations

from typing import Protocol

from swing_screener.models import PriceSeries


class DataProvider(Protocol):
    def fetch(self, code: str) -> PriceSeries: ...
