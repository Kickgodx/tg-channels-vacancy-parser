export type VacancySortField =
  | "post_date"
  | "detected_salary_avg"
  | "detected_salary_avg_rub"
  | "work_format"
  | "detected_grade"
  | "salary_currency"
  | "vacancy_score"
  | "match_score";

export type VacancySortOrder = "asc" | "desc";

export const SORTABLE_COLUMNS: VacancySortField[] = [
  "post_date",
  "detected_salary_avg",
  "detected_salary_avg_rub",
  "salary_currency",
  "work_format",
  "detected_grade",
  "vacancy_score",
  "match_score",
];

/** Вторая ступень сортировки (`then_by`): только пара «дата ↔ зарплата ₽» — под неё есть индексы. */
export type VacancyThenSortField = "post_date" | "detected_salary_avg_rub";

/** Поле, которое можно добавить второй ступенью к `sortBy`, или null. */
export function thenSortPartner(sortBy: VacancySortField): VacancyThenSortField | null {
  if (sortBy === "post_date") return "detected_salary_avg_rub";
  if (sortBy === "detected_salary_avg_rub") return "post_date";
  return null;
}

/** `then_by` из URL/пресета, если он образует допустимую пару с `sortBy`. */
export function parseThenSort(
  sortBy: VacancySortField,
  raw: string | null | undefined,
): VacancyThenSortField | null {
  const partner = thenSortPartner(sortBy);
  return partner != null && raw === partner ? partner : null;
}
