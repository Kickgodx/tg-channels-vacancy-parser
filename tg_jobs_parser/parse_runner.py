"""Оркестрация: Telethon → извлечение → фильтры → дедуп → метрики."""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from tg_jobs_parser.analytics.metrics_builder import build_metrics
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.deduplication.deduplicator import deduplicate_vacancies
from tg_jobs_parser.extraction.company_extractor import extract_company
from tg_jobs_parser.extraction.contact_extractor import extract_contact
from tg_jobs_parser.extraction.grade_extractor import extract_grade
from tg_jobs_parser.extraction.location_extractor import detect_work_format, extract_country_city
from tg_jobs_parser.extraction.normalizer import clean_display_text, normalize_for_match
from tg_jobs_parser.extraction.salary_extractor import extract_salary
from tg_jobs_parser.extraction.specialization_extractor import extract_specializations
from tg_jobs_parser.extraction.technology_extractor import extract_technologies
from tg_jobs_parser.extraction.vacancy_detector import compute_vacancy_score
from tg_jobs_parser.filters.vacancy_filters import apply_post_filters
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parsers.base import BaseChannelParser, RawPost
from tg_jobs_parser.parsers.channel_username import normalize_channel_username
from tg_jobs_parser.utils.dates import clamp_range_days, parse_date
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.utils.sorting import sort_vacancies_for_report
from tg_jobs_parser.utils.logger import get_logger

_LOG = get_logger(__name__)


def _channel_parser(config: AppConfig) -> BaseChannelParser:
    """Единственный источник сообщений — Telethon (MTProto с файлом сессии)."""
    from tg_jobs_parser.parsers.telegram_telethon import TelegramTelethonParser, telethon_import_error

    if err := telethon_import_error():
        raise RuntimeError(
            "Нужен пакет Telethon: pip install telethon python-dotenv "
            "(или poetry install --extras telethon). Убедитесь, что используете тот же Python, что и при установке зависимостей."
        ) from err
    return TelegramTelethonParser(config)


def _extract_employment(text: str) -> str | None:
    t = normalize_for_match(text)
    if "full-time" in t or "фуллтайм" in t or "полная занятость" in t:
        return "full-time"
    if "part-time" in t or "part time" in t or "частичная" in t:
        return "part-time"
    if "contract" in t or "контракт" in t or "контрактор" in t:
        return "contract"
    return None


def _extract_experience(text: str) -> str | None:
    m = re.search(r"\b(\d+\+?\s*(?:лет|years?|y\/o)|опыт\s*от\s*\d+[\s\-]*\d*\s*(?:лет|years)?)\b", text, re.I)
    if m:
        return m.group(1).strip()
    if re.search(r"\bno\s+experience\b|без\s+опыт", text, re.I):
        return "no experience"
    return None


def raw_post_to_vacancy(
    post: RawPost,
    config: AppConfig,
    *,
    spec_map: dict[str, list[str]] | None = None,
    tech_list: list[str] | None = None,
    wf_map: dict[str, list[str]] | None = None,
) -> Vacancy:
    """
    Преобразовать сырой пост в вакансию.

    Словари можно передать заранее (``spec_map``/``tech_list``/``wf_map``) — это критично
    при пакетной обработке, иначе на каждый пост они пересобираются заново.
    """
    raw = post.text
    cleaned = clean_display_text(raw)
    score, _, substantive = compute_vacancy_score(raw)

    if spec_map is None:
        spec_map = config.merged_specializations()
    if tech_list is None:
        tech_list = config.merged_technologies()
    if wf_map is None:
        wf_map = config.merged_work_format_synonyms()

    specs = extract_specializations(raw, spec_map)
    salary = extract_salary(raw)
    grade = extract_grade(raw)
    country, city = extract_country_city(raw)
    wf = detect_work_format(raw, wf_map)
    contact = extract_contact(raw)
    technologies = extract_technologies(raw, tech_list)
    company = extract_company(raw)

    ch = normalize_channel_username(post.channel_username)
    vid = f"{ch}-{post.post_id}"

    source_url = f"https://t.me/s/{ch}"

    avg = salary.avg_value
    if salary.min_value is not None and salary.max_value is not None:
        avg = (salary.min_value + salary.max_value) / 2

    return Vacancy(
        id=vid,
        source_channel=ch,
        source_url=source_url,
        post_url=post.post_url,
        post_date=post.post_date,
        raw_text=raw,
        cleaned_text=cleaned,
        detected_specializations=specs,
        detected_grade=grade,
        detected_salary_min=salary.min_value,
        detected_salary_max=salary.max_value,
        detected_salary_avg=avg,
        salary_currency=salary.currency,
        work_format=wf,
        country=country,
        city=city,
        company=company,
        contact=contact,
        technologies=technologies,
        required_experience=_extract_experience(raw),
        employment_type=_extract_employment(raw),
        vacancy_score=score,
        vacancy_substantive_score=substantive,
        is_duplicate=False,
        duplicate_group_id=None,
        duplicate_reason=None,
        parsed_at=datetime.now(timezone.utc),
    )


def run_parse(config: AppConfig, cli_overrides: dict | None = None) -> ParseResult:
    """
    Основной сценарий MVP.

    ``cli_overrides`` — простой dict для сериализации в ``run_params`` (опционально).
    """
    try_load_dotenv()

    channel_fetcher = _channel_parser(config)

    date_from = parse_date(config.search.date_from)
    date_to = parse_date(config.search.date_to)

    all_warnings: list[str] = []

    if date_from and date_to:
        span = (date_to - date_from).days + 1
        cap = min(config.search.max_days, 365)
        df_clamped, dt_clamped = clamp_range_days(date_from, date_to, cap)
        if span > cap:
            all_warnings.append(
                f"Запрошенный период {span} дн. обрезан до {cap} дн. "
                f"({df_clamped.isoformat()} — {dt_clamped.isoformat()})."
            )
        config = config.model_copy(
            update={
                "search": config.search.model_copy(
                    update={
                        "date_from": df_clamped.isoformat(),
                        "date_to": dt_clamped.isoformat(),
                    }
                )
            }
        )
        date_from, date_to = df_clamped, dt_clamped

    all_warnings.append(
        "Telegram: Telethon (MTProto). Убедитесь, что активна сессия и заданы TELEGRAM_API_ID / TELEGRAM_API_HASH."
    )

    # Предвычисляем все словари один раз — иначе они пересобирались бы на каждом из тысяч постов.
    specs_map = config.merged_specializations()
    tech_list = config.merged_technologies()
    wf_map = config.merged_work_format_synonyms()
    exclude_kw = config.merged_exclude_keywords()
    threshold = config.parser.vacancy_score_threshold

    posts_fetched = 0
    candidates: list[Vacancy] = []

    channels_unique: list[str] = []
    for ch in config.sources.channels:
        u = normalize_channel_username(ch)
        if u and u not in channels_unique:
            channels_unique.append(u)

    # Каналы опрашиваются параллельно в рамках одной Telethon-сессии (см. parser.channel_concurrency).
    # Результат — список (username, posts, warning) в произвольном порядке: пост-обработка и так
    # сортирует выдачу по дате, поэтому порядок каналов значения не имеет.
    fetch_started = time.monotonic()
    fetch_results = channel_fetcher.fetch_posts_many(channels_unique)
    fetch_elapsed = time.monotonic() - fetch_started

    all_posts: list[RawPost] = []
    for ch, posts, warn in fetch_results:
        if warn:
            all_warnings.append(warn)
            _LOG.warning("Канал @%s: %s", ch, warn)
        posts_fetched += len(posts)
        all_posts.extend(posts)

    _LOG.info(
        "Выборка завершена: %s постов с %s каналов за %.1f с.",
        posts_fetched,
        len(channels_unique),
        fetch_elapsed,
    )

    def _process_one(post: RawPost) -> Vacancy | None:
        v = raw_post_to_vacancy(post, config, spec_map=specs_map, tech_list=tech_list, wf_map=wf_map)
        if v.vacancy_score < threshold:
            return None
        if not apply_post_filters(v, config, specs_map, exclude_keywords=exclude_kw):
            return None
        return v

    # CPU-bound пост-обработка: каждый пост проходит через десяток regex-экстракторов.
    # Python re реализован на C и отпускает GIL, поэтому ThreadPoolExecutor реально ускоряет
    # обработку на нескольких ядрах. На малом объёме оверхед пула не оправдан — идём в один поток.
    workers = max(2, min(8, (os.cpu_count() or 4)))
    process_started = time.monotonic()
    if len(all_posts) < 200 or workers == 1:
        for i, post in enumerate(all_posts, 1):
            v = _process_one(post)
            if v is not None:
                candidates.append(v)
            if i % 500 == 0:
                _LOG.info("Обработано постов: %s/%s; прошло вакансий: %s", i, len(all_posts), len(candidates))
    else:
        _LOG.info(
            "Пост-обработка %s постов в %s потоков (regex отпускает GIL).",
            len(all_posts),
            workers,
        )
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="post-proc") as ex:
            chunksize = max(16, len(all_posts) // (workers * 8) or 16)
            for i, v in enumerate(ex.map(_process_one, all_posts, chunksize=chunksize), 1):
                if v is not None:
                    candidates.append(v)
                if i % 500 == 0:
                    _LOG.info(
                        "Обработано постов: %s/%s; прошло вакансий: %s",
                        i,
                        len(all_posts),
                        len(candidates),
                    )

    process_elapsed = time.monotonic() - process_started
    _LOG.info(
        "Пост-обработка завершена за %.1f с. После фильтров осталось вакансий-кандидатов: %s",
        process_elapsed,
        len(candidates),
    )

    dedup_started = time.monotonic()
    unique, dups = deduplicate_vacancies(candidates, config)
    _LOG.info(
        "Дедупликация: %s уникальных, %s дубликатов за %.1f с.",
        len(unique),
        len(dups),
        time.monotonic() - dedup_started,
    )

    sort_vacancies_for_report(candidates)
    sort_vacancies_for_report(unique)
    sort_vacancies_for_report(dups)

    metrics = build_metrics(
        vacancies_all=candidates,
        vacancies_unique=unique,
        duplicates=dups,
        posts_collected=posts_fetched,
    )
    metrics.warnings.extend(all_warnings)

    run_params = config.to_serializable_dict()
    if cli_overrides:
        run_params["cli_overrides"] = cli_overrides

    parse_warnings = "; ".join(all_warnings) if all_warnings else None

    return ParseResult(
        run_params=run_params,
        posts_fetched=posts_fetched,
        parse_warnings=parse_warnings,
        vacancies_all=candidates,
        vacancies_unique=unique,
        duplicates=dups,
        metrics=metrics,
    )
