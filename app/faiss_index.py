import faiss
import numpy as np
import os
import json
import tempfile
from filelock import FileLock
from typing import Optional, List, Dict, Any

# Use env var so Railway volume path works correctly
FAISS_BASE_PATH = os.getenv("FAISS_PATH", "data/faiss")


class FaissIndex:
    DEFAULT_M = 32
    DEFAULT_NPROBE = 2

    def __init__(
        self,
        dim: int,
        user_id: int,
        path: str = None,
        m: Optional[int] = None,
        nprobe: Optional[int] = None,
    ):
        self.user_id = int(user_id)
        self.dim = int(dim)
        self.m = int(m) if m is not None else self.DEFAULT_M
        self.nprobe = int(nprobe) if nprobe is not None else self.DEFAULT_NPROBE

        # Allow override via argument, else use env-based default
        base_path = path if path is not None else FAISS_BASE_PATH
        base_dir = os.path.join(base_path, f"{self.user_id}.faiss")
        self.index_path = os.path.join(base_dir, f"{self.user_id}.index")
        self.metadata_path = os.path.join(base_dir, f"{self.user_id}_metadata.json")
        self._lock_file = os.path.join(tempfile.gettempdir(), f"faiss-lock-{self.user_id}.lock")

        os.makedirs(base_dir, exist_ok=True)

        if os.path.exists(self.index_path):
            try:
                self.index = faiss.read_index(self.index_path)
                if not isinstance(self.index, faiss.IndexIDMap):
                    self.index = faiss.IndexIDMap(self.index)
            except Exception:
                self.index = self._create_hnsw_idmap(self.dim, self.m)
        else:
            self.index = self._create_hnsw_idmap(self.dim, self.m)

        self._load_metadata()
        self._maybe_set_nprobe(self.nprobe)

    def _create_hnsw_idmap(self, dim: int, m: int):
        hnsw = faiss.IndexHNSWFlat(dim, m)
        return faiss.IndexIDMap(hnsw)

    def _maybe_set_nprobe(self, nprobe: int):
        try:
            if hasattr(self.index, 'nprobe'):
                self.index.nprobe = int(nprobe)
        except Exception:
            pass

    def _load_metadata(self):
        try:
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.metadata: Dict[int, Dict[str, Any]] = {int(k): v for k, v in raw.items()}
            else:
                self.metadata = {}
                if isinstance(raw, list):
                    for i, v in enumerate(raw):
                        if v:
                            self.metadata[i] = v
        except (FileNotFoundError, json.JSONDecodeError):
            self.metadata = {}

    def _save_metadata(self):
        with open(self.metadata_path, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in self.metadata.items()}, f)

    def _ensure_float32(self, arr: np.ndarray) -> np.ndarray:
        arr = np.asarray(arr)
        if arr.dtype != np.float32:
            arr = arr.astype('float32', copy=False)
        return np.ascontiguousarray(arr)

    def add_embeddings(self, embeddings, chunk_ids: Optional[List[int]] = None, chunk_metadata: Optional[List[Dict]] = None) -> List[int]:
        with FileLock(self._lock_file, timeout=60):
            embeddings_np = self._ensure_float32(embeddings)
            if embeddings_np.ndim != 2 or embeddings_np.shape[1] != self.dim:
                raise ValueError(f"Embeddings must have shape (N, {self.dim}), got {embeddings_np.shape}")

            if chunk_ids is None:
                start_id = max(self.metadata.keys()) + 1 if self.metadata else 0
                chunk_ids = list(range(start_id, start_id + len(embeddings_np)))
            ids_np = np.asarray(chunk_ids, dtype='int64')

            try:
                self.index.add_with_ids(embeddings_np, ids_np)
            except Exception as e:
                raise RuntimeError(f"Failed to add_with_ids to FAISS index: {e}")

            if chunk_metadata is None:
                chunk_metadata = [{} for _ in range(len(ids_np))]
            for cid, meta in zip(ids_np.tolist(), chunk_metadata):
                existing = self.metadata.get(int(cid), {})
                self.metadata[int(cid)] = {**existing, **(meta or {})}

            self.save_index_safely()

            try:
                from .models import IndexMeta, db
                index_meta = IndexMeta(user_id=self.user_id, index_path=self.index_path)
                db.session.add(index_meta)
                db.session.commit()
            except Exception:
                pass

            return ids_np.tolist()

    def search(self, query_embedding, top_k: int = 5, nprobe: Optional[int] = None):
        query_np = self._ensure_float32(np.asarray([query_embedding]))
        if query_np.shape != (1, self.dim):
            raise ValueError(f"Query embedding must have shape ({self.dim},)")

        distances, indices = self.index.search(query_np, top_k)

        result_metadata = []
        for idx in indices[0]:
            result_metadata.append(self.metadata.get(int(idx), {}))

        return distances[0], indices[0], result_metadata

    def get_index_size(self) -> int:
        return int(self.index.ntotal)

    def get_metadata(self, chunk_id: int) -> Dict[str, Any]:
        return self.metadata.get(int(chunk_id), {})

    def remove_id(self, chunk_id: int):
        with FileLock(self._lock_file, timeout=60):
            try:
                ids_to_remove = np.asarray([int(chunk_id)], dtype='int64')
                self.index.remove_ids(ids_to_remove)
                if int(chunk_id) in self.metadata:
                    del self.metadata[int(chunk_id)]
                self.save_index_safely()
            except Exception as e:
                print(f"Error removing ID {chunk_id} from FAISS index: {e}")

    def rebuild_index_from_chunks(self):
        from .models import Chunk
        from .embeddings import EmbeddingGenerator

        with FileLock(self._lock_file, timeout=60):
            chunks = Chunk.query.filter_by(user_id=self.user_id).all()
            if not chunks:
                self.index = self._create_hnsw_idmap(self.dim, self.m)
                self.metadata = {}
                self.save_index_safely()
                return

            chunk_texts = [c.text for c in chunks]
            chunk_ids = [c.id for c in chunks]

            embedder = EmbeddingGenerator()
            embeddings_np = self._ensure_float32(embedder.embed_chunks(chunk_texts))

            new_index = self._create_hnsw_idmap(self.dim, self.m)
            new_index.add_with_ids(embeddings_np, np.asarray(chunk_ids, dtype='int64'))

            new_metadata: Dict[int, Dict[str, Any]] = {}
            for c in chunks:
                new_metadata[int(c.id)] = {
                    "chunk_id": c.id,
                    "file_id": c.file_id,
                    "file_name": getattr(c.file, "filename", "Unknown") if getattr(c, "file", None) else "Unknown",
                    "start_char": getattr(c, "start_char", None),
                    "end_char": getattr(c, "end_char", None),
                    "length": len(c.text) if c.text else 0,
                }

            self.index = new_index
            self.metadata = new_metadata
            self.save_index_safely()

    def save_index_safely(self):
        import shutil
        tmp_index = os.path.join(os.path.dirname(self.index_path), "tmp.index")
        tmp_meta = os.path.join(os.path.dirname(self.metadata_path), "tmp_metadata.json")

        faiss.write_index(self.index, tmp_index)
        with open(tmp_meta, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in self.metadata.items()}, f)

        try:
            test_index = faiss.read_index(tmp_index)
            if int(test_index.ntotal) != int(self.index.ntotal):
                raise ValueError("Index size mismatch after write verification")
        except Exception as e:
            for p in [tmp_index, tmp_meta]:
                if os.path.exists(p):
                    os.remove(p)
            raise RuntimeError(f"Index verification failed: {e}")

        shutil.move(tmp_index, self.index_path)
        shutil.move(tmp_meta, self.metadata_path)


# Simple helper API
DIMENSION = 768
DEFAULT_M = FaissIndex.DEFAULT_M


def create_index_hnsw():
    idx = faiss.IndexHNSWFlat(DIMENSION, DEFAULT_M)
    return faiss.IndexIDMap(idx)


def build_or_update_index(embeddings: np.ndarray, index_file: str = None):
    if index_file is None:
        index_file = os.path.join(FAISS_BASE_PATH, "global.index")
    os.makedirs(os.path.dirname(index_file), exist_ok=True)
    if os.path.exists(index_file):
        index = faiss.read_index(index_file)
        if not isinstance(index, faiss.IndexIDMap):
            index = faiss.IndexIDMap(index)
    else:
        index = create_index_hnsw()

    emb = np.asarray(embeddings, dtype=np.float32)
    index.add(emb)
    faiss.write_index(index, index_file)
    return index


def load_index(index_file: str = None):
    if index_file is None:
        index_file = os.path.join(FAISS_BASE_PATH, "global.index")
    if not os.path.exists(index_file):
        raise ValueError("Index not found, build it first.")
    idx = faiss.read_index(index_file)
    if not isinstance(idx, faiss.IndexIDMap):
        idx = faiss.IndexIDMap(idx)
    return idx


def search_vectors(query_embedding, top_k=5, index_file: str = None):
    index = load_index(index_file)
    q = np.asarray([query_embedding], dtype=np.float32)
    distances, indices = index.search(q, top_k)
    return distances[0], indices[0]
