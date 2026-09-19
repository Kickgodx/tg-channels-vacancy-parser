export type RunStatus = "pending" | "running" | "completed" | "cancelled" | "failed";

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
  q1?: number | null;
  median?: number | null;
  q3?: number | null;
  max?: number | null;
}

export interface SalaryMonthlyBucket {
  period: string;
  count: number;
  mean?: number | null;
  median?: number | null;
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
}

export interface ProgressEvent {
  posts_processed: number;
  posts_total: number;
  vacancies_found: number;
  phase: string;
  ai_llm_disabled?: boolean;
  ai_llm_disabled_reason?: string | null;
  ai_tokens_in?: number;
  ai_tokens_out?: number;
  ai_skipped_score?: number;
  vacancies_preview?: Vacancy[];
}

export interface RunCreateBody {
  channels?: string[];
  search?: Record<string, unknown>;
  filters?: Record<string, unknown>;
  parser?: Record<string, unknown>;
  ai?: Record<string, unknown>;
  telegram_session_file?: string;
}

export interface ParseFormState {
  channelsText: string;
  dateFrom: string;
  dateTo: string;
  anySpecialization: boolean;
  specializations: string[];
  excludeKeywordsText: string;
  countriesText: string;
  citiesText: string;
  workFormats: string[];
  vacancyScoreThreshold: number;
  vacancyMinSubstantive: number;
  telethonSession: string;
  useAi: boolean;
  aiModel: string;
  aiFallbacks: string[];
  ignoreLlmCache: boolean;
  channelSearchQuery: string;
}
