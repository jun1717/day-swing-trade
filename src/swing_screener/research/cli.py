"""検証用 CLI（RESEARCH_DESIGN §11）。

    python -m swing_screener.research.cli run [--months 6]
    python -m swing_screener.research.cli fetch-history --years 2

本番 `swing` CLI にはサブコマンドを追加しない（分離を維持するため）。
本番の config.yaml / experimental.yaml / output/ には一切書き込まない。
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import typer

from swing_screener import universe
from swing_screener.config import load_all
from swing_screener.research import events as events_mod
from swing_screener.research import sweep as sweep_mod
from swing_screener.research.config import (
    DEFAULT,
    SWEEP_THRESHOLDS,
    ResearchConfig,
    threshold_label,
    threshold_tag,
    with_position_threshold,
)
from swing_screener.research.replay import replay_all, resolve_window
from swing_screener.screener import load_price_map

app = typer.Typer(help="過去データでのイベントスタディ検証（本番ロジックとは分離）")


@app.command()
def run(
    months: int = typer.Option(6, help="検証期間（ヶ月）。データがあれば12まで拡張可"),
    out: Path = typer.Option(Path("research"), help="出力先"),
    config: Path = typer.Option(Path("config.yaml")),
    experimental: Path = typer.Option(Path("experimental.yaml")),
    skip_verify: bool = typer.Option(False, help="等価性検証を省略（非推奨）"),
    no_report: bool = typer.Option(False, help="HTML/チャート生成を省略"),
) -> None:
    """過去データで日次リプレイし、max_position_in_range を比較する。"""
    cfg, exp = load_all(config, experimental)
    research = ResearchConfig(months=months)

    stocks = universe.load_universe(cfg)
    price_map, warnings = load_price_map(stocks, cfg)
    for w in warnings[:5]:
        typer.echo(f"警告: {w}")
    if not price_map:
        typer.echo("株価キャッシュがありません。先に `swing fetch` を実行してください。")
        raise typer.Exit(1)

    start, end, warmup = resolve_window(price_map, cfg, exp, months=months, research=research)
    if start is None:
        typer.echo("検証可能な期間がありません。")
        raise typer.Exit(1)

    typer.echo(f"検証期間: {start} 〜 {end}（{months}ヶ月指定 / warmup {warmup}本）")
    typer.echo(f"対象銘柄: {len(price_map)}")
    typer.echo("リプレイ中（max_position_in_range は制限なしで1回だけ実行）...")

    unlimited = with_position_threshold(exp, None)
    t0 = time.time()

    def progress(n: int, total: int, code: str) -> None:
        if n % 10 == 0 or n == total:
            sys.stderr.write(f"\r  [{n}/{total}] {code}   ")
            sys.stderr.flush()

    days = replay_all(
        stocks, price_map, cfg, unlimited, start=start, end=end,
        research=research, progress=progress,
    )
    sys.stderr.write("\r" + " " * 40 + "\r")
    typer.echo(f"リプレイ完了: {len(days):,} 銘柄日 ({time.time() - t0:.1f}秒)")

    stocks_by_code = {s.code: s for s in stocks}

    verified, mismatches = True, []
    if not skip_verify:
        typer.echo("事後導出の等価性を検証中...")
        verified, mismatches = sweep_mod.verify_derivation(
            days, stocks_by_code, price_map, cfg, exp
        )
        if verified:
            typer.echo("  OK: 導出は実際の再計算と一致")
        else:
            typer.echo(f"  不一致 {len(mismatches)} 件。導出を使わず再計算します")
            for m in mismatches[:3]:
                typer.echo(f"    {m}")

    by_threshold = sweep_mod.build_sweep(
        days, stocks_by_code, price_map, research, thresholds=SWEEP_THRESHOLDS
    )
    all_events = by_threshold[threshold_label(None)].events

    trading_days = len({d.date for d in days})
    result = sweep_mod.SweepResult(
        start=start, end=end, months=months, warmup=warmup,
        stock_count=len(price_map), trading_days=trading_days,
        all_events=all_events, by_threshold=by_threshold,
        thresholds=SWEEP_THRESHOLDS,
        derivation_verified=verified, derivation_mismatches=mismatches,
        experimental_snapshot=exp.as_dict(), config_snapshot=cfg.as_dict(),
    )

    out.mkdir(parents=True, exist_ok=True)
    events_mod.write_events_csv(all_events, out / "events.csv")
    for threshold in SWEEP_THRESHOLDS:
        label = threshold_label(threshold)
        events_mod.write_events_csv(
            by_threshold[label].events, out / f"events_pos{threshold_tag(threshold)}.csv"
        )
    _write_summary(result, out / "summary.csv")

    typer.echo("")
    typer.echo("=== max_position_in_range 別 ENTRY 件数 ===")
    for tr in result.ordered():
        stop_rate = tr.stop_rate()
        rate = f"{stop_rate:.0f}%" if stop_rate is not None else "－"
        typer.echo(
            f"  {tr.label:>6}: ENTRY {len(tr.events):3d} 件"
            f"（forward完全 {len(tr.complete_events):3d}／損切り到達 {rate}）"
        )

    typer.echo("")
    typer.echo("=== 緩めたときに追加されるイベント ===")
    for prev, cur, added in result.added_by_loosening():
        typer.echo(f"  {prev} → {cur}: +{len(added)} 件")

    if not no_report:
        _emit_report(result, price_map, cfg, out)

    typer.echo("")
    typer.echo(f"出力: {out}/")


def _write_summary(result: sweep_mod.SweepResult, path: Path) -> None:
    """閾値別の集計。平均だけでなく中央値・四分位数を出す。"""
    from swing_screener.research import classify

    dist_attrs = [
        ("position_in_range", "レンジ内位置"),
        ("days_from_touch_to_signal", "下限接触からENTRYまでの日数"),
        ("fwd5_max_gain_pct_from_close", "5日最大上昇率(終値基準)"),
        ("fwd10_max_gain_pct_from_close", "10日最大上昇率(終値基準)"),
        ("fwd5_max_loss_pct_from_close", "5日最大下落率(終値基準)"),
        ("fwd10_max_loss_pct_from_close", "10日最大下落率(終値基準)"),
        ("fwd5_max_gain_pct_from_next_open", "5日最大上昇率(翌日始値基準)"),
        ("fwd10_max_gain_pct_from_next_open", "10日最大上昇率(翌日始値基準)"),
        ("fwd5_max_loss_pct_from_next_open", "5日最大下落率(翌日始値基準)"),
        ("fwd10_max_loss_pct_from_next_open", "10日最大下落率(翌日始値基準)"),
    ]

    rows = []
    for tr in result.ordered():
        row = {
            "threshold": tr.label,
            "entry_count": len(tr.events),
            "forward_complete_count": len(tr.complete_events),
            "stop_rate_pct": (
                f"{tr.stop_rate():.1f}" if tr.stop_rate() is not None else ""
            ),
        }
        for shape, count in tr.shape_counts().items():
            row[f"shape_{shape}"] = count
        for outcome, count in tr.outcome_counts().items():
            row[f"outcome_{outcome}"] = count
        for attr, _ in dist_attrs:
            stats = tr.complete_distribution(attr) if attr.startswith("fwd") else tr.distribution(attr)
            for key in ("median", "q1", "q3", "mean", "min", "max"):
                value = stats.get(key)
                row[f"{attr}_{key}"] = f"{value:.2f}" if value is not None else ""
        rows.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# イベントスタディの集計。パラメータ最適化ではない。"
            " forward の *_from_close はシグナル日終値基準で、実際には約定できない仮定を含む。\n"
        )
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _emit_report(result, price_map, cfg, out: Path) -> None:
    try:
        from swing_screener.research import charts, report
    except ImportError as e:
        typer.echo(f"レポート生成をスキップ（未実装）: {e}")
        return
    try:
        paths = charts.render_event_charts(result, price_map, cfg, out)
        typer.echo(f"代表チャート: {len(paths)} 枚")
    except Exception as e:  # noqa: BLE001 - 検証ツールなので握って継続
        typer.echo(f"チャート生成に失敗: {e}")
    try:
        path = report.write_report(result, out)
        typer.echo(f"レポート: {path}")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"レポート生成に失敗: {e}")


@app.command("exit-study")
def exit_study_cmd(
    events_csv: Path = typer.Option(
        Path("research/events_pos065.csv"),
        help="追跡対象の ENTRY イベント（前回リプレイの閾値別ファイル）",
    ),
    out: Path = typer.Option(Path("research/exit_study"), help="出力先"),
    config: Path = typer.Option(Path("config.yaml")),
    experimental: Path = typer.Option(Path("experimental.yaml")),
    threshold: float = typer.Option(0.65, help="表示用。閾値の変更はしない"),
    per_category: int = typer.Option(3, help="代表チャートの1カテゴリあたり枚数"),
    no_charts: bool = typer.Option(False, help="チャート生成を省略"),
) -> None:
    """ENTRY_CANDIDATE の ENTRY 後の値動きを、現在の売買ルールに沿って追跡する。

    ポジションを閉じる機械判定に使うのは確定ルールの初期損切りのみ。
    警戒陰線・トレーリングは参考記録に留め、売買判定には使わない。
    config.yaml / experimental.yaml / output/ には一切書き込まない。
    """
    from swing_screener.research import exit_report, exit_study

    cfg, exp = load_all(config, experimental)
    stocks = universe.load_universe(cfg)
    price_map, warnings = load_price_map(stocks, cfg)
    for w in warnings[:5]:
        typer.echo(f"警告: {w}")
    if not price_map:
        typer.echo("株価キャッシュがありません。先に `swing fetch` を実行してください。")
        raise typer.Exit(1)

    if not events_csv.exists():
        typer.echo(f"{events_csv} がありません。先に `research.cli run` を実行してください。")
        raise typer.Exit(1)
    with events_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader([ln for ln in f if not ln.startswith("#")]))
    if not rows:
        typer.echo("追跡対象のイベントがありません。")
        raise typer.Exit(1)

    typer.echo(f"追跡対象: {len(rows)} 件（{events_csv}）")

    tracked: list[exit_study.TrackedEvent] = []
    skipped: list[str] = []
    for row in rows:
        series = price_map.get(row["code"])
        if series is None:
            skipped.append(f"{row['code']} {row['date']}: 株価キャッシュなし")
            continue
        idx = int(row["signal_index"])
        # signal_index はキャッシュ更新でずれ得るので、日付で必ず突き合わせる
        if idx >= len(series.bars) or series.bars[idx].date.isoformat() != row["date"]:
            match = next(
                (i for i, b in enumerate(series.bars) if b.date.isoformat() == row["date"]),
                None,
            )
            if match is None:
                skipped.append(f"{row['code']} {row['date']}: シグナル日の足が見つからない")
                continue
            row = {**row, "signal_index": str(match)}
        tracked.append(exit_study.track_event(row, series, exp))

    for s in skipped:
        typer.echo(f"スキップ: {s}")
    exit_study.apply_classification(tracked)

    out.mkdir(parents=True, exist_ok=True)
    exit_study.write_events_csv(tracked, out / "events.csv")
    exit_study.write_timeline_csv(tracked, out / "timeline.csv")
    exit_study.write_warning_candles_csv(tracked, out / "warning_candles.csv")
    exit_study.write_trail_csv(tracked, out / "trail_candidates.csv")
    summary = exit_study.summarize(tracked)
    exit_study.write_summary_csv(summary, out / "summary.csv")

    chart_map: dict = {}
    if not no_charts:
        try:
            from swing_screener.research import exit_charts

            chart_map = exit_charts.render_all(
                tracked, price_map, cfg, out, per_category=per_category
            )
            typer.echo(f"代表チャート: {sum(len(v) for v in chart_map.values())} 枚")
        except Exception as e:  # noqa: BLE001 - 検証ツールなので握って継続
            typer.echo(f"チャート生成に失敗: {e}")

    dates = [e.signal_date.isoformat() for e in tracked]
    period = (min(dates), max(dates)) if dates else ("", "")
    try:
        path = exit_report.write_report(
            tracked, summary, chart_map, out, period=period, threshold=threshold
        )
        typer.echo(f"レポート: {path}")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"レポート生成に失敗: {e}")

    typer.echo("")
    for row in summary:
        typer.echo(f"  [{row.section}] {row.metric}: {row.value}")
    typer.echo("")
    typer.echo(f"出力: {out}/")
    typer.echo(
        "注意: この結果を理由に config.yaml / experimental.yaml / 本番ロジックは変更していない。"
    )


@app.command("exit-state-machine")
def exit_state_machine_cmd(
    events_csv: Path = typer.Option(
        Path("research/events_pos065.csv"),
        help="追跡対象の ENTRY イベント（前回リプレイの閾値別ファイル）",
    ),
    out: Path = typer.Option(Path("research/exit_state_machine"), help="出力先"),
    config: Path = typer.Option(Path("config.yaml")),
    experimental: Path = typer.Option(Path("experimental.yaml")),
    threshold: float = typer.Option(0.65, help="表示用。閾値の変更はしない"),
    per_category: int = typer.Option(3, help="代表チャートの1カテゴリあたり枚数"),
    no_charts: bool = typer.Option(False, help="チャート生成を省略"),
) -> None:
    """EXIT ロジックを日足の状態機械として再現できるかを検証する。

    INITIAL_HOLD → TREND_HOLD → WARNING → REHIGH → STOP引き上げ → TREND_HOLD
    のループを 1 営業日ずつ再生し、CASE1/CASE2/CASE3 を並べて比較する。

    今回の状態遷移は**現行の文章ルールの読み方**であって正式ルールではない。
    ENTRY ロジック / near.max_position_in_range / 初期STOP は変更しない。
    config.yaml / experimental.yaml / output/ には一切書き込まない。
    前回の research/exit_study/ も上書きしない。
    """
    from swing_screener.research import exit_state_machine as sm

    cfg, exp = load_all(config, experimental)
    stocks = universe.load_universe(cfg)
    price_map, warnings = load_price_map(stocks, cfg)
    for w in warnings[:5]:
        typer.echo(f"警告: {w}")
    if not price_map:
        typer.echo("株価キャッシュがありません。先に `swing fetch` を実行してください。")
        raise typer.Exit(1)

    if not events_csv.exists():
        typer.echo(f"{events_csv} がありません。先に `research.cli run` を実行してください。")
        raise typer.Exit(1)
    with events_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader([ln for ln in f if not ln.startswith("#")]))
    if not rows:
        typer.echo("追跡対象のイベントがありません。")
        raise typer.Exit(1)

    typer.echo(f"追跡対象: {len(rows)} 件（{events_csv}）")

    tracked: list[sm.SMEvent] = []
    skipped: list[str] = []
    for row in rows:
        series = price_map.get(row["code"])
        if series is None:
            skipped.append(f"{row['code']} {row['date']}: 株価キャッシュなし")
            continue
        idx = int(row["signal_index"])
        # signal_index はキャッシュ更新でずれ得るので、日付で必ず突き合わせる
        if idx >= len(series.bars) or series.bars[idx].date.isoformat() != row["date"]:
            match = next(
                (i for i, b in enumerate(series.bars) if b.date.isoformat() == row["date"]),
                None,
            )
            if match is None:
                skipped.append(f"{row['code']} {row['date']}: シグナル日の足が見つからない")
                continue
            row = {**row, "signal_index": str(match)}
        tracked.append(sm.track_event(row, series, exp))

    for s in skipped:
        typer.echo(f"スキップ: {s}")
    sm.apply_classification(tracked)

    out.mkdir(parents=True, exist_ok=True)
    sm.write_events_csv(tracked, out / "events.csv")
    sm.write_timeline_csv(tracked, out / "state_timeline.csv")
    sm.write_warnings_csv(tracked, out / "warnings.csv")
    sm.write_stop_updates_csv(tracked, out / "stop_updates.csv")
    sm.write_daily_state_csv(tracked, out / "daily_state.csv")
    sm.write_case_comparison_csv(tracked, out / "case_comparison.csv")
    summary = sm.summarize(tracked)
    sm.write_summary_csv(summary, out / "summary.csv")

    chart_map: dict = {}
    if not no_charts:
        try:
            from swing_screener.research import exit_sm_charts

            chart_map = exit_sm_charts.render_all(
                tracked, price_map, cfg, out, per_category=per_category
            )
            typer.echo(f"代表チャート: {sum(len(v) for v in chart_map.values())} 枚")
        except Exception as e:  # noqa: BLE001 - 検証ツールなので握って継続
            typer.echo(f"チャート生成に失敗: {e}")

    dates = [e.signal_date.isoformat() for e in tracked]
    period = (min(dates), max(dates)) if dates else ("", "")
    try:
        from swing_screener.research import exit_sm_report

        path = exit_sm_report.write_report(
            tracked, summary, chart_map, out, period=period, threshold=threshold
        )
        typer.echo(f"レポート: {path}")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"レポート生成に失敗: {e}")

    typer.echo("")
    for row in summary:
        typer.echo(f"  [{row.section}] {row.metric}: {row.value}")
    typer.echo("")
    typer.echo(f"出力: {out}/")
    typer.echo(
        "注意: 今回の状態遷移は検証用の読み方であり、正式ルールに昇格していない。"
        " config.yaml / experimental.yaml / 本番スクリーナーは変更していない。"
    )


@app.command("warning-start-study")
def warning_start_study_cmd(
    events_csv: Path = typer.Option(
        Path("research/events_pos065.csv"),
        help="追跡対象の ENTRY イベント（前回リプレイの閾値別ファイル）",
    ),
    out: Path = typer.Option(Path("research/warning_start_study"), help="出力先"),
    config: Path = typer.Option(Path("config.yaml")),
    experimental: Path = typer.Option(Path("experimental.yaml")),
    threshold: float = typer.Option(0.65, help="表示用。閾値の変更はしない"),
    per_category: int = typer.Option(2, help="代表チャートの1カテゴリあたり枚数"),
    no_charts: bool = typer.Option(False, help="チャート生成を省略"),
) -> None:
    """警戒陰線を「いつ有効化するか」だけを A/B/C で比較する。

        A  上限を終値突破した翌営業日から（現行案・比較基準）
        B  突破後に high  > breakout_day_high  を満たした日の翌営業日から
        C  突破後に close > breakout_day_close を満たした日の翌営業日から

    reference_high の定義 / warning_low 割れ後の CASE3 の扱い / 押し安値 /
    トレーリングは 3 案とも完全に同一。ENTRY ロジック・0.65・初期STOP も不変。
    config.yaml / experimental.yaml / 本番スクリーナーには書き込まない。
    前回の research/exit_state_machine/ も上書きしない。
    """
    from swing_screener.research import warning_start_study as ws

    cfg, exp = load_all(config, experimental)
    stocks = universe.load_universe(cfg)
    price_map, warnings = load_price_map(stocks, cfg)
    for w in warnings[:5]:
        typer.echo(f"警告: {w}")
    if not price_map:
        typer.echo("株価キャッシュがありません。先に `swing fetch` を実行してください。")
        raise typer.Exit(1)

    if not events_csv.exists():
        typer.echo(f"{events_csv} がありません。先に `research.cli run` を実行してください。")
        raise typer.Exit(1)
    with events_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader([ln for ln in f if not ln.startswith("#")]))
    if not rows:
        typer.echo("追跡対象のイベントがありません。")
        raise typer.Exit(1)

    prepared: list[tuple[dict, object]] = []
    skipped: list[str] = []
    for row in rows:
        series = price_map.get(row["code"])
        if series is None:
            skipped.append(f"{row['code']} {row['date']}: 株価キャッシュなし")
            continue
        idx = int(row["signal_index"])
        # signal_index はキャッシュ更新でずれ得るので、日付で必ず突き合わせる
        if idx >= len(series.bars) or series.bars[idx].date.isoformat() != row["date"]:
            match = next(
                (i for i, b in enumerate(series.bars) if b.date.isoformat() == row["date"]),
                None,
            )
            if match is None:
                skipped.append(f"{row['code']} {row['date']}: シグナル日の足が見つからない")
                continue
            row = {**row, "signal_index": str(match)}
        prepared.append((row, series))

    for s in skipped:
        typer.echo(f"スキップ: {s}")
    typer.echo(f"追跡対象: {len(prepared)} 件 × 3案（{events_csv}）")

    runs = ws.run_variants(prepared, exp)
    metrics = ws.compare_metrics(runs)
    early = ws.extract_early_warning_cases(runs)
    late = ws.extract_late_warning_cases(runs)
    confirms = ws.compare_confirmations(runs)

    out.mkdir(parents=True, exist_ok=True)
    ws.write_events_csv(runs, out / "events.csv")
    ws.write_warnings_csv(runs, out / "warnings.csv")
    ws.write_variant_comparison_csv(metrics, out / "variant_comparison.csv")
    ws.write_summary_csv(runs, out / "summary.csv")
    ws.write_early_warning_csv(early, out / "early_warning_cases.csv")
    ws.write_late_warning_csv(late, out / "late_warning_cases.csv")
    ws.write_confirm_comparison_csv(confirms, out / "bc_confirm_comparison.csv")

    chart_map: dict = {}
    if not no_charts:
        try:
            from swing_screener.research import warning_start_charts

            chart_map = warning_start_charts.render_all(
                runs, price_map, cfg, out, per_category=per_category
            )
            typer.echo(f"代表チャート: {sum(len(v) for v in chart_map.values())} 枚")
        except Exception as e:  # noqa: BLE001 - 検証ツールなので握って継続
            typer.echo(f"チャート生成に失敗: {e}")

    dates = [e.signal_date.isoformat() for e in runs[ws.sm.VARIANT_A].events]
    period = (min(dates), max(dates)) if dates else ("", "")
    try:
        from swing_screener.research import warning_start_report

        path = warning_start_report.write_report(
            runs, metrics, early, late, confirms, chart_map, out,
            period=period, threshold=threshold,
        )
        typer.echo(f"レポート: {path}")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"レポート生成に失敗: {e}")

    typer.echo("")
    for row in metrics:
        vals = " / ".join(
            f"{v}: {row.values.get(v, '－')}" for v in ws.sm.VARIANTS
        )
        typer.echo(f"  [{row.section}] {row.metric} … {vals}")
    typer.echo("")
    typer.echo(f"出力: {out}/")
    typer.echo(
        "注意: A/B/C はいずれも現行の文章ルールの読み方であり、正式ルールではない。"
        " 成績が良い案を採用する、という結論は出していない。"
        " config.yaml / experimental.yaml / 本番スクリーナーは変更していない。"
    )


def _load_prepared(
    events_csv: Path, price_map: dict
) -> list[tuple[dict, object]]:
    """events_pos065.csv の行を株価系列と突き合わせる（研究コマンド共通）。"""
    if not events_csv.exists():
        typer.echo(f"{events_csv} がありません。先に `research.cli run` を実行してください。")
        raise typer.Exit(1)
    with events_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader([ln for ln in f if not ln.startswith("#")]))
    if not rows:
        typer.echo("追跡対象のイベントがありません。")
        raise typer.Exit(1)

    prepared: list[tuple[dict, object]] = []
    for row in rows:
        series = price_map.get(row["code"])
        if series is None:
            typer.echo(f"スキップ: {row['code']} {row['date']}: 株価キャッシュなし")
            continue
        idx = int(row["signal_index"])
        # signal_index はキャッシュ更新でずれ得るので、日付で必ず突き合わせる
        if idx >= len(series.bars) or series.bars[idx].date.isoformat() != row["date"]:
            match = next(
                (i for i, b in enumerate(series.bars) if b.date.isoformat() == row["date"]),
                None,
            )
            if match is None:
                typer.echo(f"スキップ: {row['code']} {row['date']}: シグナル日の足なし")
                continue
            row = {**row, "signal_index": str(match)}
        prepared.append((row, series))
    return prepared


@app.command("warning-break-study")
def warning_break_study_cmd(
    events_csv: Path = typer.Option(
        Path("research/events_pos065.csv"),
        help="追跡対象の ENTRY イベント（前回リプレイの閾値別ファイル）",
    ),
    out: Path = typer.Option(Path("research/warning_break_study"), help="出力先"),
    config: Path = typer.Option(Path("config.yaml")),
    experimental: Path = typer.Option(Path("experimental.yaml")),
    threshold: float = typer.Option(0.65, help="表示用。閾値の変更はしない"),
    per_category: int = typer.Option(2, help="代表チャートの1カテゴリあたり枚数"),
    no_charts: bool = typer.Option(False, help="チャート生成を省略"),
) -> None:
    """warning_low を割ったあと「どこで降りるか」だけを 4 案で比較する。

        参考 HOLD_UNTIL_STOP  割っても降りない（前回 CASE3）
        V1   LOW_BREAK        low   < warning_low
        V2   CLOSE_BREAK      close < warning_low
        V3   STRUCTURAL_BREAK close < warning_low かつ close < 元レンジ上限

    WARNING 開始条件は VARIANT A に固定（原因を 1 つに絞るための研究上の基準で、
    A の正式採用ではない）。reference_high / 押し安値 / トレーリング / 初期STOP /
    ENTRY ロジック / 0.65 はすべて不変。
    config.yaml / experimental.yaml / 本番スクリーナーには書き込まない。
    前回までの research/exit_state_machine/・research/warning_start_study/ も
    上書きしない。
    """
    from swing_screener.research import warning_break_study as wb

    cfg, exp = load_all(config, experimental)
    stocks = universe.load_universe(cfg)
    price_map, warnings = load_price_map(stocks, cfg)
    for w in warnings[:5]:
        typer.echo(f"警告: {w}")
    if not price_map:
        typer.echo("株価キャッシュがありません。先に `swing fetch` を実行してください。")
        raise typer.Exit(1)

    prepared = _load_prepared(events_csv, price_map)
    typer.echo(f"追跡対象: {len(prepared)} 件 × 4案（{events_csv}）")

    runs = wb.run_rules(prepared, exp)
    reality = wb.break_reality(runs)
    metrics = wb.compare_metrics(runs)
    revivals = wb.extract_revivals(runs)
    waited = wb.extract_waited_too_long(runs)
    natural = wb.classify_naturalness(runs)

    out.mkdir(parents=True, exist_ok=True)
    wb.write_events_csv(runs, out / "events.csv")
    wb.write_warning_breaks_csv(runs, out / "warning_breaks.csv")
    wb.write_variant_comparison_csv(metrics, out / "variant_comparison.csv")
    wb.write_summary_csv(runs, out / "summary.csv")
    wb.write_break_reality_csv(reality, out / "break_reality.csv")
    wb.write_revival_csv(revivals, out / "revival_cases.csv")
    wb.write_waited_csv(waited, out / "waited_too_long_cases.csv")
    wb.write_naturalness_csv(natural, out / "naturalness.csv")

    chart_map: dict = {}
    if not no_charts:
        try:
            from swing_screener.research import warning_break_charts

            chart_map = warning_break_charts.render_all(
                runs, price_map, cfg, out, per_category=per_category
            )
            typer.echo(f"代表チャート: {sum(len(v) for v in chart_map.values())} 枚")
        except Exception as e:  # noqa: BLE001 - 検証ツールなので握って継続
            typer.echo(f"チャート生成に失敗: {e}")

    dates = [e.signal_date.isoformat() for e in runs[wb.sm.BREAK_HOLD].events]
    period = (min(dates), max(dates)) if dates else ("", "")
    try:
        from swing_screener.research import warning_break_report

        path = warning_break_report.write_report(
            runs, reality, metrics, revivals, waited, natural, chart_map, out,
            period=period, threshold=threshold,
        )
        typer.echo(f"レポート: {path}")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"レポート生成に失敗: {e}")

    typer.echo("")
    for row in reality:
        typer.echo(f"  [§8] {row.metric} … {row.rate}（{row.count}/{row.denominator}）")
    typer.echo("")
    for row in metrics:
        vals = " / ".join(
            f"{wb.sm.BREAK_RULE_SHORT_JA[r]}: {row.values.get(r, '－')}" for r in wb.RULES
        )
        typer.echo(f"  [{row.section}] {row.metric} … {vals}")
    typer.echo("")
    typer.echo(f"出力: {out}/")
    typer.echo(
        "注意: LOW/CLOSE/STRUCTURAL はいずれも現行の文章ルールの読み方であり、"
        "正式ルールではない。成績が良い案を採用する、という結論は出していない。"
        " reference_high / 押し安値 / トレーリング / WARNING 開始条件は変更していない。"
        " config.yaml / experimental.yaml / 本番スクリーナーも変更していない。"
    )


@app.command("reference-high-study")
def reference_high_study_cmd(
    events_csv: Path = typer.Option(
        Path("research/events_pos065.csv"),
        help="追跡対象の ENTRY イベント（前回リプレイの閾値別ファイル）",
    ),
    out: Path = typer.Option(Path("research/reference_high_study"), help="出力先"),
    config: Path = typer.Option(Path("config.yaml")),
    experimental: Path = typer.Option(Path("experimental.yaml")),
    threshold: float = typer.Option(0.65, help="表示用。閾値の変更はしない"),
    per_category: int = typer.Option(1, help="代表チャートの1カテゴリあたり枚数"),
    no_charts: bool = typer.Option(False, help="チャート生成を省略"),
) -> None:
    """reference_high の決め方だけを 5 案で比較する。

        RH-A HOLDING_HIGH            max(high) ENTRY〜警戒足当日（現行）
        RH-B WARNING_HIGH            warning_high
        RH-C PRE_WARNING_CLOSE_HIGH  max(close) ENTRY〜警戒足前日
        RH-D WARNING_OPEN            warning_open
        RH-E PRE_WARNING_HIGH        max(high) ENTRY〜警戒足前日（参考VARIANT）

    WARNING 開始条件は VARIANT A、warning_low 割れ後は CLOSE_BREAK に固定
    （原因を 1 つに絞るための研究上の基準で、どちらも正式採用ではない）。
    ENTRY ロジック / 0.65 / 初期STOP / warning_low の定義 / 押し安値の取り方 /
    trail = 押し安値*0.995 / STOP を下げないこと はすべて不変。
    config.yaml / experimental.yaml / 本番スクリーナーには書き込まない。
    前回までの research/ の各検証結果も上書きしない。
    """
    from swing_screener.research import reference_high_study as rhs

    cfg, exp = load_all(config, experimental)
    stocks = universe.load_universe(cfg)
    price_map, warnings = load_price_map(stocks, cfg)
    for w in warnings[:5]:
        typer.echo(f"警告: {w}")
    if not price_map:
        typer.echo("株価キャッシュがありません。先に `swing fetch` を実行してください。")
        raise typer.Exit(1)

    prepared = _load_prepared(events_csv, price_map)
    typer.echo(f"追跡対象: {len(prepared)} 件 × 5案（{events_csv}）")

    runs = rhs.run_rules(prepared, exp)
    frames = rhs.build_frames(prepared)
    early = rhs.extract_early_trail(runs, frames)
    metrics = rhs.all_metrics(runs, frames, early)
    position = rhs.position_rows(runs)
    fractal = rhs.fractal_rows(runs, frames, exp)
    # §7: 同日成立の扱いを逆にしても結論が変わらないかを見るための第 2 実行
    runs_exit_first = rhs.run_rules(prepared, exp, ambiguous_order=sm_amb_exit())
    sens = rhs.ambiguity_sensitivity(runs, runs_exit_first)
    cases = rhs.case_rows(runs, frames)

    out.mkdir(parents=True, exist_ok=True)
    rhs.write_events_csv(runs, frames, out / "events.csv")
    rhs.write_summary_csv(runs, frames, out / "summary.csv")
    rhs.write_variant_comparison_csv(metrics, out / "variant_comparison.csv")
    rhs.write_rehigh_events_csv(runs, out / "rehigh_events.csv")
    rhs.write_stop_updates_csv(runs, out / "stop_updates.csv")
    rhs.write_ambiguous_csv(runs, out / "ambiguous_events.csv")
    rhs.write_position_csv(position, out / "position_relations.csv")
    rhs.write_early_trail_csv(early, out / "early_trail_cases.csv")
    rhs.write_fractal_csv(fractal, out / "fractal_comparison.csv")
    rhs.write_sensitivity_csv(sens, out / "ambiguity_sensitivity.csv")
    rhs.write_case_rows_csv(cases, out / "case_matrix.csv")

    chart_map: dict = {}
    if not no_charts:
        try:
            from swing_screener.research import reference_high_charts

            chart_map = reference_high_charts.render_all(
                runs, frames, price_map, cfg, out, per_category=per_category
            )
            typer.echo(f"代表チャート: {sum(len(v) for v in chart_map.values())} 枚")
        except Exception as e:  # noqa: BLE001 - 検証ツールなので握って継続
            typer.echo(f"チャート生成に失敗: {e}")

    dates = [e.signal_date.isoformat() for e in runs[rhs.sm.RH_HOLDING].events]
    period = (min(dates), max(dates)) if dates else ("", "")
    try:
        from swing_screener.research import reference_high_report

        path = reference_high_report.write_report(
            runs, frames, metrics, position, early, fractal, sens, chart_map, out,
            period=period, threshold=threshold,
        )
        typer.echo(f"レポート: {path}")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"レポート生成に失敗: {e}")

    typer.echo("")
    for row in metrics:
        vals = " / ".join(
            f"{rhs.sm.RH_RULE_SHORT_JA[r]}: {row.values.get(r, '－')}" for r in rhs.RULES
        )
        typer.echo(f"  [{row.section}] {row.metric} … {vals}")
    typer.echo("")
    for p in position:
        typer.echo(f"  [§15] {p.metric} … {p.value} {p.count}")
    typer.echo("")
    typer.echo(f"出力: {out}/")
    typer.echo(
        "注意: RH-A〜RH-E はいずれも比較用の仮説であり、正式ルールではない。"
        " trail 成立件数や仮想利益が高い案を採用する、という結論は出していない。"
        " ENTRY ロジック / 0.65 / 初期STOP / WARNING 開始条件 / warning_low の扱い /"
        " 押し安値・trail の定義は変更していない。"
        " config.yaml / experimental.yaml / 本番スクリーナーも変更していない。"
    )


def sm_amb_exit() -> str:
    from swing_screener.research import exit_state_machine as sm

    return sm.AMB_EXIT


@app.command("fetch-history")
def fetch_history(
    years: int = typer.Option(2, help="取得年数。12ヶ月検証には2年推奨"),
    config: Path = typer.Option(Path("config.yaml")),
) -> None:
    """検証用に長期の株価を取得する。

    本番 config.yaml の fetch_period は**変更しない**。research 側で period を
    上書きして同じ cache/prices/ を更新する。
    """
    import copy

    from swing_screener.config import Params, load_config
    from swing_screener.data import cache
    from swing_screener.data.yfinance_provider import YfinanceProvider

    cfg = load_config(config)
    data = copy.deepcopy(cfg.as_dict())
    data["data"]["fetch_period"] = f"{years}y"
    long_cfg = Params(data)

    stocks = universe.load_universe(cfg)
    provider = YfinanceProvider(long_cfg)
    total = len(stocks)
    failed = []
    for i, stock in enumerate(stocks, start=1):
        if not stock.enabled:
            continue
        typer.echo(f"[{i}/{total}] {stock.code} {stock.name} ...", nl=False)
        try:
            series = provider.fetch(stock.code)
            cache.save_prices(series, cfg)
            typer.echo(f" OK ({len(series.bars)}本)")
        except Exception as e:  # noqa: BLE001
            failed.append(stock.code)
            typer.echo(f" 失敗: {e}")
        time.sleep(float(cfg.data.sleep_sec))
    cache.record_fetch(cfg)
    typer.echo(f"完了。失敗 {len(failed)} 件")


if __name__ == "__main__":
    app()
