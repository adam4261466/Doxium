import re
import threading

class EmbeddingGenerator:
    """
    Memory-optimized embedding generator.
    - Loads the model lazily only when needed.
    - Keeps a single instance per worker using thread-safe lazy init.
    - Reduces RAM spikes when embedding large files.
    """

    _model = None
    _lock = threading.Lock()
    _model_name = "nomic-ai/nomic-embed-text-v1.5"
    dimension = 768

    @classmethod
    def _load_model(cls):
        """Lazy-load model once per process."""
        with cls._lock:
            if cls._model is None:
                from sentence_transformers import SentenceTransformer
                cls._model = SentenceTransformer(cls._model_name, trust_remote_code=True)
        return cls._model

    def embed_text(self, text):
        """Embed a single short text."""
        model = self._load_model()
        return model.encode(text, convert_to_numpy=True)

    def embed_chunks(self, texts, max_tokens=50):
        """
        Embed text or list of text chunks.
        Automatically performs semantic chunking on long strings.
        """
        model = self._load_model()

        # If user passes a big string → we chunk it.
        if isinstance(texts, str):
            chunks = []
            current = []

            for sentence in re.split(r'(?<=[.!?])\s+|\n+', texts):
                words = sentence.split()
                if not words:
                    continue

                if len(current) + len(words) > max_tokens:
                    chunks.append(" ".join(current))
                    current = words
                else:
                    current.extend(words)

            if current:
                chunks.append(" ".join(current))

            return model.encode(chunks, convert_to_numpy=True)

        # If list → embed directly
        elif isinstance(texts, list):
            return model.encode(texts, convert_to_numpy=True)

        else:
            raise ValueError("Input must be a string or a list of strings.")

    def get_dimension(self):
        return self.dimension
