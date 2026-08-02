"""MySqlConnector's TCP keepalive tuning on connect.

Real incident: a single-column COUNT(DISTINCT) on ws_stock_opnames --
already the smallest possible query after column/distinct batching -- still
hit MySQL error 2013 ("Lost connection... during query"). The query ran long
enough server-side that zero bytes crossed the wire for minutes; a stateful
firewall/NAT on the VPN path (Kemkes gateway) silently dropped the idle
connection. pymysql already sets bare SO_KEEPALIVE, but the OS default idle
time before the first probe (~2h on Linux) never fires in time. Tuning
TCP_KEEPIDLE/INTVL/CNT to short values makes the OS probe within seconds,
keeping the NAT table entry alive for the query's duration.
"""
from __future__ import annotations

import socket

import pytest

from validation_core.connectors.mysql import _tune_tcp_keepalive


class _FakeSocket:
    def __init__(self, raise_on_setsockopt: bool = False):
        self.calls: list[tuple] = []
        self._raise = raise_on_setsockopt

    def setsockopt(self, *args):
        if self._raise:
            raise OSError("setsockopt not supported")
        self.calls.append(args)


class _FakeDbapiConn:
    def __init__(self, sock):
        self._sock = sock


class TestTuneTcpKeepalive:
    def test_enables_keepalive_and_tunes_available_options(self):
        sock = _FakeSocket()
        _tune_tcp_keepalive(_FakeDbapiConn(sock))

        assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in sock.calls
        # Only assert on options this platform actually exposes -- e.g. macOS
        # lacks TCP_KEEPIDLE/INTVL/CNT, so the function must skip them, not
        # crash trying to reference a nonexistent socket constant.
        if hasattr(socket, "TCP_KEEPIDLE"):
            assert (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15) in sock.calls
        if hasattr(socket, "TCP_KEEPINTVL"):
            assert (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10) in sock.calls
        if hasattr(socket, "TCP_KEEPCNT"):
            assert (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5) in sock.calls

    def test_missing_socket_attribute_is_a_silent_noop(self):
        class _NoSockConn:
            pass

        _tune_tcp_keepalive(_NoSockConn())  # must not raise

    def test_setsockopt_failure_is_swallowed(self):
        """Some platforms/socket types reject these options entirely -- losing
        the tuning must never take down the whole connection attempt."""
        sock = _FakeSocket(raise_on_setsockopt=True)
        _tune_tcp_keepalive(_FakeDbapiConn(sock))  # must not raise
