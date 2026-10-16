"""One serialized Modbus TCP session per physical Ethernet-to-RS485 gateway.

A Modbus TCP gateway can multiplex Unit IDs onto the same half-duplex RS485 bus.
Independent HA config entries MUST NOT own independent sessions to that gateway.
This module intentionally depends only on the Python standard library.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time

_LOGGER = logging.getLogger(__name__)
_SESSIONS: dict[tuple[str, int], "GatewaySession"] = {}
_READ_CODES = frozenset((1, 2, 3, 4))


class ModbusFrameError(ConnectionError):
    """Malformed MBAP frame or response for a different transaction."""


class GatewaySession:
    """A shared, reference-counted, one-request-at-a-time Modbus TCP session."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.references = 0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._last_sent = 0.0
        self.successes = 0
        self.failures = 0
        self.connections = 0
        self.recovered_reads = 0
        self.last_response_ms: int | None = None
        self.last_failure: str | None = None
        self.last_error: str | None = None
        self.last_unit_id: int | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    def diagnostics(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "connected": self.connected,
            "clients": self.references,
            "successful_requests": self.successes,
            "failed_attempts": self.failures,
            "tcp_connections": self.connections,
            "recovered_reads": self.recovered_reads,
            "last_response_ms": self.last_response_ms,
            "last_failure": self.last_failure,
            "last_unit_id": self.last_unit_id,
            "last_error": self.last_error,
        }

    async def _close_locked(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1.0)
            except (OSError, TimeoutError, ConnectionError):
                pass

    async def reset(self) -> None:
        """Reset a bad gateway connection without discarding other config entries."""
        async with self._lock:
            await self._close_locked()

    async def exchange(self, request: bytes, timeout: float, *, retry_read: bool) -> bytes:
        """Send one Modbus TCP ADU and receive exactly one matching ADU.

        Locking covers both sending and receiving: no second request may reach
        this serial gateway while the previous transaction is in progress.
        Writes must never be replayed automatically after an ambiguous timeout.
        """
        if len(request) < 8:
            raise ValueError("Invalid Modbus TCP request")
        expected_tid, expected_protocol, expected_length, expected_unit = struct.unpack(">HHHB", request[:7])
        if expected_protocol != 0 or expected_length != len(request) - 6:
            raise ValueError("Invalid Modbus TCP request MBAP header")
        request_function = request[7]

        async with self._lock:
            attempts = 2 if retry_read else 1
            for attempt in range(attempts):
                started = time.monotonic()
                stage = "connect"
                try:
                    async with asyncio.timeout(timeout):
                        if not self.connected:
                            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
                            self.connections += 1
                            _LOGGER.debug("[%s:%s] Connected; shared clients=%d", self.host, self.port, self.references)
                        stage = "turnaround"
                        # Conservative RTU inter-request turnaround for TCP/serial bridges.
                        delay = 0.05 - (time.monotonic() - self._last_sent)
                        if delay > 0:
                            await asyncio.sleep(delay)
                        stage = "send"
                        self._writer.write(request)
                        self._last_sent = time.monotonic()
                        await self._writer.drain()

                        # TCP is a byte stream; short reads are normal.
                        stage = "mbap_header"
                        header = await self._reader.readexactly(7)
                        transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                        if protocol != 0 or not 2 <= length <= 254:
                            raise ModbusFrameError("Invalid Modbus TCP MBAP length/protocol")
                        stage = "mbap_body"
                        body = await self._reader.readexactly(length - 1)
                        if transaction != expected_tid or unit != expected_unit:
                            raise ModbusFrameError(
                                f"Wrong MBAP transaction/unit: {transaction}/{unit}, "
                                f"expected {expected_tid}/{expected_unit}"
                            )
                        self.last_unit_id = unit
                        self.last_error = None
                        self.successes += 1
                        self.last_response_ms = round((time.monotonic() - started) * 1000)
                        if attempt:
                            self.recovered_reads += 1
                            _LOGGER.debug(
                                "[%s:%s] Modbus TCP slave=%d fc=%d tid=%d recovered on read retry; "
                                "last_failure=%s",
                                self.host, self.port, expected_unit, request_function,
                                expected_tid, self.last_failure,
                            )
                        return header + body
                except asyncio.CancelledError:
                    # Cancellation may occur in the middle of an ADU; do not reuse it.
                    if self._writer is not None:
                        self._writer.close()
                    self._reader = self._writer = None
                    raise
                except (OSError, TimeoutError, asyncio.IncompleteReadError, ModbusFrameError) as exc:
                    self.failures += 1
                    elapsed = round((time.monotonic() - started) * 1000)
                    detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                    self.last_error = (
                        f"{detail}, stage={stage}, elapsed_ms={elapsed}, "
                        f"slave={expected_unit}, fc={request_function}, tid={expected_tid}"
                    )
                    self.last_failure = self.last_error
                    (_LOGGER.warning if attempt + 1 == attempts else _LOGGER.debug)(
                        "[%s:%s] Modbus TCP request attempt=%d/%d failed: %s",
                        self.host, self.port, attempt + 1, attempts, self.last_error,
                    )
                    await self._close_locked()
                    if attempt + 1 == attempts:
                        raise
                    await asyncio.sleep(0.2)
        raise AssertionError("Unreachable")


def acquire_gateway(host: str, port: int | str) -> GatewaySession:
    key = (str(host), int(port))
    session = _SESSIONS.get(key)
    if session is None:
        session = _SESSIONS[key] = GatewaySession(*key)
    session.references += 1
    return session


async def release_gateway(session: GatewaySession) -> None:
    """Detach a HA config entry; close only after its last user unloads."""
    key = (session.host, session.port)
    if _SESSIONS.get(key) is not session:
        return
    session.references -= 1
    if session.references <= 0:
        _SESSIONS.pop(key, None)
        await session.reset()
