export type VacancySortField =
  | "post_date"
  | "detected_salary_avg"
  | "work_format"
  | "detected_grade"
  | "salary_currency"
  | "vacancy_score";

export type VacancySortOrder = "asc" | "desc";

export const SORTABLE_COLUMNS: VacancySortField[] = [
  "post_date",
  "detected_salary_avg",
  "salary_currency",
  "work_format",
  "detected_grade",
  "vacancy_score",
];
