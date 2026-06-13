"""Peer-to-peer TCP transport for the broker-free DLM.

Each node runs one listener. DLM coordination is **message-passing**, not
RPC: a request (``LOCK_REQ``) and its eventual reply
(``LOCK_GRANT`` / ``LOCK_DENY``) are independent messages, each sent from
the originator to the target's listener. Replies are correlated by
``req_id`` at the DLM layer (see ``p2p_dlm``), so a blocking claim can
wait on a future without holding a TCP connection open for its whole
(possibly long) duration.

Wire framing: a 4-byte big-endian length prefix followed by a UTF-8 JSON
object. JSON is used for the same reasons as ``discovery`` — tiny
payloads, easy to debug, and this is not a hot path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import Awaitable, Callable


log = logging.getLogger(__name__)

LEN_PREFIX = struct.Struct(">I")
MAX_FRAME = 4 * 1024 * 1024  # 4 MB ceiling; DLM messages are tiny

# Handler receives one decoded message; any reply is sent back as a
# separate message via Transport.send(), so the handler returns nothing.
MessageHandler = Callable[[dict], Awaitable[None]]


async def read_frame(reader: asyncio.StreamReader) -> dict:
    hdr = await reader.readexactly(LEN_PREFIX.size)
    (length,) = LEN_PREFIX.unpack(hdr)
    if length == 0 or length > MAX_FRAME:
        raise ValueError(f"bad frame length {length}")
    body = await reader.readexactly(length)
    return json.loads(body.decode("utf-8"))


def encode_frame(msg: dict) -> bytes:
    body = json.dumps(msg).encode("utf-8")
    return LEN_PREFIX.pack(len(body)) + body


class Transport:
    """One TCP listener per node plus a best-effort send helper."""

    def __init__(self, bind_host: str = "0.0.0.0", port: int = 0) -> None:
        self.bind_host = bind_host
        self.port = port  # 0 => ephemeral; real port filled in by start()
        self.server: asyncio.base_events.Server | None = None
        self.handler: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self.handler = handler

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self.handle_client, host=self.bind_host, port=self.port,
        )
        # Resolve the actual bound port when an ephemeral port was requested.
        sock = self.server.sockets[0]
        self.port = sock.getsockname()[1]
        log.info("p2p transport listening on %s:%d", self.bind_host, self.port)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass
            self.server = None

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                try:
                    msg = await read_frame(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                except ValueError as exc:
                    log.warning("p2p: dropping bad frame: %s", exc)
                    return
                if self.handler is not None:
                    try:
                        await self.handler(msg)
                    except Exception:
                        log.exception("p2p message handler failed")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def send(
        self, host: str, port: int, msg: dict, *, retries: int = 2,
    ) -> bool:
        """Best-effort fire-and-forget send of one message to a peer.

        Opens a short-lived connection, writes one frame, and closes.
        Returns True if the frame was handed to the kernel, False if the
        peer was unreachable after ``retries``.
        """
        if not port:
            return False
        frame = encode_frame(msg)
        last_exc: Exception | None = None
        for _ in range(retries + 1):
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except (ConnectionError, OSError) as exc:
                last_exc = exc
                await asyncio.sleep(0.05)
                continue
            try:
                writer.write(frame)
                await writer.drain()
                return True
            except (ConnectionError, OSError) as exc:
                last_exc = exc
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        log.warning("p2p send to %s:%d failed: %s", host, port, last_exc)
        return False
