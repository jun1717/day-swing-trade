"""data/cache.py のテスト。round-trip と破損ファイル耐性を検証する。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from swing_screener.config import Params
from swing_screener.data.cache import (
    cached_codes,
    last_fetch_at,
    load_prices,
    price_path,
    record_fetch,
    save_prices,
)
from swing_screener.models import OHLCVBar, PriceSeries


def _make_cfg(tmp_path: Path) -> Params:
    return Params({"data": {"cache_dir": str(tmp_path / "prices")}})


def _sample_series(code: str = "5803") -> PriceSeries:
    bars = (
        OHLCVBar(date=date(2026, 8, 5), open=100.0, high=105.0, low=99.0, close=104.0, volume=10000),
        OHLCVBar(date=date(2026, 8, 6), open=104.0, high=110.0, low=103.0, close=108.0, volume=12000),
        OHLCVBar(date=date(2026, 8, 7), open=108.0, high=112.0, low=107.0, close=111.0, volume=9000),
    )
    return PriceSeries(code=code, bars=bars)


def test_round_trip_save_and_load(tmp_path):
    cfg = _make_cfg(tmp_path)
    series = _sample_series()

    saved_path = save_prices(series, cfg)
    assert saved_path == price_path("5803", cfg)
    assert saved_path.exists()

    loaded = load_prices("5803", cfg)
    assert loaded is not None
    assert loaded.code == "5803"
    assert len(loaded.bars) == 3
    assert loaded.bars[0].date == date(2026, 8, 5)
    assert loaded.latest is not None
    assert loaded.latest.date == date(2026, 8, 7)
    assert loaded.latest.close == 111.0


def test_round_trip_preserves_all_ohlcv_values(tmp_path):
    cfg = _make_cfg(tmp_path)
    series = _sample_series()
    save_prices(series, cfg)
    loaded = load_prices("5803", cfg)

    assert loaded is not None
    for original, restored in zip(series.bars, loaded.bars):
        assert original.date == restored.date
        assert original.open == restored.open
        assert original.high == restored.high
        assert original.low == restored.low
        assert original.close == restored.close
        assert original.volume == restored.volume


def test_save_sorts_bars_by_date_ascending(tmp_path):
    cfg = _make_cfg(tmp_path)
    # あえて日付を逆順で渡す
    unordered = PriceSeries(
        code="5803",
        bars=(
            OHLCVBar(date=date(2026, 8, 7), open=1, high=1, low=1, close=1, volume=1),
            OHLCVBar(date=date(2026, 8, 5), open=1, high=1, low=1, close=1, volume=1),
            OHLCVBar(date=date(2026, 8, 6), open=1, high=1, low=1, close=1, volume=1),
        ),
    )
    save_prices(unordered, cfg)
    loaded = load_prices("5803", cfg)

    assert loaded is not None
    assert [b.date for b in loaded.bars] == [date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]


def test_load_missing_file_returns_none(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert load_prices("9999", cfg) is None


def test_load_empty_file_returns_none(tmp_path):
    cfg = _make_cfg(tmp_path)
    path = price_path("0001", cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")

    assert load_prices("0001", cfg) is None


def test_load_header_only_file_returns_none(tmp_path):
    cfg = _make_cfg(tmp_path)
    path = price_path("0003", cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("date,open,high,low,close,volume\n", encoding="utf-8")

    assert load_prices("0003", cfg) is None


def test_load_corrupt_file_returns_none_without_raising(tmp_path):
    cfg = _make_cfg(tmp_path)
    path = price_path("0002", cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "date,open,high,low,close,volume\nnot-a-date,abc,def,ghi,jkl,mno\n", encoding="utf-8"
    )

    # 例外を投げず None を返すことが契約
    assert load_prices("0002", cfg) is None


def test_cached_codes_lists_saved_codes_only(tmp_path):
    cfg = _make_cfg(tmp_path)
    save_prices(_sample_series("5803"), cfg)
    save_prices(_sample_series("200A"), cfg)
    record_fetch(cfg)  # .meta.json も同じディレクトリに書かれるが codes には含めない

    assert cached_codes(cfg) == sorted(["5803", "200A"])


def test_cached_codes_empty_when_dir_missing(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert cached_codes(cfg) == []


def test_last_fetch_at_round_trip(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert last_fetch_at(cfg) is None

    record_fetch(cfg)
    fetched_at = last_fetch_at(cfg)

    assert fetched_at is not None
    assert "T" in fetched_at  # ISO8601 形式


def test_last_fetch_at_corrupt_meta_returns_none(tmp_path):
    cfg = _make_cfg(tmp_path)
    meta_path = Path(cfg.data.cache_dir) / ".meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text("{not valid json", encoding="utf-8")

    assert last_fetch_at(cfg) is None
