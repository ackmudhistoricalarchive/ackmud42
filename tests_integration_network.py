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

    def test_websocket_upgrade_with_proxy_v2_prefix(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            proxy_v2 = bytes.fromhex(
                "0d0a0d0a000d0a515549540a"  # signature
                "2111000c"                  # v2/proxy, TCPv4, addr len 12
                "cb00710a"                  # src ip 203.0.113.10
                "7f000001"                  # dst ip 127.0.0.1
                "c350"                      # src port 50000
                "22bc"                      # dst port 8892
            )
            req = proxy_v2 + (
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


    def test_websocket_upgrade_headers_split_across_ticks(self):
        """When the HTTP upgrade request arrives in two separate TCP writes
        separated by more than one game tick (~125 ms), the server must not
        treat the first partial read as a telnet command.  Without the fix
        read_from_buffer() would extract 'GET / HTTP/1.1' from inbuf between
        ticks, leaving 'Host: ...' at the front of the buffer so the
        subsequent call to maybe_process_websocket_handshake() would see a
        non-GET line and mark the connection as telnet instead of WebSocket."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            part1 = (
                "GET / HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Upgrade: websocket\r\n"
            ).encode("ascii")
            part2 = (
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            ).encode("ascii")
            s.sendall(part1)
            # Sleep longer than one game tick (1/PULSE_PER_SECOND = 125 ms)
            # so the server processes the first chunk before the second arrives.
            time.sleep(0.2)
            s.sendall(part2)
            response = _read_with_timeout(s, timeout=3)
            self.assertIn(b"101 Switching Protocols", response,
                          f"split-header handshake failed; got: {response!r}")
        finally:
            s.close()

    def test_websocket_upgrade_with_large_browser_headers(self):
        """Real browsers send many extra headers (User-Agent, Accept-Language,
        Cookie, Sec-Fetch-*, etc.) that can push the upgrade request well past
        1024 bytes.  The server must handle this without dropping the data."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            req = (
                "GET / HTTP/1.1\r\n"
                "Host: ackmud.com:8892\r\n"
                "Connection: Upgrade\r\n"
                "Pragma: no-cache\r\n"
                "Cache-Control: no-cache\r\n"
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36\r\n"
                "Upgrade: websocket\r\n"
                "Origin: http://ackmud.com\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Extensions: permessage-deflate; "
                "client_max_window_bits\r\n"
                "Accept-Encoding: gzip, deflate, br, zstd\r\n"
                "Accept-Language: en-US,en;q=0.9,fr;q=0.8,de;q=0.7,"
                "ja;q=0.6,zh-CN;q=0.5,zh;q=0.4,ko;q=0.3\r\n"
                "Sec-Fetch-Dest: websocket\r\n"
                "Sec-Fetch-Mode: websocket\r\n"
                "Sec-Fetch-Site: same-origin\r\n"
                "Cookie: " + "x" * 512 + "\r\n"
                "\r\n"
            ).encode("ascii")
            self.assertGreater(len(req), 1024,
                               "test request must exceed 1024 bytes")
            s.sendall(req)
            response = _read_with_timeout(s, timeout=3)
            self.assertIn(b"101 Switching Protocols", response,
                          f"handshake failed; got: {response!r}")
        finally:
            s.close()

    def test_websocket_upgrade_with_very_large_cookie_headers(self):
        """Browser sessions can attach very large Cookie headers. The
        WebSocket upgrade must still succeed when the initial HTTP request is
        much larger than the historical 2560-byte inbuf size."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            cookie_value = "session=" + ("x" * 7000)
            req = (
                "GET / HTTP/1.1\r\n"
                "Host: ackmud.com:8892\r\n"
                "Connection: keep-alive, Upgrade\r\n"
                "Pragma: no-cache\r\n"
                "Cache-Control: no-cache\r\n"
                "Upgrade: websocket\r\n"
                "Origin: https://ackmud.com\r\n"
                "Sec-WebSocket-Protocol: binary\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Accept-Encoding: gzip, deflate, br\r\n"
                "Accept-Language: en-US,en;q=0.9\r\n"
                f"Cookie: {cookie_value}\r\n"
                "\r\n"
            ).encode("ascii")
            self.assertGreater(len(req), 3000)
            s.sendall(req)
            response = _read_with_timeout(s, timeout=3)
            self.assertIn(
                b"101 Switching Protocols", response,
                f"large browser-like request should handshake; got: {response!r}",
            )
        finally:
            s.close()


    def test_websocket_upgrade_with_huge_headers_over_10k(self):
        """Some real browser sessions include very large cookie payloads
        (analytics + auth + feature flags) that can push the opening HTTP
        upgrade request past 10KB. The server should still complete the
        handshake instead of disconnecting with code 1006."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            cookie_value = "session=" + ("x" * 14000)
            req = (
                "GET / HTTP/1.1\r\n"
                "Host: ackmud.com:8892\r\n"
                "Connection: keep-alive, Upgrade\r\n"
                "Pragma: no-cache\r\n"
                "Cache-Control: no-cache\r\n"
                "Upgrade: websocket\r\n"
                "Origin: https://ackmud.com\r\n"
                "Sec-WebSocket-Protocol: binary\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits\r\n"
                "Accept-Encoding: gzip, deflate, br, zstd\r\n"
                "Accept-Language: en-US,en;q=0.9\r\n"
                f"Cookie: {cookie_value}\r\n"
                "\r\n"
            ).encode("ascii")
            self.assertGreater(len(req), 10000)
            s.sendall(req)
            response = _read_with_timeout(s, timeout=3)
            self.assertIn(
                b"101 Switching Protocols", response,
                f"huge browser-like request should handshake; got: {response!r}",
            )
        finally:
            s.close()

    def test_websocket_login_no_telnet_iac_bytes(self):
        """After the WebSocket handshake the server must never send raw
        telnet IAC sequences (0xFF) inside WebSocket text frames.  The
        echo-off / echo-on commands used during password entry contain
        IAC bytes which are invalid UTF-8 and cause browsers to drop the
        connection."""
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

            # Consume the greeting frame(s) until we see a name prompt.
            frames = []
            parts = response.split(b"\r\n\r\n", 1)
            leftover = parts[1] if len(parts) == 2 else b""
            if leftover:
                # Parse the first server frame from the leftover bytes
                frames.append(leftover)

            # Read any additional greeting frames
            greeting_data = _read_with_timeout(s, timeout=2)
            if greeting_data:
                frames.append(greeting_data)

            # Send a name that is very unlikely to exist --
            # the server should respond with a confirmation prompt
            # rather than a password prompt with IAC echo-off.
            s.sendall(_ws_masked_text_frame(b"TestWsNoIAC\n"))

            # Read the server's response frames
            login_data = _read_with_timeout(s, timeout=3)

            # Verify no IAC byte (0xFF) appears anywhere in the raw
            # data sent by the server.  IAC is never valid UTF-8 and
            # indicates a telnet command leaked into the WebSocket stream.
            all_data = b"".join(frames) + (greeting_data or b"") + (login_data or b"")
            iac_positions = [i for i, b in enumerate(all_data) if b == 0xFF]
            self.assertEqual(
                iac_positions, [],
                f"Server sent telnet IAC byte(s) at position(s) {iac_positions} "
                f"in WebSocket data stream; raw bytes around first IAC: "
                f"{all_data[max(0, iac_positions[0]-4):iac_positions[0]+8]!r}"
                if iac_positions else "No IAC bytes found"
            )
        finally:
            s.close()


    def test_websocket_header_overflow_does_not_crash_server(self):
        """When accumulated HTTP upgrade headers reach the inbuf overflow
        threshold (sizeof(inbuf)-10 bytes) the server must disconnect
        gracefully rather than crashing via a NULL d->character dereference.
        Before the fix, any request whose headers filled inbuf near the overflow boundary and did NOT yet contain the \\r\\n\\r\\n terminator
        would cause the overflow check to fire on the next game tick and
        crash the server with a SIGSEGV, dropping all active connections."""
        # Build a request whose headers are exactly (16*640-10) bytes without the
        # terminating \\r\\n\\r\\n so the first read leaves the buffer in the
        # danger zone.  We then send the remainder (including \\r\\n\\r\\n)
        # in a second write after a tick boundary.
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        prefix = (
            "GET / HTTP/1.1\r\n"
            "Host: ackmud.com:8892\r\n"
            "Connection: Upgrade\r\n"
            "Upgrade: websocket\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Cookie: x="
        )
        # Fill the cookie value so that prefix + value reaches target_len bytes
        # (the overflow threshold). We stop short of \r\n\r\n deliberately.
        target_len = (16 * 640) - 10
        filler_len = target_len - len(prefix)
        if filler_len < 1:
            self.skipTest("prefix already exceeds overflow threshold")
        part1 = (prefix + "a" * filler_len).encode("ascii")
        part2 = "\r\n\r\n".encode("ascii")

        self.assertEqual(len(part1), target_len)

        s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        try:
            s.sendall(part1)
            # Sleep past a game tick so the server processes the partial
            # read and the overflow check fires on the NEXT call.
            time.sleep(0.2)
            s.sendall(part2)
            response = _read_with_timeout(s, timeout=3)
            # The server should either complete the handshake or disconnect
            # cleanly.  The critical property is that it does NOT crash —
            # verified by checking that a subsequent fresh connection still
            # gets a valid response.
        finally:
            s.close()

        # Confirm the server is still alive by making a new connection.
        s2 = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        try:
            key2 = base64.b64encode(os.urandom(16)).decode("ascii")
            req2 = (
                "GET / HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key2}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            ).encode("ascii")
            s2.sendall(req2)
            resp2 = _read_with_timeout(s2, timeout=3)
            self.assertIn(
                b"101 Switching Protocols", resp2,
                "Server crashed after the overflow condition; subsequent "
                f"connection did not get a valid handshake: {resp2!r}",
            )
        finally:
            s2.close()

    def test_websocket_close_frame_roundtrip(self):
        """After a successful WebSocket handshake the server must respond to a
        client Close frame (opcode 0x8) with a raw Close frame — NOT with the
        Close bytes wrapped inside a Text frame.  Before the fix, the pong and
        close senders used write_to_descriptor() which adds a WS text-frame
        header when ws_active is TRUE, producing a double-framed response that
        browsers reject."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
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

            # Drain the greeting frame(s).
            _read_with_timeout(s, timeout=1)

            # Send a masked Close frame (opcode 0x8, no payload).
            mask = os.urandom(4)
            close_frame = bytes([0x88, 0x80]) + mask  # FIN|Close, masked, len=0
            s.sendall(close_frame)

            # Server must respond with a Close frame (first byte 0x88),
            # not a Text frame (0x81) wrapping Close bytes.
            s.settimeout(3)
            try:
                resp = s.recv(16)
            except socket.timeout:
                resp = b""
            # Either the server sent a close frame or closed the connection.
            # If it sent data, the first byte must be 0x88 (close frame).
            if resp:
                self.assertEqual(
                    resp[0], 0x88,
                    f"Server responded to Close frame with opcode "
                    f"0x{resp[0]:02x} instead of 0x88 (Close); "
                    f"likely double-framed: {resp!r}",
                )
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
