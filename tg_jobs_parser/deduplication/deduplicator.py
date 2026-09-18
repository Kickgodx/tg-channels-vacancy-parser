"""Дедупликация вакансий: хэши и fuzzy."""

from __future__ import annotations

from collections.abc import Iterable

from rapidfuzz import fuzz

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
    """

    def __init__(self, config: AppConfig) -> None:
        self._threshold = config.parser.vacancy_fuzzy_duplicate_threshold
        self._prefix_len = config.parser.fuzzy_compare_prefix_len
        self._groups: dict[str, list[Vacancy]] = {}
        # group_keys кэширует посчитанный ключ представителя группы — иначе на каждой новой
        # вакансии этот ключ пересчитывался бы O(N) раз для одной и той же группы.
        self._group_keys: dict[str, str] = {}
        self._raw_hashes: dict[str, str] = {}
        self._norm_hashes: dict[str, str] = {}
        self.unique: list[Vacancy] = []
        self.duplicates: list[Vacancy] = []

    def _group_key_parts(self, v: Vacancy) -> str:
        spec = ",".join(sorted(v.detected_specializations))
        sal = f"{v.detected_salary_min}-{v.detected_salary_max}-{v.salary_currency}"
        contact = v.contact or ""
        head = first_sentence(v.cleaned_text, self._prefix_len)
        return f"{spec}|{v.company or ''}|{sal}|{contact}|{head}"

    def add(self, vacancies: Iterable[Vacancy]) -> tuple[list[Vacancy], list[Vacancy]]:
        """
        Прогнать новую порцию кандидатов через дедуп.

        Помечает ``is_duplicate``, ``duplicate_group_id``, ``duplicate_reason``.
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

            if rh in self._raw_hashes:
                assigned_group = self._raw_hashes[rh]
                reason = "exact_raw_hash"
            elif nh in self._norm_hashes:
                assigned_group = self._norm_hashes[nh]
                reason = "normalized_hash"
            else:
                candidate_key = self._group_key_parts(v)
                best_gid: str | None = None
                best_score = 0
                for gid, ref_key in self._group_keys.items():
                    score = fuzz.token_set_ratio(candidate_key, ref_key)
                    if score >= self._threshold and score > best_score:
                        best_score = score
                        best_gid = gid
                if best_gid:
                    assigned_group = best_gid
                    reason = f"fuzzy:{best_score}"

            if assigned_group is None:
                gid = short_id_from_hash(sha256_text(rh + nh))
                self._groups.setdefault(gid, []).append(v)
                self._group_keys[gid] = candidate_key
                self._raw_hashes[rh] = gid
                self._norm_hashes[nh] = gid
                v.is_duplicate = False
                v.duplicate_group_id = gid
                v.duplicate_reason = None
                new_unique.append(v)
            else:
                v.is_duplicate = True
                v.duplicate_group_id = assigned_group
                v.duplicate_reason = reason
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
