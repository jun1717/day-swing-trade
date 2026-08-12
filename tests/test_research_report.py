"""検証レポートとチャートの検証（RESEARCH_DESIGN §9, §10）。"""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import make_stock, uptrend_with_range
from swing_screener.config import load_all
from swing_screener.research import classify
from swing_screener.research.charts import render_event_chart, render_event_charts
from swing_screener.research.config import DEFAULT, SWEEP_THRESHOLDS, threshold_label
from swing_screener.research.events import EntryEvent, build_event, write_events_csv
from swing_screener.research.replay import replay_stock
from swing_screener.research.report import write_report
from swing_screener.research.sweep import SweepResult, ThresholdResult, build_sweep
from swing_screener.research.config import with_position_threshold


@pytest.fixture
def sample_sweep(cfg, exp):
    """合成系列からリプレイして、実際の SweepResult を作る。"""
    series = uptrend_with_range(trend_days=110, range_days=6, touch_days=(1, 4)).build()
    stock = make_stock()
    unlimited = with_position_threshold(exp, None)
    days = list(replay_stock(stock, series, cfg, unlimited))
    by_threshold = build_sweep(
        days, {stock.code: stock}, {stock.code: series}, DEFAULT,
        thresholds=SWEEP_THRESHOLDS,
    )
    result = SweepResult(
        start=days[0].date, end=days[-1].date, months=6, warmup=70,
        stock_count=1, trading_days=len(days),
        all_events=by_threshold[threshold_label(None)].events,
        by_threshold=by_threshold, thresholds=SWEEP_THRESHOLDS,
        derivation_verified=True, derivation_mismatches=[],
    )
    return result, series, stock


def test_レポートに必須の注記が含まれる(sample_sweep, cfg, tmp_path):
    result, _, _ = sample_sweep
    path = write_report(result, tmp_path)
    doc = path.read_text(encoding="utf-8")

    # パラメータ最適化ではない旨
    assert "パラメータ最適化ではない" in doc
    # 終値基準の非約定性
    assert "実際には約定できない" in doc
    # 翌日始値が新ルールではない旨
    assert "新しい売買ルールではない" in doc
    # 損切り率の単純比較への警告
    assert "損切り到達率を閾値間で単純比較しない" in doc
    # 結論を出さない旨
    assert "推奨する閾値を示さない" in doc


def test_レポートは自己完結で外部参照しない(sample_sweep, tmp_path):
    result, _, _ = sample_sweep
    doc = write_report(result, tmp_path).read_text(encoding="utf-8")
    for bad in ("http://", "https://", "cdn.", "<script src"):
        assert bad not in doc, f"外部参照が含まれている: {bad}"


def test_レポートに全閾値が出る(sample_sweep, tmp_path):
    result, _, _ = sample_sweep
    doc = write_report(result, tmp_path).read_text(encoding="utf-8")
    for threshold in SWEEP_THRESHOLDS:
        assert threshold_label(threshold) in doc


def test_イベントが0件でもレポートが落ちない(tmp_path):
    empty = SweepResult(
        start=date(2026, 1, 1), end=date(2026, 6, 30), months=6, warmup=70,
        stock_count=0, trading_days=0, all_events=[],
        by_threshold={
            threshold_label(t): ThresholdResult(threshold=t, label=threshold_label(t))
            for t in SWEEP_THRESHOLDS
        },
        thresholds=SWEEP_THRESHOLDS, derivation_verified=True, derivation_mismatches=[],
    )
    path = write_report(empty, tmp_path)
    assert path.exists()
    assert "パラメータ最適化ではない" in path.read_text(encoding="utf-8")


def test_チャートが生成される(sample_sweep, cfg, tmp_path):
    result, series, _ = sample_sweep
    events = result.all_events
    assert events, "合成データで ENTRY が出ていない"

    out = tmp_path / "c.png"
    render_event_chart(events[0], series, cfg, out)
    assert out.exists() and out.stat().st_size > 0


def test_カテゴリ別チャートは該当0件でも落ちない(sample_sweep, cfg, tmp_path):
    result, series, stock = sample_sweep
    paths = render_event_charts(result, {stock.code: series}, cfg, tmp_path)
    assert isinstance(paths, list)
    for p in paths:
        assert p.exists() and p.stat().st_size > 0


def test_eventsCSVに基準価格の注記が入る(sample_sweep, tmp_path):
    result, _, _ = sample_sweep
    path = write_events_csv(result.all_events, tmp_path / "events.csv")
    head = path.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("#")
    assert "実際には不可能" in head
    assert "収益バックテストではない" in head


def test_CSVヘッダに基準価格が明示された列名がある(sample_sweep, tmp_path):
    result, _, _ = sample_sweep
    path = write_events_csv(result.all_events, tmp_path / "events.csv")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if not ln.startswith("#")]
    header = lines[0]
    assert "fwd5_max_gain_pct_from_close" in header
    assert "fwd10_max_gain_pct_from_next_open" in header
    assert "next_open" in header and "gap_pct" in header
