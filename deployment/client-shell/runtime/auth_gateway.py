"""Default-deny, one-request gateway for every CERBERUS account surface."""

from __future__ import annotations

import argparse
import hmac
import os
import select
import signal
import socket
import threading
from dataclasses import dataclass
from typing import Final


MAX_HEADER_BYTES: Final = 65_536
MAX_BODY_BYTES: Final = 1_000_000
HEALTH_PATHS: Final = {"/healthz"}
STOPPING = threading.Event()


@dataclass(frozen=True)
class Request:
    payload: bytes
    method: str
    path: str
    authorization: str
    websocket: bool


def response(connection: socket.socket, status: int, reason: str, body: bytes) -> None:
    headers = (
        f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
        + b"Content-Type: application/json\r\n"
        + b"Connection: close\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    )
    connection.sendall(headers + body)


def unauthorized(connection: socket.socket) -> None:
    response(connection, 401, "Unauthorized", b'{"ok":false,"error":"unauthorized"}\n')


def receive_request(connection: socket.socket) -> Request:
    payload = bytearray()
    while b"\r\n\r\n" not in payload:
        chunk = connection.recv(8192)
        if not chunk:
            raise ValueError("client closed before request headers")
        payload.extend(chunk)
        if len(payload) > MAX_HEADER_BYTES:
            raise ValueError("request headers are too large")

    raw_headers, buffered_body = bytes(payload).split(b"\r\n\r\n", 1)
    lines = raw_headers.split(b"\r\n")
    request_parts = lines[0].decode("latin-1", errors="replace").split()
    if len(request_parts) < 3:
        raise ValueError("invalid request line")
    method = request_parts[0].upper()
    path = request_parts[1].split("?", 1)[0]
    authorization = ""
    content_length = 0
    websocket = False
    rewritten = [lines[0]]

    for raw in lines[1:]:
        if b":" not in raw:
            continue
        name, value = raw.split(b":", 1)
        normalized = name.strip().lower()
        clean_value = value.strip()
        if normalized == b"authorization":
            authorization = clean_value.decode("latin-1", errors="replace")
        elif normalized == b"content-length":
            content_length = int(clean_value.decode("ascii"))
        elif normalized == b"transfer-encoding" and clean_value.lower() != b"identity":
            raise ValueError("chunked request bodies are not accepted")
        elif normalized == b"upgrade" and clean_value.lower() == b"websocket":
            websocket = True
        if normalized not in {b"connection", b"proxy-connection"}:
            rewritten.append(raw)

    if content_length < 0 or content_length > MAX_BODY_BYTES:
        raise ValueError("invalid request body size")
    body = bytearray(buffered_body[:content_length])
    while len(body) < content_length:
        chunk = connection.recv(min(8192, content_length - len(body)))
        if not chunk:
            raise ValueError("client closed before request body")
        body.extend(chunk)

    connection_header = b"Connection: Upgrade" if websocket else b"Connection: close"
    rewritten.append(connection_header)
    request_payload = b"\r\n".join(rewritten) + b"\r\n\r\n" + bytes(body)
    return Request(
        payload=request_payload,
        method=method,
        path=path,
        authorization=authorization,
        websocket=websocket,
    )


def relay_websocket(client: socket.socket, upstream: socket.socket) -> None:
    sockets = (client, upstream)
    while not STOPPING.is_set():
        readable, _, _ = select.select(sockets, (), (), 1)
        if not readable:
            continue
        for source in readable:
            data = source.recv(65_536)
            if not data:
                return
            target = upstream if source is client else client
            target.sendall(data)


def relay_single_response(client: socket.socket, upstream: socket.socket) -> None:
    while True:
        data = upstream.recv(65_536)
        if not data:
            return
        client.sendall(data)


def handle_connection(
    client: socket.socket,
    upstream_host: str,
    upstream_port: int,
    token: str,
) -> None:
    with client:
        client.settimeout(10)
        try:
            request = receive_request(client)
            anonymous_health = request.method == "GET" and request.path in HEALTH_PATHS
            expected = f"Bearer {token}"
            if not anonymous_health and not hmac.compare_digest(request.authorization, expected):
                unauthorized(client)
                try:
                    client.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return

            with socket.create_connection((upstream_host, upstream_port), timeout=5) as upstream:
                upstream.sendall(request.payload)
                client.settimeout(None)
                upstream.settimeout(30)
                if request.websocket:
                    upstream.settimeout(None)
                    relay_websocket(client, upstream)
                else:
                    relay_single_response(client, upstream)
        except (OSError, ValueError):
            return


def socket_from_systemd() -> socket.socket:
    listen_pid = int(os.getenv("LISTEN_PID", "0") or "0")
    listen_fds = int(os.getenv("LISTEN_FDS", "0") or "0")
    if listen_pid != os.getpid() or listen_fds != 1:
        raise SystemExit("exactly one systemd socket is required")
    return socket.fromfd(3, socket.AF_INET, socket.SOCK_STREAM)


def parse_listen(value: str) -> tuple[str, int]:
    host, separator, raw_port = value.rpartition(":")
    if not separator:
        raise SystemExit("--listen must be HOST:PORT")
    port = int(raw_port)
    if not 1024 <= port <= 65535:
        raise SystemExit("listen port must be between 1024 and 65535")
    return host, port


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    listener = parser.add_mutually_exclusive_group(required=True)
    listener.add_argument("--systemd-socket", action="store_true")
    listener.add_argument("--listen")
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    token = os.getenv("CERBERUS_HTTP_TOKEN", "").strip()
    if not token:
        raise SystemExit("CERBERUS_HTTP_TOKEN is required")

    if args.systemd_socket:
        listener = socket_from_systemd()
    else:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(parse_listen(args.listen))
        listener.listen(128)

    def stop(_signum: int, _frame: object) -> None:
        STOPPING.set()
        listener.close()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    with listener:
        while not STOPPING.is_set():
            try:
                client, _address = listener.accept()
            except OSError:
                if STOPPING.is_set():
                    break
                raise
            thread = threading.Thread(
                target=handle_connection,
                args=(client, args.upstream_host, args.upstream_port, token),
                daemon=True,
            )
            thread.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
