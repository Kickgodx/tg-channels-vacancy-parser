"""Дедупликация вакансий: хэши и fuzzy."""

from __future__ import annotations

from rapidfuzz import fuzz

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.extraction.normalizer import normalize_for_match
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import sha256_text, short_id_from_hash
from tg_jobs_parser.utils.text import first_sentence


def deduplicate_vacancies(vacancies: list[Vacancy], config: AppConfig) -> tuple[list[Vacancy], list[Vacancy]]:
    """
    Помечает ``is_duplicate``, ``duplicate_group_id``, ``duplicate_reason``.

    Возвращает ``(unique_list, duplicate_list)`` с теми же объектами.
    """
    threshold = config.parser.vacancy_fuzzy_duplicate_threshold
    prefix_len = config.parser.fuzzy_compare_prefix_len

    groups: dict[str, list[Vacancy]] = {}
    raw_hashes: dict[str, str] = {}
    norm_hashes: dict[str, str] = {}

    unique: list[Vacancy] = []
    duplicates: list[Vacancy] = []

    def group_key_parts(v: Vacancy) -> str:
        spec = ",".join(sorted(v.detected_specializations))
        sal = f"{v.detected_salary_min}-{v.detected_salary_max}-{v.salary_currency}"
        contact = v.contact or ""
        head = first_sentence(v.cleaned_text, prefix_len)
        return f"{spec}|{v.company or ''}|{sal}|{contact}|{head}"

    for v in vacancies:
        rh = sha256_text(v.raw_text)
        nh = sha256_text(normalize_for_match(v.cleaned_text))

        assigned_group: str | None = None
        reason: str | None = None

        if rh in raw_hashes:
            assigned_group = raw_hashes[rh]
            reason = "exact_raw_hash"
        elif nh in norm_hashes:
            assigned_group = norm_hashes[nh]
            reason = "normalized_hash"
        else:
            candidate_key = group_key_parts(v)
            best_gid: str | None = None
            best_score = 0
            for gid, members in groups.items():
                ref = members[0]
                ref_key = group_key_parts(ref)
                score = fuzz.token_set_ratio(candidate_key, ref_key)
                if score >= threshold and score > best_score:
                    best_score = score
                    best_gid = gid
            if best_gid:
                assigned_group = best_gid
                reason = f"fuzzy:{best_score}"

        if assigned_group is None:
            gid = short_id_from_hash(sha256_text(rh + nh))
            assigned_group = gid
            groups.setdefault(gid, []).append(v)
            raw_hashes[rh] = gid
            norm_hashes[nh] = gid
            v.is_duplicate = False
            v.duplicate_group_id = gid
            v.duplicate_reason = None
            unique.append(v)
        else:
            v.is_duplicate = True
            v.duplicate_group_id = assigned_group
            v.duplicate_reason = reason
            groups.setdefault(assigned_group, []).append(v)
            duplicates.append(v)

    return unique, duplicates
