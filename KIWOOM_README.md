# 키움 REST API 연결 (kiwoom_client.py)

## 1. 사용자가 할 일 (최초 1회)

1. **키움 계좌**(비대면 개설 가능) + 키움 홈페이지 로그인(공동/간편인증).
2. **키움 REST API 사이트** https://openapi.kiwoom.com → `API 사용신청` → 약관 동의 후 신청. 신청 현황이 `등록`이면 완료.
3. **모의투자 신청**(먼저 모의로 시험): 키움 홈페이지 `모의투자` 메뉴에서 상시 모의투자 참가 신청 → 모의투자 계좌 발급. 이후 REST API 사이트에서 모의투자용 앱키를 따로 발급받는다(발급 메뉴 위치는 미확인 — 사이트 안내를 따른다. **실전 앱키와 모의 앱키는 별개** — 섞어 쓰면 오류 8030/8031).
4. **IP 등록**: REST API 사이트 `계좌 App Key 관리`에서 이 PC의 공인 IP(`현재 나의 IP`)를 등록(최대 10개). 공유기 재부팅·장소 이동으로 IP가 바뀌면 다시 등록해야 한다(오류 8010 = IP 불일치).
5. 계좌 등록 → **App Key / Secret Key 발급**. 비밀번호처럼 관리한다.
6. 3개월 이상 미사용 시 해지될 수 있다는 안내가 있으니 공식 공지 확인.

## 2. 키 넣는 곳

같은 폴더 `config.local.json`(기존 `dart_api_key`는 그대로 두고 키만 추가):

```json
{
  "dart_api_key": "(기존 값 유지)",
  "kiwoom_appkey": "실전 앱키",
  "kiwoom_secretkey": "실전 시크릿",
  "kiwoom_mock_appkey": "모의 앱키",
  "kiwoom_mock_secretkey": "모의 시크릿",
  "kiwoom_account": "계좌번호(표시용)"
}
```

- `mock=True`(기본)일 때 `kiwoom_mock_*`가 있으면 그것을, 없으면 `kiwoom_appkey/secretkey`를 쓴다. 모의 키만 있다면 `kiwoom_appkey/secretkey`에 모의 키를 넣어도 된다.
- 계좌번호는 요청에 쓰이지 않는다(앱키가 계좌에 묶여 있음).
- 토큰은 `state\kiwoom_token.json`에 만료시각과 함께 저장(실전/모의 따로, 키가 바뀌면 자동 재발급). 이 폴더는 구글 드라이브로 동기화되므로 키·토큰 파일을 외부 공유하지 않는다.

## 3. 확인

```
set PYTHONIOENCODING=utf-8
python kiwoom_client.py
```
키가 없으면 안내 후 종료, 키가 있으면 모의 서버에서 토큰 발급 + 삼성전자(005930) 일봉 마지막 3행 출력. 주문은 하지 않는다.

## 4. 사용 예

```python
from kiwoom_client import KiwoomClient
c = KiwoomClient()                     # 모의(mockapi.kiwoom.com)
df = c.daily_chart("005930", 250)      # Open/High/Low/Close/Volume
c.price("005930"); c.balance(); c.cash()
KiwoomClient(dry_run=True).buy("005930", 1)          # 전송 없이 요청 내용만 로그
c.buy("005930", 1)                      # 모의 시장가 매수
c.sell("005930", 1, 70000)              # 모의 지정가 매도
KiwoomClient(mock=False, allow_live=True)  # 실전 주문은 이 두 플래그를 모두 줘야만 가능
```

## 5. 쓰는 API (공식 GitHub `Kiwoom-Securities/Kiwoom-REST-API` 스펙으로 확인)

| 기능 | api-id | 경로 |
|---|---|---|
| 토큰 발급 | au10001 | /oauth2/token |
| 주식기본정보(현재가) | ka10001 | /api/dostk/stkinfo |
| 일봉 차트 | ka10081 | /api/dostk/chart |
| 매수 / 매도 | kt10000 / kt10001 | /api/dostk/ordr |
| 계좌평가잔고 | kt00018 | /api/dostk/acnt |
| 예수금 상세 | kt00001 | /api/dostk/acnt |

미확인: 정확한 초당 호출 한도(0.25초 간격 + 한도 초과 코드 1700~1702 재시도로 대응), 모의투자의 NXT/SOR 지원 여부(기본 KRX).
