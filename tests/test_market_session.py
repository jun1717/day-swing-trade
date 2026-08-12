"""場中の未確定日足を「その日の確定 bundle」にしないことの検査。

背景（実際に起きたこと）:

    generated_at=2026-08-12T03:45:48+00:00   # = 12:45 JST。まだ場中
    market_data_as_of=2026-08-12             # なのに 8/12 の bundle として保存

こうなると 16:10 の定期実行が「前回 bundle と同じ市場日だから」と skip し、
**場中 12:45 時点の未確定日足が 8/12 の最終 bundle として残る**。

TRADING_RULES.md §1 の運用は「引け後に確定日足を確認 → 翌営業日の行動を決める」
なので、正式 bundle は当日セッション終了後のデータだけでなければならない。

時刻はすべて `--now` / `now=` で注入する。**実時間に依存させない**
（16:00 JST をまたぐと結果が変わるテストを書かない）。

このファイルは売買ロジックを一切扱わない。判定するのは
「その市場日のセッションが終わっているか」だけである。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from tests.test_chatgpt_export import _write_project, read_csv
from typer.testing import CliRunner

from swing_screener import chatgpt_export as export_mod
from swing_screener import market_session as session_mod
from swing_screener.cli import app
from swing_screener.config import load_config
from swing_screener.data.cache import load_prices, save_prices
from swing_screener.market_session import (
    SESSION_FINAL,
    SESSION_INTRADAY,
    MarketSession,
    load_session,
)
from swing_screener.models import PriceSeries
from swing_screener.universe import load_universe

runner = CliRunner()

JST = ZoneInfo("Asia/Tokyo")

# 2026-08-12 は水曜日、2026-08-11 は火曜日（どちらも平日）。
MARKET_DAY = date(2026, 8, 12)
PREV_MARKET_DAY = date(2026, 8, 11)

INTRADAY_NOW = "2026-08-12T12:45:00"  # 東証はまだ取引中
AFTER_CLOSE_NOW = "2026-08-12T16:10:00"  # 定期実行の時刻


# --- MarketSession 単体 --------------------------------------------------------


def test_大引け前は確定していない():
    s = MarketSession()
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 15, 29, tzinfo=JST)) is False
    assert s.status(MARKET_DAY, datetime(2026, 8, 12, 12, 45, tzinfo=JST)) == SESSION_INTRADAY


def test_データ確定待ちの時刻を過ぎたら確定():
    s = MarketSession()
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 16, 0, tzinfo=JST)) is True
    assert s.status(MARKET_DAY, datetime(2026, 8, 12, 16, 10, tzinfo=JST)) == SESSION_FINAL


def test_大引けと確定待ちの間はまだ確定扱いにしない():
    """15:30〜16:00 はデータ提供元の更新待ち。安全側に倒す。"""
    s = MarketSession()
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 15, 45, tzinfo=JST)) is False


def test_過去の市場日は常に確定():
    s = MarketSession()
    assert s.is_finalized(PREV_MARKET_DAY, datetime(2026, 8, 12, 9, 0, tzinfo=JST)) is True


def test_未来の市場日は確定扱いにしない():
    s = MarketSession()
    assert s.is_finalized(date(2026, 8, 13), datetime(2026, 8, 12, 16, 10, tzinfo=JST)) is False


def test_UTCの時刻はJSTへ変換して判定する():
    """GitHub Actions の runner は UTC。03:45 UTC は 12:45 JST でまだ場中。"""
    s = MarketSession()
    utc = ZoneInfo("UTC")
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 3, 45, tzinfo=utc)) is False
    # 07:10 UTC = 16:10 JST
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 7, 10, tzinfo=utc)) is True


def test_tzなしの時刻は市場時間帯とみなす():
    s = MarketSession()
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 12, 45)) is False
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 16, 10)) is True


def test_確定時刻は大引けより前にはできない():
    """設定を早めても 15:30 JST より前を FINAL にはしない。"""
    s = MarketSession(close_time=time(15, 30), finalize_after=time(9, 0))
    assert s.finalize_time == time(15, 30)
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 10, 0, tzinfo=JST)) is False
    assert s.is_finalized(MARKET_DAY, datetime(2026, 8, 12, 15, 30, tzinfo=JST)) is True


def test_configから読む値がv1の運用設定と一致する():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    s = load_session(cfg)

    assert s.timezone == "Asia/Tokyo"
    assert s.close_time == time(15, 30)  # 東証の大引け
    assert s.finalize_after == time(16, 0)  # データ確定待ち（売買パラメータではない）


def test_configにキーがなくても既定値で動く():
    """market_session は運用設定なので、無ければ安全側の既定値を使う。"""
    from swing_screener.config import Params

    s = load_session(Params({}))
    assert (s.timezone, s.close_time, s.finalize_after) == ("Asia/Tokyo", time(15, 30), time(16, 0))


def test_壊れた時刻設定は分かるエラーにする():
    from swing_screener.config import Params

    with pytest.raises(session_mod.SessionConfigError):
        load_session(Params({"market_session": {"close_time": "とても遅く"}}))


# --- 合成プロジェクト（最終足の日付を指定できる） ---------------------------------


def _prev_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _shift_to(series: PriceSeries, last_date: date) -> PriceSeries:
    """系列の最終足が `last_date` になるよう日付だけを振り直す。

    値段は触らない（レンジ判定・ENTRY判定は元のままであることが前提）。
    """
    dates: list[date] = []
    d = last_date
    for _ in series.bars:
        dates.append(d)
        d = _prev_business_day(d)
    dates.reverse()
    return PriceSeries(
        code=series.code,
        bars=tuple(replace(bar, date=day) for bar, day in zip(series.bars, dates)),
    )


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """最終足が 2026-08-12（水）の合成プロジェクト。"""
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, ["0002", "0004", "0001", "0003"])
    cfg = load_config(tmp_path / "config.yaml")
    for stock in load_universe(cfg):
        series = load_prices(stock.code, cfg)
        assert series is not None
        save_prices(_shift_to(series, MARKET_DAY), cfg)
    return tmp_path


def invoke(*args):
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output + str(result.exception)
    return result


def bundle_root(project: Path) -> Path:
    return project / "output" / "chatgpt"


def bundle_names(project: Path) -> list[str]:
    root = bundle_root(project)
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def github_outputs(result) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in result.output.strip().splitlines() if "=" in line
    )


def write_previous_final(project: Path, as_of: date) -> Path:
    """前回の FINAL bundle があった状態を作る（manifest だけで足りる）。"""
    directory = bundle_root(project) / as_of.isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / export_mod.MANIFEST_FILENAME).write_text(
        "# ChatGPT 分析用データ\n"
        f"market_data_as_of={as_of.isoformat()}\n"
        f"session_status={SESSION_FINAL}\n"
        "is_finalized=true\n",
        encoding="utf-8",
    )
    return directory


def write_intraday_bundle(project: Path, as_of: date) -> Path:
    """場中に作られてしまった bundle（旧実装の出力）を再現する。

    旧実装の manifest には session_status が無い。したがって
    「FINAL ではない」と判定され、引け後の正式生成を妨げてはならない。
    """
    directory = bundle_root(project) / as_of.isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / export_mod.MANIFEST_FILENAME).write_text(
        "# ChatGPT 分析用データ\n"
        "generated_at=2026-08-12T03:45:48+00:00\n"
        f"market_data_as_of={as_of.isoformat()}\n"
        "candidate_count=99\n",
        encoding="utf-8",
    )
    (directory / export_mod.CANDIDATES_FILENAME).write_text("場中の残骸\n", encoding="utf-8")
    (directory / export_mod.DAILY_BARS_FILENAME).write_text("場中の残骸\n", encoding="utf-8")
    return directory


# --- ケース1: 場中の手動実行 --------------------------------------------------------


def test_ケース1_場中は正式bundleを作らない(project):
    """12:45 JST に workflow_dispatch した場合。何も保存せず正常終了する。"""
    result = invoke("chatgpt-export", "--now", INTRADAY_NOW)

    assert "Market session is not closed yet." in result.output
    assert "No finalized bundle was generated." in result.output
    # output/chatgpt/2026-08-12 を作らない
    assert bundle_names(project) == []


def test_ケース1_market_checkは場中をhas_new_data_falseにする(project):
    result = invoke("market-check", "--format", "github", "--now", INTRADAY_NOW)
    out = github_outputs(result)

    assert out["has_new_data"] == "false"
    assert out["skip_reason"] == "session_not_closed"
    assert out["session_status"] == SESSION_INTRADAY
    assert out["is_finalized"] == "false"
    assert out["market_date"] == MARKET_DAY.isoformat()


def test_ケース1_場中はsignalsも日次記録も増えない(project):
    """workflow は has_new_data で `swing daily` ごと飛ばす。その前提を固定する。"""
    invoke("chatgpt-export", "--now", INTRADAY_NOW)

    assert not (project / "data" / "journal").exists()
    assert bundle_names(project) == []


# --- ケース2: 引け後の正式実行 --------------------------------------------------------


def test_ケース2_引け後は当日のFINAL_bundleを作る(project):
    write_previous_final(project, PREV_MARKET_DAY)

    check = github_outputs(
        invoke("market-check", "--format", "github", "--now", AFTER_CLOSE_NOW)
    )
    assert check["has_new_data"] == "true"
    assert check["session_status"] == SESSION_FINAL
    assert check["last_final_export_date"] == PREV_MARKET_DAY.isoformat()

    invoke("chatgpt-export", "--now", AFTER_CLOSE_NOW)

    directory = bundle_root(project) / MARKET_DAY.isoformat()
    assert directory.exists()
    meta = export_mod.read_manifest_meta(directory)
    assert meta["market_data_as_of"] == MARKET_DAY.isoformat()
    assert meta["session_status"] == SESSION_FINAL
    assert meta["is_finalized"] == "true"
    assert meta["market_timezone"] == "Asia/Tokyo"
    assert export_mod.is_finalized_bundle(directory) is True
    assert read_csv(directory / "candidates.csv")  # 本番の候補が入っている

    # workflow と同じ検査（session_status=FINAL を含む）を通ること
    invoke("chatgpt-validate", "--date", MARKET_DAY.isoformat())


def test_ケース2_manifestは日本時間の生成時刻も持つ(project):
    """generated_at が UTC でも「引け後か」を manifest だけで確認できること。"""
    invoke("chatgpt-export", "--now", "2026-08-12T07:10:00+00:00")

    meta = export_mod.read_manifest_meta(bundle_root(project) / MARKET_DAY.isoformat())
    assert meta["generated_at_market_tz"].startswith("2026-08-12T16:10")
    assert meta["session_status"] == SESSION_FINAL


# --- ケース3: 場中 bundle が残っていても引け後に FINAL を作る -----------------------------


def test_ケース3_場中bundleは引け後の正式生成を妨げない(project):
    """**この対応の中心。** 同じ市場日でも FINAL でなければ stale 扱いしない。"""
    write_previous_final(project, PREV_MARKET_DAY)
    stale = write_intraday_bundle(project, MARKET_DAY)
    assert export_mod.is_finalized_bundle(stale) is False

    check = github_outputs(
        invoke("market-check", "--format", "github", "--now", AFTER_CLOSE_NOW)
    )
    assert check["has_new_data"] == "true", "場中 bundle のせいで skip されている"
    assert check["last_export_date"] == MARKET_DAY.isoformat()
    assert check["last_final_export_date"] == PREV_MARKET_DAY.isoformat()

    # workflow と同じ `--skip-existing` でも上書きされること
    invoke("chatgpt-export", "--skip-existing", "--now", AFTER_CLOSE_NOW)

    assert export_mod.is_finalized_bundle(stale) is True
    assert "場中の残骸" not in (stale / export_mod.CANDIDATES_FILENAME).read_text(
        encoding="utf-8"
    )
    assert export_mod.read_manifest_meta(stale)["candidate_count"] != "99"


def test_ケース3_場中bundleは検査で落ちる(project):
    """未確定 bundle が artifact / automation-data へ渡らない最後の関門。"""
    write_intraday_bundle(project, MARKET_DAY)

    result = runner.invoke(app, ["chatgpt-validate"])
    assert result.exit_code == 1
    assert "session_status=FINAL ではない" in result.output


# --- ケース4: 同日再実行（冪等） -------------------------------------------------------


def test_ケース4_引け後の再実行は重複bundleを作らない(project):
    invoke("chatgpt-export", "--now", AFTER_CLOSE_NOW)

    check = github_outputs(
        invoke("market-check", "--format", "github", "--now", "2026-08-12T17:00:00")
    )
    assert check["has_new_data"] == "false"
    assert check["skip_reason"] == "already_finalized"
    assert check["session_status"] == SESSION_FINAL

    directory = bundle_root(project) / MARKET_DAY.isoformat()
    before = (directory / "candidates.csv").read_bytes()

    result = invoke("chatgpt-export", "--skip-existing", "--now", "2026-08-12T17:00:00")
    assert "既にあります" in result.output
    assert (directory / "candidates.csv").read_bytes() == before
    assert bundle_names(project) == [MARKET_DAY.isoformat()]


def test_ケース4_skip_existingなしの再実行でも中身が変わらない(project):
    invoke("chatgpt-export", "--now", AFTER_CLOSE_NOW)
    directory = bundle_root(project) / MARKET_DAY.isoformat()
    before = (directory / "candidates.csv").read_bytes()

    invoke("chatgpt-export", "--now", "2026-08-12T17:30:00")

    assert (directory / "candidates.csv").read_bytes() == before
    assert export_mod.is_finalized_bundle(directory) is True
    assert bundle_names(project) == [MARKET_DAY.isoformat()]


# --- ケース5: 市場休日 ---------------------------------------------------------------


def test_ケース5_市場休日はNo_new_market_dataで終わる(tmp_path, monkeypatch):
    """最新の市場データが前営業日のままで、前回 FINAL も同じ日付のとき。"""
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, ["0002", "0004", "0001", "0003"])
    cfg = load_config(tmp_path / "config.yaml")
    for stock in load_universe(cfg):
        series = load_prices(stock.code, cfg)
        save_prices(_shift_to(series, PREV_MARKET_DAY), cfg)
    write_previous_final(tmp_path, PREV_MARKET_DAY)

    # 休日の引け後相当の時刻に実行しても、新しい市場日はない
    check = github_outputs(
        runner.invoke(app, ["market-check", "--format", "github", "--now", AFTER_CLOSE_NOW])
    )
    assert check["has_new_data"] == "false"
    assert check["skip_reason"] == "already_finalized"
    assert check["market_date"] == PREV_MARKET_DAY.isoformat()

    human = invoke("market-check", "--now", AFTER_CLOSE_NOW)
    assert "No new market data" in human.output

    result = invoke("chatgpt-export", "--skip-existing", "--now", AFTER_CLOSE_NOW)
    assert "既にあります" in result.output
    assert bundle_names(tmp_path) == [PREV_MARKET_DAY.isoformat()]


# --- 過去日の指定 -------------------------------------------------------------------


def test_過去営業日の指定は場中でも書き出せる(project):
    """`--date` で過去日を指定した場合、その日の引けはとっくに終わっている。"""
    invoke("chatgpt-export", "--date", PREV_MARKET_DAY.isoformat(), "--now", INTRADAY_NOW)

    directory = bundle_root(project) / PREV_MARKET_DAY.isoformat()
    assert export_mod.is_finalized_bundle(directory) is True
    assert bundle_names(project) == [PREV_MARKET_DAY.isoformat()]


def test_当日を明示指定しても場中なら書き出さない(project):
    """`--date` は「未来を混ぜない」ための切り出しであって、確定判定の抜け道ではない。"""
    result = invoke("chatgpt-export", "--date", MARKET_DAY.isoformat(), "--now", INTRADAY_NOW)

    assert "No finalized bundle was generated." in result.output
    assert bundle_names(project) == []


# --- workflow 側 ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow_text() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / ".github" / "workflows" / "daily-screening.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict:
    import yaml

    return yaml.safe_load(workflow_text)


def test_場中実行では書き込み系ステップが1つも走らない(workflow):
    """artifact・automation-data・journal のどれにも触れないこと。

    has_new_data=false のとき実行されうるのは、読み取りと通知だけのステップに限る。
    """
    steps = workflow["jobs"]["screen"]["steps"]
    gate = "steps.market.outputs.has_new_data == 'true'"

    always_run = [s for s in steps if "if" not in s]
    allowed = {
        "Checkout",
        "Set up Python",
        "Install dependencies",
        "Restore generated data from data branch",  # 復元のみ。書き出さない
        "Fetch prices",  # cache/ だけ。永続化対象ではない
        "Check for new finalized market data",
    }
    assert {s["name"] for s in always_run} == allowed

    for name in (
        "Screen and record (daily)",  # journal / signals
        "Export ChatGPT market data",
        "Validate exported CSV",
        "Upload ChatGPT market data",  # artifact
        "Persist generated data to data branch",  # automation-data
    ):
        step = next(s for s in steps if s.get("name") == name)
        assert step["if"] == gate, name


def test_workflowは場中実行の理由を出し分ける(workflow_text):
    assert "skip_reason" in workflow_text
    assert "Market session is not closed yet." in workflow_text
    assert "No finalized bundle was generated." in workflow_text


def test_workflowはFINAL_bundleの日付で比較する(workflow_text):
    assert "last_final_export_date" in workflow_text
    # 定期実行の 16:10 JST は維持する
    assert "10 7 * * 1-5" in workflow_text
