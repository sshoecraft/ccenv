"""Integration tests for the p2p DLM over real loopback TCP — no NATS.

Spins up multiple P2PDlm instances in one event loop, each with its own
Transport on 127.0.0.1, and exercises contended claims, shared
coexistence, and master failover. Membership is driven by a shared
``alive`` set rather than UDP discovery so the tests are deterministic.
"""
from __future__ import annotations

import asyncio

import pytest

from ccteam import p2p_transport
from ccteam.p2p_dlm import P2PDlm
from ccteam.dlm import LockMode


class Cluster:
    def __init__(self) -> None:
        self.transports: dict[str, p2p_transport.Transport] = {}
        self.dlms: dict[str, P2PDlm] = {}
        self.alive: set[str] = set()

    async def add(self, node_id: str) -> None:
        t = p2p_transport.Transport(bind_host="127.0.0.1", port=0)
        await t.start()
        self.transports[node_id] = t
        self.alive.add(node_id)

        def members_fn(self_id: str = node_id):
            return {
                o: ("127.0.0.1", self.transports[o].port, o, 1)
                for o in self.alive if o != self_id
            }

        self.dlms[node_id] = P2PDlm(
            node_id=node_id, pid=1, hostname=node_id,
            transport=t, members_fn=members_fn,
        )

    async def settle(self) -> None:
        for d in self.dlms.values():
            await d.on_membership_change()
        await asyncio.sleep(0.05)

    async def kill(self, node_id: str) -> None:
        self.alive.discard(node_id)
        await self.transports[node_id].stop()
        del self.dlms[node_id]
        await self.settle()

    async def stop(self) -> None:
        for t in self.transports.values():
            await t.stop()


@pytest.fixture
async def cluster():
    c = Cluster()
    yield c
    await c.stop()


async def test_contended_exclusive_blocks_until_release(cluster) -> None:
    await cluster.add("a")  # master (lowest id)
    await cluster.add("b")
    await cluster.settle()

    # b (non-master) claims via the master a.
    r = await cluster.dlms["b"].claim("f", LockMode.EXCLUSIVE, 5000)
    assert r.granted

    # a now wants the same path — must queue behind b.
    task = asyncio.create_task(cluster.dlms["a"].claim("f", LockMode.EXCLUSIVE, 5000))
    await asyncio.sleep(0.2)
    assert not task.done()

    await cluster.dlms["b"].release("f")
    res = await asyncio.wait_for(task, 2.0)
    assert res.granted


async def test_shared_locks_coexist(cluster) -> None:
    await cluster.add("a")
    await cluster.add("b")
    await cluster.settle()

    ra = await cluster.dlms["a"].claim("f", LockMode.SHARED, 5000)
    rb = await cluster.dlms["b"].claim("f", LockMode.SHARED, 5000)
    assert ra.granted and rb.granted


async def test_exclusive_times_out_when_held(cluster) -> None:
    await cluster.add("a")
    await cluster.add("b")
    await cluster.settle()

    assert (await cluster.dlms["a"].claim("f", LockMode.EXCLUSIVE, 5000)).granted
    res = await cluster.dlms["b"].claim("f", LockMode.EXCLUSIVE, 300)
    assert not res.granted
    assert res.timed_out


async def test_master_failover_rebuilds_table(cluster) -> None:
    await cluster.add("a")  # master
    await cluster.add("b")
    await cluster.add("c")
    await cluster.settle()

    # b holds an EX lock granted by master a.
    assert (await cluster.dlms["b"].claim("g", LockMode.EXCLUSIVE, 5000)).granted

    # Master a dies; b becomes the new master and must rebuild the table
    # from survivors' self-known holds (b still holds "g").
    await cluster.kill("a")
    assert cluster.dlms["b"].is_master()

    # c now contends for "g" against the rebuilt table — should block.
    task = asyncio.create_task(cluster.dlms["c"].claim("g", LockMode.EXCLUSIVE, 5000))
    await asyncio.sleep(0.2)
    assert not task.done()

    await cluster.dlms["b"].release("g")
    res = await asyncio.wait_for(task, 2.0)
    assert res.granted
