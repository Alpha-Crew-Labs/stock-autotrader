"""키움증권 REST API 클라이언트 (개인 자동매매용).

스펙 출처(2026-11-13 확인): 키움 공식 GitHub `Kiwoom-Securities/Kiwoom-REST-API`
  - postman/kiwoom-openapi.postman_collection.json (PRD/MOCK 요청 예시)
  - kiwoom/_data/kiwoom_api_spec.json (openapi.kiwoom.com 명세를 옮긴 337개 API 스펙 + 오류코드표)
  - kiwoom/core/auth.py, client.py, errors.py (공식 런타임)

검증된 항목 (공식 스펙과 일치):
  호스트   실전 https://api.kiwoom.com / 모의 https://mockapi.kiwoom.com
  au10001  POST /oauth2/token      body {grant_type:"client_credentials", appkey, secretkey}
           응답 {token, token_type:"bearer", expires_dt:"YYYYMMDDHHMMSS"(KST), return_code, return_msg}
  ka10001  POST /api/dostk/stkinfo body {stk_cd}                    주식기본정보요청
  ka10081  POST /api/dostk/chart   body {stk_cd, base_dt, upd_stkpc_tp} 주식일봉차트조회요청
           응답 리스트 키 stk_dt_pole_chart_qry: dt, open_pric, high_pric, low_pric, cur_prc(종가), trde_qty
  kt10000  POST /api/dostk/ordr    body {dmst_stex_tp, stk_cd, ord_qty, ord_uv, trde_tp, cond_uv} 매수
  kt10001  POST /api/dostk/ordr    (kt10000과 동일 필드)                                      매도
           trde_tp: 0=보통(지정가), 3=시장가 ... / 응답 ord_no
  kt00018  POST /api/dostk/acnt    body {qry_tp:"1"(합산)|"2"(개별), dmst_stex_tp:"KRX"|"NXT"} 계좌평가잔고내역
           응답 리스트 키 acnt_evlt_remn_indv_tot
  kt00001  POST /api/dostk/acnt    body {qry_tp:"3"(추정)|"2"(일반)}  예수금상세현황
  헤더     Content-Type: application/json;charset=UTF-8, api-id, authorization: Bearer <token>,
           cont-yn / next-key (연속조회: 응답 헤더 cont-yn=Y 이면 next-key 를 요청 헤더에 그대로 실어 재요청)
  오류     return_code != 0 이면 실패. 1700/1701/1702 = 요청 한도 초과, 8005 등 = 토큰 무효,
           8030/8031 = 실전/모의 앱키·토큰 불일치, 8010 = 토큰 발급 IP와 요청 IP 불일치, 8104 = 모의 미지원 API

검증 못 한 항목 ([미검증] 표시):
  - 정확한 초당 요청 한도 수치(공식 스펙에 수치 없음) → 보수적으로 요청 간 최소 0.25초 간격 + 1700대 코드 재시도
  - 모의투자에서 dmst_stex_tp 로 NXT/SOR 지원 여부 → 기본 KRX 사용
  - 계좌번호는 요청 본문에 들어가지 않는다(앱키가 계좌에 묶임). kiwoom_account 는 표시·확인용
  - 일봉 1페이지당 행 수(스펙에 없음) → cont-yn 기준으로 필요한 만큼 페이징
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.local.json"
STATE_DIR = HERE / "state"
TOKEN_PATH = STATE_DIR / "kiwoom_token.json"

REAL_HOST = "https://api.kiwoom.com"
MOCK_HOST = "https://mockapi.kiwoom.com"
KST = timezone(timedelta(hours=9))

RATE_LIMIT_CODES = {1700, 1701, 1702}
TOKEN_INVALID_CODES = {8003, 8005, 8006, 8009, 8103}
MIN_INTERVAL_SEC = 0.25  # [미검증] 공식 한도 수치 미공개 → 보수적 간격
MAX_RETRIES = 4

log = logging.getLogger("kiwoom")


class KiwoomError(RuntimeError):
    def __init__(self, msg: str, return_code: int | None = None):
        super().__init__(msg)
        self.return_code = return_code


class KiwoomConfigError(KiwoomError):
    pass


class KiwoomSafetyError(KiwoomError):
    pass


def _num(v: Any) -> float:
    """'+00000012345', '-1,234', '' 같은 키움 숫자 문자열 → float. 가격 부호는 호출부에서 abs."""
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", "")
    if s in ("", "+", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


class KiwoomClient:
    """키움 REST API 클라이언트.

    mock=True(기본)  → mockapi.kiwoom.com, 모의투자 앱키 사용
    allow_live=True → 실전 주문 허용(이 플래그 없이는 mock=False 여도 주문 거부)
    dry_run=True    → 주문 요청을 보내지 않고 (비밀값 가린) 요청 내용만 로그로 남김
    """

    def __init__(self, mock: bool = True, allow_live: bool = False, dry_run: bool = False,
                 config_path: Path | str = CONFIG_PATH, timeout: float = 10.0):
        self.mock = bool(mock)
        self.allow_live = bool(allow_live)
        self.dry_run = bool(dry_run)
        self.host = MOCK_HOST if self.mock else REAL_HOST
        self.timeout = timeout
        self._session = requests.Session()
        self._last_call = 0.0
        self._token: str | None = None
        self._token_exp: datetime | None = None
        self._appkey, self._secretkey, self.account = self._load_config(Path(config_path))

    # ------------------------------------------------------------------ config
    def _load_config(self, path: Path) -> tuple[str, str, str]:
        if not path.exists():
            raise KiwoomConfigError(f"설정 파일이 없습니다: {path}")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        # 키움 앱키는 실전/모의가 별개(오류 8030). 모의는 kiwoom_mock_* 가 있으면 우선 사용.
        if self.mock and cfg.get("kiwoom_mock_appkey") and cfg.get("kiwoom_mock_secretkey"):
            ak, sk = cfg["kiwoom_mock_appkey"], cfg["kiwoom_mock_secretkey"]
        else:
            ak, sk = cfg.get("kiwoom_appkey"), cfg.get("kiwoom_secretkey")
        missing = [k for k, v in (("kiwoom_appkey", ak), ("kiwoom_secretkey", sk)) if not v]
        if missing:
            raise KiwoomConfigError(
                "config.local.json 에 키움 키가 없습니다: " + ", ".join(missing)
                + " (KIWOOM_README.md 참고)")
        return str(ak).strip(), str(sk).strip(), str(cfg.get("kiwoom_account") or "").strip()

    @property
    def _fingerprint(self) -> str:
        return hashlib.sha256(f"{self._appkey}:{self._secretkey}".encode()).hexdigest()[:16]

    @property
    def _mode(self) -> str:
        return "mock" if self.mock else "real"

    # ------------------------------------------------------------------ token
    def _load_cached_token(self) -> bool:
        try:
            data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
            rec = data.get(self._mode) or {}
            if rec.get("fp") != self._fingerprint:
                return False
            exp = datetime.fromisoformat(rec["expires_at"])
            if exp - datetime.now(KST) < timedelta(minutes=10):
                return False
            self._token, self._token_exp = rec["token"], exp
            return True
        except (OSError, ValueError, KeyError):
            return False

    def _save_token(self) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        try:
            data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[self._mode] = {"token": self._token, "expires_at": self._token_exp.isoformat(),
                            "fp": self._fingerprint}
        tmp = TOKEN_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, TOKEN_PATH)

    def _issue_token(self) -> None:
        self._throttle()
        r = self._session.post(
            f"{self.host}/oauth2/token",
            headers={"Content-Type": "application/json;charset=UTF-8", "api-id": "au10001"},
            json={"grant_type": "client_credentials", "appkey": self._appkey, "secretkey": self._secretkey},
            timeout=self.timeout)
        try:
            d = r.json()
        except ValueError:
            raise KiwoomError(f"토큰 발급 실패: HTTP {r.status_code} (JSON 아님)")
        code = int(_num(d.get("return_code", 0)))
        if r.status_code >= 400 or code != 0 or not d.get("token"):
            raise KiwoomError(f"토큰 발급 실패 ({code}): {d.get('return_msg', '')}", code)
        self._token = d["token"]
        exp = d.get("expires_dt")
        self._token_exp = (datetime.strptime(exp, "%Y%m%d%H%M%S").replace(tzinfo=KST)
                           if exp else datetime.now(KST) + timedelta(hours=12))
        self._save_token()
        log.info("키움 토큰 발급(%s), 만료 %s", self._mode, self._token_exp.strftime("%Y-%m-%d %H:%M"))

    def token(self, force: bool = False) -> str:
        """유효 토큰 확보(캐시 → 없으면 발급). 반환값은 출력·로그하지 말 것."""
        if not force and self._token and self._token_exp and self._token_exp - datetime.now(KST) > timedelta(minutes=10):
            return self._token
        if not force and self._load_cached_token():
            return self._token  # type: ignore[return-value]
        self._issue_token()
        return self._token  # type: ignore[return-value]

    # ------------------------------------------------------------------ core
    def _throttle(self) -> None:
        wait = MIN_INTERVAL_SEC - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _post(self, path: str, api_id: str, body: dict, cont_yn: str = "", next_key: str = "") -> tuple[dict, dict]:
        """요청 1회(재시도 포함). (응답 JSON, 응답 헤더) 반환."""
        token_retried = False
        for attempt in range(MAX_RETRIES + 1):
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "api-id": api_id,
                "authorization": f"Bearer {self.token()}",
            }
            if cont_yn:
                headers["cont-yn"] = cont_yn
                headers["next-key"] = next_key
            self._throttle()
            try:
                r = self._session.post(f"{self.host}{path}", headers=headers, json=body, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt >= MAX_RETRIES:
                    raise KiwoomError(f"{api_id} 네트워크 오류: {type(e).__name__}")
                time.sleep(1.0 * (attempt + 1))
                continue
            try:
                d = r.json()
            except ValueError:
                d = {}
            code = int(_num(d.get("return_code", 0))) if d else None
            msg = str(d.get("return_msg", "")) if d else ""
            if r.status_code == 429 or code in RATE_LIMIT_CODES or r.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    raise KiwoomError(f"{api_id} 요청 한도/서버 오류 반복 (HTTP {r.status_code}, {code}): {msg}", code)
                time.sleep(1.0 * (2 ** attempt))
                continue
            if (r.status_code == 401 or code in TOKEN_INVALID_CODES) and not token_retried:
                token_retried = True
                self.token(force=True)
                continue
            if r.status_code >= 400 or code is None:
                raise KiwoomError(f"{api_id} HTTP {r.status_code}: {msg or r.text[:200]}", code)
            if code != 0:
                raise KiwoomError(f"{api_id} 오류 ({code}): {msg}", code)
            return d, {k.lower(): v for k, v in r.headers.items()}
        raise KiwoomError(f"{api_id} 재시도 초과")

    # ------------------------------------------------------------------ 시세
    def daily_chart(self, code: str, n_days: int = 250, base_dt: str | None = None, adjusted: bool = True):
        """ka10081 일봉. 반환: DataFrame[Open, High, Low, Close, Volume], DatetimeIndex 오름차순."""
        import pandas as pd

        body = {"stk_cd": code, "base_dt": base_dt or datetime.now(KST).strftime("%Y%m%d"),
                "upd_stkpc_tp": "1" if adjusted else "0"}
        rows: list[dict] = []
        cont, nkey = "", ""
        for _ in range(200):  # 안전 상한
            d, h = self._post("/api/dostk/chart", "ka10081", body, cont, nkey)
            rows.extend(d.get("stk_dt_pole_chart_qry") or [])
            if len(rows) >= n_days or h.get("cont-yn", "N") != "Y" or not h.get("next-key"):
                break
            cont, nkey = "Y", h["next-key"]
        recs = [{
            "Date": pd.to_datetime(x["dt"], format="%Y%m%d"),
            "Open": abs(_num(x.get("open_pric"))), "High": abs(_num(x.get("high_pric"))),
            "Low": abs(_num(x.get("low_pric"))), "Close": abs(_num(x.get("cur_prc"))),
            "Volume": abs(_num(x.get("trde_qty"))),
        } for x in rows if x.get("dt")]
        if not recs:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"],
                                index=pd.DatetimeIndex([], name="Date"))
        df = pd.DataFrame(recs).drop_duplicates("Date").set_index("Date").sort_index()
        return df.tail(n_days)

    def price(self, code: str) -> dict:
        """ka10001 주식기본정보. 주요 값은 숫자로 정리, 원본은 raw."""
        d, _ = self._post("/api/dostk/stkinfo", "ka10001", {"stk_cd": code})
        return {
            "code": code, "name": d.get("stk_nm", ""),
            "price": abs(_num(d.get("cur_prc"))), "change": _num(d.get("pred_pre")),
            "change_pct": _num(d.get("flu_rt")), "open": abs(_num(d.get("open_pric"))),
            "high": abs(_num(d.get("high_pric"))), "low": abs(_num(d.get("low_pric"))),
            "volume": _num(d.get("trde_qty")), "upper_limit": abs(_num(d.get("upl_pric"))),
            "lower_limit": abs(_num(d.get("lst_pric"))), "raw": d,
        }

    # ------------------------------------------------------------------ 계좌
    def balance(self, exchange: str = "KRX") -> dict:
        """kt00018 계좌평가잔고내역(합산). 반환 {summary: {...}, holdings: DataFrame}."""
        import pandas as pd

        body = {"qry_tp": "1", "dmst_stex_tp": exchange}
        items: list[dict] = []
        first: dict = {}
        cont, nkey = "", ""
        for _ in range(50):
            d, h = self._post("/api/dostk/acnt", "kt00018", body, cont, nkey)
            first = first or d
            items.extend(d.get("acnt_evlt_remn_indv_tot") or [])
            if h.get("cont-yn", "N") != "Y" or not h.get("next-key"):
                break
            cont, nkey = "Y", h["next-key"]
        summary = {k: _num(first.get(k)) for k in (
            "tot_pur_amt", "tot_evlt_amt", "tot_evlt_pl", "tot_prft_rt", "prsm_dpst_aset_amt")}
        holdings = pd.DataFrame([{
            "code": str(x.get("stk_cd", "")).lstrip("AJQ"), "name": x.get("stk_nm", ""),
            "qty": _num(x.get("rmnd_qty")), "sellable_qty": _num(x.get("trde_able_qty")),
            "avg_price": _num(x.get("pur_pric")), "cur_price": abs(_num(x.get("cur_prc"))),
            "eval_amt": _num(x.get("evlt_amt")), "pnl": _num(x.get("evltv_prft")),
            "pnl_pct": _num(x.get("prft_rt")),
        } for x in items])
        return {"summary": summary, "holdings": holdings}

    def cash(self) -> dict:
        """kt00001 예수금상세현황(추정조회)."""
        d, _ = self._post("/api/dostk/acnt", "kt00001", {"qry_tp": "3"})
        return {
            "deposit": _num(d.get("entr")), "d1": _num(d.get("d1_entra")), "d2": _num(d.get("d2_entra")),
            "orderable": _num(d.get("ord_alow_amt")), "withdrawable": _num(d.get("pymn_alow_amt")),
            "raw": d,
        }

    # ------------------------------------------------------------------ 주문
    def _order(self, api_id: str, code: str, qty: int, price: float | None, exchange: str) -> dict:
        if int(qty) <= 0:
            raise ValueError("qty 는 1 이상이어야 합니다")
        if not self.mock and not self.allow_live:
            raise KiwoomSafetyError("실전 주문 거부: mock=False 인데 allow_live=True 가 아닙니다.")
        body = {
            "dmst_stex_tp": exchange, "stk_cd": code, "ord_qty": str(int(qty)),
            "ord_uv": "" if price is None else str(int(price)),
            "trde_tp": "3" if price is None else "0",  # 3=시장가, 0=보통(지정가)
            "cond_uv": "",
        }
        side = "매수" if api_id == "kt10000" else "매도"
        if self.dry_run:
            log.warning("[DRY-RUN] %s %s POST %s/api/dostk/ordr headers={api-id:%s, authorization:Bearer ***} body=%s",
                        self._mode, side, self.host, api_id, json.dumps(body, ensure_ascii=False))
            return {"dry_run": True, "api_id": api_id, "body": body}
        log.warning("%s 주문 전송(%s): %s %s주 %s", side, self._mode, code, qty,
                    "시장가" if price is None else f"{int(price)}원")
        d, _ = self._post("/api/dostk/ordr", api_id, body)
        return {"ord_no": d.get("ord_no", ""), "exchange": d.get("dmst_stex_tp", exchange),
                "return_msg": d.get("return_msg", "")}

    def buy(self, code: str, qty: int, price: float | None = None, exchange: str = "KRX") -> dict:
        """kt10000 매수. price=None → 시장가."""
        return self._order("kt10000", code, qty, price, exchange)

    def sell(self, code: str, qty: int, price: float | None = None, exchange: str = "KRX") -> dict:
        """kt10001 매도. price=None → 시장가."""
        return self._order("kt10001", code, qty, price, exchange)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        c = KiwoomClient(mock=True)
    except KiwoomConfigError as e:
        print(f"[자가점검] 키 없음 → 종료: {e}")
        sys.exit(0)
    try:
        c.token()
        print("[자가점검] 토큰 확보 OK (mock)")
        df = c.daily_chart("005930", 5)
        print(df.tail(3))
    except KiwoomError as e:
        print(f"[자가점검] 실패: {e}")
        sys.exit(1)
