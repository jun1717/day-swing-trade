"""市場セッションの確定判定（**運用設定であって売買ルールではない**）。

TRADING_RULES.md §1 の運用前提は「引け後に確定日足を確認 → 翌営業日の行動を
決める」である。したがって ChatGPT へ渡す日次 bundle は、**その日の取引が
終わったあとの確定日足** でなければならない。場中に取得した未確定の当日足を
「その日の確定データ」として永続化してしまうと、引け後の本物の日足で作り直す
機会が失われる（同じ市場日なので stale 判定で skip されてしまう）。

ここで決めるのは 2 つだけ。

    close_time      東証の大引け（15:30 JST）。**市場の事実**であって設定ではない
    finalize_after  この時刻以降を「当日足が確定した」とみなす運用上の待ち時間

`finalize_after` は **データ提供元（yfinance）の更新待ち** のための安全側の
時刻であって、新しい売買パラメータではない。大引けの直後は終値が未確定・
未反映のことがあるため既定を 16:00 JST に置いている。値を早める方向へ
変更しても `close_time` より前に FINAL になることはない（`finalize_time` が
必ず大引け以降にクランプする）。

判定は「日本時間の今」と「データの市場日」だけで行う。祝日カレンダーは
持たない（休日は株価の日付が進まないので、そもそも新しい市場日にならない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

# 日本株なので基準は Asia/Tokyo（UTC+9 固定・サマータイムなし）。
DEFAULT_TIMEZONE = "Asia/Tokyo"
# 東証の大引け。市場の事実。
DEFAULT_CLOSE_TIME = "15:30"
# データ確定待ちの安全側時刻（運用設定。売買パラメータではない）。
DEFAULT_FINALIZE_AFTER = "16:00"

# manifest.txt へ書く確定状態。永続化・artifact 化してよいのは FINAL だけ。
SESSION_FINAL = "FINAL"
SESSION_INTRADAY = "INTRADAY"


class SessionConfigError(ValueError):
    """market_session の設定値が読めない。"""


def parse_time(text: Any, *, field: str) -> time:
    """"15:30" / "15:30:00" を time にする。YAML が time を返す場合も許す。"""
    if isinstance(text, time):
        return text
    raw = str(text).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise SessionConfigError(
        f"market_session.{field} は HH:MM 形式で書いてください（実際の値: {text!r}）"
    )


def parse_now(text: str | None, tz: ZoneInfo) -> datetime | None:
    """`--now` オプションの文字列を datetime にする（テスト・検証用）。

    tz 情報がなければ市場時間帯（Asia/Tokyo）とみなす。
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise SessionConfigError(
            f"--now は ISO 8601 形式で指定してください（例 2026-08-12T16:10, 実際の値: {text!r}）"
        ) from e
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)


@dataclass(frozen=True)
class MarketSession:
    """「その市場日のセッションは終わっているか」を答えるだけの値オブジェクト。"""

    timezone: str = DEFAULT_TIMEZONE
    close_time: time = time(15, 30)
    finalize_after: time = time(16, 0)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def finalize_time(self) -> time:
        """FINAL とみなす最も早い時刻。

        **大引け前を FINAL にすることは絶対にしない。** 設定で
        `finalize_after` を早めても `close_time` でクランプする。
        """
        return max(self.close_time, self.finalize_after)

    def localize(self, now: datetime | None = None) -> datetime:
        """「今」を市場時間帯（既定 Asia/Tokyo）へ揃える。"""
        if now is None:
            return datetime.now(self.tz)
        if now.tzinfo is None:
            return now.replace(tzinfo=self.tz)
        return now.astimezone(self.tz)

    def is_finalized(self, market_date: date, now: datetime | None = None) -> bool:
        """`market_date` の日足が確定済みとみなせるか。

        - 過去の市場日      → 確定済み（引けはとっくに終わっている）
        - 当日の市場日      → `finalize_time` 以降なら確定済み
        - 未来の市場日      → 確定していない（本来ありえないので安全側に倒す）
        """
        local = self.localize(now)
        if market_date > local.date():
            return False
        if market_date < local.date():
            return True
        return local.time() >= self.finalize_time

    def status(self, market_date: date, now: datetime | None = None) -> str:
        return SESSION_FINAL if self.is_finalized(market_date, now) else SESSION_INTRADAY

    def describe(self) -> str:
        """人間向けの 1 行説明。"""
        return (
            f"{self.timezone} {self.finalize_time.strftime('%H:%M')} 以降"
            f"（大引け {self.close_time.strftime('%H:%M')} + データ確定待ち）"
        )


def load_session(cfg: Any = None) -> MarketSession:
    """config.yaml の `market_session` から読む（キーがなければ既定値）。"""
    if cfg is None:
        return MarketSession()
    timezone = str(cfg.get("market_session.timezone", DEFAULT_TIMEZONE))
    close_time = parse_time(
        cfg.get("market_session.close_time", DEFAULT_CLOSE_TIME), field="close_time"
    )
    finalize_after = parse_time(
        cfg.get("market_session.finalize_after", DEFAULT_FINALIZE_AFTER),
        field="finalize_after",
    )
    return MarketSession(
        timezone=timezone, close_time=close_time, finalize_after=finalize_after
    )
