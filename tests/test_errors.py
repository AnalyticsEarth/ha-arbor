"""Tests for the exception taxonomy.

The shape of this hierarchy is a behavioural contract: exactly one branch of it
makes Home Assistant stop and ask the user for their password again. Everything
else must be retried with the password already stored.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _loader import errors  # noqa: E402


class TestOnlyCredentialRejectionPromptsForAPassword(unittest.TestCase):
    """Nothing transient may be mistaken for a wrong password."""

    def test_a_credential_rejection_is_an_auth_error(self) -> None:
        self.assertTrue(issubclass(errors.ArborAuthError, errors.ArborError))
        self.assertTrue(issubclass(errors.ArborNoSchoolsError, errors.ArborAuthError))

    def test_transient_failures_are_not_auth_errors(self) -> None:
        for error in (errors.ArborConnectionError, errors.ArborNotAvailableError):
            with self.subTest(error=error.__name__):
                self.assertFalse(issubclass(error, errors.ArborAuthError))
                self.assertTrue(issubclass(error, errors.ArborError))

    def test_a_forbidden_page_is_not_a_connection_problem_either(self) -> None:
        # They are handled differently: one is remembered and skipped, the other
        # is retried on the next refresh.
        self.assertFalse(
            issubclass(errors.ArborNotAvailableError, errors.ArborConnectionError)
        )
        self.assertFalse(
            issubclass(errors.ArborConnectionError, errors.ArborNotAvailableError)
        )

    def test_catching_the_base_error_catches_everything(self) -> None:
        for error in (
            errors.ArborConnectionError,
            errors.ArborNotAvailableError,
            errors.ArborAuthError,
            errors.ArborNoSchoolsError,
        ):
            with self.subTest(error=error.__name__):
                try:
                    raise error("boom")
                except errors.ArborError as caught:
                    self.assertIsInstance(caught, error)


class TestConfigurationErrorsAreNotSwallowed(unittest.TestCase):
    """A setup problem must not be reported as an unavailable page.

    Regression: the probe script stopping at "which of your two schools?" was
    caught by the scraper's tolerant page fetch and surfaced as "could not read
    the Arbor dashboard", which pointed the investigation at the wrong thing.
    """

    def test_it_is_an_arbor_error_but_not_the_tolerable_kind(self) -> None:
        self.assertTrue(issubclass(errors.ArborConfigurationError, errors.ArborError))
        self.assertFalse(
            issubclass(errors.ArborConfigurationError, errors.ArborNotAvailableError)
        )
        self.assertFalse(
            issubclass(errors.ArborConfigurationError, errors.ArborConnectionError)
        )

    def test_it_is_not_an_auth_error(self) -> None:
        # It must not trigger a password prompt either.
        self.assertFalse(issubclass(errors.ArborConfigurationError, errors.ArborAuthError))

    def test_the_scraper_re_raises_it(self) -> None:
        import ast
        from pathlib import Path as _Path

        source = (
            _Path(__file__).resolve().parents[1]
            / "custom_components"
            / "arbor"
            / "scraper.py"
        ).read_text()
        tree = ast.parse(source)
        reraised = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            caught = node.type
            names = (
                [n.id for n in caught.elts if isinstance(n, ast.Name)]
                if isinstance(caught, ast.Tuple)
                else [caught.id] if isinstance(caught, ast.Name) else []
            )
            if "ArborConfigurationError" not in names:
                continue
            if any(isinstance(stmt, ast.Raise) for stmt in node.body):
                reraised.append(names)
        self.assertTrue(
            reraised,
            "scraper.py must re-raise ArborConfigurationError rather than "
            "treating it as an unavailable page",
        )


class TestNoDuplicateDefinitions(unittest.TestCase):
    """A method defined twice silently loses the first definition.

    Mechanical refactoring produced exactly that in scraper.py, and nothing
    caught it because both copies happened to be identical.
    """

    def test_no_module_defines_anything_twice(self) -> None:
        import ast
        import collections
        from pathlib import Path as _Path

        pkg = _Path(__file__).resolve().parents[1] / "custom_components" / "arbor"
        offenders: list[str] = []
        for path in sorted(pkg.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                names = [
                    child.name
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                for name, count in collections.Counter(names).items():
                    if count > 1:
                        offenders.append(f"{path.name}: {node.name}.{name} x{count}")
            top = [
                child.name
                for child in tree.body
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            ]
            for name, count in collections.Counter(top).items():
                if count > 1:
                    offenders.append(f"{path.name}: {name} x{count}")
        self.assertEqual(offenders, [])


class TestOnlyTheLoginStepRaisesAnAuthError(unittest.TestCase):
    """Static guard over the client, so the invariant cannot quietly regress.

    Scans every module that can raise, not just one, and asserts it found
    something -- when the login code moved to protocol.py, that sanity check is
    what revealed the guard had stopped looking at anything.
    """

    #: Functions allowed to report an authentication failure: the school lookup,
    #: which validates credentials as a side effect, and the login itself.
    PERMITTED = frozenset(
        {
            "async_list_schools",
            "_login_locked",
            "parse_school_search",
            "parse_login",
        }
    )

    MODULES = ("api", "protocol", "coordinator", "scraper")

    @classmethod
    def _auth_raises(cls) -> list[tuple[str, int, str]]:
        import ast

        pkg = (
            Path(__file__).resolve().parents[1] / "custom_components" / "arbor"
        )
        found: list[tuple[str, int, str]] = []
        for module in cls.MODULES:
            path = pkg / f"{module}.py"
            if not path.exists():
                continue
            tree = ast.parse(path.read_text())

            owner: dict[int, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                        owner.setdefault(line, node.name)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Raise) or node.exc is None:
                    continue
                call = node.exc
                name = call.func if isinstance(call, ast.Call) else call
                if isinstance(name, ast.Name) and name.id in (
                    "ArborAuthError",
                    "ArborNoSchoolsError",
                ):
                    found.append((module, node.lineno, owner.get(node.lineno, "<module>")))
        return found

    def test_auth_errors_only_come_from_the_login_flow(self) -> None:
        """Regression: a 403 on one optional endpoint prompted for a password."""
        offenders = [
            entry for entry in self._auth_raises() if entry[2] not in self.PERMITTED
        ]
        self.assertEqual(
            offenders,
            [],
            "authentication errors must only be raised while logging in; "
            f"found some elsewhere: {offenders}",
        )

    def test_the_guard_is_actually_looking_at_something(self) -> None:
        # A move or a rename could otherwise make the test above vacuous.
        self.assertTrue(
            self._auth_raises(),
            "found no authentication raises at all -- has the login code moved "
            f"out of {self.MODULES}?",
        )


if __name__ == "__main__":
    unittest.main()
