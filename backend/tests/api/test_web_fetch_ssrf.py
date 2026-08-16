"""SSRF guard unit tests for the web_fetch URL validation."""

import pytest

from app.agent.tools.web_fetch import (
    _is_blocked_address,
    _resolve_and_check,
    _validate_url,
    _validate_url_async,
)


class TestBlockedAddresses:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.0.0.0",
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.1",
            "192.168.0.0",
            "169.254.169.254",
            "169.254.1.1",
            "0.0.0.0",
            "::1",
            "::",
            "fc00::1",
            "fd12:3456:789a::1",
            "fe80::1",
            "127.0.0.2",
        ],
    )
    def test_private_loopback_linklocal_blocked(self, ip):
        assert _is_blocked_address(ip) is True

    @pytest.mark.parametrize(
        "ip",
        ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"],
    )
    def test_public_addresses_allowed(self, ip):
        assert _is_blocked_address(ip) is False

    def test_ipv4_mapped_ipv6_blocked(self):
        assert _is_blocked_address("::ffff:127.0.0.1") is True
        assert _is_blocked_address("::ffff:10.0.0.5") is True
        assert _is_blocked_address("::ffff:8.8.8.8") is False


class TestUrlValidation:
    async def test_private_ip_literal_rejected(self):
        for url in [
            "http://127.0.0.1:8000/admin",
            "https://10.0.0.1/",
            "https://172.16.0.1/x",
            "https://192.168.1.1/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://[::1]/",
            "https://[fc00::1]/",
        ]:
            error, _ = await _validate_url_async(url)
            assert error is not None, url

    async def test_public_ip_literal_allowed(self):
        for url in ["https://8.8.8.8/", "http://1.1.1.1/"]:
            error, _ = await _validate_url_async(url)
            assert error is None, url

    async def test_bad_scheme_rejected(self):
        error, _ = await _validate_url_async("ftp://8.8.8.8/file")
        assert error is not None
        error, _ = await _validate_url_async("file:///etc/passwd")
        assert error is not None

    async def test_credentials_rejected(self):
        error, _ = await _validate_url_async("https://user:pass@8.8.8.8/")
        assert error is not None

    def test_syntax_only_validation(self):
        assert _validate_url("https://example.com/") is None
        assert _validate_url("http://192.168.1.1/x") is None  # syntax layer only
        assert _validate_url("ftp://example.com") is not None
        assert _validate_url("not a url") is not None

    async def test_hostname_with_bracket_ipv6(self):
        error, _ = await _resolve_and_check("[::1]")
        assert error is not None
        error, _ = await _resolve_and_check("[2606:4700:4700::1111]")
        assert error is None
