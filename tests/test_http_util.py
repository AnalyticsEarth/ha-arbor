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


class TestStripRoutePrefix(unittest.TestCase):
    """Address-bar routes reduce to a plain path; real queries survive."""

    def test_strips_the_spa_route_marker(self) -> None:
        self.assertEqual(
            http_util.strip_route_prefix("https://s.uk.arbor.sc/?/guardians/x"),
            "/guardians/x",
        )
        self.assertEqual(http_util.strip_route_prefix("/?/guardians/x"), "/guardians/x")

    def test_leaves_a_plain_path_alone(self) -> None:
        self.assertEqual(http_util.strip_route_prefix("/guardians/x"), "/guardians/x")

    def test_keeps_a_genuine_query_string(self) -> None:
        self.assertEqual(
            http_util.strip_route_prefix("/guardians/x?page=2"), "/guardians/x?page=2"
        )


class TestBuildPageUrl(unittest.TestCase):
    """Portal pages are fetched as paths, not as a `/?/route` query.

    Requesting the address-bar form returns Arbor's HTML application shell
    instead of data, so this is the difference between working and not.
    """

    BASE = "https://school.uk.arbor.sc"

    def test_uses_the_path_form(self) -> None:
        self.assertEqual(
            http_util.build_page_url(
                self.BASE, "/guardians/home-ui/dashboard", const.FORMAT_JAVASCRIPT
            ),
            f"{self.BASE}/guardians/home-ui/dashboard?format=javascript",
        )

    def test_never_emits_the_shell_returning_form(self) -> None:
        for path in (
            "/guardians/home-ui/dashboard",
            "/?/guardians/home-ui/dashboard",
            "https://school.uk.arbor.sc/?/guardians/home-ui/dashboard",
        ):
            with self.subTest(path=path):
                url = http_util.build_page_url(self.BASE, path, const.FORMAT_JAVASCRIPT)
                self.assertNotIn("/?/", url)
                self.assertEqual(
                    url, f"{self.BASE}/guardians/home-ui/dashboard?format=javascript"
                )

    def test_keeps_an_id_bearing_route_intact(self) -> None:
        self.assertEqual(
            http_util.build_page_url(
                self.BASE,
                "/guardians/attendance/index/student-id/40219",
                const.FORMAT_JAVASCRIPT,
            ),
            f"{self.BASE}/guardians/attendance/index/student-id/40219?format=javascript",
        )

    def test_appends_to_an_existing_query_string(self) -> None:
        self.assertEqual(
            http_util.build_page_url(self.BASE, "/guardians/x?page=2", const.FORMAT_JAVASCRIPT),
            f"{self.BASE}/guardians/x?page=2&format=javascript",
        )

    def test_adds_a_leading_slash(self) -> None:
        self.assertEqual(
            http_util.build_page_url(self.BASE, "guardians/x", const.FORMAT_JAVASCRIPT),
            f"{self.BASE}/guardians/x?format=javascript",
        )

    def test_does_not_duplicate_the_format_flag(self) -> None:
        self.assertEqual(
            http_util.build_page_url(
                self.BASE, "/guardians/x?format=javascript", const.FORMAT_JAVASCRIPT
            ),
            f"{self.BASE}/guardians/x?format=javascript",
        )


if __name__ == "__main__":
    unittest.main()
