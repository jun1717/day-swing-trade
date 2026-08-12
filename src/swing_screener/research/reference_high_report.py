"""`reference_high` の決め方の比較（reference_high_study.py）のレポートHTML。

姉妹モジュール `exit_sm_report.py` / `warning_break_report.py` と同じ規約
（CSS・`.warn`・`.ref`・`.scroll`・`.q`）に合わせる。外部CDN不使用の自己完結HTML。

**結論を書かない。** 「どの案が一番 trail を上げられるか／儲かるか」ではなく
「『WARNING 後の調整が終わり、上昇が再開した』と判断する基準として、
どの価格が最もチャート構造として自然か」を材料として提示するだけで、
成績のよい案を採用するという結論は出さない（§22）。
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from swing_screener.research import exit_state_machine as sm
from swing_screener.research import reference_high_study as rhs
from swing_screener.research.exit_sm_report import CSS, _cls, _day, _pct
from swing_screener.research.exit_study import _median, _rate

DISCLAIMER = """
<div class="warn">
<h2>このレポートの読み方（先に必ず読むこと）</h2>
<ul>
<li><b>今回変えたのは <code>reference_high</code> の決め方だけ。</b>
ENTRY ロジック、<code>near.max_position_in_range = 0.65</code>、
初期STOP <code>range_lower × 0.995</code>、WARNING 開始条件、
<code>warning_low</code> の定義、押し安値候補の計算方法、
<code>trail = 押し安値 × 0.995</code>、STOP を下方向へ動かさないこと は
<b>5 案とも完全に同一で、今回いっさい触っていない。</b>
固定利確も導入しておらず、新しい%閾値も探索していない。</li>
<li><b>WARNING 開始条件は VARIANT A、<code>warning_low</code> 割れ後は
CLOSE_BREAK（仮想EXITは翌営業日始値）に固定した。</b>
どちらも「今回の変数を <code>reference_high</code> だけに絞るための研究上の基準」であって、
<b>正式採用ではない。</b>前 2 回の検証の結論は保留のままである。</li>
<li><b>RH-A〜RH-E はいずれも比較用の仮説であって正式ルールではない。</b>
「trail 成立件数がいちばん多い案」や「仮想利益がいちばん高い案」を
自動的に採用する、という使い方はしない。</li>
<li><b>同じ日に「終値で <code>warning_low</code> 割れ」と
「<code>high &gt; reference_high</code>」の両方が成立する日がある。</b>
日足では先後を決められないので <code>AMBIGUOUS_REHIGH_EXIT_ORDER</code> として分離し、
<b>REHIGH 優先 / EXIT 優先の両方を走らせて差分を出している。</b>
どちらが正しい順序かは決めていない。</li>
<li><b>これは収益バックテストではない。</b>母数が小さく、
「どの案が正しいか」を成績で決められる規模ではない。</li>
</ul>
</div>
"""

EXTRA_CSS = """
figure{margin:14px 0 22px;}
figure img{width:100%;border:1px solid #d8dee6;border-radius:6px;}
figcaption{font-size:12px;color:#5a6b7a;margin-top:5px;}
.q{margin:16px 0 6px;font-weight:700;color:#1f3550;}
.a{margin:0 0 14px;padding-left:12px;border-left:3px solid #c8d4e0;}
td.num{text-align:right;font-variant-numeric:tabular-nums;}
.rulehead{font-size:12px;}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _d(v) -> str:
    return _day(v)


# --- 1. 5 案の定義 ------------------------------------------------------------


def _definition_section() -> str:
    body = "".join(
        f"<tr><td><code>{_e(r)}</code></td>"
        f"<td>{_e(sm.RH_RULE_SHORT_JA[r])}</td>"
        f"<td><code>{_e(sm.RH_RULE_CONDITION_JA[r])}</code></td>"
        f"<td class='muted'>{_e(sm.RH_RULE_LABELS_JA[r])}</td></tr>"
        for r in rhs.RULES
    )
    return (
        "<div class='scroll'><table><thead><tr><th>rh_rule</th><th>略称</th>"
        "<th>reference_high</th><th>意味</th></tr></thead><tbody>"
        + body + "</tbody></table></div>"
        + """
<div class="ref">
<b>大小関係は日足の定義から決まっている。</b> 警戒足は陰線なので
<code>open &gt; close</code>、また <code>high ≧ open</code> なので
<code>RH-A ≧ RH-B ≧ RH-D</code>、さらに <code>RH-A ≧ RH-C</code>、
<code>RH-A ≧ RH-E</code> が常に成り立つ。
つまり <b>RH-A が必ず最も高いハードル</b>で、他の 4 案はそれを下げる方向にしか動かない。
RH-C と RH-B / RH-D の前後は決まらない。
</div>
<div class="ref">
<b>RH-E を 1 案だけ足した理由（§6）。</b>
RH-A は保有中最高値に <b>警戒足自身の高値を含む</b>。上ヒゲの長い陰線が出ると
「その日の天井をもう一度抜くこと」が再上昇の条件になり、§4 で問題視された構造は
ここにある。ところが RH-B は警戒足の高値そのものなので、
<b>警戒足が最高値を作った日は RH-A と一致してしまい</b>、この構造を切り分けられない。
RH-E（警戒足前日までの高値）は「警戒足が出る<b>前</b>に作った高値を回復したか」という
同じ意味を、警戒足自身を含めずに測る唯一の既存価格であり、
RH-C（終値版）に対する高値版でもある。
ATR 倍率・○%・N日高値・移動平均といった<b>新しい調整パラメータは使っていない</b>。
</div>
"""
    )


# --- 2. 横並び比較 -------------------------------------------------------------


def _comparison_table(metrics: list[rhs.MetricRow], sections: tuple[str, ...]) -> str:
    rows = [m for m in metrics if m.section in sections]
    if not rows:
        return "<p class='muted'>該当なし。</p>"
    head = "".join(
        f"<th class='rulehead'>{_e(sm.RH_RULE_SHORT_JA[r])}</th>" for r in rhs.RULES
    )
    body = []
    for m in rows:
        cells = "".join(
            f"<td class='num'>{_e(m.values.get(r, '－'))}</td>" for r in rhs.RULES
        )
        body.append(
            f"<tr><td>{_e(m.section)}</td><td>{_e(m.metric)}</td>{cells}"
            f"<td class='muted'>{_e(m.note)}</td></tr>"
        )
    return (
        "<div class='scroll'><table><thead><tr><th>節</th><th>指標</th>"
        + head + "<th>注記</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table></div>"
    )


# --- 3. §15 位置関係 ----------------------------------------------------------


def _position_section(rows: list[rhs.PositionRow]) -> str:
    if not rows:
        return "<p class='muted'>該当なし。</p>"
    body = "".join(
        f"<tr><td>{_e(r.metric)}</td><td class='num'>{_e(r.value)}</td>"
        f"<td class='num'>{_e(r.count)}</td>"
        f"<td class='muted'>{_e(r.note)}</td></tr>"
        for r in rows
    )
    return (
        "<div class='scroll'><table><thead><tr><th>項目</th><th>値</th>"
        "<th>該当件数</th><th>注記</th></tr></thead><tbody>"
        + body + "</tbody></table></div>"
    )


# --- 4. §14 早すぎる trail -----------------------------------------------------


def _early_trail_section(rows: list[rhs.EarlyTrailCase]) -> str:
    if not rows:
        return "<p class='muted'>該当なし。</p>"
    ordered = sorted(rows, key=lambda c: -(c.post_exit_rise_pct or -999))
    body = "".join(
        f"<tr><td>{_e(c.rh_rule_label)}</td><td>{_e(c.code)} {_e(c.name)}</td>"
        f"<td>{_d(c.signal_date)}</td><td>{_d(c.exit_date)}</td>"
        f"<td class='num'>{_pct(c.exit_return_pct)}</td>"
        f"<td class='num'>{_pct(c.post_exit_rise_pct)}</td>"
        f"<td>{'○' if c.exceeded_holding_high else '－'}</td>"
        f"<td class='num'>{_pct(c.post_exit_max_gain_pct)}</td></tr>"
        for c in ordered[:40]
    )
    return (
        "<div class='scroll'><table><thead><tr><th>案</th><th>銘柄</th>"
        "<th>シグナル</th><th>trail EXIT</th><th>EXITリターン</th>"
        "<th>EXIT後の上昇率</th><th>保有中最高値を更新</th>"
        "<th>EXIT後の最大含み益</th></tr></thead><tbody>"
        + body + "</tbody></table></div>"
        + ("<p class='muted'>上位 40 件のみ表示。全件は "
           "<code>early_trail_cases.csv</code>。</p>" if len(ordered) > 40 else "")
    )


# --- 5. §19 fractal 参考比較 ---------------------------------------------------


def _fractal_section(rows: list[rhs.FractalRow]) -> str:
    if not rows:
        return "<p class='muted'>該当なし。</p>"
    body = "".join(
        f"<tr><td>{_e(r.rh_rule_label)}</td><td class='num'>{r.ours_confirmed}</td>"
        f"<td class='num'>{r.both_recognized}</td><td class='num'>{r.ours_only}</td>"
        f"<td class='num'>{r.fractal_only}</td><td class='num'>{r.ours_first}</td>"
        f"<td class='num'>{r.same_day}</td><td class='num'>{r.fractal_first}</td>"
        f"<td class='num'>{_e(r.median_lead_days)}</td></tr>"
        for r in rows
    )
    return (
        "<div class='scroll'><table><thead><tr><th>案</th>"
        "<th>今回確定</th><th>fractalも同じ安値</th><th>今回のみ</th>"
        "<th>fractalのみ</th><th>今回が先</th><th>同日</th><th>fractalが先</th>"
        "<th>今回のリード日数 中央値</th></tr></thead><tbody>"
        + body + "</tbody></table></div>"
        + """
<div class="ref">
<b>成績競争ではない（§19）。</b> 見たいのは「reference_high 方式の押し安値確定が、
fractal より自然な時系列判定になっているか」の一点である。
<code>fractalのみ</code> は「今回は REHIGH に到達せず押し安値を確定できなかったが、
その WARNING 期間の中に fractal なら押し安値と認める安値があった」件数。
fractal は右側 pivot が揃うまで確定しないので、確定日の比較も
<code>_fractal_confirm_index</code> で 1 日ずつスライスして求めている。
この比較は状態機械の後段パスであり、<b>追跡結果には一切影響しない</b>。
</div>
"""
    )


# --- 6. §7 同日順序の感度 ------------------------------------------------------


def _sensitivity_section(rows: list[rhs.AmbiguitySensitivity]) -> str:
    if not rows:
        return "<p class='muted'>該当なし。</p>"
    body = "".join(
        f"<tr><td>{_e(r.rh_rule_label)}</td><td class='num'>{r.ambiguous_days}</td>"
        f"<td class='num'>{r.events_affected}</td><td class='num'>{r.events_changed}</td>"
        f"<td class='num'>{_e(r.rehigh_first_median_return)}</td>"
        f"<td class='num'>{_e(r.exit_first_median_return)}</td></tr>"
        for r in rows
    )
    return (
        "<div class='scroll'><table><thead><tr><th>案</th><th>順序不明の日</th>"
        "<th>該当イベント</th><th>結果が変わったイベント</th>"
        "<th>REHIGH優先の仮想EXIT 中央値</th><th>EXIT優先の仮想EXIT 中央値</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
        + """
<div class="ref">
<b>どちらが正しい順序かは決めていない（§7）。</b>
日足には「その日のどの時点で <code>high</code> を付けたか」の情報がない。
引け後に日足を見る運用では、<b>終値割れも高値更新も同時に見える</b>ので、
時間順ではなく「どちらの合図を優先するか」というルール設計の問題になる。
前回までの実装は REHIGH 優先だったが、それは実装の都合であって根拠ではないため、
今回は外部パラメータに切り出して<b>両方を走らせた結果を並べている</b>。
</div>
"""
    )


# --- 7. イベント別の並び -------------------------------------------------------


def _event_matrix(
    runs: dict[str, rhs.RHRun], frames: dict[rhs.EventKey, rhs.Frame]
) -> str:
    base = runs[sm.RH_HOLDING]
    avail = rhs.available_max_gain(frames)
    by_rule = {r: runs[r].by_key for r in rhs.RULES if r in runs}
    head = "".join(
        f"<th class='rulehead'>{_e(sm.RH_RULE_SHORT_JA[r])}</th>" for r in rhs.RULES
    )
    rows: list[str] = []
    for ev in sorted(
        base.entered, key=lambda e: -(avail.get(rhs.key_of(e)) or 0.0)
    ):
        k = rhs.key_of(ev)
        cells = []
        for r in rhs.RULES:
            e = by_rule[r].get(k)
            if e is None:
                cells.append("<td class='num'>－</td>")
                continue
            res = e.path_result
            cells.append(
                f"<td class='num {_cls(res.approximate_return_pct)}'>"
                f"{_pct(res.approximate_return_pct)}"
                f"<br><span class='muted' style='font-size:11px'>"
                f"{_e(res.exit_type)} / STOP↑{e.stop_raise_count}</span></td>"
            )
        rows.append(
            f"<tr><td>{_e(ev.code)} {_e(ev.name)}</td><td>{_d(ev.signal_date)}</td>"
            f"<td class='num'>{_pct(avail.get(k))}</td>" + "".join(cells) + "</tr>"
        )
    return (
        "<div class='scroll'><table><thead><tr><th>銘柄</th><th>シグナル</th>"
        "<th>追跡窓の最大含み益</th>" + head + "</tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
    )


# --- 8. 代表チャート -----------------------------------------------------------


def _charts_section(chart_map: dict, out_dir: Path) -> str:
    from swing_screener.research.reference_high_charts import CATEGORIES

    out: list[str] = []
    for key, label in CATEGORIES:
        items = chart_map.get(key) or []
        out.append(f"<h3>{_e(label)}</h3>")
        if not items:
            out.append("<p class='muted'>該当なし。</p>")
            continue
        for evs, path in items:
            base = evs[sm.RH_HOLDING]
            rel = path.relative_to(out_dir).as_posix()
            summary = " ／ ".join(
                f"{sm.RH_RULE_SHORT_JA[r]} "
                f"{_pct(evs[r].path_result.approximate_return_pct)}"
                for r in rhs.RULES if r in evs
            )
            out.append(
                f"<figure><img src='{_e(rel)}' alt='{_e(base.code)}'>"
                f"<figcaption>{_e(base.code)} {_e(base.name)} "
                f"シグナル {_d(base.signal_date)} — {_e(summary)}</figcaption></figure>"
            )
    return "".join(out)


# --- 9. §18 look-ahead --------------------------------------------------------

LOOKAHEAD_ROWS = [
    ("RH-A は WARNING 発生時点までのデータだけを使用",
     "`holding_high` は日次ループの中で当日の足まで反映した running max。"
     "警戒足を開く `_open_warning` の呼び出し時点の値をそのまま固定し、以後書き換えない。",
     "test_RH_Aは警戒足当日までの高値だけを使う"),
    ("RH-B は WARNING 当日の OHLC だけを使用",
     "`reference_high = bar.high`。その足以外は参照しない。",
     "test_RH_Bは警戒足自身の高値になる"),
    ("RH-C は WARNING 前日までの close だけを使用",
     "終値ベースの running max は日次処理の**最後**に更新する。"
     "同じ日に開いた警戒足の `reference_high` には当日の終値が入らない。",
     "test_RH_Cは警戒足当日の終値を含まない"),
    ("RH-D は WARNING 当日の open だけを使用",
     "`reference_high = bar.open`。その足以外は参照しない。",
     "test_RH_Dは警戒足の始値になる"),
    ("RH-E は WARNING 前日までの high だけを使用",
     "当日の足を running max に入れる**前**の値を渡している。"
     "警戒足自身の上ヒゲは入らない。",
     "test_RH_Eは警戒足当日の高値を含まない"),
    ("REHIGH はその日までのデータで判定",
     "`high > reference_high` をその営業日の足だけで評価する。"
     "翌日以降の足はループの中で一度も参照しない。",
     "test_REHIGHはその営業日の足だけで判定する"),
    ("new_swing_low_candidate は REHIGH 確定時に初めて確定",
     "押し安値は「警戒足の日から**今日まで**」の安値の最小値。"
     "`min_low_value` は毎日更新され、再突破した日の値でそのまま確定する。",
     "test_押し安値は再突破日までの安値だけで決まる"),
    ("trail stop は REHIGH 翌営業日からのみ有効",
     "確定した STOP は `pending_stop` に入り、翌営業日の寄りで `active_stop` に入る。"
     "確定した当日の安値には遡って適用されない。",
     "test_trail_stopは翌営業日から有効"),
    ("EXIT 後の未来値を判定には使用しない",
     "EXIT 後にどこまで伸びたかは追跡が終わったあとに集計する。"
     "状態機械は EXIT 時点で閉じており、その後の足で挙動が変わることはない。",
     "test_EXIT後の値動きは状態遷移に影響しない_RH"),
    ("prefix invariant を各 RH 案で満たす",
     "系列を先頭から k 本で打ち切って再実行しても、"
     "その時点までに確定していた REHIGH・押し安値・STOP 引き上げは変わらない。",
     "test_prefix不変性_5案いずれでも成立する"),
]

LOOKAHEAD_EXTRA = """
<div class="ref">
<b>prefix 不変性テストを 5 案に拡張した。</b> 系列を先頭から <code>k</code> 本で
打ち切って再実行し、フル実行の結果と一致するかを RH-A〜RH-E すべてで確認している。
その時点までに成立していた REHIGH の日付、押し安値、STOP 引き上げの内容と
有効化日は打ち切っても変わらない。
<br><br>
<b>変異チェックも行った。</b> 次の 3 通りに書き換えると look-ahead テストが失敗し、
元に戻すと全 243 件が通ることを確認している。
<ul>
<li>RH-C の終値 running max を日次処理の<b>先頭</b>で更新する（＝警戒足当日の終値が
<code>reference_high</code> に混ざる）→ <b>1 件失敗</b></li>
<li>RH-E に当日の足を入れた<b>後</b>の <code>holding_high</code> を渡す
（＝RH-A と同じになる）→ <b>4 件失敗</b></li>
<li>REHIGH 判定を <code>bars[d+1].high</code> で行う → <b>7 件失敗</b>
（今回のテストだけでなく、既存の <code>exit_state_machine</code> /
<code>warning_break</code> のテストも落ちる）</li>
</ul>
<b>既存出力の回帰も確認済み。</b> <code>rh_rule</code> の既定値は
<code>HOLDING_HIGH</code>（＝従来の定義）なので、
<code>research/exit_state_machine/</code> の CSV 7 本、
<code>research/warning_start_study/</code> の CSV 7 本、
<code>research/warning_break_study/</code> の集計 CSV は
<b>いずれも数値がバイト単位で以前と同一</b>である。
唯一の差分は <code>warning_break_study/events.csv</code> の
<code>timeline_summary</code> 列に <code>AMBIGUOUS_REHIGH_EXIT_ORDER</code> の
表示が増えたことで、これは今回 §7 に従って
<b>それまで黙って REHIGH 優先で処理していた同日成立を明示した</b>ためである。
判定も数値も変わっていない。
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


# --- 10. §21 の 14 問 ----------------------------------------------------------


def _mv(metrics: list[rhs.MetricRow], metric: str, rule: str) -> str:
    for m in metrics:
        if m.metric == metric:
            return m.values.get(rule, "－")
    return "－"


def _all(metrics: list[rhs.MetricRow], metric: str) -> str:
    return " / ".join(_mv(metrics, metric, r) for r in rhs.RULES)


def _pv(rows: list[rhs.PositionRow], prefix: str) -> str:
    for r in rows:
        if r.metric.startswith(prefix):
            return f"{r.value}{('（' + r.count + '）') if r.count else ''}"
    return "－"


def _answers_section(
    runs: dict[str, rhs.RHRun],
    frames: dict[rhs.EventKey, rhs.Frame],
    metrics: list[rhs.MetricRow],
    position: list[rhs.PositionRow],
    fractal: list[rhs.FractalRow],
    sens: list[rhs.AmbiguitySensitivity],
    early: list[rhs.EarlyTrailCase],
) -> str:
    a = runs[sm.RH_HOLDING]
    n = len(a.entered)
    frac = {f.rh_rule: f for f in fractal}
    by_rule_early: dict[str, list[rhs.EarlyTrailCase]] = {r: [] for r in rhs.RULES}
    for c in early:
        by_rule_early.setdefault(c.rh_rule, []).append(c)

    def qa(q: str, ans: str) -> str:
        return f"<div class='q'>{_e(q)}</div><div class='a'>{ans}</div>"

    out: list[str] = []

    out.append(qa(
        "1. RH-A/B/C/D で REHIGH_CONFIRMED 件数はどう変化したか",
        "警戒足に対する REHIGH の成立率は "
        f"<b>{_all(metrics, 'REHIGH_CONFIRMED 件数')}</b>（RH-A/B/C/D/E の順）。"
        "<b>RH-A が最も少ない。</b>ハードルを下げれば成立は増えるが、"
        f"WARNING → REHIGH までの営業日数の中央値は "
        f"<b>{_all(metrics, 'WARNING → REHIGH までの営業日数 中央値')}</b> で"
        "<b>どの案も変わらない</b>。つまり緩めても「早く」なるのではなく、"
        "<b>それまで成立しなかった件が成立するようになる</b>という変化である。"
        "警戒足の件数自体も案によって動く（"
        f"{_all(metrics, 'WARNING 発生件数')}）。"
        "REHIGH すると WARNING を抜けて次の警戒足が出るので、"
        "分母も一緒に変わる点に注意。",
    ))

    out.append(qa(
        "2. trail stop 成立件数はどう変化したか",
        f"1 回以上引き上げたイベントは <b>{_all(metrics, 'trail stop を1回以上引き上げたイベント数')}</b>、"
        f"2 回以上は <b>{_all(metrics, 'trail stop を2回以上引き上げたイベント数')}</b>、"
        f"更新の総回数は <b>{_all(metrics, 'trail stop 更新の総回数')}</b>。"
        f"初回引き上げまでの営業日数は <b>{_all(metrics, '初回 STOP 引き上げまでの営業日数 中央値')}</b>、"
        f"初期STOPからの引き上げ幅は <b>{_all(metrics, 'initial_stop → 初回 trail stop の引き上げ幅 中央値')}</b>。"
        "<b>緩い案ほど件数は増えるが、1 回あたりの引き上げ幅は小さくなる。</b>"
        "低い位置で押し安値が確定するので当然の結果で、"
        f"STOP を ENTRY 価格より上へ持ち上げられた件数は "
        f"<b>{_all(metrics, 'STOP を ENTRY 価格より上へ持ち上げたイベント数')}</b> と"
        "<b>ほとんど動かない</b>。",
    ))

    out.append(qa(
        "3. 現行 RH-A が厳しすぎるという仮説は支持されたか",
        "<b>方向としては支持されたが、程度は小さい。</b>"
        f"RH-A は 5 案の中で必ず最も高い水準で、REHIGH 成立率も最小"
        f"（{_mv(metrics, 'REHIGH_CONFIRMED 件数', sm.RH_HOLDING)}）。"
        "§4 で問題視した「警戒陰線自身が最高値を作り、その天井をもう一度抜くことが"
        "再上昇の条件になる」構造は実在し、"
        f"<b>{_pv(position, 'RH-A と RH-B が同じ')}</b> で起きていた。"
        "ただしその構造を取り除いた RH-E（警戒足前日までの高値）でも REHIGH は "
        f"{_mv(metrics, 'REHIGH_CONFIRMED 件数', sm.RH_PRE_HIGH)} にしかならず、"
        f"RH-A の {_mv(metrics, 'REHIGH_CONFIRMED 件数', sm.RH_HOLDING)} からほとんど動かない。"
        "<b>「厳しすぎるから拾えない」より「その水準に戻ってくる相場がそもそも少ない」"
        "方が効いている。</b>",
    ))

    out.append(qa(
        "4. RH-B <code>warning_high</code> は「警戒足を否定して再上昇」という意味で"
        "自然に機能するか",
        "<b>意味としては自然だが、期待したほど別物にならない。</b>"
        f"{_pv(position, 'RH-A と RH-B が同じ')} は RH-A と同一値で、"
        "その場合「警戒足を否定する」ことと「保有中最高値を抜く」ことが同じになるため、"
        "<b>いちばん問題が出やすい場面でだけ RH-A と区別がつかない</b>。"
        f"差がある残りでも RH-A との差は中央値 {_pv(position, 'RH-B 警戒足高値')} と小さい。"
        f"結果として trail 成立は {_mv(metrics, 'trail stop を1回以上引き上げたイベント数', sm.RH_WARNING_HIGH)}、"
        f"trail EXIT 後にさらに +10% 以上伸びたのは "
        f"{_mv(metrics, '　うち EXIT 後さらに +10% 以上上昇', sm.RH_WARNING_HIGH)}。",
    ))

    out.append(qa(
        "5. RH-C の終値ベースは、現在の日足・終値中心の運用と自然に整合するか",
        "<b>整合する。</b>本システムは元レンジ上限の突破を終値で判定し、"
        "warning_low 割れも（今回の固定条件では）終値で判定している。"
        "「調整終了」だけを高値（上ヒゲ）で測る現行 RH-A は、その中で唯一"
        "<b>ヒゲ基準が混ざっている場所</b>である。RH-C はそこを終値ベースに揃える案で、"
        f"RH-A より低い幅は中央値 {_pv(position, 'RH-C 前日までの終値高値')}、"
        f"REHIGH 成立は {_mv(metrics, 'REHIGH_CONFIRMED 件数', sm.RH_PRE_CLOSE)}。"
        "<b>ただし判定そのものは 5 案とも <code>high &gt; reference_high</code> のままで、"
        "終値で判定しているわけではない。</b>"
        "「終値で作った高値を、ザラ場で超えたか」を見ている点は残る。",
    ))

    out.append(qa(
        "6. RH-D は緩すぎる兆候があるか",
        "<b>ある。</b>RH-D は 5 案の中で"
        f"{_pv(position, '5 案の中で最も低かった: RH-D')} と最頻の最安水準で、"
        f"REHIGH 成立は最多（{_mv(metrics, 'REHIGH_CONFIRMED 件数', sm.RH_WARNING_OPEN)}）、"
        f"trail 成立も最多（{_mv(metrics, 'trail stop を1回以上引き上げたイベント数', sm.RH_WARNING_OPEN)}）。"
        "しかしその trail で降りた後にさらに上昇したケースが、"
        f"+3% 以上 {_mv(metrics, '　うち EXIT 後さらに +3% 以上上昇', sm.RH_WARNING_OPEN)}、"
        f"+10% 以上 {_mv(metrics, '　うち EXIT 後さらに +10% 以上上昇', sm.RH_WARNING_OPEN)}、"
        f"EXIT 後に保有中最高値を更新したのが "
        f"{_mv(metrics, '　うち EXIT 後に保有中最高値を更新', sm.RH_WARNING_OPEN)}。"
        "<b>「陰線の始値を一度上回った」だけでは調整終了の判断として弱い</b>ことを示している。",
    ))

    out.append(qa(
        "7. +5% / +10% 以上伸びたケースの利益吐き出しは改善したか",
        "<b>ほとんど改善しない。これが今回いちばん重要な結果である。</b>"
        f"最大含み益 +5% 以上（{_mv(metrics, '最大含み益 +5% 以上に到達したイベント数', sm.RH_HOLDING)}）で"
        f"最大利益のうち残せた割合の中央値は <b>{_all(metrics, '　最大利益のうち残せた割合 中央値（+5%）')}</b>、"
        f"+10% 以上（{_mv(metrics, '最大含み益 +10% 以上に到達したイベント数', sm.RH_HOLDING)}）でも "
        f"<b>{_all(metrics, '　最大利益のうち残せた割合 中央値（+10%）')}</b>。"
        f"吐き出し幅の中央値も <b>{_all(metrics, '吐き出し幅 中央値（共通分母 − 仮想EXITリターン）')}</b> と"
        "ほぼ横並びで、<b>reference_high をどう変えてもこの穴は埋まらない。</b>"
        "trail を上げられた件数は増えるのに残せた利益が増えないのは、"
        "<b>低い位置で押し安値が確定した trail は、上昇の途中で当たってしまう</b>からである（Q8）。",
    ))

    out.append(qa(
        "8. trail 成立を増やす代わりに、正常な調整で早く降りる副作用は増えたか",
        "<b>明確に増えた。</b>trail STOP で降りたイベントのうち、"
        f"EXIT 後にさらに +3% 以上上昇したのは <b>{_all(metrics, '　うち EXIT 後さらに +3% 以上上昇')}</b>、"
        f"+10% 以上は <b>{_all(metrics, '　うち EXIT 後さらに +10% 以上上昇')}</b>、"
        f"EXIT 後に保有中最高値を更新したのは <b>{_all(metrics, '　うち EXIT 後に保有中最高値を更新')}</b>。"
        f"EXIT 後の上昇率の中央値は <b>{_all(metrics, '　EXIT 後の上昇率 中央値')}</b>。"
        "<b>trail 成立件数と早降りは同じ現象の裏表で、片方だけを増やすことはできていない。</b>"
        "「trail が立った」ことと「利益を確保できた」ことは別である。",
    ))

    out.append(qa(
        "9. STUCK_IN_WARNING / INITIAL_STOP_EXIT_AFTER_BREAKOUT は減ったか",
        f"STUCK_IN_WARNING は <b>{_all(metrics, 'STUCK_IN_WARNING')}</b>、"
        f"INITIAL_STOP_EXIT_AFTER_BREAKOUT は "
        f"<b>{_all(metrics, 'INITIAL_STOP_EXIT_AFTER_BREAKOUT')}</b>。"
        "<b>どちらも reference_high では動かない。</b>"
        "STUCK は warning_low を日中に割ってから終値で確定するまでの猶予で発生するもので、"
        "前回の検証で CLOSE_BREAK を採った時点でほぼ解消済み。"
        "上限突破後に初期STOPまで戻る件も、"
        "<b>そもそも reference_high に届かないから trail が立たない</b>のであって、"
        "水準を下げても大半は救えていない。",
    ))

    out.append(qa(
        "10. fractal 方式と比べて、押し安値をより自然なタイミングで確定できたか",
        "<b>「早い／遅い」ではなく「見ている安値が違う」。</b>"
        f"RH-A では今回の方法で {frac[sm.RH_HOLDING].ours_confirmed} 件の押し安値を確定したが、"
        f"fractal も同じ安値を認めたのは {frac[sm.RH_HOLDING].both_recognized} 件だけで、"
        f"{frac[sm.RH_HOLDING].ours_only} 件は今回のみ、"
        f"{frac[sm.RH_HOLDING].fractal_only} 件は fractal のみが認識した。"
        "案を緩めると今回側の確定は増える（"
        + " / ".join(f"{frac[r].ours_confirmed}" for r in rhs.RULES)
        + " 件）が、"
        "<b>一致件数はほとんど増えない</b>（"
        + " / ".join(f"{frac[r].both_recognized}" for r in rhs.RULES)
        + " 件）。"
        "確定日を比べられた件では今回が先／同日／fractal が先が "
        + " / ".join(
            f"{frac[r].ours_first}・{frac[r].same_day}・{frac[r].fractal_first}"
            for r in rhs.RULES
        )
        + "。<b>今回の方式は fractal より早いというより、"
        "fractal が拾わない安値を押し安値として採っている。</b>"
        "「自然な時系列判定になっているか」という問いには、"
        "<b>右側の足を待たずにその日で確定できる点は自然だが、"
        "採っている安値がチャート上の押し目と一致しているとは言い切れない</b>と答えるほかない。",
    ))

    out.append(qa(
        "11. 4 案の中に、チャート構造・運用方法の両面から明らかに不自然な案はあるか",
        "<b>RH-D（warning_open）は不自然である。</b>陰線の始値は"
        "「その日にたまたま寄った値」で、チャート構造として何かの節目ではない。"
        "同じ形の陰線でも寄りが高ければハードルが上がり、低ければ下がる、"
        "という<b>再現性のない基準</b>になる。実測でも最も緩く（"
        f"{_pv(position, '5 案の中で最も低かった: RH-D')}）、"
        "早降りの副作用が最大だった（Q6・Q8）。"
        "<br><br>"
        "<b>RH-E は不自然ではないが、RH-A とほとんど区別がつかない。</b>"
        f"RH-A より低くなるのは {_pv(position, 'RH-E 前日までの高値')} だけで、"
        "これは「警戒足自身が最高値を作った」場合に限られる。"
        "<b>切り分け用としては意味があったが、独立した案としての存在意義は薄い。</b>"
        "残る RH-A / RH-B / RH-C はどれも節目として説明でき、"
        "<b>この 3 つに明らかに不自然なものはない。</b>",
    ))

    out.append(qa(
        "12. 4 案の中に、正式ルール候補として次の統合検証へ持っていく価値がある案はあるか",
        "<b>ある。ただし「成績がよいから」ではない。</b>"
        "持っていく価値があるのは <b>RH-C（前日までの終値高値）</b>で、理由は 2 つ。"
        "(1) 現行の運用がレンジ上限突破も warning_low 割れも終値で判定しているのに、"
        "調整終了だけがヒゲ基準になっている<b>不整合を解消する</b>（Q5）。"
        "(2) RH-A より確実に低く（"
        f"{_pv(position, 'RH-C 前日までの終値高値')}）、"
        "かつ RH-D のように「その日の寄り値」という恣意的な水準ではない。"
        "<b>RH-A も候補から外せない。</b>今回の数字は RH-A を否定していない"
        "（Q7 のとおり利益保持はどの案でも変わらず、"
        "早降りの副作用は RH-A が最小）。"
        "<b>RH-D は外してよい</b>（Q11）。"
        "<b>RH-B / RH-E は RH-A との差が小さすぎて、単独で検証する価値が薄い。</b>"
        "<b>ただしこれは候補の絞り込みであって、採用の決定ではない。</b>",
    ))

    out.append(qa(
        "13. まだ reference_high の定義を追加で検証する必要があるか",
        "<b>reference_high の水準をこれ以上いじる必要はない。</b>"
        "5 案は ENTRY 比で中央値 "
        f"{_pv(position, '5 案の水準差')} のハードル差しかなく、"
        "その範囲では利益吐き出しが改善しないことが Q7 で確認できた。"
        "<b>ただし、水準ではない部分に未検証の論点が 2 つ残っている。</b>"
        "(1) <b>再突破の判定を <code>high</code> ではなく <code>close</code> にすること。</b>"
        "今回は §7 の指定どおり 5 案とも <code>high &gt; reference_high</code> で固定したので、"
        "この軸は一度も動かしていない。日足・引け後判断との整合という Q5 の論点は、"
        "<b>水準より判定側に効く可能性がある</b>。"
        "(2) <b>押し安値の取り方そのもの。</b>Q8 のとおり trail が立っても利益が残らないのは、"
        "押し安値が WARNING 期間の最安値（＝直近の投げ）に張り付くためで、"
        "これは reference_high ではなく押し安値側の問題である。"
        "<b>いずれも今回の指示の範囲外なので、実装も検証もしていない。</b>",
    ))

    out.append(qa(
        "14. それとも次の「EXIT統合検証」へ進める段階か",
        "<b>reference_high 単独の比較としては、これで材料は出そろっている。</b>"
        "5 案の性質・自然さ・副作用は Q1〜Q11 のとおり整理できた。"
        "<b>ただし、統合検証に入る前に決めておくべきことが 2 つある。</b>"
        "(1) <b>同日に REHIGH と利確候補が両立した日の扱い（§7）。</b>"
        f"順序不明の日は案ごとに {' / '.join(str(s.ambiguous_days) for s in sens)} 日あり、"
        "<b>その日を含むイベントは全件、順序次第で結果が変わった</b>。"
        "全体の中央値はほとんど動かないが、これはルールとして先に決めておかないと"
        "統合検証の結果が再現しない。"
        "(2) <b>Q13 の (1)（判定を close にするか）。</b>"
        "reference_high の水準と判定方法を同時に動かすと、また原因が分からなくなる。"
        "<br><br>"
        "<b>この検証では正式ルールの変更を行っていない。</b>"
        "どの案を採用するかも決めていない。",
    ))

    return "".join(out)


# --- 11. 未確定項目 / 出力ファイル ---------------------------------------------

OPEN_ITEMS = [
    ("どの reference_high を正式ルールにするか",
     "決めていない。5 案の性質と副作用を整理しただけ。"
     "trail 成立件数や仮想利益で自動採用はしない（§22）。"),
    ("同日に REHIGH と利確候補が両立した日の優先順位",
     "決めていない。AMBIGUOUS_REHIGH_EXIT_ORDER として分離し、"
     "REHIGH 優先 / EXIT 優先の両方を走らせて差分を示すに留めた（§7）。"),
    ("再突破の判定を high にするか close にするか",
     "今回は §7 の指定どおり 5 案とも high > reference_high で固定した。"
     "この軸は一度も動かしていない。"),
    ("押し安値の取り方",
     "変更していない。min(low) 警戒足〜再突破日のまま。"
     "trail が立っても利益が残らない原因はここにある可能性が高いが、今回の範囲外。"),
    ("WARNING 開始条件（VARIANT A/B/C）",
     "VARIANT A に固定した。研究上の基準であって正式採用ではない。"),
    ("warning_low 割れ後の処理（LOW/CLOSE/STRUCTURAL）",
     "CLOSE_BREAK に固定した。研究上の基準であって正式採用ではない。"),
    ("固定利確・新しい%閾値", "導入も探索もしていない。"),
    ("本番設定・本番スクリーナー",
     "変更していない。config.yaml / experimental.yaml / screener.py には書き込んでいない。"),
]

OUTPUT_FILES = [
    ("report.html", "このレポート"),
    ("summary.csv", "案ごとの 1 行サマリ"),
    ("events.csv", "イベント × 案 の全列（状態機械の標準列 + 今回の追加列）"),
    ("variant_comparison.csv", "§11〜§14・§21-9 の横並び指標"),
    ("rehigh_events.csv",
     "警戒足ごとの 5 案の reference_high 水準と決着（§11 / §15 の生データ）"),
    ("stop_updates.csv", "trail stop の引き上げ履歴（§12。翌営業日から有効）"),
    ("ambiguous_events.csv",
     "同日に REHIGH と利確候補が両立した警戒足だけを抜いたもの（§7）"),
    ("position_relations.csv", "§15 各案が実際どの程度違うハードルだったか"),
    ("early_trail_cases.csv", "§14 trail STOP で降りた後さらに上昇したケース"),
    ("fractal_comparison.csv", "§19 既存 fractal との参考比較"),
    ("ambiguity_sensitivity.csv", "§7 同日順序を逆にしたときの差分"),
    ("case_matrix.csv", "イベント × 案 の結果一覧（代表チャートの選定材料）"),
    ("representative_charts/", "§16 の代表チャート（12 カテゴリ）"),
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
    runs: dict[str, rhs.RHRun],
    frames: dict[rhs.EventKey, rhs.Frame],
    metrics: list[rhs.MetricRow],
    position: list[rhs.PositionRow],
    early: list[rhs.EarlyTrailCase],
    fractal: list[rhs.FractalRow],
    sens: list[rhs.AmbiguitySensitivity],
    chart_map: dict,
    out_dir: Path,
    *,
    period: tuple[str, str],
    threshold: float = 0.65,
) -> Path:
    base = runs[sm.RH_HOLDING]
    entered = base.entered
    body = f"""
<h1>reference_high の決め方の比較検証（RH-A / B / C / D / E）</h1>
<div class="sub">
検証期間 {_e(period[0])} 〜 {_e(period[1])} ／
対象 <code>near.max_position_in_range = {threshold}</code> で発生した ENTRY_CANDIDATE
{len(base.events)} 件（仮想ENTRY成立 {len(entered)} 件）× 5 案 ／
WARNING 開始条件は VARIANT A、<code>warning_low</code> 割れ後は CLOSE_BREAK に固定 ／
生成日 {date.today().isoformat()}
</div>
{DISCLAIMER}

<h2>1. 比較する 5 案</h2>
{_definition_section()}

<h2>2. §11 REHIGH の発生状況</h2>
<div class="ref">「WARNING 発生件数」は案によって変わる。
REHIGH すると WARNING を抜けて TREND_HOLD に戻り、そこからまた次の陰線が
警戒足になるため、<b>緩い案ほど警戒足の総数も増える</b>。
割合を読むときは分母が案ごとに違うことに注意。</div>
{_comparison_table(metrics, ("§11",))}

<h2>3. §12 trail stop の成立</h2>
{_comparison_table(metrics, ("§12",))}

<h2>4. §13 利益の吐き出し</h2>
<div class="ref">「追跡窓の最大含み益」が <b>5 案共通の分母</b>。
案ごとの <code>max_gain_pct</code> は EXIT 日までしか見ないので、
案が違うと分母まで変わってしまい「利益をどれだけ残せたか」を比較できない。
ここでは ENTRY から追跡終端までにその銘柄が見せた最大含み益を使う。</div>
{_comparison_table(metrics, ("§13",))}

<h2>5. §14 早すぎる trail の副作用</h2>
<div class="ref"><b>trail 成立が増えれば良いわけではない。</b>
ここは §12 と表裏で、<b>どちらか一方だけを見て解釈を決めない。</b>
EXIT 後の窓は追跡終端までで、5 案とも同じ物差しで測っている。</div>
{_comparison_table(metrics, ("§14",))}
{_early_trail_section(early)}

<h2>6. §15 reference_high 同士の位置関係</h2>
<div class="ref">母集団は <b>RH-A の警戒足に固定</b>している。
案が違うと REHIGH の有無が変わり、その後に出る警戒足も変わってしまうので、
「同じ警戒足の上で 5 案を並べる」には母集団を 1 つに決める必要がある。</div>
{_position_section(position)}

<h2>7. §7 同日に REHIGH と利確候補が両立した日</h2>
{_sensitivity_section(sens)}

<h2>8. §19 既存 fractal との参考比較</h2>
{_fractal_section(fractal)}

<h2>9. §21-9 STUCK / 初期STOP / EXIT 種別</h2>
{_comparison_table(metrics, ("§21-9", "§7"))}

<h2>10. イベント別の並び</h2>
<p class="muted">「追跡窓の最大含み益」は 5 案共通の分母。
セル内は 仮想EXITリターン / EXIT 種別 / STOP 引き上げ回数。</p>
{_event_matrix(runs, frames)}

<h2>11. §16 代表チャート</h2>
<p class="muted">1 枚に 5 案を重ねている。
灰 = RH-A 保有中最高値 / 赤 = RH-B 警戒足高値 / 青 = RH-C 前日までの終値高値 /
緑 = RH-D 警戒足始値 / 黄土 = RH-E 前日までの高値。
5 本の水平線が各案の <code>reference_high</code>、
▲ が REHIGH、＿ が押し安値候補、◆ が STOP 引き上げ。</p>
{_charts_section(chart_map, out_dir)}

<h2>12. §18 look-ahead bias</h2>
{_lookahead_section()}

<h2>13. §21 の 14 の問いへの回答</h2>
{_answers_section(runs, frames, metrics, position, fractal, sens, early)}

<h2>14. 未確定のまま残した項目</h2>
<div class="ref">以下は<b>この検証では決めなかった</b>項目である。
32 件への当てはめで決めると過剰最適化になるため、観察結果を材料として
提示するに留める。<b>正式なルール変更は行っていない。</b></div>
{_open_items_table()}

<h2>15. 出力ファイル</h2>
{_output_files_table()}
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>reference_high の決め方の比較</title>"
        f"<style>{CSS}{EXTRA_CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
