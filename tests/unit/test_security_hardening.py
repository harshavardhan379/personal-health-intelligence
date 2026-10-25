"""Security hardening (YGH-115, YGH-123): what the review found and fixed, each pinned by a test.

  * logs never print an exception's message (a validation error carries the request body, which
    can hold a password) and the access log, which prints query strings, is off
  * validation errors do not echo the input (that was also a 500: bytes are not JSON)
  * error responses keep Retry-After and WWW-Authenticate
  * every response carries the security headers
  * rate limits on login, register and the assistant
  * production refuses unsafe settings and serves no API docs
  * source-level guards: no string-built SQL, no HTML injection in the web app, no secrets committed
"""

import ast
import io
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.observability import logs
from services.api.app.core import ratelimit, security
from services.api.app.core.config import DEV_JWT_SECRET, Settings, settings
from services.api.app.core.security_headers import SecurityHeadersMiddleware, security_headers
from services.api.main import app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "hunter2-correct-horse"


# --- logs never carry an exception's message -------------------------------------------------
INNER_SECRET = "inner secret: HbA1c 9.9"
OUTER_SECRET = f"outer secret: {PASSWORD}"


def raise_nested():
    # The values travel in variables: a traceback shows source lines (code), never runtime values.
    try:
        raise KeyError(INNER_SECRET)
    except KeyError as inner:
        raise ValueError(OUTER_SECRET) from inner


def captured_exception():
    try:
        raise_nested()
    except ValueError:
        return sys.exc_info()


def test_a_traceback_keeps_the_frames_and_the_class_but_not_the_message():
    text = logs.safe_traceback(captured_exception())
    assert "ValueError" in text and "KeyError" in text
    assert "raise_nested" in text and "Traceback (most recent call last)" in text
    assert PASSWORD not in text and OUTER_SECRET not in text
    assert INNER_SECRET not in text and "9.9" not in text


def test_a_chained_cause_is_shown_first_as_the_standard_library_does():
    text = logs.safe_traceback(captured_exception())
    assert text.index("KeyError") < text.index("ValueError")
    assert "direct cause" in text


def test_an_exception_with_no_cause_is_a_single_traceback():
    try:
        raise RuntimeError(PASSWORD)
    except RuntimeError:
        text = logs.safe_traceback(sys.exc_info())
    assert text.count("Traceback") == 1 and text.endswith("RuntimeError")


def test_the_json_handler_writes_that_form_into_the_log_line():
    stream = io.StringIO()
    handler = logs.build_handler(stream)
    logger = logging.getLogger("hardening.test")
    logger.handlers, logger.propagate = [handler], False
    try:
        try:
            raise ValueError(f"body was password={PASSWORD}")
        except ValueError:
            logger.error("unhandled", exc_info=True)
    finally:
        logger.handlers = []
    line = stream.getvalue()
    assert "ValueError" in line and PASSWORD not in line
    assert json.loads(line)["message"] == "unhandled"


def test_the_uvicorn_access_log_is_switched_off():
    logs.configure_logging("INFO")
    access = logging.getLogger("uvicorn.access")
    assert access.disabled and not access.propagate and access.handlers == []


# --- error bodies ----------------------------------------------------------------------------
@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_a_form_encoded_login_is_a_422_not_a_500_and_does_not_echo_the_body(client):
    response = client.post("/api/v1/auth/login", data={"username": "a", "password": PASSWORD})
    assert response.status_code == 422
    assert PASSWORD not in response.text and "input" not in json.dumps(response.json())


def test_validation_details_say_where_and_what_but_never_the_value(client):
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email",
                                                       "password": PASSWORD})
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details and all(set(d) == {"loc", "msg", "type"} for d in details)
    assert "email" in details[0]["loc"]
    assert "not-an-email" not in response.text and PASSWORD not in response.text


def test_a_429_keeps_its_retry_after_and_a_401_its_www_authenticate(client):
    unauthenticated = client.get("/api/v1/account")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    ratelimit.login_failures.reset()
    try:
        for _ in range(ratelimit.LOGIN_FAILURES):
            ratelimit.login_failures.record("testclient|limited@example.com")
        limited = client.post("/api/v1/auth/login", json={"email": "limited@example.com",
                                                          "password": PASSWORD})
    finally:
        ratelimit.login_failures.reset()
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0


# --- security headers ------------------------------------------------------------------------
EXPECTED = {
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def test_every_response_carries_the_security_headers(client):
    for response in (client.get("/api/v1/health"), client.get("/api/v1/account"),
                     client.get("/api/v1/nowhere")):
        for name, value in EXPECTED.items():
            assert response.headers[name] == value, (response.status_code, name)
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_even_the_crash_response_carries_them():
    crashing = FastAPI()

    @crashing.get("/boom")
    def boom():
        raise RuntimeError(PASSWORD)

    from services.api.app.core.errors import unhandled_error_handler
    crashing.add_exception_handler(Exception, unhandled_error_handler)
    response = TestClient(crashing, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500 and PASSWORD not in response.text
    for name, value in EXPECTED.items():
        assert response.headers[name] == value, name


def test_the_hsts_header_is_for_production_only():
    assert "Strict-Transport-Security" not in security_headers(False)
    assert security_headers(True)["Strict-Transport-Security"].startswith("max-age=")


def test_a_header_a_route_set_itself_is_kept_and_the_docs_page_has_no_csp():
    small = FastAPI()
    small.add_middleware(SecurityHeadersMiddleware)

    @small.get("/own")
    def own():
        from fastapi.responses import Response
        return Response("x", headers={"Cache-Control": "private, max-age=5"})

    local = TestClient(small)
    assert local.get("/own").headers["cache-control"] == "private, max-age=5"
    assert local.get("/own").headers["x-content-type-options"] == "nosniff"
    docs = TestClient(app).get("/docs")
    assert docs.status_code == 200 and "content-security-policy" not in docs.headers
    assert docs.headers["x-content-type-options"] == "nosniff"


# --- rate limiting ---------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_window_allows_up_to_its_limit_then_says_how_long_to_wait():
    clock = Clock()
    window = ratelimit.SlidingWindow(3, 60, clock)
    for _ in range(3):
        assert window.retry_after("k") == 0
        window.record("k")
    assert window.retry_after("k") == 61
    clock.now += 30
    assert window.retry_after("k") == 31
    clock.now += 31
    assert window.retry_after("k") == 0


def test_the_window_slides_one_hit_at_a_time():
    clock = Clock()
    window = ratelimit.SlidingWindow(2, 10, clock)
    window.record("k")
    clock.now += 6
    window.record("k")
    assert window.retry_after("k") > 0
    clock.now += 5  # the first hit is 11 s old, the second 5 s
    assert window.retry_after("k") == 0
    window.record("k")
    assert window.retry_after("k") > 0


def test_keys_are_independent_and_reset_clears_one_or_all():
    window = ratelimit.SlidingWindow(1, 60, Clock())
    window.record("a")
    assert window.retry_after("a") > 0 and window.retry_after("b") == 0
    window.reset("a")
    assert window.retry_after("a") == 0
    window.record("a")
    window.record("b")
    window.reset()
    assert window.retry_after("a") == 0 and window.retry_after("b") == 0


def test_memory_is_bounded_by_forgetting_the_oldest_key(monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 3)
    window = ratelimit.SlidingWindow(1, 60, Clock())
    for key in ("a", "b", "c", "d"):
        window.record(key)
    assert len(window._hits) == 3 and "a" not in window._hits


def test_refusing_does_not_count_and_counting_does():
    window = ratelimit.SlidingWindow(1, 60, Clock())
    ratelimit.refuse_if_limited(window, "k")
    ratelimit.refuse_if_limited(window, "k")  # still free: looking costs nothing
    ratelimit.count_and_refuse_if_limited(window, "k")
    with pytest.raises(Exception) as info:
        ratelimit.count_and_refuse_if_limited(window, "k")
    assert info.value.status_code == 429 and "Retry-After" in info.value.headers


@pytest.fixture
def accounts(monkeypatch):
    """The auth routes over an in-memory database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from packages.schemas.db import get_db
    from packages.schemas.models import User

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    User.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: session
    ratelimit.login_failures.reset()
    ratelimit.register_attempts.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()
    ratelimit.login_failures.reset()
    ratelimit.register_attempts.reset()
    session.close()


def login(client, email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_five_wrong_passwords_lock_that_account_from_that_address(accounts):
    accounts.post("/api/v1/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    codes = [login(accounts, "a@example.com", "wrong-password").status_code
             for _ in range(ratelimit.LOGIN_FAILURES)]
    assert codes == [401] * ratelimit.LOGIN_FAILURES
    assert login(accounts, "a@example.com", PASSWORD).status_code == 429  # even the right one


def test_a_correct_login_clears_the_count(accounts):
    accounts.post("/api/v1/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    for _ in range(ratelimit.LOGIN_FAILURES - 1):
        login(accounts, "a@example.com", "wrong-password")
    assert login(accounts, "a@example.com", PASSWORD).status_code == 200
    for _ in range(ratelimit.LOGIN_FAILURES - 1):
        assert login(accounts, "a@example.com", "wrong-password").status_code == 401


def test_the_limit_is_per_account_and_does_not_lock_everyone_out(accounts):
    for email in ("a@example.com", "b@example.com"):
        accounts.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    for _ in range(ratelimit.LOGIN_FAILURES):
        login(accounts, "a@example.com", "wrong-password")
    assert login(accounts, "b@example.com", PASSWORD).status_code == 200


def test_an_unknown_email_is_limited_too_and_the_message_does_not_reveal_it(accounts):
    codes = [login(accounts, "ghost@example.com", "x" * 10) for _ in range(6)]
    assert [c.status_code for c in codes] == [401] * 5 + [429]
    assert "ghost@example.com" not in codes[-1].text


def test_registration_is_limited_per_address(accounts, monkeypatch):
    monkeypatch.setattr(ratelimit.register_attempts, "limit", 2)
    codes = [accounts.post("/api/v1/auth/register",
                           json={"email": f"user{i}@example.com", "password": PASSWORD}).status_code
             for i in range(3)]
    assert codes == [201, 201, 429]


def test_passwords_must_be_8_characters_and_at_most_72_bytes(accounts):
    short = accounts.post("/api/v1/auth/register", json={"email": "a@example.com",
                                                         "password": "short"})
    assert short.status_code == 422 and "at least 8" in short.json()["error"]["message"]
    long_ = accounts.post("/api/v1/auth/register", json={"email": "b@example.com",
                                                         "password": "x" * 73})
    assert long_.status_code == 422 and "at most 72" in long_.json()["error"]["message"]
    multibyte = accounts.post("/api/v1/auth/register",
                              json={"email": "c@example.com", "password": "é" * 37})  # 74 bytes
    assert multibyte.status_code == 422
    exactly = accounts.post("/api/v1/auth/register",
                            json={"email": "d@example.com", "password": "x" * 72})
    assert exactly.status_code == 201


# --- settings per environment ----------------------------------------------------------------
def safe_production(**overrides):
    values = dict(environment="production", jwt_secret="s" * 40,
                  database_url="postgresql+psycopg://phi:strong@db:5432/phi",
                  cors_origins=["https://app.example.com"], storage_dir="/data/storage",
                  llm_provider="ollama")
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_a_safe_production_configuration_has_no_problems():
    assert safe_production().production_problems() == []


@pytest.mark.parametrize("override,fragment", [
    ({"jwt_secret": DEV_JWT_SECRET}, "JWT_SECRET is the development default"),
    ({"jwt_secret": "short"}, "at least 32 characters"),
    ({"database_url": "postgresql+psycopg://phi:phi_dev_password@db/phi"}, "DATABASE_URL"),
    ({"cors_origins": []}, "CORS_ORIGINS is empty"),
    ({"cors_origins": ["*"]}, "not '*'"),
    ({"storage_dir": "storage"}, "STORAGE_DIR"),
    ({"storage_dir": "./data"}, "STORAGE_DIR"),
    ({"llm_provider": "openai", "openai_api_key": ""}, "OPENAI_API_KEY"),
    ({"llm_provider": "grok", "xai_api_key": ""}, "XAI_API_KEY"),
    ({"environment": "prod"}, "ENVIRONMENT must be one of"),
])
def test_each_unsafe_production_setting_is_named(override, fragment):
    problems = safe_production(**override).production_problems()
    assert any(fragment in p for p in problems), problems


def test_a_hosted_provider_with_its_key_is_fine():
    assert safe_production(llm_provider="openai", openai_api_key="k").production_problems() == []


def test_the_problem_messages_never_contain_the_values():
    problems = safe_production(jwt_secret="topsecretvalue",
                               database_url="postgresql://u:phi_dev_password@h/d").production_problems()
    assert "topsecretvalue" not in " ".join(problems)
    assert "phi_dev_password" not in " ".join(problems)


def test_only_production_is_production():
    assert safe_production().is_production
    assert not Settings(_env_file=None, environment="development").is_production
    assert not Settings(_env_file=None, environment="test").is_production


def test_the_app_refuses_to_start_in_production_with_unsafe_settings(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", DEV_JWT_SECRET)
    with pytest.raises(RuntimeError, match="Unsafe production settings.*JWT_SECRET"):
        with TestClient(app):
            pass


def test_the_app_starts_in_production_when_the_settings_are_safe(monkeypatch):
    safe = safe_production()
    for name in ("environment", "jwt_secret", "database_url", "cors_origins", "storage_dir"):
        monkeypatch.setattr(settings, name, getattr(safe, name))
    from services.api import main
    monkeypatch.setattr(main, "SessionLocal", lambda: (_ for _ in ()).throw(OSError("no db")))
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_production_serves_no_api_docs():
    code = (
        "from fastapi.testclient import TestClient\n"
        "from services.api.main import app\n"
        "c = TestClient(app)\n"
        "print([c.get(p).status_code for p in ('/docs', '/redoc', '/openapi.json')])\n"
    )
    env = {"PATH": "", "SYSTEMROOT": "C:/Windows", "ENVIRONMENT": "production",
           "JWT_SECRET": "s" * 40, "PYTHONPATH": str(ROOT)}
    import os
    env = {**os.environ, **env}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                         env=env, timeout=60)
    assert out.stdout.strip().splitlines()[-1] == "[404, 404, 404]", out.stderr[-500:]


# --- the login token -------------------------------------------------------------------------
def test_tokens_use_the_configured_secret_and_lifetime(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "another-secret-of-sufficient-length-1")
    monkeypatch.setattr(settings, "jwt_expire_minutes", 5)
    token = security.create_access_token("user-1")
    claims = jwt.decode(token, "another-secret-of-sufficient-length-1", algorithms=["HS256"])
    assert claims["sub"] == "user-1"
    lifetime = datetime.fromtimestamp(claims["exp"], timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(minutes=4) < lifetime <= timedelta(minutes=5)
    assert security.decode_access_token(token) == "user-1"
    monkeypatch.setattr(settings, "jwt_secret", "a-different-secret-of-sufficient-len-2")
    assert security.decode_access_token(token) is None  # signed with the old secret


def test_an_expired_or_unsigned_token_decodes_to_nothing():
    expired = jwt.encode({"sub": "u", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
                         settings.jwt_secret, algorithm="HS256")
    unsigned = jwt.encode({"sub": "u"}, None, algorithm="none")
    assert security.decode_access_token(expired) is None
    assert security.decode_access_token(unsigned) is None


# --- source-level guards ---------------------------------------------------------------------
def python_files(*roots):
    for root in roots:
        yield from (ROOT / root).rglob("*.py")


def built_strings(call: ast.Call):
    """Does this call's first argument build a string from parts (f-string, +, %, .format)?"""
    if not call.args:
        return False
    arg = call.args[0]
    if isinstance(arg, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in arg.values)
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)):
        return True
    return (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "format")


def sql_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if name in ("text", "execute", "exec_driver_sql"):
                yield node


def test_no_sql_is_built_from_strings_in_the_product():
    offenders = []
    for path in python_files("services", "packages"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.relative_to(ROOT)}:{n.lineno}" for n in sql_calls(tree)
                      if built_strings(n)]
    assert offenders == [], offenders


def test_the_sql_guard_does_catch_a_built_query():
    for source in ('text(f"SELECT * FROM t WHERE a = {x}")', 'db.execute("SELECT " + x)',
                   'db.execute("SELECT %s" % x)', 'text("SELECT {}".format(x))'):
        call = next(sql_calls(ast.parse(source)))
        assert built_strings(call), source
    for source in ('text("SELECT 1")', 'db.execute(select(User))', 'db.execute(delete(User))'):
        call = next(sql_calls(ast.parse(source)))
        assert not built_strings(call), source


WEB_SRC = ROOT / "apps" / "web" / "src"
DANGEROUS = re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=|document\.write|\beval\(|"
                       r"new Function\(")


def test_the_web_app_never_injects_html_or_evaluates_strings():
    hits = []
    for path in list(WEB_SRC.rglob("*.ts")) + list(WEB_SRC.rglob("*.tsx")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DANGEROUS.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}")
    assert hits == [], hits


SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "xAI key": re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "Slack token": re.compile(r"\bxox[abp]-[A-Za-z0-9-]{10,}"),
}


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available")
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [ROOT / name for name in out.stdout.splitlines()]


def test_no_secret_looking_value_is_committed():
    hits = []
    for path in tracked_files():
        if path.suffix in (".png", ".jpg", ".pdf", ".ico", ".woff2", ".lock") or not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(body):
                hits.append(f"{path.relative_to(ROOT)}: {label}")
    assert hits == [], hits


def test_the_secret_scanner_does_catch_each_kind():
    samples = {
        "OpenAI-style key": "sk-" + "a" * 30,
        "xAI key": "xai-" + "b" * 30,
        "AWS access key": "AKIA" + "C" * 16,
        "private key block": "-----BEGIN RSA PRIVATE KEY-----",
        "GitHub token": "ghp_" + "d" * 36,
        "Slack token": "xoxb-" + "1" * 12,
    }
    for label, sample in samples.items():
        assert SECRET_PATTERNS[label].search(sample), label


def test_the_environment_files_that_hold_real_values_are_not_tracked():
    names = {p.name for p in tracked_files()}
    assert ".env" not in names and ".env.production" not in names
    assert not any(n.startswith(".env.") and not n.endswith(".example") for n in names)
