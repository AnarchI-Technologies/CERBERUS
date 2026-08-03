from __future__ import annotations

import http.client
import http.server
import socket
import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from auth_gateway import handle_connection  # noqa: E402


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    def _send(self) -> None:
        body = self.server.account.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _send
    do_POST = _send

    def log_message(self, _format: str, *args: object) -> None:
        return


def start_upstream(
    account: str,
    port: int = 0,
) -> tuple[http.server.ThreadingHTTPServer, int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), UpstreamHandler)
    server.account = account
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def start_gateway(token: str, upstream_port: int) -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(20)

    def accept() -> None:
        while True:
            try:
                client, _address = listener.accept()
            except OSError:
                return
            threading.Thread(
                target=handle_connection,
                args=(client, "127.0.0.1", upstream_port, token),
                daemon=True,
            ).start()

    threading.Thread(target=accept, daemon=True).start()
    return listener, int(listener.getsockname()[1])


def request(
    port: int,
    method: str,
    path: str,
    token: str = "",
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = b"{}" if method == "POST" else None
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return int(response.status), payload


class AccountGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream_a, upstream_a_port = start_upstream("account-a")
        cls.upstream_b, upstream_b_port = start_upstream("account-b")
        cls.gateway_a, cls.port_a = start_gateway("account-a-bearer", upstream_a_port)
        cls.gateway_b, cls.port_b = start_gateway("account-b-bearer", upstream_b_port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.gateway_a.close()
        cls.gateway_b.close()
        cls.upstream_a.shutdown()
        cls.upstream_b.shutdown()

    def test_health_is_the_only_anonymous_route(self) -> None:
        self.assertEqual(request(self.port_a, "GET", "/healthz")[0], 200)
        self.assertEqual(request(self.port_a, "GET", "/stats")[0], 401)
        self.assertEqual(request(self.port_a, "GET", "/stream/stats")[0], 401)
        self.assertEqual(request(self.port_a, "POST", "/stream/chat")[0], 401)

    def test_account_a_token_cannot_access_account_b(self) -> None:
        status, _payload = request(
            self.port_b,
            "GET",
            "/stats",
            token="account-a-bearer",
        )
        self.assertEqual(status, 401)

    def test_anonymous_health_cannot_pipeline_into_protected_route(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port_a), timeout=3) as connection:
            connection.sendall(
                b"GET /healthz HTTP/1.1\r\nHost: local\r\n\r\n"
                b"GET /stats HTTP/1.1\r\nHost: local\r\n\r\n"
            )
            response_bytes = bytearray()
            while True:
                chunk = connection.recv(8192)
                if not chunk:
                    break
                response_bytes.extend(chunk)
        self.assertEqual(bytes(response_bytes).count(b"200 OK"), 1)

    def test_correct_account_token_reaches_only_its_upstream(self) -> None:
        status_a, payload_a = request(
            self.port_a,
            "GET",
            "/stats",
            token="account-a-bearer",
        )
        status_b, payload_b = request(
            self.port_b,
            "GET",
            "/stats",
            token="account-b-bearer",
        )
        self.assertEqual((status_a, payload_a), (200, b"account-a"))
        self.assertEqual((status_b, payload_b), (200, b"account-b"))

    def test_gateway_recovers_after_core_crash_on_same_loopback_address(self) -> None:
        upstream, upstream_port = start_upstream("before-restart")
        gateway, gateway_port = start_gateway("restart-bearer", upstream_port)
        restarted = None
        try:
            self.assertEqual(
                request(gateway_port, "GET", "/stats", token="restart-bearer"),
                (200, b"before-restart"),
            )
            upstream.shutdown()
            upstream.server_close()
            with self.assertRaises((OSError, http.client.HTTPException)):
                request(gateway_port, "GET", "/stats", token="restart-bearer")

            restarted, rebound_port = start_upstream("after-restart", upstream_port)
            self.assertEqual(rebound_port, upstream_port)
            self.assertEqual(
                request(gateway_port, "GET", "/stats", token="restart-bearer"),
                (200, b"after-restart"),
            )
        finally:
            gateway.close()
            if restarted is not None:
                restarted.shutdown()
                restarted.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
