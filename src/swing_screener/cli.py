"""CLI (DESIGN.md §11 / typer)。

毎日使うのは `fetch` → `daily` → `serve` の 3 つ。

    swing fetch [--code CODE] [--force]    # 株価取得 → cache/prices/（ネットワークはここだけ）
    swing daily                            # スクリーニング + 記録 + 保有レビューを一度に
    swing serve [--port 8000]              # Web UI 起動

保有・トレード記録:

    swing holdings                         # 保有銘柄の当日レビュー
    swing buy CODE --price P [--qty N]     # 購入を記録
    swing sell CODE --price P [--reason R] # 売却を記録（チャートも保存）

ChatGPT へ渡すデータ（GitHub Actions の日次実行と同じもの）:

    swing chatgpt-export [--date YYYY-MM-DD]   # output/chatgpt/YYYY-MM-DD/ へCSV出力
    swing chatgpt-validate [--date ...]        # 出力済みCSVを検査する
    swing market-check                         # 引け後の新しい確定営業日が来ているか

`chatgpt-export` / `market-check` は **当日セッションの終了前は正式 bundle を
作らない**（config.yaml: market_session。既定 16:00 JST 以降）。場中の未確定
日足を「その日の確定データ」として残さないため。両コマンドの `--now` は
時刻を注入する検証・テスト用オプションで、運用では使わない。

その他:

    swing normalize                        # watchlist.csv → stocks.csv / stock_themes.csv
    swing screen [--json PATH]             # スクリーニングのみ（記録しない）
    swing chart CODE [--days 120]          # 単体チャートPNG生成
    swing forward-export                   # フォワード検証用CSVの書き出し

`screen` / `chart` / `serve` は他エージェント担当モジュール（screener.py /
charting.py / explain.py / web/app.py）を DESIGN.md §12.5 のシグネチャで
呼ぶだけ。並行開発中はこれらが未実装で ImportError になり得るが、
`normalize` / `fetch` はそれとは独立に動作する必要があるため、他モジュールの
import は各コマンド関数の内部で遅延させている（モジュールロード時に
まとめて import しない）。

注意: PEP604 (`str | None`) 型注釈を typer が実行時に解決できるよう、
このファイルでは `from __future__ import annotations` を使わない
（postponed evaluation と typer の相性問題を避けるため）。
"""

import time
from datetime import date
from pathlib import Path

import typer

from swing_screener import config as config_mod
from swing_screener import models
from swing_screener.data.cache import last_fetch_at, load_prices, record_fetch, save_prices
from swing_screener.data.yfinance_provider import YfinanceProvider
from swing_screener.universe import load_universe, normalize_watchlist

app = typer.Typer(add_completion=False, help="日本株 日足短期スイング スクリーナー")

_CONFIG_OPTION = typer.Option(
    str(config_mod.DEFAULT_CONFIG_PATH), "--config", help="config.yaml のパス"
)
_EXPERIMENTAL_OPTION = typer.Option(
    str(config_mod.DEFAULT_EXPERIMENTAL_PATH), "--experimental", help="experimental.yaml のパス"
)


def _load_cfg(config: str):
    return config_mod.load_config(config)


def _load_cfg_exp(config: str, experimental: str):
    return config_mod.load_config(config), config_mod.load_experimental(experimental)


@app.command()
def normalize(config: str = _CONFIG_OPTION) -> None:
    """watchlist.csv → stocks.csv / stock_themes.csv に正規化する。"""
    cfg = _load_cfg(config)
    stocks, warnings = normalize_watchlist(cfg)

    typer.echo(f"銘柄数: {len(stocks)}")
    typer.echo(f"  → {cfg.universe.stocks_csv}")
    typer.echo(f"  → {cfg.universe.stock_themes_csv}")
    for w in warnings:
        typer.secho(f"警告: {w}", fg=typer.colors.YELLOW)


@app.command()
def fetch(
    code: str | None = typer.Option(None, "--code", help="このコードのみ取得する"),
    force: bool = typer.Option(False, "--force", help="当日取得済みでも再取得する"),
    config: str = _CONFIG_OPTION,
) -> None:
    """株価を取得して cache/prices/ に保存する（ネットワークアクセスはここだけ）。"""
    cfg = _load_cfg(config)
    stocks = load_universe(cfg)

    if code is not None:
        stocks = [s for s in stocks if s.code == code]
        if not stocks:
            typer.secho(f"コード {code} は監視銘柄に見つかりません。", fg=typer.colors.RED)
            raise typer.Exit(1)

    provider = YfinanceProvider(cfg)
    today = date.today()
    total = len(stocks)
    failed: list[str] = []
    fetched = 0
    skipped = 0

    for i, stock in enumerate(stocks, start=1):
        prefix = f"[{i}/{total}] {stock.code} {stock.name}"

        if not force:
            existing = load_prices(stock.code, cfg)
            if existing is not None and existing.latest is not None and existing.latest.date == today:
                typer.echo(f"{prefix} ... スキップ（当日取得済み）")
                skipped += 1
                continue

        try:
            series = provider.fetch(stock.code)
            save_prices(series, cfg)
            fetched += 1
            typer.echo(f"{prefix} ... OK ({len(series.bars)}本)")
        except Exception as e:
            failed.append(stock.code)
            typer.secho(f"{prefix} ... 失敗: {e}", fg=typer.colors.RED)

        # 1銘柄の失敗で全体を止めない。実際にAPIへアクセスした場合のみ間隔を空ける。
        if i < total and cfg.data.sleep_sec:
            time.sleep(cfg.data.sleep_sec)

    record_fetch(cfg)

    typer.echo(f"完了: 取得成功 {fetched} / スキップ {skipped} / 失敗 {len(failed)} / 全体 {total}")
    if failed:
        typer.secho(f"失敗銘柄: {', '.join(failed)}", fg=typer.colors.RED)


@app.command()
def screen(
    json_path: str | None = typer.Option(None, "--json", help="結果の保存先を指定する（既定は output/ 配下）"),
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """キャッシュ済み価格からスクリーニングする（オフライン、何度でも再実行可）。"""
    from swing_screener import screener  # 他エージェント担当モジュール（DESIGN.md §12.5）

    cfg, exp = _load_cfg_exp(config, experimental)
    stocks = load_universe(cfg)

    price_map, warnings = screener.load_price_map(stocks, cfg)
    for w in warnings:
        typer.secho(f"警告: {w}", fg=typer.colors.YELLOW)

    run = screener.run_screening(stocks, price_map, cfg, exp)
    saved_path = screener.save_run(run, cfg, Path(json_path) if json_path else None)

    counts = run.counts()
    typer.echo(
        "ENTRY_CANDIDATE: {e} / NEAR: {n} / RANGE: {r} / OUT: {o}".format(
            e=counts.get(models.STATUS_ENTRY, 0),
            n=counts.get(models.STATUS_NEAR, 0),
            r=counts.get(models.STATUS_RANGE, 0),
            o=counts.get(models.STATUS_OUT, 0),
        )
    )
    typer.echo(f"保存先: {saved_path}")

    for status in (models.STATUS_ENTRY, models.STATUS_NEAR):
        results = run.by_status(status)
        if not results:
            continue
        typer.echo(f"--- {status} ---")
        for r in results:
            dist = (
                f"{r.distance_to_lower_pct:+.1f}%" if r.distance_to_lower_pct is not None else "-"
            )
            price = f"{r.latest_close:,.0f}円" if r.latest_close is not None else "-"
            typer.echo(f"  {r.stock.code} {r.stock.name}  現在値{price}  下限まで{dist}")


@app.command()
def chart(
    code: str = typer.Argument(..., help="銘柄コード"),
    days: int = typer.Option(120, "--days", help="表示日数"),
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """1銘柄の日足チャートPNGを生成する。"""
    from swing_screener import screener  # 他エージェント担当モジュール（DESIGN.md §12.5）
    from swing_screener.charting import render_daily_chart  # 他エージェント担当モジュール

    cfg, exp = _load_cfg_exp(config, experimental)
    stocks = load_universe(cfg)
    stock = next((s for s in stocks if s.code == code), None)
    if stock is None:
        typer.secho(f"コード {code} は監視銘柄に見つかりません。", fg=typer.colors.RED)
        raise typer.Exit(1)

    series = load_prices(code, cfg)
    if series is None:
        typer.secho(
            f"{code} の価格キャッシュがありません。先に `swing fetch --code {code}` を実行してください。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    result = screener.screen_one(stock, series, cfg, exp)

    chart_dir = Path(cfg.output.chart_dir)
    chart_dir.mkdir(parents=True, exist_ok=True)
    output_path = chart_dir / f"{code}.png"

    saved = render_daily_chart(series, result, cfg, exp, output_path, days=days)
    typer.echo(f"チャート生成: {saved}")


# --- 毎日の運用 ----------------------------------------------------------------


def _run_screening(config: str, experimental: str):
    """キャッシュ済み株価からスクリーニングする（ネットワークに触れない）。"""
    from swing_screener import screener

    cfg, exp = _load_cfg_exp(config, experimental)
    stocks = load_universe(cfg)
    price_map, warnings = screener.load_price_map(stocks, cfg)
    run = screener.run_screening(stocks, price_map, cfg, exp)
    return cfg, exp, price_map, run, warnings


def _print_candidates(run) -> None:
    for status in (models.STATUS_ENTRY, models.STATUS_NEAR):
        results = run.by_status(status)
        typer.echo(f"--- {status} ({len(results)}件) ---")
        if not results:
            typer.echo("  なし")
            continue
        for r in results:
            dist = f"{r.distance_to_lower_pct:+.1f}%" if r.distance_to_lower_pct is not None else "-"
            price = f"{r.latest_close:,.0f}円" if r.latest_close is not None else "-"
            stop = f"{r.stop_price:,.0f}円" if r.stop_price is not None else "-"
            typer.echo(f"  {r.stock.code} {r.stock.name}  {price}  下限まで{dist}  初期STOP {stop}")


def _print_holdings(views) -> None:
    from swing_screener import review as review_mod

    if not views:
        typer.echo("  保有銘柄の登録はありません（swing buy で記録できます）。")
        return
    colors = {
        review_mod.LEVEL_SCENARIO_RISK: typer.colors.RED,
        review_mod.LEVEL_CAUTION: typer.colors.YELLOW,
        review_mod.LEVEL_REVIEW: typer.colors.CYAN,
        review_mod.LEVEL_NONE: None,
    }
    for v in views:
        pnl = f"{v.pnl_pct:+.1f}%" if v.pnl_pct is not None else "-"
        close = f"{v.close:,.0f}円" if v.close is not None else "-"
        stop = f"{v.initial_stop:,.0f}円" if v.initial_stop is not None else "-"
        dist = f"{v.distance_to_stop_pct:+.1f}%" if v.distance_to_stop_pct is not None else "-"
        head = (
            f"  [{v.level}] {v.trade.code} {v.trade.name}  {close} ({pnl})"
            f"  初期STOP {stop} (あと{dist})"
        )
        typer.secho(head, fg=colors.get(v.level))
        for sign in v.signs:
            typer.echo(f"      ・{sign.label}: {sign.detail}")
        if v.note:
            typer.secho(f"      ! {v.note}", fg=typer.colors.YELLOW)


@app.command()
def daily(
    force_snapshot: bool = typer.Option(
        False, "--force-snapshot", help="同じ日付のスナップショットを上書きする"
    ),
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """毎日の 1 コマンド。スクリーニング → 記録 → 保有レビューまでを行う。

    株価は取得しない（先に `swing fetch` を実行すること）。
    """
    from swing_screener import journal, portfolio
    from swing_screener import review as review_mod
    from swing_screener import screener

    cfg, exp, price_map, run, warnings = _run_screening(config, experimental)
    for w in warnings:
        typer.secho(f"警告: {w}", fg=typer.colors.YELLOW)

    counts = run.counts()
    typer.echo("")
    typer.secho(f"■ データ基準日: {run.as_of}", bold=True)
    typer.echo(
        "ENTRY_CANDIDATE: {e} / NEAR: {n} / RANGE: {r} / OUT: {o}".format(
            e=counts.get(models.STATUS_ENTRY, 0),
            n=counts.get(models.STATUS_NEAR, 0),
            r=counts.get(models.STATUS_RANGE, 0),
            o=counts.get(models.STATUS_OUT, 0),
        )
    )

    saved_path = screener.save_run(run, cfg)
    typer.echo(f"スクリーニング結果: {saved_path}")

    snap_path, snap_msg = journal.save_daily_snapshot(run, cfg, force=force_snapshot)
    if snap_path is not None:
        typer.echo(f"日次スナップショット: {snap_path}  {snap_msg}")
    else:
        typer.secho(f"日次スナップショット: {snap_msg}", fg=typer.colors.YELLOW)

    new_signals = journal.record_signals(run, cfg)
    if new_signals:
        typer.secho(f"ENTRY候補を履歴へ追加: {len(new_signals)}件", fg=typer.colors.GREEN)
    else:
        typer.echo("ENTRY候補の履歴追加: なし")

    typer.echo("")
    typer.secho("■ 今日の候補", bold=True)
    _print_candidates(run)

    typer.echo("")
    typer.secho("■ 保有銘柄", bold=True)
    trades = portfolio.load_trades(cfg)
    views = review_mod.build_views(portfolio.open_trades(trades), price_map, cfg, exp)
    _print_holdings(views)

    typer.echo("")
    typer.secho(
        "次にやること: 上の候補と保有銘柄の日足チャートを確認してください（swing serve）。"
        "このツールは売買判定をしません。",
        fg=typer.colors.BRIGHT_BLACK,
    )


@app.command()
def holdings(
    closed: bool = typer.Option(False, "--closed", help="決済済みのトレードを表示する"),
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """保有銘柄の当日レビューを表示する（売買判定はしない）。"""
    from swing_screener import portfolio
    from swing_screener import review as review_mod

    cfg, exp, price_map, _run, _warnings = _run_screening(config, experimental)
    trades = portfolio.load_trades(cfg)

    if closed:
        done = portfolio.closed_trades(trades)
        typer.secho(f"■ 決済済み {len(done)}件", bold=True)
        for t in done:
            pnl = f"{t.realized_pnl_pct:+.1f}%" if t.realized_pnl_pct is not None else "-"
            typer.echo(
                f"  {t.code} {t.name}  {t.entry_date} → {t.exit_date}  "
                f"{t.entry_price:,.0f}円 → {t.exit_price:,.0f}円  {pnl}  理由: {t.exit_reason or '—'}"
            )
        return

    views = review_mod.build_views(portfolio.open_trades(trades), price_map, cfg, exp)
    levels = review_mod.summarize_levels(views)
    typer.secho(f"■ 保有 {len(views)}件", bold=True)
    typer.echo(
        "  SCENARIO_RISK {a} / CAUTION {b} / REVIEW {c} / OK {d}".format(
            a=levels.get(review_mod.LEVEL_SCENARIO_RISK, 0),
            b=levels.get(review_mod.LEVEL_CAUTION, 0),
            c=levels.get(review_mod.LEVEL_REVIEW, 0),
            d=levels.get(review_mod.LEVEL_NONE, 0),
        )
    )
    _print_holdings(views)
    typer.secho(
        "  ※ いずれも「チャートを見るべき理由」であって売り判定ではありません。",
        fg=typer.colors.BRIGHT_BLACK,
    )


# --- トレード記録 ---------------------------------------------------------------

# 記入例。**自動判定ルールではない**（portfolio.EXIT_REASONS と同じ並び）。
EXIT_REASON_HINT = (
    "initial_stop",
    "scenario_break",
    "warning_candle",
    "support_break",
    "profit_protection",
    "discretionary",
    "other",
)


def _float_or_none(text):
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@app.command()
def buy(
    code: str = typer.Argument(..., help="銘柄コード"),
    price: float = typer.Option(..., "--price", help="実際の取得単価"),
    quantity: int = typer.Option(None, "--qty", help="株数"),
    entry_date: str = typer.Option(None, "--date", help="購入日 YYYY-MM-DD（既定は今日）"),
    signal_date: str = typer.Option(None, "--signal-date", help="紐づける ENTRY候補の日付"),
    stop: float = typer.Option(None, "--stop", help="初期STOP（既定は候補履歴の値）"),
    lower: float = typer.Option(None, "--lower", help="買ったときのレンジ下限"),
    upper: float = typer.Option(None, "--upper", help="買ったときのレンジ上限"),
    reason: str = typer.Option("", "--reason", help="買った理由（シナリオ）"),
    memo: str = typer.Option("", "--memo", help="メモ"),
    config: str = _CONFIG_OPTION,
) -> None:
    """購入を記録する。レンジ・初期STOPは ENTRY候補履歴から補完する。"""
    from swing_screener import journal, portfolio

    cfg = _load_cfg(config)
    stocks = load_universe(cfg)
    stock = next((s for s in stocks if s.code == code), None)

    sig = None
    if signal_date:
        sig = next(
            (
                r
                for r in journal.load_signals(cfg)
                if r.get("code") == code and r.get("signal_date") == signal_date
            ),
            None,
        )
        if sig is None:
            typer.secho(
                f"{code} の {signal_date} の ENTRY候補履歴が見つかりません。",
                fg=typer.colors.YELLOW,
            )
    else:
        sig = journal.latest_signal_for(code, cfg)

    trade = portfolio.Trade(
        code=code,
        name=stock.name if stock else (sig.get("name", "") if sig else ""),
        entry_date=date.fromisoformat(entry_date) if entry_date else date.today(),
        entry_price=price,
        quantity=quantity,
        original_range_lower=(
            lower if lower is not None else _float_or_none(sig.get("range_lower") if sig else None)
        ),
        original_range_upper=(
            upper if upper is not None else _float_or_none(sig.get("range_upper") if sig else None)
        ),
        initial_stop=(
            stop if stop is not None else _float_or_none(sig.get("initial_stop") if sig else None)
        ),
        entry_reason=reason or (sig.get("signal_reason", "") if sig else ""),
        memo=memo,
        signal_date=(
            date.fromisoformat(sig["signal_date"]) if sig and sig.get("signal_date") else None
        ),
    )

    trades = portfolio.load_trades(cfg)
    try:
        trades = portfolio.add_trade(trades, trade)
    except portfolio.PortfolioError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1)
    saved = portfolio.save_trades(trades, cfg)

    typer.secho(f"記録しました: {trade.code} {trade.name}", fg=typer.colors.GREEN)
    typer.echo(f"  購入日: {trade.entry_date}  単価: {trade.entry_price:,.0f}円  株数: {trade.quantity or '—'}")
    typer.echo(
        f"  買ったときのレンジ: {trade.original_range_lower or '—'} 〜 {trade.original_range_upper or '—'}"
        f"  初期STOP: {trade.initial_stop or '—'}"
    )
    if trade.initial_stop is None:
        typer.secho(
            "  初期STOPが空です。--stop で指定するか、data/trades.csv を編集してください。",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  台帳: {saved}")
    typer.secho(
        "  ※ 初期STOPは最大損失を保証しません（ギャップダウンで下回って約定し得ます）。",
        fg=typer.colors.BRIGHT_BLACK,
    )


@app.command()
def sell(
    code: str = typer.Argument(..., help="銘柄コード"),
    price: float = typer.Option(..., "--price", help="実際の売却単価"),
    exit_date: str = typer.Option(None, "--date", help="売却日 YYYY-MM-DD（既定は今日）"),
    reason: str = typer.Option("", "--reason", help=f"売却理由（例: {', '.join(EXIT_REASON_HINT)}）"),
    memo: str = typer.Option("", "--memo", help="メモ"),
    no_chart: bool = typer.Option(False, "--no-chart", help="チャートPNGを保存しない"),
    config: str = _CONFIG_OPTION,
) -> None:
    """売却を記録し、ENTRY時点と EXIT時点のチャートPNGを保存する。"""
    from swing_screener import portfolio

    cfg = _load_cfg(config)
    trades = portfolio.load_trades(cfg)
    try:
        trades, trade = portfolio.close_trade(
            trades,
            code,
            exit_date=date.fromisoformat(exit_date) if exit_date else date.today(),
            exit_price=price,
            exit_reason=reason,
            exit_memo=memo,
        )
    except portfolio.PortfolioError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1)
    saved = portfolio.save_trades(trades, cfg)

    pnl = trade.realized_pnl_pct
    typer.secho(f"記録しました: {trade.code} {trade.name}", fg=typer.colors.GREEN)
    typer.echo(
        f"  {trade.entry_date} {trade.entry_price:,.0f}円 → {trade.exit_date} {trade.exit_price:,.0f}円"
        + (f"  {pnl:+.1f}%" if pnl is not None else "")
    )
    typer.echo(f"  理由: {trade.exit_reason or '—'}")
    typer.echo(f"  台帳: {saved}")

    if not no_chart:
        for path in _save_trade_charts(trade, cfg):
            typer.echo(f"  チャート: {path}")


def _save_trade_charts(trade, cfg) -> list:
    """ENTRY時点と EXIT時点のチャートを保存する（後から見返すため）。

    `as_of` を渡して当時までの足だけで描くので、あとから再実行しても
    「そのとき見えていた形」が変わらない。
    """
    from swing_screener.charting import render_holding_chart

    series = load_prices(trade.code, cfg)
    if series is None or not series.bars:
        typer.secho(f"  {trade.code} の株価キャッシュがないためチャートは保存しません。", fg=typer.colors.YELLOW)
        return []

    out_dir = Path(str(cfg.get("output.dir", "output"))) / "trades"
    stamp = trade.entry_date.isoformat() if trade.entry_date else "unknown"
    saved = []
    for label, as_of in (("entry", trade.entry_date), ("exit", trade.exit_date)):
        if as_of is None:
            continue
        path = out_dir / f"{trade.code}_{stamp}_{label}.png"
        try:
            saved.append(render_holding_chart(series, trade, cfg, path, days=120, as_of=as_of))
        except Exception as e:  # 記録そのものは成功しているので描画失敗で落とさない
            typer.secho(f"  チャート生成に失敗: {e}", fg=typer.colors.YELLOW)
    return saved


@app.command("trade-chart")
def trade_chart(
    code: str = typer.Argument(..., help="銘柄コード"),
    days: int = typer.Option(120, "--days", help="表示日数"),
    as_of: str = typer.Option(None, "--as-of", help="この日までの足で描く YYYY-MM-DD"),
    config: str = _CONFIG_OPTION,
) -> None:
    """保有銘柄（または決済済みトレード）のチャートPNGを生成する。"""
    from swing_screener import portfolio
    from swing_screener.charting import render_holding_chart

    cfg = _load_cfg(config)
    trades = portfolio.load_trades(cfg)
    trade = portfolio.find_open(trades, code)
    if trade is None:
        done = [t for t in portfolio.closed_trades(trades) if t.code == code]
        trade = done[0] if done else None
    if trade is None:
        typer.secho(f"{code} のトレード記録がありません。", fg=typer.colors.RED)
        raise typer.Exit(1)

    series = load_prices(code, cfg)
    if series is None:
        typer.secho(f"{code} の価格キャッシュがありません。", fg=typer.colors.RED)
        raise typer.Exit(1)

    out_dir = Path(str(cfg.get("output.dir", "output"))) / "trades"
    stamp = trade.entry_date.isoformat() if trade.entry_date else "unknown"
    path = out_dir / f"{code}_{stamp}_holding.png"
    saved = render_holding_chart(
        series, trade, cfg, path, days=days,
        as_of=date.fromisoformat(as_of) if as_of else None,
    )
    typer.echo(f"チャート生成: {saved}")


@app.command("forward-export")
def forward_export(
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """フォワード検証用の素データを 1 枚の CSV に書き出す。

    **集計も判定もしない。** ENTRY候補履歴・トレード台帳・その後の値動きを
    横に並べるだけ。新しい研究を始めるのは、十分な件数が貯まってからにすること
    （RESEARCH_SUMMARY.md の結論）。
    """
    from swing_screener import journal, portfolio, screener

    cfg = _load_cfg(config)
    stocks = load_universe(cfg)
    price_map, _warnings = screener.load_price_map(stocks, cfg)

    signals = journal.load_signals(cfg)
    trades = portfolio.load_trades(cfg)
    rows = journal.build_forward_rows(signals, trades, price_map)
    path = journal.write_forward_rows(rows, cfg)

    purchased = sum(1 for r in rows if r.get("purchased") == "true")
    typer.echo(f"ENTRY候補 {len(rows)}件（うち購入済み {purchased}件）を書き出しました: {path}")
    typer.secho(
        "件数が十分に貯まるまで、この CSV を使った閾値探索は行わないでください。",
        fg=typer.colors.BRIGHT_BLACK,
    )


# --- ChatGPT へ渡すデータ ---------------------------------------------------------


def _market_session(cfg, now_text: str | None):
    """market_session 設定と「今」を解決する。

    `--now` は検証・テスト用の時刻注入。実時間に依存したテストを書かないための
    入口であって、運用では使わない（既定は現在時刻）。
    """
    from swing_screener import market_session as session_mod

    try:
        session = session_mod.load_session(cfg)
        return session, session_mod.parse_now(now_text, session.tz)
    except session_mod.SessionConfigError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1)


def _screening_for(config: str, experimental: str, as_of_text: str | None):
    """ChatGPT 用 export の入力を作る。

    判定は本番の `screener.run_screening` に任せる（export 側で再判定しない）。
    `--date` を指定した場合は株価系列をその日までに切ってから本番判定へ渡す。
    こうすると系列の最終足＝その日になるので、未来の足は構造的に入らない。
    """
    from swing_screener import chatgpt_export, screener

    cfg, exp = _load_cfg_exp(config, experimental)
    stocks = load_universe(cfg)
    price_map, warnings = screener.load_price_map(stocks, cfg)

    if as_of_text:
        as_of = date.fromisoformat(as_of_text)
        price_map = chatgpt_export.truncate_price_map(price_map, as_of)
        if not price_map:
            typer.secho(
                f"{as_of} 以前の株価キャッシュがありません。", fg=typer.colors.RED
            )
            raise typer.Exit(1)
    else:
        as_of = chatgpt_export.latest_market_date(price_map)
        if as_of is None:
            typer.secho(
                "株価キャッシュが空です。先に `swing fetch` を実行してください。",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)

    run = screener.run_screening(stocks, price_map, cfg, exp)
    return cfg, exp, price_map, run, as_of, warnings


@app.command("chatgpt-export")
def chatgpt_export_cmd(
    date_: str = typer.Option(None, "--date", help="対象営業日 YYYY-MM-DD（既定は最新の確定営業日）"),
    lookback_days: int = typer.Option(
        None, "--lookback-days", help="daily_bars.csv の本数（既定 70営業日）"
    ),
    out: str = typer.Option(None, "--out", help="出力先ディレクトリ（既定は output/chatgpt）"),
    skip_existing: bool = typer.Option(
        False, "--skip-existing", help="同じ日付の FINAL bundle が既にあれば何もしない"
    ),
    now: str = typer.Option(
        None, "--now", help="「今」を指定する ISO 8601 日時（検証・テスト用。既定は現在時刻）"
    ),
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """ChatGPT 分析用の CSV を output/chatgpt/YYYY-MM-DD/ へ書き出す。

    本番スクリーニング結果をそのまま CSV へ写すだけで、売買判定はしない。
    書き出し後に検査を行い、不整合があれば失敗する（中途半端な CSV を
    「その日の最新データ」として残さないため）。

    **当日セッションの終了前は何も書き出さない。** 場中の未確定日足を
    「その日の確定 bundle」として残すと、引け後の本物の日足で作り直せなくなる
    （TRADING_RULES.md §1: 引け後に確定日足を確認して翌営業日を決める）。
    """
    from swing_screener import chatgpt_export as export_mod
    from swing_screener import market_session as session_mod

    lookback = lookback_days or export_mod.DEFAULT_BAR_LOOKBACK_DAYS
    cfg, _exp, price_map, run, as_of, warnings = _screening_for(config, experimental, date_)
    for w in warnings:
        typer.secho(f"警告: {w}", fg=typer.colors.YELLOW)

    session, now_dt = _market_session(cfg, now)
    if not session.is_finalized(as_of, now_dt):
        # A: 正式 bundle を作らずに正常終了する（保存も artifact も signals もなし）。
        typer.echo("Market session is not closed yet.")
        typer.echo("No finalized bundle was generated.")
        typer.echo(f"  市場データ基準日: {as_of}")
        typer.echo(f"  確定とみなす時刻: {session.describe()}")
        typer.echo(f"  現在（{session.timezone}）: {session.localize(now_dt).isoformat(timespec='seconds')}")
        return

    target = export_mod.bundle_dir(as_of, cfg, out)
    if skip_existing and export_mod.is_finalized_bundle(target):
        typer.echo(f"{as_of} の FINAL bundle は既にあります（上書きしません）: {target}")
        return

    try:
        bundle = export_mod.write_bundle(
            run,
            price_map,
            cfg,
            as_of=as_of,
            out_dir=out,
            lookback_days=lookback,
            config_path=config,
            experimental_path=experimental,
            session=session,
            session_status=session_mod.SESSION_FINAL,
            generated_at_dt=session.localize(now_dt) if now else None,
        )
    except export_mod.ExportError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1)

    errors = export_mod.validate_bundle(
        bundle.directory, lookback_days=lookback, run=run
    )
    if errors:
        typer.secho(f"検査に失敗しました（{len(errors)}件）:", fg=typer.colors.RED)
        for e in errors:
            typer.secho(f"  - {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    counts = bundle.status_counts()
    typer.echo(f"データ基準日: {bundle.as_of}（session_status={bundle.session_status}）")
    typer.echo(
        "候補 {total}件（ENTRY_CANDIDATE {e} / NEAR {n} / RANGE {r}）".format(
            total=bundle.candidate_count,
            e=counts.get(models.STATUS_ENTRY, 0),
            n=counts.get(models.STATUS_NEAR, 0),
            r=counts.get(models.STATUS_RANGE, 0),
        )
    )
    typer.echo(f"日足 {len(bundle.bar_rows)}行（直近{lookback}営業日 × 候補全銘柄）")
    for path in bundle.files:
        typer.echo(f"  → {path}")
    typer.secho(
        "このデータは分析の入力です。売買判定は人間が日足を見て行ってください。",
        fg=typer.colors.BRIGHT_BLACK,
    )


@app.command("chatgpt-validate")
def chatgpt_validate_cmd(
    date_: str = typer.Option(None, "--date", help="検査する営業日 YYYY-MM-DD（既定は最新）"),
    lookback_days: int = typer.Option(
        None, "--lookback-days", help="daily_bars.csv の上限本数（既定 70営業日）"
    ),
    out: str = typer.Option(None, "--out", help="検査対象ディレクトリの親（既定は output/chatgpt）"),
    allow_unfinalized: bool = typer.Option(
        False,
        "--allow-unfinalized",
        help="session_status=FINAL でない bundle も検査対象にする（旧形式の確認用）",
    ),
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """書き出し済みの ChatGPT 用 CSV を検査する（本番判定との一致も見る）。

    既定では manifest が `session_status=FINAL` であることも要求する。
    artifact / automation-data へ渡る直前の最後の関門なので、場中の未確定
    日足で作られた bundle をここで止める。
    """
    from swing_screener import chatgpt_export as export_mod

    lookback = lookback_days or export_mod.DEFAULT_BAR_LOOKBACK_DAYS
    cfg, _exp, _price_map, run, as_of, _warnings = _screening_for(
        config, experimental, date_
    )
    target = export_mod.bundle_dir(as_of, cfg, out)
    if not target.exists():
        typer.secho(f"{target} がありません。", fg=typer.colors.RED)
        raise typer.Exit(1)

    errors = export_mod.validate_bundle(
        target, lookback_days=lookback, run=run, require_final=not allow_unfinalized
    )
    if errors:
        typer.secho(f"検査に失敗しました（{len(errors)}件）:", fg=typer.colors.RED)
        for e in errors:
            typer.secho(f"  - {e}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho(f"検査OK: {target}", fg=typer.colors.GREEN)


@app.command("market-check")
def market_check(
    format_: str = typer.Option(
        "text", "--format", help="text（人間向け）または github（key=value 行）"
    ),
    out: str = typer.Option(None, "--out", help="bundle の親ディレクトリ（既定は output/chatgpt）"),
    now: str = typer.Option(
        None, "--now", help="「今」を指定する ISO 8601 日時（検証・テスト用。既定は現在時刻）"
    ),
    config: str = _CONFIG_OPTION,
) -> None:
    """正式 bundle を作るべき状態かを調べる（市場休日と場中の空振りを避ける）。

    条件は 2 つあり、両方を満たしたときだけ `has_new_data=true` になる。

    1. **当日セッションが終わっている**（`market_session`。既定 16:00 JST 以降）。
       場中の未確定日足を「その日の確定 bundle」として残さないため。
    2. **前回の FINAL bundle より新しい市場日である**。祝日は株価の日付が
       進まないので false になり、bundle を作り直さずに正常終了できる。
       比較対象を FINAL に限るので、場中に作られた bundle や旧形式の bundle が
       同じ日付で残っていても、引け後の正式生成は skip されない。

    `--format github` は `key=value` を標準出力へ出すので、GitHub Actions では
    `>> "$GITHUB_OUTPUT"` へ流すだけで後続ステップの条件に使える。
    """
    from swing_screener import chatgpt_export as export_mod
    from swing_screener import market_session as session_mod
    from swing_screener import screener

    cfg = _load_cfg(config)
    stocks = load_universe(cfg)
    price_map, _warnings = screener.load_price_map(stocks, cfg)

    market_date = export_mod.latest_market_date(price_map)
    last_export = export_mod.latest_exported_date(cfg, out)
    last_final = export_mod.latest_finalized_export_date(cfg, out)

    if market_date is None:
        # 株価キャッシュが空なのは「市場休日」ではなく異常。休日として握り潰すと
        # 取得が全滅した日に静かに成功してしまうため、ここで失敗させる。
        typer.secho(
            "株価キャッシュが空です（市場休日ではなく取得の失敗）。"
            "`swing fetch` の結果を確認してください。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    session, now_dt = _market_session(cfg, now)
    session_status = session.status(market_date, now_dt)
    is_finalized = session_status == session_mod.SESSION_FINAL

    if not is_finalized:
        has_new, skip_reason = False, "session_not_closed"
    elif last_final is not None and market_date <= last_final:
        has_new, skip_reason = False, "already_finalized"
    else:
        has_new, skip_reason = True, ""

    if format_ == "github":
        typer.echo(f"has_new_data={'true' if has_new else 'false'}")
        typer.echo(f"market_date={market_date.isoformat()}")
        typer.echo(f"session_status={session_status}")
        typer.echo(f"is_finalized={'true' if is_finalized else 'false'}")
        typer.echo(f"last_export_date={last_export.isoformat() if last_export else ''}")
        typer.echo(
            f"last_final_export_date={last_final.isoformat() if last_final else ''}"
        )
        typer.echo(f"skip_reason={skip_reason}")
        typer.echo(f"now_market_tz={session.localize(now_dt).isoformat(timespec='seconds')}")
        return

    typer.echo(f"株価キャッシュの最新日: {market_date}")
    typer.echo(f"現在（{session.timezone}）: {session.localize(now_dt).isoformat(timespec='seconds')}")
    typer.echo(f"当日セッション: {session_status}（確定とみなす時刻 {session.describe()}）")
    typer.echo(f"前回の FINAL bundle: {last_final or 'なし'}")
    if last_export is not None and last_export != last_final:
        typer.echo(f"（FINAL でない bundle が {last_export} に残っています）")
    if has_new:
        typer.secho("新しい営業日の確定データがあります。", fg=typer.colors.GREEN)
    elif skip_reason == "session_not_closed":
        typer.echo("Market session is not closed yet.")
        typer.echo("No finalized bundle was generated.")
    else:
        typer.echo("No new market data（市場休日または未更新。書き出しは不要です）")


@app.command()
def serve(
    port: int = typer.Option(8000, "--port", help="待受ポート"),
    reload: bool = typer.Option(False, "--reload", help="コード変更時に自動リロードする"),
    config: str = _CONFIG_OPTION,
    experimental: str = _EXPERIMENTAL_OPTION,
) -> None:
    """Web UI を起動する（起動時に screen 相当の判定を行う）。"""
    import uvicorn

    if reload:
        # uvicorn の --reload はモジュール文字列からアプリを都度生成し直す仕組みのため、
        # --config/--experimental で指定したカスタムパスは反映できない（既定パスを使う）。
        uvicorn.run(
            "swing_screener.web.app:create_app",
            factory=True,
            host="127.0.0.1",
            port=port,
            reload=True,
        )
        return

    from swing_screener.web.app import create_app  # 他エージェント担当モジュール（DESIGN.md §12.5）

    web_app = create_app(config_path=config, experimental_path=experimental)
    uvicorn.run(web_app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    app()
