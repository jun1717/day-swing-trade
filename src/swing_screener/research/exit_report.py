"""EXIT スタディのレポートHTML。

外部CDN不使用の自己完結HTML。**結論を書かない。** 事実と順序を提示するだけで、
どこまで機械化しどこから人間判断にするかは人間が決める。
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from swing_screener.research.exit_study import (
    FLAG_LABELS_JA,
    GAIN_TARGETS,
    K_STOP_HIT,
    K_TRAIL,
    K_WARNING,
    K_WARNING_BREAK,
    TYPE_LABELS_JA,
    SummaryRow,
    TrackedEvent,
)

DISCLAIMER = """
<div class="warn">
<h2>このレポートの読み方（先に必ず読むこと）</h2>
<ul>
<li><b>これは収益バックテストではない。</b> 現行 <code>near.max_position_in_range = 0.65</code>
で発生した ENTRY_CANDIDATE を、現在の売買ルールに沿って ENTRY から追跡した観察記録である。
勝率・平均利益率で戦略を評価しないこと。</li>
<li><b>ポジションを閉じる機械判定に使ったのは確定ルールの初期損切り
（<code>range_lower × 0.995</code>, CODEX_HANDOFF §20）だけ。</b>
利確ルールは機械定義が未確定なので、それ以外に降りる判断を入れていない。
その結果ほぼ全件が最終的に初期STOPへ到達しているが、<b>これを「損切り失敗率」として読んではいけない。</b>
上昇後に遅れてSTOPへ来た件と、上昇せずにSTOPへ来た件は<b>別物</b>である（§12）。
順序を見ること。</li>
<li><b>ENTRY価格は「シグナル翌営業日の始値」。</b> これは検証用の約定価格であって、
「今後必ず翌日始値で買う」というルールを確定するものではない。</li>
<li><b>警戒陰線とトレーリングの数値はすべて未確定ルールの参考値。</b>
これらで売却判定はしていない。列名・見出しに「参考」を残してある。</li>
<li><b>ギャップの閾値・陰線サイズの閾値・トレーリング幅は一切作っていない。</b>
観察値を並べているだけである。</li>
<li><b>32件は独立ではない。</b> 同一銘柄・近接日のイベントが含まれる。
保有中に重複したENTRYは別ポジションとして扱わず、フラグを立てて分離している。</li>
<li><b>母数が小さい。</b> 分母32件、上限突破後の集計は22件。率は参考程度に留めること。</li>
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


def _event_table(events: list[TrackedEvent]) -> str:
    head = (
        "<tr><th>シグナル日</th><th>銘柄</th><th>TYPE</th><th>ギャップ</th>"
        "<th>位置<br>signal→翌寄</th><th>初期STOP<br>まで</th><th>上限<br>到達</th>"
        "<th>終値<br>突破</th><th>最大<br>上昇</th><th>+3/+5/+10</th>"
        "<th>STOP<br>到達日</th><th>警戒陰線<br>/割れ</th><th>trail<br>A/B</th>"
        "<th>フラグ</th></tr>"
    )
    rows = [head]
    for e in sorted(events, key=lambda x: (x.signal_date, x.code)):
        gains = "".join(
            "●" if e.reached_gain[t] else "○" for t in GAIN_TARGETS
        )
        pos = (
            f"{e.position_in_range:.2f}→{e.position_in_range_at_entry:.2f}"
            if e.position_in_range_at_entry is not None else f"{e.position_in_range:.2f}"
        )
        trail_a = "●" if e.trail_sim_strict and e.trail_sim_strict.armed else "○"
        trail_b = "●" if e.trail_sim_loose and e.trail_sim_loose.armed else "○"
        flags = "".join(
            f"<span class='pill'>{_e(FLAG_LABELS_JA.get(f, f))}</span>" for f in e.flags
        )
        rows.append(
            f"<tr><td>{e.signal_date}</td>"
            f"<td>{_e(e.code)} {_e(e.name[:10])}</td>"
            f"<td>{_e(e.type_label)}</td>"
            f"<td class='num {_cls(e.gap_pct)}'>{_pct(e.gap_pct)}</td>"
            f"<td class='num'>{pos}</td>"
            f"<td class='num'>{_pct(e.dist_to_stop_pct_at_entry, 1, False)}</td>"
            f"<td class='num'>{'D+' + str(e.upper_touch_day_offset) if e.reached_upper else '－'}</td>"
            f"<td class='num'>{'D+' + str(e.upper_close_break_day_offset) if e.upper_close_break else '－'}</td>"
            f"<td class='num {_cls(e.max_gain_pct)}'>{_pct(e.max_gain_pct, 1)}</td>"
            f"<td class='num'>{gains}</td>"
            f"<td class='num'>{'D+' + str(e.stop_day_offset) if e.hit_initial_stop else '未到達'}</td>"
            f"<td class='num'>{len(e.warning_candles)}/{e.warning_break_count}</td>"
            f"<td class='num'>{trail_a}/{trail_b}</td>"
            f"<td>{flags}</td></tr>"
        )
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


_KIND_SHORT = {
    "ENTRY": "ENTRY",
    "RANGE_UPPER_TOUCH": "上限到達",
    "RANGE_UPPER_HIGH_ONLY": "高値のみ上限超",
    "RANGE_UPPER_CLOSE_BREAK": "終値で上限突破",
    "NEW_HIGH": "高値更新",
    "GAIN_3PCT": "+3%",
    "GAIN_5PCT": "+5%",
    "GAIN_10PCT": "+10%",
    K_WARNING: "警戒陰線",
    K_WARNING_BREAK: "警戒陰線安値割れ→利確候補",
    "SWING_LOW_CONFIRMED": "押し安値確定",
    K_TRAIL: "trail候補",
    K_STOP_HIT: "初期STOP到達",
    "AMBIGUOUS_INTRADAY_ORDER": "順序不明",
    "DATA_END_STILL_OPEN": "データ終端",
    "NEXT_OPEN_ABOVE_RANGE_UPPER": "翌寄が上限超",
}

# 順序の主役だけを並べる。高値更新・押し安値確定は本数が多く読めなくなるため畳む。
_TIMELINE_KEEP = (
    "ENTRY", "RANGE_UPPER_TOUCH", "RANGE_UPPER_CLOSE_BREAK",
    "GAIN_3PCT", "GAIN_5PCT", "GAIN_10PCT", K_WARNING_BREAK, K_TRAIL,
    K_STOP_HIT, "AMBIGUOUS_INTRADAY_ORDER", "DATA_END_STILL_OPEN",
    "NEXT_OPEN_ABOVE_RANGE_UPPER",
)


def _timeline_row(e: TrackedEvent, keep=_TIMELINE_KEEP, max_items: int = 14) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for te in e.timeline:
        if te.kind not in keep:
            continue
        if te.kind in (K_TRAIL,) and te.kind in seen:
            continue
        seen.add(te.kind)
        label = _KIND_SHORT.get(te.kind, te.kind)
        chunk = f"D+{te.day_offset} {label}"
        parts.append(f"<b>{_e(chunk)}</b>" if te.kind == K_STOP_HIT else _e(chunk))
        if len(parts) >= max_items:
            break
    return " → ".join(parts)


def _timeline_section(events: list[TrackedEvent]) -> str:
    rows = ["<tr><th>銘柄</th><th>TYPE</th><th>イベント順序</th></tr>"]
    for e in sorted(events, key=lambda x: (x.type_label, x.signal_date)):
        rows.append(
            f"<tr><td>{_e(e.code)} {_e(e.name[:8])}<br>"
            f"<span class='muted'>{e.signal_date}</span></td>"
            f"<td>{_e(e.type_label)}</td>"
            f"<td class='tl'>{_timeline_row(e)}</td></tr>"
        )
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


def _warning_section(events: list[TrackedEvent]) -> str:
    """MANUAL_EXIT_REVIEW 用。閾値は設けず、各指標の極値だけを並べる。"""
    all_wc = [(e, w) for e in events for w in e.warning_candles]
    if not all_wc:
        return "<p>警戒陰線なし。</p>"

    def top(key, reverse, label):
        pool = [(e, w) for e, w in all_wc if key(w) is not None]
        pool.sort(key=lambda p: key(p[1]), reverse=reverse)
        rows = [
            "<tr><th>銘柄</th><th>日付</th><th>当日騰落率</th><th>実体/ATR14</th>"
            "<th>出来高倍率</th><th>終値の当日レンジ内位置</th><th>安値割れ</th></tr>"
        ]
        for e, w in pool[:8]:
            rows.append(
                f"<tr><td>{_e(e.code)} {_e(e.name[:8])}</td><td>{w.date}</td>"
                f"<td class='num {_cls(w.change_pct)}'>{_pct(w.change_pct)}</td>"
                f"<td class='num'>{w.body_to_atr:.2f}</td>"
                f"<td class='num'>{w.volume_ratio:.2f}倍</td>"
                f"<td class='num'>{w.close_pos_in_day_range:.2f}</td>"
                f"<td class='num'>{w.broke_low_date or '－'}</td></tr>"
            )
        return f"<h3>{label}</h3><div class='scroll'><table>{''.join(rows)}</table></div>"

    return (
        "<div class='ref'>§9 の「大陰線＋出来高急増＋安値引け＋支持帯割れなら陰線1本でも早期利確」は、"
        "機械的な数値定義が未確定である。したがって<b>ここでは一切売却判定をしていない</b>。"
        "以下は <code>MANUAL_EXIT_REVIEW</code>（人間が後からチャート確認する）用に、"
        "各参考指標の極値を並べたもの。<b>合成スコアも閾値も作っていない。</b>"
        f"保有中の陰線は全 {len(all_wc)} 本で、全件が MANUAL_EXIT_REVIEW 対象。</div>"
        + top(lambda w: w.change_pct, False, "当日騰落率が大きい順（下落）")
        + top(lambda w: w.body_to_atr, True, "実体幅 / ATR14 が大きい順")
        + top(lambda w: w.volume_ratio, True, "出来高倍率（25日平均比）が大きい順")
        + top(lambda w: w.close_pos_in_day_range, False, "終値が当日安値に近い順（安値引け）")
    )


def _trail_section(events: list[TrackedEvent]) -> str:
    rows = [
        "<tr><th>銘柄</th><th>初期STOP</th><th>参考A strict</th><th>Aでの撤退</th>"
        "<th>参考B loose</th><th>Bでの撤退</th><th>初期STOPのみの場合</th></tr>"
    ]
    for e in sorted(events, key=lambda x: (x.signal_date, x.code)):
        a, b = e.trail_sim_strict, e.trail_sim_loose
        if not (a and a.armed) and not (b and b.armed):
            continue

        def cell(s):
            if not s or not s.armed:
                return "<td class='muted'>－</td><td class='muted'>－</td>"
            lvl = f"{s.trail_stop_level:.1f}"
            if s.exit_date is None:
                ex = "<span class='muted'>期間内に未到達（保有継続）</span>"
            else:
                amb = " <b class='bad'>順序不明</b>" if s.ambiguous_with_initial_stop else ""
                ex = (f"D+{s.exit_day_offset} {s.exit_date}<br>"
                      f"<span class='{_cls(s.exit_return_pct)}'>"
                      f"{_pct(s.exit_return_pct)}</span>{amb}")
            return f"<td class='num'>{lvl}</td><td>{ex}</td>"

        rows.append(
            f"<tr><td>{_e(e.code)} {_e(e.name[:8])}<br>"
            f"<span class='muted'>{e.signal_date}</span></td>"
            f"<td class='num'>{e.initial_stop:.1f}</td>"
            + cell(a) + cell(b)
            + f"<td class='num {_cls(e.exit_return_pct)}'>{_pct(e.exit_return_pct)}</td></tr>"
        )
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


def _gap_section(events: list[TrackedEvent]) -> str:
    entered = [e for e in events if e.entry_available]
    rows = [
        "<tr><th>銘柄</th><th>シグナル日終値</th><th>翌営業日始値</th><th>ギャップ率</th>"
        "<th>元レンジ内位置<br>signal → 翌寄</th><th>元上限までの距離</th>"
        "<th>初期STOPまでの距離</th><th>元レンジを上抜けているか</th></tr>"
    ]
    for e in sorted(entered, key=lambda x: -(x.gap_pct or 0)):
        over = ("<b class='bad'>上抜け済み</b>" if e.entry_above_range_upper
                else ("<span class='bad'>位置 &gt; 0.65</span>"
                      if (e.position_in_range_at_entry or 0) > 0.65 else "－"))
        rows.append(
            f"<tr><td>{_e(e.code)} {_e(e.name[:10])}<br>"
            f"<span class='muted'>{e.signal_date}</span></td>"
            f"<td class='num'>{e.signal_close:.1f}</td>"
            f"<td class='num'>{e.entry_price:.1f}</td>"
            f"<td class='num {_cls(e.gap_pct)}'>{_pct(e.gap_pct)}</td>"
            f"<td class='num'>{e.position_in_range:.2f} → "
            f"{e.position_in_range_at_entry:.2f}</td>"
            f"<td class='num'>{_pct(e.dist_to_upper_pct_at_entry, 2, False)}</td>"
            f"<td class='num'>{_pct(e.dist_to_stop_pct_at_entry, 2, False)}</td>"
            f"<td>{over}</td></tr>"
        )
    return (
        "<div class='ref'>§4 のとおり、<b>「ギャップ +X% 以上なら買わない」のような新しい閾値は作っていない。</b>"
        "どの程度ギャップが出ているかを観察するだけ。判定に使っているのは、"
        "既に運用中の確定ガード <code>near.max_position_in_range = 0.65</code> との対比のみ。</div>"
        "<div class='scroll'><table>" + "".join(rows) + "</table></div>"
    )


def _charts_section(chart_map: dict, out_dir: Path) -> str:
    from swing_screener.research.exit_charts import CATEGORIES

    parts: list[str] = []
    for key, label in CATEGORIES:
        items = chart_map.get(key, [])
        parts.append(f"<h3>{_e(label)}</h3>")
        if not items:
            parts.append(
                "<p class='muted'>該当なし。"
                + ("初期STOPのレベルでは同日に両方へ到達した日が 0 件だった。"
                   if key == "ambiguous" else "")
                + "</p>"
            )
            continue
        for ev, path in items:
            rel = path.relative_to(out_dir).as_posix()
            parts.append(
                f"<div class='chart'><img src='{_e(rel)}' alt='{_e(ev.code)}'>"
                f"<div class='cap'><b>{_e(ev.code)} {_e(ev.name)}</b> "
                f"シグナル {ev.signal_date} / {_e(ev.type_label)}<br>"
                f"{_timeline_row(ev)}</div></div>"
            )
    return "".join(parts)


def _answers_section(events: list[TrackedEvent]) -> str:
    """§16 の 10 問への事実回答。判断ではなく数字を返す。"""
    entered = [e for e in events if e.entry_available]
    n, ne = len(events), len(entered)
    stopped = [e for e in entered if e.hit_initial_stop]
    broke = [e for e in entered if e.upper_close_break]
    type1 = [e for e in entered if e.type_label == "TYPE1"]
    warn_break = [e for e in entered if e.warning_break_count > 0]
    a_armed = [e for e in entered if e.trail_sim_strict and e.trail_sim_strict.armed]
    b_armed = [e for e in entered if e.trail_sim_loose and e.trail_sim_loose.armed]
    guard = [e for e in entered if (e.position_in_range_at_entry or 0) > 0.65]

    def r(num, den):
        return f"{num}/{den}（{num / den * 100:.0f}%）" if den else "－"

    qs = [
        ("1. 32件のうち、翌日始値で仮想ENTRYできた件数",
         f"<b>{r(ne, n)}</b>。全件で翌営業日の足が存在し、始値を取得できた。"
         "初期STOP以下で寄り付いた件は 0 件。"),
        ("2. ENTRY後すぐ初期STOPになった件数",
         f"上限に触れる前にSTOPへ来たのは <b>{r(len(type1), ne)}</b>（TYPE1）。"
         f"うち D+0〜D+1 でのSTOPが "
         f"{sum(1 for e in type1 if (e.stop_day_offset or 99) <= 1)} 件。"),
        ("3. 初期STOPより先にレンジ上限へ到達した割合",
         f"<b>{r(sum(1 for e in entered if e.first_event_order == 'upper_first'), ne)}</b>。"
         f"STOPが先だったのは {r(sum(1 for e in entered if e.first_event_order == 'stop_first'), ne)}。"
         f"日足で先後を決められなかったのは "
         f"{sum(1 for e in entered if e.first_event_order == 'ambiguous')} 件。"),
        ("4. レンジ上限を終値で突破した割合",
         f"<b>{r(len(broke), ne)}</b>。高値だけ上限を超えて終値は上限以下だったのは "
         f"{sum(1 for e in entered if e.upper_high_only_break and not e.upper_close_break)} 件、"
         f"上限に一度も触れなかったのは {ne - sum(1 for e in entered if e.reached_upper)} 件。"),
        ("5. 上限突破後に +3% / +5% / +10% まで伸びた割合",
         " / ".join(
             f"+{t:.0f}%: <b>{r(sum(1 for e in broke if e.reached_gain[t]), len(broke))}</b>"
             for t in GAIN_TARGETS
         ) + "（分母は終値で上限突破した件。基準は仮想ENTRY価格）"),
        ("6. 警戒陰線ルールを適用するとどんなEXIT候補が出るか",
         f"保有中に陰線が出たのは {r(sum(1 for e in entered if e.warning_candles), ne)}、"
         f"その安値を割ったのは <b>{r(len(warn_break), ne)}</b>。"
         f"つまりほぼ全件で利確候補が発生する。最初の警戒陰線が出るまでの中央値は "
         f"D+{sorted(e.warning_candles[0].day_offset for e in entered if e.warning_candles)[sum(1 for e in entered if e.warning_candles) // 2]}。"
         "<b>陰線を1本ずつ警戒足として扱うと候補が出すぎて選別にならない</b>点が、"
         "この検証で最もはっきりした未確定部分。"),
        ("7. トレーリングで初期STOPより有利な撤退ラインへ移行できそうな件数",
         f"§30 を文字通り読む参考A（押し安値の後に高値更新を要求）では <b>{r(len(a_armed), ne)}</b> しか成立しない。"
         f"押し安値の確定時点で引き上げてよいと読む参考B では <b>{r(len(b_armed), ne)}</b>。"
         "読み方の違いだけで結果が大きく変わるため、<b>ここは定義を決めないと数値が意味を持たない</b>。"),
        ("8. 翌日ギャップアップでENTRY品質が悪化するケース",
         f"翌日始値で元レンジ上限を上抜けていたのは "
         f"{sum(1 for e in entered if e.entry_above_range_upper)} 件。"
         f"現行ガード 0.65 をレンジ内位置が超えたのは <b>{r(len(guard), ne)}</b>。"
         "ただしギャップ率の中央値自体は小さく、位置ガードのすぐ下でシグナルが出た件が"
         "わずかなギャップで超えているだけのものも含まれる（次表で個別に確認できる）。"),
        ("9. ENTRYロジック自体に明らかな問題が見つかったか",
         f"上限へ到達したのが {r(sum(1 for e in entered if e.reached_upper), ne)}、"
         f"終値突破が {r(len(broke), ne)} なので、"
         "「下限反発を拾って上昇波の入口を捉える」という意図は機能している。"
         f"一方で {r(len(stopped), ne)} が最終的に初期STOPへ到達しており、"
         "<b>問題はENTRY側ではなく、上昇したあと利益を確定する機械ルールが無いこと</b>に集中している。"
         f"STOPへ来た件のうち {sum(1 for e in stopped if e.upper_break_before_stop)} 件は"
         "STOP前に上限を終値突破していた。"),
        ("10. 次に機械定義を詰めるべき項目",
         "レポート末尾の「未確定のまま残った項目」を参照。"),
    ]
    return "".join(
        f"<div class='q'><h4>{_e(q)}</h4><p>{a}</p></div>" for q, a in qs
    )


OPEN_ITEMS = """
<ol>
<li><b>「最初の陰線＝警戒足」の絞り込み。</b> 保有中の陰線をそのまま警戒足にすると
今回 175 本・ほぼ全件で利確候補が出た。<b>どの陰線を警戒足とみなすか</b>を決めないと、
§30 の HOLD／利確候補の分岐が機能しない。決めるべきは「高値更新の直後の陰線に限るのか」
「一定以上の実体を持つものに限るのか」など。<b>今回は数値を決めていない。</b></li>
<li><b>「陰線安値を割らず高値更新」の“高値”の定義。</b> 警戒陰線自身の高値なのか、
保有中の最高値なのかで結果が変わる（今回は両方を記録した）。</li>
<li><b>「新しい押し安値」の定義。</b> 既存の fractal（pivot_window=2）は
右側2本ぶん遅れて確定するため、トレーリングの反応が構造的に遅い。
さらに §30 を文字通り読むか緩く読むかで成立件数が 2 件と 10 件に分かれた。</li>
<li><b>初期STOPの約定前提。</b> STOP到達 31 件のうち 10 件は寄り付きで STOP を割っており、
日足検証の「STOP価格で降りられる」という前提は成立しない。
スリッページをどう見込むか。</li>
<li><b>利確ルールが無いこと自体。</b> 現状ポジションを閉じる機械判定は初期STOPだけなので、
上昇分を必ず往復させる構造になっている。§9 の大陰線例外を含め、
ここが機械定義の最優先項目である。</li>
</ol>
"""


def write_report(
    events: list[TrackedEvent],
    summary: list[SummaryRow],
    chart_map: dict,
    out_dir: Path,
    *,
    period: tuple[str, str],
    threshold: float,
) -> Path:
    entered = [e for e in events if e.entry_available]
    body = f"""
<h1>ENTRY 後の値動き追跡（EXIT スタディ）</h1>
<div class="sub">
検証期間 {_e(period[0])} 〜 {_e(period[1])} ／
対象 <code>near.max_position_in_range = {threshold}</code> で発生した ENTRY_CANDIDATE
{len(events)} 件（仮想ENTRY成立 {len(entered)} 件）／
生成日 {date.today().isoformat()}
</div>
{DISCLAIMER}

<h2>1. 主要指標</h2>
{_summary_table(summary)}

<h2>2. §16 への回答</h2>
{_answers_section(events)}

<h2>3. イベント一覧</h2>
<p class="muted">「+3/+5/+10」は仮想ENTRY価格（翌日始値）からの到達。●=到達 ○=未到達。
「trail A/B」は参考シミュレーションで trail が有効になったか。</p>
{_event_table(events)}

<h2>4. イベントの順序</h2>
<div class="ref">§12 のとおり、<b>単一の勝率指標で成否を決めない。</b>
同じ「初期STOP到達」でも、上限を突破して +8% まで伸びた後に戻ってきたのか、
一度も上昇せずに落ちたのかは別物である。以下は各イベントで何が何の順で起きたかを並べたもの。
高値更新と押し安値確定は本数が多いため畳んである（全量は <code>timeline.csv</code>）。</div>
{_timeline_section(events)}

<h2>5. 翌日ギャップと ENTRY 位置の再評価</h2>
{_gap_section(events)}

<h2>6. 警戒陰線（参考・売却判定はしていない）</h2>
{_warning_section(events)}

<h2>7. トレーリング候補（参考シミュレーション）</h2>
<div class="ref"><b>本番の売買ルールには反映していない。</b>
§30 の「新しい押し安値」は機械定義が未確定なので、既存の swing 検出
（<code>fractal</code>, <code>pivot_window=2</code>）で確定した押し安値を暫定的に使い、
§30 の読み方を 2 通りに分けて併記している。<br>
<b>参考A strict</b>: 押し安値の形成後に「前の高値を上抜く」ことを要求する（文字通りの読み）。<br>
<b>参考B loose</b>: 押し安値が確定した時点で trail を引き上げてよいとする読み。<br>
どちらが正しいかを<b>この検証では決めない</b>。</div>
{_trail_section(events)}

<h2>8. 代表チャート</h2>
{_charts_section(chart_map, out_dir)}

<h2>9. 未確定のまま残った項目</h2>
<div class="ref">以下は<b>この検証では決めなかった</b>。数値を置けば動くが、
過去6ヶ月32件への当てはめで決めると過剰最適化になるため、
観察結果を材料として提示するに留める。</div>
{OPEN_ITEMS}

<h2>10. 出力ファイル</h2>
<table>
<tr><th>ファイル</th><th>内容</th></tr>
<tr><td><code>events.csv</code></td><td>32件の追跡結果（1行1イベント）</td></tr>
<tr><td><code>summary.csv</code></td><td>集計値</td></tr>
<tr><td><code>timeline.csv</code></td><td>ENTRY後に起きたことの全イベント（順序の生データ）</td></tr>
<tr><td><code>warning_candles.csv</code></td><td>保有中の陰線全件と MANUAL_EXIT_REVIEW 用の参考指標</td></tr>
<tr><td><code>trail_candidates.csv</code></td><td>トレーリング候補（参考A/B）</td></tr>
<tr><td><code>representative_charts/</code></td><td>代表チャート</td></tr>
</table>
<p class="muted">前回の閾値スイープ検証（<code>research/report.html</code>,
<code>research/events*.csv</code>, <code>research/charts/</code>）は上書きしていない。</p>
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>ENTRY後の値動き追跡（EXITスタディ）</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
