"""Regression tests for a multi-slave Modbus TCP/RS485 gateway (stdlib only)."""
import asyncio
import importlib.util
from pathlib import Path
import struct
import unittest

MODULE = Path(__file__).resolve().parents[1] / "custom_components" / "solarman" / "pysolarman" / "shared_modbus_tcp.py"
spec = importlib.util.spec_from_file_location("shared_modbus_tcp", MODULE)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


def request(unit, tid, code=3):
    return struct.pack(">HHHBBHH", tid, 0, 6, unit, code, 0, 1)


def response(req):
    tid, _protocol, _length, unit = struct.unpack(">HHHB", req[:7])
    pdu = bytes((3, 2, 0, unit))
    return struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu


class TestSharedGateway(unittest.TestCase):
    def test_multiple_slaves_one_tcp_session_and_fragmentation(self):
        async def main():
            connections = 0
            units = []

            async def handle(reader, writer):
                nonlocal connections
                connections += 1
                try:
                    while True:
                        header = await reader.readexactly(7)
                        length = struct.unpack(">H", header[4:6])[0]
                        frame = header + await reader.readexactly(length - 1)
                        units.append(frame[6])
                        reply = response(frame)
                        writer.write(reply[:3])
                        await writer.drain()
                        await asyncio.sleep(0.001)
                        writer.write(reply[3:])
                        await writer.drain()
                except asyncio.IncompleteReadError:
                    pass
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            try:
                first = gateway.acquire_gateway("127.0.0.1", port)
                second = gateway.acquire_gateway("127.0.0.1", port)
                self.assertIs(first, second)
                calls = [first.exchange(request(1 + i % 2, i + 1), 2, retry_read=True) for i in range(20)]
                results = await asyncio.gather(*calls)
                self.assertEqual([r[6] for r in results], [1 + i % 2 for i in range(20)])
                self.assertEqual(units, [1 + i % 2 for i in range(20)])
                self.assertEqual(connections, 1)
                self.assertEqual(first.diagnostics()["successful_requests"], 20)
                await gateway.release_gateway(first)
                self.assertTrue(second.connected)
                await gateway.release_gateway(second)
                self.assertFalse(second.connected)
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(main())

    def test_read_reconnects_after_peer_disconnect(self):
        async def main():
            connections = 0
            async def handle(reader, writer):
                nonlocal connections
                connections += 1
                try:
                    header = await reader.readexactly(7)
                    length = struct.unpack(">H", header[4:6])[0]
                    req = header + await reader.readexactly(length - 1)
                    if connections > 1:
                        writer.write(response(req))
                        await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            session = gateway.acquire_gateway("127.0.0.1", port)
            try:
                value = await session.exchange(request(2, 10), 2, retry_read=True)
                self.assertEqual(value[6], 2)
                self.assertEqual(connections, 2)
                self.assertEqual(session.diagnostics()["failed_attempts"], 1)
                self.assertEqual(session.diagnostics()["recovered_reads"], 1)
                self.assertIn("mbap_header", session.diagnostics()["last_failure"])
            finally:
                await gateway.release_gateway(session)
                server.close()
                await server.wait_closed()

        asyncio.run(main())


    def test_delayed_reply_under_budget_keeps_same_connection(self):
        async def main():
            connections = 0
            async def handle(reader, writer):
                nonlocal connections
                connections += 1
                try:
                    header = await reader.readexactly(7)
                    length = struct.unpack(">H", header[4:6])[0]
                    req = header + await reader.readexactly(length - 1)
                    await asyncio.sleep(0.12)
                    writer.write(response(req))
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            session = gateway.acquire_gateway("127.0.0.1", port)
            try:
                result = await session.exchange(request(1, 12), 0.5, retry_read=True)
                self.assertEqual(result[6], 1)
                self.assertEqual(connections, 1)
                self.assertEqual(session.diagnostics()["failed_attempts"], 0)
                self.assertGreaterEqual(session.diagnostics()["last_response_ms"], 100)
            finally:
                await gateway.release_gateway(session)
                server.close()
                await server.wait_closed()
        asyncio.run(main())

    def test_write_not_replayed_on_uncertain_failure(self):
        async def main():
            connections = 0
            async def handle(reader, writer):
                nonlocal connections
                connections += 1
                try:
                    await reader.readexactly(12)
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            session = gateway.acquire_gateway("127.0.0.1", port)
            try:
                with self.assertRaises(asyncio.IncompleteReadError):
                    await session.exchange(request(1, 11, code=6), 2, retry_read=False)
                self.assertEqual(connections, 1)
            finally:
                await gateway.release_gateway(session)
                server.close()
                await server.wait_closed()
        asyncio.run(main())

    def test_rejects_mismatched_transaction_and_unit(self):
        async def main():
            async def handle(reader, writer):
                try:
                    header = await reader.readexactly(7)
                    length = struct.unpack(">H", header[4:6])[0]
                    req = header + await reader.readexactly(length - 1)
                    bad = bytearray(response(req))
                    bad[6] = 3
                    writer.write(bad)
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            session = gateway.acquire_gateway("127.0.0.1", port)
            try:
                with self.assertRaises(gateway.ModbusFrameError):
                    await session.exchange(request(1, 7), 2, retry_read=False)
            finally:
                await gateway.release_gateway(session)
                server.close()
                await server.wait_closed()
        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
