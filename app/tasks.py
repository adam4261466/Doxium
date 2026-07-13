from app.celery_app import celery
from .models import File, Chunk, db, User
from .document_processor import process_file, search_similar_chunks
import os
import logging

logger = logging.getLogger(__name__)


@celery.task(name="tasks.process_file")
def process_file_task(file_id: int, user_id: int, upload_folder: str) -> int:
    count = process_file(file_id, user_id, upload_folder)
    return count


@celery.task(name="tasks.rebuild_index")
def rebuild_index_task(user_id: int) -> int:
    from .faiss_index import FaissIndex
    from .embeddings import EmbeddingGenerator
    embedder = EmbeddingGenerator()
    index = FaissIndex(dim=embedder.get_dimension(), user_id=user_id)
    index.rebuild_index_from_chunks()
    return index.get_index_size()


@celery.task(name="tasks.cleanup_expired_files")
def cleanup_expired_files():
    from datetime import datetime, timedelta
    from app.routes import FREE_LIMITS
    cutoff = datetime.utcnow() - timedelta(days=30)

    expired_users = User.query.filter(
        User.is_pilot == False,
        User.subscription_cancelled_at != None,
        User.subscription_cancelled_at <= cutoff
    ).all()

    for user in expired_users:
        files = File.query.filter_by(user_id=user.id) \
                          .order_by(File.created_at.asc()).all()
        free_limit = FREE_LIMITS["max_total_files"]
        files_to_delete = files[free_limit:]
        for file in files_to_delete:
            for chunk in file.chunks:
                db.session.delete(chunk)
            if os.path.exists(file.path):
                os.remove(file.path)
            db.session.delete(file)
        db.session.commit()


@celery.task(name="tasks.generate_query_answer")
def generate_query_answer(user_id, query_text, file_ids=None):
    try:
        user = User.query.get(user_id)
        if not user:
            return {"error": "User not found."}

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return {"error": "GROQ_API_KEY is not configured."}

        similar_chunks = search_similar_chunks(user_id, query_text, top_k=5, file_ids=file_ids)
        if not similar_chunks:
            return {
                "query": query_text,
                "answer": "No relevant documents found for your query.",
                "chunks": [],
            }

        context_parts = []
        for i, chunk_data in enumerate(similar_chunks, 1):
            filename = getattr(chunk_data["chunk"].file, "filename", "Unknown")
            context_parts.append(f"[Source {i} — {filename}]\n{chunk_data['chunk'].text}")
        context = "\n\n".join(context_parts)

        from groq import Groq
        client = Groq(api_key=groq_api_key)

        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant that answers questions based strictly "
                        "on the provided document context. Always cite your sources inline "
                        "using [1], [2], etc. corresponding to the provided sources. "
                        "If the answer is not in the context, say so clearly. "
                        "Be concise and accurate."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {query_text}",
                },
            ],
            temperature=0.3,
            max_tokens=512,
        )

        answer = response.choices[0].message.content.strip()

        serializable_chunks = []
        for i, chunk_data in enumerate(similar_chunks, 1):
            serializable_chunks.append({
                "id": chunk_data["chunk"].id,
                "text": chunk_data["chunk"].text,
                "filename": getattr(chunk_data["chunk"].file, "filename", "Unknown"),
                "score": chunk_data.get("distance", 0.0),
                "source_num": i,
            })

        return {
            "query": query_text,
            "answer": answer,
            "chunks": serializable_chunks,
        }

    except Exception as exc:
        return {"error": f"Query failed: {str(exc)}"}


@celery.task(name="tasks.generate_summary")
def generate_summary(user_id, file_id):
    try:
        user = User.query.get(user_id)
        if not user:
            return {"error": "User not found."}

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return {"error": "GROQ_API_KEY is not configured."}

        chunks = Chunk.query.filter_by(file_id=file_id, user_id=user_id).all()
        if not chunks:
            return {"error": "No content found for this document."}

        file = File.query.get(file_id)
        full_text = "\n".join(c.text for c in chunks)
        if len(full_text) > 12000:
            full_text = full_text[:12000]

        from groq import Groq
        client = Groq(api_key=groq_api_key)

        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant that creates structured summaries of documents. "
                        "Include: main topics, key points, important details, and conclusions. "
                        "Use markdown formatting with headers and bullet points."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Summarize this document:\n\n{full_text}",
                },
            ],
            temperature=0.3,
            max_tokens=1024,
        )

        return {
            "type": "summary",
            "filename": file.filename if file else "Unknown",
            "content": response.choices[0].message.content.strip(),
        }

    except Exception as exc:
        return {"error": f"Summary failed: {str(exc)}"}


@celery.task(name="tasks.generate_flashcards")
def generate_flashcards(user_id, file_id):
    try:
        user = User.query.get(user_id)
        if not user:
            return {"error": "User not found."}

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return {"error": "GROQ_API_KEY is not configured."}

        chunks = Chunk.query.filter_by(file_id=file_id, user_id=user_id).all()
        if not chunks:
            return {"error": "No content found for this document."}

        file = File.query.get(file_id)
        full_text = "\n".join(c.text for c in chunks)
        if len(full_text) > 12000:
            full_text = full_text[:12000]

        from groq import Groq
        client = Groq(api_key=groq_api_key)

        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate study flashcards from the document. "
                        "Return a JSON array of objects with 'front' (question) and 'back' (answer) keys. "
                        "Generate 8-12 flashcards. Only return the JSON array, no other text."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Document content:\n\n{full_text}",
                },
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        import json
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removesuffix("```").strip()
        cards = json.loads(raw)

        return {
            "type": "flashcards",
            "filename": file.filename if file else "Unknown",
            "cards": cards,
        }

    except Exception as exc:
        return {"error": f"Flashcard generation failed: {str(exc)}"}


@celery.task(name="tasks.generate_quiz")
def generate_quiz(user_id, file_id):
    try:
        user = User.query.get(user_id)
        if not user:
            return {"error": "User not found."}

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return {"error": "GROQ_API_KEY is not configured."}

        chunks = Chunk.query.filter_by(file_id=file_id, user_id=user_id).all()
        if not chunks:
            return {"error": "No content found for this document."}

        file = File.query.get(file_id)
        full_text = "\n".join(c.text for c in chunks)
        if len(full_text) > 12000:
            full_text = full_text[:12000]

        from groq import Groq
        client = Groq(api_key=groq_api_key)

        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate a 5-question multiple choice quiz from the document. "
                        "Return a JSON array of objects with: 'question' (string), "
                        "'options' (array of 4 strings labeled A-D), 'correct' (the correct letter A-D), "
                        "and 'explanation' (why the answer is correct). Only return the JSON array."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Document content:\n\n{full_text}",
                },
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        import json
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removesuffix("```").strip()
        questions = json.loads(raw)

        return {
            "type": "quiz",
            "filename": file.filename if file else "Unknown",
            "questions": questions,
        }

    except Exception as exc:
        return {"error": f"Quiz generation failed: {str(exc)}"}
