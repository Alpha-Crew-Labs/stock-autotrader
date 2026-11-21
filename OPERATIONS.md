# 실전 운영 가이드

> 모의 장부(paper) → 키움 모의투자(mock, KR 전용) → 실전(live, KR 전용) 순서로만 올라간다. 단계를 건너뛰지 않는다.

## 0. 처음 설치 (한 번)

```bash
git clone https://github.com/Alpha-Crew-Labs/stock-autotrader.git
cd stock-autotrader
pip install -r requirements.txt
python -m pytest -q          # 173건 전부 통과해야 정상
```
`config.local.json` 만들기 (이 파일은 GitHub에 안 올라감):
```json
{
  "dart_api_key": "OpenDART 키 (KR 스크리너의 전환사채 제외용, opendart.fss.or.kr 무료)",
  "kiwoom_mock_appkey": "모의투자 앱키 (KR 전용)",
  "kiwoom_mock_secretkey": "모의투자 시크릿 (KR 전용)",
  "kiwoom_appkey": "실전 앱키 (KR 실전 단계에서만)",
  "kiwoom_secretkey": "실전 시크릿 (KR 실전 단계에서만)",
  "telegram_bot_token": "(선택) 텔레그램 봇 토큰",
  "telegram_chat_id": "(선택) 텔레그램 채팅 ID"
}
```
키움 키 발급 절차는 `KIWOOM_README.md`.

## 1. 단계별 운영

| 단계 | config.json `mode` | 무엇이 일어나나 | 다음 단계로 가는 기준 |
|---|---|---|---|
| ① 모의 장부 | `paper` | KR · US 모두 신호대로 장부에만 기록 (돈·주문 없음) | 3개월 이상, 청산 20건 이상, 수익팩터 ≥ 1.0 |
| ② 키움 모의투자 | `mock` | KR 전용 키움 **모의 서버**로 진짜 주문 → 잔고로 장부를 맞춤 | 1개월 이상 주문·체결·장부가 어긋나지 않음 |
| ③ 실전 | `live` | KR 실제 계좌로 주문 | — |

> 💡 미국 시장(`--market US`)은 현재 **`paper` 모의 장부 및 백테스트/리플레이 연구 전용**이다. 키움 자동 전송(`run_orders.py`)은 KR 주문만 처리한다.

실전 켜기 (이중 잠금):
1. `config.json` → `"mode": "live"`, `"live": {"enabled": true}`
2. 아침 주문을 `python run_orders.py --live` 로 실행 (`run_orders.cmd` 또는 `run_orders.sh`에 `--live` 추가)
3. 처음 1개월은 `limits.max_order_krw`를 작게(예: 30만 원)

## 2. 매일 일과 (Windows 작업 스케줄러 또는 macOS/Linux cron·launchd)

| 시각 (평일) | 실행 | 하는 일 |
|---|---|---|
| 08:40 | `run_orders.cmd` / `run_orders.sh` | 어제 만든 KR 대기주문을 시장가로 전송 (장전 동시호가 → 시가 체결). paper면 아무것도 안 함 |
| 16:10 | `run_daily.cmd` / `run_daily.sh` | KR 스크리너 → 잔고/체결/단기채 파킹 반영 → v8 레짐/신호 → 내일 주문 → 리포트·대시보드 |
| (선택) 미국 마감 후 | `python run_daily.py --market US` | `us_bluechips.csv` 65종목 `yfinance` 시세로 US 모의 장부(`state/paper_us.json`, `dashboard_us/`) 갱신 |

- PC가 그 시각에 **켜져 있고 절전이 아니어야** 한다
- 상태 진단: `python run_daily.py --check` 및 `python run_daily.py --market US --check` (파일 변경 없이 설정·키·`KILL_SWITCH`·최신성만 점검)
- 기록: KR(`state/paper.json`, `reports/daily_날짜.md`, `dashboard/`) · US(`state/paper_us.json`, `reports_us/daily_날짜.md`, `dashboard_us/`) · 주문(`logs/run.log`, `logs/orders.log`, `state/orders_날짜.json`)
- 확인: `dashboard/index.html` 또는 `dashboard_us/index.html`

## 3. 안전장치

| 장치 | 동작 | 설정 |
|---|---|---|
| 이중 잠금 | live는 `live.enabled=true` + `--live` 둘 다 있어야 | — |
| 읽기 전용 잔고 | 오후 장부 맞춤(`run_daily.py`)은 `read_only=True` 브로커만 사용(주문 권한 없음) | — |
| 1건 상한 | 주문 1건 금액 상한 | `limits.max_order_krw` |
| 하루 매수 상한 | 하루 매수 합계 상한 | `limits.max_daily_buy_krw` |
| 손실 한도 | 하루 -3% 또는 고점 대비 -15% → `KILL_SWITCH` 자동 생성 → 다음 날부터 주문 중지 | `limits.daily_loss_stop_pct`, `max_drawdown_stop_pct` |
| 시장 레짐·변동성 타게팅 | 지수 20일 변동성(`0.35~1.25배`), `Vol20/Vol60>1.50` 시 신규 진입 정지, `SMA200<지수≤SMA50` 시 `0.5x` 감속 | `strategy.vol_targeting`, `vol_ratio_brake`, `yellow_light_mult` |
| 유휴현금 단기채 파킹 | `paper` 장부 유휴현금을 `153130`(KR)·`BIL`(US)에 2% 버퍼·5% 데드밴드로 자동 파킹 (`mock`/`live`는 권고 안내) | `strategy.cash_park`, `cash_park_etf_kr/us` |
| ATR/200일선 손절 | v8(`E3_atr`)에서 손실 중 종목이 진입가 `-2.5*ATR14` 또는 `SMA200` 이탈 시 자동 매도 | `strategy.atr_stop_mult`, `exit_mode` |
| 긴급 정지 | 폴더에 `KILL_SWITCH` 파일이 있으면 주문 0건. **사람이 직접 만들어도 됨** | 지우면 해제 |
| 중복 방지 | 같은 날 두 번 실행해도 `run_daily.py` 대기주문 및 `run_orders.py` 주문은 1회만 생성·전송 | `state/orders_날짜.json` |
| 만료 주문 차단 | `created` 기준 3영업일 초과 미체결 **매수** 대기주문은 아침 전송에서 자동 제외(매도는 유지) | `strategy.max_pending_age_bdays` |
| 시세 오류 격리 | 보유종목 시세 조회 실패 시 직전가로 평가 유지(거짓 손실한도 방지), 주문 시 종목별 예외 격리 | — |
| 원자적 저장 | 임시 파일 기록 후 교체(`os.replace`)로 동기화 중 상태 파일 손상 방지 | — |
| 잔고 우선 | 매도 수량·보유 종목은 증권사 잔고 기준 (수동 매매도 반영) | — |

## 4. 문제가 생기면

| 증상 | 확인 / 조치 |
|---|---|
| 대시보드 "데이터 최신성" 노랑·빨강 | 16:10 실행이 안 됨 → PC 절전·스케줄러 경로 확인, `logs/run.log` |
| "만료된 대기주문" 건너뜀 | 3영업일 넘게 체결되지 않은 매수 주문 자동 차단. 당일 오후 신호로 재판단 |
| "미체결" 로그 | 상한가·거래정지 등. 다음 날 신호로 다시 판단 (자동) |
| "장부에 없던 보유" | 수동으로 산 종목. 장부에 1회 매수로 편입됨 |
| 키움 오류 8010 | 등록하지 않은 IP → 키움 사이트에서 IP 등록 |
| 키움 오류 8030/8031 | 실전·모의 키를 섞어 씀 → config.local.json 확인 |
| 크게 잃는 중 | `KILL_SWITCH` 파일 만들기 (즉시 주문 중지) → 원인 확인 |

## 5. 주의
- 직장에 임직원 개인 매매 규정(신고 계좌, 매매 보고 등)이 있으면 실전 전에 확인
- 백테스트는 생존 편향(현재 대형주/우량주 기준)이 있다. 실전 성과는 모의 단계에서 확인된 숫자를 기준으로 기대한다
- 미국 주식 실전 확장 시에는 환전·해외주식 양도세·시간차 체결 규칙을 별도 검토한다
- 투자 판단과 책임은 사람에게 있다
