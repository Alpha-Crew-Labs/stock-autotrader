r"""0% 전환사채 발행 공시 사건 연구 (OpenDART)

1) list.json(주요사항보고서 B001)에서 '전환사채권발행결정' 공시 수집 (상장사만)
2) cvbdIsDecsn.json으로 조건(표면·만기이자율, 권면총액, 전환가, 최저조정가, 희석률) 수집
3) 공시일 종가 기준 +20/60/120 거래일 수익률 - 같은 시장 지수(KOSPI/KOSDAQ) 수익률 = 초과수익
4) 그룹 비교: 0%/0% vs 그 외, 그리고 우리 필터(희석률<10%, 20일 평균 거래대금 20억+, 200일선 위)

키: 환경변수 DART_API_KEY 또는 config.local.json {"dart_api_key": "..."} (키는 출력·로그에 남기지 않음)
실행: python cb_event_study.py [시작연도=2021]
"""
from pathlib import Path
import json, os, sys, time
import numpy as np
import pandas as pd
import requests
import FinanceDataReader as fdr

HERE = Path(__file__).parent
CACHE = HERE / "data_cb"
OUT = HERE / "results"
API = "https://opendart.fss.or.kr/api"


def api_key():
    k = os.environ.get("DART_API_KEY")
    f = HERE / "config.local.json"
    if not k and f.exists():
        k = json.loads(f.read_text(encoding="utf-8")).get("dart_api_key")
    if not k:
        sys.exit("DART 키 없음: config.local.json 에 dart_api_key 를 넣어 주세요.")
    return k


def get(ep, **p):
    for n in range(6):
        try:
            r = requests.get(f"{API}/{ep}.json", params={"crtfc_key": KEY, **p}, timeout=30)
            j = r.json()
        except Exception as e:  # 네트워크 오류: 키가 담긴 URL이 찍히지 않도록 메시지 축약
            print(f"  재시도 {n+1}: {ep} {type(e).__name__}")
            time.sleep(3 * (n + 1))
            continue
        if j.get("status") in ("000", "013"):  # 013 = 데이터 없음
            return j
        if j.get("status") == "020":           # 요청 제한
            time.sleep(5)
            continue
        raise RuntimeError(f"{ep} {j.get('status')} {j.get('message')}")
    raise RuntimeError(f"{ep} 재시도 실패")


def collect_list(y0):
    f = CACHE / f"cb_list_{y0}.csv"
    if f.exists():
        return pd.read_csv(f, dtype=str)
    rows = []
    CACHE.mkdir(exist_ok=True)
    for s in pd.date_range(f"{y0}-01-01", pd.Timestamp.today(), freq="3MS"):
        e = min(s + pd.DateOffset(months=3) - pd.Timedelta(days=1), pd.Timestamp.today())
        qf = CACHE / f"q_{s:%Y%m}.json"
        if qf.exists() and e < pd.Timestamp.today() - pd.Timedelta(days=7):
            rows += json.loads(qf.read_text(encoding="utf-8"))
            continue
        q0 = len(rows)
        page = 1
        while True:
            j = get("list", bgn_de=f"{s:%Y%m%d}", end_de=f"{e:%Y%m%d}", pblntf_detail_ty="B001",
                    page_no=page, page_count=100)
            rows += [x for x in j.get("list", []) if "전환사채권발행결정" in x["report_nm"]
                     and x.get("stock_code", "").strip() and x["corp_cls"] in ("Y", "K")]
            if page >= int(j.get("total_page", 1)):
                break
            page += 1
        qf.write_text(json.dumps(rows[q0:], ensure_ascii=False), encoding="utf-8")
        print(f"  목록 {s:%Y-%m} ~ {e:%Y-%m}: 누적 {len(rows)}건")
    df = pd.DataFrame(rows)
    df = df[~df["report_nm"].str.contains("철회")]
    CACHE.mkdir(exist_ok=True)
    df.to_csv(f, index=False, encoding="utf-8-sig")
    return df


def num(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except ValueError:
        return np.nan


def collect_terms(lst):
    f = CACHE / "cb_terms.csv"
    done = pd.read_csv(f, dtype=str) if f.exists() else pd.DataFrame(columns=["rcept_no"])
    need = lst[~lst["rcept_no"].isin(done["rcept_no"])]
    new = []
    for corp, g in need.groupby("corp_code"):
        lo, hi = g["rcept_dt"].min(), g["rcept_dt"].max()
        j = get("cvbdIsDecsn", corp_code=corp, bgn_de=lo, end_de=hi)
        new += [x for x in j.get("list", []) if x.get("rcept_no") in set(g["rcept_no"])]
    if new:
        done = pd.concat([done, pd.DataFrame(new).astype(str)], ignore_index=True)
        done.to_csv(f, index=False, encoding="utf-8-sig")
    return done


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    lst = collect_list(y0)
    # 정정공시는 최신 건만 (같은 회사·같은 회차 판단이 어려워 '[기재정정]'은 원공시 대체로 처리)
    lst = lst.sort_values("rcept_no").drop_duplicates(["corp_code", "rcept_dt"], keep="last")
    terms = collect_terms(lst)
    d = lst.merge(terms, on="rcept_no", how="inner", suffixes=("", "_t"))
    d["표면"], d["만기"] = d["bd_intr_ex"].map(num), d["bd_intr_sf"].map(num)
    d["희석률"] = d["cvisstk_tisstk_vs"].map(num)
    d["발행액억"] = d["bd_fta"].map(num) / 1e8
    d["전환가"] = d["cv_prc"].map(num)
    d["리픽싱최저"] = d["act_mktprcfl_cvprc_lwtrsprc"].map(num)
    d["제로"] = (d["표면"] == 0) & (d["만기"] == 0)
    d["dt"] = pd.to_datetime(d["rcept_dt"])
    analyze(d, y0)


def analyze(d, y0, tag=""):
    print(f"상장사 CB 공시 {len(d)}건 (0%/0% {int(d['제로'].sum())}건)")
    idx = {"Y": fdr.DataReader("KS11", f"{y0-1}-01-01")["Close"],
           "K": fdr.DataReader("KQ11", f"{y0-1}-01-01")["Close"]}
    res = []
    for code, g in d.groupby("stock_code"):
        try:
            px = fdr.DataReader(code, f"{y0-1}-01-01")
        except Exception:
            continue
        if px.empty:
            continue
        c, amt = px["Close"], (px["Close"] * px["Volume"]).rolling(20).mean()
        ma200 = c.rolling(200).mean()
        for _, r in g.iterrows():
            t = c.index.searchsorted(r["dt"])
            if t >= len(c):
                continue
            ix = idx[r["corp_cls"]]
            ti = ix.index.searchsorted(c.index[t])
            row = {"rcept_no": r["rcept_no"], "회사": r["corp_name"], "코드": code, "시장": r["corp_cls"],
                   "공시일": r["dt"].date(), "제로": r["제로"], "표면": r["표면"], "만기": r["만기"],
                   "희석률": r["희석률"], "발행액억": r["발행액억"],
                   "현재가/전환가": c.iloc[t] / r["전환가"] if r["전환가"] else np.nan,
                   "거래대금20억+": amt.iloc[t] >= 2e9, "200일선위": c.iloc[t] > ma200.iloc[t]}
            for h in (20, 60, 120):
                if t + h < len(c) and ti + h < len(ix):
                    sr = c.iloc[t + h] / c.iloc[t] - 1
                    mr = ix.iloc[ti + h] / ix.iloc[ti] - 1
                    row[f"초과{h}"] = sr - mr
            res.append(row)
    ev = pd.DataFrame(res)
    OUT.mkdir(exist_ok=True)
    ev.to_csv(OUT / f"cb_events{tag}.csv", index=False, encoding="utf-8-sig")

    ev["우리필터"] = (ev["희석률"] < 10) & ev["거래대금20억+"] & ev["200일선위"]
    groups = {"전체 CB": ev, "0%/0% CB": ev[ev["제로"]], "그 외 CB": ev[~ev["제로"]],
              "0%/0% + 우리필터": ev[ev["제로"] & ev["우리필터"]],
              "0%/0% + 희석<10%": ev[ev["제로"] & (ev["희석률"] < 10)],
              "0%/0% + 200일선위": ev[ev["제로"] & ev["200일선위"]]}
    out = []
    for name, g in groups.items():
        r = {"그룹": name, "건수": len(g)}
        for h in (20, 60, 120):
            x = g[f"초과{h}"].dropna()
            r[f"+{h}일 평균%"] = round(100 * x.mean(), 1) if len(x) else np.nan
            r[f"+{h}일 중앙값%"] = round(100 * x.median(), 1) if len(x) else np.nan
            r[f"+{h}일 승률%"] = round(100 * (x > 0).mean(), 1) if len(x) else np.nan
        out.append(r)
    s = pd.DataFrame(out)
    s.to_csv(OUT / f"cb_summary{tag}.csv", index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(s.to_string(index=False))


KEY = None
if __name__ == "__main__":
    KEY = api_key()
    main()
