#!/usr/bin/env python3
import base64
import hashlib
import os
import random
import socket
import struct
import subprocess
import time
import unittest


ROOT = os.path.dirname(__file__)
SRC_DIR = os.path.join(ROOT, "src")


def _read_with_timeout(sock: socket.socket, timeout: float = 3.0) -> bytes:
    sock.settimeout(timeout)
    chunks = []
    end = time.time() + timeout
    while time.time() < end:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
        if len(chunk) < 4096:
            break
    return b"".join(chunks)


def _ws_masked_text_frame(payload: bytes) -> bytes:
    mask = os.urandom(4)
    plen = len(payload)
    out = bytearray()
    out.append(0x81)
    if plen <= 125:
        out.append(0x80 | plen)
    elif plen <= 65535:
        out.append(0x80 | 126)
        out.extend(struct.pack("!H", plen))
    else:
        out.append(0x80 | 127)
        out.extend(struct.pack("!Q", plen))
    out.extend(mask)
    out.extend(bytes([payload[i] ^ mask[i % 4] for i in range(plen)]))
    return bytes(out)


def _ws_read_frame(sock: socket.socket, timeout: float = 3.0):
    sock.settimeout(timeout)
    hdr = sock.recv(2)
    if len(hdr) < 2:
        raise RuntimeError("short websocket header")
    b0, b1 = hdr
    fin = (b0 >> 7) & 1
    opcode = b0 & 0x0F
    masked = (b1 >> 7) & 1
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    mask = sock.recv(4) if masked else b""
    payload = b""
    while len(payload) < length:
        payload += sock.recv(length - len(payload))
    if masked:
        payload = bytes([payload[i] ^ mask[i % 4] for i in range(length)])
    return fin, opcode, payload


class NetworkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ack_bin = os.path.join(SRC_DIR, "ack")
        if not os.path.exists(ack_bin):
            subprocess.run(["make", "merc"], cwd=SRC_DIR, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cls.port = random.randint(42000, 52000)
        cls.proc = subprocess.Popen(
            ["./ack", str(cls.port)],
            cwd=SRC_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", cls.port), timeout=0.3)
                s.close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("ack server did not open port in time")

    @classmethod
    def tearDownClass(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                cls.proc.kill()

    def test_telnet_connection_reaches_name_prompt(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        try:
            s.sendall(b"Tester\n")
            data = _read_with_timeout(s, timeout=4)
            self.assertTrue(data, "expected telnet login response bytes")
            lower = data.lower()
            self.assertTrue(
                (b"tester" in lower) or (b"y/n" in lower) or (b"name" in lower),
                f"expected telnet login prompt/confirmation, got: {data!r}",
            )
        finally:
            s.close()

    def test_websocket_upgrade_with_proxy_prefix_line(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            req = (
                "PROXY TCP4 203.0.113.10 127.0.0.1 50000 8892\r\n"
                "GET / HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            ).encode("ascii")
            s.sendall(req)
            response = _read_with_timeout(s, timeout=3)
            self.assertIn(b"101 Switching Protocols", response)
        finally:
            s.close()

    def test_websocket_upgrade_and_text_roundtrip(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            req = (
                "GET / HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            ).encode("ascii")
            s.sendall(req)
            response = _read_with_timeout(s, timeout=3)
            self.assertIn(b"101 Switching Protocols", response)

            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
            )
            self.assertIn(b"Sec-WebSocket-Accept: " + accept, response)

            parts = response.split(b"\r\n\r\n", 1)
            framed = parts[1] if len(parts) == 2 else b""
            if not framed:
                s.sendall(_ws_masked_text_frame(b"\n"))
                fin, opcode, payload = _ws_read_frame(s, timeout=4)
            else:
                # parse a server text frame already returned with handshake
                b0, b1 = framed[0], framed[1]
                self.assertEqual(b0 & 0x0F, 0x1)
                plen = b1 & 0x7F
                offset = 2
                if plen == 126:
                    plen = struct.unpack("!H", framed[offset:offset+2])[0]
                    offset += 2
                payload = framed[offset:offset+plen]
                fin = (b0 >> 7) & 1
                opcode = b0 & 0x0F

            self.assertEqual(fin, 1)
            self.assertEqual(opcode, 0x1)
            lower = payload.lower()
            self.assertTrue(
                (b"name" in lower) or (b"who do you think you are" in lower),
                f"expected login prompt in websocket payload, got: {payload!r}",
            )
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
