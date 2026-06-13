"""Pure-logic tests for the p2p election DLM — no real transport."""
from __future__ import annotations

from ccteam import p2p_dlm
from ccteam.dlm import LockMode, path_key


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, int, dict]] = []
        self.handler = None

    def on_message(self, handler) -> None:
        self.handler = handler

    async def send(self, host, port, msg, *, retries=2) -> bool:
        self.sent.append((host, port, msg))
        return True


def make_dlm(node_id: str, peers: dict[str, tuple[str, int, str, int]] | None = None):
    peers = peers or {}
    return p2p_dlm.P2PDlm(
        node_id=node_id, pid=1, hostname="h",
        transport=FakeTransport(), members_fn=lambda: peers,
    )


def test_election_lowest_id_wins() -> None:
    d = make_dlm("nodeB", {"nodeA": ("h", 1, "ha", 1), "nodeC": ("h", 2, "hc", 1)})
    assert d.current_master() == "nodeA"
    assert not d.is_master()


def test_election_alone_is_master() -> None:
    d = make_dlm("nodeB", {})
    assert d.current_master() == "nodeB"
    assert d.is_master()


def test_master_admit_first_claim_grants() -> None:
    d = make_dlm("nodeA")
    key = path_key("f")
    assert d._master_admit(key, "f", "nodeA", LockMode.EXCLUSIVE)
    assert d.table[key].has_exclusive()


def test_master_admit_conflicting_ex_denied() -> None:
    d = make_dlm("nodeA")
    key = path_key("f")
    assert d._master_admit(key, "f", "nodeX", LockMode.EXCLUSIVE)
    assert not d._master_admit(key, "f", "nodeY", LockMode.EXCLUSIVE)


def test_master_admit_shared_coexist() -> None:
    d = make_dlm("nodeA")
    key = path_key("f")
    assert d._master_admit(key, "f", "nodeX", LockMode.SHARED)
    assert d._master_admit(key, "f", "nodeY", LockMode.SHARED)
    assert len(d.table[key].holders) == 2


def test_master_admit_queues_behind_waiters() -> None:
    # FIFO fairness: with a waiter present, even a compatible request queues.
    d = make_dlm("nodeA")
    key = path_key("f")
    d._master_admit(key, "f", "nodeX", LockMode.EXCLUSIVE)
    d.waiters[key] = [p2p_dlm.Waiter("r1", "nodeY", "f", LockMode.SHARED, "h", 1)]
    assert not d._master_admit(key, "f", "nodeZ", LockMode.SHARED)


def test_promote_grants_waiter_after_release() -> None:
    d = make_dlm("nodeA")
    key = path_key("f")
    d._master_admit(key, "f", "nodeX", LockMode.EXCLUSIVE)
    d.waiters[key] = [p2p_dlm.Waiter("r1", "nodeY", "f", LockMode.EXCLUSIVE, "h", 1)]
    # Simulate nodeX releasing.
    d.table[key].holders = [h for h in d.table[key].holders if h.node != "nodeX"]
    promoted = d._master_promote(key, "f")
    assert [w.node_id for w in promoted] == ["nodeY"]
    assert d.table[key].holders[0].node == "nodeY"
    assert key not in d.waiters


def test_promote_fifo_does_not_barge_ex() -> None:
    # Head waiter is EX and blocked by a SHARED holder; a later SHARED must
    # not jump ahead.
    d = make_dlm("nodeA")
    key = path_key("f")
    d._master_admit(key, "f", "nodeX", LockMode.SHARED)
    d.waiters[key] = [
        p2p_dlm.Waiter("r1", "nodeY", "f", LockMode.EXCLUSIVE, "h", 1),
        p2p_dlm.Waiter("r2", "nodeZ", "f", LockMode.SHARED, "h", 1),
    ]
    promoted = d._master_promote(key, "f")
    assert promoted == []  # blocked at the EX head
    assert len(d.waiters[key]) == 2


async def test_purge_node_removes_holds() -> None:
    d = make_dlm("nodeA")
    key = path_key("f")
    d._master_admit(key, "f", "nodeX", LockMode.EXCLUSIVE)
    count = await d.purge_node("nodeX")
    assert count == 1
    assert key not in d.table


def test_table_sync_rebuilds_holds() -> None:
    d = make_dlm("nodeA")  # alone => master
    d._handle_table_sync({
        "node_id": "nodeX", "hostname": "hx", "pid": 7,
        "holds": [{"path": "f", "mode": "EX"}, {"path": "g", "mode": "SHARED"}],
    })
    assert d.table[path_key("f")].holders[0].node == "nodeX"
    assert d.table[path_key("g")].holders[0].mode == "SHARED"


def test_seed_table_from_self() -> None:
    d = make_dlm("nodeA")
    d.held[path_key("f")] = ("f", "EX")
    d._seed_table_from_self()
    assert d.table[path_key("f")].holders[0].node == "nodeA"
