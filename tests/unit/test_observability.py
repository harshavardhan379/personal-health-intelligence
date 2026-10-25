"""Request ids, structured logs, metrics, AI-trace collection and retry policy (M16, YGH-90 to 94).

Pure logic plus a tiny app wired with the real middleware and error handlers; no database."""

import errno
import io
import json
import logging

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from packages.observability import resilience, trace
from packages.observability.context import (
    get_request_id,
    new_request_id,
    request_id_from,
    set_request_id,
    valid_request_id,
)
from packages.observability.logs import (
    MAX_MESSAGE_CHARS,
    REDACTED,
    SENSITIVE_FIELDS,
    build_handler,
)
from packages.observability.metrics import (
    SAMPLES,
    STAGES,
    Metrics,
    registry,
    request_metric,
    route_template,
)
from packages.observability.middleware import RequestContextMiddleware
from services.api.app.core import errors
from services.api.app.core.config import settings


# --- request ids ------------------------------------------------------------------------
def test_a_plain_token_is_accepted_as_a_request_id():
    for ok in ("abc123", "web-9f8e7d6c", "a.b_c-d", "x" * 64, new_request_id()):
        assert valid_request_id(ok) and request_id_from(ok) == ok


def test_anything_else_is_replaced_never_echoed():
    for bad in ("", "has space", "line\nbreak", "abc\r\nX-Evil: 1", "x" * 65, "ünï", None, 123,
                "<script>"):
        assert not valid_request_id(bad)
        replaced = request_id_from(bad)
        assert valid_request_id(replaced) and replaced != bad


def test_generated_ids_are_unique():
    assert len({new_request_id() for _ in range(200)}) == 200


# --- structured logs --------------------------------------------------------------------
def logged(message, level=logging.INFO, request_id=None, **extra):
    stream = io.StringIO()
    logger = logging.getLogger(f"test.logs.{id(stream)}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(build_handler(stream))
    set_request_id(request_id)
    logger.log(level, message, extra=extra)
    set_request_id(None)
    return json.loads(stream.getvalue())


def test_every_line_is_json_with_timestamp_level_logger_and_request_id():
    line = logged("hello", request_id="req-1")
    assert line["message"] == "hello" and line["level"] == "INFO"
    assert line["request_id"] == "req-1" and line["logger"].startswith("test.logs.")
    assert line["timestamp"]


def test_a_line_outside_a_request_says_so():
    assert logged("startup")["request_id"] == "-"


def test_fields_that_would_carry_health_content_are_redacted():
    line = logged("done", question="What is my HbA1c?", answer="7.2", quote="HbA1c 7.2 %",
                  claim_text="x", data={"value": 7.2}, email="a@b.c", route="/x", status=200)
    for name in ("question", "answer", "quote", "claim_text", "data", "email"):
        assert line[name] == REDACTED, name
    assert line["route"] == "/x" and line["status"] == 200  # metadata passes through
    assert "7.2" not in json.dumps(line) and "a@b.c" not in json.dumps(line)


def test_every_sensitive_name_is_covered_whatever_its_case():
    for name in sorted(SENSITIVE_FIELDS):
        assert logged("x", **{name: "SECRET"})[name] == REDACTED, name
    assert logged("x", Question="SECRET")["Question"] == REDACTED


def test_a_huge_message_is_cut():
    line = logged("A" * (MAX_MESSAGE_CHARS * 3))
    assert len(line["message"]) < MAX_MESSAGE_CHARS + 30 and line["message"].endswith("[truncated]")


def test_the_source_filename_field_python_adds_is_left_alone():
    # "filename" is a standard LogRecord attribute (the source file); redacting it would hide
    # where a line came from. Document names go in `document_name`, which IS redacted.
    assert "filename" not in SENSITIVE_FIELDS and "document_name" in SENSITIVE_FIELDS


# --- metrics ----------------------------------------------------------------------------
def test_metrics_report_count_errors_mean_percentiles_and_max():
    m = Metrics()
    for ms in range(1, 101):
        m.observe("llm", float(ms), ok=ms != 100)
    s = m.snapshot()["llm"]
    assert s["count"] == 100 and s["errors"] == 1 and s["max_ms"] == 100.0
    assert s["mean_ms"] == 50.5 and 49 <= s["p50_ms"] <= 52 and 94 <= s["p95_ms"] <= 96


def test_metrics_memory_is_bounded_but_counts_are_not():
    m = Metrics()
    for i in range(SAMPLES * 3):
        m.observe("x", float(i))
    s = m.snapshot()["x"]
    assert s["count"] == SAMPLES * 3 and len(m._series["x"].recent) == SAMPLES
    assert s["p50_ms"] > SAMPLES  # percentiles come from the RECENT observations


def test_timed_records_a_block_and_an_exception_counts_as_an_error_but_propagates():
    m = Metrics()
    with m.timed("ok"):
        pass
    with pytest.raises(ValueError):
        with m.timed("bad"):
            raise ValueError("boom")
    snap = m.snapshot()
    assert snap["ok"]["count"] == 1 and snap["ok"]["errors"] == 0
    assert snap["bad"]["count"] == 1 and snap["bad"]["errors"] == 1


def test_a_stage_with_no_observations_is_simply_absent():
    assert Metrics().snapshot() == {}


def test_the_stages_the_ticket_names_are_all_defined():
    assert set(STAGES) == {"upload", "processing", "extraction", "retrieval", "llm",
                           "verification"}


def test_a_route_template_collapses_ids_so_one_route_is_one_metric():
    uid = "11111111-1111-4111-8111-111111111111"
    assert route_template(f"/api/v1/documents/{uid}/file", {"document_id": uid}) == \
        "/api/v1/documents/{document_id}/file"
    assert route_template("/api/v1/health", {}) == "/api/v1/health"
    assert route_template("/x", None) == "/x"
    assert request_metric("GET", "/x") == "request GET /x"


# --- the request middleware, in a small app with the real error handlers -----------------
@pytest.fixture
def client():
    registry.reset()
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, slow_ms=50)
    app.add_exception_handler(StarletteHTTPException, errors.http_error_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_error_handler)
    app.add_exception_handler(Exception, errors.unhandled_error_handler)

    @app.get("/items/{item_id}")
    def item(item_id: int):
        return {"request_id_seen_inside": get_request_id()}

    @app.get("/state")
    def state(request: Request):
        return {"on_request_state": request.state.request_id}

    @app.get("/missing")
    def missing():
        raise HTTPException(status_code=404, detail="Nope")

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret health detail in an exception message")

    @app.get("/slow")
    def slow():
        import time
        time.sleep(0.08)
        return {}

    return TestClient(app, raise_server_exceptions=False)


def test_every_response_carries_a_request_id_and_the_endpoint_sees_it(client):
    response = client.get("/items/1")
    rid = response.headers["x-request-id"]
    assert valid_request_id(rid) and response.json()["request_id_seen_inside"] == rid


def test_the_id_is_on_the_request_state_for_handlers_outside_the_middleware(client):
    response = client.get("/state", headers={"X-Request-ID": "req-state"})
    assert response.json() == {"on_request_state": "req-state"}


def test_a_callers_id_is_adopted_and_a_bad_one_replaced(client):
    assert client.get("/items/1", headers={"X-Request-ID": "web-abc.123"}) \
        .headers["x-request-id"] == "web-abc.123"
    replaced = client.get("/items/1", headers={"X-Request-ID": "bad id with spaces"}) \
        .headers["x-request-id"]
    assert valid_request_id(replaced) and replaced != "bad id with spaces"


def test_error_responses_carry_the_id_in_header_and_body(client):
    response = client.get("/missing", headers={"X-Request-ID": "req-404"})
    assert response.status_code == 404
    assert response.headers["x-request-id"] == "req-404"
    assert response.json()["error"] == {"code": 404, "message": "Nope", "request_id": "req-404"}


def test_a_validation_error_carries_the_id_too(client):
    body = client.get("/items/not-a-number", headers={"X-Request-ID": "req-422"}).json()
    assert body["error"]["code"] == 422 and body["error"]["request_id"] == "req-422"


def test_a_crash_is_a_generic_500_with_the_id_and_none_of_the_exception_text(client):
    response = client.get("/boom", headers={"X-Request-ID": "req-500"})
    assert response.status_code == 500 and response.headers["x-request-id"] == "req-500"
    assert response.json()["error"] == {"code": 500, "message": "Internal server error",
                                        "request_id": "req-500"}
    assert "secret" not in response.text


def test_a_500_is_readable_by_the_web_app_only_for_an_allowed_origin(client):
    allowed = settings.cors_origins[0]
    ok = client.get("/boom", headers={"Origin": allowed})
    assert ok.headers["access-control-allow-origin"] == allowed
    assert "x-request-id" in ok.headers["access-control-expose-headers"].lower()
    evil = client.get("/boom", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_the_crash_log_names_the_error_class_and_never_its_message(client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        client.get("/boom")
    record = next(r for r in caplog.records if r.message == "unhandled_error")
    assert record.error_type == "RuntimeError"
    assert "secret health detail" not in record.getMessage()


def test_one_log_line_per_request_with_the_route_template_not_the_ids(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        client.get("/items/42?secret=value", headers={"X-Request-ID": "req-log"})
    [record] = [r for r in caplog.records if r.name == "app.request"]
    assert record.message == "request_finished" and record.method == "GET"
    assert record.route == "/items/{item_id}" and record.status == 200
    assert record.duration_ms >= 0 and record.slow is False
    assert "42" not in record.route and "secret" not in str(record.__dict__)


def test_a_failed_or_slow_request_is_logged_as_a_warning(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        client.get("/boom")
        client.get("/slow")
    levels = {r.route: r.levelno for r in caplog.records if r.name == "app.request"}
    assert levels["/boom"] == logging.WARNING and levels["/slow"] == logging.WARNING
    slow = next(r for r in caplog.records if r.name == "app.request" and r.route == "/slow")
    assert slow.slow is True


def test_request_timings_are_recorded_per_route_and_a_500_counts_as_an_error(client):
    for item_id in (1, 2, 3):
        client.get(f"/items/{item_id}")
    client.get("/boom")
    snap = registry.snapshot()
    assert snap[request_metric("GET", "/items/{item_id}")]["count"] == 3
    assert snap[request_metric("GET", "/boom")]["errors"] == 1


def test_non_http_traffic_passes_straight_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    import asyncio
    asyncio.run(RequestContextMiddleware(inner)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


# --- AI trace collection ----------------------------------------------------------------
def test_recording_with_no_active_trace_does_nothing():
    trace.end_trace()
    trace.record(model="x")  # must not raise
    assert trace.current_trace() is None


def test_a_started_trace_collects_what_the_stages_record():
    set_request_id("req-trace")
    t = trace.start_trace(question_chars=24)
    trace.record(intent="TEMPORAL", model="llama3.2:3b", llm_ms=1234, input_tokens=300,
                 output_tokens=40, retrieval_event_ids=["e1", "e2"], evidence_count=2)
    assert t is trace.current_trace()
    assert (t.request_id, t.question_chars, t.intent, t.evidence_count) == (
        "req-trace", 24, "TEMPORAL", 2)
    assert t.status == trace.OK and t.retrieval_event_ids == ["e1", "e2"]
    trace.end_trace()
    set_request_id(None)
    assert trace.current_trace() is None


def test_a_typo_in_a_trace_field_is_an_error_not_silently_lost_telemetry():
    trace.start_trace()
    with pytest.raises(TypeError, match="unknown trace field"):
        trace.record(modle="x")
    trace.end_trace()


def test_the_trace_has_no_field_that_could_hold_health_content():
    names = set(trace.AITraceData.__dataclass_fields__)
    assert not names & SENSITIVE_FIELDS
    assert not names & {"question", "answer", "evidence", "prompt", "quote", "text"}


def test_label_counts():
    assert trace.label_counts(["SUPPORTED", "SUPPORTED", "CONTRADICTED"]) == {
        "SUPPORTED": 2, "CONTRADICTED": 1}
    assert trace.label_counts([]) == {}


# --- retry and timeout policy -----------------------------------------------------------
class Boom(Exception):
    pass


def flaky(failures, error=Boom("transient")):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= failures:
            raise error
        return "ok"
    return fn, calls


RETRY3 = resilience.RetryPolicy(attempts=3, base_delay=1.0, max_delay=8.0, jitter=0.0)


def test_a_transient_failure_is_retried_until_it_works():
    fn, calls = flaky(2)
    sleeps = []
    result = resilience.call_with_retry(fn, RETRY3, is_transient=lambda e: True, name="x",
                                        sleep=sleeps.append)
    assert result == "ok" and calls["n"] == 3 and sleeps == [1.0, 2.0]  # doubling backoff


def test_it_gives_up_after_the_attempts_and_raises_the_last_error():
    fn, calls = flaky(10)
    with pytest.raises(Boom):
        resilience.call_with_retry(fn, RETRY3, is_transient=lambda e: True, name="x",
                                   sleep=lambda s: None)
    assert calls["n"] == 3


def test_a_permanent_failure_is_not_retried_and_does_not_wait():
    fn, calls = flaky(10)
    sleeps = []
    with pytest.raises(Boom):
        resilience.call_with_retry(fn, RETRY3, is_transient=lambda e: False, name="x",
                                   sleep=sleeps.append)
    assert calls["n"] == 1 and sleeps == []


def test_a_policy_of_one_attempt_never_retries():
    fn, calls = flaky(1)
    with pytest.raises(Boom):
        resilience.call_with_retry(fn, resilience.NO_RETRY, is_transient=lambda e: True,
                                   name="x", sleep=lambda s: None)
    assert calls["n"] == 1


def test_the_delay_is_capped_and_jitter_only_adds():
    policy = resilience.RetryPolicy(attempts=6, base_delay=1.0, max_delay=3.0, jitter=0.5)
    fn, _ = flaky(5)
    sleeps = []
    resilience.call_with_retry(fn, policy, is_transient=lambda e: True, name="x",
                               sleep=sleeps.append, rng=lambda: 1.0)
    assert sleeps == [1.5, 3.0, 4.5, 4.5, 4.5]  # 1,2 then capped at 3, each x1.5


def test_a_retry_is_logged_with_the_dependency_and_error_class_only(caplog):
    fn, _ = flaky(1, error=Boom("patient HbA1c 7.2 in the message"))
    with caplog.at_level(logging.WARNING, logger="app.resilience"):
        resilience.call_with_retry(fn, RETRY3, is_transient=lambda e: True, name="llm",
                                   sleep=lambda s: None)
    [record] = caplog.records
    assert (record.dependency, record.attempt, record.error_type) == ("llm", 1, "Boom")
    assert "HbA1c" not in str(record.__dict__)


def http_status_error(status):
    request = httpx.Request("POST", "http://x")
    return httpx.HTTPStatusError(
        "e", request=request, response=httpx.Response(status, request=request))


def test_http_failures_that_are_worth_retrying():
    for transient in (httpx.ConnectError("x"), httpx.ConnectTimeout("x"),
                      httpx.RemoteProtocolError("x"), http_status_error(500),
                      http_status_error(503), http_status_error(429)):
        assert resilience.is_transient_http(transient), transient


def test_http_failures_that_are_not():
    for permanent in (httpx.ReadTimeout("slow model"), http_status_error(400),
                      http_status_error(401), http_status_error(404), ValueError("x")):
        assert not resilience.is_transient_http(permanent), permanent


def test_file_failures_that_are_worth_retrying_and_those_that_are_not():
    assert resilience.is_transient_os(PermissionError())
    assert resilience.is_transient_os(BlockingIOError())
    assert resilience.is_transient_os(OSError(errno.EBUSY, "busy"))
    assert not resilience.is_transient_os(FileNotFoundError())
    assert not resilience.is_transient_os(IsADirectoryError())
    assert not resilience.is_transient_os(OSError(errno.ENOSPC, "disk full"))
    assert not resilience.is_transient_os(ValueError())


def test_database_failures_that_are_worth_retrying():
    assert resilience.is_transient_db(OperationalError("s", {}, Exception("connection refused")))
    assert not resilience.is_transient_db(ValueError("x"))


def test_every_dependency_in_the_ticket_has_a_stated_policy_and_reason():
    assert set(resilience.POLICIES) == {"llm", "ocr", "embeddings", "storage", "database"}
    for policy in resilience.POLICIES.values():
        assert policy.why and policy.retry.attempts >= 1


def test_the_expensive_or_deterministic_calls_do_not_retry_a_timeout():
    assert resilience.POLICIES["ocr"].retry.attempts == 1  # a page that times out will again
    assert resilience.POLICIES["llm"].retry.attempts == 3
    assert not resilience.is_transient_http(httpx.ReadTimeout("slow"))
    assert resilience.POLICIES["llm"].timeout_s == settings.llm_timeout_seconds
