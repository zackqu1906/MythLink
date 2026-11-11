import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mythring.__main__ import main, run


class WindowsCliEncodingTests(unittest.TestCase):
    def test_redirected_help_uses_utf8_and_preserves_explicit_encoding(self):
        for override, encoding in (("", "cp1252"), ("gb18030", "gb18030")):
            with self.subTest(override=override), patch.object(sys, "platform", "win32"), \
                    patch.dict(os.environ, {"PYTHONIOENCODING": override}), \
                    patch.object(sys, "argv", ["mythring", "--help"]):
                with io.TextIOWrapper(io.BytesIO(), encoding=encoding) as output, \
                        io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="backslashreplace") as errors:
                    with patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors), \
                            self.assertRaises(SystemExit) as exit_status:
                        main()
                    self.assertEqual(exit_status.exception.code, 0)
                    expected = override or "utf-8"
                    self.assertEqual(errors.encoding, expected)
                    self.assertEqual(errors.errors, "backslashreplace")
                    output.flush()
                    self.assertIn("扫描", output.buffer.getvalue().decode(expected))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_distinguishes_system_connection_without_inventing_rssi(self):
        devices = [SimpleNamespace(address="one", name="Ringo", rssi=None, source="system_connected"),
                   SimpleNamespace(address="two", name="Ringo", rssi=-52, source="advertisement")]
        output = io.StringIO()
        with patch("mythring.__main__.scan", AsyncMock(return_value=devices)), redirect_stdout(output):
            await run(SimpleNamespace(command="scan", timeout=.1))
        self.assertIn("one\tRingo\t—\t系统已连接", output.getvalue())
        self.assertIn("two\tRingo\t-52 dBm\t广播", output.getvalue())

    async def test_info_uses_known_identifier_without_requiring_new_advertisement(self):
        ring = SimpleNamespace(address="known-uuid", mtu_size=185,
                               device_info=SimpleNamespace(firmware_version="2.1.6"),
                               get_battery_level=AsyncMock(return_value=84))
        with patch("mythring.__main__.Ring") as factory, patch("mythring.__main__.scan", AsyncMock()) as discover:
            factory.return_value.__aenter__ = AsyncMock(return_value=ring)
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with redirect_stdout(io.StringIO()):
                await run(SimpleNamespace(command="info", address="known-uuid", timeout=.1))
            factory.assert_called_once_with("known-uuid")
            discover.assert_not_called()
            ring.get_battery_level.assert_awaited_once()
            factory.return_value.__aexit__.assert_awaited_once()
