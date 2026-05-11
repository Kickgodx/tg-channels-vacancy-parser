"""Pydantic-схема config.yaml и встроенные словари по умолчанию."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class SearchConfig(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
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
