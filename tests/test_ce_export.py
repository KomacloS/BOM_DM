import pytest

from app.integration import ce_bridge_client


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, ok: bool = True):
        self.status_code = status_code
        self._payload = payload
        self.ok = ok
        self.content = b"payload" if payload is not None else b""

    def json(self):
        return self._payload


def test_export_complexes_mdb_success(monkeypatch):
    captured = {}

    def fake_request(method, endpoint, json_body=None, allow_conflict=False):
        captured["json"] = json_body
        return _FakeResponse(200, {"exported": 2, "linked": 2})

    monkeypatch.setattr(ce_bridge_client, "_request", fake_request)
    result = ce_bridge_client.export_complexes_mdb(
        ["PN1", "pn1", "PN2", ""],
        "C:/out",
    )

    assert result == {"exported": 2, "linked": 2}
    assert captured["json"]["pns"] == ["PN1", "PN2"]
    assert captured["json"]["out_dir"] == "C:/out"
    assert captured["json"]["mdb_name"] == "bom_complexes.mdb"
    assert captured["json"]["require_linked"] is True


def test_export_complexes_mdb_busy(monkeypatch):
    def fake_request(method, endpoint, json_body=None, allow_conflict=False):
        return _FakeResponse(409, {"reason": "busy", "message": "CE busy"}, ok=False)

    monkeypatch.setattr(ce_bridge_client, "_request", fake_request)
    with pytest.raises(ce_bridge_client.CEExportBusyError) as excinfo:
        ce_bridge_client.export_complexes_mdb(["PN1"], "out")
    assert "busy" in str(excinfo.value).lower()


def test_export_complexes_mdb_strict(monkeypatch):
    payload = {
        "reason": "unlinked_or_missing",
        "unlinked": ["PN1"],
        "missing": ["PN2"],
    }

    def fake_request(method, endpoint, json_body=None, allow_conflict=False):
        return _FakeResponse(409, payload, ok=False)

    monkeypatch.setattr(ce_bridge_client, "_request", fake_request)
    with pytest.raises(ce_bridge_client.CEExportStrictError) as excinfo:
        ce_bridge_client.export_complexes_mdb(["PN1", "PN2"], "out")
    assert excinfo.value.unlinked == ["PN1"]
    assert excinfo.value.missing == ["PN2"]
