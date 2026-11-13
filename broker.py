"""증권사 계층 — 전략 코드는 이 인터페이스만 쓴다 (키움 외 증권사로 바꿀 때 여기만 고치면 됨)

Broker
  holdings() -> {code: {"name", "qty", "sellable", "avg_price"}}
  orderable_cash() -> float
  buy(code, qty) / sell(code, qty) -> dict (주문번호 등)  — 시장가
구현
  KiwoomBroker : kiwoom_client.KiwoomClient 래핑 (mode mock -> 모의 서버, live -> 실전 + allow_live)
  DryRunBroker : 주문을 보내지 않고 기록만 (키 없이 주문 흐름 시험용)
"""
from __future__ import annotations


class DryRunBroker:
    name = "dry-run"

    def __init__(self, holdings: dict | None = None, cash: float = 0.0):
        self._h, self._cash, self.sent = holdings or {}, cash, []

    def holdings(self):
        return dict(self._h)

    def orderable_cash(self):
        return self._cash

    def buy(self, code, qty):
        self.sent.append(("buy", code, qty))
        return {"dry_run": True, "side": "buy", "code": code, "qty": qty}

    def sell(self, code, qty):
        self.sent.append(("sell", code, qty))
        return {"dry_run": True, "side": "sell", "code": code, "qty": qty}


class KiwoomBroker:
    def __init__(self, mode: str, live_confirmed: bool = False):
        from kiwoom_client import KiwoomClient
        if mode not in ("mock", "live"):
            raise ValueError("KiwoomBroker 는 mock / live 모드에서만")
        if mode == "live" and not live_confirmed:
            raise PermissionError("실전 주문은 config.json live.enabled=true 와 --live 플래그가 모두 필요합니다.")
        self.name = f"kiwoom-{mode}"
        self.c = KiwoomClient(mock=(mode == "mock"), allow_live=(mode == "live" and live_confirmed))

    def holdings(self):
        h = self.c.balance()["holdings"]
        return {r["code"]: {"name": r["name"], "qty": int(r["qty"]), "sellable": int(r["sellable_qty"]),
                            "avg_price": float(r["avg_price"])} for _, r in h.iterrows() if r["qty"] > 0}

    def orderable_cash(self):
        return float(self.c.cash()["orderable"])

    def buy(self, code, qty):
        return self.c.buy(code, int(qty))

    def sell(self, code, qty):
        return self.c.sell(code, int(qty))


def get_broker(cfg: dict, live_flag: bool = False, dry_run: bool = False):
    if dry_run or cfg["mode"] == "paper":
        return DryRunBroker()
    live_ok = cfg["mode"] == "live" and bool(cfg["live"].get("enabled")) and live_flag
    return KiwoomBroker(cfg["mode"], live_confirmed=live_ok)
