"""Guard tests for tools/arbor_probe.py.

The script imports the integration's modules by path, so a rename inside the
integration would break it silently. Importing it here turns that into a test
failure instead of a surprise the next time someone tries to debug an account.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "arbor_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("arbor_probe_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestProbeToolLoads(unittest.TestCase):
    """Importing the script resolves every name it borrows."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def test_it_imports(self) -> None:
        self.assertTrue(hasattr(self.probe, "UrllibArborClient"))
        self.assertTrue(hasattr(self.probe, "ArborScraper"))

    def test_login_command_is_available(self) -> None:
        # The one that shows why credentials a browser accepts are refused here.
        self.assertIn("login", self.probe.COMMANDS)

    def test_a_login_response_keeps_its_reason_but_loses_its_secrets(self) -> None:
        redacted = self.probe.protocol.redact_login_response(
            {
                "items": [
                    {
                        "session_id": "abc123",
                        "jwt": "ey.secret",
                        "logged_in": False,
                        "login_form_message": "Your account has been locked.",
                    }
                ]
            }
        )
        item = redacted["items"][0]
        self.assertEqual(item["session_id"], "<redacted>")
        self.assertEqual(item["jwt"], "<redacted>")
        self.assertEqual(item["login_form_message"], "Your account has been locked.")

    def test_the_school_search_dump_omits_school_names(self) -> None:
        trimmed = self.probe._without_school_names(
            {"payload": [{"name": "A School", "postalCode": "X1 1XX", "sisUrl": "a.example"}]}
        )
        self.assertEqual(trimmed["payload"], [{"sisUrl": "a.example"}])

    def test_shapes_command_is_available(self) -> None:
        # One command to dump every scraped page, rather than many `shape` runs.
        self.assertIn("shapes", self.probe.COMMANDS)

    def test_every_command_exists(self) -> None:
        for name, handler in self.probe.COMMANDS.items():
            with self.subTest(command=name):
                self.assertTrue(callable(handler))

    def test_it_reuses_the_integrations_own_protocol(self) -> None:
        # Not a reimplementation: the same request builders must be in play.
        request = self.probe.protocol.login_request("https://s.example", "a@b.c", "pw")
        self.assertIn("/auth/login", request.url)

    def test_it_reuses_the_integrations_own_scraper(self) -> None:
        self.assertEqual(self.probe.ArborScraper.__name__, "ArborScraper")


class TestProbeToolIsPrivateByDefault(unittest.TestCase):
    """Output must be shareable unless the user opts out."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def test_depth_is_deep_enough_for_a_real_guardian_page(self) -> None:
        # Arbor nests page > column > section > subsection > row > field, and a
        # shallow dump truncates exactly where the values are.
        args = self.probe.build_parser().parse_args(["shapes", "--email", "a@b.c"])
        self.assertGreaterEqual(args.depth, 14)

    def test_show_values_defaults_to_off(self) -> None:
        args = self.probe.build_parser().parse_args(["report", "--email", "a@b.c"])
        self.assertFalse(args.show_values)

    def test_redaction_removes_content_by_default(self) -> None:
        payload = {"studentName": "Amelia Example", "count": 3}
        redacted = self.probe.redact(payload, False)
        self.assertNotIn("Amelia Example", str(redacted))
        self.assertEqual(redacted["studentName"], "str[14]")

    def test_values_are_kept_when_asked(self) -> None:
        payload = {"studentName": "Amelia Example"}
        self.assertEqual(self.probe.redact(payload, True), payload)

    def test_names_are_masked_in_the_report(self) -> None:
        self.assertEqual(self.probe._mask_name("Amelia Example", False), "A… E…")
        self.assertEqual(self.probe._mask_name("Amelia Example", True), "Amelia Example")

    def test_the_two_refusals_give_different_advice(self) -> None:
        """A wrong password and a rate limit need opposite advice.

        Checked against the source rather than by running main(), which would
        need a live login.
        """
        source = Path(self.probe.__file__ or "").read_text()
        # Wrong password: check it, and check it is for the right school.
        self.assertIn("separate account even under one email", source)
        # Rate limit: wait, do not touch the password.
        self.assertIn("not a password problem", source)
        self.assertIn("is_rate_limited", source)

    def test_the_keychain_is_only_ever_read(self) -> None:
        """The user stores the password; the script must never write it."""
        source = Path(self.probe.__file__ or "").read_text()
        self.assertIn("find-generic-password", source)
        self.assertNotIn("add-generic-password\",", source)
        self.assertNotIn('"add-generic-password"', source)

    def test_the_store_command_is_shown_not_run(self) -> None:
        command = self.probe.keychain_store_command("a@b.c", "arbor-probe")
        self.assertEqual(
            command, "security add-generic-password -a a@b.c -s arbor-probe -w"
        )
        # -w with no value makes `security` prompt without echo.
        self.assertTrue(command.endswith(" -w"))

    def test_a_missing_keychain_entry_is_not_fatal(self) -> None:
        self.assertIsNone(
            self.probe.keychain_password(
                "definitely-not-a-real-account@example.invalid", "arbor-probe-absent"
            )
        )

    def test_no_password_and_no_terminal_explains_both_options(self) -> None:
        import contextlib
        import io
        import os

        previous = os.environ.pop("ARBOR_PASSWORD", None)
        captured = io.StringIO()
        try:
            with contextlib.redirect_stderr(captured):
                code = self.probe.main(
                    ["report", "--email", "nobody@example.invalid",
                     "--keychain-service", "arbor-probe-absent"]
                )
        finally:
            if previous is not None:
                os.environ["ARBOR_PASSWORD"] = previous
        self.assertEqual(code, 2)
        message = captured.getvalue()
        self.assertIn("add-generic-password", message)
        self.assertIn("read -rs ARBOR_PASSWORD", message)

    def test_there_is_no_password_argument(self) -> None:
        # A --password flag would be recorded in the user's shell history.
        actions = {
            option
            for action in self.probe.build_parser()._actions
            for option in action.option_strings
        }
        self.assertNotIn("--password", actions)
        self.assertNotIn("-p", actions)


class TestProbeToolSessionChecks(unittest.TestCase):
    """The session cookie must belong to the school, not the login service."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def _client(self, cookie_domain: str | None):
        import http.cookiejar

        client = self.probe.UrllibArborClient(
            "a@b.c", "pw", "https://school.uk.arbor.education"
        )
        if cookie_domain is not None:
            client._jar.set_cookie(
                http.cookiejar.Cookie(
                    0, "mis", "x", None, False, cookie_domain, True, False,
                    "/", True, True, None, False, None, None, {},
                )
            )
        return client

    def test_a_tenant_cookie_counts(self) -> None:
        self.assertTrue(self._client("school.uk.arbor.education")._has_session_cookie())

    def test_a_login_service_cookie_does_not(self) -> None:
        # Would otherwise report a good session the school never granted.
        self.assertFalse(self._client("login.arbor.sc")._has_session_cookie())

    def test_no_cookie_at_all_does_not(self) -> None:
        self.assertFalse(self._client(None)._has_session_cookie())

    def test_reads_the_logged_in_flag(self) -> None:
        self.assertIs(
            self.probe._logged_in_flag({"items": [{"logged_in": True}]}), True
        )
        self.assertIs(
            self.probe._logged_in_flag({"items": [{"logged_in": False}]}), False
        )
        self.assertIsNone(self.probe._logged_in_flag({"items": [{}]}))
        self.assertIsNone(self.probe._logged_in_flag("nonsense"))

    def test_summaries_carry_no_content(self) -> None:
        summary = self.probe._summarise({"studentName": "Amelia Example", "x": 1})
        self.assertNotIn("Amelia", summary)
        self.assertIn("studentName", summary)


class TestSessionReuse(unittest.TestCase):
    """Logging in on every invocation is what trips Arbor's login limit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def setUp(self) -> None:
        import os
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self._previous = os.environ.get("ARBOR_PROBE_CONFIG")
        os.environ["ARBOR_PROBE_CONFIG"] = str(Path(self._dir.name) / "schools.json")
        self.probe.remember_school("a@b.c", "https://s.uk.arbor.education")

    def tearDown(self) -> None:
        import os

        if self._previous is None:
            os.environ.pop("ARBOR_PROBE_CONFIG", None)
        else:
            os.environ["ARBOR_PROBE_CONFIG"] = self._previous
        self._dir.cleanup()

    def _client(self, logins: list[str], *, reuse: bool = True):
        import http.cookiejar
        import json

        probe = self.probe

        class Stub(probe.UrllibArborClient):
            def _request(self, method, url, body, headers):
                if "/auth/login" in url:
                    logins.append(url)
                    return 200, json.dumps(
                        {
                            "success": True,
                            "items": [{"logged_in": True, "session_id": "s"}],
                        }
                    )
                if "?session=" in url:
                    self._jar.set_cookie(
                        http.cookiejar.Cookie(
                            0, "mis", "tok", None, False, "s.uk.arbor.education",
                            True, False, "/", True, True, None, True, None, None, {},
                        )
                    )
                    return 200, ""
                return 200, json.dumps({"ok": True})

        return Stub("a@b.c", "pw", reuse_session=reuse)

    def test_a_second_run_reuses_the_session(self) -> None:
        import asyncio

        logins: list[str] = []
        asyncio.run(self._client(logins).fetch_json("/x"))
        self.assertEqual(len(logins), 1)
        # A separate client, as a separate invocation would be.
        asyncio.run(self._client(logins).fetch_json("/x"))
        self.assertEqual(len(logins), 1, "the saved session should have been reused")

    def test_fresh_forces_a_login(self) -> None:
        import asyncio

        logins: list[str] = []
        asyncio.run(self._client(logins).fetch_json("/x"))
        asyncio.run(self._client(logins, reuse=False).fetch_json("/x"))
        self.assertEqual(len(logins), 2)

    def test_the_session_file_is_not_world_readable(self) -> None:
        import asyncio

        asyncio.run(self._client([]).fetch_json("/x"))
        path = Path(self.probe._cookie_path())
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_a_corrupt_session_file_is_ignored(self) -> None:
        path = Path(self.probe._cookie_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not a cookie jar")
        # Constructing the client must not raise.
        self.probe.UrllibArborClient("a@b.c", "pw", "https://s.uk.arbor.education")


class TestSchoolSelection(unittest.TestCase):
    """An account can cover several Arbor tenants; picking one must be easy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    SCHOOLS = {
        "payload": [
            {
                "name": "Ightham Primary School",
                "shortName": "Ightham Primary School",
                "sisUrl": "ightham-primary.uk.arbor.education",
                "postalCode": "TN15 9DD",
            },
            {
                "name": "Wrotham School",
                "shortName": "Wrotham School",
                "sisUrl": "wrotham-school.uk.arbor.education",
                "postalCode": "TN15 7RD",
            },
        ]
    }

    def setUp(self) -> None:
        # Isolate from any school this machine has already remembered.
        import os
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self._previous = os.environ.get("ARBOR_PROBE_CONFIG")
        os.environ["ARBOR_PROBE_CONFIG"] = str(Path(self._dir.name) / "none.json")

    def tearDown(self) -> None:
        import os

        if self._previous is None:
            os.environ.pop("ARBOR_PROBE_CONFIG", None)
        else:
            os.environ["ARBOR_PROBE_CONFIG"] = self._previous
        self._dir.cleanup()

    def _client(self, selector):
        import json

        probe = self.probe

        class Stub(probe.UrllibArborClient):
            def _request(self, method, url, body, headers):
                return 200, json.dumps(TestSchoolSelection.SCHOOLS)

        return Stub("a@b.c", "pw", selector)

    def test_a_url_needs_no_lookup(self) -> None:
        client = self._client("https://wrotham-school.uk.arbor.education")
        self.assertEqual(client.base_url, "https://wrotham-school.uk.arbor.education")

    def test_a_bare_host_is_accepted(self) -> None:
        client = self._client("wrotham-school.uk.arbor.education")
        self.assertEqual(client.base_url, "https://wrotham-school.uk.arbor.education")

    def test_a_name_fragment_resolves(self) -> None:
        import contextlib
        import io

        for selector in ("wrotham", "WROTHAM", "Wrotham School"):
            with self.subTest(selector=selector), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    self._client(selector)._resolve_school(),
                    "https://wrotham-school.uk.arbor.education",
                )

    def test_an_ambiguous_fragment_is_refused(self) -> None:
        with self.assertRaises(self.probe.ArborError) as caught:
            self._client("school")._resolve_school()
        self.assertIn("matches 2 schools", str(caught.exception))

    def test_an_unknown_fragment_lists_what_is_available(self) -> None:
        with self.assertRaises(self.probe.ArborError) as caught:
            self._client("nope")._resolve_school()
        message = str(caught.exception)
        self.assertIn("No school matches", message)
        self.assertIn("--school https://wrotham-school.uk.arbor.education", message)

    def test_no_selector_with_several_schools_is_refused_helpfully(self) -> None:
        with self.assertRaises(self.probe.ArborError) as caught:
            self._client(None)._resolve_school()
        message = str(caught.exception)
        self.assertIn("covers 2 schools", message)
        # Must be copy-pasteable, not just a list of names.
        self.assertIn("--school https://ightham-primary.uk.arbor.education", message)

    def test_a_remembered_school_is_honoured_by_every_command(self) -> None:
        """Regression: the login diagnostic took schools[0] instead.

        With two schools that meant sending one school's password to the other,
        and Arbor correctly answering that the credentials were wrong -- which
        read as a rejected password rather than as a bug.
        """
        import os
        import tempfile

        previous = os.environ.get("ARBOR_PROBE_CONFIG")
        directory = tempfile.TemporaryDirectory()
        os.environ["ARBOR_PROBE_CONFIG"] = str(Path(directory.name) / "schools.json")
        try:
            self.probe.remember_school(
                "a@b.c", "https://wrotham-school.uk.arbor.education"
            )
            client = self._client(None)
            # Ightham is first in the list; the remembered school must win.
            self.assertEqual(
                client.resolve_school(),
                "https://wrotham-school.uk.arbor.education",
            )
        finally:
            if previous is None:
                os.environ.pop("ARBOR_PROBE_CONFIG", None)
            else:
                os.environ["ARBOR_PROBE_CONFIG"] = previous
            directory.cleanup()

    def test_resolution_can_reuse_an_already_fetched_list(self) -> None:
        import contextlib
        import io

        schools = self.probe.protocol.parse_school_search(
            __import__("json").dumps(self.SCHOOLS)
        )
        with contextlib.redirect_stderr(io.StringIO()):
            resolved = self._client("wrotham").resolve_school(schools)
        self.assertEqual(resolved, "https://wrotham-school.uk.arbor.education")

    def test_school_url_remains_accepted_as_a_flag(self) -> None:
        args = self.probe.build_parser().parse_args(
            ["report", "--email", "a@b.c", "--school-url", "wrotham"]
        )
        self.assertEqual(args.school, "wrotham")


class TestRememberedSchool(unittest.TestCase):
    """The school is asked for once, then remembered."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def setUp(self) -> None:
        import os
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self._previous = os.environ.get("ARBOR_PROBE_CONFIG")
        self.config = Path(self._dir.name) / "schools.json"
        os.environ["ARBOR_PROBE_CONFIG"] = str(self.config)

    def tearDown(self) -> None:
        import os

        if self._previous is None:
            os.environ.pop("ARBOR_PROBE_CONFIG", None)
        else:
            os.environ["ARBOR_PROBE_CONFIG"] = self._previous
        self._dir.cleanup()

    def test_round_trip(self) -> None:
        self.assertIsNone(self.probe.remembered_school("a@b.c"))
        self.probe.remember_school("a@b.c", "https://school.uk.arbor.education")
        self.assertEqual(
            self.probe.remembered_school("a@b.c"), "https://school.uk.arbor.education"
        )

    def test_the_email_is_matched_case_insensitively(self) -> None:
        self.probe.remember_school("A@B.C", "https://school.uk.arbor.education")
        self.assertEqual(
            self.probe.remembered_school("a@b.c"), "https://school.uk.arbor.education"
        )

    def test_nothing_resembling_a_credential_is_written(self) -> None:
        self.probe.remember_school("a@b.c", "https://school.uk.arbor.education")
        stored = self.config.read_text().casefold()
        self.assertNotIn("password", stored)
        self.assertNotIn("secret", stored)
        self.assertNotIn("token", stored)
        self.assertNotIn("session", stored)

    def test_a_corrupt_file_is_ignored_rather_than_fatal(self) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text("{not json")
        self.assertIsNone(self.probe.remembered_school("a@b.c"))

    def test_an_unwritable_location_is_not_fatal(self) -> None:
        import os

        os.environ["ARBOR_PROBE_CONFIG"] = "/proc/nope/schools.json"
        # Must not raise: remembering is a convenience, not a requirement.
        self.probe.remember_school("a@b.c", "https://school.uk.arbor.education")

    def test_forgetting_removes_the_file(self) -> None:
        self.probe.remember_school("a@b.c", "https://school.uk.arbor.education")
        self.assertTrue(self.config.exists())
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()):
            self.probe.forget_schools()
        self.assertFalse(self.config.exists())

    def test_forget_school_needs_no_command_or_email(self) -> None:
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.probe.main(["--forget-school"]), 0)

    def test_an_explicit_school_overrides_the_memory(self) -> None:
        self.probe.remember_school("a@b.c", "https://remembered.uk.arbor.education")
        client = self.probe.UrllibArborClient(
            "a@b.c", "pw", "https://explicit.uk.arbor.education"
        )
        self.assertEqual(client.base_url, "https://explicit.uk.arbor.education")

    def test_ambiguity_without_a_terminal_is_refused_not_guessed(self) -> None:
        import io
        import sys as _sys

        schools = [
            self.probe.protocol.ArborSchool(name="A", base_url="https://a.example"),
            self.probe.protocol.ArborSchool(name="B", base_url="https://b.example"),
        ]
        original = _sys.stdin
        _sys.stdin = io.StringIO("")
        try:
            with self.assertRaises(self.probe.ArborConfigurationError):
                self.probe._choose_school(schools)
        finally:
            _sys.stdin = original


class TestProbeToolArgumentChecks(unittest.TestCase):
    """Commands that need a path must say so rather than failing later."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def test_shape_without_a_path_is_rejected(self) -> None:
        self.assertEqual(self.probe.main(["shape", "--email", "a@b.c"]), 2)

    def test_json_without_a_path_is_rejected(self) -> None:
        self.assertEqual(self.probe.main(["json", "--email", "a@b.c"]), 2)


if __name__ == "__main__":
    unittest.main()


class TestPasswordIsOnlyNeededForALogin(unittest.TestCase):
    """A saved session should make a run need no credential at all."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def test_the_password_is_not_resolved_unless_asked_for(self) -> None:
        calls: list[int] = []

        def provider() -> str:
            calls.append(1)
            return "pw"

        client = self.probe.UrllibArborClient(
            "a@b.c", provider, "https://s.uk.arbor.education"
        )
        self.assertEqual(calls, [], "constructing a client must not need a password")
        self.assertEqual(client.password, "pw")
        self.assertEqual(len(calls), 1)
        # Resolved once, then cached.
        self.assertEqual(client.password, "pw")
        self.assertEqual(len(calls), 1)

    def test_a_plain_string_still_works(self) -> None:
        client = self.probe.UrllibArborClient(
            "a@b.c", "pw", "https://s.uk.arbor.education"
        )
        self.assertEqual(client.password, "pw")

    def test_a_provider_may_refuse_and_that_reaches_the_caller(self) -> None:
        def provider() -> str:
            raise self.probe.ArborConfigurationError("no password available")

        client = self.probe.UrllibArborClient(
            "a@b.c", provider, "https://s.uk.arbor.education"
        )
        with self.assertRaises(self.probe.ArborConfigurationError):
            _ = client.password
