import json
import logging

import pytest

import kiwoom_client as kc

APPKEY = "DUMMY_APPKEY_9f3a1c"
SECRET = "DUMMY_SECRET_77b2e4"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """토큰 캐시를 임시 폴더로, HTTP 는 호출되면 실패, 대기 시간은 0."""
    monkeypatch.setattr(kc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(kc, "TOKEN_PATH", tmp_path / "state" / "kiwoom_token.json")
    monkeypatch.setattr(kc, "MIN_INTERVAL_SEC", 0.0)

    def no_http(*a, **k):
        raise AssertionError("테스트 중 HTTP 요청 발생")
    monkeypatch.setattr(kc.requests.Session, "post", no_http)
    monkeypatch.setattr(kc.requests.Session, "get", no_http)


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.local.json"
    p.write_text(json.dumps({"kiwoom_appkey": APPKEY, "kiwoom_secretkey": SECRET,
                             "kiwoom_account": "0000-0000"}), encoding="utf-8")
    return p


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(kc.KiwoomConfigError):
        kc.KiwoomClient(config_path=tmp_path / "nope.json")


@pytest.mark.parametrize("content", [{}, {"kiwoom_appkey": APPKEY}, {"kiwoom_appkey": "", "kiwoom_secretkey": ""}])
def test_no_keys_raises_config_error(tmp_path, content):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(kc.KiwoomConfigError):
        kc.KiwoomClient(config_path=p)


def test_default_is_mock(cfg):
    c = kc.KiwoomClient(config_path=cfg)
    assert c.mock is True and c.host == kc.MOCK_HOST
    assert c.allow_live is False


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_live_order_refused_without_allow_live(cfg, side, dry_run):
    c = kc.KiwoomClient(mock=False, allow_live=False, dry_run=dry_run, config_path=cfg)
    assert c.host == kc.REAL_HOST
    with pytest.raises(kc.KiwoomSafetyError):
        getattr(c, side)("005930", 1, 70000)
    with pytest.raises(kc.KiwoomSafetyError):
        getattr(c, side)("005930", 1)            # 시장가도 동일


def test_safety_error_is_kiwoom_error():
    assert issubclass(kc.KiwoomSafetyError, kc.KiwoomError)
    assert issubclass(kc.KiwoomConfigError, kc.KiwoomError)


def test_zero_qty_rejected(cfg):
    c = kc.KiwoomClient(dry_run=True, config_path=cfg)
    with pytest.raises(ValueError):
        c.buy("005930", 0)


@pytest.mark.parametrize("mock,allow_live", [(True, False), (False, True)])
def test_dry_run_masks_secrets(cfg, caplog, capsys, mock, allow_live):
    c = kc.KiwoomClient(mock=mock, allow_live=allow_live, dry_run=True, config_path=cfg)
    with caplog.at_level(logging.DEBUG, logger="kiwoom"):
        r1 = c.buy("005930", 3, 70000)
        r2 = c.sell("005930", 3)
    out = capsys.readouterr()
    blob = caplog.text + out.out + out.err + json.dumps([r1, r2], ensure_ascii=False) + repr(c.__dict__.get("host"))
    assert "DRY-RUN" in caplog.text
    assert APPKEY not in blob and SECRET not in blob
    assert r1["dry_run"] and r1["body"]["trde_tp"] == "0" and r1["body"]["ord_uv"] == "70000"
    assert r2["body"]["trde_tp"] == "3" and r2["body"]["ord_uv"] == ""
    assert r1["api_id"] == "kt10000" and r2["api_id"] == "kt10001"


def test_mock_keys_preferred_in_mock_mode(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"kiwoom_appkey": "REAL_A", "kiwoom_secretkey": "REAL_S",
                             "kiwoom_mock_appkey": "MOCK_A", "kiwoom_mock_secretkey": "MOCK_S"}), encoding="utf-8")
    assert kc.KiwoomClient(mock=True, config_path=p)._appkey == "MOCK_A"
    assert kc.KiwoomClient(mock=False, config_path=p)._appkey == "REAL_A"


def test_live_order_sends_when_allowed(cfg, monkeypatch):
    """allow_live=True 일 때만 실제 _post 경로로 간다(HTTP 는 가짜)."""
    calls = []

    def fake_post(self, path, api_id, body, cont_yn="", next_key=""):
        calls.append((path, api_id, body))
        return {"ord_no": "12345", "return_code": 0, "return_msg": "ok"}, {}
    monkeypatch.setattr(kc.KiwoomClient, "_post", fake_post)
    c = kc.KiwoomClient(mock=False, allow_live=True, config_path=cfg)
    r = c.buy("005930", 2, 70000)
    assert r["ord_no"] == "12345"
    assert calls == [("/api/dostk/ordr", "kt10000", calls[0][2])]


@pytest.mark.parametrize("raw,exp", [("+00000012345", 12345.0), ("-1,234", -1234.0), ("", 0.0),
                                     (None, 0.0), ("+", 0.0), ("abc", 0.0), (7, 7.0)])
def test_num_parser(raw, exp):
    assert kc._num(raw) == exp
