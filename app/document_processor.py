import os
import json
from .models import db, Chunk, File


# -------- TEXT EXTRACTION (LIGHTWEIGHT) -------- #

def extract_text(file_path):
    """Extracts text from different formats with minimal RAM usage."""

    ext = os.path.splitext(file_path)[1].lower()
    text = ""

    try:
        # PDF — lazy import
        if ext == ".pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
            text = "\n".join(parts)
            return text

        # TXT / MD
        elif ext in [".txt", ".md"]:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()

        # DOCX — lazy import
        elif ext == ".docx":
            from docx import Document
            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs)

        # PPTX — lazy import
        elif ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(file_path)
            parts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        parts.append(shape.text)
            return "\n".join(parts)

        # CSV / XLSX — lazy import + no full df sync
        elif ext in [".xlsx", ".csv"]:
            import pandas as pd
            df = pd.read_excel(file_path) if ext == ".xlsx" else pd.read_csv(file_path)
            return df.to_string(index=False)

        # HTML — lazy import
        elif ext == ".html":
            from bs4 import BeautifulSoup
            with open(file_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            return soup.get_text(separator="\n")

        # JSON
        elif ext == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return json.dumps(data, indent=2)

        else:
            return "Unsupported file type"

    except Exception as e:
        return f"Error extracting text: {e}"


# -------- TEXT CHUNKING -------- #

def chunk_text(text, chunk_size=1000, overlap=200):
    chunks = []
    start = 0
    ln = len(text)
    while start < ln:
        end = start + chunk_size
        chunks.append((text[start:end], start, end))
        start = end - overlap
    return chunks


# -------- PROCESS FILE INTO DB + FAISS -------- #

def process_file(file_id, user_id, upload_folder="data/uploads"):
    file = File.query.get(file_id)
    file_path = file.path

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{file_path} does not exist.")

    # Extract + chunk
    raw_text = extract_text(file_path)
    chunked = chunk_text(raw_text)
    chunk_texts = [c[0] for c in chunked]   # smaller list

    # Lazy load heavy modules
    from .embeddings import EmbeddingGenerator
    from .faiss_index import FaissIndex

    embedder = EmbeddingGenerator()
    faiss_index = FaissIndex(dim=embedder.get_dimension(), user_id=user_id)

    # Generate embeddings
    embeddings = embedder.embed_chunks(chunk_texts)

    # DB batch insert (memory safe)
    db_chunks = []
    for text, start, end in chunked:
        chunk = Chunk(
            file_id=file.id,
            user_id=user_id,
            text=text,
            start_char=start,
            end_char=end,
            chunk_metadata={
                "length": len(text),
                "file_id": file.id,
                "file_name": file.filename
            }
        )
        db.session.add(chunk)
        db_chunks.append(chunk)

    db.session.commit()  # Now chunks have IDs

    # Prepare FAISS metadata (small dicts)
    meta_list = []
    ids = []

    for c in db_chunks:
        meta_list.append({
            "chunk_id": c.id,
            "file_id": c.file_id,
            "file_name": file.filename,
            "start_char": c.start_char,
            "end_char": c.end_char,
            "length": len(c.text)
        })
        ids.append(c.id)

    # Add to FAISS
    faiss_index.add_embeddings(
        embeddings=embeddings,
        chunk_ids=ids,
        chunk_metadata=meta_list
    )
    ############################
    # Mark file as processed
    file.processed = True
    db.session.add(file)
    db.session.commit()
    ############################

    return len(chunked)


# -------- FAISS SEARCH -------- #

def search_similar_chunks(user_id, query_text, top_k=5):
    from .embeddings import EmbeddingGenerator
    from .faiss_index import FaissIndex

    embedder = EmbeddingGenerator()
    faiss_index = FaissIndex(dim=embedder.get_dimension(), user_id=user_id)

    query_embedding = embedder.embed_text(query_text)

    distances, indices, metadata = faiss_index.search(query_embedding, top_k=top_k)

    results = []
    for dist, cid, meta in zip(distances, indices, metadata):
        if cid < 0:
            continue

        cid = int(cid)
        chunk = Chunk.query.get(cid)
        if not chunk:
            faiss_index.remove_id(cid)
            continue

        results.append({
            "chunk": chunk,
            "distance": float(dist),
            "metadata": meta
        })

    return results
