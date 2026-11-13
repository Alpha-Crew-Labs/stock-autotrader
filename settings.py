"""운영 설정 + 안전장치 공통 함수

config.json (커밋됨, 비밀값 없음)
  mode: "paper"(모의 장부만) | "mock"(키움 모의투자 주문) | "live"(키움 실전 주문)
  live 는 config.json 의 live.enabled=true + 실행 시 --live 플래그, 두 가지가 모두 있어야 열린다.
KILL_SWITCH 파일이 있으면 어떤 주문도 나가지 않는다 (손실 한도 초과 시 자동 생성, 지우면 해제).
"""
from pathlib import Path
import json
import datetime as dt

HERE = Path(__file__).parent
KILL = HERE / "KILL_SWITCH"
DEFAULTS = {
    "mode": "paper", "capital": 10_000_000, "max_positions": 10, "units": 3, "watchlist_top": 75,
    "limits": {"max_order_krw": 1_000_000, "max_daily_buy_krw": 3_000_000,
               "daily_loss_stop_pct": 3.0, "max_drawdown_stop_pct": 15.0},
    "live": {"enabled": False},
}
MODES = ("paper", "mock", "live")


def load(path: Path = HERE / "config.json") -> dict:
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
    return cfg


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
