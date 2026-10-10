import type { Vacancy, VacancyListItem } from "../types/api";

/** Привести полную Vacancy или уже slim-элемент к VacancyListItem для таблицы/модалки. */
export function toVacancyListItem(v: Vacancy | VacancyListItem): VacancyListItem {
  if ("cleaned_text" in v) {
    const text = v.cleaned_text || v.raw_text || "";
    const preview = text.length > 120 ? `${text.slice(0, 120)}…` : text;
    return {
      id: v.id,
      source_channel: v.source_channel,
      source_url: v.source_url,
      post_url: v.post_url,
      post_date: v.post_date,
      preview,
      detected_specializations: v.detected_specializations,
      detected_grade: v.detected_grade,
      detected_salary_min: v.detected_salary_min,
      detected_salary_max: v.detected_salary_max,
      detected_salary_avg: v.detected_salary_avg,
      detected_salary_avg_rub: v.detected_salary_avg_rub,
      salary_currency: v.salary_currency,
      work_format: v.work_format,
      country: v.country,
      city: v.city,
      company: v.company,
      contact: v.contact,
      technologies: v.technologies,
      application_urls: v.application_urls,
      vacancy_score: v.vacancy_score,
      extraction_source: v.extraction_source,
      ai_confidence: v.ai_confidence,
      ai_notes: v.ai_notes,
      match_score: v.match_score,
      match_explanation: v.match_explanation,
    };
  }
  return v;
}
