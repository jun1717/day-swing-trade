"""トレード台帳（portfolio.py）のテスト。

台帳は「ユーザーが実際に何をしたか」の記録であって判定を含まない。
したがってここで固定するのは、**入れた値がそのまま戻ってくること** と、
**矛盾した操作を拒否すること** の 2 点だけである。
"""

from __future__ import annotations

from datetime import date

import pytest

from swing_screener import portfolio
from swing_screener.portfolio import PortfolioError, Trade


def make_trade(code: str = "1234", **kwargs) -> Trade:
    base = dict(
        name="テスト銘柄",
        entry_date=date(2026, 5, 1),
        entry_price=5000.0,
        quantity=100,
        original_range_lower=4900.0,
        original_range_upper=5200.0,
        initial_stop=4875.5,
        entry_reason="レンジ下限反発",
        memo="メモ",
        signal_date=date(2026, 4, 30),
    )
    base.update(kwargs)
    return Trade(code=code, **base)


# --- 保存と読み込み -------------------------------------------------------------


def test_roundtrip_preserves_all_fields(tmp_path):
    path = tmp_path / "trades.csv"
    original = make_trade()
    portfolio.save_trades([original], path=path)

    loaded = portfolio.load_trades(path=path)
    assert len(loaded) == 1
    for field in portfolio.FIELD_NAMES:
        assert getattr(loaded[0], field) == getattr(original, field), field


def test_missing_file_returns_empty_list(tmp_path):
    """初回起動でも落ちないこと。"""
    assert portfolio.load_trades(path=tmp_path / "nope.csv") == []


def test_blank_optional_fields_roundtrip_as_none(tmp_path):
    path = tmp_path / "trades.csv"
    sparse = Trade(code="1234", entry_date=date(2026, 5, 1), entry_price=5000.0)
    portfolio.save_trades([sparse], path=path)

    loaded = portfolio.load_trades(path=path)[0]
    assert loaded.quantity is None
    assert loaded.initial_stop is None
    assert loaded.original_range_lower is None
    assert loaded.exit_date is None
    assert loaded.is_open


def test_column_order_is_stable(tmp_path):
    """列順が変わると過去の CSV との差分が読めなくなる。"""
    path = tmp_path / "trades.csv"
    portfolio.save_trades([make_trade()], path=path)
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == list(portfolio.FIELD_NAMES)
    assert header.startswith("code,name,entry_date,entry_price,quantity")


def test_rows_without_code_are_skipped(tmp_path):
    """手編集で空行が混じっても壊れないこと。"""
    path = tmp_path / "trades.csv"
    path.write_text(
        "code,name,entry_date,entry_price\n"
        "1234,テスト,2026-05-01,5000\n"
        ",,,\n",
        encoding="utf-8",
    )
    assert [t.code for t in portfolio.load_trades(path=path)] == ["1234"]


# --- 保有中 / 決済済み -----------------------------------------------------------


def test_is_open_depends_only_on_exit_date():
    assert make_trade().is_open
    assert not make_trade(exit_date=date(2026, 5, 10)).is_open


def test_open_and_closed_are_partitioned():
    trades = [
        make_trade("1111"),
        make_trade("2222", exit_date=date(2026, 5, 10), exit_price=5300.0),
        make_trade("3333"),
    ]
    assert [t.code for t in portfolio.open_trades(trades)] == ["1111", "3333"]
    assert [t.code for t in portfolio.closed_trades(trades)] == ["2222"]


def test_open_trades_sorted_by_entry_date():
    trades = [
        make_trade("3333", entry_date=date(2026, 5, 3)),
        make_trade("1111", entry_date=date(2026, 5, 1)),
        make_trade("2222", entry_date=date(2026, 5, 2)),
    ]
    assert [t.code for t in portfolio.open_trades(trades)] == ["1111", "2222", "3333"]


# --- 損益 -----------------------------------------------------------------------


def test_realized_pnl():
    t = make_trade(exit_date=date(2026, 5, 10), exit_price=5300.0)
    assert t.realized_pnl_pct == pytest.approx(6.0)
    assert t.realized_pnl_yen == pytest.approx(30000.0)
    assert t.holding_days == 9


def test_unrealized_pnl_uses_current_close():
    assert make_trade().unrealized_pnl_pct(4750.0) == pytest.approx(-5.0)
    assert make_trade().unrealized_pnl_pct(None) is None


def test_pnl_is_none_without_prices():
    t = Trade(code="1234")
    assert t.realized_pnl_pct is None
    assert t.realized_pnl_yen is None
    assert t.unrealized_pnl_pct(5000.0) is None


# --- 更新 -----------------------------------------------------------------------


def test_add_trade_rejects_duplicate_open_position():
    """同一銘柄の買い増しは v1 では扱わない。黙って壊れるより明示的に落とす。"""
    trades = portfolio.add_trade([], make_trade("1234"))
    with pytest.raises(PortfolioError, match="既に保有中"):
        portfolio.add_trade(trades, make_trade("1234"))


def test_add_trade_allows_reentry_after_close():
    trades = [make_trade("1234", exit_date=date(2026, 5, 10), exit_price=5300.0)]
    trades = portfolio.add_trade(trades, make_trade("1234", entry_date=date(2026, 6, 1)))
    assert len(trades) == 2
    assert len(portfolio.open_trades(trades)) == 1


def test_add_trade_requires_code():
    with pytest.raises(PortfolioError):
        portfolio.add_trade([], Trade(code=""))


def test_close_trade_sets_exit_fields():
    trades = [make_trade("1234")]
    trades, closed = portfolio.close_trade(
        trades, "1234", exit_date=date(2026, 5, 12), exit_price=5250.0,
        exit_reason="scenario_break", exit_memo="MA25割れ",
    )
    assert closed.exit_date == date(2026, 5, 12)
    assert closed.exit_price == 5250.0
    assert closed.exit_reason == "scenario_break"
    assert closed.exit_memo == "MA25割れ"
    assert not closed.is_open


def test_close_trade_rejects_unknown_code():
    with pytest.raises(PortfolioError, match="保有中ではありません"):
        portfolio.close_trade([], "9999", exit_date=date(2026, 5, 12), exit_price=100.0)


def test_close_trade_rejects_exit_before_entry():
    trades = [make_trade("1234", entry_date=date(2026, 5, 10))]
    with pytest.raises(PortfolioError, match="より前"):
        portfolio.close_trade(
            trades, "1234", exit_date=date(2026, 5, 1), exit_price=5000.0
        )


def test_exit_reasons_are_examples_not_a_closed_set():
    """自由記述を受け付けること（EXIT_REASONS は記入例にすぎない）。"""
    trades = [make_trade("1234")]
    _, closed = portfolio.close_trade(
        trades, "1234", exit_date=date(2026, 5, 12), exit_price=5250.0,
        exit_reason="決算またぎを避けた",
    )
    assert closed.exit_reason == "決算またぎを避けた"
    assert "initial_stop" in portfolio.EXIT_REASONS


def test_trades_path_prefers_config_key(tmp_path):
    from swing_screener.config import Params

    cfg = Params({"journal": {"trades_csv": str(tmp_path / "custom.csv")}})
    assert portfolio.trades_path(cfg).name == "custom.csv"
    assert portfolio.trades_path(Params({})).as_posix() == portfolio.DEFAULT_TRADES_CSV
