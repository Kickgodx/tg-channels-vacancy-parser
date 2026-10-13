"""Pydantic-схема config.yaml и встроенные словари по умолчанию."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tg_jobs_parser.ai.models_catalog import LlmModelEntry

# Значения OpenRouter ``reasoning.effort`` (по убыванию).
REASONING_EFFORTS = ("max", "xhigh", "high", "medium", "low", "minimal", "none")


DEFAULT_SPECIALIZATIONS: dict[str, list[str]] = {
    "QA": ["qa", "quality assurance", "тестировщик", "тестирование"],
    "Тестировщик": ["тестировщик", "tester"],
    "Manual QA": ["manual qa", "ручной тестировщик"],
    "AQA": [
        "aqa",
        "automation qa",
        "автотестировщик",
        "автоматизатор тестирования",
    ],
    "Automation QA": ["automation qa engineer"],
    "SDET": ["sdet", "software development engineer in test"],
    "Python": ["python", "питон", "python developer"],
    "Java": ["java", "java developer"],
    "Backend": ["backend", "бэкенд", "бэкэнд", "бэк-енд"],
    "DevOps": ["devops", "dev ops", "девопс"],
    "Data Engineer": ["data engineer", "дата инженер", "инженер данных"],
    "AI": [
        "ai engineer",
        "ai developer",
        "ai specialist",
        "ai/ml",
        "ai разработчик",
        "machine learning engineer",
        "ml engineer",
        "llm engineer",
        "llm developer",
        "prompt engineer",
        "nlp engineer",
        "computer vision engineer",
        "computer vision",
        "deep learning engineer",
        "искусственный интеллект",
        "инженер ии",
        "разработчик ии",
        "нейросет",
        "mlops engineer",
    ],
    "Android": ["android", "андроид"],
    "iOS": ["ios", "swift developer", "apple developer"],
    "Software Engineer": [
        "software engineer",
        "software developer",
        "инженер-программист",
    ],
    "Программист, разработчик": ["программист, разработчик", "программист"],
    "Go": ["go", "golang", "go developer", "golang developer"],
    "Golang": ["golang", "go lang"],
    "Frontend": ["frontend", "front-end", "front end", "фронтенд", "react developer"],
    "Fullstack": ["fullstack", "full-stack", "full stack", "фулстек"],
    "Analyst": ["analyst", "аналитик"],
    "System Analyst": ["system analyst", "системный аналитик", "са"],
    "Business Analyst": ["business analyst", "бизнес-аналитик", "ба"],
    "Product Manager": ["product manager", "продакт", "продакт-менеджер", "pm"],
    "Project Manager": ["project manager", "проектный менеджер", "пм"],
}


DEFAULT_EXCLUDE_KEYWORDS = [
    "junior",
    "intern",
    "internship",
    "стажировка",
    "без опыта",
    "обучение",
    "курсы",
    "реклама",
    "ищу работу",
    "резюме",
    "hr",
    "sales",
    "продажник",
    "не it",
]


DEFAULT_TECHNOLOGIES = [
    "Python",
    "Java",
    "Kotlin",
    "Swift",
    "Go",
    "Golang",
    "JavaScript",
    "TypeScript",
    "React",
    "Spring Boot",
    "Spring",
    "Kafka",
    "Docker",
    "Kubernetes",
    "PostgreSQL",
    "MySQL",
    "Redis",
    "MongoDB",
    "Selenium",
    "Playwright",
    "Pytest",
    "Appium",
    "REST",
    "GraphQL",
    "Linux",
    "AWS",
    "GCP",
    "Azure",
]


WORK_FORMAT_SYNONYMS: dict[str, list[str]] = {
    "remote": [
        "remote",
        "удаленка",
        "удалёнка",
        "full remote",
        "полностью удаленно",
        "полностью удалённо",
        "wfh",
        "work from home",
    ],
    "hybrid": ["hybrid", "гибрид", "гибридный"],
    "office": ["office", "офис", "онсайт", "onsite"],
    "relocation": ["relocation", "релокация", "relocate", "переезд"],
}


class SourcesConfig(BaseModel):
    channels: list[str] = Field(default_factory=list)


class AiMergeConfig(BaseModel):
    prefer_ai_for: list[str] = Field(
        default_factory=lambda: [
            "detected_specializations",
            "detected_salary_min",
            "detected_salary_max",
            "salary_currency",
            "work_format",
            "detected_grade",
            "country",
            "city",
            "application_urls",
        ]
    )
    min_confidence: float = Field(default=0.45, ge=0, le=1)


class AiConfig(BaseModel):
    enabled: bool = False
    mode: str = Field(
        default="assist",
        description="assist — AI дополняет/уточняет поля; primary — AI основной источник.",
    )
    provider: str = Field(default="openrouter", description="openrouter")
    model: str = Field(
        default="",
        description="Id основной модели (из llm_models.yaml). Пусто — default_model из реестра.",
    )
    llm_models_path: str | None = Field(
        default=None,
        description="Путь к llm_models.yaml; по умолчанию — рядом с config.yaml или корень проекта.",
    )
    fallback_models: list[str] = Field(
        default_factory=list,
        description="Запасные модели (id из llm_models.yaml). Пусто — default_fallbacks из реестра.",
    )
    match_model: str = Field(
        default="",
        description=("Модель для матчинга резюме к вакансиям (обычно reasoning). Пусто — берётся ``model``."),
    )
    match_reasoning_enabled: bool = Field(
        default=False,
        description="true для reasoning-модели матчинга (OpenRouter reasoning вместо json_object).",
    )
    match_reasoning_effort: str = Field(
        default="",
        description=(
            "Уровень рассуждений матчинга и писем (OpenRouter ``reasoning.effort``): "
            "max / xhigh / high / medium / low / minimal / none. Действует только при "
            "``match_reasoning_enabled: true``; пусто — дефолт модели. Неподдерживаемый "
            "моделью уровень OpenRouter сводит к ближайшему."
        ),
    )
    cover_letter_model: str = Field(
        default="",
        description=("Модель для генерации сопроводительных писем. Пусто — берётся ``match_model``, затем ``model``."),
    )
    base_url: str = Field(default="https://openrouter.ai/api/v1")
    max_input_chars: int = Field(default=6000, ge=500, le=32000)
    max_posts_per_run: int = Field(
        default=0,
        ge=0,
        description="0 — без лимита LLM-вызовов за прогон.",
    )
    skip_if_vacancy_score_below: int = Field(
        default=0,
        ge=0,
        description="Не вызывать LLM для постов с score ниже (0 = все посты).",
    )
    concurrency: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Параллельные LLM-запросы к OpenRouter (парсинг).",
    )
    interactive_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Параллельные фоновые джобы мэтчинга и сопроводительных писем.",
    )
    interactive_per_user_limit: int = Field(
        default=2,
        ge=1,
        le=16,
        description="Сколько LLM-джоб (queued+running) одновременно на одного пользователя.",
    )
    requests_per_minute: float = Field(
        default=12.0,
        gt=0,
        le=500,
        description="Целевой темп запросов (пауза между вызовами к API).",
    )
    max_retries_on_429: int = Field(default=0, ge=0, le=10)
    retry_base_delay_seconds: float = Field(default=5.0, ge=1, le=120)
    max_retries_on_error: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Повторы при транзиентном сбое (5xx, таймаут, обрыв соединения) с backoff 2/4/8… с.",
    )
    stop_llm_when_rate_limited: bool = Field(
        default=True,
        description="При X-RateLimit-Remaining=0 / дневном лимите — отключить LLM до конца прогона (rules).",
    )
    use_few_shot: bool = False
    prompt_version: str = Field(
        default="extract-v6",
        description=(
            "Должен совпадать с EXTRACT_PROMPT_VERSION в ai/prompts.py. "
            "Кэш всегда ключуется константой; расхождение → warning."
        ),
    )
    use_cache: bool = True
    cache_backend: str = Field(
        default="postgres",
        description="postgres — таблица ai_extraction_cache; file — локальные JSON.",
    )
    cache_ttl_days: int = Field(
        default=90,
        ge=0,
        description="Срок жизни записи кэша в днях; 0 — без срока (только ручная очистка).",
    )
    cache_path: str = ".cache/ai_extractions"
    request_timeout_seconds: float = 90.0
    max_completion_tokens: int = Field(
        default=5000,
        ge=64,
        le=32000,
        description="Лимит выходных токенов на один запрос (OpenRouter max_tokens).",
    )
    temperature: float = Field(default=0.1, ge=0, le=2)
    reasoning_enabled: bool = Field(
        default=False,
        description="Reasoning-модели OpenRouter: для JSON-экстракции лучше false.",
    )
    allow_unknown_specializations: bool = True
    merge: AiMergeConfig = Field(default_factory=AiMergeConfig)

    @model_validator(mode="before")
    @classmethod
    def migrate_fallback_model(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "fallback_models" not in data:
            legacy = data.get("fallback_model")
            if legacy:
                data = dict(data)
                data["fallback_models"] = [legacy] if isinstance(legacy, str) else legacy
        if "models" in data:
            data = dict(data)
            data.pop("models", None)
        return data

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        allowed = {"assist", "primary"}
        if v not in allowed:
            raise ValueError(f"ai.mode must be one of {allowed}")
        return v

    @field_validator("match_reasoning_effort", mode="before")
    @classmethod
    def validate_reasoning_effort(cls, v: object) -> str:
        normalized = str(v or "").strip().lower()
        if normalized and normalized not in REASONING_EFFORTS:
            raise ValueError(f"ai.match_reasoning_effort must be one of {REASONING_EFFORTS}")
        return normalized

    @field_validator("cache_backend")
    @classmethod
    def validate_cache_backend(cls, v: str) -> str:
        normalized = (v or "postgres").strip().lower()
        if normalized not in {"postgres", "file"}:
            raise ValueError("ai.cache_backend must be 'postgres' or 'file'")
        return normalized

    def resolved_match_model(self) -> str:
        """Модель матчинга: ``match_model`` или, если не задана, основная ``model``."""
        return self.match_model.strip() or self.model.strip()

    def resolved_cover_letter_model(self) -> str:
        """Модель писем: ``cover_letter_model`` → ``match_model`` → ``model``."""
        return self.cover_letter_model.strip() or self.resolved_match_model()

    def resolved_fallback_models(self) -> list[str]:
        """Запасные модели без дубликатов и без совпадения с ``model``."""
        primary = self.model.strip()
        seen = {primary}
        out: list[str] = []
        for raw in self.fallback_models:
            m = raw.strip()
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out


class SearchConfig(BaseModel):
    date_from: str | None = Field(
        default="week_ago",
        description=(
            "Нижняя граница: ISO-дата, либо week_ago/week/today-7d/неделя "
            "(сегодня минус 7 дней), либо пусто — без нижней границы."
        ),
    )
    date_to: str | None = Field(
        default=None,
        description=("Верхняя граница: ISO-дата, либо today/сегодня, либо пусто — при запуске подставляется сегодня."),
    )
    max_days: int = 365


class FiltersConfig(BaseModel):
    specializations: list[str] = Field(default_factory=list)
    any_specialization: bool = Field(
        default=False,
        description=(
            "Любая IT-роль: не фильтровать посты по списку specializations; "
            "LLM не отклоняет вакансию только из-за стека вне target_specializations."
        ),
    )
    exclude_keywords: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    """Пустые списки country/city означают «без фильтра по локации»."""
    work_formats: list[str] = Field(
        default_factory=list,
        description="Значения: remote, hybrid, office, relocation, any, exclude_office",
    )


class ParserConfig(BaseModel):
    vacancy_score_threshold: int = 3
    vacancy_min_substantive_score: int = Field(
        default=0,
        ge=0,
        description=(
            "Минимальный vacancy_substantive_score: баллы без учёта email/@. "
            "Например 1 отсечёт пост «только упомянули @канал без зарплаты/офис/vacancy…» при низком пороге общего счёта."
        ),
    )
    max_posts_per_channel: int = Field(
        default=5000,
        ge=1,
        description=(
            "Telethon: при заданных date_from/date_to — максимум сообщений, которые можно **просмотреть** "
            "(идём от новых к старым, пока не выйдем за date_from или не упрёмся в этот лимит)."
        ),
    )
    progress_batch_posts: int = Field(
        default=25,
        ge=1,
        le=500,
        description="Как часто отдавать снимок прогресса в UI (каждые N постов).",
    )
    db_flush_every_posts: int = Field(
        default=500,
        ge=1,
        le=100000,
        description=(
            "Промежуточная запись в БД: сливать накопленные уникальные вакансии каждые "
            "N обработанных постов или при накоплении db_flush_pending_vacancies штук — "
            "что наступит раньше (если прогон идёт через backend с vacancy_batch_sink). "
            "При падении прогона уже записанные батчи не теряются."
        ),
    )
    db_flush_pending_vacancies: int = Field(
        default=100,
        ge=1,
        le=100000,
        description=(
            "Промежуточная запись в БД: сливать, когда накопилось N ещё не записанных "
            "уникальных вакансий (или каждые db_flush_every_posts постов — что раньше)."
        ),
    )
    channel_concurrency: int = Field(
        default=4,
        ge=1,
        le=32,
        description=(
            "Сколько каналов опрашивать одновременно в рамках одной Telethon-сессии. "
            "1 = последовательно (старое поведение). Разумный диапазон — 3..8: больше повышает риск FloodWait."
        ),
    )
    request_timeout_seconds: float = 20.0
    sleep_between_requests_seconds: float = 1.0
    vacancy_fuzzy_duplicate_threshold: int = 88
    fuzzy_compare_prefix_len: int = 500


class FxConfig(BaseModel):
    """Курсы валют для зарплат в рублях (ЦБ РФ)."""

    enabled: bool = True
    provider: str = Field(default="cbr", description="Источник курсов (пока только cbr).")
    default_currency_if_missing: str = Field(
        default="RUB",
        description="Если salary_currency пуст — считать эту валюту.",
    )
    display_currency: str = Field(default="RUB")
    sync_days_on_startup: int = Field(
        default=7,
        ge=0,
        le=90,
        description="Сколько последних дней подтянуть в exchange_rates при старте API (0 — не грузить).",
    )
    request_timeout_seconds: float = 20.0


class RetentionConfig(BaseModel):
    """Хранение вакансий: TTL → soft delete (archived_at)."""

    vacancy_ttl_days: int = Field(
        default=40,
        ge=0,
        description=(
            "Через сколько дней архивировать вакансию (по post_date, а без неё — по parsed_at). 0 — не архивировать."
        ),
    )


DEFAULT_HH_SEARCH_TEXT = (
    'Python AND (автотест* OR AQA OR "QA Automation" OR pytest OR QAA OR QA Auto '
    'OR "Fullstack QA" OR Automation QA OR SDET OR автоматизатор)'
)


class HhSearchConfig(BaseModel):
    """Параметры GET /vacancies для HH sync."""

    text: str = Field(default=DEFAULT_HH_SEARCH_TEXT)
    professional_role: list[str] = Field(default_factory=lambda: ["124", "96"])
    per_page: int = Field(default=100, ge=1, le=100)


class HhConfig(BaseModel):
    """Парсинг вакансий с HH.ru (admin-only sync, без LLM)."""

    enabled: bool = True
    listing_ttl_days: int = Field(
        default=40,
        ge=0,
        description=(
            "TTL кэша короткой выдачи hh_listings (по last_seen_at). "
            "0 — не чистить. Протухшая строка → detail снова запросится."
        ),
    )
    search: HhSearchConfig = Field(default_factory=HhSearchConfig)
    max_rps: float = Field(default=3.0, gt=0, description="Лимит запросов к HH API.")
    db_flush_batch_size: int = Field(
        default=30,
        ge=1,
        le=500,
        description=(
            "Сколько Vacancy копить в памяти перед UPSERT+commit. "
            "Меньше — меньше RAM и потеря при обрыве; больше — меньше round-trip к БД."
        ),
    )
    api_base: str = Field(default="https://api.hh.ru")
    host: str = Field(default="hh.ru")
    app_name: str = Field(default="PersonalJobSearch")
    app_version: str = Field(default="1.0")


class OutputConfig(BaseModel):
    directory: str = "output"
    excel_filename: str = "vacancies.xlsx"
    json_filename: str = "vacancies.json"
    charts_directory: str = "charts"
    save_progress_batches: bool = Field(
        default=True,
        description="Сохранять partial_*.json по мере обработки (для UI и восстановления).",
    )
    partial_batches_subdirectory: str = "partial"


class TelegramConfig(BaseModel):
    """Получение сообщений только через Telethon (MTProto, файл сессии)."""

    model_config = ConfigDict(extra="ignore")

    telethon_session_file: str = Field(
        default="sessions/tg_jobs.session",
        description="Файл сессии Telethon (после первой авторизации).",
    )


class DictionariesConfig(BaseModel):
    specializations: dict[str, list[str]] = Field(default_factory=dict)
    exclude_keywords: list[str] | None = None
    technologies: list[str] = Field(default_factory=list)
    work_format_synonyms: dict[str, list[str]] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """Корневая модель конфигурации приложения."""

    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    ai: AiConfig = Field(default_factory=AiConfig)
    llm_model_catalog: list[LlmModelEntry] = Field(
        default_factory=list,
        description="Каталог из llm_models.yaml (заполняется при load_config).",
    )
    search: SearchConfig = Field(default_factory=SearchConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    parser: ParserConfig = Field(default_factory=ParserConfig)
    fx: FxConfig = Field(default_factory=FxConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    hh: HhConfig = Field(default_factory=HhConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    dictionaries: DictionariesConfig = Field(default_factory=DictionariesConfig)

    @field_validator("search")
    @classmethod
    def cap_max_days(cls, v: SearchConfig) -> SearchConfig:
        if v.max_days > 365:
            v.max_days = 365
        return v

    def merged_specializations(self) -> dict[str, list[str]]:
        merged = dict(DEFAULT_SPECIALIZATIONS)
        for name, synonyms in self.dictionaries.specializations.items():
            merged.setdefault(name, [])
            merged[name] = list(dict.fromkeys(merged.get(name, []) + list(synonyms)))
        return merged

    def merged_exclude_keywords(self) -> list[str]:
        if self.dictionaries.exclude_keywords is not None:
            base = list(self.dictionaries.exclude_keywords)
        else:
            base = list(DEFAULT_EXCLUDE_KEYWORDS)
        extras = list(self.filters.exclude_keywords)
        return list(dict.fromkeys(base + extras))

    def merged_technologies(self) -> list[str]:
        tech = list(self.dictionaries.technologies) if self.dictionaries.technologies else []
        base = DEFAULT_TECHNOLOGIES + tech
        return list(dict.fromkeys(base))

    def merged_work_format_synonyms(self) -> dict[str, list[str]]:
        out = {k: list(v) for k, v in WORK_FORMAT_SYNONYMS.items()}
        for fmt, syns in self.dictionaries.work_format_synonyms.items():
            out.setdefault(fmt, [])
            out[fmt] = list(dict.fromkeys(out.get(fmt, []) + list(syns)))
        return out

    def to_serializable_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")
