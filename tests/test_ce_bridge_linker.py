from __future__ import annotations

import json

import pytest

from app.integration import ce_bridge_linker


class DummyResponse:
    def __init__(self, status_code: int, payload: object | None = None):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300
        if payload is None:
            self.content = b""
            self.text = ""
        else:
            self.text = json.dumps(payload)
            self.content = self.text.encode("utf-8")

    def json(self):
        if self._payload is None:
            raise ValueError("no payload")
        return self._payload


class DummySession:
    def __init__(self, responses: list[DummyResponse]):
        self._responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url, *, headers=None, params=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
            }
        )
        try:
            return next(self._responses)
        except StopIteration:  # pragma: no cover - defensive
            raise AssertionError("No more responses queued")


@pytest.fixture(autouse=True)
def _reset_linker(monkeypatch):
    monkeypatch.setattr(ce_bridge_linker, "_STATE_CACHE", None)
    monkeypatch.setattr(
        ce_bridge_linker,
        "resolve_bridge_connection",
        lambda: ("http://bridge.local", "token", 5.0),
    )


def test_select_best_match_ranking(monkeypatch):
    responses = [
        DummyResponse(
            200,
            {
                "features": {
                    "search_match_kind": True,
                    "normalization_rules_version": "v1",
                }
            },
        ),
        DummyResponse(
            200,
            [
                {"id": "1", "pn": "PN-1", "match_kind": "normalized_pn"},
                {
                    "id": "2",
                    "pn": "PN-2",
                    "match_kind": "exact_alias",
                    "reason": "alias match",
                    "analysis": {
                        "normalized_input": "pn2",
                        "normalized_targets": ["pn2"],
                    },
                },
                {"id": "3", "pn": "PN-3", "match_kind": "exact_alias"},
            ],
        ),
    ]
    session = DummySession(responses)
    monkeypatch.setattr(
        ce_bridge_linker.ce_bridge_transport, "get_session", lambda: session
    )

    decision = ce_bridge_linker.select_best_match("PN-XYZ")

    assert decision.best is not None
    assert decision.best.id == "2"
    assert decision.best.match_kind == "exact_alias"
    assert decision.best.normalized_input == "pn2"
    assert decision.best.normalized_targets == ["pn2"]
    assert decision.needs_review is True
    assert len(decision.results) == 3

    assert len(session.calls) == 2
    search_call = session.calls[1]
    assert search_call["params"] == {"pn": "PN-XYZ", "limit": 50, "analyze": "true"}


def test_select_best_match_rejects_wildcard(monkeypatch):
    session = DummySession([])
    monkeypatch.setattr(
        ce_bridge_linker.ce_bridge_transport, "get_session", lambda: session
    )

    with pytest.raises(ce_bridge_linker.LinkerInputError):
        ce_bridge_linker.select_best_match("*")

    assert session.calls == []


def test_select_best_match_top_level_normalized_fields(monkeypatch):
    responses = [
        DummyResponse(
            200,
            {
                "features": {
                    "search_match_kind": True,
                    "normalization_rules_version": "v1",
                }
            },
        ),
        DummyResponse(
            200,
            [
                {
                    "id": "1",
                    "pn": "PN-1",
                    "match_kind": "exact_pn",
                    "normalized_input": "pn-1",
                    "normalized_targets": ["pn-1", "pn-one"],
                }
            ],
        ),
    ]
    session = DummySession(responses)
    monkeypatch.setattr(
        ce_bridge_linker.ce_bridge_transport, "get_session", lambda: session
    )

    decision = ce_bridge_linker.select_best_match("PN-1")

    assert decision.best is not None
    assert decision.best.normalized_input == "pn-1"
    assert decision.best.normalized_targets == ["pn-1", "pn-one"]


def test_select_best_match_analysis_normalized_fields(monkeypatch):
    responses = [
        DummyResponse(
            200,
            {
                "features": {
                    "search_match_kind": True,
                    "normalization_rules_version": "v1",
                }
            },
        ),
        DummyResponse(
            200,
            [
                {
                    "id": "1",
                    "pn": "PN-1",
                    "match_kind": "normalized_alias",
                    "analysis": {
                        "normalized_input": "pn-1",
                        "normalized_targets": ["pn-1"],
                    },
                }
            ],
        ),
    ]
    session = DummySession(responses)
    monkeypatch.setattr(
        ce_bridge_linker.ce_bridge_transport, "get_session", lambda: session
    )

    decision = ce_bridge_linker.select_best_match("PN-1")

    assert decision.best is not None
    assert decision.best.normalized_input == "pn-1"
    assert decision.best.normalized_targets == ["pn-1"]


def test_search_best_match_alias(monkeypatch):
    responses = [
        DummyResponse(
            200,
            {
                "features": {
                    "search_match_kind": True,
                    "normalization_rules_version": "v1",
                }
            },
        ),
        DummyResponse(200, []),
    ]
    session = DummySession(responses)
    monkeypatch.setattr(
        ce_bridge_linker.ce_bridge_transport, "get_session", lambda: session
    )

    decision = ce_bridge_linker.search_best_match("PN-XYZ")

    assert decision.results == []
