"""Broker-free distributed lock manager over peer-to-peer TCP.

Used in shared-filesystem deployments, where the bytes are already common
to every node so the overlay/replication layer is unnecessary and the
only thing left to coordinate is mutual exclusion. This replaces the
NATS-KV lock table (``dlm.DLM``) with the single-master, lowest-node-ID
election model proven in MXFS (``/src/mxfs/docs/dlm-protocol.md``):

- The node with the lowest ``node_id`` among live members is the master
  for **all** paths and holds the authoritative lock table.
- Non-master nodes forward ``LOCK_REQ`` to the master and block until a
  ``LOCK_GRANT`` / ``LOCK_DENY`` message comes back.
- On a membership change the master is recomputed; the new master seeds
  its table from its own held locks and the survivors push theirs via
  ``TABLE_SYNC``.

``P2PDlm`` mirrors the ``dlm.DLM`` method surface (``claim`` / ``release``
/ ``held_by`` / ``force_claim`` / ``purge_node``) and reuses
``LockMode`` / ``Holder`` / ``LockState`` / ``ClaimResult`` /
``compatible`` / ``path_key`` from ``dlm`` so the MCP and IPC layers are
backend-agnostic.

Scope is deliberately smaller than MXFS: no journal recovery, no
cache-coherency BASTs, no 6-mode matrix, no per-resource mastering, and
no epoch-mismatch hardening. ccteam has a handful of nodes, low
contention, and tolerates the rare race (last-writer-wins on a shared
file), which is not acceptable for a filesystem but is fine here.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import p2p_transport
from .dlm import (
    ClaimResult,
    Holder,
    LockMode,
    LockState,
    compatible,
    path_key,
)


log = logging.getLogger(__name__)


# Wire message types.
LOCK_REQ = "lock_req"
LOCK_GRANT = "lock_grant"
LOCK_DENY = "lock_deny"
LOCK_RELEASE = "lock_release"
LOCK_CANCEL = "lock_cancel"
FORCE_REQ = "force_req"
TABLE_SYNC = "table_sync"
HELD_QUERY = "held_query"
HELD_RESP = "held_resp"


# members_fn returns the live p2p-capable peers as
# {node_id: (host, port, hostname, pid)} — self is NOT included.
MembersFn = Callable[[], dict[str, tuple[str, int, str, int]]]


@dataclass
class Waiter:
    req_id: str
    node_id: str
    path: str
    mode: LockMode
    hostname: str
    pid: int
    # Set for a wait originating on the master's own node; None for a
    # remote waiter (which is signalled via a LOCK_GRANT message instead).
    local_future: asyncio.Future | None = field(default=None)


class P2PDlm:
    def __init__(
        self,
        node_id: str,
        pid: int,
        hostname: str,
        transport: p2p_transport.Transport,
        members_fn: MembersFn,
    ) -> None:
        self.node_id = node_id
        self.pid = pid
        self.hostname = hostname
        self.transport = transport
        self.members_fn = members_fn

        # Authoritative state — meaningful only while this node is master.
        self.table: dict[str, LockState] = {}
        self.waiters: dict[str, list[Waiter]] = {}

        # Locks THIS node holds (any master). Keyed by path_key →
        # (path, mode_value). Used to seed/rebuild the master table on
        # failover and to know what to release.
        self.held: dict[str, tuple[str, str]] = {}

        # Outstanding remote requests awaiting a reply, keyed by req_id.
        self.pending: dict[str, asyncio.Future] = {}
        self.pending_query: dict[str, asyncio.Future] = {}

        self.master_id: str | None = None
        self.epoch_counter = 0

        transport.on_message(self.on_message)

    # ── identity / membership ────────────────────────────────────────

    def members(self) -> dict[str, tuple[str, int, str, int]]:
        return self.members_fn()

    def current_master(self) -> str:
        node_ids = set(self.members().keys())
        node_ids.add(self.node_id)
        return min(node_ids)

    def is_master(self) -> bool:
        return self.current_master() == self.node_id

    def next_epoch(self) -> int:
        self.epoch_counter += 1
        return self.epoch_counter

    def make_holder(self, node_id: str, mode: LockMode, hostname: str, pid: int) -> Holder:
        return Holder(
            node=node_id,
            mode=mode.value,
            epoch=self.next_epoch(),
            acquired_at=time.time(),
            pid=pid,
            hostname=hostname,
        )

    # ── public DLM surface (mirrors dlm.DLM) ─────────────────────────

    async def claim(self, path: str, mode: LockMode, timeout_ms: int) -> ClaimResult:
        start = time.monotonic()
        deadline = start + (timeout_ms / 1000.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                holders = await self._best_effort_holders(path)
                return ClaimResult(
                    granted=False, timed_out=True,
                    current_holders=holders,
                    elapsed_ms=self._ms(start),
                )
            master = self.current_master()
            if master == self.node_id:
                res = await self._local_claim(path, mode, remaining, start)
            else:
                res = await self._remote_claim(path, master, mode, remaining, start)
            if res is None:
                # Master changed or unreachable; loop and retry.
                await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                continue
            return res

    async def release(self, path: str) -> bool:
        key = path_key(path)
        self.held.pop(key, None)
        if self.is_master():
            await self._master_release(key, path, self.node_id)
            return True
        master = self.current_master()
        addr = self.members().get(master)
        if addr is None:
            return False
        host, port, _, _ = addr
        return await self.transport.send(host, port, {
            "type": LOCK_RELEASE, "path": path, "node_id": self.node_id,
        })

    async def held_by(self, path: str) -> LockState | None:
        key = path_key(path)
        if self.is_master():
            return self.table.get(key)
        master = self.current_master()
        addr = self.members().get(master)
        if addr is None:
            return None
        host, port, _, _ = addr
        req_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending_query[req_id] = fut
        try:
            sent = await self.transport.send(host, port, {
                "type": HELD_QUERY, "req_id": req_id, "path": path,
                "node_id": self.node_id,
            })
            if not sent:
                return None
            try:
                res = await asyncio.wait_for(fut, 1.0)
            except asyncio.TimeoutError:
                return None
        finally:
            self.pending_query.pop(req_id, None)
        holders = res.get("holders") or []
        if not holders:
            return None
        return LockState(path=path, holders=[Holder(**h) for h in holders])

    async def force_claim(
        self, path: str, mode: LockMode,
    ) -> tuple[ClaimResult, list[Holder]]:
        start = time.monotonic()
        key = path_key(path)
        if self.is_master():
            prior = list(self.table.get(key, LockState(path=path)).holders)
            self.table[key] = LockState(
                path=path,
                holders=[self.make_holder(self.node_id, mode, self.hostname, self.pid)],
            )
            self.held[key] = (path, mode.value)
            return ClaimResult(granted=True, elapsed_ms=self._ms(start)), prior
        master = self.current_master()
        addr = self.members().get(master)
        if addr is None:
            return ClaimResult(granted=False, elapsed_ms=self._ms(start)), []
        host, port, _, _ = addr
        req_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[req_id] = fut
        try:
            sent = await self.transport.send(host, port, {
                "type": FORCE_REQ, "req_id": req_id, "path": path,
                "mode": mode.value, "node_id": self.node_id,
                "hostname": self.hostname, "pid": self.pid,
            })
            if not sent:
                return ClaimResult(granted=False, elapsed_ms=self._ms(start)), []
            try:
                res = await asyncio.wait_for(fut, 5.0)
            except asyncio.TimeoutError:
                return ClaimResult(granted=False, elapsed_ms=self._ms(start)), []
        finally:
            self.pending.pop(req_id, None)
        if res.get("status") == "granted":
            self.held[key] = (path, mode.value)
            prior = [Holder(**h) for h in res.get("prior", [])]
            return ClaimResult(granted=True, elapsed_ms=self._ms(start)), prior
        return ClaimResult(granted=False, elapsed_ms=self._ms(start)), []

    async def purge_node(self, node: str) -> int:
        """Remove all of ``node``'s holds and waiters (master only)."""
        if not self.is_master():
            return 0
        count = 0
        for key in list(self.table.keys()):
            state = self.table[key]
            new_holders = [h for h in state.holders if h.node != node]
            if len(new_holders) != len(state.holders):
                count += 1
                self.table[key] = LockState(path=state.path, holders=new_holders)
                await self._promote_and_deliver(key, state.path)
                if not self.table[key].holders and not self.waiters.get(key):
                    self.table.pop(key, None)
        for key in list(self.waiters.keys()):
            self.waiters[key] = [w for w in self.waiters[key] if w.node_id != node]
            if not self.waiters[key]:
                self.waiters.pop(key, None)
        return count

    # ── claim helpers ────────────────────────────────────────────────

    async def _local_claim(
        self, path: str, mode: LockMode, remaining: float, start: float,
    ) -> ClaimResult | None:
        key = path_key(path)
        granted = self._master_admit(key, path, self.node_id, mode)
        if granted:
            self.held[key] = (path, mode.value)
            return ClaimResult(granted=True, elapsed_ms=self._ms(start))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        waiter = Waiter(
            req_id=uuid.uuid4().hex, node_id=self.node_id, path=path, mode=mode,
            hostname=self.hostname, pid=self.pid, local_future=fut,
        )
        self.waiters.setdefault(key, []).append(waiter)
        try:
            res = await asyncio.wait_for(fut, remaining)
        except asyncio.TimeoutError:
            self._remove_waiter(key, waiter.req_id)
            holders = list(self.table.get(key, LockState(path=path)).holders)
            return ClaimResult(
                granted=False, timed_out=True, current_holders=holders,
                elapsed_ms=self._ms(start),
            )
        if res.get("status") == "granted":
            self.held[key] = (path, mode.value)
            return ClaimResult(granted=True, elapsed_ms=self._ms(start))
        if res.get("status") == "retry":
            return None
        return ClaimResult(granted=False, elapsed_ms=self._ms(start))

    async def _remote_claim(
        self, path: str, master: str, mode: LockMode, remaining: float, start: float,
    ) -> ClaimResult | None:
        addr = self.members().get(master)
        if addr is None:
            return None
        host, port, _, _ = addr
        req_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[req_id] = fut
        sent = await self.transport.send(host, port, {
            "type": LOCK_REQ, "req_id": req_id, "path": path, "mode": mode.value,
            "node_id": self.node_id, "hostname": self.hostname, "pid": self.pid,
        })
        if not sent:
            self.pending.pop(req_id, None)
            return None
        try:
            res = await asyncio.wait_for(fut, remaining)
        except asyncio.TimeoutError:
            self.pending.pop(req_id, None)
            await self.transport.send(host, port, {
                "type": LOCK_CANCEL, "req_id": req_id, "path": path,
                "node_id": self.node_id,
            })
            holders = await self._best_effort_holders(path)
            return ClaimResult(
                granted=False, timed_out=True, current_holders=holders,
                elapsed_ms=self._ms(start),
            )
        finally:
            self.pending.pop(req_id, None)
        status = res.get("status")
        if status == "granted":
            self.held[path_key(path)] = (path, mode.value)
            return ClaimResult(granted=True, elapsed_ms=self._ms(start))
        if status in ("retry", "not_master"):
            return None
        return ClaimResult(granted=False, elapsed_ms=self._ms(start))

    async def _best_effort_holders(self, path: str) -> list[Holder]:
        try:
            state = await self.held_by(path)
        except Exception:
            return []
        return list(state.holders) if state is not None else []

    # ── master-side table operations (synchronous mutation) ──────────

    def _master_admit(self, key: str, path: str, node_id: str, mode: LockMode) -> bool:
        """Grant immediately iff no queue ahead and compatible. FIFO-fair."""
        if self.waiters.get(key):
            return False
        state = self.table.get(key) or LockState(path=path)
        if not compatible(state, node_id, mode):
            return False
        holders = [h for h in state.holders if h.node != node_id]
        hostname, pid = (self.hostname, self.pid) if node_id == self.node_id else ("", 0)
        holders.append(self.make_holder(node_id, mode, hostname, pid))
        self.table[key] = LockState(path=path, holders=holders)
        return True

    def _master_promote(self, key: str, path: str) -> list[Waiter]:
        """Promote grantable waiters at the head of the FIFO queue."""
        waiters = self.waiters.get(key)
        if not waiters:
            return []
        state = self.table.get(key) or LockState(path=path)
        promoted: list[Waiter] = []
        i = 0
        while i < len(waiters):
            w = waiters[i]
            if not compatible(state, w.node_id, w.mode):
                break  # FIFO: stop at first blocked waiter to avoid barging
            holders = [h for h in state.holders if h.node != w.node_id]
            holders.append(self.make_holder(w.node_id, w.mode, w.hostname, w.pid))
            state = LockState(path=path, holders=holders)
            promoted.append(w)
            i += 1
            if w.mode == LockMode.EXCLUSIVE:
                break
        self.table[key] = state
        rest = waiters[i:]
        if rest:
            self.waiters[key] = rest
        else:
            self.waiters.pop(key, None)
        return promoted

    def _remove_waiter(self, key: str, req_id: str) -> None:
        waiters = self.waiters.get(key)
        if not waiters:
            return
        self.waiters[key] = [w for w in waiters if w.req_id != req_id]
        if not self.waiters[key]:
            self.waiters.pop(key, None)

    async def _promote_and_deliver(self, key: str, path: str) -> None:
        for w in self._master_promote(key, path):
            await self._deliver(w, "granted")

    async def _deliver(self, waiter: Waiter, status: str, prior: list | None = None) -> None:
        if waiter.local_future is not None:
            if not waiter.local_future.done():
                waiter.local_future.set_result({"status": status, "prior": prior or []})
            return
        addr = self.members().get(waiter.node_id)
        if addr is None:
            return
        host, port, _, _ = addr
        msg_type = LOCK_GRANT if status == "granted" else LOCK_DENY
        await self.transport.send(host, port, {
            "type": msg_type, "req_id": waiter.req_id, "path": waiter.path,
            "status": status, "prior": prior or [],
        })

    async def _master_release(self, key: str, path: str, node_id: str) -> None:
        state = self.table.get(key)
        if state is not None:
            new_holders = [h for h in state.holders if h.node != node_id]
            self.table[key] = LockState(path=path, holders=new_holders)
        await self._promote_and_deliver(key, path)
        if not self.table.get(key, LockState(path=path)).holders and not self.waiters.get(key):
            self.table.pop(key, None)

    # ── incoming message dispatch ────────────────────────────────────

    async def on_message(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == LOCK_REQ:
            await self._handle_lock_req(msg)
        elif mtype == LOCK_GRANT or mtype == LOCK_DENY:
            self._resolve_pending(msg)
        elif mtype == LOCK_RELEASE:
            await self._handle_release(msg)
        elif mtype == LOCK_CANCEL:
            self._remove_waiter(path_key(msg["path"]), msg["req_id"])
        elif mtype == FORCE_REQ:
            await self._handle_force_req(msg)
        elif mtype == TABLE_SYNC:
            self._handle_table_sync(msg)
        elif mtype == HELD_QUERY:
            await self._handle_held_query(msg)
        elif mtype == HELD_RESP:
            self._resolve_query(msg)
        else:
            log.warning("p2p_dlm: unknown message type %r", mtype)

    async def _handle_lock_req(self, msg: dict) -> None:
        path = msg["path"]
        key = path_key(path)
        node_id = msg["node_id"]
        mode = LockMode(msg["mode"])
        if not self.is_master():
            addr = self.members().get(node_id)
            if addr is not None:
                host, port, _, _ = addr
                await self.transport.send(host, port, {
                    "type": LOCK_DENY, "req_id": msg["req_id"], "path": path,
                    "status": "not_master", "prior": [],
                })
            return
        if self._master_admit(key, path, node_id, mode):
            addr = self.members().get(node_id)
            if addr is not None:
                host, port, _, _ = addr
                await self.transport.send(host, port, {
                    "type": LOCK_GRANT, "req_id": msg["req_id"], "path": path,
                    "status": "granted", "prior": [],
                })
            else:
                # Requester vanished between send and admit; roll back.
                await self._master_release(key, path, node_id)
            return
        self.waiters.setdefault(key, []).append(Waiter(
            req_id=msg["req_id"], node_id=node_id, path=path, mode=mode,
            hostname=msg.get("hostname", ""), pid=int(msg.get("pid", 0)),
            local_future=None,
        ))

    async def _handle_release(self, msg: dict) -> None:
        if not self.is_master():
            return
        path = msg["path"]
        await self._master_release(path_key(path), path, msg["node_id"])

    async def _handle_force_req(self, msg: dict) -> None:
        path = msg["path"]
        key = path_key(path)
        node_id = msg["node_id"]
        mode = LockMode(msg["mode"])
        addr = self.members().get(node_id)
        if not self.is_master():
            if addr is not None:
                host, port, _, _ = addr
                await self.transport.send(host, port, {
                    "type": LOCK_DENY, "req_id": msg["req_id"], "path": path,
                    "status": "not_master", "prior": [],
                })
            return
        prior = list(self.table.get(key, LockState(path=path)).holders)
        self.table[key] = LockState(
            path=path,
            holders=[self.make_holder(
                node_id, mode, msg.get("hostname", ""), int(msg.get("pid", 0)),
            )],
        )
        if addr is not None:
            host, port, _, _ = addr
            await self.transport.send(host, port, {
                "type": LOCK_GRANT, "req_id": msg["req_id"], "path": path,
                "status": "granted",
                "prior": [self._holder_dict(h) for h in prior],
            })

    def _handle_table_sync(self, msg: dict) -> None:
        if not self.is_master():
            return
        sender = msg["node_id"]
        hostname = msg.get("hostname", "")
        pid = int(msg.get("pid", 0))
        for hold in msg.get("holds", []):
            path = hold["path"]
            key = path_key(path)
            mode = LockMode(hold["mode"])
            state = self.table.get(key) or LockState(path=path)
            holders = [h for h in state.holders if h.node != sender]
            holders.append(self.make_holder(sender, mode, hostname, pid))
            self.table[key] = LockState(path=path, holders=holders)

    async def _handle_held_query(self, msg: dict) -> None:
        path = msg["path"]
        holders: list[dict] = []
        if self.is_master():
            state = self.table.get(path_key(path))
            if state is not None:
                holders = [self._holder_dict(h) for h in state.holders]
        addr = self.members().get(msg["node_id"])
        if addr is not None:
            host, port, _, _ = addr
            await self.transport.send(host, port, {
                "type": HELD_RESP, "req_id": msg["req_id"], "holders": holders,
            })

    def _resolve_pending(self, msg: dict) -> None:
        fut = self.pending.get(msg.get("req_id", ""))
        if fut is not None and not fut.done():
            fut.set_result({"status": msg.get("status", "denied"), "prior": msg.get("prior", [])})
        elif msg.get("status") == "granted":
            # Grant arrived after we gave up — release it so the master
            # doesn't leak the lock to a node that isn't waiting.
            asyncio.create_task(self._release_orphan(msg.get("path", "")))

    def _resolve_query(self, msg: dict) -> None:
        fut = self.pending_query.get(msg.get("req_id", ""))
        if fut is not None and not fut.done():
            fut.set_result({"holders": msg.get("holders", [])})

    async def _release_orphan(self, path: str) -> None:
        if not path:
            return
        master = self.current_master()
        addr = self.members().get(master)
        if addr is None:
            return
        host, port, _, _ = addr
        await self.transport.send(host, port, {
            "type": LOCK_RELEASE, "path": path, "node_id": self.node_id,
        })

    # ── membership change / failover ─────────────────────────────────

    async def on_membership_change(self) -> None:
        new_master = self.current_master()
        if new_master == self.master_id:
            return
        log.info("p2p_dlm: master %s -> %s", self.master_id, new_master)
        self.master_id = new_master

        # Fail in-flight remote requests so claim() retries against the
        # new master; mirrors MXFS fail_all_pending.
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.set_result({"status": "retry", "prior": []})
        self.pending.clear()
        for fut in list(self.pending_query.values()):
            if not fut.done():
                fut.set_result({"holders": []})
        self.pending_query.clear()

        # Fail local waiters too (their grant may have been lost with the
        # old master); claim() will re-enter and re-queue.
        for waiters in list(self.waiters.values()):
            for w in waiters:
                if w.local_future is not None and not w.local_future.done():
                    w.local_future.set_result({"status": "retry", "prior": []})
        self.waiters.clear()

        if new_master == self.node_id:
            self._seed_table_from_self()
        else:
            addr = self.members().get(new_master)
            if addr is not None:
                host, port, _, _ = addr
                await self.transport.send(host, port, {
                    "type": TABLE_SYNC, "node_id": self.node_id,
                    "hostname": self.hostname, "pid": self.pid,
                    "holds": self._held_list(),
                })

    def _seed_table_from_self(self) -> None:
        self.table = {}
        for key, (path, mode_value) in self.held.items():
            self.table[key] = LockState(
                path=path,
                holders=[self.make_holder(
                    self.node_id, LockMode(mode_value), self.hostname, self.pid,
                )],
            )

    def _held_list(self) -> list[dict]:
        return [{"path": path, "mode": mode} for (path, mode) in self.held.values()]

    # ── misc ─────────────────────────────────────────────────────────

    @staticmethod
    def _holder_dict(h: Holder) -> dict:
        return {
            "node": h.node, "mode": h.mode, "epoch": h.epoch,
            "acquired_at": h.acquired_at, "pid": h.pid, "hostname": h.hostname,
        }

    @staticmethod
    def _ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)
