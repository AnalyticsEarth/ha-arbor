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

    def test_show_values_defaults_to_off(self) -> None:
        args = self.probe.build_parser().parse_args(["report", "--email", "a@b.c"])
        self.assertFalse(args.show_values)

    def test_redaction_removes_content_by_default(self) -> None:
        payload = {"studentName": "Amelia Example", "count": 3}
        redacted = self.probe.redact(payload, show_values=False)
        self.assertNotIn("Amelia Example", str(redacted))
        self.assertEqual(redacted["studentName"], "str[14]")

    def test_values_are_kept_when_asked(self) -> None:
        payload = {"studentName": "Amelia Example"}
        self.assertEqual(self.probe.redact(payload, show_values=True), payload)

    def test_names_are_masked_in_the_report(self) -> None:
        self.assertEqual(self.probe._mask_name("Amelia Example", False), "A… E…")
        self.assertEqual(self.probe._mask_name("Amelia Example", True), "Amelia Example")

    def test_there_is_no_password_argument(self) -> None:
        # A --password flag would be recorded in the user's shell history.
        actions = {
            option
            for action in self.probe.build_parser()._actions
            for option in action.option_strings
        }
        self.assertNotIn("--password", actions)
        self.assertNotIn("-p", actions)


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
