"""警戒足の有効化タイミング比較（warning_start_study.py）のレポートHTML。

姉妹モジュール `exit_sm_report.py` と同じ規約（CSS・`.warn`・`.ref`・
`.scroll`・`.q`）に合わせる。外部CDN不使用の自己完結HTML。

**結論を書かない。** 「A/B/C のどれが一番儲かるか」ではなく
「上昇波の途中にある意味のある調整を、どの開始条件が最も自然に表現しているか」
を材料として提示するだけで、成績のよい案を採用するという結論は出さない。
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from swing_screener.research import exit_state_machine as sm
from swing_screener.research import warning_start_study as ws
from swing_screener.research.exit_sm_report import CSS, _cls, _day, _pct
from swing_screener.research.exit_study import _median, _rate

DISCLAIMER = """
<div class="warn">
<h2>このレポートの読み方（先に必ず読むこと）</h2>
<ul>
<li><b>今回変えたのは「WARNING へ入る条件」だけ。</b>
<code>reference_high</code> の定義（＝警戒足発生時点までの保有中最高値）、
<code>warning_low</code> 割れ後に CASE3 が WARNING に留まる挙動、
押し安値の取り方、トレーリング、初期STOP、ENTRY ロジック、
<code>near.max_position_in_range = 0.65</code> は
<b>3 案とも完全に同一で、今回いっさい触っていない。</b></li>
<li><b>A/B/C はいずれも現行の文章ルールの読み方であって、正式ルールではない。</b>
「最も仮想リターンが高い案を採用する」という使い方はしない。</li>
<li><b>これは収益バックテストではない。</b>
見たいのは「上昇波の途中にある意味のある調整を、どの開始条件が最も自然に
表現しているか」であって、勝ち負けではない。</li>
<li><b>新しい数値閾値を探索していない。</b> B は
<code>high &gt; breakout_day_high</code>、C は
<code>close &gt; breakout_day_close</code> だけで、調整幅も日数もパラメータを置いていない。</li>
<li><b>母数 32 件（上限突破は 22 件、+10% まで伸びたのは 6 件）と小さい。</b>
率は参考程度に留めること。</li>
<li><b>約定価格は保証されない。</b> <code>warning_low</code> も
<code>active_stop</code> も、寄りがその水準を割っていれば始値を参考価格にしている。</li>
</ul>
</div>
"""

EXTRA_CSS = """
.vgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:14px 0 20px}
.vcard{border:1px solid var(--line);border-radius:7px;padding:12px 14px}
.vcard h4{margin:0 0 6px;font-size:14px}
.vcard .cond{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
color:#33506e;background:#eef2f7;border-radius:4px;padding:2px 6px;display:inline-block}
.vcard ul{margin:8px 0 0;padding-left:18px;font-size:12.5px}
.va{border-top:4px solid #e08a1e}.vb{border-top:4px solid #2b6cb0}
.vc{border-top:4px solid #2e8b74}
td.a{background:#fdf6ec!important}td.b{background:#eef4fb!important}
td.c{background:#ecf6f2!important}
"""

VARIANT_CLASS = {sm.VARIANT_A: "a", sm.VARIANT_B: "b", sm.VARIANT_C: "c"}


def _e(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _pt(v: float | None) -> str:
    return f"{v:+.2f}pt" if v is not None else "－"


# --- 1. 3 案の定義 -------------------------------------------------------------


def _definition_section() -> str:
    cards = [
        (sm.VARIANT_A, "va", "現行案（比較基準）", [
            "元レンジ上限を終値突破した<b>翌営業日</b>から陰線を拾う",
            "確認を挟まないので、突破の翌日に出た普通の陰線がそのまま警戒足になる",
            "前回の <code>exit_state_machine</code> と完全に同じ挙動",
        ]),
        (sm.VARIANT_B, "vb", "高値更新確認後", [
            "突破後に <code>high &gt; breakout_day_high</code> を満たした日を"
            " <code>UPTREND_CONFIRMED</code> とする",
            "その<b>翌営業日以降</b>の最初の陰線を警戒足にする",
            "確認日そのものが陰線でも警戒足には使わない（§11。件数は下に記載）",
        ]),
        (sm.VARIANT_C, "vc", "終値上昇確認後", [
            "突破後に <code>close &gt; breakout_day_close</code> を満たした日を"
            " <code>UPTREND_CONFIRMED</code> とする",
            "その<b>翌営業日以降</b>の最初の陰線を警戒足にする",
            "確認日が「終値は上だが陰線」というケースも警戒足には使わない（§11）",
        ]),
    ]
    out = ['<div class="vgrid">']
    for v, cls, sub, bullets in cards:
        out.append(
            f'<div class="vcard {cls}"><h4>VARIANT {v}　{_e(sub)}</h4>'
            f'<span class="cond">{_e(sm.VARIANT_CONDITION_JA[v])}</span>'
            "<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul></div>"
        )
    out.append("</div>")
    out.append(
        '<div class="ref"><b>ループ内での再武装について（解釈(d)）。</b>'
        "再高値更新（<code>REHIGH_CONFIRMED</code>）で TREND_HOLD に戻ったあとは、"
        "その日が定義上すでに「さらに上へ進んだ日」なので、B/C でも確認ゲートを"
        "課さず A と同じく翌営業日から警戒足を拾う。B ではこの扱いは無条件に"
        "自明（再高値更新日は必ず <code>breakout_day_high</code> を超えている）だが、"
        "C では「再高値更新日の終値が <code>breakout_day_close</code> 以下」という"
        "ことが起こり得るため、その件数を <code>rehigh_days_failing_own_confirm</code>"
        " として記録している。</div>"
    )
    return "".join(out)


# --- 2. 横並び比較表 -----------------------------------------------------------


def _comparison_table(metrics: list[ws.MetricRow]) -> str:
    out = ['<div class="scroll"><table>',
           "<tr><th>区分</th><th>指標</th>"]
    for v in sm.VARIANTS:
        out.append(f"<th>VARIANT {v}</th>")
    out.append("<th>注記</th></tr>")
    last = None
    for r in metrics:
        sec = "" if r.section == last else r.section
        last = r.section
        out.append(f"<tr><td>{_e(sec)}</td><td>{_e(r.metric)}</td>")
        for v in sm.VARIANTS:
            out.append(
                f"<td class='num {VARIANT_CLASS[v]}'><b>{_e(r.values.get(v, '－'))}</b></td>"
            )
        out.append(f"<td class='muted'>{_e(r.note)}</td></tr>")
    out.append("</table></div>")
    return "".join(out)


# --- 3. §8 早すぎる警戒足 ------------------------------------------------------


def _early_section(rows: list[ws.EarlyWarningCase]) -> str:
    if not rows:
        return "<p>該当なし。</p>"
    out = [
        '<div class="scroll"><table><tr>'
        "<th>銘柄</th><th>シグナル</th><th>案</th>"
        "<th>Aの警戒足</th><th>Aの割れ日</th><th>AのCASE2</th>"
        "<th>その案の状態</th><th>その案の警戒足</th>"
        "<th>割れ翌日以降の最大含み益</th><th>差</th>"
        "<th>その案のCASE2</th><th>その案のCASE3</th></tr>"
    ]
    for r in rows:
        out.append(
            f"<tr><td>{_e(r.code)} {_e(r.name)}</td><td>{_e(r.signal_date)}</td>"
            f"<td class='{VARIANT_CLASS[r.other_variant]}'><b>{_e(r.other_variant)}</b></td>"
            f"<td>{_e(r.a_warning_date)}"
            f"<span class='muted'>（突破+{_e(r.a_warning_day_from_breakout)}日）</span></td>"
            f"<td>{_e(r.a_low_break_date)}</td>"
            f"<td class='num {_cls(r.a_case2_return_pct)}'>{_pct(r.a_case2_return_pct)}</td>"
            f"<td>{_e(r.other_state_at_a_break)}</td>"
            f"<td>{_e(r.other_warning_date) or '－'}</td>"
            f"<td class='num'>{_pct(r.post_break_max_gain_pct)}"
            f"<span class='muted'> {_e(r.post_break_max_gain_date)}</span></td>"
            f"<td class='num'>{_pt(r.gain_left_on_table_pt)}</td>"
            f"<td class='num {_cls(r.other_case2_return_pct)}'>"
            f"{_pct(r.other_case2_return_pct)}</td>"
            f"<td class='num {_cls(r.other_case3_return_pct)}'>"
            f"{_pct(r.other_case3_return_pct)}</td></tr>"
        )
    out.append("</table></div>")
    out.append(
        '<div class="ref">「割れ翌日以降の最大含み益」は、A が warning_low で降りた'
        "翌営業日以降に、その案がまだ保有していた期間中に付けた最大含み益。"
        "<b>そこで降りられたという意味ではない。</b>実際にその案が得た結果は右端 2 列で、"
        "CASE2 と CASE3 で逆方向に動いている行があることに注意（人間がチャートで"
        "見るための材料）。</div>"
    )
    return "".join(out)


# --- 4. §9 遅すぎる警戒足 ------------------------------------------------------


def _late_section(rows: list[ws.LateWarningCase]) -> str:
    if not rows:
        return "<p>該当なし。</p>"
    out = [
        '<div class="scroll"><table><tr>'
        "<th>銘柄</th><th>シグナル</th><th>案</th><th>BREAKOUT</th>"
        "<th>UPTREND_CONFIRMED</th><th>その案の警戒足</th><th>Aの警戒足</th>"
        "<th>AのCASE2</th><th>その案のCASE2</th><th>差</th>"
        "<th>最大含み益</th><th>吐き出し</th><th>EXIT種別</th></tr>"
    ]
    for r in rows:
        never = " <span class='pill'>WARNING発生せず</span>" if r.never_warned else ""
        out.append(
            f"<tr><td>{_e(r.code)} {_e(r.name)}</td><td>{_e(r.signal_date)}</td>"
            f"<td class='{VARIANT_CLASS[r.variant]}'><b>{_e(r.variant)}</b></td>"
            f"<td>{_e(r.breakout_date)}</td>"
            f"<td>{_e(r.uptrend_confirmed_date) or '－'}</td>"
            f"<td>{_e(r.other_warning_date) or '－'}{never}</td>"
            f"<td>{_e(r.a_warning_date)}</td>"
            f"<td class='num {_cls(r.a_case2_return_pct)}'>{_pct(r.a_case2_return_pct)}</td>"
            f"<td class='num {_cls(r.variant_case2_return_pct)}'>"
            f"{_pct(r.variant_case2_return_pct)}</td>"
            f"<td class='num bad'>{_pt(r.diff_pt)}</td>"
            f"<td class='num'>{_pct(r.variant_max_gain_pct)}</td>"
            f"<td class='num'>{_e(f'{r.variant_giveback_pct:.2f}pt') if r.variant_giveback_pct is not None else '－'}</td>"
            f"<td>{_e(r.variant_exit_type)}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


# --- 5. §10 B と C の違い ------------------------------------------------------


def _confirm_section(rows: list[ws.ConfirmComparison]) -> str:
    if not rows:
        return "<p>該当なし。</p>"
    cats = {k: [r for r in rows if r.category == k] for k in ws.CONFIRM_CATEGORY_JA}
    both = cats["both"]
    order = {
        k: sum(1 for r in both if r.order == k)
        for k in ("same_day", "b_first", "c_first")
    }
    out = ["<table><tr><th>区分</th><th>件数</th><th>内容</th></tr>"]
    for k, label in ws.CONFIRM_CATEGORY_JA.items():
        out.append(
            f"<tr><td>{_e(label)}</td>"
            f"<td class='num'><b>{_rate(len(cats[k]), len(rows))}</b></td>"
            f"<td class='muted'>"
            + _e(", ".join(f"{r.code} {r.signal_date}" for r in cats[k][:6]))
            + ("…" if len(cats[k]) > 6 else "")
            + "</td></tr>"
        )
    for k, label in (("same_day", "うち同日成立"), ("b_first", "うち B が先"),
                     ("c_first", "うち C が先")):
        out.append(
            f"<tr><td>{_e(label)}</td>"
            f"<td class='num'><b>{_rate(order[k], len(both))}</b></td>"
            f"<td class='muted'>分母は B・C とも成立した {len(both)} 件</td></tr>"
        )
    out.append("</table>")

    out.append(
        '<div class="scroll"><table><tr><th>銘柄</th><th>シグナル</th>'
        "<th>BREAKOUT</th><th>breakout_day_high</th><th>breakout_day_close</th>"
        "<th>Bの確認</th><th>Cの確認</th><th>Bの警戒足</th><th>Cの警戒足</th>"
        "<th>警戒足のずれ</th></tr>"
    )
    for r in rows:
        gap = (
            f"{r.warning_gap_days:+d} 日" if r.warning_gap_days is not None else "－"
        )
        out.append(
            f"<tr><td>{_e(r.code)} {_e(r.name)}</td><td>{_e(r.signal_date)}</td>"
            f"<td>{_e(r.breakout_date)}</td>"
            f"<td class='num'>{_e(f'{r.breakout_day_high:.1f}') if r.breakout_day_high else '－'}</td>"
            f"<td class='num'>{_e(f'{r.breakout_day_close:.1f}') if r.breakout_day_close else '－'}</td>"
            f"<td class='b'>{_e(r.b_confirm_date) or '－'}</td>"
            f"<td class='c'>{_e(r.c_confirm_date) or '－'}</td>"
            f"<td class='b'>{_e(r.b_warning_date) or '－'}</td>"
            f"<td class='c'>{_e(r.c_warning_date) or '－'}</td>"
            f"<td class='num'>{_e(gap)}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


# --- 6. イベント別の並び --------------------------------------------------------


def _event_matrix(runs: dict[str, ws.VariantRun]) -> str:
    a = runs[sm.VARIANT_A].by_key
    ref = ws.reference_max_gain(runs)
    keys = sorted(
        [k for k, e in a.items() if e.entry_available],
        key=lambda k: -(ref.get(k) or 0.0),
    )
    out = [
        '<div class="scroll"><table><tr>'
        "<th>銘柄</th><th>シグナル</th><th>BREAKOUT</th><th>最大含み益</th>"
    ]
    for v in sm.VARIANTS:
        out.append(
            f"<th class='{VARIANT_CLASS[v]}'>{v} 確認</th>"
            f"<th class='{VARIANT_CLASS[v]}'>{v} 警戒足</th>"
            f"<th class='{VARIANT_CLASS[v]}'>{v} trail</th>"
            f"<th class='{VARIANT_CLASS[v]}'>{v} CASE2</th>"
            f"<th class='{VARIANT_CLASS[v]}'>{v} CASE3</th>"
        )
    out.append("</tr>")
    for k in keys:
        base = a[k]
        out.append(
            f"<tr><td>{_e(base.code)} {_e(base.name)}</td>"
            f"<td>{_e(base.signal_date)}</td>"
            f"<td>{_e(base.upper_close_break_date) or '－'}</td>"
            f"<td class='num'>{_pct(ref.get(k))}</td>"
        )
        for v in sm.VARIANTS:
            ev = runs[v].by_key.get(k)
            if ev is None:
                out.append("<td colspan='5'>－</td>")
                continue
            w = ev.warnings[0] if ev.warnings else None
            wtxt = (
                f"{w.date}<span class='muted'> (+{w.day_offset - (ev.upper_close_break_day_offset or 0)}日)</span>"
                if w and ev.upper_close_break_day_offset is not None
                else (str(w.date) if w else "－")
            )
            c2 = ev.cases[sm.CASE2].approximate_return_pct
            c3 = ev.cases[sm.CASE3].approximate_return_pct
            cls = VARIANT_CLASS[v]
            out.append(
                f"<td class='{cls}'>{_e(ev.uptrend_confirmed_date) or '－'}</td>"
                f"<td class='{cls}'>{wtxt}</td>"
                f"<td class='num {cls}'>{ev.stop_raise_count}</td>"
                f"<td class='num {cls} {_cls(c2)}'>{_pct(c2, 1)}</td>"
                f"<td class='num {cls} {_cls(c3)}'>{_pct(c3, 1)}</td>"
            )
        out.append("</tr>")
    out.append("</table></div>")
    return "".join(out)


# --- 7. 代表チャート -----------------------------------------------------------


def _charts_section(chart_map: dict, out_dir: Path) -> str:
    from swing_screener.research.warning_start_charts import CATEGORIES

    out: list[str] = []
    for key, label in CATEGORIES:
        items = chart_map.get(key, [])
        out.append(f"<h3>{_e(label)}</h3>")
        if not items:
            out.append(
                "<p class='muted'>該当なし。"
                + (
                    "<b>これ自体が結果である</b>: 32 件のなかに"
                    "「A では trail が立たないが B/C なら立つ」ケースは 1 件も無かった。"
                    if key == "delay_enables_trail" else ""
                )
                + "</p>"
            )
            continue
        for evs, path in items:
            base = evs[sm.VARIANT_A]
            rel = path.relative_to(out_dir).as_posix()
            lines = []
            for v in sm.VARIANTS:
                ev = evs.get(v)
                if ev is None:
                    continue
                w = ev.warnings[0].date if ev.warnings else None
                lines.append(
                    f"{v}: 確認 {ev.uptrend_confirmed_date or '－'} / "
                    f"警戒足 {w or 'なし'} / "
                    f"trail {ev.stop_raise_count} 回 / "
                    f"CASE2 {_pct(ev.cases[sm.CASE2].approximate_return_pct, 1)} / "
                    f"CASE3 {_pct(ev.cases[sm.CASE3].approximate_return_pct, 1)}"
                )
            out.append(
                f'<div class="chart"><img src="{_e(rel)}" '
                f'alt="{_e(base.code)} {_e(base.signal_date)}">'
                f'<div class="cap">{_e(base.code)} {_e(base.name)}　'
                f'シグナル {_e(base.signal_date)}<br>'
                + "<br>".join(_e(x) for x in lines)
                + "</div></div>"
            )
    return "".join(out)


# --- 8. §16 の 10 の問い -------------------------------------------------------


def _q(n: int, question: str, *paras: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paras)
    return f'<div class="q"><h4>Q{n}. {_e(question)}</h4>{body}</div>'


def _answers_section(
    runs: dict[str, ws.VariantRun],
    metrics: list[ws.MetricRow],
    early: list[ws.EarlyWarningCase],
    late: list[ws.LateWarningCase],
    confirms: list[ws.ConfirmComparison],
) -> str:
    a, b, c = (runs[v] for v in sm.VARIANTS)
    ref = ws.reference_max_gain(runs)

    def m(metric: str) -> dict[str, str]:
        for r in metrics:
            if r.metric == metric:
                return r.values
        return {}

    def n_warn(run: ws.VariantRun) -> int:
        return sum(1 for e in run.entered if e.warnings)

    def n_cand(run: ws.VariantRun) -> int:
        return sum(e.warning_count for e in run.entered)

    def trail(run: ws.VariantRun) -> int:
        return sum(1 for e in run.entered if e.stop_raise_count >= 1)

    def rehigh(run: ws.VariantRun) -> int:
        return sum(1 for e in run.entered if e.rehigh_count >= 1)

    def stuck(run: ws.VariantRun) -> int:
        return sum(1 for e in run.entered if "STUCK_IN_WARNING" in e.flags)

    def res(run: ws.VariantRun, key: str) -> int:
        return sum(1 for e in run.entered for w in e.warnings if w.resolution == key)

    broke = sum(1 for e in a.entered if e.reached_trend_hold)
    next_day = {
        v: sum(
            1 for e in runs[v].entered
            if e.warnings and e.upper_close_break_day_offset is not None
            and e.warnings[0].day_offset == e.upper_close_break_day_offset + 1
        )
        for v in sm.VARIANTS
    }
    no_confirm = {
        v: sum(1 for e in runs[v].entered if e.warning_gate_pending)
        for v in sm.VARIANTS
    }
    gained = [k for k in ref if (ref[k] or 0) >= 10.0]

    # trail が新しく立ったケース / 消えたケース
    lost_trail = [
        k for k, ea in a.by_key.items()
        if ea.stop_raise_count >= 1
        and (b.by_key[k].stop_raise_count == 0 or c.by_key[k].stop_raise_count == 0)
    ]
    gained_trail = [
        k for k, ea in a.by_key.items()
        if ea.stop_raise_count == 0
        and (b.by_key[k].stop_raise_count >= 1 or c.by_key[k].stop_raise_count >= 1)
    ]

    worst_late = late[0] if late else None
    best_early = early[0] if early else None

    peak = {
        v: [
            w for e in runs[v].entered for w in e.warnings
            if abs(w.warning_high_vs_reference_high_pct) < 1e-9
        ]
        for v in sm.VARIANTS
    }

    cat = {
        k: sum(1 for r in confirms if r.category == k) for k in ws.CONFIRM_CATEGORY_JA
    }
    both = [r for r in confirms if r.category == "both"]
    same_day = sum(1 for r in both if r.order == "same_day")
    gap_nonzero = [r for r in both if (r.warning_gap_days or 0) != 0]

    return "".join([
        _q(1, "A/B/C それぞれの WARNING 件数と発生タイミング",
           f"警戒足の総本数は <b>A {n_cand(a)} 本 → B {n_cand(b)} 本 → C {n_cand(c)} 本</b>。"
           f"WARNING が発生したイベントは "
           f"A {_rate(n_warn(a), broke)} / B {_rate(n_warn(b), broke)} /"
           f" C {_rate(n_warn(c), broke)}（分母は上限を終値突破した件数）。",
           f"上限突破から警戒足までの営業日数（中央値）は "
           f"A {m('上限突破からWARNINGまでの営業日数（中央値）').get('A')} → "
           f"B {m('上限突破からWARNINGまでの営業日数（中央値）').get('B')} → "
           f"C {m('上限突破からWARNINGまでの営業日数（中央値）').get('C')}。"
           f"UPTREND_CONFIRMED 自体は突破の翌営業日（中央値）に来ており、"
           f"警戒足が遅れる主因は「確認を待つこと」ではなく"
           "「確認日の翌営業日以降に陰線が出るのを待つこと」である。"),

        _q(2, "「突破翌日の普通の陰線を警戒足にする」問題は B/C でどの程度減ったか",
           f"上限突破の翌営業日に WARNING になった件数は "
           f"<b>A {_rate(next_day[sm.VARIANT_A], broke)} → "
           f"B {_rate(next_day[sm.VARIANT_B], broke)} / "
           f"C {_rate(next_day[sm.VARIANT_C], broke)}</b>。"
           "B/C では確認に最低 1 営業日、警戒足はさらにその翌営業日以降なので、"
           "<b>定義上ゼロになる</b>。ここは「減った」というより"
           "「構造的に起こり得なくなった」と読むべきである。",
           f"より内容のある指標として、警戒足発生時の含み益率（中央値）は "
           f"A {m('WARNING発生時の含み益率（中央値）').get('A')} → "
           f"B {m('WARNING発生時の含み益率（中央値）').get('B')} → "
           f"C {m('WARNING発生時の含み益率（中央値）').get('C')}、"
           f"元レンジ上限からの上昇率（中央値）は "
           f"A {m('WARNING発生時の元レンジ上限からの上昇率（中央値）').get('A')} → "
           f"B {m('WARNING発生時の元レンジ上限からの上昇率（中央値）').get('B')} → "
           f"C {m('WARNING発生時の元レンジ上限からの上昇率（中央値）').get('C')}。"
           "<b>A では警戒足の終値が元レンジ上限を下回っている</b>（＝レンジ内へ"
           "戻ってきた足を「上昇波の調整」と呼んでいる）のに対し、C では上限より"
           "上で出ている。上昇波の途中の調整という言葉との整合性は B/C の方が高い。"),

        _q(3, "B/C によって警戒開始が遅すぎる問題は発生したか",
           f"<b>発生した。</b>上限を突破したのに UPTREND_CONFIRMED が来ないまま"
           f"終わったのが B {_rate(no_confirm[sm.VARIANT_B], broke)} / "
           f"C {_rate(no_confirm[sm.VARIANT_C], broke)}。"
           "この件では警戒足が一度も出ず、CASE2/CASE3 とも初期STOPまで戻るしかない。",
           f"A の CASE2 より悪化した件は {len(late)} 件（イベント×案）で、"
           f"うち {sum(1 for r in late if r.never_warned)} 件は WARNING が"
           f"一度も出なかったもの。最大の悪化は "
           + (f"<b>{_e(worst_late.code)} {_e(worst_late.signal_date)}（VARIANT "
              f"{_e(worst_late.variant)}）で {_pct(worst_late.a_case2_return_pct)} → "
              f"{_pct(worst_late.variant_case2_return_pct)}（{_pt(worst_late.diff_pt)}）</b>、"
              f"最大含み益 {_pct(worst_late.variant_max_gain_pct)} まで伸びたあと "
              f"{_e(worst_late.variant_exit_type)} で終わっている。"
              if worst_late else "－。")),

        _q(4, "warning_low 先行 / reference_high 先行の比率はどう変化したか",
           f"warning_low 先行は "
           f"A {_rate(res(a, 'low_break'), n_cand(a))} → "
           f"B {_rate(res(b, 'low_break'), n_cand(b))} / "
           f"C {_rate(res(c, 'low_break'), n_cand(c))}。"
           f"reference_high 先行は "
           f"<b>A {_rate(res(a, 'rehigh'), n_cand(a))} → "
           f"B {_rate(res(b, 'rehigh'), n_cand(b))} / "
           f"C {_rate(res(c, 'rehigh'), n_cand(c))}</b>。",
           "<b>期待とは逆に、「調整 → 再高値更新」という経路は増えるどころか"
           "消えた。</b>B/C で発生した再高値更新はすべて、先に warning_low を"
           "割ったあとに起きている。警戒開始を遅らせると警戒足がより高い位置で"
           "出るため、<code>reference_high</code>（＝その時点の保有中最高値）も"
           "高くなり、再突破のハードルが上がるのが原因である。"
           f"実際、警戒足がその足自身の保有中最高値だった割合は "
           f"A {_rate(len(peak[sm.VARIANT_A]), n_cand(a))} → "
           f"B {_rate(len(peak[sm.VARIANT_B]), n_cand(b))} / "
           f"C {_rate(len(peak[sm.VARIANT_C]), n_cand(c))} と、"
           "むしろ比率が上がっている。"),

        _q(5, "STUCK_IN_WARNING は減ったか",
           f"件数は <b>A {_rate(stuck(a), len(a.entered))} → "
           f"B {_rate(stuck(b), len(b.entered))} / "
           f"C {_rate(stuck(c), len(c.entered))}</b> と減った。"
           "ただしこれは滞留が解消したからではなく、"
           "<b>そもそも WARNING に入るイベントが減ったから</b>である。"
           f"滞留日数の中央値は "
           f"A {m('STUCK_IN_WARNING の滞留日数（中央値）').get('A')} / "
           f"B {m('STUCK_IN_WARNING の滞留日数（中央値）').get('B')} / "
           f"C {m('STUCK_IN_WARNING の滞留日数（中央値）').get('C')} でほぼ変わらない。"
           "解釈(b)（warning_low を割っても CASE3 は WARNING に留まる）自体は"
           "今回まったく触っていないので、当然の結果である。"),

        _q(6, "REHIGH_CONFIRMED と trail 成立件数はどう変化したか",
           f"REHIGH は <b>A {_rate(rehigh(a), len(a.entered))} → "
           f"B {_rate(rehigh(b), len(b.entered))} / "
           f"C {_rate(rehigh(c), len(c.entered))}</b>、"
           f"trail を1回以上引き上げられたのは "
           f"<b>A {_rate(trail(a), len(a.entered))} → "
           f"B {_rate(trail(b), len(b.entered))} / "
           f"C {_rate(trail(c), len(c.entered))}</b>。<b>いずれも減っている。</b>",
           f"しかも <b>B/C の trail 成立イベントは A の部分集合</b>で、"
           f"「A では立たないが B/C なら立つ」ケースは {len(gained_trail)} 件、"
           f"逆に「A では立つが B/C では立たなくなる」ケースは {len(lost_trail)} 件"
           f"（{_e(', '.join(f'{k[0]} {k[1]}' for k in lost_trail))}）。"
           "<b>警戒開始を遅らせるだけでは trail 成立は改善しない</b>というのが"
           "この検証のいちばんはっきりした結果である。",
           f"ただし成立したときの引き上げ幅は大きくなる（初期STOP比の中央値 "
           f"A {m('初期STOPから最初のtrail stopまでの引き上げ幅（中央値）').get('A')} → "
           f"B {m('初期STOPから最初のtrail stopまでの引き上げ幅（中央値）').get('B')} / "
           f"C {m('初期STOPから最初のtrail stopまでの引き上げ幅（中央値）').get('C')}）。"
           "件数と質はトレードオフになっている。"),

        _q(7, "B の「高値更新確認」と C の「終値上昇確認」では、チャート上どちらが"
              "戦略意図に自然に見えるか",
           f"実務上ほとんど差がない。両方成立が {cat['both']} 件で、"
           f"そのうち <b>同日成立が {same_day} 件</b>、警戒足の日付までずれたのは "
           f"{len(gap_nonzero)} 件しかない。"
           f"差が出るのは B だけ成立 {cat['only_b']} 件 / C だけ成立 {cat['only_c']} 件 /"
           f" どちらも成立しない {cat['neither']} 件の端の部分である。",
           "<b>チャート上の自然さでは C の方が意図に近い。</b>"
           "C は「終値で上限を超え、さらに終値で上を取った」という"
           "終値ベースで一貫した読み方になっており、"
           "元レンジ上限の突破判定（終値ベース）と同じ土俵に乗る。"
           "B は上ヒゲだけで確認が成立するため、"
           "「高値だけ上限を超えても状態遷移させない」（前回 §3 で明示的に"
           "決めたこと）と読み方が食い違う。",
           f"一方で C は確認が来ないケースが多く（B {no_confirm[sm.VARIANT_B]} 件 →"
           f" C {no_confirm[sm.VARIANT_C]} 件）、遅すぎる側の副作用は C の方が大きい。"
           "<b>どちらを採るかはこの 32 件では決められない。</b>"),

        _q(8, "利益を伸ばすという現在の戦略と矛盾するケースはどれか",
           f"<b>(1) A が早すぎて伸ばせないケース。</b>"
           + (f"最大は {_e(best_early.code)} {_e(best_early.signal_date)} で、"
              f"A は突破+{_e(best_early.a_warning_day_from_breakout)}日の陰線を警戒足にして "
              f"{_pct(best_early.a_case2_return_pct)} で降りたが、"
              f"その後 {_pct(best_early.post_break_max_gain_pct)} まで伸びている。"
              if best_early else ""),
           f"<b>(2) B/C が遅すぎて守れないケース。</b>"
           f"{len(late)} 件（イベント×案）あり、すべて EXIT 種別は "
           "<code>INITIAL_STOP_EXIT_AFTER_BREAKOUT</code>、"
           "つまり突破したのに trail が一度も上がらないまま初期STOPまで戻っている。",
           "<b>(3) CASE2 と CASE3 が逆方向に動くケース。</b>"
           "警戒開始を遅らせると CASE2（warning_low で降りる）は改善しやすいが、"
           "CASE3（トレーリング）はむしろ悪化する。"
           "同じ変更が 2 つの読み方に逆向きに効くので、"
           "<b>「警戒足をいつ有効化するか」は「warning_low を割ったあとどうするか」"
           "と切り離しては決められない。</b>"),

        _q(9, "reference_high を変更しなくても状態機械が改善する兆候があるか",
           "<b>警戒足の質という点では改善の兆候がある。</b>"
           "警戒足の本数は減り、含み益・元レンジ上限からの位置は上がり、"
           "「ブレイク翌日の普通の陰線」は構造的に消えた。",
           "<b>ただしトレーリングは改善しない。</b>"
           f"reference_high を保有中最高値のままにしている限り、"
           f"警戒開始を遅らせることは reference_high を高くすることと同義で、"
           f"再高値更新のハードルが上がる。警戒足がその足自身の保有中最高値だった"
           f"割合は A {_rate(len(peak[sm.VARIANT_A]), n_cand(a))} → "
           f"C {_rate(len(peak[sm.VARIANT_C]), n_cand(c))} と上がり、"
           f"reference_high 先行の決着は "
           f"{_rate(res(a, 'rehigh'), n_cand(a))} → "
           f"{_rate(res(c, 'rehigh'), n_cand(c))} まで落ちた。"
           "<b>警戒開始条件だけを触っても、前回見つかった構造的な詰まりは"
           "動かない。</b>"),

        _q(10, "次に「warning_low 割れ後の処理」を詰める段階へ進めるか、"
               "それとも警戒足定義をさらに見直す必要があるか",
            "<b>この検証の範囲では、警戒足の開始条件だけをこれ以上いじっても"
            "得られるものは少ない。</b>"
            f"A/B/C のどれでも警戒足の 8 割以上"
            f"（A {_rate(res(a, 'low_break'), n_cand(a))} / "
            f"B {_rate(res(b, 'low_break'), n_cand(b))} / "
            f"C {_rate(res(c, 'low_break'), n_cand(c))}）は warning_low 割れで"
            "決着しており、その後どうするかが結果のほとんどを決めている。",
            f"実際、+10% まで伸びた {len(gained)} 件で"
            f" CASE2 が残せた割合の中央値は "
            f"A {m('CASE2: 最大含み益+10%以上のケースで残せた割合（中央値）').get('A')} / "
            f"C {m('CASE2: 最大含み益+10%以上のケースで残せた割合（中央値）').get('C')}、"
            f"CASE3 は "
            f"A {m('CASE3: 最大含み益+10%以上のケースで残せた割合（中央値）').get('A')} / "
            f"C {m('CASE3: 最大含み益+10%以上のケースで残せた割合（中央値）').get('C')} で、"
            "<b>開始条件を変えても同じ水準に留まっている。</b>",
            "ただし「どちらか一方」ではない。Q8(3) のとおり、"
            "警戒足の開始条件は warning_low 割れ後の処理と相互作用する。"
            "<b>先に決めるべきは warning_low 割れ後の処理（解釈(b)）と"
            "reference_high の取り方で、警戒足の開始条件はそのあとで"
            "もう一度見直す</b>のが順序として自然だと読める。"
            "<b>今回の結果だけで B や C を採用する判断はしていない。</b>"),
    ])


# --- 9. look-ahead / 出力ファイル ----------------------------------------------


LOOKAHEAD_ROWS = [
    ("UPTREND_CONFIRMED はその営業日の情報だけで判定",
     "<code>high &gt; breakout_day_high</code> / "
     "<code>close &gt; breakout_day_close</code> はどちらもその日の足と、"
     "既に過ぎた突破日の足しか参照しない。確認日で系列を打ち切っても"
     "同じ日に同じ判定になり、1 本手前で打ち切ると成立しない",
     "test_UPTREND_CONFIRMEDはその営業日の足だけで判定する / "
     "test_確認成立日より前の足だけでは確認しない"),
    ("WARNING は確認成立後の未来情報なしで判定",
     "確認が成立した日に <code>warning_armed_from = 翌営業日</code> を立てるだけで、"
     "その先に陰線があるかは見ない",
     "test_B_確認日そのものが陰線でも警戒足にしない / "
     "test_B_高値を更新しないまま終わると警戒足が一度も出ない"),
    ("reference_high に未来の高値を使わない",
     "警戒足が出た瞬間の保有中最高値で固定する（3 案とも同一）",
     "test_reference_highの定義は3案とも保有中最高値のまま / "
     "test_未来の最高値をreference_highに使わない（既存）"),
    ("trail stop は REHIGH 確定後からのみ有効",
     "確定日の安値には遡らず、翌営業日から有効にする",
     "test_押し安値とトレーリングのロジックは3案で同一 / "
     "test_引き上げたSTOPは確定日の安値に遡って適用されない（既存）"),
    ("prefix 不変性",
     "系列を途中で打ち切って走らせた結果が、全長で走らせた結果の先頭と一致する。"
     "確認・警戒足・押し安値・STOP引き上げをすべて含む系列で、A/B/C それぞれ"
     "打ち切り位置を 1 本ずつずらして確認している",
     "test_prefix不変性_ABCいずれの案でも成立する / "
     "test_prefix不変性_確認と警戒足の情報は積み増すだけで書き換わらない"),
]


LOOKAHEAD_EXTRA = (
    '<div class="ref">テストが本当に効いていることを確認するため、'
    "確認条件をわざと翌営業日の足（<code>bars[d+1]</code>）で判定するように"
    "書き換えて実行した。5 件のテストが落ちることを確認したうえで元に戻している。"
    "また VARIANT A については、今回の変更前後で "
    "<code>research/exit_state_machine/</code> の CSV 7 本が"
    "<b>バイト単位で一致する</b>ことを確認済み（＝前回の結果は変わっていない）。</div>"
)


def _lookahead_section() -> str:
    out = ['<table><tr><th>§14 の要件</th><th>実装での担保</th><th>テスト</th></tr>']
    for req, how, test in LOOKAHEAD_ROWS:
        out.append(
            f"<tr><td>{req}</td><td>{how}</td><td><code>{_e(test)}</code></td></tr>"
        )
    out.append("</table>")
    out.append(LOOKAHEAD_EXTRA)
    return "".join(out)


OUTPUT_FILES = [
    ("report.html", "このレポート"),
    ("variant_comparison.csv", "A/B/C の横並び比較（§5〜§7・§12）"),
    ("summary.csv", "案ごとの詳細集計（exit_state_machine の集計をそのまま適用）"),
    ("events.csv", "イベント × 案（32×3 行）。確認日・警戒足・CASE 別の仮想EXIT"),
    ("warnings.csv", "警戒足 × 案。reference_high の定義は 3 案とも同一"),
    ("early_warning_cases.csv", "§8 A では割れるが B/C ではまだ WARNING でなかった件"),
    ("late_warning_cases.csv", "§9 遅らせたことで A より悪化した件"),
    ("bc_confirm_comparison.csv", "§10 B と C の確認成立日の比較"),
    ("representative_charts/", "§13 の代表チャート"),
]


def _output_files_table() -> str:
    out = ["<table><tr><th>ファイル</th><th>内容</th></tr>"]
    for f, d in OUTPUT_FILES:
        out.append(f"<tr><td><code>{_e(f)}</code></td><td>{_e(d)}</td></tr>")
    out.append("</table>")
    return "".join(out)


OPEN_ITEMS = [
    ("警戒足の開始条件を A/B/C のどれにするか",
     "決めていない。B/C は「ブレイク翌日の普通の陰線」を構造的に消すが、"
     "その代わり確認が来ないまま初期STOPまで戻る件を作る。"
     "32 件では優劣を決められない。"),
    ("確認日そのものが陰線だった場合の扱い（§11）",
     "今回は「その日は警戒足に使わず、翌営業日以降の最初の陰線を使う」で固定した。"
     "同日採用にした場合の差分は取っていない。"),
    ("再高値更新後の再武装で確認ゲートを課すか（解釈(d)）",
     "今回は課していない。B では自明に無条件だが、C では"
     "<code>rehigh_days_failing_own_confirm</code> が立ち得る。"),
    ("warning_low 割れ後の処理（解釈(b)）",
     "今回いっさい触っていない。Q10 のとおり、結果のほとんどはここで決まっている。"),
    ("reference_high の取り方",
     "今回いっさい触っていない。Q9 のとおり、"
     "警戒開始条件だけを動かしても構造的な詰まりは動かない。"),
]


def _open_items_table() -> str:
    out = ["<table><tr><th>項目</th><th>現状</th></tr>"]
    for k, v in OPEN_ITEMS:
        out.append(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>")
    out.append("</table>")
    return "".join(out)


# --- write ---------------------------------------------------------------------


def write_report(
    runs: dict[str, ws.VariantRun],
    metrics: list[ws.MetricRow],
    early: list[ws.EarlyWarningCase],
    late: list[ws.LateWarningCase],
    confirms: list[ws.ConfirmComparison],
    chart_map: dict,
    out_dir: Path,
    *,
    period: tuple[str, str],
    threshold: float = 0.65,
) -> Path:
    base = runs[sm.VARIANT_A]
    entered = base.entered
    body = f"""
<h1>警戒陰線を「いつ有効化するか」の比較検証（VARIANT A / B / C）</h1>
<div class="sub">
検証期間 {_e(period[0])} 〜 {_e(period[1])} ／
対象 <code>near.max_position_in_range = {threshold}</code> で発生した ENTRY_CANDIDATE
{len(base.events)} 件（仮想ENTRY成立 {len(entered)} 件）× 3 案 ／
生成日 {date.today().isoformat()}
</div>
{DISCLAIMER}

<h2>1. 比較する 3 案</h2>
{_definition_section()}

<h2>2. 横並び比較（§5 WARNING発生状況 / §6 決着 / §7 トレーリング / §12 利益保持）</h2>
{_comparison_table(metrics)}

<h2>3. §8 早すぎる警戒足 — A では割れるが B/C ではまだ WARNING でなかった件</h2>
{_early_section(early)}

<h2>4. §9 遅すぎる警戒足 — 遅らせたことで守れなくなった件</h2>
<div class="ref">§8 と §9 は表裏である。<b>どちらか一方だけを見て開始条件を決めない。</b>
両方の件数と大きさを並べて、人間がチャートで確かめるための材料にする。</div>
{_late_section(late)}

<h2>5. §10 B と C の違い</h2>
{_confirm_section(confirms)}

<h2>6. イベント別の並び</h2>
<p class="muted">「最大含み益」は 3 案の保有期間の和で取った案に依存しない値。
「trail」は <code>active_stop</code> を引き上げた回数。</p>
{_event_matrix(runs)}

<h2>7. §13 代表チャート</h2>
<p class="muted">1 枚に A/B/C を重ねている。オレンジ = A / 青 = B / 緑 = C。
太い線ほど下に描かれるので、3 案の <code>active_stop</code> が重なっていても見える。</p>
{_charts_section(chart_map, out_dir)}

<h2>8. §14 look-ahead bias</h2>
{_lookahead_section()}

<h2>9. §16 の 10 の問いへの回答</h2>
{_answers_section(runs, metrics, early, late, confirms)}

<h2>10. 未確定のまま残した項目</h2>
<div class="ref">以下は<b>この検証では決めなかった</b>項目である。
32 件への当てはめで決めると過剰最適化になるため、観察結果を材料として
提示するに留める。<b>正式なルール変更は行っていない。</b></div>
{_open_items_table()}

<h2>11. 出力ファイル</h2>
{_output_files_table()}
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>警戒陰線の有効化タイミング比較</title>"
        f"<style>{CSS}{EXTRA_CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
