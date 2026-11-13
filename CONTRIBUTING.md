# 같이 고치는 방법

두 사람(+ 각자의 Claude Code)이 같은 저장소를 고친다. 규칙은 단순하게.

## 처음 한 번
1. GitHub 초대 수락 (Alpha-Crew-Labs/stock-autotrader)
2. [Claude Code](https://claude.com/claude-code) 설치 → 저장소 받기
   ```bash
   git clone https://github.com/Alpha-Crew-Labs/stock-autotrader.git
   cd stock-autotrader
   pip install -r requirements.txt
   claude
   ```
3. Claude가 `CLAUDE.md`를 자동으로 읽는다 — 프로젝트 규칙·매매 규칙·금지사항이 들어 있다

## Claude에게 이렇게 말하면 된다 (예시)
- "매도 조건을 %K 70에서 꺾이면 파는 걸로 바꾸면 어떤지 백테스트해서 비교해줘"
- "watchlist.csv에 내 관심종목 10개 넣어줘: ..."
- "오늘 대시보드 보고 이상한 거 있는지 봐줘"
- "이 변경 브랜치 만들어서 PR 올려줘"

## 작업 순서
1. **브랜치에서 작업**: `main`에 바로 올리지 않는다 → `git switch -c 바꿀내용` (Claude에게 "브랜치 만들어서 해줘")
2. **전략을 바꾸면 백테스트 근거 첨부**: 이전 vs 이후 수익팩터·거래당 평균·최대낙폭 표를 PR 설명에
3. **테스트 통과**: `python -m pytest -q` (GitHub에서도 자동으로 돈다 — 빨간 X면 머지 금지)
4. **PR 올리고 상대방이 확인 후 머지**

## 꼭 지킬 것
- 🔑 키·계좌·`config.local.json`·`state/`는 절대 커밋하지 않는다 (공개 저장소)
- 🛑 실전 안전장치(이중 잠금, 한도, KILL_SWITCH)를 약하게 하는 변경은 두 사람 모두 동의 후
- 📊 "좋아 보이는" 결과는 비용 반영·거래 수·과적합부터 의심한다
- `config.json`의 `mode`(paper/mock/live)는 실제로 돌리는 사람이 직접 바꾼다

## 역할 (제안)
| | 형님 | 유찬 |
|---|---|---|
| 매매 규칙·종목 판단 | ✅ 주도 | 검증 |
| 백테스트·코드·자동화 | 아이디어 | ✅ 주도 |
| 관심종목 `watchlist.csv` | ✅ | ✅ |
