r"""모의 장부 대시보드 (정적 HTML 1장) — run_daily.py 가 매번 호출

build(state, snap, k200_close, out_dir) -> out_dir/index.html
  state: state/paper.json 내용 (equity_log, positions, pending, history ...)
  snap : 오늘 실행 결과 {asof, mkt_on, rows, new_orders, log, last_run}
따로 실행: python dashboard.py  (state/paper.json + state/last_snapshot.json 으로 다시 그림)
"""
from pathlib import Path
import json, html
import datetime as dt
import pandas as pd

HERE = Path(__file__).parent
# 백테스트(2016~2026, 형님 v2 + 시장·종목 필터)에서 나온 기대치 — 실전 성과 비교용
EXPECT = {"승률%": 37.5, "거래당 평균%": 0.34, "PF": 1.11, "평균 보유일": 8.0}


def _fmt(x, nd=0):
    return f"{x:,.{nd}f}"


def health(state, snap, today=None):
    """(등급, 항목, 내용) 목록. 등급: good / warning / critical"""
    out = []
    today = today or pd.Timestamp(dt.date.today())
    asof = pd.Timestamp(snap["asof"])
    lag = len(pd.bdate_range(asof, today)) - 1
    out.append(("good" if lag <= 1 else "warning" if lag <= 3 else "critical", "데이터 최신성",
                f"기준일 {asof:%Y-%m-%d} (영업일 {lag}일 전)"))
    old = [o for o in state["pending"] if len(pd.bdate_range(o["created"], asof)) > 3]
    out.append(("good" if not old else "warning", "미체결 대기주문",
                f"{len(state['pending'])}건" + (f", 3영업일 넘은 것 {len(old)}건" if old else "")))
    out.append(("good" if state["cash"] >= 0 else "critical", "현금", f"{_fmt(state['cash'])}원"))
    err = [r for r in snap["rows"] if r.get("메모")]
    out.append(("good" if not err else "warning", "종목 데이터 오류", f"{len(err)}종목"))
    h = pd.DataFrame(state["history"])
    if len(h) >= 20:
        w = h["ret"]
        pf = w[w > 0].sum() / max(-w[w < 0].sum(), 1e-9)
        out.append(("good" if pf >= 1 else "warning" if pf >= 0.8 else "critical", "실전 수익팩터(PF)",
                    f"{pf:.2f} (백테스트 기대 {EXPECT['PF']})"))
    else:
        out.append(("good", "실전 수익팩터(PF)", f"청산 {len(h)}건 — 20건 이상부터 판단"))
    return out


def build(state, snap, k200_close, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    eq = pd.DataFrame(state.get("equity_log", []))
    series = []
    if len(eq):
        eq["date"] = pd.to_datetime(eq["date"])
        eq = eq.drop_duplicates("date", keep="last").set_index("date")
        k = k200_close.reindex(eq.index, method="ffill")
        base_e, base_k = eq["equity"].iloc[0], k.iloc[0]
        series = [{"d": f"{d:%Y-%m-%d}", "a": round(100 * e / base_e, 2), "b": round(100 * kk / base_k, 2)}
                  for d, e, kk in zip(eq.index, eq["equity"], k)]
    equity = eq["equity"].iloc[-1] if len(eq) else state["cash"]
    ret = 100 * (equity / state["capital0"] - 1)
    bm = series[-1]["b"] - 100 if series else 0.0
    h = pd.DataFrame(state["history"])
    live = {}
    if len(h):
        w = h["ret"]
        live = {"승률%": round(100 * (w > 0).mean(), 1), "거래당 평균%": round(100 * w.mean(), 2),
                "PF": round(w[w > 0].sum() / max(-w[w < 0].sum(), 1e-9), 2),
                "평균 보유일": round(pd.Series([len(pd.bdate_range(a, b)) - 1 for a, b in zip(h["opened"], h["closed"])]).mean(), 1)}
    hl = health(state, snap)
    icon = {"good": "✓", "warning": "!", "critical": "✕"}
    label = {"good": "정상", "warning": "주의", "critical": "문제"}

    def table(cols, rows):
        if not rows:
            return '<p class="muted">없음</p>'
        th = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        tb = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r) + "</tr>" for r in rows)
        return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>'

    last_px = {r["코드"]: r.get("종가") for r in snap["rows"]}
    pos_rows = []
    for c, p in state["positions"].items():
        avg = p["cost"] / p["qty"]
        cur = last_px.get(c) or avg
        pos_rows.append([p["name"], c, _fmt(p["qty"]), _fmt(avg), _fmt(cur), f"{100*(cur/avg-1):+.1f}%",
                         f"{p['units']}/3", p["opened"]])
    sig_rows = [[r["종목"], r["코드"], _fmt(r.get("종가") or 0), r.get("%K", ""), r.get("%D", ""), r["신호"]]
                for r in snap["rows"] if r.get("신호")]
    pend_rows = [["매수" if o["side"] == "buy" else "매도", o["name"], o["code"], o["why"],
                  _fmt(o["amount"]) + "원" if o.get("amount") else "전량", o["created"]] for o in state["pending"]]
    hist_rows = [[x["name"], x["code"], x["opened"], x["closed"], f"{x['units']}회", f"{100*x['ret']:+.2f}%"]
                 for x in reversed(state["history"][-30:])]
    cmp_rows = [[k, v, live.get(k, "-")] for k, v in EXPECT.items()]
    health_html = "".join(f'<li class="st {g}"><span class="ic" aria-hidden="true">{icon[g]}</span>'
                          f'<b>{label[g]}</b> {html.escape(n)} — {html.escape(d)}</li>' for g, n, d in hl)

    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>자동매매 대시보드</title>
<style>
:root{{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e4e3df;
--s1:#2a78d6;--s2:#eb6834;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b}}
@media (prefers-color-scheme:dark){{:root{{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;
--line:#383835;--s1:#3987e5;--s2:#d95926}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 "Pretendard","맑은 고딕",system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 48px}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:28px 0 10px}}
.muted{{color:var(--muted)}}.sub{{color:var(--ink2);margin:0 0 20px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px}}
.tile .k{{color:var(--ink2);font-size:12px}}.tile .v{{font-size:22px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px}}
.legend{{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin-bottom:6px}}.sw{{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:6px}}
svg text{{fill:var(--muted);font-size:11px}}.tw{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--ink2);font-weight:600;font-size:12px}}ul.hl{{list-style:none;padding:0;margin:0;display:grid;gap:6px}}
.st .ic{{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;color:#fff;font-size:11px;margin-right:6px}}
.good .ic{{background:var(--good)}}.warning .ic{{background:var(--warn);color:#000}}.critical .ic{{background:var(--crit)}}
#tip{{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;display:none;box-shadow:0 2px 8px #0002}}
</style></head><body><main>
<h1>주식 자동매매 — 모의 장부</h1>
<p class="sub">형님 스토캐스틱 v2 · 일봉 · 기준일 {snap['asof']} · 마지막 실행 {snap.get('last_run','')}</p>
<div class="tiles">
<div class="tile"><div class="k">평가자산</div><div class="v">{_fmt(equity)}원</div></div>
<div class="tile"><div class="k">누적 수익률</div><div class="v">{ret:+.2f}%</div></div>
<div class="tile"><div class="k">같은 기간 KODEX 200</div><div class="v">{bm:+.2f}%</div></div>
<div class="tile"><div class="k">보유 / 최대</div><div class="v">{len(state['positions'])} / 10</div></div>
<div class="tile"><div class="k">시장 필터</div><div class="v">{'ON · 매수 가능' if snap['mkt_on'] else 'OFF · 신규매수 중지'}</div></div>
</div>
<h2>점검</h2><div class="card"><ul class="hl">{health_html}</ul></div>
<h2>계좌 vs KODEX 200 (시작 = 100)</h2>
<div class="card"><div class="legend"><span><i class="sw" style="background:var(--s1)"></i>모의 계좌</span>
<span><i class="sw" style="background:var(--s2)"></i>KODEX 200</span></div><div id="chart"></div></div>
<h2>보유 종목</h2>{table(["종목","코드","수량","평단","현재가","손익","매수횟수","진입일"], pos_rows)}
<h2>내일 시가 주문(대기)</h2>{table(["구분","종목","코드","사유","금액","생성일"], pend_rows)}
<h2>오늘 신호</h2>{table(["종목","코드","종가","%K","%D","신호"], sig_rows)}
<h2>실전 vs 백테스트 기대치</h2>{table(["지표","백테스트","모의 실적"], cmp_rows)}
<h2>최근 청산 (최대 30건)</h2>{table(["종목","코드","진입","청산","매수횟수","수익률"], hist_rows)}
<p class="muted" style="margin-top:28px">모의 장부(실제 주문 아님). 신호는 종가 판단, 다음 날 시가 체결 가정. 투자 권유 아님.</p>
</main><div id="tip"></div>
<script>
const D={json.dumps(series)};
(function(){{const el=document.getElementById('chart');if(D.length<2){{el.innerHTML='<p class="muted">2일 이상 쌓이면 그려집니다.</p>';return;}}
const W=el.clientWidth||900,H=280,L=44,R=12,T=10,B=26;const v=D.flatMap(p=>[p.a,p.b]);let lo=Math.min(...v),hi=Math.max(...v);const pad=(hi-lo)*.08||1;lo-=pad;hi+=pad;
const x=i=>L+(W-L-R)*i/(D.length-1),y=t=>T+(H-T-B)*(1-(t-lo)/(hi-lo));const path=k=>D.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(p[k]).toFixed(1)).join('');
let g='';for(let j=0;j<=4;j++){{const t=lo+(hi-lo)*j/4;g+=`<line x1="${{L}}" x2="${{W-R}}" y1="${{y(t)}}" y2="${{y(t)}}" stroke="var(--line)"/><text x="${{L-6}}" y="${{y(t)+4}}" text-anchor="end">${{t.toFixed(0)}}</text>`;}}
const n=D.length,ti=[0,Math.floor(n/2),n-1];ti.forEach(i=>g+=`<text x="${{x(i)}}" y="${{H-6}}" text-anchor="middle">${{D[i].d}}</text>`);
el.innerHTML=`<svg width="${{W}}" height="${{H}}" role="img" aria-label="모의 계좌와 KODEX200 지수화 추이">${{g}}<line x1="${{L}}" x2="${{W-R}}" y1="${{y(100)}}" y2="${{y(100)}}" stroke="var(--muted)" stroke-dasharray="3 3"/>
<path d="${{path('b')}}" fill="none" stroke="var(--s2)" stroke-width="2"/><path d="${{path('a')}}" fill="none" stroke="var(--s1)" stroke-width="2"/>
<line id="cx" y1="${{T}}" y2="${{H-B}}" stroke="var(--muted)" visibility="hidden"/><rect x="${{L}}" y="${{T}}" width="${{W-L-R}}" height="${{H-T-B}}" fill="transparent" id="hit"/></svg>`;
const tip=document.getElementById('tip'),cx=el.querySelector('#cx');el.querySelector('#hit').addEventListener('mousemove',e=>{{const r=e.target.getBoundingClientRect();
const i=Math.max(0,Math.min(n-1,Math.round((e.clientX-r.left)/(r.width)*(n-1))));const p=D[i];cx.setAttribute('x1',x(i));cx.setAttribute('x2',x(i));cx.setAttribute('visibility','visible');
tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';tip.innerHTML=`<b>${{p.d}}</b><br>모의 계좌 ${{p.a.toFixed(1)}}<br>KODEX 200 ${{p.b.toFixed(1)}}`;}});
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
