import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from pump_console.access import AccessPolicy
from pump_console.repository import ApiError


class AccessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = AccessPolicy({"https_origins": ["https://192.0.2.10"],
                                    "trusted_proxy_addresses": ["127.0.0.1"], "allow_ssh_loopback": True})
        self.headers = {"Host": "192.0.2.10", "X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.19"}

    def test_proxy_requires_exact_origin_tls_local_peer_and_single_client_address(self):
        self.assertEqual(self.policy.resolve(self.headers, "127.0.0.1"), ("https://192.0.2.10", "198.51.100.19"))
        variants = [("Host", "untrusted.example"), ("Host", "192.0.2.10:444"),
                    ("X-Forwarded-Proto", "http"), ("X-Forwarded-For", "198.51.100.1, 203.0.113.2"),
                    ("X-Forwarded-For", ""), ("Host", "user@192.0.2.10"), ("Host", "192.0.2.10/path")]
        for key, value in variants:
            with self.subTest(key=key, value=value), self.assertRaises(ApiError):
                self.policy.resolve({**self.headers, key: value}, "127.0.0.1")
        with self.assertRaises(ApiError):
            self.policy.resolve(self.headers, "198.51.100.19")

    def test_loopback_fallback_never_trusts_forwarding_headers(self):
        self.assertEqual(self.policy.resolve({"Host": "localhost:4310"}, "127.0.0.1"),
                         ("http://localhost:4310", "127.0.0.1"))
        with self.assertRaises(ApiError):
            self.policy.resolve({"Host": "localhost:4310", "X-Forwarded-Proto": "https"}, "127.0.0.1")
        locked = AccessPolicy({"https_origins": ["https://192.0.2.10"], "allow_ssh_loopback": False})
        with self.assertRaises(ApiError):
            locked.resolve({"Host": "localhost:4310"}, "127.0.0.1")

    def test_configuration_cannot_enable_plain_http_or_remote_proxy_trust(self):
        for origin in ("http://192.0.2.10", "https://user@example.test", "https://example.test/path", "https://localhost"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                AccessPolicy({"https_origins": [origin]})
        with self.assertRaises(ValueError):
            AccessPolicy({"trusted_proxy_addresses": ["198.51.100.19"]})


if __name__ == "__main__":
    unittest.main()
