"""선택적 Telegram 알림 모듈 — config.local.json 에 토큰/챗ID가 있을 때만 조용히 발송.

토큰·URL 이 로그나 예외 메시지에 절대 노출되지 않도록 예외 종류명만 남긴다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

import settings

log = logging.getLogger("notify")


def format_daily_summary(
    snap: dict,
    equity: float,
    capital0: float,
    n_pos: int,
    kill: str | None = None,
) -> str:
    """장 마감 실행 결과 요약 문자열을 만든다."""
    ret = 100 * (equity / capital0 - 1) if capital0 else 0.0
    lines = [
        f"[주식 자동매매] {snap['asof']} ({snap.get('mode', 'paper')})",
        f"평가자산 {equity:,.0f}원 ({ret:+.2f}%) · 보유 {n_pos}/{snap.get('max_positions', 10)}",
        f"시장필터: {'ON' if snap.get('mkt_on') else 'OFF'} · 전략: {snap.get('exit_mode', 'E3')}",
    ]
    if kill:
        lines.append(f"⛔ KILL_SWITCH: {kill}")
    orders = snap.get("new_orders") or []
    if orders:
        lines.append(f"대기주문 {len(orders)}건:")
        for o in orders[:8]:
            lines.append(f" - {o['side']} {o['name']}({o['code']}) {o['why']}")
    else:
        lines.append("대기주문 없음")
    return "\n".join(lines)


def format_order_summary(
    mode: str,
    broker_name: str,
    cash: float,
    results: list[dict],
    skipped: list[str],
    kill: str | None = None,
) -> str:
    """아침 주문 전송 결과 요약 문자열을 만든다."""
    if kill:
        return f"[주식 자동매매 · 아침주문] ⛔ KILL_SWITCH 중지: {kill}"
    ok_cnt = sum(1 for r in results if r.get("ok"))
    fail_cnt = len(results) - ok_cnt
    lines = [
        f"[주식 자동매매 · 아침주문] {broker_name} ({mode})",
        f"주문가능현금 {cash:,.0f}원 · 성공 {ok_cnt}건 / 실패 {fail_cnt}건",
    ]
    for r in results[:8]:
        mark = "✓" if r.get("ok") else "✗"
        lines.append(f" {mark} {r['side']} {r['name']}({r['code']}) {r['qty']}주")
    for s in skipped[:5]:
        lines.append(f" - {s}")
    return "\n".join(lines)


def send_telegram(
    text: str,
    local_cfg: dict | None = None,
    local_path: Path | None = None,
    timeout: float = 10.0,
) -> bool:
    """Telegram 봇으로 메시지를 전송한다. 설정이 없으면 False(무해한 no-op)."""
    cfg = local_cfg if local_cfg is not None else settings.load_local_config(local_path or (settings.HERE / "config.local.json"))
    token = str(cfg.get("telegram_bot_token") or "").strip()
    chat_id = str(cfg.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=timeout,
        )
        if r.status_code != 200:
            log.warning("텔레그램 응답 오류: HTTP %s", r.status_code)
            return False
        data = r.json()
        return bool(data.get("ok", True))
    except Exception as e:
        log.warning("텔레그램 전송 실패: %s", type(e).__name__)
        return False
