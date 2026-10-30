from __future__ import annotations

import unittest
from unittest.mock import patch

from smart_launcher.infrastructure.network import lan_url


class NetworkTests(unittest.TestCase):
    def test_lan_url_uses_discovered_address_and_requested_port(self) -> None:
        with patch(
            "smart_launcher.infrastructure.network.discover_lan_ipv4",
            return_value="192.168.1.25",
        ):
            self.assertEqual(lan_url(8288), "http://192.168.1.25:8288")

    def test_lan_url_is_absent_when_no_route_is_available(self) -> None:
        with patch(
            "smart_launcher.infrastructure.network.discover_lan_ipv4",
            return_value=None,
        ):
            self.assertIsNone(lan_url(8188))


if __name__ == "__main__":
    unittest.main()
