"""EXIT 状態機械スタディのレポートHTML。

`exit_state_machine.py` の検証結果を出力する。姉妹モジュール
`exit_report.py`（前回の EXIT スタディ用）と同じ規約に合わせている:
CSS・エスケープヘルパー・`.warn` 免責ブロック・`.ref` 補足ボックス・
`.scroll` テーブルラッパー・`.pill` フラグ表示・`.q` Q&A ブロック。

外部CDN不使用の自己完結HTML。**結論を書かない。**
「どの CASE が勝つか」ではなく「文章ルールをどこまで状態機械として
自然に再現できたか」を提示するだけで、成績のよい CASE を採用する
という結論は出さない。
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from swing_screener.research.exit_state_machine import (
    CASE1,
    CASE2,
    CASE3,
    CASES,
    CASE_LABELS_JA,
    E_AMBIGUOUS_STOP,
    E_AMBIGUOUS_WARNING,
    E_DATA_END,
    E_ENTRY,
    E_GAP_THROUGH,
    E_REHIGH,
    E_STOP_KEPT,
    E_STOP_RAISED,
    E_UPPER_CLOSE_BREAK,
    E_UPPER_HIGH_ONLY,
    E_WARNING_CANDLE,
    E_WARNING_EXTRA,
    E_WARNING_LOW_BREAK,
    FLAG_LABELS_JA,
    GIVEBACK_MOST_RATIO,
    PATH_LABELS_JA,
    X_DATA_END,
    X_INITIAL_STOP,
    X_INITIAL_STOP_AFTER_BREAK,
    X_TRAIL_STOP,
    X_WARNING_LOW,
    SMEvent,
    SummaryRow,
)
from swing_screener.research.exit_study import _median, _rate

# --- 免責 -----------------------------------------------------------------

DISCLAIMER = """
<div class="warn">
<h2>このレポートの読み方（先に必ず読むこと）</h2>
<ul>
<li><b>これは収益バックテストではない。</b>
「現在の文章ルール（警戒陰線・高値更新・押し安値・トレーリング）を
日足の状態機械としてどこまで自然に再現できるか」の検証である。
勝率・平均利益率で戦略を評価しないこと。</li>
<li><b>今回の検証仮説:</b> 警戒陰線を ENTRY 直後からではなく
「元レンジ上限を終値突破した後」から有効化した。
<b>これは仮説であり正式ルールではない。</b></li>
<li><b>変更していない確定ルール:</b> ENTRY ロジック /
<code>near.max_position_in_range = 0.65</code> /
<code>initial_stop = range_lower × 0.995</code> の3点のみ。</li>
<li><b>CASE2（warning_low 下抜けで利確）と CASE3（トレーリング）は
文章ルールの読み方であり、成績のよい CASE を採用するという
結論は出さない。</b> 目的はどこまで機械化しどこから人間判断にするかを
見えるようにすることであって、勝ち負けを決めることではない。</li>
<li><b>+3/+5/+10% とレンジ上限到達は分析指標としてのみ記録している。</b>
機械的な利確には一切使っていない。</li>
<li><b>大陰線・出来高急増・安値引けは参考指標のみ。</b>
自動 EXIT の判定には使っていない（人間がチャートを見る
<code>MANUAL_EXIT_REVIEW</code> 用）。</li>
<li><b>約定価格は保証されない。</b> warning_low も active_stop も、
寄りがその水準を割っていれば始値を参考価格にしている。</li>
<li><b>母数 32 件と小さい。</b> 率は参考程度に留めること。</li>
</ul>
</div>
"""

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#dcdcdc;--accent:#2b6cb0;
--warn-bg:#fff8e6;--warn-line:#e0b34d;--bad:#b2242f;--good:#2e8b74;--ref-bg:#f2f6fa;}
*{box-sizing:border-box}
body{margin:0;padding:28px 30px 90px;background:var(--bg);color:var(--fg);
font-family:"Hiragino Sans","Yu Gothic",system-ui,-apple-system,sans-serif;
line-height:1.65;font-size:14.5px}
h1{font-size:23px;margin:0 0 4px}
h2{font-size:18px;margin:36px 0 10px;padding-bottom:5px;border-bottom:2px solid var(--line)}
h3{font-size:15.5px;margin:24px 0 8px}
.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.warn{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:7px;
padding:14px 18px;margin:18px 0 26px}
.warn h2{margin:0 0 8px;border:none;font-size:16px}
.warn ul{margin:0;padding-left:20px}
.warn li{margin:6px 0}
.ref{background:var(--ref-bg);border-left:4px solid var(--accent);padding:10px 14px;
margin:12px 0 18px;font-size:13.5px;border-radius:0 5px 5px 0}
table{border-collapse:collapse;width:100%;margin:10px 0 18px;font-size:13px}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{background:#f5f5f5;font-weight:600;white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:nth-child(even) td{background:#fafafa}
.scroll{overflow-x:auto}
.bad{color:var(--bad)}.good{color:var(--good)}.muted{color:var(--muted)}
code{background:#f0f0f0;padding:1px 5px;border-radius:3px;font-size:12.5px}
pre.tl{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
line-height:1.55;color:#333;background:#fafafa;border:1px solid var(--line);
border-radius:6px;padding:12px 16px;overflow-x:auto}
.tl{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
line-height:1.5;color:#333}
.tl b{color:var(--bad)}
.chart{margin:16px 0 26px}
.chart img{max-width:100%;border:1px solid var(--line);border-radius:5px}
.chart .cap{font-size:12.5px;color:var(--muted);margin-top:5px}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11.5px;
background:#eef2f7;color:#33506e;margin-right:4px;white-space:nowrap}
.q{background:#fbfbfb;border:1px solid var(--line);border-radius:6px;
padding:12px 16px;margin:12px 0}
.q h4{margin:0 0 6px;font-size:14.5px}
.q p{margin:4px 0}
"""


# --- 小さな共通ヘルパ（exit_report.py と同じ規約）--------------------------


def _e(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _pct(v: float | None, digits: int = 2, sign: bool = True) -> str:
    if v is None:
        return "－"
    return f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"


def _cls(v: float | None) -> str:
    if v is None:
        return ""
    return "good" if v > 0 else ("bad" if v < 0 else "")


def _day(off: int | None) -> str:
    return f"D+{off}" if off is not None else "－"


def _mark(b: bool | None) -> str:
    return "●" if b else "○"


def _find_row(summary: list[SummaryRow], metric_substr: str) -> SummaryRow | None:
    for r in summary:
        if metric_substr in r.metric:
            return r
    return None


def _summary_table(rows: list[SummaryRow]) -> str:
    out = ["<table><tr><th>区分</th><th>指標</th><th>値</th><th>注記</th></tr>"]
    for r in rows:
        out.append(
            f"<tr><td>{_e(r.section)}</td><td>{_e(r.metric)}</td>"
            f"<td class='num'><b>{_e(r.value)}</b></td>"
            f"<td class='muted'>{_e(r.note)}</td></tr>"
        )
    out.append("</table>")
    return "".join(out)


_EXIT_TYPE_LABELS_JA = {
    X_INITIAL_STOP: "初期STOP（突破前）",
    X_INITIAL_STOP_AFTER_BREAK: "初期STOP（突破後・trail前）",
    X_WARNING_LOW: "warning_low利確（CASE2）",
    X_TRAIL_STOP: "trail STOP",
    X_DATA_END: "追跡終端（保有継続）",
    "NO_ENTRY": "仮想ENTRY不可",
}

_RESOLUTION_LABELS_JA = {
    "low_break": "warning_low割れ",
    "rehigh": "再高値更新",
    "ambiguous_both": "同日両方成立（順序不明）",
    "stop": "STOP到達で決着",
    "open": "未決着（追跡終端）",
}

# --- §2 状態遷移の定義 -------------------------------------------------------

STATE_DIAGRAM = """\
INITIAL_HOLD
   │ 終値 > 元レンジ上限  (RANGE_UPPER_CLOSE_BREAK)
   ▼
TREND_HOLD  ◄────────────────────────────────────┐
   │ 上限突破後、最初の陰線 (WARNING_CANDLE)         │
   │ ※判定は翌営業日から開始（解釈 a）                │
   ▼                                              │
WARNING ──────────────┐                           │
   │ 高値 > reference_high (REHIGH_CONFIRMED)      │
   │   → 押し安値確定・STOP引き上げ (TRAIL_STOP_RAISED)
   └──────────────────────────────────────────────┘
   │ 安値 < warning_low (WARNING_LOW_BREAK)
   └─► 利確候補（CASE2 のみ EXIT。CASE3 は解釈(b)により WARNING に留まる）

（いずれの状態でも）安値 <= active_stop → CLOSED（EXIT）
"""

TRANSITION_ROWS = [
    ("INITIAL_HOLD", "RANGE_UPPER_CLOSE_BREAK", "TREND_HOLD",
     "終値 &gt; 元レンジ上限", "警戒足の判定は翌営業日から開始（解釈a）"),
    ("INITIAL_HOLD", "（active_stop到達）", "CLOSED / INITIAL_STOP_EXIT",
     "安値 &lt;= active_stop（= initial_stop）", ""),
    ("TREND_HOLD", "WARNING_CANDLE", "WARNING",
     "上限突破後で最初の陰線（終値&lt;始値）", "警戒足は1本だけ拾い、以後は置き換えない（§9）"),
    ("TREND_HOLD", "（active_stop到達）", "CLOSED / INITIAL_STOP_EXIT_AFTER_BREAKOUT",
     "安値 &lt;= active_stop", "解釈(c)。4つ目のEXIT種別"),
    ("WARNING", "WARNING_LOW_BREAK", "WARNING（CASE2のみEXIT）",
     "安値 &lt; warning_low", "CASE3は解釈(b)によりWARNINGへ留まる"),
    ("WARNING", "REHIGH_CONFIRMED", "TREND_HOLD",
     "高値 &gt; reference_high", "押し安値確定・STOP引き上げ（TRAIL_STOP_RAISED）を伴う"),
    ("WARNING", "（active_stop到達）", "CLOSED / TRAIL_STOP_EXIT または INITIAL_STOP_EXIT_AFTER_BREAKOUT",
     "安値 &lt;= active_stop", "引き上げ済みなら TRAIL_STOP_EXIT"),
]

INTERPRETATION_NOTES = [
    (
        "(a)",
        "TREND_HOLD へ入った当日（＝上限突破日）と、再高値更新が確定した当日の"
        "ローソクが陰線だった場合に、それを即その状態の警戒足とするか。"
        "→「その後に最初に発生した陰線」（§8）を素直に取り、<b>翌営業日から</b>"
        "警戒足の判定を始める。同日採用にした場合の差分は"
        "<code>same_day_bearish_at_trend_entry</code> として件数だけ記録する。",
    ),
    (
        "(b)",
        "CASE3（トレーリング）で warning_low を割ったあと、どの状態へ行くか。"
        "→ 文章ルールに「警戒足を置き換えない」（§9）とあるので WARNING に留まり、"
        "reference_high を抜くか active_stop に当たるまで待つ。"
        "この「割ったのに WARNING に居続ける」滞留日数を記録する。",
    ),
    (
        "(c)",
        "上限突破後・トレーリング成立前に active_stop（＝初期STOPのまま）へ"
        "到達した場合。§12 の EXIT A/B/C のどれにも当てはまらないため"
        "<code>INITIAL_STOP_EXIT_AFTER_BREAKOUT</code> として4つ目のEXIT種別を置いた。",
    ),
]


def _state_machine_section() -> str:
    rows = ["<tr><th>現在の状態</th><th>イベント</th><th>次の状態</th>"
            "<th>条件</th><th>備考</th></tr>"]
    for cur, ev, nxt, cond, note in TRANSITION_ROWS:
        rows.append(
            f"<tr><td>{_e(cur)}</td><td><code>{_e(ev)}</code></td><td>{_e(nxt)}</td>"
            f"<td>{cond}</td><td class='muted'>{_e(note)}</td></tr>"
        )
    interp = "".join(
        f"<li><b>{label}</b> {text}</li>" for label, text in INTERPRETATION_NOTES
    )
    return (
        f"<pre class='tl'>{_e(STATE_DIAGRAM)}</pre>"
        "<div class='scroll'><table>" + "".join(rows) + "</table></div>"
        "<div class='ref'><b>実装時に読み方を決めた箇所</b>"
        "（文章ルールが曖昧で、実装のために解釈を選んだ3箇所。"
        "この検証で唯一の正解として確定したわけではない）"
        f"<ol>{interp}</ol></div>"
    )


# --- §3 CASE 比較 -----------------------------------------------------------


def _case_compare_table(events: list[SMEvent]) -> str:
    head_cells = "".join(
        f"<th>{c[:5]}種別</th>"
        f"<th>{c[:5]}日</th><th>{c[:5]}リターン</th>"
        f"<th>{c[:5]}最大含み益</th><th>{c[:5]}吐き出し</th>"
        for c in CASES
    )
    rows = [f"<tr><th>銘柄</th><th>経路</th>{head_cells}</tr>"]
    for e in sorted(events, key=lambda x: (x.signal_date, x.code)):
        cells = []
        for c in CASES:
            r = e.cases[c]
            cells.append(
                f"<td>{_e(_EXIT_TYPE_LABELS_JA.get(r.exit_type, r.exit_type))}</td>"
                f"<td class='num'>{_day(r.exit_day_offset)}</td>"
                f"<td class='num {_cls(r.approximate_return_pct)}'>"
                f"{_pct(r.approximate_return_pct, 1)}</td>"
                f"<td class='num'>{_pct(r.max_gain_pct, 1)}</td>"
                f"<td class='num'>"
                f"{f'{r.giveback_pct:.2f}pt' if r.giveback_pct is not None else '－'}</td>"
            )
        rows.append(
            f"<tr><td>{_e(e.code)} {_e(e.name[:10])}<br>"
            f"<span class='muted'>{e.signal_date}</span></td>"
            f"<td>{_e(PATH_LABELS_JA.get(e.path_label, e.path_label))}</td>"
            + "".join(cells) + "</tr>"
        )
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


def _case_aggregate_table(entered: list[SMEvent]) -> str:
    rows = [
        "<tr><th>CASE</th><th>仮想EXIT件数</th><th>リターン中央値</th>"
        "<th>最大含み益中央値</th><th>吐き出し幅中央値</th>"
        "<th>+5%以上上昇後に損失</th><th>+10%以上から利益の過半を失った</th></tr>"
    ]
    for c in CASES:
        rs = [e.cases[c] for e in entered]
        rets = [r.approximate_return_pct for r in rs if r.approximate_return_pct is not None]
        gains = [r.max_gain_pct for r in rs if r.max_gain_pct is not None]
        gives = [r.giveback_pct for r in rs if r.giveback_pct is not None]
        exited = sum(1 for r in rs if not r.still_open)
        rose5 = sum(1 for r in rs if r.rose5_then_lost)
        gave_most = sum(1 for r in rs if r.gave_back_most)
        med_ret = _median(rets)
        med_gain = _median(gains)
        med_give = _median(gives)
        rows.append(
            f"<tr><td>{_e(CASE_LABELS_JA[c])}</td>"
            f"<td class='num'>{_rate(exited, len(rs))}</td>"
            f"<td class='num {_cls(med_ret)}'>{_pct(med_ret) if med_ret is not None else '－'}</td>"
            f"<td class='num'>{_pct(med_gain) if med_gain is not None else '－'}</td>"
            f"<td class='num'>{f'{med_give:.2f}pt' if med_give is not None else '－'}</td>"
            f"<td class='num'>{_rate(rose5, len(rs))}</td>"
            f"<td class='num'>{_rate(gave_most, len(rs))}</td></tr>"
        )
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


def _case_section(events: list[SMEvent], entered: list[SMEvent]) -> str:
    return (
        "<p class='muted'>1行1イベント。列は CASE1（初期STOPのみ）/ "
        "CASE2（warning_low利確）/ CASE3（トレーリング）の順。"
        "「経路」は <code>classify_path()</code> による分析用の区分ラベルで、売買ルールではない。</p>"
        + _case_compare_table(events)
        + "<h3>CASE別の集計（比較用。これで勝敗を決めない）</h3>"
        + _case_aggregate_table(entered)
        + "<div class='ref'>この比較表は<b>どの CASE が勝つかを決めるための表ではない</b>。"
        "目的は、文章ルール（CASE1=前回と同じ初期STOPのみ／"
        "CASE2=warning_low下抜けで利確／CASE3=トレーリングで warning_low では降りない）"
        "を、状態機械としてどこまで素直に再現できたかを見ることである。"
        f"母数は {len(entered)} 件と小さく、"
        "「+10%以上から利益の過半を失った」の「過半」は "
        f"GIVEBACK_MOST_RATIO={GIVEBACK_MOST_RATIO}（最大含み益の"
        f"{GIVEBACK_MOST_RATIO * 100:.0f}%未満に最終リターンが落ちたか）という"
        "集計上の定義であって売買閾値ではない。"
        "CASE間の優劣は今後の定義変更で容易に反転し得るため、"
        "<b>成績のよい CASE を採用するという結論はここでは出さない。</b></div>"
    )


# --- §4 イベント一覧 ---------------------------------------------------------


def _event_table(events: list[SMEvent]) -> str:
    head = (
        "<tr><th>シグナル日</th><th>銘柄</th><th>経路</th><th>ギャップ</th>"
        "<th>上限終値<br>突破日</th><th>警戒足<br>本数</th>"
        "<th>再高値<br>更新回数</th><th>STOP<br>引上回数</th>"
        "<th>最大STOP<br>初期比</th><th>CASE1<br>リターン</th>"
        "<th>CASE2<br>リターン</th><th>CASE3<br>リターン</th><th>フラグ</th></tr>"
    )
    rows = [head]
    for e in sorted(events, key=lambda x: (x.signal_date, x.code)):
        if e.initial_stop:
            max_stop_pct = (
                (e.max_active_stop - e.initial_stop) / e.initial_stop * 100.0
            )
        else:
            max_stop_pct = None
        stop_pct_display = (
            _pct(max_stop_pct, 2, False) if max_stop_pct is not None else "－"
        )
        flags = "".join(
            f"<span class='pill'>{_e(FLAG_LABELS_JA.get(f, f))}</span>" for f in e.flags
        )
        rets = [e.cases[c].approximate_return_pct for c in CASES]
        ret_cells = "".join(
            f"<td class='num {_cls(r)}'>{_pct(r, 1)}</td>" for r in rets
        )
        rows.append(
            f"<tr><td>{e.signal_date}</td>"
            f"<td>{_e(e.code)} {_e(e.name[:10])}</td>"
            f"<td>{_e(PATH_LABELS_JA.get(e.path_label, e.path_label))}</td>"
            f"<td class='num {_cls(e.gap_pct)}'>{_pct(e.gap_pct)}</td>"
            f"<td class='num'>{_day(e.upper_close_break_day_offset)}</td>"
            f"<td class='num'>{e.warning_count}</td>"
            f"<td class='num'>{e.rehigh_count}</td>"
            f"<td class='num'>{e.stop_raise_count}</td>"
            f"<td class='num'>{stop_pct_display}</td>"
            f"{ret_cells}"
            f"<td>{flags}</td></tr>"
        )
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


# --- §5 状態遷移の時系列 ------------------------------------------------------

_KIND_SHORT = {
    E_ENTRY: "ENTRY",
    E_UPPER_CLOSE_BREAK: "上限終値突破",
    E_UPPER_HIGH_ONLY: "高値のみ上限超",
    E_WARNING_CANDLE: "警戒陰線",
    E_WARNING_EXTRA: "追加陰線(置換なし)",
    E_WARNING_LOW_BREAK: "warning_low割れ",
    E_GAP_THROUGH: "ギャップ割れ",
    E_REHIGH: "再高値更新",
    E_STOP_RAISED: "STOP引上げ",
    E_STOP_KEPT: "STOP据置",
    E_AMBIGUOUS_WARNING: "順序不明(警戒)",
    E_AMBIGUOUS_STOP: "順序不明(STOP)",
    E_DATA_END: "データ終端",
    X_INITIAL_STOP: "初期STOP到達",
    X_INITIAL_STOP_AFTER_BREAK: "初期STOP到達(突破後)",
    X_TRAIL_STOP: "trailSTOP到達",
}

# 畳む（noisy）イベント種別。件数が多く、順序の骨格を読めなくする。
_TIMELINE_FOLD = (E_WARNING_EXTRA, E_UPPER_HIGH_ONLY)

_EXIT_KINDS = (X_INITIAL_STOP, X_INITIAL_STOP_AFTER_BREAK, X_TRAIL_STOP)


def _timeline_row(ev: SMEvent, max_items: int = 24) -> str:
    parts: list[str] = []
    for te in ev.timeline:
        if te.kind in _TIMELINE_FOLD:
            continue
        label = _KIND_SHORT.get(te.kind, te.kind)
        chunk = f"D+{te.day_offset} {label}"
        if te.case != "ALL":
            chunk += f"[{te.case[:5]}]"
        text = f"<b>{_e(chunk)}</b>" if te.kind in _EXIT_KINDS else _e(chunk)
        parts.append(text)
        if len(parts) >= max_items:
            break
    return " → ".join(parts) if parts else "－"


def _timeline_section(events: list[SMEvent]) -> str:
    rows = ["<tr><th>銘柄</th><th>経路</th><th>状態遷移の順序</th></tr>"]
    for e in sorted(events, key=lambda x: (x.path_label, x.signal_date)):
        rows.append(
            f"<tr><td>{_e(e.code)} {_e(e.name[:8])}<br>"
            f"<span class='muted'>{e.signal_date}</span></td>"
            f"<td>{_e(PATH_LABELS_JA.get(e.path_label, e.path_label))}</td>"
            f"<td class='tl'>{_timeline_row(e)}</td></tr>"
        )
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


# --- §6 WARNING の詳細（参考）-------------------------------------------------


def _warning_table(events: list[SMEvent]) -> str:
    rows = [
        "<tr><th>銘柄</th><th>警戒足日</th><th>D+n</th><th>warning_low</th>"
        "<th>reference_high</th><th>含み益率</th><th>決着</th><th>決着まで</th>"
        "<th>安値割れ日</th><th>ギャップ割れ</th><th>再高値更新日</th>"
        "<th>新押し安値候補</th><th>trail候補</th><th>STOP引上</th>"
        "<th>追加陰線</th><th>割れ後の滞留日数</th></tr>"
    ]
    any_row = False
    for e in sorted(events, key=lambda x: (x.signal_date, x.code)):
        for w in e.warnings:
            any_row = True
            rows.append(
                f"<tr><td>{_e(e.code)} {_e(e.name[:8])}</td>"
                f"<td>{w.date}</td><td class='num'>D+{w.day_offset}</td>"
                f"<td class='num'>{w.low:.1f}</td>"
                f"<td class='num'>{w.reference_high:.1f}</td>"
                f"<td class='num {_cls(w.unrealized_pct_at_warning)}'>"
                f"{_pct(w.unrealized_pct_at_warning)}</td>"
                f"<td>{_e(_RESOLUTION_LABELS_JA.get(w.resolution, w.resolution))}</td>"
                f"<td class='num'>"
                f"{f'{w.days_to_resolve}日' if w.days_to_resolve is not None else '－'}</td>"
                f"<td class='num'>{w.low_break_date if w.low_break_date else '－'}</td>"
                f"<td class='num'>{_mark(w.gap_through_warning_low)}</td>"
                f"<td class='num'>{w.rehigh_date if w.rehigh_date else '－'}</td>"
                f"<td class='num'>"
                f"{f'{w.new_swing_low_candidate:.1f}' if w.new_swing_low_candidate else '－'}</td>"
                f"<td class='num'>"
                f"{f'{w.trail_stop_candidate:.1f}' if w.trail_stop_candidate else '－'}</td>"
                f"<td class='num'>{_mark(w.stop_raised)}</td>"
                f"<td class='num'>{w.extra_bearish_count}</td>"
                f"<td class='num'>"
                f"{w.days_held_in_warning_after_low_break if w.days_held_in_warning_after_low_break is not None else '－'}"
                f"</td></tr>"
            )
    if not any_row:
        return "<p class='muted'>WARNING エピソードなし。</p>"
    return (
        "<div class='ref'><code>reference_high</code> は"
        "<b>警戒足発生までの保有中最高値</b>であり、警戒足自身の高値ではない。"
        "この解釈が今回の検証対象である（実装時に読み方を決めた箇所 §2 参照）。</div>"
        "<div class='scroll'><table>" + "".join(rows) + "</table></div>"
    )


# --- §7 STOP 引き上げ履歴 -----------------------------------------------------


def _stop_update_table(events: list[SMEvent]) -> str:
    rows = [
        "<tr><th>銘柄</th><th>確定日</th><th>旧STOP</th><th>新STOP</th>"
        "<th>押し安値</th><th>押し安値日</th><th>再高値更新日</th>"
        "<th>翌営業日から有効</th><th>初期STOP比</th></tr>"
    ]
    any_row = False
    for e in sorted(events, key=lambda x: (x.signal_date, x.code)):
        for su in e.stop_updates:
            any_row = True
            rows.append(
                f"<tr><td>{_e(e.code)} {_e(e.name[:8])}</td>"
                f"<td>{su.stop_update_date}</td>"
                f"<td class='num'>{su.old_stop:.1f}</td>"
                f"<td class='num'>{su.new_stop:.1f}</td>"
                f"<td class='num'>{su.new_swing_low_candidate:.1f}</td>"
                f"<td>{su.new_swing_low_date}</td>"
                f"<td>{su.rehigh_date}</td>"
                f"<td>{su.effective_from_date if su.effective_from_date else '－（追跡終端）'}</td>"
                f"<td class='num {_cls(su.raise_pct_from_initial_stop)}'>"
                f"{_pct(su.raise_pct_from_initial_stop)}</td></tr>"
            )
    if not any_row:
        return "<p class='muted'>STOP 引き上げ履歴なし。</p>"
    return (
        "<div class='ref'>引き上げた active_stop は<b>翌営業日から</b>有効であり、"
        "確定日（再高値更新日）自身の安値には遡って適用しない。"
        "これが look-ahead bias を作らないためのガードである"
        "（<code>exit_state_machine.py</code> モジュールdocstring内"
        "「このモジュールが守る境界」の3番目に対応）。</div>"
        "<div class='scroll'><table>" + "".join(rows) + "</table></div>"
    )


# --- §8 大陰線例外の参考指標 ---------------------------------------------------


def _bearish_extremes_section(events: list[SMEvent]) -> str:
    all_w = [(e, w) for e in events for w in e.warnings]
    if not all_w:
        return "<p class='muted'>警戒足なし。</p>"

    def top(key, reverse, label, fmt):
        pool = [(e, w) for e, w in all_w if key(w) is not None]
        pool.sort(key=lambda p: key(p[1]), reverse=reverse)
        rows = [
            "<tr><th>銘柄</th><th>警戒足日</th><th>値</th>"
            "<th>当日騰落率</th><th>実体/ATR14</th><th>出来高倍率</th>"
            "<th>終値の当日レンジ内位置</th></tr>"
        ]
        for e, w in pool[:5]:
            body_atr = f"{w.body_to_atr:.2f}" if w.body_to_atr is not None else "－"
            vol_ratio = f"{w.volume_ratio:.2f}倍" if w.volume_ratio is not None else "－"
            close_pos = (
                f"{w.close_pos_in_day_range:.2f}"
                if w.close_pos_in_day_range is not None else "－"
            )
            rows.append(
                f"<tr><td>{_e(e.code)} {_e(e.name[:8])}</td><td>{w.date}</td>"
                f"<td class='num'><b>{fmt(key(w))}</b></td>"
                f"<td class='num {_cls(w.change_pct)}'>{_pct(w.change_pct)}</td>"
                f"<td class='num'>{body_atr}</td>"
                f"<td class='num'>{vol_ratio}</td>"
                f"<td class='num'>{close_pos}</td></tr>"
            )
        return f"<h3>{label}</h3><div class='scroll'><table>{''.join(rows)}</table></div>"

    return (
        "<div class='ref'>大陰線・出来高急増・安値引けの例外は文章ルール上の言及のみで、"
        "<b>合成スコアも閾値も一切作っていない。</b>"
        f"保有中の警戒足は全 {len(all_w)} 本で、全件が"
        " <code>manual_exit_review</code>（人間が後からチャート確認する）対象である。"
        "以下は各参考指標を個別に見たときの極値。</div>"
        + top(lambda w: w.body_to_atr, True, "実体幅 / ATR14 が大きい順", lambda v: f"{v:.2f}")
        + top(lambda w: w.volume_ratio, True, "出来高倍率（25日平均比）が大きい順", lambda v: f"{v:.2f}倍")
        + top(lambda w: w.close_pos_in_day_range, False, "終値が当日安値に近い順（安値引け）",
              lambda v: f"{v:.2f}")
        + top(lambda w: w.change_pct, False, "当日騰落率が大きい順（下落）", lambda v: f"{v:+.2f}%")
    )


# --- §9 既存 fractal との比較 --------------------------------------------------


def _fractal_pairs(events: list[SMEvent]) -> list[tuple[SMEvent, "object", int]]:
    out = []
    for e in events:
        for w in e.warnings:
            if w.fractal_confirm_day_offset is not None and w.rehigh_day_offset is not None:
                lag = w.fractal_confirm_day_offset - w.rehigh_day_offset
                out.append((e, w, lag))
    return out


def _fractal_section(events: list[SMEvent]) -> str:
    pairs = _fractal_pairs(events)
    if not pairs:
        return (
            "<p class='muted'>既存 fractal 側でも押し安値として確定した組が"
            "0件だったため、比較できるデータがない。</p>"
        )
    earlier = sum(1 for _, _, lag in pairs if lag > 0)   # fractal の方が遅い = SM が早い
    same = sum(1 for _, _, lag in pairs if lag == 0)
    later = sum(1 for _, _, lag in pairs if lag < 0)      # fractal の方が早い
    med = _median([float(lag) for _, _, lag in pairs])
    rows = [
        "<tr><th>銘柄</th><th>警戒足日</th><th>状態機械の再高値更新日<br>(D+n)</th>"
        "<th>fractal確定日<br>(D+n)</th><th>差（fractal−状態機械）</th><th>判定</th></tr>"
    ]
    for e, w, lag in sorted(pairs, key=lambda p: (p[0].signal_date, p[0].code)):
        verdict = "状態機械が早い" if lag > 0 else ("同日" if lag == 0 else "fractalが早い")
        rows.append(
            f"<tr><td>{_e(e.code)} {_e(e.name[:8])}</td><td>{w.date}</td>"
            f"<td class='num'>D+{w.rehigh_day_offset}</td>"
            f"<td class='num'>D+{w.fractal_confirm_day_offset}</td>"
            f"<td class='num'>{lag:+.0f}日</td><td>{verdict}</td></tr>"
        )
    return (
        "<div class='ref'>fractal（既存 swing 検出, <code>pivot_window=2</code>）は"
        "状態機械の押し安値確定には<b>一切使っていない</b>。比較専用の後段パス"
        "（<code>attach_fractal_comparison</code>）で、確定済みの"
        "<code>new_swing_low_candidate</code> を fractal がいつ押し安値として"
        "認めるかを事後的に調べているだけである。</div>"
        f"<p>両方確定した組は {len(pairs)} 件。"
        f"状態機械の方が早い（差&gt;0） {earlier} 件 / 同日 {same} 件 / "
        f"fractal の方が早い（差&lt;0） {later} 件。差の中央値は "
        f"{f'{med:+.1f}日' if med is not None else '－'}"
        "（正なら fractal の方が遅い＝状態機械の方が早い）。</p>"
        "<div class='scroll'><table>" + "".join(rows) + "</table></div>"
    )


# --- §10 代表チャート ---------------------------------------------------------

_CATEGORY_LABEL_FALLBACK: dict[str, str] = {**PATH_LABELS_JA, **FLAG_LABELS_JA}


def _category_label(key: str) -> str:
    return _CATEGORY_LABEL_FALLBACK.get(key, key)


def _charts_section(chart_map: dict) -> str:
    if not chart_map:
        return "<p class='muted'>代表チャートなし（chart_map が空）。</p>"
    parts: list[str] = []
    for key, items in chart_map.items():
        parts.append(f"<h3>{_e(_category_label(key))}</h3>")
        if not items:
            parts.append("<p class='muted'>該当なし。</p>")
            continue
        for ev, path in items:
            rets = " / ".join(
                f"{c[:5]}: {_pct(ev.cases[c].approximate_return_pct, 1)}"
                for c in CASES
            )
            parts.append(
                f"<div class='chart'><img src='representative_charts/{_e(path.name)}' "
                f"alt='{_e(ev.code)}'>"
                f"<div class='cap'><b>{_e(ev.code)} {_e(ev.name)}</b> "
                f"シグナル {ev.signal_date} / "
                f"{_e(PATH_LABELS_JA.get(ev.path_label, ev.path_label))}<br>{rets}</div></div>"
            )
    return "".join(parts)


# --- §11 §20 の 10 の問いへの回答 ---------------------------------------------


def _answers_section(events: list[SMEvent], summary: list[SummaryRow]) -> str:
    """事実と件数だけを返す。データが決めていない問いはそう明記する。"""
    entered = [e for e in events if e.entry_available]
    n, ne = len(events), len(entered)
    broke = [e for e in entered if e.reached_trend_hold]
    warned = [e for e in broke if e.warnings]
    total_warnings = sum(len(e.warnings) for e in entered)

    resolutions: dict[str, int] = {}
    for e in entered:
        for w in e.warnings:
            resolutions[w.resolution] = resolutions.get(w.resolution, 0) + 1
    total_resolved = sum(resolutions.values())

    all_w = [(e, w) for e in entered for w in e.warnings]
    same_high = sum(
        1 for _, w in all_w if abs(w.warning_high_vs_reference_high_pct) < 1e-9
    )
    below_high = sum(1 for _, w in all_w if w.warning_high_vs_reference_high_pct < 0)

    pairs = _fractal_pairs(entered)
    sm_earlier = sum(1 for _, _, lag in pairs if lag > 0)
    fractal_earlier = sum(1 for _, _, lag in pairs if lag < 0)

    rehigh_events = [e for e in entered if e.rehigh_count >= 1]
    raised1 = [e for e in entered if e.stop_raise_count >= 1]

    c1 = [e.cases[CASE1] for e in entered]
    c2 = [e.cases[CASE2] for e in entered]
    c3 = [e.cases[CASE3] for e in entered]
    c1_rets = [r.approximate_return_pct for r in c1 if r.approximate_return_pct is not None]
    c2_rets = [r.approximate_return_pct for r in c2 if r.approximate_return_pct is not None]
    c3_rets = [r.approximate_return_pct for r in c3 if r.approximate_return_pct is not None]
    c1_give = [r.giveback_pct for r in c1 if r.giveback_pct is not None]
    c3_give = [r.giveback_pct for r in c3 if r.giveback_pct is not None]

    c2_lt_c3 = sum(
        1 for e in entered
        if e.cases[CASE2].approximate_return_pct is not None
        and e.cases[CASE3].approximate_return_pct is not None
        and e.cases[CASE2].approximate_return_pct < e.cases[CASE3].approximate_return_pct
    )
    c2_paired = sum(
        1 for e in entered
        if e.cases[CASE2].approximate_return_pct is not None
        and e.cases[CASE3].approximate_return_pct is not None
    )

    stuck = [e for e in entered if "STUCK_IN_WARNING" in e.flags]
    amb_warn = sum(1 for e in entered if e.ambiguous_warning_days)
    amb_stop = sum(1 for e in entered if e.ambiguous_stop_days)
    multi_rehigh = sum(1 for e in entered if e.path_label == "P4_REHIGH_MULTI")
    c3_better_c1 = sum(1 for e in entered if "CASE3_BETTER_THAN_CASE1" in e.flags)
    c2_better_c3 = sum(1 for e in entered if "CASE2_BETTER_THAN_CASE3" in e.flags)
    same_day_bearish_total = sum(e.same_day_bearish_at_trend_entry for e in entered)
    after_break_stop = sum(
        1 for e in entered for c in CASES
        if e.cases[c].exit_type == X_INITIAL_STOP_AFTER_BREAK
    )

    row_warned = _find_row(summary, "上限突破後にWARNINGが発生した件数")
    row_trail = _find_row(summary, "trail stop を1回以上引き上げられた件数")

    # Q2: 「調整後の再高値更新」がそもそも何回成立したか（決着の内訳）
    res_low = sum(1 for _, w in all_w if w.resolution == "low_break")
    res_rehigh = sum(1 for _, w in all_w if w.resolution == "rehigh")
    res_amb = sum(1 for _, w in all_w if w.resolution == "ambiguous_both")
    # 警戒足自身が保有中最高値で、かつ二度と高値を更新できなかった本数
    peak_warn = [w for _, w in all_w if abs(w.warning_high_vs_reference_high_pct) < 1e-9]
    peak_never_rehigh = sum(1 for w in peak_warn if w.rehigh_date is None)

    # Q3: fractal がそもそも同じ安値を押し安値と認めたか
    confirmed_lows = [w for _, w in all_w if w.new_swing_low_date is not None]
    fractal_missed = sum(1 for w in confirmed_lows if not w.fractal_is_same_low)

    # Q5: 「早く降りすぎか」は CASE 間の勝敗ではなく、
    #     最大含み益のうちどれだけ取れたかで見ないと答えにならない。
    def _capture(case_key: str, min_gain: float) -> list[tuple]:
        out = []
        for e in entered:
            base = e.cases[CASE1]
            r = e.cases[case_key]
            if base.max_gain_pct is None or base.max_gain_pct < min_gain:
                continue
            if r.approximate_return_pct is None:
                continue
            out.append((e, base.max_gain_pct, r.approximate_return_pct))
        return out

    big10_c2 = _capture(CASE2, 10.0)
    big10_c3 = _capture(CASE3, 10.0)
    big5_c2 = _capture(CASE2, 5.0)

    def _cap_ratio(rows) -> float | None:
        vals = [ret / mg * 100.0 for _, mg, ret in rows if mg > 0]
        return _median(vals) if vals else None

    def med(xs):
        return _median(xs)

    qs = [
        (
            "1. 「元レンジ上限終値突破後から警戒陰線を有効化する」という解釈は、"
            "ENTRY直後から全陰線を見る場合より選別として機能するか",
            f"仮想ENTRY成立 {ne} 件のうち TREND_HOLD へ到達（＝元レンジ上限を終値突破）"
            f"したのは <b>{_rate(len(broke), ne)}</b>。うち実際に警戒足が発生したのは "
            f"{_rate(len(warned), len(broke))}、警戒足の総数は {total_warnings} 本。"
            + (f" {_e(row_warned.note)}。" if row_warned else "")
            + "ENTRY直後から全陰線を拾った場合の本数はこのレポート単体では持たないため、"
            "厳密な倍率比較はできない。上記の note にある前回件数との対比が唯一の手がかりであり、"
            f"<b>母数 {ne} 件でこれだけをもって選別性能を断定することはできない。</b>"
        ),
        (
            "2. <code>reference_high = 警戒足発生までの保有中最高値</code> とすることで、"
            "「調整後の再高値更新」を自然に判定できるか",
            (
                f"警戒足 {len(all_w)} 本の決着の内訳は、"
                f"<b>warning_low を先に割ったのが {_rate(res_low, len(all_w))}</b>、"
                f"reference_high を先に更新できたのが {_rate(res_rehigh, len(all_w))}、"
                f"同日に両方成立して順序不明が {_rate(res_amb, len(all_w))}。"
                "つまり「調整 → 再高値更新」という経路自体が"
                "<b>ほとんど発生していない。</b>"
                " 原因は reference_high の取り方にある。"
                f"警戒足のうち {same_high} 本は<b>その足自身が保有中最高値</b>"
                f"（残り {below_high} 本は保有中の別の日の方が高い）で、"
                "この場合 reference_high は直近の天井そのものになるため、"
                "再高値更新には「その日のうちに天井を抜く」ことが要求される。"
                f"実際、その {same_high} 本のうち {peak_never_rehigh} 本は"
                "最後まで高値を更新できず、トレーリングが再武装しないまま終わっている。"
                " 定義としては素直に動くが、<b>天井の日に警戒足が出ると"
                "以後まったく機能しなくなる</b>という偏りがある。"
                if all_w else "警戒足自体が0本のため判定材料がない。"
            ),
        ),
        (
            "3. <code>warning → reference_high再突破 → 期間中最安値を押し安値確定</code> "
            "という方法で、既存fractalより早く、かつlook-aheadなしでトレーリング候補を"
            "作れるか",
            (
                f"状態機械が確定した押し安値 {len(confirmed_lows)} 件のうち、"
                f"<b>既存 fractal がそもそも押し安値と認めないものが {fractal_missed} 件</b>"
                "（pivot 条件を満たさない）。両方が確定した "
                f"{len(pairs)} 件で確定日を比べると、状態機械の方が早いのは {sm_earlier} 件、"
                f"fractal の方が早いのは {fractal_earlier} 件で、"
                "<b>「fractal より早い」とは言えない。</b>"
                "違いは速さではなく、fractal が拾わない押し安値も候補にできる点にある。"
                " look-ahead が無いことは prefix 不変性テスト（打ち切り長を変えても"
                "先頭の結果が一致すること）で別途固定している。"
                if pairs else
                "既存 fractal 側でも押し安値が確定した組が0件のため、"
                "早さの比較ができない。"
            ),
        ),
        (
            "4. 前回の「trail成立2/32」という問題がどの程度改善するか",
            f"trail（STOP引き上げ）が1回以上成立したのは <b>{_rate(len(raised1), ne)}</b>。"
            + (f" {_e(row_trail.note)}。" if row_trail else "")
            + "この数字が前回の 2/32・10/32 と比べて改善したかどうかは、"
            "同じ32件の母集団かどうかに依存するため、母集団が異なる場合は"
            "単純な比較として読まないこと。",
        ),
        (
            "5. WARNING_EXITは利益を守る一方で、強い上昇を早く降りすぎる傾向があるか",
            (
                "<b>両方ある。</b> 全体では CASE2 のリターン中央値 "
                f"{_pct(med(c2_rets)) if c2_rets else '－'} が CASE3 の "
                f"{_pct(med(c3_rets)) if c3_rets else '－'} を上回り"
                f"（CASE2 が CASE3 を上回ったのは {c2_better_c3} 件、逆は {c2_lt_c3} 件）、"
                "往復を避ける方向には効いている。"
                " しかし「早く降りすぎか」は勝敗ではなく"
                "<b>最大含み益のうちどれだけ取れたか</b>で見る必要がある。"
                + (
                    f" 最大含み益が +10% 以上に達した {len(big10_c2)} 件では、"
                    f"CASE2 が取れたのは最大含み益の中央値 "
                    f"{_cap_ratio(big10_c2):.0f}%（CASE3 は {_cap_ratio(big10_c3):.0f}%）にすぎない。"
                    if big10_c2 and _cap_ratio(big10_c2) is not None
                    and _cap_ratio(big10_c3) is not None else ""
                )
                + (
                    f" +5% 以上に達した {len(big5_c2)} 件でも中央値 "
                    f"{_cap_ratio(big5_c2):.0f}% にとどまる。"
                    if big5_c2 and _cap_ratio(big5_c2) is not None else ""
                )
                + " 個別に見ると、CASE2 が大きく取り損ねた例と、"
                "逆に CASE2 が早降りしたせいで CASE1/CASE3 に大きく負けた例の両方がある"
                "（§3 の一覧と §10 の代表チャートを参照）。"
                "<b>強い上昇を伸ばす目的には、この降り方は合っていない。</b>"
            ),
        ),
        (
            "6. TRAILINGは初期STOPのみより利益の吐き出しを減らせるか",
            (
                "<b>ほとんど減らせていない。</b> CASE1（初期STOPのみ）の吐き出し幅中央値 "
                f"{f'{med(c1_give):.2f}pt' if c1_give else '－'} に対し CASE3（トレーリング）は "
                f"{f'{med(c3_give):.2f}pt' if c3_give else '－'}、"
                f"リターン中央値も {_pct(med(c1_rets)) if c1_rets else '－'} → "
                f"{_pct(med(c3_rets)) if c3_rets else '－'} とわずかな差にとどまる"
                f"（CASE3 が CASE1 を上回ったのは {c3_better_c1} 件）。"
                " 理由ははっきりしていて、"
                f"<b>そもそも trail が武装したのが {_rate(len(raised1), ne)} しかない。</b>"
                f" CASE3 の EXIT で最も多いのは "
                f"<code>{X_INITIAL_STOP_AFTER_BREAK}</code>"
                "（上限を突破したのに trail が一度も上がらず初期STOPまで戻った）で、"
                "この経路に入ると CASE1 と同じ結果になる。"
                " トレーリングの仕組みそのものより、"
                "<b>引き上げの起点である再高値更新が起きないこと</b>が効いている（Q2 参照）。"
            ),
        ),
        (
            "7. 状態機械が複雑すぎる、または不自然なケースはあるか",
            f"同日に warning_low割れと再高値更新の両方が成立し順序不明になったのは "
            f"{amb_warn} 件、同日にSTOP到達と再高値更新が重なったのは {amb_stop} 件。"
            f"warning_low を割ったのに解釈(b)によりWARNINGへ留まり続けたのは "
            f"{len(stuck)} 件（STUCK_IN_WARNING）。"
            f"再高値更新が2回以上のループになったのは {multi_rehigh} 件"
            f"（P4_REHIGH_MULTI）。"
            f"CASE3がCASE1を上回ったのは {c3_better_c1} 件、CASE2がCASE3を"
            f"上回ったのは {c2_better_c3} 件で、CASE間の優劣が固定的でないことが分かる。"
            "これらは複雑さ・不自然さの候補であり、§12（未確定のまま残った項目）で"
            "扱う。",
        ),
        (
            "8. この解釈を正式ルール候補として検討する価値があるか",
            "上記の材料（選別性・再高値判定・fractal比較・trail成立率・吐き出し幅）を"
            "並べる限り、状態機械としては動くことが確認できた。"
            "一方で §12（未確定のまま残った項目）に挙げる複数の定義（警戒足の絞り込み、"
            "同日陰線の扱い、CASE3の滞留、4つ目のEXIT種別、ギャップ時の約定前提、"
            "大陰線例外の数値化）は<b>まだ決まっていない</b>。"
            "<b>この検証は「採用すべき」という結論を出さない。</b>"
            "定義が固まっていない状態で正式ルール化の可否を判断するのは時期尚早である。",
        ),
        (
            "9. 正式化する前に人間がチャート確認すべき代表ケースはどれか",
            f"§8（大陰線例外の参考指標）で挙げた実体幅/ATR・出来高倍率・安値引けの"
            "上位ケース、STUCK_IN_WARNING に該当した "
            f"{len(stuck)} 件、順序不明フラグが付いた "
            f"{amb_warn + amb_stop} 件、再高値更新が複数回ループした "
            f"{multi_rehigh} 件が確認優先度の高い候補である。"
            "これらはいずれも代表チャート（§10）に含めることを想定した抽出基準。",
        ),
        (
            "10. 次に定義を詰めるなら、どの項目が最優先か",
            f"頻度で見ると、同日陰線（解釈a）の該当は {same_day_bearish_total} 件、"
            f"CASE3のWARNING滞留（解釈b）は {len(stuck)} 件、"
            f"4つ目のEXIT種別 INITIAL_STOP_EXIT_AFTER_BREAKOUT の発生は "
            f"{after_break_stop} 件（CASE1〜3合算）。"
            "件数が多い項目ほど結果への影響が大きいと推測できるが、"
            "<b>ここではどれを優先すべきかの推奨はしない。</b>"
            "件数の内訳は §12 にまとめてあるので、そちらを判断材料にすること。",
        ),
    ]
    return "".join(
        f"<div class='q'><h4>{q}</h4><p>{a}</p></div>" for q, a in qs
    )


# --- §12 未確定のまま残った項目 -----------------------------------------------


def _open_items_section(events: list[SMEvent]) -> str:
    entered = [e for e in events if e.entry_available]
    same_day_total = sum(e.same_day_bearish_at_trend_entry for e in entered)
    stuck = sum(1 for e in entered if "STUCK_IN_WARNING" in e.flags)
    after_break_stop = sum(
        1 for e in entered for c in CASES
        if e.cases[c].exit_type == X_INITIAL_STOP_AFTER_BREAK
    )
    gap_through = sum(
        1 for e in entered for w in e.warnings if w.gap_through_warning_low
    )
    all_w = [(e, w) for e in entered for w in e.warnings]
    return f"""
<ol>
<li><b>どの陰線を警戒足とみなすか。</b> 今回検証したのは
「元レンジ上限を終値突破した後の最初の陰線」という1つの読み方に過ぎない。
実体の大きさ・出来高などによる絞り込みは行っていない
（警戒足の総数 {len(all_w)} 本。§8 参照）。</li>
<li><b>同日陰線（突破日・再高値更新日そのものが陰線）を警戒足として即採用するか
（解釈a）。</b> 今回は「翌営業日から判定」を採用したが、
同日採用にした場合に増える件数は <code>same_day_bearish_at_trend_entry</code>
の合計 {same_day_total} 件として記録してある。定義次第で警戒足の総数が変わる。</li>
<li><b>CASE3（トレーリング）が warning_low を割った後に何をするか（解釈b）。</b>
今回は「WARNINGに留まり続ける」を採用したが、これにより実際に
{stuck} 件が warning_low を割ったあとも決着まで WARNING に滞留した
（STUCK_IN_WARNING）。これは CASE3 の構造的な弱点として残っている。</li>
<li><b>4つ目のEXIT種別（解釈c）。</b>
上限突破後・トレーリング成立前に active_stop（＝初期STOPのまま）へ到達した
<code>INITIAL_STOP_EXIT_AFTER_BREAKOUT</code> は、CASE1〜3合算で
{after_break_stop} 件発生している。これを独立したEXIT種別として扱うか、
初期STOP到達に統合するかは決めていない。</li>
<li><b>ギャップ時の約定前提。</b> warning_low・active_stop のどちらも、
寄りが水準を割っていれば約定価格は保証していない
（<code>gap_through_warning_low</code> = {gap_through} 件）。
スリッページをどう見込むかは未定義のまま。</li>
<li><b>大陰線例外の数値定義。</b> §8 で並べた実体幅/ATR14・出来高倍率・
安値引けは観察値のままで、<b>合成スコアも閾値も作っていない。</b>
すべて <code>manual_exit_review</code> 用の参考指標。</li>
</ol>
"""


# --- §13 出力ファイル ---------------------------------------------------------

OUTPUT_FILES = [
    ("events.csv", "1行1イベントの追跡結果。CASE別のexit_type・リターン・"
     "最大含み益・吐き出し幅・警戒足/STOP引き上げの件数を含む。"),
    ("state_timeline.csv", "状態遷移の全イベント（順序の生データ）。"
     "case列がCASE2のものはCASE2だけに効くイベント。"),
    ("warnings.csv", "上限突破後の警戒足（WarningEpisode）全件。"
     "大陰線例外の参考指標とfractal比較列を含む。"),
    ("stop_updates.csv", "active_stop の引き上げ履歴。"
     "STOPは上方向にしか動かず、翌営業日から有効。"),
    ("daily_state.csv", "各営業日の寄り時点で有効だった active_stop 等の状態（§11）。"
     "STOP引き上げを当日安値へ遡って適用していないことをここで確認できる。"),
    ("case_comparison.csv", "CASE1/2/3別の仮想EXIT比較。"
     "最も成績のよいCASEを採用するという結論は出していない。"),
    ("summary.csv", "主要指標の集計値。"),
    ("representative_charts/", "代表チャート画像（カテゴリ別）。"),
]


def _output_files_table() -> str:
    rows = ["<tr><th>ファイル</th><th>内容</th></tr>"]
    for name, desc in OUTPUT_FILES:
        rows.append(f"<tr><td><code>{_e(name)}</code></td><td>{_e(desc)}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


# --- 本体 --------------------------------------------------------------------


def write_report(
    events: list[SMEvent],
    summary: list[SummaryRow],
    chart_map: dict,
    out_dir: Path,
    *,
    period: tuple[str, str],
    threshold: float = 0.65,
) -> Path:
    entered = [e for e in events if e.entry_available]
    body = f"""
<h1>EXIT ロジックの状態機械検証</h1>
<div class="sub">
検証期間 {_e(period[0])} 〜 {_e(period[1])} ／
対象 <code>near.max_position_in_range = {threshold}</code> で発生した ENTRY_CANDIDATE
{len(events)} 件（仮想ENTRY成立 {len(entered)} 件）／
生成日 {date.today().isoformat()}
</div>
{DISCLAIMER}

<h2>1. 主要指標</h2>
{_summary_table(summary)}

<h2>2. 状態遷移の定義</h2>
{_state_machine_section()}

<h2>3. CASE 比較</h2>
{_case_section(events, entered)}

<h2>4. イベント一覧</h2>
<p class="muted">「最大STOP初期比」は <code>max_active_stop</code> が
<code>initial_stop</code> から何%引き上げられたか。フラグの意味は
<code>FLAG_LABELS_JA</code> 参照。</p>
{_event_table(events)}

<h2>5. 状態遷移の時系列</h2>
<p class="muted">噪音の多い「追加陰線（置換なし）」「高値のみ上限超」は畳んである
（全量は <code>state_timeline.csv</code>）。太字が EXIT イベント。</p>
{_timeline_section(events)}

<h2>6. WARNING の詳細（参考）</h2>
{_warning_table(events)}

<h2>7. STOP 引き上げ履歴</h2>
{_stop_update_table(events)}

<h2>8. 大陰線例外の参考指標</h2>
{_bearish_extremes_section(events)}

<h2>9. 既存 fractal との比較</h2>
{_fractal_section(events)}

<h2>10. 代表チャート</h2>
{_charts_section(chart_map)}

<h2>11. §20 の 10 の問いへの回答</h2>
{_answers_section(events, summary)}

<h2>12. 未確定のまま残った項目</h2>
<div class="ref">以下は<b>この検証では決めなかった</b>項目である。
数値を置けば動くが、32件への当てはめで決めると過剰最適化になるため、
観察結果を材料として提示するに留める。</div>
{_open_items_section(events)}

<h2>13. 出力ファイル</h2>
{_output_files_table()}
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>EXIT 状態機械検証</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
