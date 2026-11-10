"""Shared setup for the people tests."""

import pytest

from tests.services.thread_harness import owner, rt, sessions  # noqa: F401  (fixtures the people tests share)


@pytest.fixture(autouse=True)
def _people_tests_use_their_own_runtime(request):
    """The app's global thread runtime keeps pooled database connections that belong to the first test's
    event loop. On PostgreSQL the next test that reaches it fails ("another operation is in progress"), so
    the people tests give the app a runtime on this test's own engine, as the thread API tests do."""
    if not request.module.__name__.rsplit(".", 1)[-1].startswith("test_people"):
        yield
        return
    app = request.getfixturevalue("app")
    runtime = request.getfixturevalue("rt")
    from app.routers.threads import get_runtime

    app.dependency_overrides[get_runtime] = lambda: runtime
    yield
