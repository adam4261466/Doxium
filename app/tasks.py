from app.celery_app import celery
from .models import File, Chunk, db, User
from .document_processor import process_file,search_similar_chunks
from .faiss_index import FaissIndex
from .embeddings import EmbeddingGenerator


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


from transformers import pipeline

# Your existing tasks remain unchanged...

@celery.task(name="tasks.generate_query_answer")
def generate_query_answer(user_id, query_text):
    """Background task for LLM query processing with retries."""
    try:
        user = User.query.get(user_id)
        if not user or not user.is_pilot:
            return {'error': 'User not authorized or not Pilot'}
        
        gen_pipeline = pipeline(
            "text2text-generation",
            model="google/flan-t5-small",
            device=-1,
            max_length=150,
            do_sample=True,
            temperature=0.3
        )
        
        similar_chunks = search_similar_chunks(user_id, query_text, top_k=1)
        if not similar_chunks:
            return {
                'query': query_text,
                'answer': "No relevant documents found for your query.",
                'chunks': []  # Empty list - JSON serializable
            }
        
        # ✅ FIX: Convert Chunk objects to JSON-serializable dicts
        serializable_chunks = []
        for chunk_data in similar_chunks:
            serializable_chunk = {
                'id': chunk_data['chunk'].id,
                'text': chunk_data['chunk'].text,
                'filename': getattr(chunk_data['chunk'], 'filename', 'Unknown'),
                'score': chunk_data.get('score', 0.0)
            }
            serializable_chunks.append(serializable_chunk)
        
        context = similar_chunks[0]['chunk'].text
        prompt = f"Read the following document and answer the question naturally.\n\nDocument:\n{context}\n\nQuestion: {query_text}\nAnswer:"
        
        result = gen_pipeline(prompt)
        answer = result[0]['generated_text'].strip()
        
        return {
            'query': query_text,
            'answer': answer,
            'chunks': serializable_chunks  # ✅ Now JSON serializable
        }
        
    except Exception as exc:
        return {'error': f'Query failed: {str(exc)}'}
