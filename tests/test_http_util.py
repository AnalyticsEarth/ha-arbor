"""Tests for the dependency-free HTTP helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _loader import const, http_util  # noqa: E402


class TestNormaliseBaseUrl(unittest.TestCase):
    """School URLs arrive from Arbor in several shapes."""

    def test_adds_a_scheme_and_drops_the_path(self) -> None:
        for raw in (
            "princesrisborough.uk.arbor.sc",
            "https://princesrisborough.uk.arbor.sc",
            "https://princesrisborough.uk.arbor.sc/",
            "  https://princesrisborough.uk.arbor.sc/?/home-ui/index  ",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(
                    http_util.normalise_base_url(raw),
                    "https://princesrisborough.uk.arbor.sc",
                )

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            http_util.normalise_base_url("   ")


class TestLooksLikeHtml(unittest.TestCase):
    """Arbor serves the app shell with a 200 when the session has gone."""

    def test_detects_the_shell(self) -> None:
        self.assertTrue(http_util.looks_like_html("<!DOCTYPE html>\n<html lang=\"en\">"))
        self.assertTrue(http_util.looks_like_html("\n  <html>"))

    def test_json_is_not_html(self) -> None:
        self.assertFalse(http_util.looks_like_html('{"success": true}'))
        self.assertFalse(http_util.looks_like_html("[]"))
        self.assertFalse(http_util.looks_like_html(""))


class TestStripJsonPrefix(unittest.TestCase):
    """Anti-hijacking prefixes have to come off before json.loads."""

    def test_strips_known_prefixes(self) -> None:
        for prefix in http_util.JSON_HIJACK_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    http_util.strip_json_prefix(f'{prefix}{{"a": 1}}'), '{"a": 1}'
                )

    def test_leaves_plain_json_alone(self) -> None:
        self.assertEqual(http_util.strip_json_prefix('  {"a": 1}'), '{"a": 1}')


class TestBuildPageUrl(unittest.TestCase):
    """Portal routes live in the query string, not the path."""

    BASE = "https://school.uk.arbor.sc"

    def test_builds_the_route_query(self) -> None:
        self.assertEqual(
            http_util.build_page_url(
                self.BASE, "/guardians/home-ui/dashboard", const.FORMAT_JAVASCRIPT
            ),
            f"{self.BASE}/?/guardians/home-ui/dashboard&format=javascript",
        )

    def test_accepts_a_route_that_already_has_the_query_prefix(self) -> None:
        self.assertEqual(
            http_util.build_page_url(
                self.BASE, "/?/guardians/home-ui/dashboard", const.FORMAT_JAVASCRIPT
            ),
            f"{self.BASE}/?/guardians/home-ui/dashboard&format=javascript",
        )

    def test_adds_a_leading_slash(self) -> None:
        self.assertEqual(
            http_util.build_page_url(self.BASE, "guardians/x", const.FORMAT_JAVASCRIPT),
            f"{self.BASE}/?/guardians/x&format=javascript",
        )

    def test_does_not_duplicate_the_format_flag(self) -> None:
        self.assertEqual(
            http_util.build_page_url(
                self.BASE, "/guardians/x&format=javascript", const.FORMAT_JAVASCRIPT
            ),
            f"{self.BASE}/?/guardians/x&format=javascript",
        )


if __name__ == "__main__":
    unittest.main()
