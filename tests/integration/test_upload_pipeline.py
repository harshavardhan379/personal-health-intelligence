"""YGH-97: upload, processing, extraction, review, confirmation and the timeline update, end to end
against a real Postgres (see conftest.py)."""

from sqlalchemy import text

from packages.schemas.models import Document, DocumentPage, Embedding, HealthEvent, Provenance
from tests.helpers import make_pdf

LAB = "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10"


def timeline(stack, headers):
    response = stack.client.get("/api/v1/timeline", headers=headers)
    assert response.status_code == 200
    return response.json()


def count(stack, model, **filters):
    with stack.db() as db:
        return db.query(model).filter_by(**filters).count()


def test_an_upload_is_processed_and_its_facts_wait_for_review(stack):
    headers = stack.register()
    response = stack.upload(headers, LAB)
    assert response.status_code == 201
    assert response.json()["processing_status"] == "uploaded"

    document = stack.client.get(f"/api/v1/documents/{response.json()['id']}",
                                headers=headers).json()
    assert document["processing_status"] == "processed"
    assert document["classification"] is not None and document["failed_stage"] is None

    events = stack.events(headers)
    assert len(events) == 1
    assert events[0]["event_type"] == "lab" and events[0]["data"]["test_name"] == "HbA1c"
    assert events[0]["data"]["value"] == 7.2 and events[0]["event_date"] == "2026-01-10"
    assert events[0]["review_status"] == "pending" and events[0]["source_pages"] == [1]


def test_unreviewed_facts_are_not_on_the_timeline_and_not_searchable(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    assert timeline(stack, headers) == []
    assert count(stack, Embedding) == 0
    assert stack.client.get("/api/v1/search", headers=headers, params={"q": "HbA1c"}).json() == []


def test_the_document_page_data_shows_each_fact_with_where_it_was_found(stack):
    headers = stack.register()
    document_id = stack.upload(headers, LAB).json()["id"]
    events = stack.client.get(f"/api/v1/documents/{document_id}/events", headers=headers).json()
    assert len(events) == 1 and events[0]["provenance"][0]["page_number"] == 1
    assert "HbA1c" in events[0]["provenance"][0]["quote"]


def test_confirming_a_fact_puts_it_on_the_timeline_and_makes_it_searchable(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    event_id = stack.events(headers)[0]["id"]

    response = stack.client.post(f"/api/v1/events/{event_id}/confirm", headers=headers)
    assert response.status_code == 200 and response.json()["review_status"] == "confirmed"

    items = timeline(stack, headers)
    assert [i["id"] for i in items] == [event_id]
    assert items[0]["provenance"][0]["page_number"] == 1
    assert count(stack, Embedding) == 1
    found = stack.client.get("/api/v1/search", headers=headers, params={"q": "HbA1c"}).json()
    assert found and event_id in found[0]["event_ids"]
    assert stack.events(headers) == []  # nothing left to review


def test_a_confirmed_fact_cannot_be_confirmed_again(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    event_id = stack.events(headers)[0]["id"]
    stack.client.post(f"/api/v1/events/{event_id}/confirm", headers=headers)
    again = stack.client.post(f"/api/v1/events/{event_id}/confirm", headers=headers)
    assert again.status_code == 409
    assert count(stack, Embedding) == 1


def test_an_edited_fact_replaces_the_value_on_the_timeline_and_in_search(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    event = stack.events(headers)[0]
    edited = stack.client.post(f"/api/v1/events/{event['id']}/edit", headers=headers,
                               json={"data": {**event["data"], "value": 6.9}})
    assert edited.status_code == 200 and edited.json()["review_status"] == "edited"
    assert timeline(stack, headers)[0]["data"]["value"] == 6.9
    with stack.db() as db:
        assert "6.9" in db.query(Embedding).one().chunk_text


def test_an_edit_that_is_not_a_small_set_of_plain_fields_is_refused(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    event_id = stack.events(headers)[0]["id"]
    too_many = {f"k{i}": 1 for i in range(31)}
    too_big = {"note": "x" * 9000}
    for payload in (too_many, too_big):
        response = stack.client.post(f"/api/v1/events/{event_id}/edit", headers=headers,
                                     json={"data": payload})
        assert response.status_code == 422
    assert stack.events(headers)[0]["review_status"] == "pending"


def test_an_edit_of_exactly_the_most_fields_allowed_is_accepted(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    event_id = stack.events(headers)[0]["id"]
    thirty = {f"k{i}": i for i in range(30)}
    response = stack.client.post(f"/api/v1/events/{event_id}/edit", headers=headers,
                                 json={"data": thirty})
    assert response.status_code == 200


def test_a_rejected_fact_never_reaches_the_timeline_or_search(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    event_id = stack.events(headers)[0]["id"]
    response = stack.client.post(f"/api/v1/events/{event_id}/reject", headers=headers)
    assert response.status_code == 200 and response.json()["review_status"] == "rejected"
    assert timeline(stack, headers) == [] and count(stack, Embedding) == 0


def test_uploading_the_same_report_twice_shows_the_fact_once(stack):
    headers = stack.register()
    stack.confirmed_document(headers, LAB, "first.pdf")
    stack.confirmed_document(headers, LAB, "second.pdf")
    assert count(stack, HealthEvent) == 2  # both are kept as records
    assert len(timeline(stack, headers)) == 1  # one fact on the timeline


def test_every_page_is_stored_with_its_text_and_each_fact_keeps_its_page(stack):
    headers = stack.register()
    document_id = stack.upload(headers, pdf=make_pdf(LAB, pages=3)).json()["id"]
    with stack.db() as db:
        pages = db.query(DocumentPage).order_by(DocumentPage.page_number).all()
        assert [p.page_number for p in pages] == [1, 2, 3]
        assert all("HbA1c" in p.text and not p.is_ocr for p in pages)
        assert db.query(Provenance).count() == db.query(HealthEvent).count() == 3
    assert stack.client.get(f"/api/v1/documents/{document_id}",
                            headers=headers).json()["processing_status"] == "processed"


def test_a_failure_part_way_leaves_nothing_behind_and_a_retry_completes_it(stack, monkeypatch):
    headers = stack.register()
    real = __import__("services.api.app.core.pipeline", fromlist=["extract_all"]).extract_all

    def broken(pages):
        raise RuntimeError("extractor crashed")
    monkeypatch.setattr(pipeline_module(), "extract_all", broken)
    document_id = stack.upload(headers, LAB).json()["id"]
    document = stack.client.get(f"/api/v1/documents/{document_id}", headers=headers).json()
    assert (document["processing_status"], document["failed_stage"]) == ("failed", "extract")
    assert count(stack, DocumentPage) == 0 and count(stack, HealthEvent) == 0

    monkeypatch.setattr(pipeline_module(), "extract_all", real)
    retry = stack.client.post(f"/api/v1/documents/{document_id}/reprocess", headers=headers)
    assert retry.status_code == 202
    document = stack.client.get(f"/api/v1/documents/{document_id}", headers=headers).json()
    assert document["processing_status"] == "processed" and document["failed_stage"] is None
    assert len(stack.events(headers)) == 1


def pipeline_module():
    from services.api.app.core import pipeline
    return pipeline


def test_a_run_that_died_is_recovered_as_interrupted_and_can_be_retried(stack):
    headers = stack.register()
    document_id = stack.upload(headers, LAB).json()["id"]
    with stack.db() as db:
        db.execute(text("UPDATE documents SET processing_status = 'processing', "
                        "processing_started_at = now() - interval '2 hours'"))
        db.commit()
    listed = stack.client.get("/api/v1/documents", headers=headers).json()
    assert (listed[0]["processing_status"], listed[0]["failed_stage"]) == ("failed",
                                                                          "interrupted")
    assert stack.client.post(f"/api/v1/documents/{document_id}/reprocess",
                             headers=headers).status_code == 202


def test_reprocessing_never_removes_a_fact_the_user_already_confirmed(stack):
    headers = stack.register()
    document = stack.confirmed_document(headers, LAB)
    with stack.db() as db:
        db.query(Document).update({"processing_status": "failed"})
        db.commit()
    stack.client.post(f"/api/v1/documents/{document['id']}/reprocess", headers=headers)
    statuses = sorted(e["review_status"] for e in
                      stack.client.get(f"/api/v1/documents/{document['id']}/events",
                                       headers=headers).json())
    assert "confirmed" in statuses  # the confirmed fact survived the re-run


def test_the_stage_timings_are_recorded_for_the_metrics_endpoint(stack):
    headers = stack.register()
    stack.upload(headers, LAB)
    stages = stack.client.get("/api/v1/metrics", headers=headers).json()["stages"]
    for stage in ("upload", "processing", "extraction"):
        assert stages[stage]["count"] == 1 and stages[stage]["errors"] == 0, stage
