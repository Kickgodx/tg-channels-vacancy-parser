import type {
  AuthUser,
  CompanyStats,
  ConfigDefaults,
  CoverLetter,
  CoverLetterTone,
  LlmModelsCatalog,
  MetricsSummary,
  ProgressEvent,
  Resume,
  RunCreateBody,
  RunDetail,
  RunStatus,
  SalaryAnalytics,
  SavedFilter,
  SavedFilterParams,
  SourceStats,
  TokenResponse,
  Vacancy,
  VacancyImportResponse,
  VacancyMatch,
} from "../types/api";

const API_BASE =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000/api/v1";

const TOKEN_KEY = "tgjp_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handleUnauthorized(path: string) {
  // 401 на самом логине — это «неверные креды», не редирект
  if (path.startsWith("/auth/")) return;
  setToken(null);
  window.location.assign("/login");
}

export interface RunSummary {
  id: string;
  status: RunStatus;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  posts_fetched: number;
  parse_warnings?: string | null;
  unique_vacancies?: number | null;
}

export interface VacancyListResponse {
  items: Vacancy[];
  total: number;
  page: number;
  page_size: number;
}

export type VacancyQueryParams = Record<string, string | number | boolean | string[]>;

function appendSearchParams(q: URLSearchParams, params?: VacancyQueryParams) {
  if (!params) return;
  Object.entries(params).forEach(([k, v]) => {
    if (v === "" || v === undefined) return;
    if (Array.isArray(v)) {
      v.forEach((item) => {
        if (item !== "") q.append(k, String(item));
      });
    } else {
      q.set(k, String(v));
    }
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...init?.headers,
    },
  });
  if (res.status === 401) handleUnauthorized(path);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? JSON.stringify(j);
    } catch {
      detail = await res.text();
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; database: boolean }>("/health"),

  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  register: (email: string, password: string) =>
    request<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  me: () => request<AuthUser>("/auth/me"),

  logout: () => {
    setToken(null);
    window.location.assign("/login");
  },

  getConfigDefaults: () => request<ConfigDefaults>("/config/defaults"),

  getLlmModels: () => request<LlmModelsCatalog>("/config/llm-models"),

  searchChannels: (query: string) =>
    request<{ found: string[]; backend: string; query: string }>("/channels/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  listRuns: () => request<RunSummary[]>("/runs"),

  getRun: (id: string) => request<RunDetail>(`/runs/${id}`),

  createRun: (body: RunCreateBody) =>
    request<{ run_id: string; status: RunStatus }>("/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  cancelRun: (id: string) => request<{ ok: boolean }>(`/runs/${id}/cancel`, { method: "POST" }),

  getAiCacheStats: () => request<{ count: number }>("/ai-cache/stats"),

  clearAiCache: () =>
    request<{ deleted: number; remaining: number }>("/ai-cache/clear", { method: "POST" }),

  listVacancies: (params?: VacancyQueryParams) => {
    const q = new URLSearchParams();
    appendSearchParams(q, params);
    const qs = q.toString();
    return request<VacancyListResponse>(`/vacancies${qs ? `?${qs}` : ""}`);
  },

  /** Агрегаты для графиков (те же фильтры, что у listVacancies, без пагинации). */
  getFxRates: () =>
    request<{ rate_date: string | null; source: string; rates: { currency: string; rate_to_rub: number }[] }>(
      "/fx/rates",
    ),

  refreshFxRates: () =>
    request<{
      rate_date: string;
      rates_count: number;
      history_days_synced: number;
      vacancies_processed: number;
      vacancies_updated_rub: number;
      vacancies_skipped_no_rate: number;
    }>("/fx/refresh", { method: "POST" }),

  /** Пересчёт зарплат в ₽ по последнему курсу в БД, без запроса к ЦБ. */
  recalculateSalaryRub: () =>
    request<{
      rate_date: string | null;
      vacancies_processed: number;
      vacancies_updated_rub: number;
      vacancies_skipped_no_rate: number;
    }>("/fx/recalculate-rub", { method: "POST" }),

  getVacancyMetrics: (params?: VacancyQueryParams) => {
    const q = new URLSearchParams();
    appendSearchParams(q, params);
    const qs = q.toString();
    return request<MetricsSummary>(`/vacancies/metrics${qs ? `?${qs}` : ""}`);
  },

  getSalaryAnalytics: (params?: VacancyQueryParams) => {
    const q = new URLSearchParams();
    appendSearchParams(q, params);
    const qs = q.toString();
    return request<SalaryAnalytics>(`/analytics/salaries${qs ? `?${qs}` : ""}`);
  },

  getSourceStats: (params?: VacancyQueryParams) => {
    const q = new URLSearchParams();
    appendSearchParams(q, params);
    const qs = q.toString();
    return request<{ items: SourceStats[] }>(`/analytics/sources${qs ? `?${qs}` : ""}`);
  },

  getCompanyStats: (params?: VacancyQueryParams) => {
    const q = new URLSearchParams();
    appendSearchParams(q, params);
    const qs = q.toString();
    return request<{ items: CompanyStats[] }>(`/analytics/companies${qs ? `?${qs}` : ""}`);
  },

  listFilters: () => request<{ items: SavedFilter[] }>("/filters"),

  createFilter: (name: string, params: SavedFilterParams) =>
    request<SavedFilter>("/filters", {
      method: "POST",
      body: JSON.stringify({ name, params }),
    }),

  deleteFilter: async (id: number) => {
    const res = await fetch(`${API_BASE}/filters/${id}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (res.status === 401) handleUnauthorized(`/filters/${id}`);
    if (!res.ok) throw new Error(`Удаление пресета: ${res.status} ${res.statusText}`);
  },

  exportVacanciesUrl: (params?: VacancyQueryParams) => {
    const q = new URLSearchParams();
    appendSearchParams(q, params);
    const qs = q.toString();
    return `${API_BASE}/vacancies/export.xlsx${qs ? `?${qs}` : ""}`;
  },

  listRunVacancies: (
    runId: string,
    page = 1,
    pageSize = 100,
    sort?: { sort_by?: string; sort_order?: string },
  ) => {
    const q = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (sort?.sort_by) q.set("sort_by", sort.sort_by);
    if (sort?.sort_order) q.set("sort_order", sort.sort_order);
    return request<VacancyListResponse>(`/runs/${runId}/vacancies?${q}`);
  },

  exportRunUrl: (runId: string, kind: "xlsx" | "json") =>
    `${API_BASE}/runs/${runId}/export.${kind}`,

  /** Ручной импорт: тексты вакансий → rules + LLM → запись в БД. */
  importVacancies: (texts: string[], sourceUrl?: string) =>
    request<VacancyImportResponse>("/vacancies/import", {
      method: "POST",
      body: JSON.stringify({ texts, source_url: sourceUrl || null }),
    }),

  listResumes: () => request<{ items: Resume[] }>("/resumes"),

  createResume: (name: string, text: string) =>
    request<Resume>("/resumes", {
      method: "POST",
      body: JSON.stringify({ name, text }),
    }),

  updateResume: (id: number, name: string, text: string) =>
    request<Resume>(`/resumes/${id}`, {
      method: "PUT",
      body: JSON.stringify({ name, text }),
    }),

  deleteResume: async (id: number) => {
    const res = await fetch(`${API_BASE}/resumes/${id}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (res.status === 401) handleUnauthorized(`/resumes/${id}`);
    if (!res.ok) throw new Error(`Удаление резюме: ${res.status} ${res.statusText}`);
  },

  /** Матчинг одной вакансии: без force отдаёт сохранённую оценку, с force — новый вызов LLM. */
  matchVacancy: (resumeId: number, vacancyId: string, force = false) =>
    request<VacancyMatch>(
      `/resumes/${resumeId}/match/${vacancyId}${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),

  getVacancyMatch: (resumeId: number, vacancyId: string) =>
    request<VacancyMatch>(`/resumes/${resumeId}/match/${vacancyId}`),

  /** Сопроводительное письмо: без force отдаёт сохранённое, с force — новый вызов LLM. */
  generateCoverLetter: (
    resumeId: number,
    vacancyId: string,
    tone: CoverLetterTone,
    force = false,
  ) =>
    request<CoverLetter>(
      `/resumes/${resumeId}/cover-letter/${vacancyId}${force ? "?force=true" : ""}`,
      { method: "POST", body: JSON.stringify({ tone }) },
    ),

  getCoverLetter: (resumeId: number, vacancyId: string) =>
    request<CoverLetter>(`/resumes/${resumeId}/cover-letter/${vacancyId}`),

  deleteCoverLetter: async (resumeId: number, vacancyId: string) => {
    const res = await fetch(
      `${API_BASE}/resumes/${resumeId}/cover-letter/${vacancyId}`,
      { method: "DELETE", headers: authHeaders() },
    );
    if (res.status === 401)
      handleUnauthorized(`/resumes/${resumeId}/cover-letter/${vacancyId}`);
    if (!res.ok && res.status !== 404)
      throw new Error(`Удаление письма: ${res.status} ${res.statusText}`);
  },

  subscribeRunEvents(
    runId: string,
    onProgress: (data: ProgressEvent) => void,
    onDone: () => void,
    onError: (err: Error) => void,
  ): () => void {
    const es = new EventSource(`${API_BASE}/runs/${runId}/events`);
    es.addEventListener("progress", (ev) => {
      try {
        onProgress(JSON.parse((ev as MessageEvent).data) as ProgressEvent);
      } catch (e) {
        onError(e instanceof Error ? e : new Error(String(e)));
      }
    });
    es.addEventListener("done", () => {
      es.close();
      onDone();
    });
    es.onerror = () => {
      es.close();
      onError(new Error("SSE connection failed"));
    };
    return () => es.close();
  },
};

export type { MetricsSummary, ProgressEvent, Vacancy };
