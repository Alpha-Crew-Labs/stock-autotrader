# AGENTS.md — Stock Autotrader 작업 규칙 (공용 지침)

> 사람이 읽을 설명은 `README.md`(한눈에) · `프로젝트_설명.md`(자세히) · `RESEARCH_V7.md`(v7 한/미 연구) · `RESEARCH_V6.md`(v6 기준선) · `OPERATIONS.md`(실전 운영) · `CONTRIBUTING.md`(협업).
> 이 파일은 **AI가 코드를 고칠 때 지킬 규칙**만 적는다. (`CLAUDE.md`는 `@AGENTS.md` 연결)

## 프로젝트 한 줄
한국(`KR_Top200`) · 미국 우량주(`US_BlueChip65`) 멀티마켓 일봉 스윙 자동매매 (**v7: `StochRSI + IBS + NoDistVol + Clenow90 + VolTarget + E3_atr`**). 유튜브 5분봉(`RSI+MACD+Stoch`) 아이디어에서 출발해 Reddit(`r/algotrading`)·정량 검증과 한·미 10년 IS/OOS + Plateau + Monte Carlo(`backtest_research_v7.py`)로 고도화했다.

## 매매 규칙 (현재 v7 기본) — 바꾸려면 반드시 IS/OOS + Random/Plateau + MC95 백테스트 근거와 함께
- 전략 로직 단일 진실 공급원: `strategy.py` (`prep`, `market_vol_scale`, `check_entry`, `inverse_vol_amount`, `evaluate_position`, `rank_candidates`, `stoch`, `weekly_ok`)
- 매수 (`entry_mode="pullback_v7"`): `Close > SMA200` & `SMA50 > SMA200` 정배열 + (`STOCH_RSI < 15` | (`IBS < 0.20` & `Close < SMA5`)) & `~VOL_SPIKE_DN` — `strategy.prep` `PULLBACK_V7` (`"pullback_v6"`, `"stoch_v5"` 전환 가능)
- 후보 순위 (`rank_by="clenow"`): 90일 로그 가격 회귀 기울기(연율화) × $R^2$ (`CLENOW90`) 높은 순 (`"rs60"`, `"k"` 전환 가능)
- 포지션 사이징 (`sizing="inverse_vol"` + `vol_targeting=true`): `총자산 * target_risk(0.018) * market_vol_scale(0.16, 0.35~1.25) / max(VOL20, 0.10)`, 단건 상한 `max_pos_weight(0.11)` 1회 완결 매수
- 매도 (`exit_mode="E3_atr"`): 이익 중이면 20일선 안착 후 종가 20일선 이탈 시 매도 / 손실 중이면 `Close < entry_px - atr_stop_mult(2.5) * ATR14` 또는 `Close < SMA200` 즉시 매도 — `strategy.evaluate_position`, 근거 `backtest_research_v7.py` · `RESEARCH_V7.md`
- 시장 필터: KR은 `069500`(KODEX 200) > 200일선, US는 `SPY` > 200일선 (이탈 시 신규 매수 중지)
- 관심종목: KR은 `watchlist.csv` 우선, 없으면 `screener.py` 결과(최근 12개월 CB 발행사 제외, `cb_event_study.py` 근거). US는 `us_bluechips.csv`(65종목) 기본 사용

## 실행 흐름
```
16:10 run_daily.cmd / .sh  → screener.py → run_daily.py [--market KR|US] (장부·신호·대기주문·리포트·대시보드)
08:40 run_orders.cmd / .sh → run_orders.py (KR mock·live 모드만 키움 시장가 주문; US는 paper 장부/연구 모드)
```
설정은 `config.json`(mode: paper/mock/live, limits, strategy). 비밀값은 `config.local.json`(커밋 금지).

## 절대 규칙
1. **비밀값 커밋 금지**: `config.local.json`, `state/`, `.env*`, 토큰. 커밋 전 `git grep -nE "appkey|secretkey|crtfc_key|telegram_bot_token"`로 값이 없는지 확인. 키를 코드·로그·출력에 쓰지 않는다.
2. **실전(live) 안전장치를 약하게 만들지 않는다**: live = `config.json live.enabled=true` + `--live` 플래그 이중 잠금, `KILL_SWITCH`, 1건·하루 한도, 손실 한도, 만료 주문 차단, 읽기 전용 잔고 조회. 바꾸려면 사람 둘 다 동의한 PR로만.
3. **`config.json`의 mode를 AI가 임의로 mock/live로 바꾸지 않는다.** 사람이 바꾼다.
4. **미래 데이터 금지**: 신호는 그날 종가까지만. 주봉은 직전 완성 주. 새 지표·필터는 "잘라서 계산한 값 = 전체로 계산한 값" 테스트를 추가한다(`tests/`).
5. **전략을 바꾸면 백테스트 근거를 남긴다**: `backtest_research_v7.py` 등에 IS/OOS + 무작위/고원/Monte Carlo 검증을 추가 → 결과를 `RESEARCH_V7.md` / `README.md`에 반영. 비용(KR 매수 0.065%·매도 0.265%, US 각 0.05%) 빼고 비교 금지. 과적합 주의(표본 부족 최고점·손실한도 `-15%` 초과안 채택 금지).
6. **테스트 통과 후 커밋**: `python -m pytest -q`. CI(GitHub Actions)도 같은 테스트를 돈다.
7. 실제 개인정보(이름, 회사, 계좌번호, 개인 경로)를 코드·문서에 쓰지 않는다 — 공개 저장소다.

## 자주 하는 작업
| 하고 싶은 것 | 어디를 |
|---|---|
| 매수/매도·사이징·순위 규칙 바꾸기 | `strategy.py` · `config.json` `strategy` · `settings.py` → `backtest_research_v7.py`로 검증 |
| 관심종목 직접 지정 | `watchlist.csv` (KR) / `us_bluechips.csv` (US) |
| 한도·자금·종목 수·전략 옵션 | `config.json` |
| 시세 로더(FDR/yfinance)·알림 | `market_data.py`, `notify.py` |
| 증권사 바꾸기 | `broker.py`에 새 Broker 클래스 (holdings/orderable_cash/buy/sell) |
| 대시보드 모양 | `dashboard.py` / README 그림 `make_figures.py` |
| 과거로 검증 | `python replay.py 2026-01-02 --top 50` · `python replay.py 2026-01-02 --market US` |
| 상태 진단 | `python run_daily.py --check` · `python run_daily.py --market US --check` (읽기 전용) |

## 환경
Python 3.12+ (`pip install -r requirements.txt`). 데이터: `FinanceDataReader`(KR 일봉), `yfinance`(US 일봉), OpenDART(KR 전환사채, 키 필요), 키움 REST API(KR 주문, 키 필요).
OS 전용 경로를 코드에 하드코딩하지 않는다(`Path(__file__).parent` 기준).
