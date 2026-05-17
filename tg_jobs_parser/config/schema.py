"""Pydantic-схема config.yaml и встроенные словари по умолчанию."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tg_jobs_parser.ai.models_catalog import LlmModelEntry


DEFAULT_SPECIALIZATIONS: dict[str, list[str]] = {
    "QA": ["qa", "quality assurance", "тестировщик", "тестирование"],
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
    "Android": ["android", "андроид"],
    "iOS": ["ios", "swift developer", "apple developer"],
    "Software Engineer": ["software engineer", "software developer", "инженер-программист"],
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
        description="Параллельные LLM-запросы; для OpenRouter :free рекомендуется 1.",
    )
    requests_per_minute: float = Field(
        default=12.0,
        gt=0,
        le=300,
        description=(
            "Целевой темп запросов (пауза между вызовами). "
            "Free OpenRouter: ~16/мин — ставьте 12; платные тарифы — выше."
        ),
    )
    max_retries_on_429: int = Field(default=0, ge=0, le=10)
    retry_base_delay_seconds: float = Field(default=5.0, ge=1, le=120)
    stop_llm_when_rate_limited: bool = Field(
        default=True,
        description="При X-RateLimit-Remaining=0 / дневном лимите — отключить LLM до конца прогона (rules).",
    )
    use_few_shot: bool = False
    prompt_version: str = "v1"
    use_cache: bool = True
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
        default=None,
        description="Нижняя граница (ISO YYYY-MM-DD). Пусто — без явной нижней границы в конфиге.",
    )
    date_to: str | None = Field(
        default=None,
        description=(
            "Верхняя граница: ISO-дата, либо today/сегодня, либо пусто — при запуске подставляется сегодня."
        ),
    )
    max_days: int = 365


class FiltersConfig(BaseModel):
    specializations: list[str] = Field(default_factory=list)
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
