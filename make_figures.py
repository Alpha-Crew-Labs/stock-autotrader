r"""README·GitHub Pages용 그림 생성 -> docs/img/*.png, docs/index.html(데모 대시보드)

입력: results/*.csv (백테스트·CB 연구), state/replay.json (python replay.py 결과)
실행: python make_figures.py
"""
from pathlib import Path
import json, shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import FinanceDataReader as fdr
from backtest_hyung import stoch

HERE = Path(__file__).parent
RES, IMG = HERE / "results", HERE / "docs" / "img"
IMG.mkdir(parents=True, exist_ok=True)

# 팔레트(데이터시각화 기본 팔레트 1·2번 + 중립 잉크)
BLUE, ORANGE, INK, INK2, MUTED, GRID, SURF = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"
plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "figure.facecolor": SURF,
                     "axes.facecolor": SURF, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
                     "axes.spines.top": False, "axes.spines.right": False, "font.size": 11})


def title(ax, t, sub=None):
    ax.set_title(t, loc="left", fontsize=15, fontweight="bold", color=INK, pad=24 if sub else 10)
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=10.5, color=INK2, va="bottom")


def pf_from_trades(strategy):
    t = pd.read_csv(RES / "trades.csv")
    t = t[(t["전략"] == strategy) & (t["손절"].isna())]["수익률"]
    return t[t > 0].sum() / -t[t < 0].sum()


# 1) 전략 비교: 수익팩터(PF) + 최대낙폭(MDD) 나란히
def fig_strategies():
    s1 = pd.read_csv(RES / "summary.csv")
    s5 = pd.read_csv(RES / "summary_5m.csv")
    h1 = pd.read_csv(RES / "summary_hyung.csv").set_index("구분")
    h2 = pd.read_csv(RES / "summary_hyung2.csv").set_index("구분")
    h3 = pd.read_csv(RES / "summary_hyung3.csv").set_index("구분")
    mdd1 = lambda st: float(s1[(s1["전략"] == st) & (s1["손절"] == "없음")]["MDD%"].iloc[0])
    rows = [("\"승률 99%\" RSI+MACD+스토캐스틱 (5분봉)", float(s5.loc[s5["설정"].str.contains("10봉"), "PF(비용후)"].iloc[0]), None),
            ("RSI + MACD + 스토캐스틱", pf_from_trades("D_스토+RSI+MACD"), mdd1("D_스토+RSI+MACD")),
            ("스토캐스틱 20 매수 / 80 매도", pf_from_trades("A0_스토캐스틱_단순"), mdd1("A0_스토캐스틱_단순")),
            ("형님 기법 v1 (교차 기준)", float(h1.loc["일봉·주식50 (2016~) / 완화형", "PF"]), float(h1.loc["일봉·주식50 (2016~) / 완화형", "MDD%"])),
            ("형님 기법 v2 (필터 없음)", float(h2.loc["필터 없음", "PF"]), float(h2.loc["필터 없음", "MDD%"])),
            ("형님 기법 v2 + 시장·종목 필터", float(h2.loc["시장+종목 필터", "PF"]), float(h2.loc["시장+종목 필터", "MDD%"])),
            ("형님 기법 v3 + 주봉 스토캐스틱 (채택)", float(h3.loc["W 주봉 필터", "PF"]), float(h3.loc["W 주봉 필터", "MDD%"]))]
    names = [r[0] for r in rows]
    y = np.arange(len(rows))
    col = [BLUE if "채택" in n else "#c9c8c2" for n in names]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5.3), sharey=True, gridspec_kw={"width_ratios": [1, 1]})
    a1.barh(y, [r[1] for r in rows], color=col, height=0.6, zorder=3)
    a1.axvline(1.0, color=INK, lw=1.2, zorder=4)
    for yi, r in zip(y, rows):
        a1.text(r[1] + 0.02, yi, f"{r[1]:.2f}", va="center", fontsize=10.5, color=INK)
    a1.set_xlim(0, 1.35)
    a1.set_yticks(y, names)
    a1.set_xlabel("수익팩터 (총이익 ÷ 총손실) — 1보다 크면 이익")
    a1.set_title("① 돈을 벌었나", loc="left", fontsize=12.5, fontweight="bold", color=INK)
    for yi, r in zip(y, rows):
        if r[2] is None:
            a2.text(-1, yi, "5분봉 — 비교 불가", va="center", ha="right", fontsize=10, color=MUTED)
            continue
        a2.barh(yi, r[2], color=col[yi], height=0.6, zorder=3)
        a2.text(r[2] - 1, yi, f"{r[2]:.1f}%", va="center", ha="right", fontsize=10.5, color=INK)
    a2.set_xlim(-60, 0)
    a2.set_xlabel("최대낙폭(%) — 고점 대비 가장 크게 빠진 폭")
    a2.set_title("② 얼마나 크게 잃었나", loc="left", fontsize=12.5, fontweight="bold", color=INK)
    for ax in (a1, a2):
        ax.grid(axis="y", visible=False)
    fig.suptitle("어떤 규칙을 쓸까 — 수익은 비슷한데, 잃는 폭이 다르다", x=0.01, ha="left", fontsize=15, fontweight="bold", color=INK)
    fig.text(0.01, 0.905, "코스피 시총 상위 50종목 일봉 2016~2026, 수수료·세금·슬리피지 반영 · v2는 평소 자금 일부만 투자해 낙폭이 작은 면도 있음", fontsize=10.5, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(IMG / "strategy_pf.png", dpi=150)
    plt.close(fig)


# 2) 위험 vs 수익: 그냥 보유 vs 형님 기법
def fig_risk():
    h2 = pd.read_csv(RES / "summary_hyung2.csv").set_index("구분")
    pts = [("그냥 보유", h2.loc["그냥 보유(기준)", "CAGR%"], h2.loc["그냥 보유(기준)", "MDD%"], ORANGE),
           ("형님 v2 (필터 없음)", h2.loc["필터 없음", "CAGR%"], h2.loc["필터 없음", "MDD%"], "#9a9993"),
           ("형님 v2 + 필터", h2.loc["시장+종목 필터", "CAGR%"], h2.loc["시장+종목 필터", "MDD%"], BLUE)]
    fig, ax = plt.subplots(figsize=(10, 3.6))
    y = np.arange(len(pts))
    ax.barh(y, [p[2] for p in pts], color=[p[3] for p in pts], height=0.55, zorder=3)
    for yi, p in zip(y, pts):
        ax.text(p[2] - 0.8, yi, f"최대 {p[2]:.1f}%   (연 {p[1]:+.1f}%)", va="center", ha="right", fontsize=10.5, color=INK)
    ax.set_yticks(y, [p[0] for p in pts])
    ax.set_xlim(-60, 0)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("최대낙폭(MDD, %) — 고점 대비 가장 크게 빠진 폭")
    title(ax, "\"최대한 잃지 말자\"는 지켜진다", "최대낙폭 -45% → -2%. 대신 연수익도 거의 0 — 수익은 종목 선택에서 나와야 한다")
    fig.tight_layout()
    fig.savefig(IMG / "risk_mdd.png", dpi=150)
    plt.close(fig)


# 3) 실제 종목에서 신호가 어떻게 나오나
def fig_example(state):
    h = pd.DataFrame(state["history"])
    # 예시 종목: 거래가 가장 많고, 같으면 보유 기간이 긴 종목 (신호 흐름이 잘 보이도록, 수익률로 고르지 않음)
    h["days"] = (pd.to_datetime(h["closed"]) - pd.to_datetime(h["opened"])).dt.days
    g = h.groupby("code").agg(n=("ret", "size"), d=("days", "sum")).sort_values(["n", "d"], ascending=False)
    code = g.index[0]
    name = h.loc[h["code"] == code, "name"].iloc[0]
    tr = h[h["code"] == code]
    df = stoch(fdr.DataReader(code, "2025-03-01")[["Open", "High", "Low", "Close", "Volume"]])
    df = df[df.index >= "2026-01-01"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True, gridspec_kw={"height_ratios": [1.6, 1]})
    a1.plot(df.index, df["Close"], color=INK2, lw=1.6, zorder=3)
    for _, t in tr.iterrows():
        o, c = pd.Timestamp(t["opened"]), pd.Timestamp(t["closed"])
        a1.axvspan(o, c, color=BLUE, alpha=0.10, lw=0)
        po, pc = df.loc[o, "Open"], df.loc[c, "Open"]
        a1.scatter([o], [po], marker="^", s=90, color=BLUE, zorder=5, edgecolor=SURF, lw=1.5)
        a1.scatter([c], [pc], marker="v", s=90, color=ORANGE, zorder=5, edgecolor=SURF, lw=1.5)
        a1.annotate(f"{100*t['ret']:+.1f}%", (c, pc), xytext=(14, -4), textcoords="offset points",
                    ha="left", fontsize=9.5, color=INK, bbox=dict(boxstyle="round,pad=0.2", fc=SURF, ec=GRID, lw=0.6))
    a1.scatter([], [], marker="^", color=BLUE, label="매수 (다음 날 시가)")
    a1.scatter([], [], marker="v", color=ORANGE, label="매도 (다음 날 시가)")
    a1.fill_between([], [], color=BLUE, alpha=0.1, label="보유 기간")
    a1.legend(loc="upper left", frameon=False, ncol=3, fontsize=10)
    a1.set_ylabel("주가(원)")
    a1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    title(a1, f"신호 예시 — {name} ({code}), 2026년", f"스토캐스틱이 20 아래에서 고개를 들면 사고, 꺾이면 판다 · 리플레이 전체 {len(h)}건 중 승률 {100*(h['ret']>0).mean():.0f}%, 거래당 평균 {100*h['ret'].mean():+.1f}%")
    a2.axhspan(0, 20, color=BLUE, alpha=0.08, lw=0)
    a2.axhline(20, color=MUTED, lw=1, ls="--")
    a2.axhline(80, color=MUTED, lw=1, ls="--")
    a2.plot(df.index, df["K"], color=BLUE, lw=1.8, label="%K (5)")
    a2.plot(df.index, df["D"], color=ORANGE, lw=1.8, label="%D (5)")
    a2.text(df.index[2], 10, "과매도 구간 (20 아래)", fontsize=9.5, color=INK2, va="center")
    a2.set_ylim(0, 100)
    a2.set_yticks([0, 20, 50, 80, 100])
    a2.set_ylabel("스토캐스틱")
    a2.legend(loc="upper left", frameon=False, ncol=2, fontsize=10)
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%m월"))
    fig.tight_layout()
    fig.savefig(IMG / "example_signals.png", dpi=150)
    plt.close(fig)


# 4) 2026 리플레이: 모의 계좌 vs KODEX200
def fig_replay(state):
    eq = pd.DataFrame(state["equity_log"])
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.set_index("date")
    k = fdr.DataReader("069500", "2025-12-01")["Close"].reindex(eq.index, method="ffill")
    a = 100 * eq["equity"] / eq["equity"].iloc[0]
    b = 100 * k / k.iloc[0]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.axhline(100, color=MUTED, lw=1, ls="--")
    ax.plot(b.index, b, color=ORANGE, lw=2)
    ax.plot(a.index, a, color=BLUE, lw=2)
    ax.text(b.index[-1], b.iloc[-1], f"  KODEX 200 {b.iloc[-1]-100:+.0f}%", color=INK, va="center", fontsize=10.5)
    ax.text(a.index[-1], a.iloc[-1], f"  모의 계좌 {a.iloc[-1]-100:+.1f}%", color=INK, va="center", fontsize=10.5)
    off = eq["mkt_on"] == False  # noqa: E712
    ax.fill_between(eq.index, 0, 1, where=off, transform=ax.get_xaxis_transform(), color=MUTED, alpha=0.12, lw=0)
    ax.text(0.995, 0.97, "회색 = 시장 필터 OFF (신규 매수 중지)", transform=ax.transAxes, ha="right", va="top",
            fontsize=9.5, color=INK2)
    ax.set_xlim(a.index[0], a.index[-1] + pd.Timedelta(days=55))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m월"))
    ax.set_ylabel("시작 = 100")
    title(ax, "2026년 리플레이 — 모의 계좌 vs KODEX 200",
          "시총 상위 50종목, 1,000만 원, 매일 장 마감 후 규칙대로 실행했다고 가정 (미래 데이터 차단)")
    fig.tight_layout()
    fig.savefig(IMG / "replay_equity.png", dpi=150)
    plt.close(fig)


# 5) 0% 전환사채 발행 후 주가
def fig_cb():
    s = pd.read_csv(RES / "cb_summary.csv").set_index("그룹")
    groups = [("0%/0% CB", BLUE), ("그 외 CB", ORANGE)]
    hz = [20, 60, 120]
    fig, ax = plt.subplots(figsize=(10, 3.8))
    w = 0.36
    x = np.arange(len(hz))
    for i, (g, c) in enumerate(groups):
        v = [s.loc[g, f"+{h}일 중앙값%"] for h in hz]
        ax.bar(x + (i - 0.5) * w, v, width=w - 0.04, color=c, label=f"{g} ({int(s.loc[g, '건수']):,}건)", zorder=3)
        for xi, vi in zip(x + (i - 0.5) * w, v):
            ax.text(xi, vi - 0.6, f"{vi:.1f}%", ha="center", va="top", fontsize=10, color=INK)
    ax.axhline(0, color=INK, lw=1)
    ax.set_xticks(x, [f"공시 후 {h}거래일" for h in hz])
    ax.set_ylim(min(s.loc[[g for g, _ in groups], "+120일 중앙값%"]) * 1.35, 2)
    ax.set_ylabel("시장 대비 초과수익 중앙값(%)")
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, loc="lower left")
    title(ax, "전환사채(CB) 발행 종목은 시장보다 뒤처졌다",
          "2021~2026 상장사 CB 공시 2,002건 (OpenDART) → 스크리너에서 최근 1년 CB 발행사는 제외")
    fig.tight_layout()
    fig.savefig(IMG / "cb_event.png", dpi=150)
    plt.close(fig)


# 6) 파라미터 안정성 히트맵 (results/v3_params.csv, backtest_v3_extra.py 결과)
def fig_params():
    f = RES / "v3_params.csv"
    if not f.exists():
        return
    p2 = pd.read_csv(f)
    m = p2.pivot(index="기간", columns="평활", values="PF")
    n = p2.pivot(index="기간", columns="평활", values="거래수")
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.grid(False)
    vmax = max(abs(m.values - 1).max(), 0.05)
    ax.imshow(m.values - 1, cmap="RdBu", vmin=-vmax, vmax=vmax)
    for (r, c), v in np.ndenumerate(m.values):
        strong = abs(v - 1) > 0.55 * vmax
        ax.text(c, r, f"{v:.2f}\n({int(n.values[r, c])}건)", ha="center", va="center", fontsize=10.5,
                color="#ffffff" if strong else INK,
                fontweight="bold" if (m.index[r], m.columns[c]) == (14, 5) else "normal")
    ax.set_xticks(range(len(m.columns)), [f"%K·%D {c}" for c in m.columns])
    ax.set_yticks(range(len(m.index)), [f"기간 {i}" for i in m.index])
    title(ax, "설정을 바꿔도 버티나 — 수익팩터", "파랑 = 1보다 큼(이익), 빨강 = 손실 · 굵은 글씨 = 형님 설정 (14,5,5)")
    fig.tight_layout()
    fig.savefig(IMG / "v3_params.png", dpi=150)
    plt.close(fig)


def main():
    state = json.loads((HERE / "state" / "replay.json").read_text(encoding="utf-8"))
    fig_strategies()
    fig_risk()
    fig_example(state)
    fig_replay(state)
    fig_cb()
    fig_params()
    # GitHub Pages 데모 = 2026 리플레이 대시보드
    src = HERE / "dashboard_replay" / "index.html"
    if src.exists():
        html = src.read_text(encoding="utf-8").replace(
            "<h1>주식 자동매매 — 모의 장부</h1>",
            "<h1>주식 자동매매 — 모의 장부 <span style='font-size:13px;font-weight:600;color:#eb6834'>"
            "데모 · 2026년 리플레이</span></h1>")
        (HERE / "docs" / "index.html").write_text(html, encoding="utf-8")
    print("저장:", sorted(p.name for p in IMG.glob("*.png")))


if __name__ == "__main__":
    main()
