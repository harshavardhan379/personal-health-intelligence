"""Small real files for tests that upload: a text PDF, a PNG and a JPEG that pass upload validation
(tests/unit/test_upload_validation.py)."""

import io
import logging


def make_pdf(text: str = "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10", pages: int = 1) -> bytes:
    """A minimal valid PDF whose every page carries `text` (Helvetica, one line)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    kids = " ".join(f"{6 + i} 0 R" for i in range(pages))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Font << /F1 3 0 R >> >>",
    ]
    for _ in range(pages):
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
                    b"/Resources 5 0 R >>")
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def make_image(fmt: str = "PNG", size: tuple = (40, 30)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 220, 240)).save(buffer, format=fmt)
    return buffer.getvalue()


def app_log_text(records) -> str:
    """What the application chose to put in these log records: the message and the fields it added
    with `extra=`. The standard LogRecord attributes (created, relativeCreated, msecs, thread ids,
    ...) are left out: they hold random-looking numbers, and a test that looks for "7.2" in them
    fails one run in a few hundred by coincidence (that is how this helper came to exist)."""
    standard = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "request_id"}
    parts = []
    for record in records:
        parts.append(record.getMessage())
        parts.extend(f"{k}={v}" for k, v in record.__dict__.items() if k not in standard)
    return "\n".join(parts)
