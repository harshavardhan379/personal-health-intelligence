"""Secure file storage for uploaded documents (YGH-49, YGH-117).

Rules:
- Never trust the client's filename for a path (path traversal).
- Only the allowed MIME types/extensions get past validation, and the three must agree: the
  extension, the declared type and what the bytes actually are. A file that is not what it says
  is refused before it is stored or processed (validate_upload, then validate_content).
- Files are written under a per-user subdirectory so one user's storage can be
  wiped independently (used by account/data deletion later).
"""

import io
import os
import uuid
from pathlib import Path

from packages.observability.resilience import POLICIES, call_with_retry, is_transient_os

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_FILENAME_CHARS = 255
MAX_PDF_PAGES = 100  # OCR is bounded per page, so this bounds the work one upload can queue
MAX_IMAGE_PIXELS = 50_000_000  # a small file can decode to gigabytes (a decompression bomb)

_EXTENSION_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "storage"))


class UploadValidationError(ValueError):
    pass


def _with_storage_retry(operation):
    """A briefly locked or interrupted file operation is retried (the "storage" policy, YGH-94);
    a missing file or a full disk is not, and surfaces at once."""
    return call_with_retry(
        operation,
        POLICIES["storage"].retry,
        is_transient=is_transient_os,
        name="storage",
    )


def validate_upload(filename: str, mime_type: str, size_bytes: int) -> None:
    """The cheap checks, on what the client said: name, extension, declared type, size."""
    if not filename or len(filename) > MAX_FILENAME_CHARS:
        raise UploadValidationError("The file name is missing or too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in filename):
        raise UploadValidationError("The file name contains control characters")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(f"Unsupported file extension: {ext or '(none)'}")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise UploadValidationError("Unsupported MIME type")
    if _EXTENSION_TYPES[ext] != mime_type:
        raise UploadValidationError("The file extension does not match its declared type")
    if size_bytes == 0:
        raise UploadValidationError("Uploaded file is empty")
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise UploadValidationError(
            f"File too large: {size_bytes} bytes (max {MAX_FILE_SIZE_BYTES})"
        )


def validate_content(mime_type: str, content: bytes) -> None:
    """The bytes must be what the declared type says, and well formed enough to process. Messages
    are fixed text: nothing from the file is echoed back or logged."""
    if mime_type == "application/pdf":
        _validate_pdf(content)
    elif mime_type == "image/png":
        _validate_image(content, _PNG_SIGNATURE, "PNG", ("PNG",))
    elif mime_type == "image/jpeg":
        # MPO is the multi-picture JPEG some cameras write: same signature, valid JPEG for us.
        _validate_image(content, _JPEG_SIGNATURE, "JPEG", ("JPEG", "MPO"))
    else:
        raise UploadValidationError("Unsupported MIME type")


def _validate_pdf(content: bytes) -> None:
    from pypdf import PdfReader

    if not content.startswith(b"%PDF-"):
        raise UploadValidationError("The file is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise UploadValidationError("Password-protected PDFs cannot be processed")
        pages = len(reader.pages)
    except UploadValidationError:
        raise
    except Exception as exc:  # damaged, truncated or hostile structure
        raise UploadValidationError("The PDF is damaged and cannot be read") from exc
    if pages < 1:
        raise UploadValidationError("The PDF has no pages")
    if pages > MAX_PDF_PAGES:
        raise UploadValidationError(f"The PDF has too many pages (max {MAX_PDF_PAGES})")


def _validate_image(content: bytes, signature: bytes, label: str, formats: tuple) -> None:
    from PIL import Image

    if not content.startswith(signature):
        raise UploadValidationError(f"The file is not a {label} image")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in formats:
                raise UploadValidationError(f"The file is not a {label} image")
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise UploadValidationError("The image is too large to process")
            image.verify()  # walks the file's structure without decoding the pixels
    except UploadValidationError:
        raise
    except Exception as exc:  # truncated, corrupt, or a decompression bomb
        raise UploadValidationError("The image is damaged and cannot be read") from exc


def save_upload(user_id: uuid.UUID, filename: str, content: bytes) -> tuple[str, str]:
    """Writes `content` to disk and returns (storage_path, stored_name).

    storage_path is relative to STORAGE_DIR and is what gets saved on the Document row;
    never derived from the user-supplied filename.
    """
    ext = Path(filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    user_dir = STORAGE_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    target = user_dir / stored_name
    _with_storage_retry(lambda: target.write_bytes(content))

    storage_path = f"{user_id}/{stored_name}"
    return storage_path, stored_name


def delete_user_storage(user_id: uuid.UUID) -> None:
    """Remove a user's whole upload folder (data and account deletion, YGH-119/121)."""
    import shutil

    user_dir = STORAGE_DIR / str(user_id)
    if user_dir.exists():
        _with_storage_retry(lambda: shutil.rmtree(user_dir))


def delete_stored_file(storage_path: str) -> None:
    """Remove one stored upload. A missing file is fine (already gone); a path that resolves
    outside the storage folder is refused, so a bad database row cannot delete anything else."""
    path = resolve_upload_path(storage_path)
    _with_storage_retry(lambda: path.unlink(missing_ok=True))


def resolve_upload_path(storage_path: str) -> Path:
    """The file for a stored path, refusing anything that resolves outside STORAGE_DIR
    (the path is generated by save_upload, but a database row is not a trust boundary)."""
    root = STORAGE_DIR.resolve()
    target = (STORAGE_DIR / storage_path).resolve()
    if root != target and root not in target.parents:
        raise FileNotFoundError("stored path is outside the storage folder")
    return target


def read_upload(storage_path: str) -> bytes:
    path = resolve_upload_path(storage_path)
    return _with_storage_retry(path.read_bytes)
