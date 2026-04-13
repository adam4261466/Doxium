from app.celery_app import celery
from .models import File, Chunk, db, User
from .document_processor import process_file, search_similar_chunks
from .faiss_index import FaissIndex
from .embeddings import EmbeddingGenerator
import os


@celery.task(name="tasks.process_file")
def process_file_task(file_id: int, user_id: int, upload_folder: str) -> int:
    count = process_file(file_id, user_id, upload_folder)
    return count


@celery.task(name="tasks.rebuild_index")
def rebuild_index_task(user_id: int) -> int:
    embedder = EmbeddingGenerator()
    index = FaissIndex(dim=embedder.get_dimension(), user_id=user_id)
    index.rebuild_index_from_chunks()
    return index.get_index_size()


@celery.task(name="tasks.generate_query_answer")
def generate_query_answer(user_id, query_text):
    try:
        user = User.query.get(user_id)
        if not user or not user.is_pilot:
            return {"error": "User not authorized or not Pilot"}

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return {"error": "GROQ_API_KEY is not configured."}

        similar_chunks = search_similar_chunks(user_id, query_text, top_k=5)
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
                        "on the provided document context. If the answer is not in the context, "
                        "say so clearly. Be concise and accurate."
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
        for chunk_data in similar_chunks:
            serializable_chunks.append({
                "id": chunk_data["chunk"].id,
                "text": chunk_data["chunk"].text,
                "filename": getattr(chunk_data["chunk"].file, "filename", "Unknown"),
                "score": chunk_data.get("distance", 0.0),
            })

        return {
            "query": query_text,
            "answer": answer,
            "chunks": serializable_chunks,
        }

    except Exception as exc:
        return {"error": f"Query failed: {str(exc)}"}
