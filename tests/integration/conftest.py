"""Integration tests: the real API, the real pipeline and a REAL Postgres (pgvector), created from
zero by running the project's own Alembic migrations (so they are tested too).

What is real: the HTTP routes and JWT login, the upload pipeline, extraction, review, the
embeddings (pgvector), retrieval, the safety gate, verification and the trace table, and every
foreign key and cascade in the schema (SQLite, which the unit tests use, enforces none of them).
What is stood in: the language model (a scripted provider, so the tests are deterministic and need
no Ollama).

The database is `phi_integration_test` on the compose Postgres, dropped and rebuilt at the start of
the run; the name must end in `_test` so a run can never touch the product database. Without
Postgres the tests skip, unless REQUIRE_POSTGRES=1 is set (CI sets it), where they fail instead:
a skipped suite must never look like a passing one.
"""

import json
import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from packages.observability import metrics
from packages.schemas.db import Base, get_db
from services.api.app.core import pipeline, ratelimit, storage
from services.api.app.core.config import settings
from services.api.main import app
from tests.helpers import make_pdf

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://phi:phi_dev_password@localhost:5433/phi_integration_test",
)
PASSWORD = "correct horse battery"


def _unavailable(reason: str):
    if os.environ.get("REQUIRE_POSTGRES") == "1":
        pytest.fail(f"REQUIRE_POSTGRES=1 but Postgres is unavailable: {reason}")
    pytest.skip(f"Postgres unavailable: {reason}")


@pytest.fixture(scope="session")
def engine():
    url = make_url(TEST_URL)
    name = url.database or ""
    assert name.endswith("_test"), f"refusing to use database {name!r}: it must end in '_test'"
    try:
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        admin.dispose()
    except OperationalError as exc:
        _unavailable(type(exc).__name__)

    config = Config()  # no ini file: loading one would reconfigure logging for the whole run
    config.set_main_option("script_location", "database/migrations")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def clean(engine):
    """Every test starts from empty tables."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    ratelimit.login_failures.reset()
    ratelimit.register_attempts.reset()
    ratelimit.ask_calls.reset()
    metrics.registry.reset()


class ScriptedProvider:
    """The language model, stood in: replies with whatever the test set, and records what it saw."""

    name = "ollama"
    model = "scripted-test-model"

    def __init__(self):
        self.reply = {"answer": "", "claims": [], "uncertainty": None}
        self.raw = None  # when set, returned as-is instead of the JSON of `reply`
        self.error = None
        self.calls = []

    def generate_with_usage(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        text = self.raw if self.raw is not None else json.dumps(self.reply)
        return text, {"input_tokens": 100, "output_tokens": 20}

    def generate(self, messages):
        return self.generate_with_usage(messages)[0]


class Stack:
    """The running application as a test sees it."""

    def __init__(self, client, db_factory, root):
        self.client, self.db_factory, self.root = client, db_factory, root
        self.llm = ScriptedProvider()

    def db(self):
        return self.db_factory()

    def register(self, email="a@example.com", password=PASSWORD) -> dict:
        response = self.client.post("/api/v1/auth/register",
                                    json={"email": email, "password": password})
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def login(self, email, password=PASSWORD):
        return self.client.post("/api/v1/auth/login", json={"email": email, "password": password})

    def upload(self, headers, text="HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10", name="lab.pdf",
               pdf=None):
        return self.client.post("/api/v1/documents", headers=headers, files={
            "file": (name, pdf if pdf is not None else make_pdf(text), "application/pdf")})

    def events(self, headers, status="pending"):
        return self.client.get("/api/v1/events", headers=headers,
                               params={"status_filter": status}).json()

    def confirm_all(self, headers):
        for event in self.events(headers):
            response = self.client.post(f"/api/v1/events/{event['id']}/confirm", headers=headers)
            assert response.status_code == 200, response.text

    def confirmed_document(self, headers, text="HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10",
                           name="lab.pdf"):
        """Upload, let the pipeline run, confirm every extracted fact. Returns the document."""
        response = self.upload(headers, text, name)
        assert response.status_code == 201, response.text
        self.confirm_all(headers)
        return self.client.get(f"/api/v1/documents/{response.json()['id']}", headers=headers).json()

    def ask(self, headers, question, request_id=None):
        extra = {"X-Request-ID": request_id} if request_id else {}
        return self.client.post("/api/v1/assistant/ask", headers={**headers, **extra},
                                json={"question": question})


@pytest.fixture
def stack(engine, session_factory, tmp_path, monkeypatch):
    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(settings, "jwt_secret", "integration-test-secret-of-sufficient-length")
    monkeypatch.setattr(pipeline, "SessionLocal", session_factory)
    app.dependency_overrides[get_db] = override_db
    system = Stack(TestClient(app), session_factory, tmp_path)
    monkeypatch.setattr("services.reasoning.generate.get_llm_provider", lambda: system.llm)
    yield system
    app.dependency_overrides.clear()
