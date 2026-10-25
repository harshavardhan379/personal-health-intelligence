"""File validation (YGH-117): unsupported formats, oversized files and malformed content are refused
before anything is stored or processed. Real PDFs and images, built in tests/helpers.py."""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.schemas.db import get_db
from packages.schemas.models import Document, User
from services.api.app.api.v1 import documents
from services.api.app.core import storage
from services.api.app.core.deps import get_current_user
from services.api.app.core.storage import (
    UploadValidationError,
    validate_content,
    validate_upload,
)
from services.api.main import app
from tests.helpers import make_image, make_pdf

SECRET = "Jane Doe HbA1c 9.9"


def refused(call, *args):
    with pytest.raises(UploadValidationError) as info:
        call(*args)
    return str(info.value)


# --- the cheap checks: what the client said ----------------------------------------------
def test_a_plain_valid_request_passes():
    validate_upload("lab.pdf", "application/pdf", 100)
    validate_upload("scan.JPG", "image/jpeg", 100)
    validate_upload("scan.jpeg", "image/jpeg", 100)
    validate_upload("scan.png", "image/png", 100)


@pytest.mark.parametrize("name", [None, "", "a" * 252 + ".pdf"])
def test_a_missing_or_overlong_file_name_is_refused(name):
    assert "file name" in refused(validate_upload, name, "application/pdf", 10)


@pytest.mark.parametrize("name", ["a\x00.pdf", "a\n.pdf", "a\x7f.pdf", "tab\t.pdf",
                                  "a\x1f.pdf", "a\x01.pdf"])
def test_control_characters_in_a_file_name_are_refused(name):
    assert "control characters" in refused(validate_upload, name, "application/pdf", 10)


@pytest.mark.parametrize(
    "name", ["virus.exe", "page.html", "archive.zip", "noextension", "x.pdf.exe"])
def test_other_extensions_are_refused(name):
    assert "Unsupported file extension" in refused(validate_upload, name, "application/pdf", 10)


def test_an_unsupported_declared_type_is_refused():
    assert "Unsupported MIME type" == refused(validate_upload, "a.pdf", "text/html", 10)


@pytest.mark.parametrize("name,mime", [
    ("a.pdf", "image/png"), ("a.png", "application/pdf"), ("a.jpg", "image/png"),
    ("a.png", "image/jpeg"),
])
def test_the_extension_and_the_declared_type_must_agree(name, mime):
    assert "does not match" in refused(validate_upload, name, mime, 10)


def test_an_empty_file_and_an_oversized_one_are_refused():
    assert "empty" in refused(validate_upload, "a.pdf", "application/pdf", 0)
    too_big = storage.MAX_FILE_SIZE_BYTES + 1
    assert "too large" in refused(validate_upload, "a.pdf", "application/pdf", too_big)
    validate_upload("a.pdf", "application/pdf", storage.MAX_FILE_SIZE_BYTES)


# --- the bytes must be what was declared ----------------------------------------------------
def test_real_files_of_each_type_pass():
    validate_content("application/pdf", make_pdf())
    validate_content("application/pdf", make_pdf(pages=3))
    validate_content("image/png", make_image("PNG"))
    validate_content("image/jpeg", make_image("JPEG"))


def test_text_declared_as_a_pdf_is_refused():
    assert "not a PDF" in refused(validate_content, "application/pdf", b"<html>" + SECRET.encode())


def test_a_pdf_header_with_nothing_behind_it_is_refused():
    assert "damaged" in refused(validate_content, "application/pdf", b"%PDF-1.4 " + SECRET.encode())


def test_a_truncated_pdf_is_refused():
    pdf = make_pdf()
    assert "damaged" in refused(validate_content, "application/pdf", pdf[: len(pdf) // 2])


def test_a_pdf_with_no_pages_is_refused():
    writer, buffer = PdfWriter(), io.BytesIO()
    writer.write(buffer)
    assert "no pages" in refused(validate_content, "application/pdf", buffer.getvalue())


def test_a_password_protected_pdf_is_refused():
    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret")
    writer.write(buffer)
    assert "Password-protected" in refused(validate_content, "application/pdf", buffer.getvalue())


def test_a_pdf_with_too_many_pages_is_refused(monkeypatch):
    monkeypatch.setattr(storage, "MAX_PDF_PAGES", 2)
    validate_content("application/pdf", make_pdf(pages=2))
    assert "too many pages" in refused(validate_content, "application/pdf", make_pdf(pages=3))


def test_an_image_must_carry_its_own_signature():
    assert "not a PNG" in refused(validate_content, "image/png", make_image("JPEG"))
    assert "not a JPEG" in refused(validate_content, "image/jpeg", make_image("PNG"))
    assert "not a PNG" in refused(validate_content, "image/png", b"GIF89a" + b"0" * 50)


def test_a_multi_picture_jpeg_from_a_camera_is_accepted_as_a_jpeg():
    """Same JPEG signature, but Pillow calls the format MPO: a real photo, not a refusal."""
    first = Image.new("RGB", (40, 30), (10, 20, 30))
    second = Image.new("RGB", (40, 30), (200, 20, 30))
    buffer = io.BytesIO()
    first.save(buffer, format="MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(buffer.getvalue())).format == "MPO"
    validate_content("image/jpeg", buffer.getvalue())


def test_other_image_formats_with_a_jpeg_signature_are_not_a_free_pass(monkeypatch):
    """The format Pillow finds must be one we accept, not just any: shrink the list to see it."""
    monkeypatch.setattr(storage, "_JPEG_SIGNATURE", b"\x89PNG")  # a PNG now passes the signature
    assert "not a JPEG" in refused(validate_content, "image/jpeg", make_image("PNG"))


def test_a_pdf_declared_as_an_image_is_refused():
    assert "not a PNG" in refused(validate_content, "image/png", make_pdf())


def test_a_truncated_image_is_refused():
    png = make_image("PNG", (200, 200))
    assert "damaged" in refused(validate_content, "image/png", png[: len(png) // 2])
    assert "damaged" in refused(validate_content, "image/png", storage._PNG_SIGNATURE + b"junk")


def test_an_image_that_decodes_to_too_many_pixels_is_refused(monkeypatch):
    monkeypatch.setattr(storage, "MAX_IMAGE_PIXELS", 1000)
    validate_content("image/png", make_image("PNG", (30, 30)))
    assert "too large" in refused(validate_content, "image/png", make_image("PNG", (40, 30)))


def test_an_unknown_declared_type_is_refused_here_too():
    assert "Unsupported MIME type" == refused(validate_content, "text/plain", b"hi")


def test_refusals_never_echo_the_file(monkeypatch):
    for mime, content in (("application/pdf", b"%PDF-1.4 " + SECRET.encode()),
                          ("image/png", storage._PNG_SIGNATURE + SECRET.encode()),
                          ("application/pdf", SECRET.encode())):
        assert SECRET not in refused(validate_content, mime, content)


# --- at the endpoint: refused before anything is stored -------------------------------------
@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (User, Document):
        table.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    me = User(id=uuid.uuid4(), email="me@example.com", hashed_password="x")
    session.add(me)
    session.commit()
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(documents, "run_pipeline", lambda *args: None)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: me
    yield session, tmp_path, TestClient(app)
    app.dependency_overrides.clear()
    session.close()


def post(client, name, mime, content):
    return client.post("/api/v1/documents", files={"file": (name, content, mime)})


def test_a_valid_upload_is_accepted_and_stored(world):
    session, root, client = world
    response = post(client, "lab.pdf", "application/pdf", make_pdf())
    assert response.status_code == 201
    assert session.query(Document).count() == 1 and any(root.rglob("*.pdf"))


@pytest.mark.parametrize("name,mime,content", [
    ("lab.pdf", "application/pdf", b"not a pdf at all"),
    ("lab.pdf", "application/pdf", b"%PDF-1.4 broken"),
    ("lab.png", "image/png", b"\x89PNG\r\n\x1a\nbroken"),
    ("lab.pdf", "image/png", make_image("PNG")),
    ("lab.exe", "application/pdf", make_pdf()),
    ("lab.pdf", "text/html", make_pdf()),
])
def test_a_refused_upload_leaves_no_record_and_no_file(world, name, mime, content):
    session, root, client = world
    response = post(client, name, mime, content)
    assert response.status_code == 422
    assert session.query(Document).count() == 0
    assert not [p for p in root.rglob("*") if p.is_file()]


def test_the_endpoint_refusal_does_not_echo_the_file(world):
    _, _, client = world
    response = post(client, "lab.pdf", "application/pdf", b"%PDF-1.4 " + SECRET.encode())
    assert SECRET not in response.text


def test_an_oversized_body_is_refused_without_reading_it_all(world, monkeypatch):
    _, root, client = world
    monkeypatch.setattr(documents, "MAX_FILE_SIZE_BYTES", 1024 * 1024)
    big = make_pdf() + b" " * (3 * 1024 * 1024)
    response = post(client, "lab.pdf", "application/pdf", big)
    assert response.status_code == 422 and "too large" in response.json()["error"]["message"]
    assert not [p for p in root.rglob("*") if p.is_file()]


def test_a_body_exactly_at_the_limit_is_read_in_full(world, monkeypatch):
    session, _, client = world
    pdf = make_pdf()
    monkeypatch.setattr(documents, "MAX_FILE_SIZE_BYTES", len(pdf))
    assert post(client, "lab.pdf", "application/pdf", pdf).status_code == 201
    assert session.query(Document).count() == 1


def test_a_file_without_a_name_is_a_clean_422(world):
    _, _, client = world
    response = client.post("/api/v1/documents", files={"file": ("", make_pdf(), "application/pdf")})
    assert response.status_code == 422
