from fastapi import APIRouter, File, UploadFile

router = APIRouter()

# In-memory for now; replaced by the database in a later task.
documents: list[dict] = []


@router.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    content = await file.read()
    doc = {
        "id": len(documents) + 1,
        "filename": file.filename,
        "size_bytes": len(content),
    }
    documents.append(doc)
    return doc


@router.get("/documents")
def list_documents():
    return documents
