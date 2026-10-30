"""Дедупликация вакансий: хэши, fuzzy по ключу и по полному тексту, вакансии прошлых прогонов."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz import fuzz, process

from jobdesk.config.schema import AppConfig
from jobdesk.extraction.normalizer import normalize_for_match
from jobdesk.models.vacancy import Vacancy
from jobdesk.utils.company import company_key
from jobdesk.utils.hashing import sha256_text, short_id_from_hash
from jobdesk.utils.text import first_sentence

# Языки программирования: шаблон «Java-разработчик» / «Python-разработчик» одной компании —
# разные позиции почти одним текстом.
PROGRAMMING_LANGUAGES = frozenset(
    {
        "python",
        "java",
        "go",
        "javascript",
        "typescript",
        "php",
        "c++",
        "c#",
        ".net",
        "kotlin",
        "swift",
        "ruby",
        "rust",
        "scala",
        "1с",
        "c",
        "dart",
        "elixir",
        "haskell",
        "perl",
        "objective-c",
        "r",
        "lua",
        "clojure",
        "erlang",
        "flutter",
    }
)

# Причина дубля по полному тексту: перепост с правками (у известной вакансии — из прошлого прогона).
TEXT_REASON = "fuzzy_text"


@dataclass(frozen=True)
class _TextRep:
    """Представитель группы для сравнения полного текста."""

    gid: str
    text: str
    grade: str
    company: str | None
    roles: frozenset[str]
    languages: frozenset[str]
    post_date: datetime | None
    order: int


def grades_compatible(a: str | None, b: str | None) -> bool:
    """Разные известные грейды — разные позиции (шаблон компании: junior и senior почти одним текстом)."""
    known = {g for g in (a, b) if g and g != "unknown"}
    return len(known) < 2


def _overlap_or_unknown(a: frozenset[str], b: frozenset[str]) -> bool:
    return not a or not b or bool(a & b)


def _languages(v: Vacancy) -> frozenset[str]:
    return frozenset(t.lower() for t in v.technologies or [] if t.lower() in PROGRAMMING_LANGUAGES)


def companies_compatible(a: str | None, b: str | None) -> bool:
    """Две разные известные компании — разные вакансии, даже при шаблонном тексте."""
    return not (a and b and a != b)


def _within(a: datetime | None, b: datetime | None, window: timedelta | None) -> bool:
    return window is None or a is None or b is None or abs(a - b) <= window


def _can_reach(a: int, b: int, threshold: int) -> bool:
    """``fuzz.ratio`` не выше ``2·min/(a+b)``: заведомо далёкие по длине тексты не сравниваем."""
    return a + b > 0 and 200 * min(a, b) / (a + b) >= threshold


class VacancyDeduplicator:
    """
    Инкрементальная дедупликация: ``add()`` можно вызывать по мере накопления кандидатов.

    Состояние (хэши и ключи групп) сохраняется между вызовами, поэтому результат
    идентичен разовой дедупликации всего списка в том же порядке. Это позволяет
    сливать уникальные вакансии в БД ещё до конца обработки.

    Порядок проверок: хэш текста → fuzzy по составному ключу (группы прогона) → fuzzy по
    полному тексту (группы прогона и вакансии прошлых прогонов из ``seed``). Fuzzy
    сравнивает вакансию не со всеми группами, а с блоками, где она могла бы оказаться:
    тот же набор специализаций (``parser.fuzzy_dedup_block_by_specializations``), та же
    компания или тот же контакт — так почти-дубль с другими извлечёнными специализациями
    находится по компании или контакту.
    """

    def __init__(self, config: AppConfig) -> None:
        p = config.parser
        self._threshold = p.vacancy_fuzzy_duplicate_threshold
        self._text_threshold = p.fuzzy_text_duplicate_threshold
        self._prefix_len = p.fuzzy_compare_prefix_len
        self._block_by_specs = p.fuzzy_dedup_block_by_specializations
        # Перепост с правками ищется среди вакансий не дальше этого срока по дате поста.
        self._text_window = timedelta(days=p.cross_run_dedup_days) if p.cross_run_dedup_days > 0 else None
        self._groups: dict[str, list[Vacancy]] = {}
        self._group_rep: dict[str, str] = {}  # group_id → id первой вакансии группы
        self._order: dict[str, int] = {}  # group_id → порядок создания (равный счёт — первая)
        # Блок → {group_id: ключ представителя группы}. Ключ кэшируется: иначе на каждой
        # новой вакансии он пересчитывался бы для всех групп заново.
        self._blocks: dict[str, dict[str, str]] = {}
        self._text_blocks: dict[str, dict[str, _TextRep]] = {}
        self._raw_hashes: dict[str, str] = {}
        self._norm_hashes: dict[str, str] = {}
        # Группы вакансий из БД (``seed``) и те из них, что уже встречены в этом прогоне.
        self._known: set[str] = set()
        self._claimed: set[str] = set()
        self.unique: list[Vacancy] = []
        self.duplicates: list[Vacancy] = []

    def _block_keys(self, v: Vacancy) -> list[str]:
        if not self._block_by_specs:
            return [""]
        keys = ["s:" + ",".join(sorted(v.detected_specializations))]
        ck = company_key(v.company)
        if ck:
            keys.append("c:" + ck)
        contact = (v.contact or "").strip().lower()
        if contact:
            keys.append("t:" + contact)
        return keys

    def _group_key_parts(self, v: Vacancy) -> str:
        spec = ",".join(sorted(v.detected_specializations))
        sal = f"{v.detected_salary_min}-{v.detected_salary_max}-{v.salary_currency}"
        contact = v.contact or ""
        head = first_sentence(v.cleaned_text, self._prefix_len)
        return f"{spec}|{v.company or ''}|{sal}|{contact}|{head}"

    def _new_group(self, gid: str, rep_id: str, rh: str, nh: str) -> None:
        self._group_rep.setdefault(gid, rep_id)
        self._order.setdefault(gid, len(self._order))
        self._raw_hashes.setdefault(rh, gid)
        self._norm_hashes.setdefault(nh, gid)

    def _index_text(self, gid: str, v: Vacancy, text: str, keys: list[str]) -> None:
        if self._text_threshold > 100:
            return
        rep = _TextRep(
            gid=gid,
            text=text,
            grade=v.detected_grade,
            company=company_key(v.company),
            roles=frozenset(v.detected_specializations or []),
            languages=_languages(v),
            post_date=v.post_date,
            order=self._order[gid],
        )
        for key in keys:
            self._text_blocks.setdefault(key, {})[gid] = rep

    def seed(self, known: Iterable[Vacancy]) -> None:
        """Вакансии прошлых прогонов (из БД): перепост с правками станет их дублем, а не новой вакансией.

        Сами они в ``unique`` не попадают. Встреченный снова тот же текст (тот же ``id``) —
        обычная уникальная вакансия прогона: её запись в БД обновит дату и привязку к прогону.
        """
        for v in known:
            rh = sha256_text(v.raw_text)
            norm = normalize_for_match(v.cleaned_text)
            gid = "db:" + v.id[:16]
            if gid in self._group_rep:
                continue
            self._new_group(gid, v.id, rh, sha256_text(norm))
            self._known.add(gid)
            self._index_text(gid, v, norm, self._block_keys(v))

    def _composite_match(self, keys: list[str], candidate_key: str) -> tuple[str, str] | None:
        if self._threshold > 100:  # порог > 100 — fuzzy по ключу выключен
            return None
        pool: dict[str, str] = {}
        for key in keys:
            pool.update(self._blocks.get(key, {}))
        if not pool:
            return None
        # При равном счёте extractOne возвращает первую группу — по порядку создания.
        pool = dict(sorted(pool.items(), key=lambda kv: self._order[kv[0]]))
        best = process.extractOne(candidate_key, pool, scorer=fuzz.token_set_ratio, score_cutoff=self._threshold)
        if best is None:
            return None
        _, score, gid = best
        return gid, f"fuzzy:{score}"

    def _text_match(self, keys: list[str], v: Vacancy, text: str) -> tuple[str, str] | None:
        if self._text_threshold > 100:
            return None
        reps: dict[str, _TextRep] = {}
        for key in keys:
            reps.update(self._text_blocks.get(key, {}))
        company = company_key(v.company)
        roles = frozenset(v.detected_specializations or [])
        languages = _languages(v)
        best: tuple[float, int, str] | None = None
        for rep in reps.values():
            if not grades_compatible(rep.grade, v.detected_grade) or not companies_compatible(rep.company, company):
                continue
            if not _within(rep.post_date, v.post_date, self._text_window):
                continue
            # Разные роли или разные языки при почти одинаковом тексте — соседние позиции шаблона.
            if not _overlap_or_unknown(rep.roles, roles) or not _overlap_or_unknown(rep.languages, languages):
                continue
            if not _can_reach(len(rep.text), len(text), self._text_threshold):
                continue
            score = fuzz.ratio(text, rep.text, score_cutoff=self._text_threshold)
            if score and (best is None or (score, -rep.order) > (best[0], -best[1])):
                best = (score, rep.order, rep.gid)
        if best is None:
            return None
        return best[2], f"{TEXT_REASON}:{round(best[0])}"

    def add(self, vacancies: Iterable[Vacancy]) -> tuple[list[Vacancy], list[Vacancy]]:
        """
        Прогнать новую порцию кандидатов через дедуп.

        Помечает ``is_duplicate``, ``duplicate_group_id``, ``duplicate_reason``,
        ``duplicate_of`` (id первой вакансии группы — для записи появления дубля; у дубля
        вакансии прошлого прогона — её id в БД). Возвращает ``(new_unique, new_duplicates)`` —
        только из этой порции; накопленные итоги доступны в ``self.unique`` / ``self.duplicates``.
        """
        new_unique: list[Vacancy] = []
        new_duplicates: list[Vacancy] = []

        for v in vacancies:
            rh = sha256_text(v.raw_text)
            norm = normalize_for_match(v.cleaned_text)
            nh = sha256_text(norm)
            keys = self._block_keys(v)
            candidate_key = self._group_key_parts(v)

            assigned_group: str | None = None
            reason: str | None = None
            if rh in self._raw_hashes:
                assigned_group, reason = self._raw_hashes[rh], "exact_raw_hash"
            elif nh in self._norm_hashes:
                assigned_group, reason = self._norm_hashes[nh], "normalized_hash"
            else:
                match = self._composite_match(keys, candidate_key) or self._text_match(keys, v, norm)
                if match is not None:
                    assigned_group, reason = match

            if (
                assigned_group in self._known
                and self._group_rep[assigned_group] == v.id
                and assigned_group not in self._claimed
            ):
                # Тот же текст, что у вакансии в БД: это она сама, встреченная снова.
                self._claimed.add(assigned_group)
                self._groups.setdefault(assigned_group, []).append(v)
                self._mark_unique(v, assigned_group)
                new_unique.append(v)
            elif assigned_group is None:
                gid = short_id_from_hash(sha256_text(rh + nh))
                self._groups.setdefault(gid, []).append(v)
                self._new_group(gid, v.id, rh, nh)
                for key in keys:
                    self._blocks.setdefault(key, {})[gid] = candidate_key
                self._index_text(gid, v, norm, keys)
                self._mark_unique(v, gid)
                new_unique.append(v)
            else:
                v.is_duplicate = True
                v.duplicate_group_id = assigned_group
                v.duplicate_reason = reason
                v.duplicate_of = self._group_rep[assigned_group]
                self._groups.setdefault(assigned_group, []).append(v)
                new_duplicates.append(v)

        self.unique.extend(new_unique)
        self.duplicates.extend(new_duplicates)
        return new_unique, new_duplicates

    @staticmethod
    def _mark_unique(v: Vacancy, gid: str) -> None:
        v.is_duplicate = False
        v.duplicate_group_id = gid
        v.duplicate_reason = None
        v.duplicate_of = None


def deduplicate_vacancies(vacancies: list[Vacancy], config: AppConfig) -> tuple[list[Vacancy], list[Vacancy]]:
    """
    Помечает ``is_duplicate``, ``duplicate_group_id``, ``duplicate_reason``.

    Возвращает ``(unique_list, duplicate_list)`` с теми же объектами.
    """
    deduplicator = VacancyDeduplicator(config)
    deduplicator.add(vacancies)
    return deduplicator.unique, deduplicator.duplicates
