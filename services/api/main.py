from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Personal Health Intelligence API")

# Lets the HTML page (opened from a file or another port) call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo only; restrict this later
    allow_methods=["*"],
    allow_headers=["*"],
)

documents = []  # in-memory for now; the database comes in Step 6


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    content = await file.read()
    doc = {
        "id": len(documents) + 1,
        "filename": file.filename,
        "size_bytes": len(content),
    }
    documents.append(doc)
    return doc


@app.get("/documents")
def list_documents():
    return documents