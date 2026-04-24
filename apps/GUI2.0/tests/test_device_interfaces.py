from __future__ import annotations

import socket
import unittest

from ucirplgui.device_interfaces.device_interfaces import _READ_TIMEOUT, _read_exact


class _FakeSocket:
    def __init__(self, *responses: bytes | BaseException) -> None:
        self._responses = list(responses)

    def recv(self, _remaining: int) -> bytes:
        if not self._responses:
            return b""
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class DeviceInterfaceReadTests(unittest.TestCase):
    def test_read_exact_timeout_without_bytes_is_not_disconnect(self) -> None:
        sock = _FakeSocket(socket.timeout())

        self.assertIs(_read_exact(sock, 8), _READ_TIMEOUT)

    def test_read_exact_keeps_partial_packet_across_timeout(self) -> None:
        sock = _FakeSocket(b"ab", socket.timeout(), b"cd")

        self.assertEqual(_read_exact(sock, 4), b"abcd")


if __name__ == "__main__":
    unittest.main()
