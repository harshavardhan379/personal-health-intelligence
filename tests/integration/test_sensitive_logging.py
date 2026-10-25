"""YGH-123, sensitive log audit, as a test: plant recognisable values everywhere, run every flow
including the failure paths, and prove none of them reaches ANY log output.

What is captured is what a deployment would actually write: every record from every logger at
DEBUG, formatted by the real JSON handler (so tracebacks go through SafeJsonFormatter), plus the
raw records (message, arguments, extra fields, exception text).

One logger is left out on purpose: `httpx` and `httpcore` log the TEST CLIENT's own outgoing
requests, URL and query string included. A real server never emits those lines for incoming
requests (its access log is switched off, which tests/unit/test_security_hardening.py checks).
"""

import io
import logging

import pytest

from packages.observability import logs
from services.api.app.core import pipeline, ratelimit
from services.document_processing import normalize
from services.reasoning.providers import LLMProviderError
from tests.helpers import app_log_text
from tests.integration.conftest import PASSWORD

EMAIL = "sentinel.person@example.com"
FILENAME = "sentinel-patient-lab.pdf"
VALUE = "7.77"
QUESTION_WORD = "sentinelquestionword"
ANSWER_TEXT = "SENTINEL-ANSWER-TEXT"
CLAIM_TEXT = "SENTINEL-CLAIM-TEXT"
PARSER_MESSAGE = "SENTINEL-PARSER-MESSAGE"
CRASH_MESSAGE = "SENTINEL-CRASH-MESSAGE"
PROVIDER_MESSAGE = "SENTINEL-PROVIDER-MESSAGE"
WRONG_PASSWORD = "Sentinel-Wrong-Password-4471"
SENTINELS = [EMAIL, "sentinel.person", FILENAME, VALUE, QUESTION_WORD, ANSWER_TEXT, CLAIM_TEXT,
             PARSER_MESSAGE, CRASH_MESSAGE, PROVIDER_MESSAGE, PASSWORD, WRONG_PASSWORD,
             "HbA1c"]
CLIENT_SIDE = ("httpx", "httpcore")  # prefixes: the library also logs as httpx2, httpcore2


class Capture:
    def __init__(self):
        self.stream = io.StringIO()
        self.records = []

        outer = self

        class Collect(logging.Handler):
            def emit(self, record):
                if record.name.startswith(CLIENT_SIDE):
                    return
                outer.records.append(record)

        self.collector = Collect(level=logging.DEBUG)
        self.formatted = logs.build_handler(self.stream)
        self.formatted.setLevel(logging.DEBUG)

        class SkipClient(logging.Filter):
            def filter(self, record):
                return not record.name.startswith(CLIENT_SIDE)

        self.formatted.addFilter(SkipClient())

    def text(self) -> str:
        return self.stream.getvalue() + "\n" + app_log_text(self.records)


@pytest.fixture
def capture():
    root = logging.getLogger()
    cap = Capture()
    old_level = root.level
    root.addHandler(cap.collector)
    root.addHandler(cap.formatted)
    root.setLevel(logging.DEBUG)
    yield cap
    root.removeHandler(cap.collector)
    root.removeHandler(cap.formatted)
    root.setLevel(old_level)


def test_nothing_sensitive_reaches_any_log_in_any_flow(stack, capture, monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.main import app

    quiet = TestClient(app, raise_server_exceptions=False)

    # --- sign up, sign in, wrong passwords, a rate limit -------------------------------------
    headers = stack.register(EMAIL, PASSWORD)
    assert stack.login(EMAIL).status_code == 200
    for _ in range(6):
        stack.login(EMAIL, WRONG_PASSWORD)
    # a form-encoded login: the validation error that used to carry the password into the log
    quiet.post("/api/v1/auth/login", data={"username": EMAIL, "password": WRONG_PASSWORD})
    stack.client.post("/api/v1/auth/register", json={"email": "not-an-email",
                                                     "password": WRONG_PASSWORD})

    # --- upload, review, confirm -------------------------------------------------------------
    upload = stack.upload(headers, f"HbA1c: {VALUE} % (ref 4.0-5.6) 2026-01-10", FILENAME)
    assert upload.status_code == 201
    stack.confirm_all(headers)
    # a refused upload (the body and the name must not be echoed into a log either)
    stack.client.post("/api/v1/documents", headers=headers, files={
        "file": (FILENAME, f"%PDF-1.4 {PARSER_MESSAGE} {VALUE}".encode(), "application/pdf")})

    # --- pipeline failures: a parser that quotes the document in its error -------------------
    def parser_quotes_the_document(content):
        raise ValueError(f"{PARSER_MESSAGE} HbA1c {VALUE} {EMAIL}")
    with monkeypatch.context() as patch:
        patch.setattr(normalize.parse, "extract_pdf_pages", parser_quotes_the_document)
        stack.upload(headers, f"HbA1c: {VALUE} % 2026-02-01", FILENAME)
    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "extract_all",
                      lambda pages: (_ for _ in ()).throw(RuntimeError(f"{CRASH_MESSAGE} {VALUE}")))
        stack.upload(headers, f"HbA1c: {VALUE} % 2026-03-01", FILENAME)

    with monkeypatch.context() as patch:  # a crash nobody planned for, inside the pipeline
        patch.setattr(pipeline, "process_document",
                      lambda *a: (_ for _ in ()).throw(MemoryError(f"{CRASH_MESSAGE} {VALUE}")))
        stack.upload(headers, f"HbA1c: {VALUE} % 2026-03-15", FILENAME)

    # --- the assistant: a good answer, a dangerous one, a refusal, a model outage ------------
    question = f"What is my latest HbA1c result {QUESTION_WORD}?"
    stack.llm.reply = {"answer": f"{ANSWER_TEXT} HbA1c {VALUE}",
                       "claims": [{"text": f"{CLAIM_TEXT} HbA1c {VALUE}", "citations": [1]}],
                       "uncertainty": f"{ANSWER_TEXT} unsure"}
    assert stack.ask(headers, question).status_code == 200
    stack.llm.reply = {"answer": f"You should stop taking metformin {ANSWER_TEXT}",
                       "claims": [{"text": f"stop {CLAIM_TEXT}", "citations": [1]}],
                       "uncertainty": None}
    stack.ask(headers, question)
    stack.ask(headers, f"Should I stop taking my metformin {QUESTION_WORD}?")
    stack.llm.error = LLMProviderError(f"{PROVIDER_MESSAGE} {VALUE} {question}")
    stack.ask(headers, question)
    stack.llm.error = None
    stack.client.get("/api/v1/search", headers=headers, params={"q": f"{QUESTION_WORD} {VALUE}"})
    stack.client.get(f"/api/v1/trends/HbA1c{QUESTION_WORD}", headers=headers)

    # --- an unhandled crash inside a route that handles a question ---------------------------
    def boom(*args, **kwargs):
        raise RuntimeError(f"{CRASH_MESSAGE} {question} {EMAIL} {PASSWORD}")
    with monkeypatch.context() as patch:
        patch.setattr("services.api.app.api.v1.assistant.run_verified_pipeline", boom)
        assert quiet.post("/api/v1/assistant/ask", headers=headers,
                          json={"question": question}).status_code == 500

    ratelimit.login_failures.reset()  # the wrong logins above locked this account, as they should
    # --- deletion: a wrong password, one document, all data, the account ---------------------
    quiet.request("DELETE", "/api/v1/account/data", headers=headers,
                  json={"password": WRONG_PASSWORD})
    first = stack.client.get("/api/v1/documents", headers=headers).json()[0]["id"]
    stack.client.delete(f"/api/v1/documents/{first}", headers=headers)
    stack.client.request("DELETE", "/api/v1/account", headers=headers, json={"password": PASSWORD})

    # --- what was captured -------------------------------------------------------------------
    written = capture.stream.getvalue()
    assert written.count("request_finished") >= 25, "the capture is not seeing the request lines"
    assert "stage_failed" in written and "document_failed" in written
    assert "pipeline_crashed" in written
    assert "user_data_deleted" in written
    everything = capture.text()
    leaked = [s for s in SENTINELS if s.lower() in everything.lower()]
    assert leaked == [], f"sensitive values reached the logs: {leaked}"


def test_the_audit_would_notice_a_leak(capture):
    """A control: the same capture does see a sensitive value when one is logged."""
    logging.getLogger("app.audit_control").warning("leak %s", EMAIL)
    assert EMAIL in capture.text()


def test_a_leak_through_an_exception_message_is_cut_by_the_formatter(capture):
    try:
        raise ValueError(f"{PARSER_MESSAGE} {PASSWORD}")
    except ValueError:
        logging.getLogger("app.audit_control").error("failed", exc_info=True)
    assert "failed" in capture.stream.getvalue() and "ValueError" in capture.stream.getvalue()
    assert PARSER_MESSAGE not in capture.stream.getvalue()
    assert PASSWORD not in capture.stream.getvalue()
