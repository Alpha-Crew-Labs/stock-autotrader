"""운영 설정 + 안전장치 + 원자적 JSON 저장 공통 함수

config.json (커밋됨, 비밀값 없음)
  mode: "paper"(모의 장부만) | "mock"(키움 모의투자 주문) | "live"(키움 실전 주문)
  live 는 config.json 의 live.enabled=true + 실행 시 --live 플래그, 두 가지가 모두 있어야 열린다.
KILL_SWITCH 파일이 있으면 어떤 주문도 나가지 않는다 (손실 한도 초과 시 자동 생성, 지우면 해제).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

HERE = Path(__file__).parent
KILL = HERE / "KILL_SWITCH"
MODES = ("paper", "mock", "live")
VALID_MARKETS = ("KR", "US")
VALID_ENTRY_MODES = ("pullback_v6", "stoch_v5")
VALID_EXIT_MODES = ("E0", "E2", "E3", "E3_atr", "E4", "chandelier", "meanrev")
VALID_RANK_BY = ("clenow", "rs60", "k")
VALID_SIZING = ("inverse_vol", "equal")

DEFAULTS = {
    "mode": "paper",
    "capital": 10_000_000,
    "max_positions": 10,
    "units": 3,
    "watchlist_top": 75,
    "limits": {
        "max_order_krw": 1_000_000,
        "max_daily_buy_krw": 3_000_000,
        "daily_loss_stop_pct": 3.0,
        "max_drawdown_stop_pct": 15.0,
        "buy_price_buffer_pct": 0.003,
    },
    "strategy": {
        "market": "KR",
        "entry_mode": "pullback_v6",
        "exit_mode": "E3_atr",
        "rank_by": "clenow",
        "sizing": "inverse_vol",
        "target_risk": 0.016,
        "max_pos_weight": 0.10,
        "atr_stop_mult": 2.5,
        "max_pending_age_bdays": 3,
        "hold_add_under20": False,
    },
    "live": {"enabled": False},
}


def load(path: Path = HERE / "config.json") -> dict:
    """config.json 을 기본값과 병합해 로드하고 필수 설정값을 검증한다."""
    cfg = json.loads(json.dumps(DEFAULTS))
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            elif not k.startswith("_"):
                cfg[k] = v
    if cfg["mode"] not in MODES:
        raise ValueError(f"config.json mode 는 {MODES} 중 하나여야 합니다: {cfg['mode']}")
    st = cfg["strategy"]
    st["market"] = str(st.get("market", "KR")).upper()
    if st["market"] not in VALID_MARKETS:
        raise ValueError(f"strategy.market 는 {VALID_MARKETS} 중 하나여야 합니다: {st['market']}")
    if st["entry_mode"] not in VALID_ENTRY_MODES:
        raise ValueError(f"strategy.entry_mode 는 {VALID_ENTRY_MODES} 중 하나여야 합니다: {st['entry_mode']}")
    if st["exit_mode"] not in VALID_EXIT_MODES:
        raise ValueError(f"strategy.exit_mode 는 {VALID_EXIT_MODES} 중 하나여야 합니다: {st['exit_mode']}")
    if st["rank_by"] not in VALID_RANK_BY:
        raise ValueError(f"strategy.rank_by 는 {VALID_RANK_BY} 중 하나여야 합니다: {st['rank_by']}")
    if st["sizing"] not in VALID_SIZING:
        raise ValueError(f"strategy.sizing 는 {VALID_SIZING} 중 하나여야 합니다: {st['sizing']}")
    return cfg


def load_local_config(path: Path = HERE / "config.local.json") -> dict:
    """비밀 키 설정 파일(config.local.json)을 안전하게 읽는다. 없으면 빈 dict."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_write_json(path: Path, data: object, indent: int | None = 1, default=None) -> None:
    """임시 파일 작성 후 os.replace() 로 원자적 JSON 저장 (동기화 충돌 손상 방지)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=indent, default=default)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def kill_reason() -> str | None:
    return KILL.read_text(encoding="utf-8").strip() or "사유 없음" if KILL.exists() else None


def trip_kill(reason: str) -> None:
    KILL.write_text(f"{dt.datetime.now():%Y-%m-%d %H:%M} {reason}\n", encoding="utf-8")


def check_loss_limits(cfg: dict, equity_log: list) -> str | None:
    """평가자산 기록으로 손실 한도 점검. 넘으면 사유 문자열 반환 (KILL_SWITCH 는 호출 측에서)."""
    if len(equity_log) < 2:
        return None
    eq = [e["equity"] for e in equity_log]
    day = 100 * (eq[-1] / eq[-2] - 1)
    dd = 100 * (eq[-1] / max(eq) - 1)
    lim = cfg["limits"]
    if day <= -abs(lim["daily_loss_stop_pct"]):
        return f"하루 손실 {day:.2f}% (한도 -{lim['daily_loss_stop_pct']}%)"
    if dd <= -abs(lim["max_drawdown_stop_pct"]):
        return f"고점 대비 {dd:.2f}% (한도 -{lim['max_drawdown_stop_pct']}%)"
    return None
