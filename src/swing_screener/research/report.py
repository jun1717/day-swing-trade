"""検証レポートHTML（RESEARCH_DESIGN §9）。

外部CDN不使用の自己完結HTML。分布はヒストグラム（インラインSVG）で比較し、
平均の点比較にしない。

**このレポートは結論を書かない。** 事実と分布を提示するだけで、
どの閾値が良いかの判断は人間が行う。
"""

from __future__ import annotations

import html
import statistics
from pathlib import Path

from swing_screener.research.classify import (
    OUTCOME_LABELS_JA,
    OUTCOME_ORDER,
    SHAPE_LABELS_JA,
    SHAPE_ORDER,
)

DISCLAIMER = """
<div class="warn">
<h2>このレポートの読み方（先に必ず読むこと）</h2>
<ul>
<li><b>これはパラメータ最適化ではない。</b> 過去データで最も成績の良い閾値を探すための
資料ではない。「<code>max_position_in_range</code> の違いによって<b>何を拾い、何を捨てるのか</b>」
を理解するための観察記録である。</li>
<li><b>これは収益バックテストではない。</b> シグナル後の値動きを観察したイベントスタディである。
利確ルール・保有継続判断・ポジションサイズは一切考慮していない。</li>
<li><b>「終値基準」の数値は実際には約定できない。</b> 日足確定後に判定する運用では、
その日の終値で買うことは不可能。比較用の基準にすぎない。</li>
<li><b>「翌日始値基準」は参考データであり、新しい売買ルールではない。</b>
実運用との乖離を見るために併記しているだけである。</li>
<li><b>損切り到達率を閾値間で単純比較しない。</b> エントリー位置が高いほど
レンジ下限（＝損切り）までの距離が広がるため、率だけを見ると誤読する。
損切りまでの距離とレンジ上限までの余地を必ず併せて見ること。</li>
<li><b>イベントは独立ではない。</b> 同一銘柄の同一レンジが連日シグナルを出すため、
件数の増加がそのまま機会の増加を意味しない。ユニーク銘柄数を併記している。</li>
</ul>
</div>
"""

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#dcdcdc;--accent:#2b6cb0;
--warn-bg:#fff8e6;--warn-line:#e0b34d;--bad:#b2242f;--good:#2e8b74;}
*{box-sizing:border-box}
body{margin:0;padding:28px 30px 80px;background:var(--bg);color:var(--fg);
font-family:"Hiragino Sans","Yu Gothic",system-ui,-apple-system,sans-serif;
line-height:1.65;font-size:14.5px}
h1{font-size:23px;margin:0 0 4px}
h2{font-size:18px;margin:34px 0 10px;padding-bottom:5px;border-bottom:2px solid var(--line)}
h3{font-size:15.5px;margin:22px 0 8px}
.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.warn{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:7px;
padding:14px 18px;margin:18px 0 26px}
.warn h2{margin:0 0 8px;border:none;font-size:16px}
.warn ul{margin:0;padding-left:20px}
.warn li{margin:5px 0}
table{border-collapse:collapse;width:100%;margin:10px 0 6px;font-size:13.3px}
th,td{border:1px solid var(--line);padding:5px 9px;text-align:right;white-space:nowrap}
th{background:#f4f6f8;font-weight:600;text-align:center}
td.l,th.l{text-align:left}
tbody tr:nth-child(even){background:#fafbfc}
.note{color:var(--muted);font-size:12.5px;margin:4px 0 16px}
.scroll{overflow-x:auto;max-width:100%}
.hl{background:#eef4fb!important;font-weight:600}
.bad{color:var(--bad)}.good{color:var(--good)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:16px}
.charts figure{margin:0;border:1px solid var(--line);border-radius:6px;padding:8px;background:#fff}
.charts img{width:100%;height:auto;display:block}
.charts figcaption{font-size:12.5px;color:var(--muted);margin-top:6px}
details{margin:10px 0}
summary{cursor:pointer;font-weight:600;padding:6px 0}
.evtable{font-size:12.4px}
.bars{font-family:ui-monospace,Menlo,monospace;font-size:12px;white-space:pre}
"""


def _e(v) -> str:
    return html.escape(str(v))


def _num(value, digits=2, suffix="") -> str:
    if value is None:
        return "－"
    return f"{value:.{digits}f}{suffix}"


def _quant(values: list[float]) -> dict:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return {}
    n = len(clean)
    mid = n // 2
    lower = clean[:mid]
    upper = clean[mid + 1:] if n % 2 else clean[mid:]
    return {
        "n": n,
        "min": clean[0],
        "q1": statistics.median(lower) if lower else clean[0],
        "median": statistics.median(clean),
        "q3": statistics.median(upper) if upper else clean[-1],
        "max": clean[-1],
        "mean": statistics.fmean(clean),
    }


def _histogram_svg(values: list[float], *, bins: int = 14, width: int = 330,
                   height: int = 110, color: str = "#2b6cb0",
                   zero_line: bool = True) -> str:
    clean = [v for v in values if v is not None]
    if not clean:
        return '<div class="note">データなし</div>'
    lo, hi = min(clean), max(clean)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in clean:
        i = min(bins - 1, int((v - lo) / step))
        counts[i] += 1
    peak = max(counts) or 1
    bw = width / bins
    rects = []
    for i, c in enumerate(counts):
        h = (c / peak) * (height - 24)
        rects.append(
            f'<rect x="{i*bw:.1f}" y="{height-20-h:.1f}" width="{bw-1.4:.1f}" '
            f'height="{h:.1f}" fill="{color}" opacity="0.78"></rect>'
        )
    zero = ""
    if zero_line and lo < 0 < hi:
        zx = (0 - lo) / (hi - lo) * width
        zero = (f'<line x1="{zx:.1f}" y1="0" x2="{zx:.1f}" y2="{height-20}" '
                f'stroke="#b2242f" stroke-width="1" stroke-dasharray="3,2"></line>')
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'{"".join(rects)}{zero}'
        f'<line x1="0" y1="{height-20}" x2="{width}" y2="{height-20}" stroke="#999"></line>'
        f'<text x="0" y="{height-6}" font-size="10.5" fill="#666">{lo:.1f}</text>'
        f'<text x="{width}" y="{height-6}" font-size="10.5" fill="#666" '
        f'text-anchor="end">{hi:.1f}</text>'
        f'<text x="{width/2}" y="{height-6}" font-size="10.5" fill="#999" '
        f'text-anchor="middle">n={len(clean)}</text></svg>'
    )


def _upside_pct(ev) -> float | None:
    if not ev.range_upper or not ev.signal_close:
        return None
    return (ev.range_upper - ev.signal_close) / ev.signal_close * 100.0


def _overview_table(result) -> str:
    rows = []
    for tr in result.ordered():
        events = tr.events
        complete = tr.complete_events
        codes = {e.code for e in events}
        pos = _quant([e.position_in_range for e in events])
        stop_d = _quant([e.stop_distance_pct_from_close for e in events])
        upside = _quant([_upside_pct(e) for e in events])
        rr = (upside.get("median") / stop_d.get("median")
              if stop_d.get("median") else None)
        stop_rate = tr.stop_rate()
        cls = ' class="hl"' if tr.threshold == 0.65 else ""
        rows.append(
            f"<tr{cls}><td class='l'>{_e(tr.label)}{' ←現行' if tr.threshold == 0.65 else ''}</td>"
            f"<td>{len(events)}</td><td>{len(codes)}</td><td>{len(complete)}</td>"
            f"<td>{_num(pos.get('median'))}</td>"
            f"<td>{_num(stop_d.get('median'), 2, '%')}</td>"
            f"<td>{_num(upside.get('median'), 2, '%')}</td>"
            f"<td>{_num(rr)}</td>"
            f"<td>{_num(stop_rate, 0, '%')}</td></tr>"
        )
    return (
        "<div class='scroll'><table><thead><tr>"
        "<th class='l'>閾値</th><th>ENTRY件数</th><th>ユニーク銘柄</th>"
        "<th>forward完全</th><th>レンジ内位置<br>中央値</th>"
        "<th>損切りまで<br>中央値</th><th>レンジ上限まで<br>中央値</th>"
        "<th>上限余地/損切距離</th><th>損切り到達率</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        "<div class='note'>「上限余地/損切距離」は、レンジ上限までの伸びしろを損切りまでの距離で"
        "割った比率。この戦略が狙う「レンジ下限から次の上昇波」に対して、どれだけの値幅を"
        "リスクに晒しているかの目安。<b>損切り到達率は閾値間で単純比較できない</b>"
        "（位置が高いほど損切りが遠いため率が下がる）。</div>"
    )


def _shape_table(result) -> str:
    head = "".join(f"<th>{_e(SHAPE_LABELS_JA[s])}</th>" for s in SHAPE_ORDER)
    rows = []
    for tr in result.ordered():
        counts = tr.shape_counts()
        cls = ' class="hl"' if tr.threshold == 0.65 else ""
        cells = "".join(f"<td>{counts.get(s, 0)}</td>" for s in SHAPE_ORDER)
        rows.append(f"<tr{cls}><td class='l'>{_e(tr.label)}</td>{cells}</tr>")
    return (
        "<div class='scroll'><table><thead><tr><th class='l'>閾値</th>"
        + head + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _outcome_table(result) -> str:
    head = "".join(f"<th>{_e(OUTCOME_LABELS_JA[o])}</th>" for o in OUTCOME_ORDER)
    rows = []
    for tr in result.ordered():
        complete = tr.complete_events
        n = len(complete) or 1
        counts: dict[str, int] = {}
        for e in complete:
            counts[e.outcome] = counts.get(e.outcome, 0) + 1
        cls = ' class="hl"' if tr.threshold == 0.65 else ""
        cells = "".join(
            f"<td>{counts.get(o, 0)}<br><span class='note'>{counts.get(o, 0)/n*100:.0f}%</span></td>"
            for o in OUTCOME_ORDER
        )
        rows.append(f"<tr{cls}><td class='l'>{_e(tr.label)}</td><td>{len(complete)}</td>{cells}</tr>")
    return (
        "<div class='scroll'><table><thead><tr><th class='l'>閾値</th><th>n</th>"
        + head + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        "<div class='note'>forward が10営業日分揃ったイベントのみが母数。"
        "「損切り到達」は10営業日以内に初期損切り価格へ到達したもので、"
        "その前にレンジ上限へ到達していたケースも含む（実運用では利確や損切り引上げの"
        "機会があった可能性がある）。</div>"
    )


def _cross_table(result, threshold_label_str: str) -> str:
    tr = result.by_threshold.get(threshold_label_str)
    if tr is None:
        return ""
    complete = tr.complete_events
    head = "".join(f"<th>{_e(OUTCOME_LABELS_JA[o])}</th>" for o in OUTCOME_ORDER)
    rows = []
    for shape in SHAPE_ORDER:
        subset = [e for e in complete if e.shape == shape]
        if not subset:
            continue
        cells = "".join(
            f"<td>{sum(1 for e in subset if e.outcome == o)}</td>" for o in OUTCOME_ORDER
        )
        rows.append(
            f"<tr><td class='l'>{_e(SHAPE_LABELS_JA[shape])}</td>"
            f"<td>{len(subset)}</td>{cells}</tr>"
        )
    if not rows:
        return "<div class='note'>該当データなし</div>"
    return (
        "<div class='scroll'><table><thead><tr><th class='l'>形状</th><th>n</th>"
        + head + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _distributions(result) -> str:
    specs = [
        ("position_in_range", "ENTRY時のレンジ内位置", "#2b6cb0", False, False),
        ("days_from_touch_to_signal", "下限接触からENTRYまでの営業日数", "#7b5ea7", False, False),
        ("fwd5_max_gain_pct_from_close", "5日最大上昇率（終値基準・約定不可）", "#2e8b74", True, True),
        ("fwd10_max_gain_pct_from_close", "10日最大上昇率（終値基準・約定不可）", "#2e8b74", True, True),
        ("fwd5_max_loss_pct_from_close", "5日最大下落率（終値基準・約定不可）", "#b2242f", True, True),
        ("fwd10_max_loss_pct_from_close", "10日最大下落率（終値基準・約定不可）", "#b2242f", True, True),
    ]
    out = []
    for attr, title, color, complete_only, zero in specs:
        cells = []
        for tr in result.ordered():
            events = tr.complete_events if complete_only else tr.events
            values = [getattr(e, attr) for e in events]
            q = _quant(values)
            stats = (
                f"中央値 {_num(q.get('median'))} / "
                f"四分位 {_num(q.get('q1'))}〜{_num(q.get('q3'))} / "
                f"平均 {_num(q.get('mean'))}"
                if q else "データなし"
            )
            cells.append(
                f"<figure><figcaption><b>{_e(tr.label)}</b><br>{stats}</figcaption>"
                f"{_histogram_svg(values, color=color, zero_line=zero)}</figure>"
            )
        out.append(f"<h3>{_e(title)}</h3><div class='charts'>{''.join(cells)}</div>")
    return "".join(out)


def _added_events(result) -> str:
    blocks = []
    for prev, cur, added in result.added_by_loosening():
        if not added:
            blocks.append(f"<h3>{_e(prev)} → {_e(cur)}：追加 0 件</h3>")
            continue
        shape_counts: dict[str, int] = {}
        for e in added:
            shape_counts[e.shape] = shape_counts.get(e.shape, 0) + 1
        breakdown = "、".join(
            f"{SHAPE_LABELS_JA.get(s, s)} {c}件"
            for s, c in sorted(shape_counts.items(), key=lambda kv: -kv[1])
        )
        complete = [e for e in added if e.forward_complete]
        stop_rate = (
            sum(1 for e in complete if e.fwd10_hit_stop) / len(complete) * 100
            if complete else None
        )
        pos = _quant([e.position_in_range for e in added])
        upside = _quant([_upside_pct(e) for e in added])
        stop_d = _quant([e.stop_distance_pct_from_close for e in added])
        rows = "".join(
            f"<tr><td class='l'>{_e(e.date)}</td><td class='l'>{_e(e.code)}</td>"
            f"<td class='l'>{_e(e.name)}</td>"
            f"<td>{_num(e.position_in_range)}</td>"
            f"<td>{_num(_upside_pct(e), 2, '%')}</td>"
            f"<td>{_num(e.stop_distance_pct_from_close, 2, '%')}</td>"
            f"<td class='l'>{_e(SHAPE_LABELS_JA.get(e.shape, e.shape))}</td>"
            f"<td class='l'>{_e(OUTCOME_LABELS_JA.get(e.outcome, e.outcome))}</td></tr>"
            for e in added
        )
        blocks.append(
            f"<h3>{_e(prev)} → {_e(cur)}：追加 {len(added)} 件</h3>"
            f"<div class='note'>内訳: {_e(breakdown)}／"
            f"レンジ内位置 中央値 {_num(pos.get('median'))}／"
            f"レンジ上限までの余地 中央値 {_num(upside.get('median'), 2, '%')}／"
            f"損切りまでの距離 中央値 {_num(stop_d.get('median'), 2, '%')}／"
            f"損切り到達率 {_num(stop_rate, 0, '%')}</div>"
            f"<details><summary>追加された {len(added)} 件を表示</summary>"
            f"<div class='scroll'><table class='evtable'><thead><tr>"
            f"<th class='l'>日付</th><th class='l'>コード</th><th class='l'>銘柄</th>"
            f"<th>位置</th><th>上限余地</th><th>損切距離</th>"
            f"<th class='l'>形状</th><th class='l'>転帰</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div></details>"
        )
    return "".join(blocks)


def _charts_section(out_dir: Path) -> str:
    charts_dir = out_dir / "charts"
    if not charts_dir.exists():
        return "<div class='note'>チャート未生成</div>"
    from swing_screener.research.charts import CATEGORIES

    blocks = []
    for cat in CATEGORIES:
        files = sorted(charts_dir.glob(f"{cat.key}_*.png"))
        if not files:
            blocks.append(f"<h3>{_e(cat.title_ja)}</h3><div class='note'>該当例なし</div>")
            continue
        figs = "".join(
            f"<figure><img src='charts/{_e(p.name)}' alt='{_e(p.stem)}'>"
            f"<figcaption>{_e(p.stem)}</figcaption></figure>"
            for p in files
        )
        blocks.append(f"<h3>{_e(cat.title_ja)}</h3><div class='charts'>{figs}</div>")
    return "".join(blocks)


def _event_table(result) -> str:
    events = sorted(result.all_events, key=lambda e: (e.date, e.code), reverse=True)
    rows = "".join(
        f"<tr><td class='l'>{_e(e.date)}</td><td class='l'>{_e(e.code)}</td>"
        f"<td class='l'>{_e(e.name)}</td><td class='l'>{_e(e.themes)}</td>"
        f"<td>{_num(e.signal_close, 0)}</td>"
        f"<td>{_num(e.position_in_range)}</td>"
        f"<td>{_num(e.range_width_pct, 1, '%')}</td>"
        f"<td>{e.range_days}</td><td>{e.lower_touch_count}</td>"
        f"<td>{e.days_from_touch_to_signal if e.days_from_touch_to_signal is not None else '－'}</td>"
        f"<td>{_num(e.breakout_pct_vs_prev_high, 2, '%')}</td>"
        f"<td>{_num(e.stop_distance_pct_from_close, 2, '%')}</td>"
        f"<td>{_num(e.fwd10_max_gain_pct_from_close, 2, '%')}</td>"
        f"<td>{_num(e.fwd10_max_loss_pct_from_close, 2, '%')}</td>"
        f"<td class='l'>{_e(SHAPE_LABELS_JA.get(e.shape, e.shape))}</td>"
        f"<td class='l'>{_e(OUTCOME_LABELS_JA.get(e.outcome, e.outcome))}</td></tr>"
        for e in events
    )
    return (
        f"<details><summary>全 {len(events)} 件のENTRYイベントを表示"
        f"（制限なし＝全候補）</summary>"
        "<div class='scroll'><table class='evtable'><thead><tr>"
        "<th class='l'>日付</th><th class='l'>コード</th><th class='l'>銘柄</th>"
        "<th class='l'>テーマ</th><th>終値</th><th>位置</th><th>幅</th>"
        "<th>日数</th><th>下限反応</th><th>接触から</th><th>前日高値突破</th>"
        "<th>損切距離</th><th>10日上昇</th><th>10日下落</th>"
        "<th class='l'>形状</th><th class='l'>転帰</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div></details>"
    )


def write_report(sweep_result, out_dir: Path) -> Path:
    """research/report.html を書き出す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    r = sweep_result
    verified = (
        "一致（事後導出を使用）" if r.derivation_verified
        else f"不一致 {len(r.derivation_mismatches)} 件"
    )
    body = f"""
<h1>max_position_in_range 検証レポート</h1>
<div class="sub">
検証期間 {_e(r.start)} 〜 {_e(r.end)}（{r.months}ヶ月指定 / warmup {r.warmup}本）
対象銘柄 {r.stock_count}　営業日 {r.trading_days}
リプレイ 銘柄日 {r.stock_count * r.trading_days:,} 規模<br>
事後導出の等価性検証: {_e(verified)}
</div>
{DISCLAIMER}

<h2>1. 閾値別の全体像</h2>
{_overview_table(r)}

<h2>2. 形状分類（シグナル時点で何を拾っているか）</h2>
{_shape_table(r)}
<div class='note'>形状ラベルはレンジ内位置で区切っている。
A（理想的な下限反発）の境界は 0.65 に置いているため、0.65 以上の閾値で
A の件数が増えないのは定義上あたりまえである点に注意。
意味があるのは「緩めたときに<b>何が</b>追加されるか」であって、
A が増えないこと自体ではない。</div>

<h2>3. 転帰分類（シグナル後に何が起きたか）</h2>
{_outcome_table(r)}

<h2>4. 形状 × 転帰（現行 0.65）</h2>
{_cross_table(r, "0.65")}
<h3>形状 × 転帰（制限なし）</h3>
{_cross_table(r, "制限なし")}

<h2>5. 分布</h2>
<div class='note'>平均の点比較では分布の形が見えないため、ヒストグラムで比較する。
赤い破線は 0%。</div>
{_distributions(r)}

<h2>6. 閾値を緩めたときに追加されるイベント</h2>
<div class='note'>この検証の本題。「緩めると何が入ってくるのか」を1件ずつ確認できる。</div>
{_added_events(r)}

<h2>7. 代表チャート</h2>
<div class='note'>シグナル日を黒い縦線で示す。その右側は判定に使っていない未来の値動き
（検証用に表示している）。</div>
{_charts_section(out_dir)}

<h2>8. ENTRYイベント一覧</h2>
{_event_table(r)}

<div class="warn" style="margin-top:34px">
<h2>結論について</h2>
<p>本レポートは事実と分布を提示するだけであり、推奨する閾値を示さない。
過去データに対して最も成績の良い閾値を選ぶことは、この検証の目的ではない。</p>
</div>
"""
    doc = (
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>max_position_in_range 検証レポート</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )
    path = out_dir / "report.html"
    path.write_text(doc, encoding="utf-8")
    return path
