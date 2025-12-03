import faiss
import numpy as np
import os
import json
import tempfile
from filelock import FileLock
from typing import Optional, List, Dict, Any


class FaissIndex:
    """
    Low-RAM, disk-persistent FAISS index per user using HNSW (default) wrapped with IndexIDMap.
    - HNSW avoids heavy training and large contiguous allocations (better for low-RAM machines).
    - Uses an ID map so we can add_with_ids / remove_ids reliably.
    - Metadata stored as a dict {id: metadata} to avoid huge sparse lists.
    - Atomic saves and a file lock to protect concurrent writes.
    """

    # Tunables for low-RAM environments
    DEFAULT_M = 32  # HNSW parameter (connectivity). Lower -> less memory, slightly slower recall.
    DEFAULT_NPROBE = 2  # kept for compatibility; not used by HNSW but left in API.

    def __init__(
        self,
        dim: int,
        user_id: int,
        path: str = "data/faiss",
        m: Optional[int] = None,
        nprobe: Optional[int] = None,
    ):
        self.user_id = int(user_id)
        self.dim = int(dim)
        self.m = int(m) if m is not None else self.DEFAULT_M
        self.nprobe = int(nprobe) if nprobe is not None else self.DEFAULT_NPROBE

        base_dir = os.path.join(path, f"{self.user_id}.faiss")
        self.index_path = os.path.join(base_dir, f"{self.user_id}.index")
        self.metadata_path = os.path.join(base_dir, f"{self.user_id}_metadata.json")
        self._lock_file = os.path.join(tempfile.gettempdir(), f"faiss-lock-{self.user_id}.lock")

        os.makedirs(base_dir, exist_ok=True)

        # Load or create index and metadata
        if os.path.exists(self.index_path):
            try:
                self.index = faiss.read_index(self.index_path)
                # If it's not an IDMap, wrap it to support IDs consistently
                if not isinstance(self.index, faiss.IndexIDMap):
                    self.index = faiss.IndexIDMap(self.index)
            except Exception:
                # If read fails, create a fresh index
                self.index = self._create_hnsw_idmap(self.dim, self.m)
        else:
            self.index = self._create_hnsw_idmap(self.dim, self.m)

        # Load metadata (dict keyed by stringified id)
        self._load_metadata()

        # Ensure index.nprobe if supported (kept for API compatibility)
        self._maybe_set_nprobe(self.nprobe)

    # -------------------------
    # Internal helpers
    # -------------------------
    def _create_hnsw_idmap(self, dim: int, m: int):
        """Create an HNSW index and wrap in an ID map for stable ID handling."""
        hnsw = faiss.IndexHNSWFlat(dim, m)
        idmap = faiss.IndexIDMap(hnsw)
        return idmap

    def _maybe_set_nprobe(self, nprobe: int):
        """Set nprobe if the index supports IVF parameters. Safe no-op for HNSW."""
        try:
            if hasattr(self.index, 'nprobe'):
                self.index.nprobe = int(nprobe)
        except Exception:
            pass

    def _load_metadata(self):
        """Load metadata from JSON file into a dict keyed by int IDs."""
        try:
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            # convert keys back to int
            if isinstance(raw, dict):
                self.metadata: Dict[int, Dict[str, Any]] = {int(k): v for k, v in raw.items()}
            else:
                # legacy fallback (list) -> convert to dict entries where present
                self.metadata = {}
                if isinstance(raw, list):
                    for i, v in enumerate(raw):
                        if v:
                            self.metadata[i] = v
        except (FileNotFoundError, json.JSONDecodeError):
            self.metadata = {}

    def _save_metadata(self):
        """Persist metadata as a JSON dict with string keys (safe for JSON)."""
        with open(self.metadata_path, 'w', encoding='utf-8') as f:
            # convert keys to strings for JSON
            json.dump({str(k): v for k, v in self.metadata.items()}, f)

    def _ensure_float32(self, arr: np.ndarray) -> np.ndarray:
        """Return contiguous float32 numpy array (FAISS expects float32)."""
        arr = np.asarray(arr)
        if arr.dtype != np.float32:
            arr = arr.astype('float32', copy=False)
        return np.ascontiguousarray(arr)

    # -------------------------
    # Public API
    # -------------------------
    def add_embeddings(self, embeddings, chunk_ids: Optional[List[int]] = None, chunk_metadata: Optional[List[Dict]] = None) -> List[int]:
        """
        Add embeddings to the index with optional explicit IDs and metadata.

        embeddings: numpy array shape (N, dim) or list-of-lists convertible to that
        chunk_ids: optional list of IDs to assign. If None, picks sequential new IDs starting from max existing id + 1
        chunk_metadata: optional list of metadata dicts aligned with embeddings / chunk_ids
        """
        with FileLock(self._lock_file, timeout=60):
            embeddings_np = self._ensure_float32(embeddings)
            if embeddings_np.ndim != 2 or embeddings_np.shape[1] != self.dim:
                raise ValueError(f"Embeddings must have shape (N, {self.dim}), got {embeddings_np.shape}")

            # Determine IDs
            if chunk_ids is None:
                # choose IDs starting from max existing ID + 1 or 0
                if len(self.metadata) == 0:
                    start_id = 0
                else:
                    start_id = max(self.metadata.keys()) + 1
                chunk_ids = list(range(start_id, start_id + len(embeddings_np)))
            ids_np = np.asarray(chunk_ids, dtype='int64')

            # Add with IDs (IndexIDMap supports add_with_ids)
            try:
                self.index.add_with_ids(embeddings_np, ids_np)
            except Exception as e:
                # Fallback: if add_with_ids fails, use add then map IDs manually (less ideal)
                # But IndexIDMap should support add_with_ids; raise if it fails here.
                raise RuntimeError(f"Failed to add_with_ids to FAISS index: {e}")

            # Update metadata dict
            if chunk_metadata is None:
                chunk_metadata = [{} for _ in range(len(ids_np))]
            for cid, meta in zip(ids_np.tolist(), chunk_metadata):
                # Merge/overwrite per ID
                existing = self.metadata.get(int(cid), {})
                merged = {**existing, **(meta or {})}
                self.metadata[int(cid)] = merged

            # Persist index + metadata
            self.save_index_safely()

            # Try to record IndexMeta in DB if model exists (best-effort)
            try:
                from .models import IndexMeta, db
                index_meta = IndexMeta(user_id=self.user_id, index_path=self.index_path)
                db.session.add(index_meta)
                db.session.commit()
            except Exception:
                pass

            return ids_np.tolist()

    def search(self, query_embedding, top_k: int = 5, nprobe: Optional[int] = None):
        """
        Search for similar embeddings.

        Returns (distances, indices, metadata_list)
        """
        query_np = self._ensure_float32(np.asarray([query_embedding]))
        if query_np.shape != (1, self.dim):
            raise ValueError(f"Query embedding must have shape ({self.dim},)")

        # HNSW ignores nprobe; kept for API compat
        distances, indices = self.index.search(query_np, top_k)

        # build metadata list aligned with indices
        result_metadata = []
        idxs = indices[0]
        dists = distances[0]
        for idx in idxs:
            if int(idx) in self.metadata:
                result_metadata.append(self.metadata[int(idx)])
            else:
                result_metadata.append({})

        return dists, idxs, result_metadata

    def get_index_size(self) -> int:
        return int(self.index.ntotal)

    def get_metadata(self, chunk_id: int) -> Dict[str, Any]:
        return self.metadata.get(int(chunk_id), {})

    def remove_id(self, chunk_id: int):
        """Remove a single ID from the index and metadata if supported."""
        with FileLock(self._lock_file, timeout=60):
            try:
                ids_to_remove = np.asarray([int(chunk_id)], dtype='int64')
                # IndexIDMap supports remove_ids
                self.index.remove_ids(ids_to_remove)
                # Remove metadata entry if present
                if int(chunk_id) in self.metadata:
                    del self.metadata[int(chunk_id)]
                self.save_index_safely()
            except Exception as e:
                print(f"Error removing ID {chunk_id} from FAISS index: {e}")

    def rebuild_index_from_chunks(self):
        """
        Rebuild the index from DB chunks. This will:
        - load all chunks from DB
        - compute embeddings (calls your EmbeddingGenerator)
        - create a fresh HNSW+IDMap index and add all vectors
        - replace old index and metadata atomically
        """
        from .models import Chunk
        from .embeddings import EmbeddingGenerator

        with FileLock(self._lock_file, timeout=60):
            print(f"DEBUG: Rebuilding FAISS index for user {self.user_id}")

            chunks = Chunk.query.filter_by(user_id=self.user_id).all()
            if not chunks:
                # Empty index
                self.index = self._create_hnsw_idmap(self.dim, self.m)
                self.metadata = {}
                self.save_index_safely()
                print(f"DEBUG: Rebuilt empty index for user {self.user_id}")
                return

            chunk_texts = [c.text for c in chunks]
            chunk_ids = [c.id for c in chunks]

            embedder = EmbeddingGenerator()
            embeddings = embedder.embed_chunks(chunk_texts)
            embeddings_np = self._ensure_float32(embeddings)

            # Create fresh index
            new_index = self._create_hnsw_idmap(self.dim, self.m)

            # Add with IDs
            ids_np = np.asarray(chunk_ids, dtype='int64')
            new_index.add_with_ids(embeddings_np, ids_np)

            # Build metadata dict
            new_metadata: Dict[int, Dict[str, Any]] = {}
            for c in chunks:
                md = {
                    "chunk_id": c.id,
                    "file_id": c.file_id,
                    "file_name": getattr(c.file, "filename", "Unknown") if getattr(c, "file", None) else "Unknown",
                    "start_char": getattr(c, "start_char", None),
                    "end_char": getattr(c, "end_char", None),
                    "length": len(c.text) if getattr(c, "text", None) else 0,
                }
                new_metadata[int(c.id)] = md

            # Swap
            self.index = new_index
            self.metadata = new_metadata
            self.save_index_safely()
            print(f"DEBUG: Successfully rebuilt index for user {self.user_id} with {len(chunk_ids)} items")

    def save_index_safely(self):
        """Atomically write index and metadata to disk and verify the written index."""
        import shutil

        tmp_index_path = os.path.join(os.path.dirname(self.index_path), "tmp.index")
        tmp_metadata_path = os.path.join(os.path.dirname(self.metadata_path), "tmp_metadata.json")

        # Write index and metadata to temp files
        faiss.write_index(self.index, tmp_index_path)
        with open(tmp_metadata_path, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in self.metadata.items()}, f)

        # Verify index loads correctly
        try:
            test_index = faiss.read_index(tmp_index_path)
            # Basic sanity check: ntotal must match
            if int(test_index.ntotal) != int(self.index.ntotal):
                raise ValueError("Index size mismatch after write verification")
        except Exception as e:
            # Clean up temp files and raise
            if os.path.exists(tmp_index_path):
                os.remove(tmp_index_path)
            if os.path.exists(tmp_metadata_path):
                os.remove(tmp_metadata_path)
            raise RuntimeError(f"Index verification failed: {e}")

        # Atomic swap
        shutil.move(tmp_index_path, self.index_path)
        shutil.move(tmp_metadata_path, self.metadata_path)
        print("✅ Index swapped safely.")

# ------------------------
# Simple helper API (keeps compatibility)
# ------------------------
DIMENSION = 768
DEFAULT_M = FaissIndex.DEFAULT_M


def create_index_hnsw():
    idx = faiss.IndexHNSWFlat(DIMENSION, DEFAULT_M)
    return faiss.IndexIDMap(idx)


def build_or_update_index(embeddings: np.ndarray, index_file: str = os.path.join("data", "faiss", "global.index")):
    os.makedirs(os.path.dirname(index_file), exist_ok=True)
    if os.path.exists(index_file):
        index = faiss.read_index(index_file)
        if not isinstance(index, faiss.IndexIDMap):
            index = faiss.IndexIDMap(index)
    else:
        index = create_index_hnsw()

    emb = np.asarray(embeddings, dtype=np.float32)
    # add without training (HNSW)
    if hasattr(index, 'add_with_ids'):
        start_id = 0
        # compute start id as max id + 1 if metadata available isn't stored here; we just append with auto ids
        index.add(emb)
    else:
        index.add(emb)

    faiss.write_index(index, index_file)
    return index


def load_index(index_file: str = os.path.join("data", "faiss", "global.index")):
    if not os.path.exists(index_file):
        raise ValueError("Index not found, build it first.")
    idx = faiss.read_index(index_file)
    if not isinstance(idx, faiss.IndexIDMap):
        idx = faiss.IndexIDMap(idx)
    return idx


def search_vectors(query_embedding, top_k=5, index_file: str = os.path.join("data", "faiss", "global.index")):
    index = load_index(index_file)
    q = np.asarray([query_embedding], dtype=np.float32)
    distances, indices = index.search(q, top_k)
    return distances[0], indices[0]
