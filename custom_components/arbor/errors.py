"""Exception taxonomy for the Arbor integration.

Kept in its own dependency-free module because the shape of this hierarchy is a
behavioural contract, not an implementation detail: exactly one branch of it
makes Home Assistant stop and ask the user for their password again.

Only :class:`ArborAuthError` does that. Anything that is merely a bad moment --
Arbor being slow, rate-limiting a login, refusing a page this account cannot
see, answering a handshake oddly -- must not, because the stored password is
almost certainly still correct and prompting for it is both useless and
alarming.
"""

from __future__ import annotations


class ArborError(Exception):
    """Base error for everything the Arbor client raises."""


class ArborConnectionError(ArborError):
    """Arbor could not be reached, or answered in a way we cannot use.

    Transient by assumption: the caller should retry later rather than involve
    the user. Also covers a login that mechanically failed without Arbor
    actually rejecting the credentials.
    """


class ArborNotAvailableError(ArborError):
    """Arbor will not serve this page or endpoint to this account.

    The credentials are good; this particular resource is not on offer. Callers
    skip it and carry on with everything else.
    """


class ArborAuthError(ArborError):
    """Arbor rejected the credentials themselves.

    The only error that should lead to a re-authentication prompt. Raise it when
    Arbor has actively said the email address and password are not valid -- never
    for a timeout, a rate limit, a missing session id, or a forbidden page.
    """


class ArborNoSchoolsError(ArborAuthError):
    """No Arbor tenant is associated with this email address and password.

    Arbor's school lookup validates credentials as a side effect and returns an
    empty payload when they are wrong, so during setup this is a credentials
    problem. It is not reachable once a school has been chosen and stored.
    """
