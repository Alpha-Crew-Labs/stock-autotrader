r"""키 없이: cb-zero-finder 공개 API(/api/cb-latest, 당해연도 YTD)로 0% CB 사건 연구
실행: python cb_event_site.py
"""
import json
import numpy as np
import pandas as pd
import requests
from cb_event_study import CACHE, analyze, num

CACHE.mkdir(exist_ok=True)
j = requests.get("https://cb-zero-finder.vercel.app/api/cb-latest", timeout=60).json()
(CACHE / "cb_site_ytd.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
r = pd.DataFrame(j["rows"])
r = r[r["stockCode"].fillna("").str.strip() != ""]
print("기간", j["period"], "/ 갱신", j["updatedAt"], "/ 시장 값:", r["market"].value_counts().to_dict())
mk = r["market"].astype(str)
d = pd.DataFrame({
    "rcept_no": r["receiptNo"], "corp_name": r["corpName"], "stock_code": r["stockCode"].str.zfill(6),
    "corp_cls": np.where(mk.str.contains("유가|KOSPI|Y", case=False), "Y", "K"),
    "표면": r["surfaceRate"].map(num), "만기": r["maturityRate"].map(num),
    "희석률": r["dilutionRate"].map(num), "발행액억": r["amountEok"].map(num),
    "전환가": r["convertPrice"].map(num), "리픽싱최저": r["refixingFloor"].map(num),
    "dt": pd.to_datetime(r["receiptDate"]),
})
d = d[d["corp_cls"].isin(["Y", "K"])]
d["제로"] = (d["표면"] == 0) & (d["만기"] == 0)
analyze(d, 2026, "_2026")
