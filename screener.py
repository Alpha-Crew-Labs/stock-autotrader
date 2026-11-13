r"""일간 종목 스크리너 — 일봉 스토캐스틱 스윙 전략(backtest_hyung2) 후보 추리기

필터 (백테스트·이벤트 스터디 결과 반영)
  0) 유니버스: KRX 상장 KOSPI·KOSDAQ 보통주 (KONEX 제외)
  1) 제외: 우선주(코드 끝자리≠0), 스팩, 리츠·인프라펀드, 관리종목·투자주의환기종목(KOSDAQ 소속부 기준), 시총 < 1,000억
  2) CB 제외: 최근 12개월 전환사채권발행결정 공시 회사 (OpenDART B001 + cb-zero-finder 사이트 0%/0% 표시)
  3) 유동성: 20일 평균 거래대금(종가×거래량) ≥ 20억
  4) 추세: 종가 > 200일선, 120일 수익률 > 0
  출력: RS60(60일 수익률 / 소속 시장지수 60일 수익률) 순위, 스토캐스틱(14,5,5) %K·%D, near_buy_zone(%K<30), buy_signal(전략 매수 신호)
  시장 필터: KODEX200(069500) 종가 > 200일선 여부 출력

실행: python screener.py
산출: watchlist/candidates_YYYYMMDD.csv, watchlist/cb_excluded_YYYYMMDD.csv
캐시: data_screen/ (DART 목록, 가격 data_screen/prices/)
DART 키는 config.local.json 의 dart_api_key 에서만 읽고 출력·기록하지 않는다.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import FinanceDataReader as fdr

HERE = Path(__file__).parent
CACHE = HERE / "data_screen"
PCACHE = CACHE / "prices"
OUT = HERE / "watchlist"

MIN_MARCAP = 1000e8       # 1,000억
MIN_AVGVAL = 20e8         # 20억
HIST_DAYS = 460           # 달력일 (~300 거래일)
WORKERS = 8
CB_SITE = "https://cb-zero-finder.vercel.app/api/cb-latest"
DART_LIST = "https://opendart.fss.or.kr/api/list.json"
REIT_EXTRA = ("맥쿼리인프라", "맵스리얼티", "한국ANKOR유전", "하나니켈")


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 가격
def _fetch(code: str, start: date) -> pd.DataFrame:
    for i in range(3):
        try:
            df = fdr.DataReader(code, start.strftime("%Y-%m-%d"))
            if df is not None:
                return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            time.sleep(1 + i)
    return pd.DataFrame()


def load_prices(code: str, today: date) -> pd.DataFrame:
    """캐시 사용. 오늘 16시 이후 저장된 캐시면 그대로, 아니면 최근 10일만 다시 받아 덧붙임."""
    f = PCACHE / f"{code}.csv"
    if f.exists():
        old = pd.read_csv(f, index_col=0, parse_dates=True)
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime.date() == today and mtime.hour >= 16:
            return old
        if len(old) and (today - old.index[-1].date()).days < 60:
            new = _fetch(code, old.index[-1].date() - timedelta(days=10))
            if len(new):
                df = pd.concat([old[old.index < new.index[0]], new])
                df.to_csv(f)
                return df
            return old
    df = _fetch(code, today - timedelta(days=HIST_DAYS))
    if len(df):
        df.to_csv(f)
    return df


def stoch(df, n=14, k=5, d=5):
    lo, hi = df["Low"].rolling(n).min(), df["High"].rolling(n).max()
    K = (100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)).rolling(k).mean()
    return K, K.rolling(d).mean()


def buy_signal(K, D):
    """backtest_hyung2.prep 의 1차 매수 신호(마지막 봉)."""
    gap = (K - D).abs()
    narrow3 = (gap < gap.shift()) & (gap.shift() < gap.shift(2)) & (gap.shift(2) < gap.shift(3))
    near_os = (K.rolling(5).min() < 20) & (K < 30)
    gc = (K > D) & (K.shift() <= D.shift())
    sig = near_os & (((K < D) & narrow3 & (K > K.shift())) | gc)
    return bool(sig.iloc[-1])


# ---------------------------------------------------------------- CB
def dart_key() -> str:
    cfg = json.loads((HERE / "config.local.json").read_text(encoding="utf-8"))
    return cfg["dart_api_key"]


def _dart_fetch(key: str, bgn: date, end: date) -> list[dict]:
    rows, page = [], 1
    while True:
        params = dict(crtfc_key=key, bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                      pblntf_detail_ty="B001", page_no=page, page_count=100)
        j = None
        for i in range(3):
            try:
                r = requests.get(DART_LIST, params=params, timeout=30)
                j = r.json()
                break
            except Exception as e:  # 예외 메시지에 URL(키 포함)이 들어가므로 종류만 남김
                err = type(e).__name__
                time.sleep(2 + i)
        if j is None:
            raise RuntimeError(f"DART 요청 실패 ({err}) {bgn}~{end} p{page}")
        st = j.get("status")
        if st == "013":           # 조회 데이터 없음
            break
        if st != "000":
            raise RuntimeError(f"DART status {st}: {j.get('message')}")
        for x in j.get("list", []):
            nm = x.get("report_nm", "")
            if "전환사채권발행결정" in nm and "철회" not in nm:
                rows.append({k: x.get(k) for k in ("corp_name", "stock_code", "corp_cls", "report_nm", "rcept_no", "rcept_dt")})
        if page >= int(j.get("total_page", 1)):
            break
        page += 1
    return rows


def dart_cb(today: date, cutoff: date) -> pd.DataFrame:
    """분기(1·4·7·10월 시작) 단위로 받아 캐시. 지난 분기는 영구, 현재 분기는 실행일마다 갱신."""
    key = dart_key()
    rows = []
    q = date(cutoff.year, 3 * ((cutoff.month - 1) // 3) + 1, 1)
    while q <= today:
        nq = date(q.year + (q.month + 3 > 12), (q.month + 2) % 12 + 1, 1)
        end = nq - timedelta(days=1)
        if end < today:
            f = CACHE / f"dart_cb_{q:%Y%m%d}_{end:%Y%m%d}.json"
            if f.exists():
                part = json.loads(f.read_text(encoding="utf-8"))
            else:
                part = _dart_fetch(key, q, end)
                f.write_text(json.dumps(part, ensure_ascii=False), encoding="utf-8")
        else:
            f = CACHE / "dart_cb_current.json"
            cur = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            if cur.get("asof") == today.isoformat() and cur.get("bgn") == q.isoformat():
                part = cur["rows"]
            else:
                part = _dart_fetch(key, q, today)
                f.write_text(json.dumps({"asof": today.isoformat(), "bgn": q.isoformat(), "rows": part},
                                        ensure_ascii=False), encoding="utf-8")
        rows += part
        q = nq
    df = pd.DataFrame(rows, columns=["corp_name", "stock_code", "corp_cls", "report_nm", "rcept_no", "rcept_dt"])
    df = df[df["stock_code"].fillna("").str.strip() != ""]
    df["rcept_dt"] = pd.to_datetime(df["rcept_dt"], format="%Y%m%d")
    return df[df["rcept_dt"] >= pd.Timestamp(cutoff)].drop_duplicates("rcept_no")


def site_cb() -> tuple[pd.DataFrame, str]:
    f = CACHE / "cb_site_latest.json"
    try:
        j = requests.get(CB_SITE, timeout=30).json()
        f.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        if not f.exists():
            log(f"  [경고] CB 사이트 조회 실패({type(e).__name__}), 캐시 없음 → 0%/0% 표시 불가")
            return pd.DataFrame(columns=["stockCode"]), "unavailable"
        log(f"  [경고] CB 사이트 조회 실패({type(e).__name__}) → 캐시 사용")
        j = json.loads(f.read_text(encoding="utf-8"))
    df = pd.DataFrame(j.get("rows", []))
    if len(df):
        for c in ("surfaceRate", "maturityRate", "dilutionRate"):
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
        df["zero_zero"] = (df["surfaceRate"] == 0) & (df["maturityRate"] == 0)
        df["receiptDate"] = pd.to_datetime(df["receiptDate"])
    per = j.get("period", {})
    return df, f"{per.get('begin')}~{per.get('end')} (updated {j.get('updatedAt')})"


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    today = date.today()
    cutoff = today - timedelta(days=365)
    for p in (CACHE, PCACHE, OUT):
        p.mkdir(parents=True, exist_ok=True)

    # 시장 필터 + 지수
    kodex = _fetch("069500", today - timedelta(days=HIST_DAYS))
    kma200 = kodex["Close"].rolling(200).mean()
    asof = kodex.index[-1]
    mkt_on = bool(kodex["Close"].iloc[-1] > kma200.iloc[-1])
    idx = {m: _fetch(s, today - timedelta(days=200))["Close"] for m, s in (("KOSPI", "KS11"), ("KOSDAQ", "KQ11"))}
    idx_ret60 = {m: s.iloc[-1] / s.iloc[-61] - 1 for m, s in idx.items()}

    # 1) 상장 목록 + 저가 필터
    RAW = fdr.StockListing("KRX")
    L = RAW.copy()
    stage = [("KRX 상장 전체", len(L))]
    L = L[L["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
    L["Mkt"] = np.where(L["Market"] == "KOSPI", "KOSPI", "KOSDAQ")
    stage.append(("KOSPI+KOSDAQ", len(L)))
    L = L[L["Code"].str[-1] == "0"]
    stage.append(("- 우선주", len(L)))
    dept = L["Dept"].fillna("")
    L = L[~(L["Name"].str.contains("스팩") | dept.str.contains("SPAC"))]
    stage.append(("- 스팩", len(L)))
    nm = L["Name"]
    reit = (nm.str.contains("리츠") & ~nm.str.contains("메리츠")) | nm.str.startswith(REIT_EXTRA)
    L = L[~reit]
    stage.append(("- 리츠/인프라", len(L)))
    dept = L["Dept"].fillna("")
    L = L[~dept.str.contains("관리종목|투자주의환기")]
    stage.append(("- 관리/환기(KOSDAQ)", len(L)))
    L = L[L["Marcap"] >= MIN_MARCAP]
    stage.append(("시총 ≥ 1,000억", len(L)))

    # 2) CB 제외
    log("CB 공시 조회 (OpenDART 12개월 + cb-zero-finder YTD)...")
    dcb = dart_cb(today, cutoff)
    scb, site_period = site_cb()
    site_codes = set(scb["stockCode"].astype(str)) if len(scb) else set()
    cb_codes = set(dcb["stock_code"]) | site_codes
    allL = RAW.set_index("Code")
    ex = []
    for code in sorted(cb_codes):
        d = dcb[dcb["stock_code"] == code]
        s = scb[scb["stockCode"].astype(str) == code] if len(scb) else pd.DataFrame()
        dates = list(d["rcept_dt"]) + (list(s["receiptDate"]) if len(s) else [])
        info = allL.loc[code] if code in allL.index else None
        last_s = s.sort_values("receiptDate").iloc[-1] if len(s) else None
        ex.append({
            "Code": code,
            "Name": info["Name"] if info is not None else (d["corp_name"].iloc[0] if len(d) else s["corpName"].iloc[0]),
            "Market": info["Market"] if info is not None else "",
            "Marcap(억)": round(info["Marcap"] / 1e8) if info is not None else np.nan,
            "in_universe": code in set(L["Code"]),
            "last_cb_date": max(dates).date() if dates else None,
            "n_cb_dart_12m": len(d),
            "n_cb_site_ytd": len(s),
            "zero_zero_any": bool(s["zero_zero"].any()) if len(s) else False,
            "last_surfaceRate": last_s["surfaceRate"] if last_s is not None else np.nan,
            "last_maturityRate": last_s["maturityRate"] if last_s is not None else np.nan,
            "last_dilutionRate": last_s["dilutionRate"] if last_s is not None else np.nan,
            "source": "+".join(x for x, ok in (("DART", len(d) > 0), ("site", len(s) > 0)) if ok),
        })
    exdf = pd.DataFrame(ex)
    if len(exdf):
        exdf = exdf[exdf["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
        exdf = exdf.sort_values(["in_universe", "zero_zero_any", "last_cb_date"], ascending=False)
    exf = OUT / f"cb_excluded_{asof:%Y%m%d}.csv"
    exdf.to_csv(exf, index=False, encoding="utf-8-sig")
    L = L[~L["Code"].isin(cb_codes)]
    stage.append(("- CB 발행(12개월)", len(L)))

    # 3) 가격 다운로드 (병렬, 캐시)
    log(f"가격 데이터 {len(L)}종목 다운로드/캐시 (workers={WORKERS})...")
    prices, fail = {}, []
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(load_prices, c, today): c for c in L["Code"]}
        for i, f in enumerate(as_completed(futs), 1):
            c = futs[f]
            try:
                df = f.result()
            except Exception:
                df = pd.DataFrame()
            (prices.__setitem__(c, df) if len(df) else fail.append(c))
            if i % 200 == 0:
                log(f"  {i}/{len(futs)}")
    stage.append(("가격 확보", len(prices)))

    rows, short_hist, stale = [], 0, 0
    for _, r in L.iterrows():
        df = prices.get(r["Code"])
        if df is None:
            continue
        df = df[df["Volume"] > 0] if (df["Volume"] > 0).sum() >= 200 else df
        if len(df) < 201:
            short_hist += 1
            continue
        if df.index[-1] < asof:      # 거래정지 등으로 최신 봉 없음
            stale += 1
            continue
        c = df["Close"]
        val20 = (c * df["Volume"]).iloc[-20:].mean()
        ma200 = c.iloc[-200:].mean()
        K, D = stoch(df)
        rs = (c.iloc[-1] / c.iloc[-61]) / (1 + idx_ret60[r["Mkt"]])
        rows.append({
            "Code": r["Code"], "Name": r["Name"], "Market": r["Market"],
            "Marcap(억)": round(r["Marcap"] / 1e8), "AvgValue20(억)": round(val20 / 1e8, 1),
            "Close": int(c.iloc[-1]), "MA200gap%": round((c.iloc[-1] / ma200 - 1) * 100, 1),
            "Ret120%": round((c.iloc[-1] / c.iloc[-121] - 1) * 100, 1),
            "Ret60%": round((c.iloc[-1] / c.iloc[-61] - 1) * 100, 1),
            "RS60": round(rs, 3),
            "%K": round(K.iloc[-1], 1), "%D": round(D.iloc[-1], 1),
            "near_buy_zone": bool(K.iloc[-1] < 30), "buy_signal": buy_signal(K, D),
        })
    A = pd.DataFrame(rows)
    stage.append((f"히스토리≥201일·최신봉(제외 {short_hist}+{stale})", len(A)))
    A = A[A["AvgValue20(억)"] >= MIN_AVGVAL / 1e8]
    stage.append(("20일 평균 거래대금 ≥ 20억", len(A)))
    A = A[(A["MA200gap%"] > 0) & (A["Ret120%"] > 0)]
    stage.append(("종가>200일선 & 120일수익>0", len(A)))
    A["RS60_rank"] = A["RS60"].rank(ascending=False, method="min").astype(int)
    A = A.sort_values("RS60_rank")
    cols = ["Code", "Name", "Market", "Marcap(억)", "AvgValue20(억)", "Close", "MA200gap%", "Ret120%",
            "Ret60%", "RS60", "RS60_rank", "%K", "%D", "near_buy_zone", "buy_signal"]
    A = A[cols]
    outf = OUT / f"candidates_{asof:%Y%m%d}.csv"
    A.to_csv(outf, index=False, encoding="utf-8-sig")

    # 요약
    log("")
    log(f"기준일 {asof:%Y-%m-%d} | 시장 필터 KODEX200 {kodex['Close'].iloc[-1]:,.0f} vs MA200 {kma200.iloc[-1]:,.0f}"
        f" → {'ON (신규 매수 허용)' if mkt_on else 'OFF (신규 매수 금지)'}")
    log(f"지수 60일 수익률: KOSPI {idx_ret60['KOSPI']*100:.1f}%, KOSDAQ {idx_ret60['KOSDAQ']*100:.1f}%")
    log("필터 단계:")
    for n, k in stage:
        log(f"  {n:<36} {k:>5}")
    log(f"CB 제외: 12개월 발행사 {len(exdf)}곳(상장), 유니버스 내 {int(exdf['in_universe'].sum()) if len(exdf) else 0}곳,"
        f" 0%/0% {int(exdf['zero_zero_any'].sum()) if len(exdf) else 0}곳 | 사이트 기간 {site_period}")
    if fail:
        log(f"가격 실패 {len(fail)}종목: {', '.join(fail[:10])}{' ...' if len(fail) > 10 else ''}")
    log(f"최종 후보 {len(A)}종목 (near_buy_zone {int(A['near_buy_zone'].sum())}, buy_signal {int(A['buy_signal'].sum())})")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        log(A.head(15)[["Code", "Name", "Market", "AvgValue20(억)", "Ret120%", "RS60", "RS60_rank", "%K", "%D",
                        "near_buy_zone"]].to_string(index=False))
    log(f"\n저장: {outf}\n      {exf}\n소요 {time.time()-t0:.0f}초")


if __name__ == "__main__":
    sys.exit(main())
