from app.domain.complex_creation import WizardPoller, WizardPollResult


def test_wizard_poller_attaches_on_unique_match():
    calls = []

    def search_stub(_pn: str, _limit: int):
        responses = getattr(search_stub, "_responses", [[], [{"id": "ce-1", "pn": "PN-1"}]])
        if not responses:
            return []
        result = responses.pop(0)
        setattr(search_stub, "_responses", responses)
        return result

    setattr(search_stub, "_responses", [[], [{"id": "ce-1", "pn": "PN-1"}]])

    def attach_stub(part_id: int, ce_id: str) -> None:
        calls.append((part_id, ce_id))

    poller = WizardPoller(
        part_id=5,
        pn="PN-1",
        limit=5,
        search=search_stub,
        attach=attach_stub,
    )

    first = poller.poll_once()
    assert first == WizardPollResult(attached=False, ce_id=None)
    second = poller.poll_once()
    assert second.attached is True
    assert second.ce_id == "ce-1"
    assert calls == [(5, "ce-1")]
