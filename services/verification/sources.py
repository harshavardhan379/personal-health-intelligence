"""The evidence behind an answer, in the shape the user is shown (YGH-122).

`describe_sources` numbers the retrieved evidence exactly as the model was shown it (so the
[n] markers and each claim's `citation_indices` line up) and resolves each document id to a
filename. The lookup is limited to the signed-in user's own documents: a document id that is
not theirs, or not a valid id at all, gets no filename rather than another user's.
"""

import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import Document
from packages.schemas.verification import SourceEvidence


def _as_uuid(value: str):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def describe_sources(
    db: Session, user_id: uuid.UUID, evidence: list[Evidence]
) -> list[SourceEvidence]:
    wanted = {u for u in (_as_uuid(e.document_id) for e in evidence) if u is not None}
    filenames = {}
    if wanted:
        rows = (
            db.query(Document.id, Document.filename)
            .filter(Document.user_id == user_id, Document.id.in_(wanted))
            .all()
        )
        filenames = {str(doc_id): name for doc_id, name in rows}

    return [
        SourceEvidence(
            number=number,
            text=item.text,
            document_id=item.document_id,
            filename=filenames.get(str(_as_uuid(item.document_id))),
            page=item.page,
            event_ids=list(item.event_ids),
            source=item.source,
        )
        for number, item in enumerate(evidence, start=1)
    ]
