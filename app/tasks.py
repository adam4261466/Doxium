from app.celery_app import celery
from .models import File, Chunk, db, User
from .document_processor import process_file, search_similar_chunks
from .faiss_index import FaissIndex
from .embeddings import EmbeddingGenerator
from app.routes import FREE_LIMITS
import os
from datetime import datetime, timedelta


@celery.task(name="tasks.process_file")
def process_file_task(file_id: int, user_id: int, upload_folder: str) -> int:
    """Background task to process a file into chunks and add embeddings to FAISS.
    Returns number of chunks processed.
    """
    # process_file handles splitting and adding to FAISS
    count = process_file(file_id, user_id, upload_folder)
    return count


@celery.task(name="tasks.rebuild_index")
def rebuild_index_task(user_id: int) -> int:
    """Background task to rebuild FAISS index for a user. Returns new ntotal."""
    # Determine embedding dimension via embedder
    embedder = EmbeddingGenerator()
    index = FaissIndex(dim=embedder.get_dimension(), user_id=user_id)
    index.rebuild_index_from_chunks()
    return index.get_index_size()



@celery.task(name="tasks.cleanup_expired_files")
def cleanup_expired_files():
    """Delete files beyond Free limits for users cancelled 30+ days ago."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    
    expired_users = User.query.filter(
        User.is_pilot == False,
        User.subscription_cancelled_at != None,
        User.subscription_cancelled_at <= cutoff
    ).all()

    for user in expired_users:
        files = File.query.filter_by(user_id=user.id)\
                          .order_by(File.created_at.asc()).all()
        files_to_delete = files[FREE_LIMITS["max_files"]:]
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
            return {"error": "User not found"}

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return {"error": "GROQ_API_KEY is not configured."}

        similar_chunks = search_similar_chunks(user_id, query_text, top_k=5)
        if file_ids:
            similar_chunks = [c for c in similar_chunks if c["chunk"].file_id in file_ids]
        if not similar_chunks:
            return {
                'query': query_text,
                'answer': "No relevant documents found for your query.",
                'chunks': []  # Empty list - JSON serializable
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
                        "1. Cite the source filename inline for each claim, like [Source 1 — filename.pdf].\n"
                        "2. If the answer draws from multiple documents, compare or contrast the information across them.\n"
                        "3. When asked to compare documents, explicitly list similarities and differences.\n"
                        "4. When asked which document mentions something, name the specific document(s)."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {query_text}",
                },
            ],
            temperature=0.3,
            max_tokens=1024,
        )

        answer = response.choices[0].message.content.strip()

        serializable_chunks = []
        for chunk_data in similar_chunks:
            serializable_chunk = {
                'id': chunk_data['chunk'].id,
                'text': chunk_data['chunk'].text,
                'filename': getattr(chunk_data['chunk'].file, 'filename', 'Unknown'),
                'score': chunk_data.get('distance', 0.0)
            }
            serializable_chunks.append(serializable_chunk)

        return {
            'query': query_text,
            'answer': answer,
            'chunks': serializable_chunks,
        }

    except Exception as exc:
        return {'error': f'Query failed: {str(exc)}'}
