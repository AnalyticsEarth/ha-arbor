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


class TestOnlyTheLoginStepRaisesAnAuthError(unittest.TestCase):
    """Static guard over api.py, so the invariant cannot quietly regress."""

    @staticmethod
    def _auth_raises() -> list[tuple[int, str]]:
        import ast

        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "arbor"
            / "api.py"
        ).read_text()
        tree = ast.parse(source)

        # Map every line to the function that contains it.
        owner: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    owner.setdefault(line, node.name)

        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc
            name = call.func if isinstance(call, ast.Call) else call
            if isinstance(name, ast.Name) and name.id in (
                "ArborAuthError",
                "ArborNoSchoolsError",
            ):
                found.append((node.lineno, owner.get(node.lineno, "<module>")))
        return found

    def test_auth_errors_only_come_from_the_login_flow(self) -> None:
        """Regression: a 403 on one optional endpoint prompted for a password.

        Only the login handshake and the school lookup -- which validates
        credentials as a side effect -- may report an authentication failure.
        """
        permitted = {"async_list_schools", "_login_locked"}
        offenders = [
            (line, func) for line, func in self._auth_raises() if func not in permitted
        ]
        self.assertEqual(
            offenders,
            [],
            "authentication errors must only be raised while logging in; "
            f"found some elsewhere: {offenders}",
        )

    def test_the_guard_is_actually_looking_at_something(self) -> None:
        # A typo in the AST walk would make the test above vacuously pass.
        self.assertTrue(self._auth_raises())


if __name__ == "__main__":
    unittest.main()
