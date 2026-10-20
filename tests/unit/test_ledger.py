import pytest

from crimsonipelago.client.database import BatchState, DeliveryState, Ledger, LedgerError


def _session(ledger, seed="S1", slot=1):
    return ledger.get_or_create_session(seed, 0, slot, "Wout", "localhost", "fp", "0.1.0")


def test_session_is_reused(ledger):
    a = _session(ledger)
    b = _session(ledger)
    assert a.id == b.id
    assert _session(ledger, seed="S2").id != a.id


def test_replay_is_ignored_and_conflict_is_an_error(ledger):
    s = _session(ledger)
    assert ledger.record_received(s.id, 0, 1001, 5, 2, 1)
    assert not ledger.record_received(s.id, 0, 1001, 5, 2, 1)
    assert ledger.next_index(s.id) == 1
    with pytest.raises(LedgerError):
        ledger.record_received(s.id, 0, 9999, 5, 2, 1)


def test_items_are_isolated_per_session(ledger):
    a, b = _session(ledger, "A"), _session(ledger, "B")
    ledger.record_received(a.id, 0, 1001, 1, 1, 0)
    assert ledger.next_index(b.id) == 0
    assert ledger.items(b.id) == []


def test_check_lifecycle(ledger):
    s = _session(ledger)
    assert ledger.record_checks(s.id, {10, 11}) == {10, 11}
    assert ledger.record_checks(s.id, {10, 11, 12}) == {12}
    assert ledger.unconfirmed_checks(s.id) == {10, 11, 12}
    ledger.confirm_checks(s.id, {10, 99})  # 99: collected by the server, never detected locally
    assert ledger.unconfirmed_checks(s.id) == {11, 12}
    assert ledger.record_checks(s.id, {99}) == {99}  # later detected in game: still recorded once
    assert ledger.check_counts(s.id)["confirmed"] == 2


def test_begin_batch_requires_pending(ledger):
    s = _session(ledger)
    ledger.record_received(s.id, 0, 1001, 1, 1, 0)
    with pytest.raises(LedgerError):
        ledger.begin_batch(s.id, "b1", [0], "pre", "post", None)
    # the failed transaction left nothing behind
    assert ledger.batches(s.id, (BatchState.PREPARED,)) == []
    assert ledger.received(s.id, 0).state is DeliveryState.RECEIVED


def test_survives_reopen(tmp_path):
    path = tmp_path / "l.sqlite3"
    led = Ledger(path)
    s = _session(led)
    led.record_received(s.id, 0, 1001, 1, 1, 0)
    led.close()
    led = Ledger(path)
    assert led.next_index(s.id) == 1
    assert led.schema_version == 1
    led.close()


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "l.sqlite3"
    led = Ledger(path)
    led.conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    led.close()
    with pytest.raises(LedgerError):
        Ledger(path)
