"""warning_low 割れ後の扱いの比較（warning_break_study.py）のレポートHTML。

姉妹モジュール `exit_sm_report.py` と同じ規約（CSS・`.warn`・`.ref`・
`.scroll`・`.q`）に合わせる。外部CDN不使用の自己完結HTML。

**結論を書かない。** 「どの案が一番儲かるか」ではなく
「『警戒陰線安値を割った』という事実を、日足短期スイングとして
どの程度重く扱うのが戦略意図に自然か」を材料として提示するだけで、
成績のよい案を採用するという結論は出さない。
"""

from __future__ import annotations

import collections
import html
from datetime import date
from pathlib import Path

from swing_screener.research import exit_state_machine as sm
from swing_screener.research import warning_break_study as wb
from swing_screener.research.exit_sm_report import CSS, _cls, _day, _pct
from swing_screener.research.exit_study import _median, _rate

DISCLAIMER = """
<div class="warn">
<h2>このレポートの読み方（先に必ず読むこと）</h2>
<ul>
<li><b>今回変えたのは <code>warning_low</code> を割ったあとの処理だけ。</b>
WARNING へ入る条件（研究上の基準として VARIANT A に固定）、
<code>reference_high</code> の定義、押し安値の取り方、トレーリング、初期STOP、
ENTRY ロジック、<code>near.max_position_in_range = 0.65</code> は
<b>4 案とも完全に同一で、今回いっさい触っていない。</b></li>
<li><b>VARIANT A を固定基準に使ったのは原因を 1 つに絞るためであって、
A を正式採用したという意味ではない。</b>
前回 <code>warning_start_study</code> の結論は保留のままである。</li>
<li><b>LOW / CLOSE / STRUCTURAL はいずれも現行の文章ルールの読み方であって、
正式ルールではない。</b>「最も仮想利益が高い案を採用する」という使い方はしない。</li>
<li><b>主分析の仮想EXITは 4 案とも「トリガー翌営業日の始値」に統一している。</b>
日足・引け後判断という現在の運用では、日中に <code>warning_low</code> を割った
という事実も引けるまで確定しないため。<code>warning_low</code> に STOP 注文を
置いていた場合の参考価格は別列（<code>cases[CASE2]</code>）に分けてあり、
主分析とは混ぜていない。</li>
<li><b>新しい数値閾値を探索していない。</b> V3 が使うのは既存の
<code>warning_low</code> と <code>original_range_upper</code> だけで、%閾値を持たない。</li>
<li><b>母数 32 件（警戒足は 31 本、+10% まで伸びたのは 6 件）と小さい。</b>
率は参考程度に留めること。</li>
</ul>
</div>
"""

EXTRA_CSS = """
.vgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:11px;margin:14px 0 20px}
.vcard{border:1px solid var(--line);border-radius:7px;padding:12px 13px}
.vcard h4{margin:0 0 6px;font-size:13.5px}
.vcard .cond{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
color:#33506e;background:#eef2f7;border-radius:4px;padding:2px 6px;display:inline-block}
.vcard ul{margin:8px 0 0;padding-left:17px;font-size:12.3px}
.vh{border-top:4px solid #8a8f98}.v1{border-top:4px solid #d64545}
.v2{border-top:4px solid #2b6cb0}.v3{border-top:4px solid #2e8b74}
td.rh{background:#f3f4f5!important}td.r1{background:#fdf0f0!important}
td.r2{background:#eef4fb!important}td.r3{background:#ecf6f2!important}
.shape{display:inline-block;font-size:11.5px;border:1px solid var(--line);
border-radius:10px;padding:0 7px;background:#fafafa;white-space:nowrap}
"""

RULE_CLASS = {
    sm.BREAK_HOLD: "rh", sm.BREAK_LOW: "r1",
    sm.BREAK_CLOSE: "r2", sm.BREAK_STRUCT: "r3",
}


def _e(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _pt(v: float | None) -> str:
    return f"{v:+.2f}pt" if v is not None else "－"


def _d(v) -> str:
    return v.isoformat() if v is not None else "－"


# --- 1. 4 案の定義 -------------------------------------------------------------


def _definition_section() -> str:
    cards = [
        (sm.BREAK_HOLD, "vh", "参考基準（前回 CASE3）", [
            "<code>warning_low</code> を割っても降りない",
            "<code>reference_high</code> 再突破か <code>active_stop</code> 到達まで待つ",
            "前回の検証で <b>STUCK_IN_WARNING</b> が出たのはこの解釈",
        ]),
        (sm.BREAK_LOW, "v1", "EXIT VARIANT 1 — 最も厳しい読み方", [
            "警戒陰線の安値を<b>少しでも</b>下回れば上昇シナリオ悪化と読む",
            "トリガーは日中に成立する（その日の安値だけで決まる）",
            "STOP 注文を置いていた場合の参考価格も別に記録している",
        ]),
        (sm.BREAK_CLOSE, "v2", "EXIT VARIANT 2 — 下ヒゲを許容する読み方", [
            "日中の一時的な割れは許容し、<b>日足の終値</b>で割ったら悪化と読む",
            "引け後判断という現在の運用と時点が一致する",
            "仮想EXITは翌営業日始値",
        ]),
        (sm.BREAK_STRUCT, "v3", "EXIT VARIANT 3 — 構造で読む", [
            "警戒安値を終値で割り、<b>さらに元レンジ上限の内側へ戻った</b>場合のみ",
            "「短期調整」ではなく「上限突破そのものの失敗」を見る",
            "新しい%閾値は置かず、既存の 2 本の水準だけを使う",
        ]),
    ]
    html_cards = []
    for rule, cls, sub, points in cards:
        lis = "".join(f"<li>{p}</li>" for p in points)
        html_cards.append(
            f"<div class='vcard {cls}'><h4>{_e(sm.BREAK_RULE_SHORT_JA[rule])}</h4>"
            f"<div class='muted' style='font-size:12px;margin-bottom:6px'>{_e(sub)}</div>"
            f"<span class='cond'>{_e(sm.BREAK_RULE_CONDITION_JA[rule])}</span>"
            f"<ul>{lis}</ul></div>"
        )
    return (
        "<div class='vgrid'>" + "".join(html_cards) + "</div>"
        "<div class='ref'><b>3 つのトリガーは入れ子になっている。</b>"
        "<code>close &lt; warning_low</code> なら必ず <code>low &lt; warning_low</code> でもあるので、"
        "V2 の成立日は V1 の成立日と同じかそれより後、V3 はさらに後になる。"
        "つまり V1 ⊇ V2 ⊇ V3 で、「どこまで割れを許容するか」の一本の軸になっている。</div>"
    )


# --- 2. §8 割れの実態 ---------------------------------------------------------


def _reality_section(rows: list[wb.BreakReality]) -> str:
    body = "".join(
        f"<tr><td>{_e(r.metric)}</td>"
        f"<td class='num'><b>{_e(r.rate)}</b></td>"
        f"<td class='num muted'>{r.count} / {r.denominator}</td>"
        f"<td class='muted'>{_e(r.note)}</td></tr>"
        for r in rows
    )
    return (
        "<div class='scroll'><table><thead><tr>"
        "<th>指標</th><th class='num'>値</th><th class='num'>件数</th><th>注記</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
    )


# --- 3. 横並び比較 -------------------------------------------------------------


def _comparison_table(metrics: list[wb.MetricRow]) -> str:
    heads = "".join(
        f"<th class='num'>{_e(sm.BREAK_RULE_SHORT_JA[r])}</th>" for r in wb.RULES
    )
    out: list[str] = []
    section = None
    for m in metrics:
        if m.section != section:
            section = m.section
            out.append(
                f"<tr><td colspan='{2 + len(wb.RULES)}' "
                f"style='background:#f3f5f7;font-weight:600'>{_e(section)}</td></tr>"
            )
        cells = "".join(
            f"<td class='num {RULE_CLASS[r]}'>{_e(m.values.get(r, '－'))}</td>"
            for r in wb.RULES
        )
        out.append(
            f"<tr><td>{_e(m.metric)}</td>{cells}"
            f"<td class='muted'>{_e(m.note)}</td></tr>"
        )
    return (
        "<div class='scroll'><table><thead><tr><th>指標</th>" + heads +
        "<th>注記</th></tr></thead><tbody>" + "".join(out) + "</tbody></table></div>"
    )


# --- 4. §9 復活したケース ------------------------------------------------------


def _revival_section(rows: list[wb.RevivalCase]) -> str:
    if not rows:
        return "<p class='muted'>該当なし。</p>"
    body = "".join(
        f"<tr><td>{_e(c.code)} {_e(c.name)}</td><td>{_d(c.signal_date)}</td>"
        f"<td>{_e(sm.BREAK_RULE_SHORT_JA[c.rule])}</td>"
        f"<td>{_d(c.intraday_break_date)}</td>"
        f"<td class='num'>{'終値回復' if c.close_recovered else '終値も割れ'}</td>"
        f"<td class='num'>{'●' if c.never_closed_below else '○'}</td>"
        f"<td class='num {_cls(c.v1_return_pct)}'>{_pct(c.v1_return_pct)}</td>"
        f"<td class='num {_cls(c.rule_return_pct)}'>{_pct(c.rule_return_pct)}</td>"
        f"<td class='num {_cls(c.diff_pt)}'>{_pt(c.diff_pt)}</td>"
        f"<td class='num'>{_pct(c.max_gain_after_break_pct)}</td>"
        f"<td class='num'>{'●' if c.reached_new_high else '○'}</td>"
        f"<td class='num'>{'●' if c.stop_raised_after_break else '○'}</td></tr>"
        for c in rows
    )
    return (
        "<div class='scroll'><table><thead><tr>"
        "<th>銘柄</th><th>シグナル</th><th>案</th><th>日中割れ</th>"
        "<th class='num'>その日の終値</th><th class='num'>終値では最後まで割らず</th>"
        "<th class='num'>V1</th><th class='num'>この案</th><th class='num'>差</th>"
        "<th class='num'>割れ後の最大含み益</th><th class='num'>新高値</th>"
        "<th class='num'>trail</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
    )


# --- 5. §10 待ちすぎたケース ---------------------------------------------------


def _waited_section(rows: list[wb.WaitedTooLongCase]) -> str:
    if not rows:
        return "<p class='muted'>該当なし。</p>"
    body = "".join(
        f"<tr><td>{_e(c.code)} {_e(c.name)}</td><td>{_d(c.signal_date)}</td>"
        f"<td>{_e(sm.BREAK_RULE_SHORT_JA[c.rule])}</td>"
        f"<td>{_d(c.v1_exit_date)}</td><td>{_d(c.rule_exit_date)}</td>"
        f"<td class='num'>{c.days_waited if c.days_waited is not None else '－'}</td>"
        f"<td class='num {_cls(c.v1_return_pct)}'>{_pct(c.v1_return_pct)}</td>"
        f"<td class='num {_cls(c.rule_return_pct)}'>{_pct(c.rule_return_pct)}</td>"
        f"<td class='num bad'>{_pt(c.diff_pt)}</td>"
        f"<td class='num'>{_pct(c.rule_max_gain_pct)}</td>"
        f"<td class='num'>{'●' if c.back_inside_range else '○'}</td>"
        f"<td class='num'>{'●' if c.hit_initial_stop else '○'}</td>"
        f"<td class='num {_cls(c.gap_pct_at_exit)}'>{_pct(c.gap_pct_at_exit)}</td></tr>"
        for c in rows
    )
    return (
        "<div class='scroll'><table><thead><tr>"
        "<th>銘柄</th><th>シグナル</th><th>案</th><th>V1のEXIT</th><th>この案のEXIT</th>"
        "<th class='num'>待った日数</th><th class='num'>V1</th><th class='num'>この案</th>"
        "<th class='num'>差</th><th class='num'>最大含み益</th>"
        "<th class='num'>元レンジ内へ</th><th class='num'>初期STOP</th>"
        "<th class='num'>EXIT時ギャップ</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
    )


# --- 6. §15 分類 --------------------------------------------------------------


def _naturalness_section(rows: list[wb.NaturalCase]) -> str:
    cat = collections.Counter(r.category for r in rows)
    shape = collections.Counter(r.shape for r in rows)
    cross = collections.Counter((r.shape, r.category) for r in rows)

    cat_tbl = "".join(
        f"<tr><td>{_e(wb.NATURAL_LABELS_JA[k])}</td>"
        f"<td class='num'><b>{cat.get(k, 0)}</b></td>"
        f"<td class='num muted'>{_rate(cat.get(k, 0), len(rows))}</td></tr>"
        for k in wb.NATURAL_ORDER
    )
    shape_tbl = "".join(
        f"<tr><td>{_e(wb.SHAPE_LABELS_JA[k])}</td>"
        f"<td class='num'><b>{shape.get(k, 0)}</b></td>"
        f"<td class='num muted'>{_rate(shape.get(k, 0), len(rows))}</td></tr>"
        for k in wb.SHAPE_ORDER
    )
    cross_rows = "".join(
        f"<tr><td><span class='shape'>{_e(wb.SHAPE_LABELS_JA[s])}</span></td>"
        + "".join(
            f"<td class='num'>{cross.get((s, c), 0) or ''}</td>"
            for c in wb.NATURAL_ORDER
        )
        + "</tr>"
        for s in wb.SHAPE_ORDER
    )
    cross_head = "".join(
        f"<th class='num'>{_e(wb.NATURAL_LABELS_JA[c].split('（')[0])}</th>"
        for c in wb.NATURAL_ORDER
    )

    diff = [r for r in rows if (r.spread_pt or 0) >= 0.01]
    detail = "".join(
        f"<tr><td>{_e(r.code)} {_e(r.name)}</td><td>{_d(r.signal_date)}</td>"
        f"<td><span class='shape'>{_e(wb.SHAPE_LABELS_JA[r.shape])}</span></td>"
        f"<td>{_d(r.intraday_break_date)}</td><td>{_d(r.close_break_date)}</td>"
        f"<td>{_d(r.struct_break_date)}</td>"
        f"<td class='num rh {_cls(r.hold_return_pct)}'>{_pct(r.hold_return_pct)}</td>"
        f"<td class='num r1 {_cls(r.v1_return_pct)}'>{_pct(r.v1_return_pct)}</td>"
        f"<td class='num r2 {_cls(r.v2_return_pct)}'>{_pct(r.v2_return_pct)}</td>"
        f"<td class='num r3 {_cls(r.v3_return_pct)}'>{_pct(r.v3_return_pct)}</td>"
        f"<td class='num'>{_pt(r.spread_pt)}</td>"
        f"<td class='num'>{_pct(r.max_gain_pct)}</td></tr>"
        for r in sorted(diff, key=lambda r: -(r.spread_pt or 0))
    )
    return (
        "<div class='ref'><b>「形」と「結果」は別々に付けている。</b>"
        "形はチャート上の事実（どこまで割れたか）だけで決め、リターンを一切見ない。"
        "結果はどの案がいちばん良かったかで、形とは独立に決める。"
        "片方だけで判断すると誤解する（日中割れがノイズだった形でも、"
        "その後 trail STOP で吐き出せば V1 の方が結果は良くなる）。"
        "形は「最初に <code>warning_low</code> を割った警戒足」で判定している。</div>"
        "<div class='scroll'><table><thead><tr><th>結果の区分（§15）</th>"
        "<th class='num'>件数</th><th class='num'>割合</th></tr></thead><tbody>"
        + cat_tbl + "</tbody></table></div>"
        "<div class='scroll'><table><thead><tr><th>チャート上の割れ方</th>"
        "<th class='num'>件数</th><th class='num'>割合</th></tr></thead><tbody>"
        + shape_tbl + "</tbody></table></div>"
        "<h3>形 × 結果</h3>"
        "<div class='scroll'><table><thead><tr><th>割れ方</th>" + cross_head
        + "</tr></thead><tbody>" + cross_rows + "</tbody></table></div>"
        "<h3>案によって結果が変わったイベント</h3>"
        "<p class='muted'>ここに出ていないイベントでは、3 案とも同じ日・同じ価格で降りている。</p>"
        "<div class='scroll'><table><thead><tr>"
        "<th>銘柄</th><th>シグナル</th><th>割れ方</th><th>日中割れ</th><th>終値割れ</th>"
        "<th>上限割れ</th><th class='num'>参考</th><th class='num'>V1</th>"
        "<th class='num'>V2</th><th class='num'>V3</th><th class='num'>最大−最小</th>"
        "<th class='num'>最大含み益</th>"
        "</tr></thead><tbody>" + detail + "</tbody></table></div>"
    )


# --- 7. イベント別の並び -------------------------------------------------------


def _event_matrix(runs: dict[str, wb.RuleRun]) -> str:
    hold = runs[sm.BREAK_HOLD]
    ref = wb.reference_max_gain(runs)
    rows: list[str] = []
    for ev in sorted(hold.events, key=lambda e: (e.signal_date, e.code)):
        k = wb.key_of(ev)
        cells = ""
        for rule in wb.RULES:
            e = runs[rule].by_key.get(k)
            if e is None:
                cells += f"<td class='num {RULE_CLASS[rule]}'>－</td>" * 2
                continue
            r = e.path_result
            cells += (
                f"<td class='num {RULE_CLASS[rule]} {_cls(r.approximate_return_pct)}'>"
                f"{_pct(r.approximate_return_pct)}</td>"
                f"<td class='num {RULE_CLASS[rule]} muted' style='font-size:11.5px'>"
                f"{_e(r.exit_type.replace('_EXIT', '').replace('WARNING_LOW_', ''))}</td>"
            )
        b = next(
            (x for x in ev.warning_breaks if x.intraday_break_date is not None), None
        )
        rows.append(
            f"<tr><td>{_e(ev.code)} {_e(ev.name)}</td><td>{_d(ev.signal_date)}</td>"
            f"<td>{_e(ev.path_label.split('_')[0])}</td>"
            f"<td>{_d(ev.warnings[0].date) if ev.warnings else '－'}</td>"
            f"<td>{_d(b.intraday_break_date) if b else '－'}</td>"
            f"<td>{_d(b.close_break_date) if b else '－'}</td>"
            f"<td>{_d(b.struct_break_date) if b else '－'}</td>"
            f"<td class='num'>{_pct(ref.get(k))}</td>" + cells + "</tr>"
        )
    heads = "".join(
        f"<th class='num' colspan='2'>{_e(sm.BREAK_RULE_SHORT_JA[r])}</th>"
        for r in wb.RULES
    )
    return (
        "<div class='scroll'><table><thead><tr>"
        "<th>銘柄</th><th>シグナル</th><th>経路</th><th>警戒足</th>"
        "<th>日中割れ</th><th>終値割れ</th><th>上限割れ</th>"
        "<th class='num'>最大含み益</th>" + heads +
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


# --- 8. 代表チャート -----------------------------------------------------------


def _charts_section(chart_map: dict, out_dir: Path) -> str:
    from swing_screener.research.warning_break_charts import CATEGORIES

    out: list[str] = []
    for key, label in CATEGORIES:
        items = chart_map.get(key) or []
        out.append(f"<h3>{_e(label)}</h3>")
        if not items:
            out.append("<p class='muted'>該当なし。</p>")
            continue
        for evs, path in items:
            base = evs[sm.BREAK_HOLD]
            rel = path.relative_to(out_dir).as_posix()
            summary = " ／ ".join(
                f"{sm.BREAK_RULE_SHORT_JA[r]} {_pct(evs[r].path_result.approximate_return_pct)}"
                for r in wb.RULES if r in evs
            )
            out.append(
                f"<figure><img src='{_e(rel)}' alt='{_e(base.code)}'>"
                f"<figcaption>{_e(base.code)} {_e(base.name)} "
                f"シグナル {_d(base.signal_date)} — {_e(summary)}</figcaption></figure>"
            )
    return "".join(out)


# --- 9. §17 look-ahead --------------------------------------------------------

LOOKAHEAD_ROWS = [
    ("トリガーはその営業日までのデータだけで判定",
     "`track_event` は仮想ENTRY日から 1 営業日ずつ前へ進み、その日の足だけで "
     "`low < warning_low` / `close < warning_low` / `close < range_upper` を評価する。"
     "翌日以降の足はループの中で一度も参照しない。",
     "test_トリガーはその営業日の足だけで判定する"),
    ("close 確認型の EXIT は翌営業日からのみ実行可能",
     "CLOSE_BREAK / STRUCTURAL_BREAK は終値が確定して初めてトリガーするので、"
     "その日に売ることはできない。仮想EXITは翌営業日の始値に置いている。",
     "test_close型のEXITは翌営業日始値で約定する"),
    ("翌営業日始値を未来から事前利用しない",
     "約定価格はループを抜けたあとの `_resolve_next_open_fill` でだけ埋める。"
     "ループ内では `price=None` のトリガーしか作らないので、"
     "翌日の始値が判定に混ざる余地がない。",
     "test_翌営業日始値は判定に使われない"),
    ("EXIT 後の値動きは評価にのみ使用",
     "EXIT 後にどこまで伸びたかは、追跡が終わったあとに `daily` から集計する。"
     "状態機械は EXIT 時点で閉じており、その後の足で挙動が変わることはない。",
     "test_EXIT後の値動きは状態遷移に影響しない"),
    ("reference_high に未来値を使用しない",
     "`reference_high` は警戒足が出た日までの保有中最高値で確定し、以後書き換えない"
     "（今回この定義は変更していない）。",
     "test_reference_highは4案とも同じで未来を見ない"),
    ("trail stop 更新は既存の prefix invariant を維持",
     "押し安値は「警戒足の日から今日まで」の最小値で確定し、引き上げた STOP は"
     "翌営業日から有効になる。4 案ともこの扱いは同じ。",
     "test_prefix不変性_4案いずれでも成立する"),
]

LOOKAHEAD_EXTRA = """
<div class="ref">
<b>prefix 不変性テストを拡張した。</b> 系列を先頭から <code>k</code> 本で打ち切って
再実行し、フル実行の結果と一致するかを 4 案すべてで確認している。
トリガー（日付・オフセット）は打ち切っても変わらない。約定（翌営業日始値）だけは
「その営業日がまだ来ていない」場合に <code>fill_pending</code> となるので、
テストでは <b>トリガーの一致</b> と <b>約定は None か一致のどちらか</b> を要求している。
これは仕様どおりで、判定に未来を使っていないことの裏返しである。
<br><br>
<b>変異チェックも行った。</b> トリガー条件をわざと <code>bars[d+1]</code> で判定するよう
書き換えると look-ahead テストが失敗し、元に戻すと全件通ることを確認している。
<br><br>
<b>既存出力の回帰も確認済み。</b> <code>break_rule</code> の既定値は
<code>HOLD_UNTIL_STOP</code>（＝前回の CASE3 と同じ挙動）なので、
<code>research/exit_state_machine/</code> の CSV 7 本と
<code>research/warning_start_study/</code> の CSV 7 本は
<b>いずれもバイト単位で以前と同一</b>である。
</div>
"""


def _lookahead_section() -> str:
    body = "".join(
        f"<tr><td>{_e(a)}</td><td>{_e(b)}</td>"
        f"<td><code style='font-size:11.5px'>{_e(c)}</code></td></tr>"
        for a, b, c in LOOKAHEAD_ROWS
    )
    return (
        "<div class='scroll'><table><thead><tr><th>確認項目</th><th>実装上の担保</th>"
        "<th>テスト</th></tr></thead><tbody>" + body + "</tbody></table></div>"
        + LOOKAHEAD_EXTRA
    )


# --- 10. §19 の 12 問 ----------------------------------------------------------


def _mv(metrics: list[wb.MetricRow], metric: str, rule: str) -> str:
    for m in metrics:
        if m.metric == metric:
            return m.values.get(rule, "－")
    return "－"


def _rv(reality: list[wb.BreakReality], metric_prefix: str) -> str:
    for r in reality:
        if r.metric.startswith(metric_prefix):
            return f"{r.rate}（{r.count}/{r.denominator}）"
    return "－"


def _answers_section(
    runs: dict[str, wb.RuleRun],
    reality: list[wb.BreakReality],
    metrics: list[wb.MetricRow],
    revivals: list[wb.RevivalCase],
    waited: list[wb.WaitedTooLongCase],
    natural: list[wb.NaturalCase],
) -> str:
    cat = collections.Counter(r.category for r in natural)
    diff_n = len([r for r in natural if (r.spread_pt or 0) >= 0.01])
    hold = runs[sm.BREAK_HOLD]

    def exits(rule: str) -> int:
        return len([
            e for e in runs[rule].entered
            if e.path_result.exit_type in sm.BREAK_EXIT_TYPES
        ])

    qs: list[tuple[str, str]] = [
        (
            "1. warning_low intraday 割れのうち、終値で回復する割合",
            f"<b>初回の日中割れ当日に終値が戻したのは {_rv(reality, '日中に割ったが、その日の終値では回復')}。</b>"
            f"さらに、WARNING でいる間に終値では一度も割らなかった警戒足は "
            f"{_rv(reality, '日中に割ったが、WARNING でいる間')}。"
            "つまり日中割れの大半はその日のうちに終値でも割れており、"
            "「下ヒゲだけの割れ」は多数派ではない。",
        ),
        (
            "2. warning_low 終値割れのうち、元レンジ上限は維持している割合",
            f"<b>{_rv(reality, '終値で割ったが、元レンジ上限より上')}。</b>"
            "残りは同じ日か数日のうちに元レンジ上限の内側まで終値で戻っており、"
            "V2 と V3 が実際に分かれるのはこの少数のケースだけ。",
        ),
        (
            "3. LOW_BREAK / CLOSE_BREAK / STRUCTURAL_BREAK で EXIT 件数はどう変わるか",
            f"利確候補で降りた件数は <b>V1 {exits(sm.BREAK_LOW)} 件 → "
            f"V2 {exits(sm.BREAK_CLOSE)} 件 → V3 {exits(sm.BREAK_STRUCT)} 件</b>"
            f"（母数 {len(hold.entered)} 件）。減った分は trail STOP か初期STOP での撤退に置き換わる。"
            f"trail STOP で降りた件数は "
            f"参考 {_mv(metrics, 'trail STOPで降りた件数', sm.BREAK_HOLD)} / "
            f"V1 {_mv(metrics, 'trail STOPで降りた件数', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, 'trail STOPで降りた件数', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, 'trail STOPで降りた件数', sm.BREAK_STRUCT)}。"
            "<b>V1 はトレーリングが立つ前に降りてしまう。</b>",
        ),
        (
            "4. STUCK_IN_WARNING は各案でどう変化するか",
            f"件数は 参考 {_mv(metrics, 'STUCK_IN_WARNING のイベント件数', sm.BREAK_HOLD)} → "
            f"V1 {_mv(metrics, 'STUCK_IN_WARNING のイベント件数', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, 'STUCK_IN_WARNING のイベント件数', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, 'STUCK_IN_WARNING のイベント件数', sm.BREAK_STRUCT)}。"
            f"最大滞留日数は 参考 {_mv(metrics, 'WARNING 滞留日数の最大', sm.BREAK_HOLD)} に対し "
            f"V2 {_mv(metrics, 'WARNING 滞留日数の最大', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, 'WARNING 滞留日数の最大', sm.BREAK_STRUCT)}。"
            "<b>3 案とも滞留は解消する。</b>V1 は定義上ゼロ、V2/V3 に残るのは"
            "「終値の確認を待つ」ぶんの数日で、前回のような長期滞留ではない。"
            "「割ったのに何も起きず初期STOPまで戻った」件数も "
            f"参考 {_mv(metrics, '割ったまま何も起きず初期STOPまで戻った件数', sm.BREAK_HOLD)} → "
            f"3 案とも {_mv(metrics, '割ったまま何も起きず初期STOPまで戻った件数', sm.BREAK_LOW)} へ減る。",
        ),
        (
            "5. LOW_BREAK は強い上昇を早く降りすぎる傾向があるか",
            f"<b>ある。</b>EXIT 価格からさらに +3% 以上伸びたのは "
            f"V1 {_mv(metrics, 'EXIT価格からさらに +3% 以上伸びた件数', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, 'EXIT価格からさらに +3% 以上伸びた件数', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, 'EXIT価格からさらに +3% 以上伸びた件数', sm.BREAK_STRUCT)}、"
            f"EXIT 後の最大上昇率の中央値は "
            f"V1 {_mv(metrics, 'EXIT後の最大上昇率（中央値・EXIT価格比）', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, 'EXIT後の最大上昇率（中央値・EXIT価格比）', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, 'EXIT後の最大上昇率（中央値・EXIT価格比）', sm.BREAK_STRUCT)}。"
            "V1 がいちばん「まだ上がる場所」で降りている。"
            "<b>ただしそれが損につながっているとは限らない</b>のが今回の難しいところで、"
            "残った上昇分を trail STOP で取り切れずに吐き出すケースが多いため、"
            "結果だけ見ると V1 が勝つ件の方が多い（下の Q8 を参照）。",
        ),
        (
            "6. CLOSE_BREAK は日足・引け後判断という現在の運用と自然に整合するか",
            "<b>時点の整合は V2 が最も良い。</b>V2 のトリガーは終値で確定し、"
            "その日の引け後に判断して翌営業日の寄りで降りる、という手順にそのまま乗る。"
            "V1 は日中に成立するので、日足しか見ない運用では"
            "「割ったこと自体を引けまで知り得ない」。"
            f"実際 V1 のトリガーの {_rv(reality, '寄りが既に warning_low 以下')} は"
            "寄り付き時点で既に warning_low を下回っており、"
            "<code>warning_low</code> で売れたという前提は置けない。"
            "V1 を採るなら「事前に STOP 注文を置く」運用が前提になる。",
        ),
        (
            "7. STRUCTURAL_BREAK は利益を伸ばせる一方で撤退が遅すぎる傾向があるか",
            f"<b>今回の 32 件では「遅すぎる」ほどにはならなかった。</b>"
            f"V2 と V3 で結果が変わったのは 3 件だけで、V3 が良かったのが 2 件"
            f"（{cat.get('v3_natural', 0)} 件が V3 最良）、悪かったのが 1 件。"
            f"最大滞留日数も V3 {_mv(metrics, 'WARNING 滞留日数の最大', sm.BREAK_STRUCT)} で V2 と同じ。"
            "理由は §8 のとおりで、終値で warning_low を割った日にはたいてい"
            "元レンジ上限も同時に割っているため、V3 の追加条件がほとんど発動しないから。"
            "<b>「遅すぎない」のではなく「めったに効かない」</b>と読むのが正確。",
        ),
        (
            "8. +5% / +10% 以上伸びたケースで、各案がどの程度利益を吐き出すか",
            f"最大含み益 +5% 以上（13 件）で残せた割合の中央値は "
            f"参考 {_mv(metrics, '最大含み益+5%以上のケースで残せた割合（中央値）', sm.BREAK_HOLD)} / "
            f"V1 {_mv(metrics, '最大含み益+5%以上のケースで残せた割合（中央値）', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, '最大含み益+5%以上のケースで残せた割合（中央値）', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, '最大含み益+5%以上のケースで残せた割合（中央値）', sm.BREAK_STRUCT)}。"
            f"+10% 以上（6 件）では "
            f"V1 {_mv(metrics, '最大含み益+10%以上のケースで残せた割合（中央値）', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, '最大含み益+10%以上のケースで残せた割合（中央値）', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, '最大含み益+10%以上のケースで残せた割合（中央値）', sm.BREAK_STRUCT)}。"
            "<b>どの案でも 8 割以上を吐き出している。</b>"
            "warning_low 割れの扱いを変えても、この吐き出しは埋まらない。",
        ),
        (
            "9. ギャップを考慮しても実運用可能な EXIT 解釈はあるか",
            f"翌営業日始値がトリガー基準を下回ったのは "
            f"V1 {_mv(metrics, '翌営業日始値がトリガー基準より下だった件数', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, '翌営業日始値がトリガー基準より下だった件数', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, '翌営業日始値がトリガー基準より下だった件数', sm.BREAK_STRUCT)}、"
            f"ギャップ率の中央値は "
            f"V1 {_mv(metrics, 'ギャップ率の中央値', sm.BREAK_LOW)} / "
            f"V2 {_mv(metrics, 'ギャップ率の中央値', sm.BREAK_CLOSE)} / "
            f"V3 {_mv(metrics, 'ギャップ率の中央値', sm.BREAK_STRUCT)}。"
            "<b>V2/V3 の方がギャップ耐性は良い。</b>"
            "V1 の中央値がマイナスなのは、日中割れの日は引けにかけて崩れることが多く、"
            "翌日の寄りが warning_low よりさらに下になるため。"
            "V2/V3 は既に安いところでトリガーするので、翌日の寄りとの差は小さい。"
            "ただしどちらも最大 4% 前後の下方ギャップは起きており、"
            "<b>「warning_low 価格で必ず売れる」前提は置けない</b>。",
        ),
        (
            "10. 「warning_low 割れ → 利確候補」を、より明確な正式ルールにできそうか",
            "<b>文章としては書ける。</b>今回の 3 案はいずれも "
            "「日足の確定値だけで一意に判定でき、翌営業日の寄りで実行できる」形になっており、"
            "曖昧さは残らない。"
            f"ただし <b>32 件のうち結果が変わったのは {diff_n} 件だけ</b>で、"
            "残りは 3 案とも同じ日・同じ価格で降りている。"
            "<b>この母数では「どの表現が正しいか」を決める根拠にならない。</b>"
            "決めるとすれば、成績ではなく「日足・引け後判断という運用の時点に合うか」"
            "という基準になる（Q6）。",
        ),
        (
            "11. 次に reference_high の定義検証へ進むべきか",
            "<b>材料としては、そちらの方が効きそうに見える。</b>"
            "Q8 のとおり、+10% まで伸びた 6 件でどの案も 8 割以上を吐き出しており、"
            "この差は warning_low の扱いでは動かない。"
            f"trail STOP まで到達した件数も参考基準で "
            f"{_mv(metrics, 'trail STOPで降りた件数', sm.BREAK_HOLD)} にとどまる。"
            "前回の検証で見つかった「警戒足自身の高値が reference_high になり、"
            "再高値更新に天井の更新を要求してしまう」構造は今回も手つかずのまま残っている。"
            "<b>ただしこれは本レポートの観察であって、次の作業指示ではない。</b>",
        ),
        (
            "12. それとも warning_low 処理自体にまだ根本的な問題が残っているか",
            "<b>残っている問題は 2 つある。</b>"
            "(1) <b>V1 の約定前提。</b>日足運用では日中割れを引けまで知り得ないので、"
            "V1 を採るなら「STOP 注文を常時置く」という運用側の決めごとが必要になる。"
            "これはルールの文言ではなく執行方法の問題。"
            "(2) <b>同日に複数の条件が成立する日の順序。</b>"
            f"終値割れと reference_high 再突破が同じ日に起きた警戒足が "
            f"{_rv(reality, '終値割れと reference_high 再突破が同日')} あり、"
            "今回は「既存の REHIGH ロジックを変更しない」という制約から再高値更新を優先したが、"
            "これは選択であって文章ルールが決めていることではない。"
            "<b>どちらも今回は決めずに残してある。</b>",
        ),
    ]
    return "".join(
        f"<div class='q'><h3>{_e(q)}</h3><p>{a}</p></div>" for q, a in qs
    )


# --- 11. 未確定項目 / 出力ファイル ---------------------------------------------

OPEN_ITEMS = [
    ("どの EXIT VARIANT を正式ルールにするか",
     "32 件のうち結果が変わるのは 8 件だけで、成績で決めると過剰最適化になる。"
     "決め手にするなら「日足・引け後判断という運用の時点と合うか」であって、リターンではない。"),
    ("V1 を採る場合の執行方法",
     "日中割れは引けまで観測できないので、STOP 注文を常時置くかどうかを"
     "運用側で決める必要がある。本検証はどちらも仮定していない。"),
    ("終値割れと reference_high 再突破が同日の場合の優先順位",
     "今回は既存の REHIGH ロジックを変更しないという制約から再高値更新を優先した。"
     "文章ルールはこの順序を決めていない。"),
    ("reference_high の定義",
     "「警戒足発生時点までの保有中最高値」のまま変更していない。"
     "利益の吐き出しはここに残っている可能性が高いが、今回は同時に変えていない。"),
    ("押し安値・トレーリングの定義",
     "WARNING 期間中の最安値 × 0.995、上方向のみ。今回いっさい触っていない。"),
    ("WARNING へ入る条件",
     "研究上の基準として VARIANT A に固定した。A を正式採用したという意味ではない。"),
]

OUTPUT_FILES = [
    ("report.html", "このレポート"),
    ("variant_comparison.csv", "4 案の横並び比較（§11/§12/§13/§14）"),
    ("break_reality.csv", "§8 warning_low 割れの実態"),
    ("events.csv", "4 案 × 32 件のイベント（break_rule 列で区別）"),
    ("warning_breaks.csv", "警戒足ごとの割れ方の観測記録（日中 / 終値 / 上限）"),
    ("summary.csv", "案ごとの詳細集計"),
    ("revival_cases.csv", "§9 V1 なら降りるが V2/V3 は保有を続けた件"),
    ("waited_too_long_cases.csv", "§10 待った結果、悪化した件"),
    ("naturalness.csv", "§15 割れ方の形 × どの案が良かったか"),
    ("representative_charts/", "§16 の代表チャート"),
]


def _open_items_table() -> str:
    body = "".join(
        f"<tr><td>{_e(a)}</td><td class='muted'>{_e(b)}</td></tr>"
        for a, b in OPEN_ITEMS
    )
    return ("<div class='scroll'><table><thead><tr><th>項目</th><th>今回の扱い</th>"
            "</tr></thead><tbody>" + body + "</tbody></table></div>")


def _output_files_table() -> str:
    body = "".join(
        f"<tr><td><code>{_e(a)}</code></td><td class='muted'>{_e(b)}</td></tr>"
        for a, b in OUTPUT_FILES
    )
    return ("<div class='scroll'><table><thead><tr><th>ファイル</th><th>内容</th>"
            "</tr></thead><tbody>" + body + "</tbody></table></div>")


# --- 本体 ---------------------------------------------------------------------


def write_report(
    runs: dict[str, wb.RuleRun],
    reality: list[wb.BreakReality],
    metrics: list[wb.MetricRow],
    revivals: list[wb.RevivalCase],
    waited: list[wb.WaitedTooLongCase],
    natural: list[wb.NaturalCase],
    chart_map: dict,
    out_dir: Path,
    *,
    period: tuple[str, str],
    threshold: float = 0.65,
) -> Path:
    base = runs[sm.BREAK_HOLD]
    entered = base.entered
    body = f"""
<h1>warning_low を割ったあとの扱いの比較検証（LOW / CLOSE / STRUCTURAL）</h1>
<div class="sub">
検証期間 {_e(period[0])} 〜 {_e(period[1])} ／
対象 <code>near.max_position_in_range = {threshold}</code> で発生した ENTRY_CANDIDATE
{len(base.events)} 件（仮想ENTRY成立 {len(entered)} 件）× 4 案 ／
WARNING 開始条件は VARIANT A に固定 ／
生成日 {date.today().isoformat()}
</div>
{DISCLAIMER}

<h2>1. 比較する 4 案</h2>
{_definition_section()}

<h2>2. §8 warning_low 割れの実態</h2>
<div class="ref">分母は <b>降りない解釈（参考基準）の警戒足</b>。
V1/V2 は自分がそこで降りてしまうため、その後の終値割れ・上限割れを観測できない。
「どこで 3 案の違いが生まれるか」を数えるには、最も長く観測できる案を使う必要がある。</div>
{_reality_section(reality)}

<h2>3. 横並び比較（§11 STUCK / §12 利益保持 / §13 EXIT後の伸び / §14 ギャップ）</h2>
{_comparison_table(metrics)}

<h2>4. §9 「割った後に復活した」ケース</h2>
<div class="ref">V1 なら降りるが V2/V3 は保有を続けた件。
<b>日中割れがノイズだった可能性のあるケース</b>で、人間がチャートで見るための材料。
「割れ後の最大含み益」は日中割れの翌日以降に付けた値で、そこで降りられたという意味ではない。</div>
{_revival_section(revivals)}

<h2>5. §10 「待ちすぎた」ケース</h2>
<div class="ref">§9 と §10 は表裏である。<b>どちらか一方だけを見て解釈を決めない。</b></div>
{_waited_section(waited)}

<h2>6. §15 どの解釈が自然だったかの分類</h2>
{_naturalness_section(natural)}

<h2>7. イベント別の並び</h2>
<p class="muted">「最大含み益」は 4 案共通の分母（降りない解釈で保有が続いていた期間の最大値）。</p>
{_event_matrix(runs)}

<h2>8. §16 代表チャート</h2>
<p class="muted">1 枚に 4 案を重ねている。灰 = 参考（降りない） / 赤 = V1 LOW_BREAK /
青 = V2 CLOSE_BREAK / 緑 = V3 STRUCTURAL_BREAK。
割れの 3 段階（日中 ▼ / 終値 ○ / 上限 □）を別マーカーで出している。</p>
{_charts_section(chart_map, out_dir)}

<h2>9. §17 look-ahead bias</h2>
{_lookahead_section()}

<h2>10. §19 の 12 の問いへの回答</h2>
{_answers_section(runs, reality, metrics, revivals, waited, natural)}

<h2>11. 未確定のまま残した項目</h2>
<div class="ref">以下は<b>この検証では決めなかった</b>項目である。
32 件への当てはめで決めると過剰最適化になるため、観察結果を材料として
提示するに留める。<b>正式なルール変更は行っていない。</b></div>
{_open_items_table()}

<h2>12. 出力ファイル</h2>
{_output_files_table()}
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>warning_low 割れ後の扱いの比較</title>"
        f"<style>{CSS}{EXTRA_CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
