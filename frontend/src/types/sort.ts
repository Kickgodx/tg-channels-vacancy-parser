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
