"""Web UI（FastAPI + Jinja2）。DESIGN.md §9, §10, §12.5 に対応する。

画面は 4 つ。

    /                候補一覧（ENTRY_CANDIDATE / NEAR / RANGE を優先表示）
    /stock/{code}    候補の詳細（チャート + 判定理由）
    /holdings        保有銘柄の当日レビュー（**売買判定はしない**）
    /holdings/{code} 保有銘柄の詳細（チャート + シナリオ確認欄）
    /signals         ENTRY候補の履歴（買わなかったものも残る）

起動時に screener.load_price_map → screener.run_screening を1回実行し、結果をプロセス内
メモリ（AppState）に保持する。/rescan はキャッシュ済み株価から再計算するだけで、
ネットワークには一切触れない（experimental.yaml のパラメータ調整→即再評価という
反復作業を速くするための機能）。

保有銘柄とトレード台帳（data/trades.csv）はリクエストのたびに読み直す。
ファイルを直接編集しても再起動なしで反映されるようにするため。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from swing_screener import journal, portfolio, screener
from swing_screener import review as review_mod
from swing_screener.charting import render_daily_chart, render_holding_chart
from swing_screener.config import load_config, load_experimental
from swing_screener.data import cache as price_cache
from swing_screener.explain import explain_lines, judgement_groups
from swing_screener.models import STATUS_ORDER
from swing_screener.universe import load_universe

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

STATUS_ORDER_LIST = ["ENTRY_CANDIDATE", "NEAR", "RANGE", "OUT"]


# --- Jinja2 カスタムフィルタ ---------------------------------------------------
# 判定結果は None（未計算・非該当）を多く含むため、テンプレート側で
# `{% if %}` を多用せずに済むよう、表示用フォーマットはすべてここに集約する。


def _f_yen(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{value:,.0f}円"
    except (TypeError, ValueError):
        return "—"


def _f_num(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{value:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _f_pct(value: Any, signed: bool = True, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if (signed and v > 0) else ""
    return f"{sign}{v:.{digits}f}%"


def _f_okng(value: Any) -> str:
    if value is True:
        return "OK"
    if value is False:
        return "NG"
    return "—"


def _f_dir_label(value: Any) -> str:
    return {"up": "上向き", "down": "下向き", "flat": "横ばい"}.get(value, "—")


def _f_datefmt(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        return str(value)


def _f_datefmt_md(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return value.strftime("%m/%d")
    except AttributeError:
        return str(value)


# --- 一覧テーブルの列定義 -------------------------------------------------------
# (row辞書のキー, 見出しラベル, セル種別) の順。CODEX_HANDOFF §24 の全項目 +
# 並び順の根拠列（トレンド強度・レンジ品質、DESIGN.md §8）+ 落選理由（OUT展開時のみ表示）。
# thead/tbody をこのリスト1つから生成することで表示列のズレを防ぐ。

TABLE_COLUMNS: list[tuple[str, str, str]] = [
    ("code", "コード", "text"),
    ("name", "銘柄名", "text"),
    ("sector", "業種", "text"),
    ("theme_display", "テーマ", "text"),
    ("priority", "優先度", "text"),
    ("is_leader", "主力", "bool_leader"),
    ("asset_type", "種別", "text"),
    ("close", "現在値", "yen"),
    ("status", "状態", "text"),
    ("ma25", "MA25", "yen"),
    ("ma25_dev_pct", "MA25乖離率", "pct1"),
    ("ma25_dir", "MA25方向", "dir"),
    ("trend_strength", "トレンド強度", "num2"),
    ("higher_highs", "高値切上", "okng"),
    ("higher_lows", "安値切上", "okng"),
    ("range_days", "range_days", "num0"),
    ("range_lower", "range_lower", "yen"),
    ("range_upper", "range_upper", "yen"),
    ("range_width_pct", "range_width_pct", "pct1_nosign"),
    ("range_quality", "レンジ品質", "num2"),
    ("lower_touch_count", "lower_touch_count", "num0"),
    ("distance_to_lower_pct", "distance_to_lower_pct", "pct1"),
    ("volume_state_label", "volume_state", "text"),
    ("prev_high", "前日高値", "yen"),
    ("rebound_confirmed", "rebound_confirmed", "okng"),
    ("stop_price", "stop_price", "yen"),
    ("out_reason", "落選理由", "text"),
]


def _num_sort(value: Any) -> str:
    """数値ソート用の文字列表現。None は常に最後に来るよう巨大な値にする。"""
    if value is None:
        return "999999999"
    try:
        return repr(float(value))
    except (TypeError, ValueError):
        return "999999999"


def _text_sort(value: Any) -> str:
    return (value or "").lower() if isinstance(value, str) else str(value or "").lower()


def _bool_sort(value: Any) -> str:
    """True→0, False→1, None→2 の順（OKが先に来る）。"""
    if value is True:
        return "0"
    if value is False:
        return "1"
    return "2"


def _cell(kind: str, value: Any) -> tuple[str, str]:
    """(表示テキスト, ソート用の値) を返す汎用セルフォーマッタ。"""
    if kind == "yen":
        return _f_yen(value), _num_sort(value)
    if kind == "pct1":
        return _f_pct(value, signed=True, digits=1), _num_sort(value)
    if kind == "pct1_nosign":
        return _f_pct(value, signed=False, digits=1), _num_sort(value)
    if kind == "num0":
        return _f_num(value), _num_sort(value)
    if kind == "num2":
        text = "—" if value is None else f"{value:.2f}"
        return text, _num_sort(value)
    if kind == "dir":
        return _f_dir_label(value), _text_sort(value)
    if kind == "okng":
        return _f_okng(value), _bool_sort(value)
    if kind == "bool_leader":
        return ("★" if value else ""), _bool_sort(value)
    # "text" 既定
    text = value if value else "—"
    return text, _text_sort(value)


def _build_cells(row: dict[str, Any]) -> list[dict[str, Any]]:
    """TABLE_COLUMNS の定義に沿って1行分の表示セルを組み立てる。

    code列・status列はリンク／バッジのHTMLを直接埋め込む（safe=True でエスケープ回避）。
    """
    cells: list[dict[str, Any]] = []
    for key, _label, kind in TABLE_COLUMNS:
        raw = row.get(key)
        if key == "code":
            text = f'<a href="/stock/{row["code"]}">{row["code"]}</a>'
            cells.append({"key": key, "text": text, "safe": True, "sort": _text_sort(row["code"])})
        elif key == "status":
            badge_class = row["status"].lower().replace("_candidate", "")
            text = f'<span class="badge badge-{badge_class}">{row["status"]}</span>'
            cells.append(
                {"key": key, "text": text, "safe": True, "sort": str(STATUS_ORDER.get(row["status"], 9))}
            )
        elif key == "asset_type":
            text = "ETF" if raw == "etf" else "個別株"
            cells.append({"key": key, "text": text, "safe": False, "sort": _text_sort(raw)})
        else:
            text, sort = _cell(kind, raw)
            cells.append({"key": key, "text": text, "safe": False, "sort": sort})
    return cells


# --- ScreenResult → テーブル1行分のフラットな表示用dict --------------------------
# Jinja テンプレート内で r.trend.ma のようなネストアクセスをすると trend=None のときに
# エラーになるため、None安全な形にあらかじめ展開しておく。data-sort-value 属性にも使う。


def _row_view(r: Any) -> dict[str, Any]:
    stock = r.stock
    trend = r.trend
    range_ = r.range_
    volume = r.volume
    rebound = r.rebound
    row = {
        "code": stock.code,
        "name": stock.name,
        "sector": stock.sector,
        "themes": list(stock.theme_names),
        "theme_display": "・".join(stock.theme_names) if stock.theme_names else "—",
        "priority": stock.display_priority,
        "is_leader": stock.is_leader_any,
        "asset_type": stock.asset_type,
        "close": r.latest_close,
        "status": r.status,
        "ma25": trend.ma if trend else None,
        "ma25_dev_pct": trend.ma_deviation_pct if trend else None,
        "ma25_dir": trend.ma_direction if trend else None,
        "trend_strength": trend.strength if trend else None,
        "higher_highs": trend.higher_highs if trend else None,
        "higher_lows": trend.higher_lows if trend else None,
        "range_days": range_.days if range_ else None,
        "range_lower": range_.lower if range_ else None,
        "range_upper": range_.upper if range_ else None,
        "range_width_pct": range_.width_pct if range_ else None,
        "range_quality": range_.quality if range_ else None,
        "lower_touch_count": range_.lower_touch_count if range_ else None,
        "distance_to_lower_pct": r.distance_to_lower_pct,
        "volume_state": volume.state if volume else None,
        "volume_state_label": volume.state_label if volume else None,
        "prev_high": rebound.prev_high if rebound else None,
        "rebound_confirmed": rebound.confirmed if rebound else None,
        "stop_price": r.stop_price,
        "out_reason": r.out_reason,
    }
    row["cells"] = _build_cells(row)
    # 一覧のクライアントJSフィルタが参照する属性（tr の data-* にそのまま使う）
    row["search_text"] = f"{stock.code} {stock.name}".lower()
    row["theme_filter"] = "|" + "|".join(stock.theme_names) + "|" if stock.theme_names else "|"
    return row


class AppState:
    """1プロセス分のスクリーニング結果を保持する。/rescan で作り直す。"""

    def __init__(self, config_path: str, experimental_path: str) -> None:
        self.config_path = config_path
        self.experimental_path = experimental_path
        self.cfg = None
        self.exp = None
        self.stocks: list = []
        self.price_map: dict[str, Any] = {}
        self.run = None
        self.warnings: list[str] = []

    def _reload_params(self) -> None:
        self.cfg = load_config(self.config_path)
        self.exp = load_experimental(self.experimental_path)

    def _clear_chart_cache(self) -> None:
        """レンジ・損切りラインなど experimental 依存の描画があるため、
        再計算のたびにチャートPNGキャッシュを空にして古い画像が残らないようにする。"""
        chart_dir = Path(self.cfg.output.chart_dir)
        if chart_dir.exists():
            for png in chart_dir.glob("*.png"):
                try:
                    png.unlink()
                except OSError:
                    pass

    def refresh(self) -> None:
        """銘柄マスター・価格キャッシュを読み直し、スクリーニングを実行する。
        ネットワークには一切触れない（load_price_map はキャッシュのみを読む）。"""
        self._reload_params()
        self.stocks = load_universe(self.cfg)
        self.price_map, warn_load = screener.load_price_map(self.stocks, self.cfg)
        self.run = screener.run_screening(self.stocks, self.price_map, self.cfg, self.exp)
        self.warnings = list(warn_load) + list(getattr(self.run, "warnings", []))
        self._clear_chart_cache()

    # 初回起動時も rescan もやることは同じ（このツールに「株価取得」は含まれないため）。
    load = refresh
    rescan = refresh

    def result_by_code(self, code: str):
        if self.run is None:
            return None
        for r in self.run.results:
            if r.stock.code == code:
                return r
        return None

    def series_by_code(self, code: str):
        return self.price_map.get(code)

    def ordered_codes(self) -> list[str]:
        if self.run is None:
            return []
        return [r.stock.code for r in self.run.results]

    # --- 保有銘柄 -----------------------------------------------------------
    # 台帳はリクエストのたびに読み直す（CSV を直接編集しても再起動不要にするため）。

    def trades(self) -> list:
        return portfolio.load_trades(self.cfg)

    def holding_views(self) -> list:
        trades = portfolio.open_trades(self.trades())
        return review_mod.build_views(trades, self.price_map, self.cfg, self.exp)

    def trade_by_code(self, code: str):
        """保有中を優先し、無ければ最も新しい決済済みを返す。"""
        all_trades = self.trades()
        open_one = portfolio.find_open(all_trades, code)
        if open_one is not None:
            return open_one
        done = [t for t in portfolio.closed_trades(all_trades) if t.code == code]
        return done[0] if done else None


def create_app(
    config_path: str = "config.yaml",
    experimental_path: str = "experimental.yaml",
) -> FastAPI:
    app = FastAPI(title="日足短期スイング スクリーナー")

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["yen"] = _f_yen
    templates.env.filters["num"] = _f_num
    templates.env.filters["pct"] = _f_pct
    templates.env.filters["okng"] = _f_okng
    templates.env.filters["dirlabel"] = _f_dir_label
    templates.env.filters["datefmt"] = _f_datefmt
    templates.env.filters["datefmt_md"] = _f_datefmt_md

    state = AppState(config_path, experimental_path)
    # 起動時に screener.load_price_map → screener.run_screening を実行して結果を保持する。
    state.load()
    app.state.screening = state

    def _list_context(request: Request) -> dict[str, Any]:
        run = state.run
        rows = [_row_view(r) for r in run.results] if run else []
        counts = run.counts() if run else {s: 0 for s in STATUS_ORDER_LIST}
        sectors = sorted({r["sector"] for r in rows if r["sector"]})
        themes = sorted({t for r in rows for t in r["themes"]})
        try:
            last_fetch_at = price_cache.last_fetch_at(state.cfg)
        except Exception:
            last_fetch_at = None
        return {
            "request": request,
            "run": run,
            "rows": rows,
            "counts": counts,
            "status_order": STATUS_ORDER_LIST,
            "sectors": sectors,
            "themes": themes,
            "as_of": run.as_of if run else None,
            "generated_at": run.generated_at if run else None,
            "last_fetch_at": last_fetch_at,
            "warnings": state.warnings,
            "total": len(rows),
            "day_options": list(state.cfg.chart.day_options),
            "table_columns": TABLE_COLUMNS,
            "holdings_count": len(portfolio.open_trades(state.trades())),
            "nav": "list",
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "list.html", _list_context(request))

    @app.post("/rescan")
    async def rescan():
        state.rescan()
        return RedirectResponse(url="/", status_code=303)

    @app.get("/stock/{code}", response_class=HTMLResponse)
    async def stock_detail(request: Request, code: str, days: int | None = None):
        result = state.result_by_code(code)
        if result is None:
            return HTMLResponse(f"<h1>銘柄 {code} が見つかりません</h1>", status_code=404)

        day_options = list(state.cfg.chart.day_options)
        default_days = int(state.cfg.chart.default_days)
        chart_days = days if days in day_options else default_days

        codes = state.ordered_codes()
        idx = codes.index(code) if code in codes else -1
        prev_code = codes[idx - 1] if idx > 0 else None
        next_code = codes[idx + 1] if 0 <= idx < len(codes) - 1 else None

        series = state.series_by_code(code)
        has_chart = series is not None and len(series.bars) > 0

        lines = explain_lines(result)
        groups = judgement_groups(result)

        context = {
            "request": request,
            "result": result,
            "row": _row_view(result),
            "code": code,
            "chart_days": chart_days,
            "day_options": day_options,
            "prev_code": prev_code,
            "next_code": next_code,
            "explain_text": "\n".join(lines),
            "explain_lines": lines,
            "judgement_groups": groups,
            "has_chart": has_chart,
            "position": (idx + 1) if idx >= 0 else None,
            "total": len(codes),
            "held": portfolio.find_open(state.trades(), code) is not None,
            "nav": "list",
        }
        return templates.TemplateResponse(request, "detail.html", context)

    @app.get("/chart/{code}.png")
    async def chart_png(code: str, days: int | None = None):
        result = state.result_by_code(code)
        series = state.series_by_code(code)
        if result is None or series is None or not series.bars:
            return HTMLResponse(f"チャート用データがありません: {code}", status_code=404)

        day_options = list(state.cfg.chart.day_options)
        default_days = int(state.cfg.chart.default_days)
        chart_days = days if days in day_options else default_days

        chart_dir = Path(state.cfg.output.chart_dir)
        chart_dir.mkdir(parents=True, exist_ok=True)
        output_path = chart_dir / f"{code}_{chart_days}.png"

        if not output_path.exists():
            render_daily_chart(series, result, state.cfg, state.exp, output_path, days=chart_days)

        return FileResponse(output_path, media_type="image/png")

    # --- 保有銘柄 ---------------------------------------------------------------

    @app.get("/holdings", response_class=HTMLResponse)
    async def holdings_list(request: Request):
        views = state.holding_views()
        closed = portfolio.closed_trades(state.trades())
        context = {
            "request": request,
            "views": views,
            "closed": closed,
            "levels": review_mod.summarize_levels(views),
            "level_order": [
                review_mod.LEVEL_SCENARIO_RISK,
                review_mod.LEVEL_CAUTION,
                review_mod.LEVEL_REVIEW,
                review_mod.LEVEL_NONE,
            ],
            "level_labels": review_mod.LEVEL_LABELS_JA,
            "as_of": state.run.as_of if state.run else None,
            "nav": "holdings",
            "trades_path": str(portfolio.trades_path(state.cfg)),
        }
        return templates.TemplateResponse(request, "holdings.html", context)

    @app.get("/holdings/{code}", response_class=HTMLResponse)
    async def holding_detail(request: Request, code: str, days: int | None = None):
        trade = state.trade_by_code(code)
        if trade is None:
            return HTMLResponse(
                f"<h1>{code} のトレード記録がありません</h1>"
                '<p><a href="/holdings">保有銘柄一覧へ戻る</a></p>',
                status_code=404,
            )

        series = state.series_by_code(code)
        view = review_mod.build_view(trade, series, state.cfg, state.exp)

        day_options = list(state.cfg.chart.day_options)
        default_days = int(state.cfg.chart.default_days)
        chart_days = days if days in day_options else default_days

        codes = [v.trade.code for v in state.holding_views()]
        idx = codes.index(code) if code in codes else -1

        context = {
            "request": request,
            "trade": trade,
            "view": view,
            "code": code,
            "chart_days": chart_days,
            "day_options": day_options,
            "has_chart": series is not None and bool(series.bars),
            "prev_code": codes[idx - 1] if idx > 0 else None,
            "next_code": codes[idx + 1] if 0 <= idx < len(codes) - 1 else None,
            "level_labels": review_mod.LEVEL_LABELS_JA,
            "level_descriptions": review_mod.LEVEL_DESCRIPTIONS_JA,
            "review_text": _holding_review_text(view),
            "nav": "holdings",
        }
        return templates.TemplateResponse(request, "holding_detail.html", context)

    @app.get("/holding-chart/{code}.png")
    async def holding_chart_png(code: str, days: int | None = None, as_of: str | None = None):
        trade = state.trade_by_code(code)
        series = state.series_by_code(code)
        if trade is None or series is None or not series.bars:
            return HTMLResponse(f"チャート用データがありません: {code}", status_code=404)

        day_options = list(state.cfg.chart.day_options)
        default_days = int(state.cfg.chart.default_days)
        chart_days = days if days in day_options else default_days

        cutoff = None
        if as_of:
            try:
                cutoff = date.fromisoformat(as_of)
            except ValueError:
                cutoff = None

        chart_dir = Path(state.cfg.output.chart_dir)
        chart_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{cutoff.isoformat()}" if cutoff else ""
        output_path = chart_dir / f"holding_{code}_{chart_days}{suffix}.png"

        if not output_path.exists():
            render_holding_chart(
                series, trade, state.cfg, output_path, days=chart_days, as_of=cutoff
            )
        return FileResponse(output_path, media_type="image/png")

    # --- ENTRY候補履歴 -----------------------------------------------------------

    @app.get("/signals", response_class=HTMLResponse)
    async def signals_page(request: Request):
        rows = journal.load_signals(state.cfg)
        trades = state.trades()
        purchased = {
            (t.code, t.signal_date.isoformat()) for t in trades if t.signal_date is not None
        }
        for row in rows:
            row["purchased"] = (row.get("code"), row.get("signal_date")) in purchased
        rows.sort(key=lambda r: (r.get("signal_date") or "", r.get("code") or ""), reverse=True)
        context = {
            "request": request,
            "rows": rows,
            "columns": journal.SIGNAL_COLUMNS,
            "signals_path": str(journal.signals_path(state.cfg)),
            "snapshot_dates": journal.snapshot_dates(state.cfg),
            "daily_dir": str(journal.daily_dir(state.cfg)),
            "nav": "signals",
        }
        return templates.TemplateResponse(request, "signals.html", context)

    @app.get("/api/results")
    async def api_results():
        if state.run is None:
            return {"results": [], "warnings": state.warnings}
        return screener.run_to_dict(state.run)

    return app


def _holding_review_text(view: Any) -> str:
    """保有銘柄の状態をそのまま ChatGPT へ貼れる文章にする（判定はしない）。"""
    t = view.trade
    lines: list[str] = [f"{t.code} {t.name}"]
    if t.entry_date is not None and t.entry_price is not None:
        lines.append(f"ENTRY: {t.entry_date} {t.entry_price:,.0f}円" + (f" × {t.quantity}株" if t.quantity else ""))
    if t.entry_reason:
        lines.append(f"買った理由: {t.entry_reason}")
    if view.as_of is not None and view.close is not None:
        pnl = f"（{view.pnl_pct:+.1f}%）" if view.pnl_pct is not None else ""
        lines.append(f"現在: {view.as_of} 終値 {view.close:,.0f}円 {pnl}")
    if t.initial_stop is not None:
        dist = f"（あと {view.distance_to_stop_pct:+.1f}%）" if view.distance_to_stop_pct is not None else ""
        lines.append(f"初期STOP: {t.initial_stop:,.0f}円 {dist}")
    if t.original_range_lower is not None and t.original_range_upper is not None:
        lines.append(
            f"買ったときのレンジ: {t.original_range_lower:,.0f}〜{t.original_range_upper:,.0f}円"
            f"（上限 高値到達 {'済' if view.reached_range_upper else 'まだ'} / "
            f"終値突破 {'済' if view.closed_above_range_upper else 'まだ'}）"
        )
    if view.holding_high is not None:
        gain = f"（ENTRY比 {view.holding_high_gain_pct:+.1f}%）" if view.holding_high_gain_pct is not None else ""
        dd = f" / そこから {view.drawdown_from_high_pct:+.1f}%" if view.drawdown_from_high_pct is not None else ""
        lines.append(f"保有後最高値: {view.holding_high:,.0f}円 {gain}{dd}")
    if view.ma25 is not None:
        lines.append(
            f"MA25: {view.ma25:,.0f}円（{_f_dir_label(view.ma_direction)}）"
            f" / 終値は{'上' if view.above_ma25 else '下'}"
        )
    if view.last_bearish_low is not None:
        lines.append(
            f"直近陰線（前日以前）: {view.last_bearish_date} 安値 {view.last_bearish_low:,.0f}円"
            f"（終値は{'割った' if view.below_last_bearish_low else '保っている'}）"
        )
    if view.recent_swing_low is not None:
        lines.append(
            f"直近の局所安値: {view.recent_swing_low:,.0f}円（{view.recent_swing_low_date}）"
        )
    if view.volume is not None:
        ratio = f"（20日平均の {view.volume_vs_avg20:.1f}倍）" if view.volume_vs_avg20 is not None else ""
        lines.append(f"出来高: {view.volume:,}株 {ratio}")

    lines.append("")
    lines.append("買った理由が残っているか:")
    for j in view.scenario:
        mark = "OK" if j.ok is True else ("NG" if j.ok is False else "—")
        lines.append(f"  [{mark}] {j.label}: {j.detail}")

    if view.signs:
        lines.append("")
        lines.append(f"見るべき理由（{view.level}）:")
        for s in view.signs:
            lines.append(f"  ・{s.label}: {s.detail}")

    lines.append("")
    lines.append("※ このツールは売買判定をしません。EXIT は人間が日足を見て判断します。")
    return "\n".join(lines)


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
