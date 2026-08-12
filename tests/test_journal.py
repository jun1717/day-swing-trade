"""日次スナップショットと ENTRY候補履歴（journal.py）のテスト。

この機能の価値は「**その日ツールが何を表示していたか**を後から再現できること」
にある。したがってここで固定するのは主に次の 2 点:

    1. 過去の記録を未来のデータで書き換えないこと
    2. 同じ日を二重に記録しないこと

株価は配当調整で遡って変わり、レンジ検出も新しい足が付くたびに動く。
上書きを許すとフォワード検証の母数そのものが壊れる。
"""

from __future__ import annotations

import csv
from datetime import date

import pytest
from tests.conftest import SeriesBuilder, make_stock, uptrend_with_range

from swing_screener import journal
from swing_screener.models import STATUS_ENTRY, STATUS_NEAR
from swing_screener.portfolio import Trade
from swing_screener.screener import run_screening


@pytest.fixture
def run_with_entry(cfg, exp):
    """ENTRY / NEAR / OUT が 1 件ずつ入った ScreeningRun。"""
    stocks = [
        make_stock(code="0002", name="ENTRY銘柄"),
        make_stock(code="0004", name="NEAR銘柄"),
        make_stock(code="0003", name="OUT銘柄"),
    ]
    price_map = {
        "0002": uptrend_with_range(range_days=6, touch_days=(1, 4), code="0002").build(),
        "0004": uptrend_with_range(range_days=6, touch_days=(1, 5), code="0004").build(),
        "0003": SeriesBuilder(code="0003").downtrend_to(80, 3000, 15).build(),
    }
    return run_screening(stocks, price_map, cfg, exp), price_map


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- range_position ------------------------------------------------------------


def test_range_position_matches_guard_definition():
    """rules/status.py のレンジ内位置ガードと同じ式であること。"""
    assert journal.range_position(5000, 4900, 5100) == pytest.approx(0.5)
    assert journal.range_position(4900, 4900, 5100) == pytest.approx(0.0)
    assert journal.range_position(5100, 4900, 5100) == pytest.approx(1.0)


def test_range_position_returns_none_on_degenerate_range():
    assert journal.range_position(5000, 5000, 5000) is None
    assert journal.range_position(None, 4900, 5100) is None
    assert journal.range_position(5000, None, 5100) is None


# --- 日次スナップショット ---------------------------------------------------------


def test_snapshot_records_every_stock(tmp_path, run_with_entry):
    run, _ = run_with_entry
    path, msg = journal.save_daily_snapshot(run, dir_path=tmp_path)

    assert path is not None
    assert path.name == f"{run.as_of.isoformat()}.csv"
    rows = read_csv(path)
    assert len(rows) == len(run.results) == 3
    assert {r["code"] for r in rows} == {"0002", "0003", "0004"}
    assert list(rows[0]) == list(journal.SNAPSHOT_COLUMNS)


def test_snapshot_contains_required_columns(tmp_path, run_with_entry):
    """§15 が要求する最低限の項目。"""
    run, _ = run_with_entry
    path, _ = journal.save_daily_snapshot(run, dir_path=tmp_path)
    row = next(r for r in read_csv(path) if r["code"] == "0002")

    assert row["date"] == run.as_of.isoformat()
    assert row["status"] == STATUS_ENTRY
    assert float(row["close"]) > 0
    assert float(row["range_lower"]) > 0
    assert float(row["range_upper"]) > float(row["range_lower"])
    assert 0.0 <= float(row["range_position"]) <= 1.0
    assert float(row["initial_stop"]) == pytest.approx(float(row["range_lower"]) * 0.995, rel=1e-3)
    assert row["reason"]


def test_snapshot_is_not_overwritten_without_force(tmp_path, run_with_entry):
    """後日の再計算で過去日を書き換えないこと（最重要）。"""
    run, _ = run_with_entry
    path, _ = journal.save_daily_snapshot(run, dir_path=tmp_path)
    original = path.read_text(encoding="utf-8")

    path.write_text(original + "改変,,,\n", encoding="utf-8")
    again, msg = journal.save_daily_snapshot(run, dir_path=tmp_path)

    assert again is None
    assert "既にあります" in msg
    assert path.read_text(encoding="utf-8").endswith("改変,,,\n")


def test_snapshot_force_overwrites(tmp_path, run_with_entry):
    run, _ = run_with_entry
    path, _ = journal.save_daily_snapshot(run, dir_path=tmp_path)
    path.write_text("壊れた\n", encoding="utf-8")

    again, _ = journal.save_daily_snapshot(run, dir_path=tmp_path, force=True)
    assert again == path
    assert len(read_csv(path)) == 3


def test_snapshot_skipped_when_as_of_unknown(tmp_path, cfg, exp):
    """株価キャッシュが空でも落ちず、何も書かないこと。"""
    run = run_screening([make_stock()], {}, cfg, exp)
    assert run.as_of is None
    path, msg = journal.save_daily_snapshot(run, dir_path=tmp_path)
    assert path is None and "基準日" in msg
    assert not list(tmp_path.glob("**/*.csv"))


def test_snapshot_dates_and_load(tmp_path, run_with_entry):
    run, _ = run_with_entry
    journal.save_daily_snapshot(run, dir_path=tmp_path)

    assert journal.snapshot_dates(dir_path=tmp_path) == [run.as_of]
    assert len(journal.load_snapshot(run.as_of, dir_path=tmp_path)) == 3
    assert journal.load_snapshot(date(2000, 1, 1), dir_path=tmp_path) == []


# --- ENTRY候補履歴 ---------------------------------------------------------------


def test_only_entry_candidates_are_recorded(tmp_path, run_with_entry):
    run, _ = run_with_entry
    path = tmp_path / "signals.csv"
    added = journal.record_signals(run, path=path)

    assert [r["code"] for r in added] == ["0002"]
    rows = read_csv(path)
    assert len(rows) == 1
    assert list(rows[0]) == list(journal.SIGNAL_COLUMNS)
    assert run.by_status(STATUS_NEAR)  # NEAR は存在するが記録されない


def test_same_signal_is_not_recorded_twice(tmp_path, run_with_entry):
    run, _ = run_with_entry
    path = tmp_path / "signals.csv"
    journal.record_signals(run, path=path)
    again = journal.record_signals(run, path=path)

    assert again == []
    assert len(read_csv(path)) == 1


def test_new_date_is_appended(tmp_path, run_with_entry):
    """既存行を書き換えず、追記だけすること。"""
    run, _ = run_with_entry
    path = tmp_path / "signals.csv"
    journal.record_signals(run, path=path)
    before = read_csv(path)[0]

    run.as_of = date(2026, 12, 1)
    journal.record_signals(run, path=path)

    rows = read_csv(path)
    assert len(rows) == 2
    assert rows[0] == before  # 既存行は 1 文字も変わらない
    assert rows[1]["signal_date"] == "2026-12-01"


def test_latest_signal_for_returns_newest(tmp_path, run_with_entry):
    run, _ = run_with_entry
    path = tmp_path / "signals.csv"
    journal.record_signals(run, path=path)
    run.as_of = date(2026, 12, 1)
    journal.record_signals(run, path=path)

    latest = journal.latest_signal_for("0002", path=path)
    assert latest is not None and latest["signal_date"] == "2026-12-01"
    assert journal.latest_signal_for("9999", path=path) is None


def test_signals_missing_file_returns_empty(tmp_path):
    assert journal.load_signals(path=tmp_path / "nope.csv") == []


# --- フォワードレビュー用の書き出し -------------------------------------------------


def test_forward_rows_flag_purchased_by_signal_date(tmp_path, run_with_entry):
    run, price_map = run_with_entry
    path = tmp_path / "signals.csv"
    journal.record_signals(run, path=path)
    signals = journal.load_signals(path=path)

    not_bought = journal.build_forward_rows(signals, [], price_map)
    assert not_bought[0]["purchased"] == "false"
    assert not_bought[0]["entry_date"] == ""

    trade = Trade(
        code="0002", entry_date=date(2026, 6, 1), entry_price=5000.0,
        signal_date=run.as_of,
    )
    bought = journal.build_forward_rows(signals, [trade], price_map)
    assert bought[0]["purchased"] == "true"
    assert bought[0]["entry_date"] == "2026-06-01"


def test_forward_rows_exclude_the_signal_day_bar(tmp_path, cfg, exp):
    """シグナル日の足は forward に含めない（二重計上の防止）。"""
    builder = uptrend_with_range(range_days=6, touch_days=(1, 4), code="0002")
    signal_series = builder.build()
    signal_date = signal_series.bars[-1].date
    signal_close = signal_series.bars[-1].close

    # シグナル日の翌営業日以降を 3 本足す
    builder.add(signal_close * 1.10)
    builder.add(signal_close * 1.05)
    builder.add(signal_close * 0.90)
    extended = builder.build()

    signals = [
        {
            "signal_date": signal_date.isoformat(),
            "code": "0002",
            "name": "ENTRY銘柄",
            "signal_close": f"{signal_close:.2f}",
            "range_lower": "0",
            "range_upper": "999999",
            "initial_stop": "0",
        }
    ]
    row = journal.build_forward_rows(signals, [], {"0002": extended})[0]

    assert row["bars_after_signal"] == "3"
    assert float(row["max_gain_pct"]) == pytest.approx(10.0, abs=0.5)
    assert float(row["max_loss_pct"]) == pytest.approx(-10.0, abs=0.5)
    assert row["days_to_max_gain"] == "1"  # 翌営業日が 1 日目


def test_forward_rows_survive_missing_price_cache(tmp_path):
    signals = [{"signal_date": "2026-05-01", "code": "9999", "name": "無し"}]
    rows = journal.build_forward_rows(signals, [], {})
    assert len(rows) == 1
    assert rows[0]["code"] == "9999"
    assert rows[0]["bars_after_signal"] == ""


def test_write_forward_rows_uses_fixed_columns(tmp_path):
    rows = [{"signal_date": "2026-05-01", "code": "1234", "unknown": "x"}]
    path = journal.write_forward_rows(rows, path=tmp_path / "forward.csv")
    written = read_csv(path)
    assert list(written[0]) == list(journal.FORWARD_COLUMNS)
    assert written[0]["code"] == "1234"


def test_paths_prefer_config_keys(tmp_path):
    from swing_screener.config import Params

    cfg = Params({"journal": {"dir": str(tmp_path / "custom")}})
    assert journal.journal_dir(cfg).name == "custom"
    assert journal.signals_path(cfg).parent.name == "custom"
    assert journal.daily_dir(Params({})).as_posix() == journal.DEFAULT_JOURNAL_DIR + "/daily"
