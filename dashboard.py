r"""모의 장부 대시보드 (정적 HTML 1장) — run_daily.py 가 매번 호출

build(state, snap, k200_close, out_dir) -> out_dir/index.html
  state: state/paper.json 내용 (equity_log, positions, pending, history ...)
  snap : 오늘 실행 결과 {asof, mkt_on, rows, new_orders, log, last_run, ...}
따로 실행: python dashboard.py  (state/paper.json + state/paper_snapshot.json 으로 다시 그림)
"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
# 백테스트(2016~2026, 시총상위200 v5 E3+RS60) 기대치 — 실전 성과 비교용
EXPECT = {"승률%": 38.1, "거래당 평균%": 1.27, "PF": 1.42, "평균 보유일": 11.1}


def _fmt(x, nd=0):
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def health(state: dict, snap: dict, today=None):
    """(등급, 항목, 내용) 목록. 등급: good / warning / critical"""
    out = []
    if snap.get("kill"):
        out.append(("critical", "긴급 정지(KILL_SWITCH)", f"{snap['kill']} — 주문 중지 중. 확인 후 파일 삭제로 해제"))
    today = today or pd.Timestamp(dt.date.today())
    asof = pd.Timestamp(snap["asof"])
    lag = max(len(pd.bdate_range(asof, today)) - 1, 0)
    out.append((
        "good" if lag <= 1 else "warning" if lag <= 3 else "critical",
        "데이터 최신성",
        f"기준일 {asof:%Y-%m-%d} (영업일 {lag}일 전)",
    ))
    old = [o for o in state.get("pending", []) if len(pd.bdate_range(o["created"], asof)) > 3]
    out.append((
        "good" if not old else "warning",
        "미체결 대기주문",
        f"{len(state.get('pending', []))}건" + (f", 3영업일 넘은 것 {len(old)}건" if old else ""),
    ))
    out.append(("good" if state["cash"] >= 0 else "critical", "현금", f"{_fmt(state['cash'])}원"))
    err = [r for r in snap.get("rows", []) if r.get("메모")]
    out.append(("good" if not err else "warning", "종목 데이터 오류", f"{len(err)}종목"))
    expect = snap.get("expect") or EXPECT
    h = pd.DataFrame(state.get("history", []))
    if len(h) >= 20:
        w = h["ret"]
        pf = w[w > 0].sum() / max(-w[w < 0].sum(), 1e-9)
        out.append((
            "good" if pf >= 1 else "warning" if pf >= 0.8 else "critical",
            "실전 수익팩터(PF)",
            f"{pf:.2f} (백테스트 기대 {expect['PF']})",
        ))
    else:
        out.append(("good", "실전 수익팩터(PF)", f"청산 {len(h)}건 — 20건 이상부터 판단"))
    return out


def build(state: dict, snap: dict, k200_close: pd.Series, out_dir: Path | str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_pos = int(snap.get("max_positions", 10))
    units = int(snap.get("units", 3))
    exit_mode = snap.get("exit_mode", "E3")
    rank_by = snap.get("rank_by", "rs60")
    expect = snap.get("expect") or EXPECT

    eq = pd.DataFrame(state.get("equity_log", []))
    series = []
    cur_dd = 0.0
    mdd = 0.0
    if len(eq):
        eq["date"] = pd.to_datetime(eq["date"])
        eq = eq.drop_duplicates("date", keep="last").set_index("date").sort_index()
        k = k200_close.reindex(eq.index, method="ffill") if len(k200_close) else pd.Series(1.0, index=eq.index)
        base_e = float(eq["equity"].iloc[0]) or 1.0
        base_k = float(k.iloc[0]) if pd.notna(k.iloc[0]) and float(k.iloc[0]) != 0 else 1.0
        dd_s = 100 * (eq["equity"] / eq["equity"].cummax() - 1)
        kdd_s = 100 * (k / k.cummax() - 1)
        cur_dd = float(dd_s.iloc[-1])
        mdd = float(dd_s.min())
        series = [
            {
                "d": f"{d:%Y-%m-%d}",
                "a": round(100 * float(e) / base_e, 2),
                "b": round(100 * float(kk) / base_k, 2) if pd.notna(kk) else 100.0,
                "dd": round(float(dd_v), 2),
                "kdd": round(float(kdd_v), 2) if pd.notna(kdd_v) else 0.0,
            }
            for d, e, kk, dd_v, kdd_v in zip(eq.index, eq["equity"], k, dd_s, kdd_s)
        ]

    equity = float(eq["equity"].iloc[-1]) if len(eq) else float(state["cash"])
    cap0 = float(state.get("capital0") or equity or 1.0)
    ret = 100 * (equity / cap0 - 1)
    bm = series[-1]["b"] - 100 if series else 0.0
    alpha = ret - bm
    inv_pct = 100 * max(equity - float(state["cash"]), 0.0) / equity if equity > 0 else 0.0

    h = pd.DataFrame(state.get("history", []))
    live = {}
    if len(h):
        w = h["ret"]
        live = {
            "승률%": round(100 * (w > 0).mean(), 1),
            "거래당 평균%": round(100 * w.mean(), 2),
            "PF": round(w[w > 0].sum() / max(-w[w < 0].sum(), 1e-9), 2),
            "평균 보유일": round(
                pd.Series([max(len(pd.bdate_range(a, b)) - 1, 0) for a, b in zip(h["opened"], h["closed"])]).mean(),
                1,
            ),
        }
    hl = health(state, snap)
    icon = {"good": "✓", "warning": "!", "critical": "✕"}
    label = {"good": "정상", "warning": "주의", "critical": "문제"}

    def table(cols, rows):
        if not rows:
            return '<p class="muted">없음</p>'
        th = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        tb = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r) + "</tr>" for r in rows)
        return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>'

    row_by_code = {r["코드"]: r for r in snap.get("rows", []) if r.get("코드")}
    asof_ts = pd.Timestamp(snap["asof"])
    pos_rows = []
    for c, p in state.get("positions", {}).items():
        avg = p["cost"] / max(p["qty"], 1)
        rr = row_by_code.get(c, {})
        cur = rr.get("종가") or p.get("last_price") or avg
        held_d = rr.get("보유일")
        if held_d is None:
            held_d = max(len(pd.bdate_range(p["opened"], asof_ts)) - 1, 0)
        kd_str = f"{rr['%K']} / {rr['%D']}" if rr.get("%K") is not None else "-"
        pos_rows.append([
            p["name"], c, _fmt(p["qty"]), _fmt(avg), _fmt(cur), f"{100*(cur/avg-1):+.1f}%",
            f"{p['units']}/{units}", p["opened"], f"{held_d}일", kd_str, rr.get("청산상태") or "-",
        ])

    sig_rows = [
        [r["종목"], r["코드"], _fmt(r.get("종가") or 0), r.get("%K", ""), r.get("%D", ""),
         r.get("RS60") if r.get("RS60") is not None else "-", r["신호"]]
        for r in snap.get("rows", []) if r.get("신호")
    ]

    radar_cands = []
    for r in snap.get("rows", []):
        k_val = r.get("%K")
        if k_val is not None and not r.get("메모"):
            try:
                if float(k_val) < 30:
                    radar_cands.append(r)
            except (TypeError, ValueError):
                pass
    radar_cands.sort(key=lambda r: float(r.get("%K", 999)))
    radar_rows = [
        [
            r["종목"],
            r["코드"],
            _fmt(r.get("종가") or 0),
            r.get("%K", ""),
            r.get("%D", ""),
            "✓" if r.get("주봉OK") else "-",
            "✓" if r.get("추세OK") else "-",
            r.get("RS60") if r.get("RS60") is not None else "-",
            r.get("신호") or ("보유 중" if r.get("보유") else "대기"),
        ]
        for r in radar_cands[:20]
    ]

    pend_rows = [
        [
            "매수" if o["side"] == "buy" else "매도",
            o["name"],
            o["code"],
            o["why"],
            _fmt(o["amount"]) + "원" if o.get("amount") else "전량",
            o["created"],
        ]
        for o in state.get("pending", [])
    ]
    hist_rows = [
        [
            x["name"],
            x["code"],
            x["opened"],
            x["closed"],
            f"{x['units']}회",
            f"{100*x['ret']:+.2f}%",
            x.get("why") or "-",
        ]
        for x in reversed(state.get("history", [])[-30:])
    ]
    cmp_rows = [[k, v, live.get(k, "-")] for k, v in expect.items()]
    health_html = "".join(
        f'<li class="st {g}"><span class="ic" aria-hidden="true">{icon[g]}</span>'
        f'<b>{label[g]}</b> {html.escape(n)} — {html.escape(d)}</li>'
        for g, n, d in hl
    )
    log_items = snap.get("log") or []
    log_html = (
        '<ul class="log">' + "".join(f"<li>{html.escape(str(x))}</li>" for x in log_items) + "</ul>"
        if log_items
        else '<p class="muted">오늘 체결·특이사항 없음</p>'
    )

    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>주식 자동매매 대시보드</title>
<style>
:root{{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e4e3df;
--s1:#2a78d6;--s2:#eb6834;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b}}
@media (prefers-color-scheme:dark){{:root{{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;
--line:#383835;--s1:#3987e5;--s2:#d95926}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 "Pretendard","맑은 고딕",system-ui,sans-serif}}
main{{max-width:1140px;margin:0 auto;padding:24px 16px 48px}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:28px 0 10px}}
.muted{{color:var(--muted)}}.sub{{color:var(--ink2);margin:0 0 20px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px}}
.tile .k{{color:var(--ink2);font-size:12px}}.tile .v{{font-size:20px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px}}
.legend{{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin-bottom:6px;flex-wrap:wrap}}.sw{{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:6px}}
svg text{{fill:var(--muted);font-size:11px}}.tw{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--ink2);font-weight:600;font-size:12px}}ul.hl,ul.log{{list-style:none;padding:0;margin:0;display:grid;gap:6px}}
.st .ic{{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;color:#fff;font-size:11px;margin-right:6px}}
.good .ic{{background:var(--good)}}.warning .ic{{background:var(--warn);color:#000}}.critical .ic{{background:var(--crit)}}
#tip{{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;display:none;box-shadow:0 2px 8px #0002}}
</style></head><body><main>
<h1>주식 자동매매 — 모의 장부</h1>
<p class="sub">스토캐스틱 v5 ({html.escape(exit_mode)} · {html.escape(rank_by)} · {units}분할) · 일봉 · 모드 {html.escape(str(snap.get('mode', 'paper')))} · 기준일 {html.escape(str(snap['asof']))} · 마지막 실행 {html.escape(str(snap.get('last_run','')))}</p>
<div class="tiles">
<div class="tile"><div class="k">평가자산</div><div class="v">{_fmt(equity)}원</div></div>
<div class="tile"><div class="k">누적 수익률</div><div class="v">{ret:+.2f}%</div></div>
<div class="tile"><div class="k">같은 기간 KODEX 200</div><div class="v">{bm:+.2f}%</div></div>
<div class="tile"><div class="k">시장 대비 초과수익</div><div class="v">{alpha:+.2f}%p</div></div>
<div class="tile"><div class="k">현재 / 최대 낙폭</div><div class="v">{cur_dd:.1f}% / {mdd:.1f}%</div></div>
<div class="tile"><div class="k">투자비중 (주식)</div><div class="v">{inv_pct:.1f}%</div></div>
<div class="tile"><div class="k">보유 / 최대</div><div class="v">{len(state.get('positions', {}))} / {max_pos}</div></div>
<div class="tile"><div class="k">시장 필터</div><div class="v">{'ON · 매수 가능' if snap['mkt_on'] else 'OFF · 신규매수 중지'}</div></div>
</div>
<h2>점검</h2><div class="card"><ul class="hl">{health_html}</ul></div>
<h2>계좌 vs KODEX 200 (시작 = 100) · 낙폭(Drawdown)</h2>
<div class="card"><div class="legend"><span><i class="sw" style="background:var(--s1)"></i>모의 계좌</span>
<span><i class="sw" style="background:var(--s2)"></i>KODEX 200</span></div><div id="chart"></div></div>
<h2>보유 종목</h2>{table(["종목","코드","수량","평단","현재가","손익","매수횟수","진입일","보유일","%K/%D","추적상태"], pos_rows)}
<h2>내일 시가 주문(대기)</h2>{table(["구분","종목","코드","사유","금액","생성일"], pend_rows)}
<h2>오늘 신호</h2>{table(["종목","코드","종가","%K","%D","RS60","신호"], sig_rows)}
<h2>매수 임박 관심종목 (%K &lt; 30)</h2>{table(["종목","코드","종가","%K","%D","주봉OK","추세OK","RS60","상태"], radar_rows)}
<h2>실전 vs 백테스트 기대치</h2>{table(["지표","백테스트","모의 실적"], cmp_rows)}
<h2>최근 청산 (최대 30건)</h2>{table(["종목","코드","진입","청산","매수횟수","수익률","사유"], hist_rows)}
<h2>오늘 실행 로그</h2><div class="card">{log_html}</div>
<p class="muted" style="margin-top:28px">모의 장부(실제 주문 아님). 신호는 종가 판단, 다음 날 시가 체결 가정. 투자 권유 아님.</p>
</main><div id="tip"></div>
<script>
const D={json.dumps(series)};
(function(){{const el=document.getElementById('chart');if(D.length<2){{el.innerHTML='<p class="muted">2일 이상 쌓이면 그려집니다.</p>';return;}}
const W=el.clientWidth||900,H1=230,GAP=26,H2=90,H=H1+GAP+H2,L=48,R=12,T=10,B=24;
const v=D.flatMap(p=>[p.a,p.b]);let lo=Math.min(...v),hi=Math.max(...v);const pad=(hi-lo)*.08||1;lo-=pad;hi+=pad;
const dds=D.flatMap(p=>[p.dd,p.kdd]);const dlo=Math.min(-2,Math.min(...dds)*1.1);
const x=i=>L+(W-L-R)*i/(D.length-1),y1=t=>T+(H1-T)*(1-(t-lo)/(hi-lo)),y2=t=>H1+GAP+(H2-B)*(1-(t-dlo)/(0-dlo));
const path=(k,yf)=>D.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+yf(p[k]).toFixed(1)).join('');
let g='';for(let j=0;j<=4;j++){{const t=lo+(hi-lo)*j/4;g+=`<line x1="${{L}}" x2="${{W-R}}" y1="${{y1(t)}}" y2="${{y1(t)}}" stroke="var(--line)"/><text x="${{L-6}}" y="${{y1(t)+4}}" text-anchor="end">${{t.toFixed(0)}}</text>`;}}
[0,dlo/2,dlo].forEach(t=>g+=`<line x1="${{L}}" x2="${{W-R}}" y1="${{y2(t)}}" y2="${{y2(t)}}" stroke="var(--line)"/><text x="${{L-6}}" y="${{y2(t)+4}}" text-anchor="end">${{t.toFixed(0)}}%</text>`);
const n=D.length,ti=[0,Math.floor(n/2),n-1];ti.forEach(i=>g+=`<text x="${{x(i)}}" y="${{H-4}}" text-anchor="middle">${{D[i].d}}</text>`);
const area=`M${{x(0).toFixed(1)}} ${{y2(0).toFixed(1)}} `+D.map((p,i)=>`L${{x(i).toFixed(1)}} ${{y2(p.dd).toFixed(1)}}`).join(' ')+` L${{x(n-1).toFixed(1)}} ${{y2(0).toFixed(1)}} Z`;
el.innerHTML=`<svg width="${{W}}" height="${{H}}" role="img" aria-label="모의 계좌와 KODEX200 추이 및 낙폭">${{g}}<line x1="${{L}}" x2="${{W-R}}" y1="${{y1(100)}}" y2="${{y1(100)}}" stroke="var(--muted)" stroke-dasharray="3 3"/>
<path d="${{path('b',y1)}}" fill="none" stroke="var(--s2)" stroke-width="2"/><path d="${{path('a',y1)}}" fill="none" stroke="var(--s1)" stroke-width="2"/>
<path d="${{area}}" fill="var(--s1)" opacity="0.14"/><path d="${{path('kdd',y2)}}" fill="none" stroke="var(--s2)" stroke-width="1.2" opacity="0.7"/><path d="${{path('dd',y2)}}" fill="none" stroke="var(--s1)" stroke-width="1.6"/>
<line id="cx" y1="${{T}}" y2="${{H-B}}" stroke="var(--muted)" visibility="hidden"/><rect x="${{L}}" y="${{T}}" width="${{W-L-R}}" height="${{H-T-B}}" fill="transparent" id="hit"/></svg>`;
const tip=document.getElementById('tip'),cx=el.querySelector('#cx');el.querySelector('#hit').addEventListener('mousemove',e=>{{const r=e.target.getBoundingClientRect();
const i=Math.max(0,Math.min(n-1,Math.round((e.clientX-r.left)/(r.width)*(n-1))));const p=D[i];cx.setAttribute('x1',x(i));cx.setAttribute('x2',x(i));cx.setAttribute('visibility','visible');
tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';tip.innerHTML=`<b>${{p.d}}</b><br>모의 계좌 ${{p.a.toFixed(1)}} (DD ${{p.dd.toFixed(1)}}%)<br>KODEX 200 ${{p.b.toFixed(1)}} (DD ${{p.kdd.toFixed(1)}}%)`;}});
el.querySelector('#hit').addEventListener('mouseleave',()=>{{tip.style.display='none';cx.setAttribute('visibility','hidden');}});}})();
</script></body></html>"""
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    return out_dir / "index.html"


if __name__ == "__main__":
    import FinanceDataReader as fdr
    st = json.loads((HERE / "state" / "paper.json").read_text(encoding="utf-8"))
    sn = json.loads((HERE / "state" / "paper_snapshot.json").read_text(encoding="utf-8"))
    k = fdr.DataReader("069500", (dt.date.today() - dt.timedelta(days=800)).isoformat())["Close"]
    print(build(st, sn, k, HERE / "dashboard"))
