import os

# Tests insert and delete documents, so they must never touch the team's shared
# database. Set before any app import: the Mongo client is created at import time.
# Same cluster as MONGO_URI (the shared Atlas cluster), separate database: the tick
# tests touch every ACTIVE session, so they must never run against `seco` itself.
os.environ["MONGO_DATABASE"] = os.environ.get("SECO_TEST_DATABASE", "seco_test")

import pytest

from app.core.config import get_settings
from app.db.migrations import apply_migrations
from tests.shared_loop import TEST_LOOP


@pytest.fixture(scope="session", autouse=True)
def test_database():
    name = get_settings().mongo_database
    if not name.endswith("_test"):
        pytest.exit(f"Refusing to run tests against non-test database {name!r}", returncode=2)

    # Validators and unique indexes are part of what the tests rely on.
    TEST_LOOP.run_until_complete(apply_migrations())
    yield
