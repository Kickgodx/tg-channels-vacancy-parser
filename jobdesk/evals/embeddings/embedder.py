"""Варианты моделей и эмбеддинги с дисковым кэшем: повторный прогон не ходит в API."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from jobdesk.config.schema import AiEmbeddingsConfig
from jobdesk.evals.embeddings.data import CACHE_DIR

_LOG = logging.getLogger(__name__)

DIMENSIONS = 1536  # колонка vector(1536) в проде — сравниваем модели в той же размерности


@dataclass(frozen=True)
class Variant:
    """Модель + как формируются тексты запроса и резюме (инструкции у instruction-aware моделей).

    Вакансии (документы) у всех вариантов одной модели одинаковые — их векторы в кэше общие.
    """

    name: str
    model: str
    price_per_m: float  # $ за 1M входных токенов (OpenRouter, на дату eval)
    query_instruction: str = ""
    resume_instruction: str = ""
    tags: tuple[str, ...] = field(default=())

    def query_text(self, text: str) -> str:
        # Как прод (query_embedding): пробелы схлопнуты, нижний регистр.
        q = " ".join(text.split()).lower()
        return _instruct(self.query_instruction, q)

    def resume_text(self, text: str) -> str:
        return _instruct(self.resume_instruction, text)


def _instruct(instruction: str, text: str) -> str:
    # Формат Qwen3-Embedding: "Instruct: {task}\nQuery:{query}".
    return f"Instruct: {instruction}\nQuery:{text}" if instruction else text


# Те же инструкции, что в проде (ai.embeddings.*_instruction по умолчанию).
_QWEN_SEARCH = AiEmbeddingsConfig.model_fields["query_instruction"].default
_QWEN_RESUME = AiEmbeddingsConfig.model_fields["resume_instruction"].default

VARIANTS: dict[str, Variant] = {
    v.name: v
    for v in (
        Variant("3-small", "openai/text-embedding-3-small", 0.02, tags=("baseline",)),
        Variant("qwen3-8b", "qwen/qwen3-embedding-8b", 0.01),
        Variant(
            "qwen3-8b+instr",
            "qwen/qwen3-embedding-8b",
            0.01,
            query_instruction=_QWEN_SEARCH,
            resume_instruction=_QWEN_RESUME,
        ),
        Variant("pplx-4b", "perplexity/pplx-embed-v1-4b", 0.03),
        Variant("gemini-001", "google/gemini-embedding-001", 0.15),
        # Не по умолчанию: подключаются через --variants.
        Variant("qwen3-4b", "qwen/qwen3-embedding-4b", 0.02, tags=("extra",)),
        Variant("3-large", "openai/text-embedding-3-large", 0.13, tags=("extra",)),
        Variant(
            "nemotron-3.5-8b",
            "nvidia/nemotron-3.5-embed-8b:free",
            0.0,
            query_instruction=_QWEN_SEARCH,
            resume_instruction=_QWEN_RESUME,
            tags=("extra",),
        ),
    )
}
DEFAULT_VARIANTS = [n for n, v in VARIANTS.items() if "extra" not in v.tags]


class EmbedFn(Protocol):
    def __call__(self, texts: list[str], *, model: str, dimensions: int) -> tuple[list[list[float]], int]: ...


def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{DIMENSIONS}\n{text}".encode()).hexdigest()


class VectorCache:
    """Кэш векторов одной модели: JSONL ``{"h": sha256, "v": base64(float32)}``, дозапись по батчам."""

    def __init__(self, model: str, cache_dir: Path = CACHE_DIR) -> None:
        self.model = model
        self.path = cache_dir / (model.replace("/", "__").replace(":", "_") + ".jsonl")
        self._vectors: dict[str, np.ndarray] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        self._vectors[row["h"]] = np.frombuffer(base64.b64decode(row["v"]), dtype=np.float32)

    def get(self, text: str) -> np.ndarray | None:
        return self._vectors.get(_key(self.model, text))

    def put_many(self, texts: list[str], vectors: list[list[float]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            for text, vec in zip(texts, vectors, strict=True):
                arr = np.asarray(vec, dtype=np.float32)
                h = _key(self.model, text)
                self._vectors[h] = arr
                f.write(json.dumps({"h": h, "v": base64.b64encode(arr.tobytes()).decode()}) + "\n")


@dataclass
class EmbedStats:
    requested: int = 0
    cached: int = 0
    tokens: int = 0


class Embedder:
    """Векторы текстов через ``embed_fn`` с кэшем; на выходе — L2-нормированная матрица.

    Нормируем сами: gemini-001 и pplx на 1536 измерениях отдают ненормированные векторы, а
    метрики считаются скалярным произведением (= косинус после нормировки, как ``<=>`` в pgvector).
    """

    def __init__(self, embed_fn: EmbedFn, *, batch_size: int = 32, cache_dir: Path = CACHE_DIR) -> None:
        self._embed_fn = embed_fn
        self._batch_size = batch_size
        self._cache_dir = cache_dir
        self._caches: dict[str, VectorCache] = {}
        self.stats: dict[str, EmbedStats] = {}

    def _cache(self, model: str) -> VectorCache:
        if model not in self._caches:
            self._caches[model] = VectorCache(model, self._cache_dir)
        return self._caches[model]

    def embed(self, model: str, texts: list[str]) -> np.ndarray:
        cache = self._cache(model)
        st = self.stats.setdefault(model, EmbedStats())
        st.requested += len(texts)
        todo = list(dict.fromkeys(t for t in texts if cache.get(t) is None))
        st.cached += len(texts) - len(todo)
        for start in range(0, len(todo), self._batch_size):
            self._embed_batch(model, todo[start : start + self._batch_size], cache, st)
        mat = np.stack([cache.get(t) for t in texts]).astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return mat / np.where(norms == 0, 1.0, norms)

    def _embed_batch(self, model: str, batch: list[str], cache: VectorCache, st: EmbedStats) -> None:
        try:
            vectors, tokens = self._embed_fn(batch, model=model, dimensions=DIMENSIONS)
        except Exception as e:
            # Лимит провайдера на размер запроса — делим батч пополам; одиночный текст — ошибка.
            if len(batch) == 1:
                raise
            _LOG.warning("%s: батч из %s не прошёл (%s), делим пополам", model, len(batch), e)
            mid = len(batch) // 2
            self._embed_batch(model, batch[:mid], cache, st)
            self._embed_batch(model, batch[mid:], cache, st)
            return
        st.tokens += tokens
        cache.put_many(batch, vectors)
