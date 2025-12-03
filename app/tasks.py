from .celery_app import celery
from .models import File, Chunk, db
from .document_processor import process_file
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
