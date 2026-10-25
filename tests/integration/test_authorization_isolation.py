"""YGH-99: cross-user access to protected data is impossible, against a real Postgres.

Two layers:
  * every route in the app is found from the app itself and must refuse a caller with no login, a
    forged login or an expired one, so a route added later cannot silently skip authentication;
  * with two real users, everything one user stored is unreachable and unchanged by the other, on
    every endpoint that takes an id.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from packages.schemas.models import Document, Embedding, HealthEvent, Provenance, User
from services.api.app.core.config import settings
from services.api.main import app

# The only routes a caller without a login may use, by (method, path).
PUBLIC = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
}
SAMPLE_PARAMS = {"document_id": str(uuid.uuid4()), "event_id": str(uuid.uuid4()),
                 "test_name": "HbA1c"}


def api_routes():
    """Every (method, path) the API serves, from its OpenAPI schema: unlike app.routes, which in
    newer FastAPI versions holds lazy wrappers for included routers, the schema always lists them
    all. A route that is left out of the schema (include_in_schema=False) would be missed, so the
    size guard below also pins how many there are."""
    found = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method in operations:
            if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                found.append((method.upper(), path))
    return sorted(found)


def fill(path: str) -> str:
    for name, value in SAMPLE_PARAMS.items():
        path = path.replace("{" + name + "}", value)
    return path


def call(client, method, path, headers=None):
    kwargs = {"headers": headers or {}}
    if method in ("POST", "DELETE", "PUT", "PATCH"):
        kwargs["json"] = {}
    return client.request(method, fill(path), **kwargs)


# --- every route wants a login ---------------------------------------------------------------
def test_the_route_list_is_found_and_is_not_trivially_small():
    assert len(api_routes()) == 25  # add a route: update this, and decide if it is public


def protected():
    return [r for r in api_routes() if r not in PUBLIC]


@pytest.mark.parametrize("method,path", protected())
def test_a_protected_route_refuses_a_caller_with_no_login(stack, method, path):
    assert call(stack.client, method, path).status_code == 401


@pytest.mark.parametrize("method,path", protected())
def test_a_protected_route_refuses_a_forged_or_expired_login(stack, method, path):
    someone = str(uuid.uuid4())
    stack.register()  # a real user exists; the tokens below still must not work
    bad = {
        "wrong secret": jwt.encode({"sub": someone, "exp": datetime.now(timezone.utc)
                                    + timedelta(hours=1)}, "not the secret" * 3,
                                   algorithm="HS256"),
        "expired": jwt.encode({"sub": someone, "exp": datetime.now(timezone.utc)
                               - timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256"),
        "no signature": jwt.encode({"sub": someone}, None, algorithm="none"),
        "garbage": "not.a.token",
    }
    for label, token in bad.items():
        response = call(stack.client, method, path, {"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, label


def test_a_token_for_a_user_that_does_not_exist_is_refused(stack):
    token = jwt.encode({"sub": str(uuid.uuid4()), "exp": datetime.now(timezone.utc)
                        + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")
    response = stack.client.get("/api/v1/account", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_a_token_whose_subject_is_not_an_id_is_refused(stack):
    token = jwt.encode({"sub": "1 OR 1=1", "exp": datetime.now(timezone.utc)
                        + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")
    response = stack.client.get("/api/v1/account", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# --- two real users --------------------------------------------------------------------------
@pytest.fixture
def two_users(stack):
    alice = stack.register("alice@example.com")
    bob = stack.register("bob@example.com")
    document = stack.confirmed_document(alice, "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10",
                                        "alice-private.pdf")
    pending = stack.upload(alice, "Cholesterol: 210 mg/dL 2026-02-01", "alice-two.pdf").json()
    with stack.db() as db:
        confirmed = db.query(HealthEvent).filter_by(review_status="confirmed").one()
        waiting = db.query(HealthEvent).filter_by(review_status="pending").one()
        ids = {"event": str(confirmed.id), "pending_event": str(waiting.id)}
    return stack, alice, bob, document["id"], pending["id"], ids


def snapshot(stack):
    with stack.db() as db:
        return {
            "documents": sorted((str(d.id), d.processing_status) for d in db.query(Document)),
            "events": sorted((str(e.id), e.review_status, str(e.data)) for e in
                             db.query(HealthEvent)),
            "provenance": db.query(Provenance).count(),
            "embeddings": db.query(Embedding).count(),
        }


def test_another_user_gets_a_404_for_every_document_endpoint(two_users):
    stack, _, bob, document_id, *_ = two_users
    for method, suffix in (("GET", ""), ("GET", "/file"), ("GET", "/events"),
                           ("POST", "/reprocess"), ("DELETE", "")):
        response = stack.client.request(method, f"/api/v1/documents/{document_id}{suffix}",
                                        headers=bob)
        assert response.status_code == 404, (method, suffix)


def test_another_user_gets_a_404_for_every_event_endpoint(two_users):
    stack, _, bob, _, _, ids = two_users
    for event_id in ids.values():
        for action in ("confirm", "edit", "reject"):
            response = stack.client.post(f"/api/v1/events/{event_id}/{action}", headers=bob,
                                         json={"data": {"value": 1}})
            assert response.status_code == 404, action
        assert stack.client.get(f"/api/v1/timeline/{event_id}", headers=bob).status_code == 404


def test_all_of_that_changed_nothing(two_users):
    stack, alice, bob, document_id, _, ids = two_users
    before = snapshot(stack)
    for method, suffix in (("POST", "/reprocess"), ("DELETE", "")):
        stack.client.request(method, f"/api/v1/documents/{document_id}{suffix}", headers=bob)
    for event_id in ids.values():
        for action in ("confirm", "edit", "reject"):
            stack.client.post(f"/api/v1/events/{event_id}/{action}", headers=bob,
                              json={"data": {"value": 1}})
    assert snapshot(stack) == before
    assert stack.client.get(f"/api/v1/documents/{document_id}", headers=alice).status_code == 200


def test_the_other_users_lists_are_empty(two_users):
    stack, _, bob, *_ = two_users
    assert stack.client.get("/api/v1/documents", headers=bob).json() == []
    assert stack.client.get("/api/v1/events", headers=bob,
                            params={"status_filter": ""}).json() == []
    assert stack.client.get("/api/v1/timeline", headers=bob).json() == []
    assert stack.client.get("/api/v1/search", headers=bob, params={"q": "HbA1c"}).json() == []
    trend = stack.client.get("/api/v1/trends/HbA1c", headers=bob).json()
    assert trend["points"] == []
    account = stack.client.get("/api/v1/account", headers=bob).json()
    assert account["documents"] == 0 and sum(account["events"].values()) == 0


def test_the_assistant_never_sees_another_users_facts(two_users):
    stack, alice, bob, *_ = two_users
    stack.llm.reply = {"answer": "Your HbA1c was 7.2%.", "claims": [], "uncertainty": None}
    response = stack.ask(bob, "What is my latest HbA1c result?")
    assert response.status_code == 200
    body = response.json()
    assert body["evidence"] == [] and "7.2" not in body["answer"]
    assert stack.llm.calls == [], "with no facts of their own, the model is not even asked"
    # and the owner does get her own fact
    stack.llm.reply = {"answer": "Your HbA1c was 7.2%.",
                       "claims": [{"text": "HbA1c was 7.2%", "citations": [1]}],
                       "uncertainty": None}
    mine = stack.ask(alice, "What is my latest HbA1c result?").json()
    assert mine["evidence"] and "7.2" in mine["answer"]


def test_a_search_cannot_be_used_to_reach_another_users_data(two_users):
    stack, _, bob, *_ = two_users
    queries = ("alice-private", "7.2", "Cholesterol", "' OR '1'='1", "%", "HbA1c; DROP TABLE users")
    for query in queries:
        response = stack.client.get("/api/v1/search", headers=bob, params={"q": query})
        assert response.status_code == 200 and response.json() == [], query


def test_injection_in_a_path_or_query_changes_nothing(two_users):
    stack, alice, *_ = two_users
    before = snapshot(stack)
    for name in ("HbA1c'; DROP TABLE health_events; --", "x' OR '1'='1", "../../etc/passwd"):
        response = stack.client.get(f"/api/v1/trends/{name}", headers=alice)
        assert response.status_code in (200, 404)
    stack.client.get("/api/v1/search", headers=alice, params={"q": "'; DELETE FROM users; --"})
    assert snapshot(stack) == before
    with stack.db() as db:
        assert db.query(User).count() == 2


def test_deleting_one_account_leaves_the_other_untouched(two_users):
    stack, alice, bob, document_id, *_ = two_users
    before = snapshot(stack)
    stack.confirmed_document(bob, "Glucose: 99 mg/dL 2026-03-01", "bob.pdf")
    response = stack.client.request("DELETE", "/api/v1/account", headers=bob,
                                    json={"password": "correct horse battery"})
    assert response.status_code == 204
    assert snapshot(stack) == before  # all of Alice's data is exactly as it was
    assert stack.client.get(f"/api/v1/documents/{document_id}", headers=alice).status_code == 200


def test_the_original_file_of_another_user_is_not_served_by_guessing_its_path(two_users):
    stack, alice, bob, document_id, *_ = two_users
    with stack.db() as db:
        stored = db.get(Document, uuid.UUID(document_id)).storage_path
    assert (stack.root / stored).exists()
    assert stack.client.get(f"/api/v1/documents/{document_id}/file", headers=bob).status_code == 404
    assert stack.client.get(f"/api/v1/documents/{document_id}/file",
                            headers=alice).status_code == 200
