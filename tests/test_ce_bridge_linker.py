from app.integration import ce_bridge_linker
from app.integration.ce_bridge_linker import LinkerDecision


def _patch_core(monkeypatch, *, payload):
    config = object()
    monkeypatch.setattr(ce_bridge_linker, "_load_config", lambda: config)
    monkeypatch.setattr(ce_bridge_linker, "_normalize_trace_id", lambda trace=None: "trace-test")
    monkeypatch.setattr(ce_bridge_linker, "_probe_state", lambda cfg, trace_id: {})

    def _fake_request(_cfg, method, path, **kwargs):
        assert _cfg is config
        assert method == "GET"
        if path == "/complexes/search":
            return payload
        if path == "/state":
            return {}
        raise AssertionError(f"Unexpected path {path}")

    monkeypatch.setattr(ce_bridge_linker, "_request_json", _fake_request)


def test_select_best_match_prefers_exact(monkeypatch):
    payload = {
        "analysis": {"normalized_input": "pn123"},
        "results": [
            {
                "id": "ce-1",
                "pn": "PN123",
                "aliases": ["ALT"],
                "db_path": "C:/linked.mdb",
                "match_kind": "exact_pn",
                "reason": "pn match",
                "normalized_targets": ["pn123"],
            },
            {
                "id": "ce-2",
                "pn": "PN124",
                "match_kind": "partial",
                "reason": "close match",
            },
        ],
    }
    _patch_core(monkeypatch, payload=payload)

    decision = ce_bridge_linker.select_best_match("PN123")
    assert isinstance(decision, LinkerDecision)
    assert decision.best is not None
    assert decision.best.id == "ce-1"
    assert decision.best.match_kind == "exact_pn"
    assert decision.needs_review is False
    assert decision.trace_id == "trace-test"
    assert decision.normalized_input == "pn123"
    assert [cand.id for cand in decision.results] == ["ce-1", "ce-2"]
    assert decision.results_data[0]["match_kind"] == "exact_pn"


def test_select_best_match_marks_ambiguous(monkeypatch):
    payload = {
        "results": [
            {"id": "ce-1", "pn": "PN123", "match_kind": "exact_pn"},
            {"id": "ce-2", "pn": "PN123", "match_kind": "exact_pn"},
        ],
    }
    _patch_core(monkeypatch, payload=payload)

    decision = ce_bridge_linker.select_best_match("PN123")
    assert decision.best is not None
    assert decision.needs_review is True


def test_fetch_normalization_info_includes_trace(monkeypatch):
    config = object()
    monkeypatch.setattr(ce_bridge_linker, "_load_config", lambda: config)
    monkeypatch.setattr(ce_bridge_linker, "_normalize_trace_id", lambda trace=None: "trace-rules")

    def _fake_request(_cfg, method, path, **kwargs):
        assert path == "/complexes/normalization"
        return {"version": "1.0", "rules": ["A", "B"]}

    monkeypatch.setattr(ce_bridge_linker, "_request_json", _fake_request)

    info = ce_bridge_linker.fetch_normalization_info()
    assert info["version"] == "1.0"
    assert info["trace_id"] == "trace-rules"
    assert info["rules"] == ["A", "B"]


def test_select_best_match_handles_invalid_payload(monkeypatch):
    _patch_core(monkeypatch, payload="unexpected")

    decision = ce_bridge_linker.select_best_match("PN123")
    assert decision.results == []
    assert decision.best is None
