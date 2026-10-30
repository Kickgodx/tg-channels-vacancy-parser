"""LLM для разметки: синтетические поисковые запросы по вакансиям и судья релевантности выдачи."""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from collections.abc import Callable, Sequence

from jobdesk.evals.embeddings.data import CorpusDoc, Judgment, Query

_LOG = logging.getLogger(__name__)

# (messages) -> разобранный JSON-ответ
ChatFn = Callable[[list[dict[str, str]]], dict]

_GEN_SYSTEM = """Ты помогаешь собрать тест для поиска вакансий. Тебе дают вакансии из Telegram.
Для каждой напиши два запроса, которые мог бы ввести соискатель, желающий найти именно такую работу:
- "keywords": 2–6 слов, как в строке поиска (роль, ключевой стек, грейд или формат — что важно в этой вакансии);
- "natural": одно предложение от первого лица, как человек описывает, что ищет.
Правила: соискатель не видел текст вакансии — не копируй фразы и не упоминай компанию, суммы, ссылки,
редкие детали. Пиши по сути (роль, направление, стек, уровень, формат, домен). Язык — как пишут
русскоязычные IT-специалисты (термины и технологии можно латиницей).
Ответ — JSON: {"items": [{"i": <номер вакансии>, "keywords": "...", "natural": "..."}]}"""

_JUDGE_SYSTEM = """Ты оцениваешь выдачу поиска вакансий по запросу соискателя.
Для каждой вакансии поставь оценку:
2 — подходит: роль/направление совпадает и явные условия запроса (стек, грейд, формат, домен) выполнены
    или в вакансии не указаны;
1 — частично: смежная роль, либо та же роль, но нарушено одно явное условие запроса;
0 — не подходит.
Условия, которых нет в запросе, не учитывай. Ответ — JSON: {"grades": {"<номер>": 0|1|2, ...}} для всех номеров."""

SIMILAR_SYSTEM = """Соискатель открыл вакансию-образец, и ему показывают блок «Похожие вакансии».
Оцени каждую вакансию из списка:
2 — похожая: та же роль/специальность и близкий уровень; стек или предметная область пересекаются;
1 — частично: смежная роль, либо та же роль, но заметно другой уровень или стек;
0 — не похожа.
Ответ — JSON: {"grades": {"<номер>": 0|1|2, ...}} для всех номеров."""


def sample_sources(corpus: Sequence[CorpusDoc], n: int, *, seed: int = 0) -> list[CorpusDoc]:
    """Вакансии для синтетических запросов: поровну по основным ролям, затем добор случайными."""
    rng = random.Random(seed)
    by_role: dict[str, list[CorpusDoc]] = defaultdict(list)
    for d in corpus:
        if d.specializations and len(d.text) >= 300:
            by_role[d.specializations[0]].append(d)
    for docs in by_role.values():
        rng.shuffle(docs)
    picked: list[CorpusDoc] = []
    # Круговой обход ролей: редкие роли тоже попадут, а частые не займут всю выборку.
    while len(picked) < n and any(by_role.values()):
        for role in sorted(by_role, key=lambda r: -len(by_role[r])):
            if by_role[role] and len(picked) < n:
                picked.append(by_role[role].pop())
    return picked


def generate_queries(chat: ChatFn, docs: Sequence[CorpusDoc], *, batch: int = 10, max_chars: int = 2500) -> list[Query]:
    out: list[Query] = []
    for start in range(0, len(docs), batch):
        chunk = docs[start : start + batch]
        body = "\n\n".join(f"### Вакансия {i + 1}\n{d.text[:max_chars]}" for i, d in enumerate(chunk))
        try:
            resp = chat([{"role": "system", "content": _GEN_SYSTEM}, {"role": "user", "content": body}])
        except Exception as e:
            _LOG.warning("Генерация запросов: батч %s пропущен (%s)", start // batch, e)
            continue
        for item in resp.get("items") or []:
            try:
                doc = chunk[int(item["i"]) - 1]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            for kind in ("keywords", "natural"):
                text = str(item.get(kind) or "").strip()
                if text:
                    out.append(Query(qid=f"{kind[0]}-{doc.id[:12]}", text=text, kind=kind, source_id=doc.id))
        _LOG.info("Запросы: %s/%s вакансий", min(start + batch, len(docs)), len(docs))
    return out


def judge(
    chat: ChatFn,
    query: Query,
    docs: Sequence[CorpusDoc],
    *,
    judge_model: str,
    chunk: int = 15,
    max_chars: int = 1200,
    system: str = _JUDGE_SYSTEM,
    header: str | None = None,
) -> list[Judgment]:
    """Оценки ``docs`` по запросу; ``system``/``header`` — для «Похожих» (образец вместо запроса)."""
    out: list[Judgment] = []
    head = header if header is not None else f"Запрос: «{query.text}»"
    for start in range(0, len(docs), chunk):
        part = docs[start : start + chunk]
        body = f"{head}\n\n" + "\n\n".join(f"### {i + 1}\n{d.text[:max_chars]}" for i, d in enumerate(part))
        try:
            resp = chat([{"role": "system", "content": system}, {"role": "user", "content": body}])
        except Exception as e:
            _LOG.warning("Судья: запрос %s, часть %s пропущена (%s)", query.qid, start // chunk, e)
            continue
        grades = resp.get("grades") or {}
        for i, d in enumerate(part):
            g = grades.get(str(i + 1))
            if g is None:
                continue
            try:
                g = max(0, min(2, int(g)))
            except (TypeError, ValueError):
                continue
            out.append(Judgment(qid=query.qid, vacancy_id=d.id, grade=g, judge_model=judge_model))
    return out


def openrouter_chat(client, model: str) -> ChatFn:
    """ChatFn поверх ``OpenRouterClient``: JSON-ответ, без reasoning, с его лимитом и повторами."""
    from jobdesk.ai.clients.openrouter import _extract_json_text

    def parse(raw: str) -> dict:
        return json.loads(_extract_json_text(raw))

    def chat(messages: list[dict[str, str]]) -> dict:
        result, _usage = client._call_llm(messages=messages, parse=parse, model=model, reasoning=False)
        return result

    return chat
