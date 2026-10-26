export type RunStatus =
  | "queued"
  | "running"
  | "completed"
  /** Дошёл до конца, но часть постов не разобрана LLM (причина — в error_message). */
  | "partial"
  | "cancelled"
  | "failed";

/** Полная вакансия (detail / import). */
export interface Vacancy {
  id: string;
  source_channel: string;
  source_url?: string;
  post_url?: string | null;
  post_date?: string | null;
  raw_text?: string;
  cleaned_text: string;
  detected_specializations: string[];
  detected_grade: string;
  detected_salary_min?: number | null;
  detected_salary_max?: number | null;
  detected_salary_avg?: number | null;
  detected_salary_avg_rub?: number | null;
  salary_currency?: string | null;
  work_format?: string | null;
  country?: string | null;
  city?: string | null;
  company?: string | null;
  contact?: string | null;
  technologies?: string[];
  application_urls?: string[];
  vacancy_score?: number;
  extraction_source?: string;
  ai_confidence?: number | null;
  ai_notes?: string | null;
  match_score?: number | null;
  match_explanation?: string | null;
  archived_at?: string | null;
}

/** Элемент списка: без полных текстов, только preview. */
export interface VacancyListItem {
  id: string;
  source_post_key?: string;
  source_channel: string;
  source_url?: string;
  post_url?: string | null;
  post_date?: string | null;
  preview: string;
  detected_specializations: string[];
  detected_grade: string;
  detected_salary_min?: number | null;
  detected_salary_max?: number | null;
  detected_salary_avg?: number | null;
  detected_salary_avg_rub?: number | null;
  salary_currency?: string | null;
  work_format?: string | null;
  country?: string | null;
  city?: string | null;
  company?: string | null;
  contact?: string | null;
  technologies?: string[];
  application_urls?: string[];
  vacancy_score?: number;
  extraction_source?: string;
  ai_confidence?: number | null;
  ai_notes?: string | null;
  match_score?: number | null;
  match_explanation?: string | null;
  /** Оценка получена другой моделью матчинга или версией промпта; null — оценки нет. */
  match_stale?: boolean | null;
  archived_at?: string | null;
}

export interface ConfigDefaults {
  channels: string[];
  search: {
    date_from: string | null;
    date_from_resolved: string | null;
    date_to: string;
    date_to_resolved: string;
    max_days: number;
  };
  filters: {
    specializations: string[];
    any_specialization: boolean;
    /** Технологии стека; со специализациями — через И. */
    technologies: string[];
    exclude_keywords: string[];
    countries: string[];
    cities: string[];
    work_formats: string[];
  };
  parser: {
    vacancy_score_threshold: number;
    vacancy_min_substantive_score: number;
  };
  ai: {
    enabled: boolean;
    model: string;
    fallback_models: string[];
    use_cache: boolean;
  };
  telegram: { telethon_session_file: string };
  specialization_options: string[];
  /** Словарь технологий config.yaml — варианты фильтра прогона. */
  technology_options: string[];
  work_format_options: string[];
  grade_options: string[];
}

export interface LlmModelOption {
  id: string;
  label: string;
  provider?: string | null;
}

export interface LlmModelsCatalog {
  default_model: string;
  default_fallbacks: string[];
  models: LlmModelOption[];
  model_ids: string[];
}

export interface MetricsSummary {
  total_posts_fetched: number;
  total_vacancies: number;
  unique_vacancies: number;
  duplicate_count: number;
  vacancies_by_specialization: Record<string, number>;
  vacancies_by_grade: Record<string, number>;
  vacancies_by_work_format: Record<string, number>;
  daily: { period: string; count: number }[];
  vacancies_with_salary: number;
  vacancies_without_salary: number;
  technology_top: Record<string, number>;
  salary_by_specialization: Record<string, { mean?: number; median?: number; count_with_salary: number }>;
  salary_by_grade: Record<string, { mean?: number; median?: number; count_with_salary: number }>;
  grade_distribution: Record<string, number>;
  salary_display?: { display_currency: string; rates_as_of?: string | null; rates_source: string } | null;
  warnings: string[];
}

export interface SalaryGroupStats {
  group: string;
  count: number;
  mean?: number | null;
  min?: number | null;
  p10?: number | null;
  q1?: number | null;
  median?: number | null;
  q3?: number | null;
  p90?: number | null;
  max?: number | null;
}

export interface SalaryMonthlyBucket {
  period: string;
  count: number;
  mean?: number | null;
  median?: number | null;
}

export interface SalaryPeriodGroupBucket {
  period: string;
  group: string;
  count: number;
  mean?: number | null;
  median?: number | null;
  min?: number | null;
  p10?: number | null;
  q1?: number | null;
  q3?: number | null;
  p90?: number | null;
  max?: number | null;
}

export interface SalaryDistributionBucket {
  range: string;
  count: number;
}

export interface SalaryAnalytics {
  by_grade: SalaryGroupStats[];
  by_work_format: SalaryGroupStats[];
  by_specialization: SalaryGroupStats[];
  monthly: SalaryMonthlyBucket[];
  yearly: SalaryMonthlyBucket[];
  by_grade_yearly: SalaryPeriodGroupBucket[];
  by_work_format_yearly: SalaryPeriodGroupBucket[];
  by_grade_monthly: SalaryPeriodGroupBucket[];
  by_work_format_monthly: SalaryPeriodGroupBucket[];
  distribution: SalaryDistributionBucket[];
  salary_display?: { display_currency: string; rates_as_of?: string | null; rates_source: string } | null;
}

export interface SourceStats {
  channel: string;
  vacancies: number;
  duplicates: number;
  unique_share: number;
  avg_score?: number | null;
  with_salary_share: number;
}

export interface CompanyStats {
  company: string;
  vacancies: number;
  avg_score?: number | null;
  with_salary: number;
  avg_salary_rub?: number | null;
}

export interface SavedFilterParams {
  q?: string | null;
  source_channel?: string | null;
  grade?: string | null;
  specializations?: string[];
  technologies?: string[];
  work_format?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  has_salary?: boolean | null;
  include_archived?: boolean;
  sort_by?: string;
  sort_order?: string;
  then_by?: string | null;
  then_order?: string;
}

export interface SavedFilter {
  id: number;
  name: string;
  params: SavedFilterParams;
  created_at: string;
}

export interface Resume {
  id: number;
  name: string;
  text: string;
  created_at: string;
}

export interface AuthUser {
  id: number;
  email: string;
  is_admin: boolean;
  created_at: string;
}

export interface HhStatus {
  enabled: boolean;
  token_configured: boolean;
  email_configured: boolean;
  listing_ttl_days: number;
  search_text: string;
  professional_role: string[];
  listings_total: number;
  listings_with_detail: number;
  listings_without_detail: number;
}

export type HhSyncWindow = "all" | "7d" | "14d" | "30d" | "range";

export interface HhSyncRequest {
  window?: HhSyncWindow;
  date_from?: string;
  date_to?: string;
}

export type HhSyncJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface HhSyncAccepted {
  job_id: string;
  status: HhSyncJobStatus;
}

export interface HhSyncProgress {
  /** search → details (done/total групп) → purge */
  phase: "search" | "details" | "purge";
  done: number;
  total: number;
  searched: number;
  pages: number;
  unique: number;
  duplicates: number;
  details_fetched: number;
  details_skipped: number;
  vacancies_inserted: number;
  vacancies_updated: number;
  errors: number;
}

/** Фоновый HH sync (POST /hh/sync → 202). result у failed — частичный, на момент сбоя. */
export interface HhSyncJob {
  id: string;
  status: HhSyncJobStatus;
  window: string;
  period: number | null;
  date_from: string | null;
  date_to: string | null;
  progress: HhSyncProgress | null;
  result: HhSyncResult | null;
  error_message: string | null;
  attempts: number;
  /** Запрошена отмена; у running — воркер остановит sync перед следующей страницей / группой. */
  cancel_requested: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface HhSyncResult {
  searched: number;
  listings_upserted: number;
  unique: number;
  duplicates: number;
  details_fetched: number;
  details_skipped: number;
  vacancies_inserted: number;
  vacancies_updated: number;
  listings_purged: number;
  pages: number;
  flush_batches: number;
  errors: string[];
  /** Нет у итогов, сохранённых до появления поля. */
  warnings?: string[];
  duration_seconds: number;
  window: string;
  period: number | null;
  date_from: string | null;
  date_to: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export interface SseTicketResponse {
  ticket: string;
  expires_in: number;
}

export type MatchImportance = "critical" | "important" | "secondary";

export interface SkillMatch {
  skill: string;
  importance: MatchImportance;
  evidence: string;
}

export interface MatchBreakdown {
  role_match: number;
  skills_match: number;
  seniority_match: number;
  domain_match: number;
  work_format_match: number;
}

export interface VacancyMatch {
  resume_id: number;
  vacancy_id: string;
  overall_score: number;
  confidence?: number;
  breakdown?: MatchBreakdown;
  explanation?: string | null;
  matching_skills: SkillMatch[];
  missing_skills: SkillMatch[];
  strengths?: string[];
  risks?: string[];
  model: string;
  created_at: string;
  cached: boolean;
  /** Получена другой моделью матчинга или версией промпта — POST без force пересчитает. */
  stale: boolean;
}

export type CoverLetterTone = "neutral" | "friendly" | "formal";

export interface CoverLetter {
  resume_id: number;
  vacancy_id: string;
  text: string;
  tone: string;
  model: string;
  created_at: string;
  cached: boolean;
  /** Получено другой моделью писем или версией промпта (тон не учитывается). */
  stale: boolean;
}

export type LlmJobKind = "match" | "cover_letter" | "import" | "reextract";
export type LlmJobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface LlmJobAccepted {
  job_id: string;
  status: string;
}

export interface LlmJob {
  id: string;
  kind: LlmJobKind;
  status: LlmJobStatus;
  /** null у импорта (kind=import). */
  resume_id: number | null;
  vacancy_id: string | null;
  error_message?: string | null;
  match?: VacancyMatch | null;
  cover_letter?: CoverLetter | null;
  /** Импорт: итог по уже обработанным текстам — растёт, пока джоба running. */
  import_result?: VacancyImportResponse | null;
  /** Переразбор (kind=reextract): итог по уже обработанным вакансиям. */
  reextract_result?: VacancyReextractResponse | null;
  /** Запрошена отмена; у running — воркер остановит задачу в ближайшей точке проверки. */
  cancel_requested: boolean;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export type VacancyReextractStatus = "updated" | "kept" | "error";

export interface VacancyReextractItem {
  index: number;
  vacancy_id: string;
  status: VacancyReextractStatus;
  message?: string | null;
}

export interface VacancyReextractResponse {
  total: number;
  items: VacancyReextractItem[];
  updated: number;
  kept: number;
  errors: number;
  warning?: string | null;
}

export interface VacancyReextractInfo {
  missing_role: number;
  max_per_job: number;
  ai_enabled: boolean;
  active_job_id?: string | null;
}

export interface TechnologyCount {
  name: string;
  count: number;
}

export type VacancyImportStatus = "created" | "exists" | "not_vacancy" | "error";

export interface VacancyImportItem {
  index: number;
  status: VacancyImportStatus;
  message?: string | null;
  vacancy?: Vacancy | null;
}

export interface VacancyImportResponse {
  items: VacancyImportItem[];
  created: number;
  existing: number;
  rejected: number;
  ai_enabled: boolean;
  warning?: string | null;
  /** Текстов в запросе; у выполняемой джобы items короче — это прогресс. */
  total: number;
}

/** Итог `api.importVacancies` (только UI): отменённый импорт — итог по обработанным текстам. */
export interface VacancyImportOutcome {
  result: VacancyImportResponse;
  cancelled: boolean;
}

export interface RunDetail {
  id: string;
  status: RunStatus;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  posts_fetched: number;
  parse_warnings?: string | null;
  unique_vacancies?: number | null;
  metrics?: MetricsSummary | null;
  run_params?: Record<string, unknown> | null;
  error_message?: string | null;
  progress?: ProgressEvent | null;
  progress_updated_at?: string | null;
  cancel_requested?: boolean;
}

export interface ProgressEvent {
  posts_processed: number;
  /** Пока phase === "fetching", это прочитанное на текущий момент — растёт. */
  posts_total: number;
  vacancies_found: number;
  phase: string;
  channels_total?: number;
  channels_fetched?: number;
  ai_llm_disabled?: boolean;
  ai_llm_disabled_reason?: string | null;
  ai_tokens_in?: number;
  ai_tokens_out?: number;
  ai_skipped_score?: number;
  vacancies_preview?: Vacancy[];
}

/** Whitelist overrides прогона — backend отклоняет любое другое поле (422). */
export interface RunCreateBody {
  channels?: string[];
  search?: { date_from?: string; date_to?: string };
  filters?: {
    specializations?: string[];
    any_specialization?: boolean;
    technologies?: string[];
    exclude_keywords?: string[];
    countries?: string[];
    cities?: string[];
    work_formats?: string[];
  };
  parser?: {
    vacancy_score_threshold?: number;
    vacancy_min_substantive_score?: number;
    /** false — перечитать окно целиком, не глядя на чекпоинты каналов. */
    incremental_fetch?: boolean;
  };
  ai?: { enabled?: boolean; model?: string; fallback_models?: string[]; use_cache?: boolean };
}

export interface ParseFormState {
  channelsText: string;
  dateFrom: string;
  dateTo: string;
  anySpecialization: boolean;
  specializations: string[];
  technologies: string[];
  excludeKeywordsText: string;
  countriesText: string;
  citiesText: string;
  workFormats: string[];
  vacancyScoreThreshold: number;
  vacancyMinSubstantive: number;
  useAi: boolean;
  aiModel: string;
  aiFallbacks: string[];
  ignoreLlmCache: boolean;
  fullRescan: boolean;
}
