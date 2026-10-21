"""Exact origins and loopback-only trust for the HTTPS reverse proxy."""
import ipaddress
from urllib.parse import urlsplit

from .repository import ApiError

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class AccessPolicy:
    def __init__(self, config=None):
        config = config or {}
        if not isinstance(config, dict) or set(config) - {"https_origins", "trusted_proxy_addresses", "allow_ssh_loopback"}:
            raise ValueError("Invalid access configuration")
        self.allow_loopback = config.get("allow_ssh_loopback", True)
        if type(self.allow_loopback) is not bool:
            raise ValueError("Invalid loopback access flag")
        self.origins = {}
        origins = config.get("https_origins", [])
        proxies = config.get("trusted_proxy_addresses", ["127.0.0.1"])
        if not isinstance(origins, list) or not isinstance(proxies, list):
            raise ValueError("Origins and proxy addresses must be lists")
        for value in origins:
            if not isinstance(value, str):
                raise ValueError("Invalid HTTPS origin")
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname in LOOPBACK_HOSTS or
                    parsed.username is not None or parsed.password is not None or parsed.path or
                    parsed.query or parsed.fragment or parsed.netloc.lower() != parsed.netloc or
                    (parsed.port is not None and not 1 <= parsed.port <= 65535)):
                raise ValueError("An exact non-loopback HTTPS origin is required")
            self.origins[parsed.netloc] = value
        self.proxies = set()
        for value in proxies:
            address = ipaddress.ip_address(value)
            if not address.is_loopback:
                raise ValueError("Only a local reverse proxy can be trusted")
            self.proxies.add(str(address))

    def resolve(self, headers, peer):
        authority = headers.get("Host", "")
        try:
            parsed = urlsplit("//" + authority)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None or
                    parsed.path or parsed.query or parsed.fragment or
                    (parsed.port is not None and not 1 <= parsed.port <= 65535)):
                raise ValueError("Invalid host")
        except ValueError:
            raise ApiError(403, "Unrecognized service address", "invalid_host")
        if parsed.hostname in LOOPBACK_HOSTS and self.allow_loopback:
            if headers.get("X-Forwarded-Proto") or headers.get("X-Forwarded-For"):
                raise ApiError(403, "Unexpected forwarding headers", "invalid_proxy")
            return "http://" + authority, peer
        origin = self.origins.get(authority)
        if not origin:
            raise ApiError(403, "Unrecognized service address", "invalid_host")
        if peer not in self.proxies or headers.get("X-Forwarded-Proto") != "https":
            raise ApiError(403, "An authenticated HTTPS ingress is required", "invalid_proxy")
        try:
            client = str(ipaddress.ip_address(headers.get("X-Forwarded-For", "")))
        except ValueError:
            raise ApiError(403, "Invalid client address", "invalid_proxy")
        return origin, client
