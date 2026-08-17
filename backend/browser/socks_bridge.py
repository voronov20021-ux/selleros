"""
socks_bridge.py — локальный HTTP CONNECT → SOCKS5 upstream.

Chromium не умеет authenticated SOCKS5. Bridge слушает localhost HTTP
и пробрасывает трафик через SOCKS5 (Proxy6net / SOCKS_UPSTREAM_*).

WBEngine на bridge НЕ переключается — только BrowserProvider.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import quote, urlsplit

log = logging.getLogger("selleros.browser.socks_bridge")

_BUF = 65536


def _redact_upstream(host: str, port: int, user: str | None) -> str:
    auth = f"{user}:***@" if user else ""
    return f"socks5://{auth}{host}:{port}"


def _parse_host_port(authority: str, default_port: int) -> tuple[str, int]:
    authority = authority.strip()
    if authority.startswith("["):
        # [ipv6]:port
        end = authority.find("]")
        if end < 0:
            raise ValueError("bad ipv6 authority")
        host = authority[1:end]
        rest = authority[end + 1 :]
        if rest.startswith(":"):
            return host, int(rest[1:])
        return host, default_port
    if authority.count(":") == 1:
        host, port_s = authority.rsplit(":", 1)
        return host, int(port_s)
    return authority, default_port


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await src.read(_BUF)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


class SocksHttpBridge:
    """Async HTTP CONNECT (и plain HTTP) proxy → SOCKS5 upstream."""

    def __init__(
        self,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8080,
        upstream_host: str,
        upstream_port: int,
        upstream_user: str = "",
        upstream_password: str = "",
        connect_timeout: float = 30.0,
    ):
        self.listen_host = (listen_host or "127.0.0.1").strip()
        self.listen_port = int(listen_port)
        self.upstream_host = (upstream_host or "").strip()
        self.upstream_port = int(upstream_port)
        self.upstream_user = upstream_user or ""
        self.upstream_password = upstream_password or ""
        self.connect_timeout = float(connect_timeout)
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    @classmethod
    def from_config(cls) -> "SocksHttpBridge":
        from backend import config as cfg

        return cls(
            listen_host=cfg.SOCKS_BRIDGE_HOST,
            listen_port=cfg.SOCKS_BRIDGE_PORT,
            upstream_host=cfg.SOCKS_UPSTREAM_HOST,
            upstream_port=cfg.SOCKS_UPSTREAM_PORT,
            upstream_user=cfg.SOCKS_UPSTREAM_USER,
            upstream_password=cfg.SOCKS_UPSTREAM_PASSWORD,
        )

    @property
    def http_url(self) -> str:
        return f"http://{self.listen_host}:{self.listen_port}"

    @property
    def upstream_redacted(self) -> str:
        return _redact_upstream(
            self.upstream_host, self.upstream_port, self.upstream_user or None
        )

    def _upstream_proxy_url(self) -> str:
        user = quote(self.upstream_user or "", safe="")
        pwd = quote(self.upstream_password or "", safe="")
        auth = f"{user}:{pwd}@" if (self.upstream_user or self.upstream_password) else ""
        return f"socks5://{auth}{self.upstream_host}:{self.upstream_port}"

    async def start(self) -> None:
        if self._server is not None:
            return
        if not self.upstream_host:
            raise ValueError("SOCKS_UPSTREAM_HOST is empty")
        self._server = await asyncio.start_server(
            self._accept,
            self.listen_host,
            self.listen_port,
        )
        socks = self._server.sockets or []
        bound = socks[0].getsockname() if socks else (self.listen_host, self.listen_port)
        log.info(
            "SOCKS bridge listening on http://%s:%s → %s",
            bound[0],
            bound[1],
            self.upstream_redacted,
        )

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("SOCKS bridge stopped")

    @property
    def running(self) -> bool:
        return self._server is not None

    async def __aenter__(self) -> "SocksHttpBridge":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    def _accept(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.create_task(self._handle_client(client_reader, client_writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _open_upstream(
        self, dest_host: str, dest_port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        from python_socks.async_.asyncio import Proxy

        proxy = Proxy.from_url(self._upstream_proxy_url())
        sock = await asyncio.wait_for(
            proxy.connect(dest_host=dest_host, dest_port=dest_port),
            timeout=self.connect_timeout,
        )
        return await asyncio.open_connection(host=None, port=None, sock=sock)

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        peer = "?"
        try:
            peer_info = client_writer.get_extra_info("peername")
            if peer_info:
                peer = f"{peer_info[0]}:{peer_info[1]}"
            first = await asyncio.wait_for(client_reader.readline(), timeout=30.0)
            if not first:
                return
            line = first.decode("latin-1", errors="replace").strip()
            parts = line.split()
            if len(parts) < 2:
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return

            method = parts[0].upper()
            target = parts[1]

            # Consume request headers (until blank line).
            headers: list[bytes] = []
            while True:
                h = await asyncio.wait_for(client_reader.readline(), timeout=30.0)
                if not h or h in (b"\r\n", b"\n"):
                    break
                headers.append(h)

            if method == "CONNECT":
                await self._handle_connect(client_reader, client_writer, target, peer)
            else:
                await self._handle_http(
                    client_reader, client_writer, method, target, headers, peer
                )
        except Exception as exc:
            log.debug("SOCKS bridge client %s error: %s", peer, type(exc).__name__)
            try:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
            except Exception:
                pass
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass

    async def _handle_connect(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        target: str,
        peer: str,
    ) -> None:
        host, port = _parse_host_port(target, 443)
        try:
            up_reader, up_writer = await self._open_upstream(host, port)
        except Exception as exc:
            log.warning(
                "CONNECT %s failed via %s: %s",
                target,
                self.upstream_redacted,
                type(exc).__name__,
            )
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            return

        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        log.debug("CONNECT tunnel %s → %s (peer=%s)", target, self.upstream_redacted, peer)

        t1 = asyncio.create_task(_pipe(client_reader, up_writer))
        t2 = asyncio.create_task(_pipe(up_reader, client_writer))
        done, pending = await asyncio.wait(
            {t1, t2}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)
        try:
            up_writer.close()
            await up_writer.wait_closed()
        except Exception:
            pass

    async def _handle_http(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: list[bytes],
        peer: str,
    ) -> None:
        # Absolute-form: GET http://host/path HTTP/1.1
        if "://" in target:
            parsed = urlsplit(target)
            host = parsed.hostname
            if not host:
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
        else:
            # origin-form + Host header
            path = target
            host_hdr = None
            for h in headers:
                if h.lower().startswith(b"host:"):
                    host_hdr = h.split(b":", 1)[1].strip().decode("latin-1")
                    break
            if not host_hdr:
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return
            host, port = _parse_host_port(host_hdr, 80)

        assert host is not None
        try:
            up_reader, up_writer = await self._open_upstream(host, int(port))
        except Exception as exc:
            log.warning(
                "HTTP %s %s failed via %s: %s",
                method,
                host,
                self.upstream_redacted,
                type(exc).__name__,
            )
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            return

        req_line = f"{method} {path} HTTP/1.1\r\n".encode("latin-1")
        up_writer.write(req_line)
        # Drop Proxy-Connection; keep other headers.
        for h in headers:
            low = h.lower()
            if low.startswith(b"proxy-connection:"):
                continue
            up_writer.write(h)
        up_writer.write(b"\r\n")
        await up_writer.drain()

        # If body follows (Content-Length), pipe remaining client→upstream briefly.
        # For typical GET probes we only need response relay.
        t1 = asyncio.create_task(_pipe(client_reader, up_writer))
        t2 = asyncio.create_task(_pipe(up_reader, client_writer))
        done, pending = await asyncio.wait(
            {t1, t2}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)
        try:
            up_writer.close()
            await up_writer.wait_closed()
        except Exception:
            pass


_GLOBAL_BRIDGE: Optional[SocksHttpBridge] = None


async def start_bridge_from_config() -> SocksHttpBridge | None:
    """Start global bridge if SOCKS_BRIDGE_ENABLED. Returns instance or None."""
    global _GLOBAL_BRIDGE
    from backend import config as cfg

    if not cfg.SOCKS_BRIDGE_ENABLED:
        return None
    if _GLOBAL_BRIDGE is not None and _GLOBAL_BRIDGE.running:
        return _GLOBAL_BRIDGE
    bridge = SocksHttpBridge.from_config()
    await bridge.start()
    _GLOBAL_BRIDGE = bridge
    return bridge


async def stop_bridge() -> None:
    global _GLOBAL_BRIDGE
    if _GLOBAL_BRIDGE is not None:
        await _GLOBAL_BRIDGE.stop()
        _GLOBAL_BRIDGE = None


def get_running_bridge() -> SocksHttpBridge | None:
    if _GLOBAL_BRIDGE is not None and _GLOBAL_BRIDGE.running:
        return _GLOBAL_BRIDGE
    return None
