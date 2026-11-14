# CLAUDE.md — Stock Autotrader 작업 규칙 (Claude Code가 이 폴더에서 자동으로 읽음)

> 사람이 읽을 설명은 `README.md`(한눈에) · `프로젝트_설명.md`(자세히) · `OPERATIONS.md`(실전 운영) · `CONTRIBUTING.md`(협업).
> 이 파일은 **Claude가 코드를 고칠 때 지킬 규칙**만 적는다.

## 프로젝트 한 줄
국내주식 일봉 **스토캐스틱(14,5,5)** 스윙 자동매매. 형님(실전 투자자)의 매매법을 규칙으로 옮겼다. 핵심은 스토캐스틱 — 다른 지표(RSI·MACD 등)는 검증 결과 도움이 안 됐다.

## 매매 규칙 (현재 v4) — 바꾸려면 반드시 백테스트 근거와 함께
- 매수: 최근 5일 안에 %K<20, 지금 %K<30, (두 선 간격 3일 연속 축소 + %K 상승) 또는 골든크로스 — `backtest_hyung2.prep` BUY
- 필터: 시장(KODEX200 > 200일선) · 종목(200일선 위, 120일 수익률>0) · 주봉(직전 완성 주봉 %K>%D, `backtest_hyung3.weekly_ok`)
- 물타기: 보유 중 %K<20에서 두 선 2일 연속 벌어짐, 직전 매수 후 5거래일, 최대 3회(1회=배정금 1/3) — `prep` ADD
- 매도(v4): 이익 중이면 %K가 80 위로 갔다가 꺾일 때 / 손실 중이면 데드크로스(예상) — `run_daily.py`, 근거 `backtest_exits.py` E2
- 손절 없음(꼬리 손절 효과 없음, `backtest_v3_extra.py`). 신호는 종가 판단 → 다음 날 시가 체결
- 관심종목: `watchlist.csv`(사람이 관리) 우선, 없으면 `screener.py` 결과. 최근 12개월 전환사채 발행사 제외(`cb_event_study.py` 근거)

## 실행 흐름
```
16:10 run_daily.cmd  → screener.py → run_daily.py (장부 갱신·신호·대기주문·리포트·dashboard/index.html)
08:40 run_orders.cmd → run_orders.py (mock·live 모드만 증권사로 시장가 주문)
```
설정은 `config.json`(mode: paper/mock/live, 한도). 비밀값은 `config.local.json`(커밋 금지).

## 절대 규칙
1. **비밀값 커밋 금지**: `config.local.json`, `state/`, `.env*`, 토큰. 커밋 전 `git grep -nE "appkey|secretkey|crtfc_key"`로 값이 없는지 확인. 키를 코드·로그·출력에 쓰지 않는다.
2. **실전(live) 안전장치를 약하게 만들지 않는다**: live = `config.json live.enabled=true` + `--live` 플래그 이중 잠금, `KILL_SWITCH`, 1건·하루 한도, 손실 한도. 바꾸려면 사람(유찬·형님) 둘 다 동의한 PR로만.
3. **`config.json`의 mode를 Claude가 임의로 mock/live로 바꾸지 않는다.** 사람이 바꾼다.
4. **미래 데이터 금지**: 신호는 그날 종가까지만. 주봉은 직전 완성 주. 새 지표·필터는 "잘라서 계산한 값 = 전체로 계산한 값" 테스트를 추가한다(`tests/`).
5. **전략을 바꾸면 백테스트 근거를 남긴다**: 새 `backtest_*.py` 또는 기존 것에 케이스 추가 → 결과를 README/프로젝트_설명 표에 반영. 비용(수수료 0.015%, 거래세 0.20%, 슬리피지) 빼고 비교 금지. 과적합 주의(거래 수가 적은 최고점 파라미터는 채택하지 않음).
6. **테스트 통과 후 커밋**: `python -m pytest -q`. CI(GitHub Actions)도 같은 테스트를 돈다.
7. 실제 개인정보(이름, 회사, 계좌번호, 개인 경로)를 코드·문서에 쓰지 않는다 — 공개 저장소다.

## 자주 하는 작업
| 하고 싶은 것 | 어디를 |
|---|---|
| 매수/매도 조건 바꾸기 | `backtest_hyung2.prep`(신호 정의) · `run_daily.py`(v4 매도) → `backtest_exits.py`로 비교 |
| 관심종목 직접 지정 | `watchlist.csv` (Code,Name) |
| 한도·자금·종목 수 | `config.json` |
| 증권사 바꾸기 | `broker.py`에 새 Broker 클래스 (holdings/orderable_cash/buy/sell) |
| 대시보드 모양 | `dashboard.py` / README 그림 `make_figures.py` |
| 과거로 검증 | `python replay.py 2026-01-02 --top 50` (미래 데이터 차단 리플레이) |

## 환경
Python 3.12+ (`pip install -r requirements.txt`). 데이터: FinanceDataReader(일봉), OpenDART(전환사채, 키 필요), 키움 REST API(주문, 키 필요).
Windows 경로를 코드에 하드코딩하지 않는다(`Path(__file__).parent` 기준).
