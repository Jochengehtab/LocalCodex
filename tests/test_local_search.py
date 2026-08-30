import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from local_search.core import SearchClient, WebAccessError, validate_public_url


PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
PRIVATE_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]


class LocalSearchTests(unittest.TestCase):
    def test_private_destinations_are_blocked(self):
        with patch("local_search.core.socket.getaddrinfo", return_value=PRIVATE_DNS):
            with self.assertRaises(WebAccessError):
                validate_public_url("http://localhost/secret")

    def test_non_web_schemes_and_custom_ports_are_blocked(self):
        with self.assertRaises(WebAccessError):
            validate_public_url("file:///etc/passwd")
        with self.assertRaises(WebAccessError):
            validate_public_url("https://example.com:8443/")

    def test_search_results_are_bounded_deduplicated_and_cached(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "One", "url": "https://example.com/a#x", "content": " first ", "engine": "test"},
                        {"title": "Duplicate", "url": "https://example.com/a#y", "content": "same", "engine": "test"},
                        {"title": "Unsafe", "url": "file:///tmp/x", "content": "bad", "engine": "test"},
                    ],
                    "unresponsive_engines": [],
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            client = SearchClient("http://127.0.0.1:18082", Path(directory) / "cache.sqlite3")
            client.client.close()
            client.client = httpx.Client(transport=httpx.MockTransport(handler))
            first = client.web_search("current test")
            second = client.web_search("current test")
            client.close()
        self.assertEqual(1, calls)
        self.assertEqual(1, len(first["results"]))
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertIn("UNTRUSTED", first["warning"])

    def test_html_fetch_extracts_text_and_revalidates_url(self):
        def handler(request):
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"<html><head><title>Example</title></head><body><article><p>Useful current information here.</p></article></body></html>",
            )

        with tempfile.TemporaryDirectory() as directory, patch(
            "local_search.core.socket.getaddrinfo", return_value=PUBLIC_DNS
        ):
            client = SearchClient("http://127.0.0.1:18082", Path(directory) / "cache.sqlite3")
            client.client.close()
            client.client = httpx.Client(transport=httpx.MockTransport(handler))
            client._pace = lambda _: None
            result = client.fetch_page("https://example.com/article")
            client.close()
        self.assertEqual("Example", result["title"])
        self.assertIn("Useful current information", result["text"])
        self.assertIn("UNTRUSTED", result["warning"])


if __name__ == "__main__":
    unittest.main()
