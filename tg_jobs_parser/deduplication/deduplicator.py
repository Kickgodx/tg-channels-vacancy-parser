"""Дедупликация вакансий: хэши и fuzzy."""

from __future__ import annotations

from collections.abc import Iterable

from rapidfuzz import fuzz, process

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.extraction.normalizer import normalize_for_match
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import sha256_text, short_id_from_hash
from tg_jobs_parser.utils.text import first_sentence


class VacancyDeduplicator:
    """
    Инкрементальная дедупликация: ``add()`` можно вызывать по мере накопления кандидатов.

    Состояние (хэши и ключи групп) сохраняется между вызовами, поэтому результат
    идентичен разовой дедупликации всего списка в том же порядке. Это позволяет
    сливать уникальные вакансии в БД ещё до конца обработки.

    Fuzzy-сравнение — самая дорогая часть (``token_set_ratio`` ~10 мкс на пару, цикл
    по всем группам даёт O(N²) на прогон), поэтому группы разбиты на блоки по набору
    специализаций (``parser.fuzzy_dedup_block_by_specializations``): кандидат
    сравнивается только со своим блоком.
    """

    def __init__(self, config: AppConfig) -> None:
        self._threshold = config.parser.vacancy_fuzzy_duplicate_threshold
        self._prefix_len = config.parser.fuzzy_compare_prefix_len
        self._block_by_specs = config.parser.fuzzy_dedup_block_by_specializations
        self._groups: dict[str, list[Vacancy]] = {}
        self._group_rep: dict[str, str] = {}  # group_id → id первой вакансии группы
        # Блок → {group_id: ключ представителя группы}. Ключ кэшируется: иначе на каждой
        # новой вакансии он пересчитывался бы для всех групп заново.
        self._blocks: dict[str, dict[str, str]] = {}
        self._raw_hashes: dict[str, str] = {}
        self._norm_hashes: dict[str, str] = {}
        self.unique: list[Vacancy] = []
        self.duplicates: list[Vacancy] = []

    def _block_key(self, v: Vacancy) -> str:
        return ",".join(sorted(v.detected_specializations)) if self._block_by_specs else ""

    def _group_key_parts(self, v: Vacancy) -> str:
        spec = ",".join(sorted(v.detected_specializations))
        sal = f"{v.detected_salary_min}-{v.detected_salary_max}-{v.salary_currency}"
        contact = v.contact or ""
        head = first_sentence(v.cleaned_text, self._prefix_len)
        return f"{spec}|{v.company or ''}|{sal}|{contact}|{head}"

    def add(self, vacancies: Iterable[Vacancy]) -> tuple[list[Vacancy], list[Vacancy]]:
        """
        Прогнать новую порцию кандидатов через дедуп.

        Помечает ``is_duplicate``, ``duplicate_group_id``, ``duplicate_reason``,
        ``duplicate_of`` (id первой вакансии группы — для записи появления дубля).
        Возвращает ``(new_unique, new_duplicates)`` — только из этой порции;
        накопленные итоги доступны в ``self.unique`` / ``self.duplicates``.
        """
        new_unique: list[Vacancy] = []
        new_duplicates: list[Vacancy] = []

        for v in vacancies:
            rh = sha256_text(v.raw_text)
            nh = sha256_text(normalize_for_match(v.cleaned_text))

            assigned_group: str | None = None
            reason: str | None = None
            block = self._blocks.setdefault(self._block_key(v), {})
            candidate_key = self._group_key_parts(v)

            if rh in self._raw_hashes:
                assigned_group = self._raw_hashes[rh]
                reason = "exact_raw_hash"
            elif nh in self._norm_hashes:
                assigned_group = self._norm_hashes[nh]
                reason = "normalized_hash"
            elif block and self._threshold <= 100:  # порог > 100 — fuzzy выключен
                # При равном счёте extractOne возвращает первую группу — как и прежний цикл.
                best = process.extractOne(
                    candidate_key,
                    block,
                    scorer=fuzz.token_set_ratio,
                    score_cutoff=self._threshold,
                )
                if best is not None:
                    _, best_score, assigned_group = best
                    reason = f"fuzzy:{best_score}"

            if assigned_group is None:
                gid = short_id_from_hash(sha256_text(rh + nh))
                self._groups.setdefault(gid, []).append(v)
                self._group_rep.setdefault(gid, v.id)
                block[gid] = candidate_key
                self._raw_hashes[rh] = gid
                self._norm_hashes[nh] = gid
                v.is_duplicate = False
                v.duplicate_group_id = gid
                v.duplicate_reason = None
                v.duplicate_of = None
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


def deduplicate_vacancies(vacancies: list[Vacancy], config: AppConfig) -> tuple[list[Vacancy], list[Vacancy]]:
    """
    Помечает ``is_duplicate``, ``duplicate_group_id``, ``duplicate_reason``.

    Возвращает ``(unique_list, duplicate_list)`` с теми же объектами.
    """
    deduplicator = VacancyDeduplicator(config)
    deduplicator.add(vacancies)
    return deduplicator.unique, deduplicator.duplicates
