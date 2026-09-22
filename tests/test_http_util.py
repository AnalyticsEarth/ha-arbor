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


class TestClassifyResponse(unittest.TestCase):
    """A dead session and a forbidden resource look identical on the wire."""

    SHELL = '<!DOCTYPE html>\n<html lang="en">'
    JSON = '{"success": true, "items": []}'

    def test_a_good_response_is_ok(self) -> None:
        self.assertEqual(
            http_util.classify_response(200, self.JSON, retried=False),
            http_util.RESPONSE_OK,
        )

    def test_first_denial_is_treated_as_a_stale_session(self) -> None:
        for status, body in ((401, ""), (403, ""), (200, self.SHELL)):
            with self.subTest(status=status):
                self.assertEqual(
                    http_util.classify_response(status, body, retried=False),
                    http_util.RESPONSE_SESSION_STALE,
                )

    def test_denial_after_a_fresh_login_is_not_an_auth_problem(self) -> None:
        """The regression: a 403 here used to take the integration down.

        Logging in is what validates credentials, so once it has just succeeded a
        denial can only be about the resource.
        """
        for status, body in ((401, ""), (403, ""), (200, self.SHELL)):
            with self.subTest(status=status):
                self.assertEqual(
                    http_util.classify_response(status, body, retried=True),
                    http_util.RESPONSE_NOT_AVAILABLE,
                )

    def test_missing_pages_are_skipped_not_retried(self) -> None:
        self.assertEqual(
            http_util.classify_response(404, "", retried=False),
            http_util.RESPONSE_NOT_AVAILABLE,
        )

    def test_server_errors_are_worth_retrying(self) -> None:
        for status in (500, 502, 503):
            with self.subTest(status=status):
                self.assertEqual(
                    http_util.classify_response(status, "", retried=False),
                    http_util.RESPONSE_SERVER_ERROR,
                )

    def test_a_429_is_a_server_condition_not_an_auth_failure(self) -> None:
        self.assertEqual(
            http_util.classify_response(429, "", retried=True),
            http_util.RESPONSE_SERVER_ERROR,
        )


class TestRefusalMessage(unittest.TestCase):
    """Arbor refuses a page with HTTP 200 and a JSON body."""

    def test_reads_the_reason(self) -> None:
        self.assertEqual(
            http_util.refusal_message(
                {
                    "success": False,
                    "message": "User is not allowed to access mvc:default/navigation/main-menu",
                }
            ),
            "User is not allowed to access mvc:default/navigation/main-menu",
        )

    def test_handles_a_refusal_with_no_reason(self) -> None:
        self.assertEqual(http_util.refusal_message({"success": False}), "no reason given")

    def test_a_successful_payload_is_not_a_refusal(self) -> None:
        for payload in (
            {"success": True, "items": []},
            {"items": []},
            [],
            None,
            "text",
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(http_util.refusal_message(payload))


class TestLoggedOutJson(unittest.TestCase):
    """A dead session's third disguise: a 200 with well-formed JSON."""

    BODY = '{"items": [{"session_id": "x", "logged_in": false}], "success": true}'

    def test_a_logged_out_body_is_a_stale_session(self) -> None:
        self.assertTrue(http_util.says_logged_out(self.BODY))
        self.assertEqual(
            http_util.classify_response(200, self.BODY, retried=False),
            http_util.RESPONSE_SESSION_STALE,
        )

    def test_after_retrying_it_is_treated_as_unavailable(self) -> None:
        # Consistent with the rest: only the login handshake reports an auth
        # failure, so a second refusal means skip the endpoint, not prompt.
        self.assertEqual(
            http_util.classify_response(200, self.BODY, retried=True),
            http_util.RESPONSE_NOT_AVAILABLE,
        )

    def test_a_logged_in_body_is_fine(self) -> None:
        body = '{"items": [{"logged_in": true, "display_name": "A Guardian"}]}'
        self.assertFalse(http_util.says_logged_out(body))
        self.assertEqual(
            http_util.classify_response(200, body, retried=False), http_util.RESPONSE_OK
        )

    def test_ordinary_data_is_not_mistaken_for_it(self) -> None:
        self.assertFalse(http_util.says_logged_out('{"assignments": [{"due": "x"}]}'))
        self.assertFalse(http_util.says_logged_out("logged_in false"))


if __name__ == "__main__":
    unittest.main()
