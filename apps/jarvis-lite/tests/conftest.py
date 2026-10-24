"""Tests may reach local PostgreSQL, never live email/LLM/payment providers."""
import socket

import pytest


@pytest.fixture(autouse=True)
def local_network_only(monkeypatch):
    original_connect = socket.socket.connect
    original_lookup = socket.getaddrinfo

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("External network is forbidden in Jarvis Lite tests")
        return original_connect(sock, address)

    def lookup(host, *args, **kwargs):
        if host not in {"localhost", "127.0.0.1", "::1", None, b"localhost", b"127.0.0.1", b"::1"}:
            raise AssertionError("External DNS is forbidden in Jarvis Lite tests")
        return original_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
