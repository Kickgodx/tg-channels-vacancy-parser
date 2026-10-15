import type {
  AuthUser,
  CompanyStats,
  ConfigDefaults,
  CoverLetter,
  CoverLetterTone,
  HhStatus,
  HhSyncAccepted,
  HhSyncJob,
  HhSyncRequest,
  LlmJob,
  LlmJobAccepted,
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
  VacancyListItem,
  VacancyMatch,
} from "../types/api";

const API_BASE =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000/api/v1";

const TOKEN_KEY = "tgjp_token";
const VITE_API_KEY = (import.meta.env.VITE_API_KEY as string | undefined)?.trim() || "";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(
    message: string,
    opts: { status: number; code?: string; retryable?: boolean },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status;
    this.code = opts.code ?? "INTERNAL";
    this.retryable = opts.retryable ?? false;
  }
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (VITE_API_KEY) headers["X-API-Key"] = VITE_API_KEY;
  return headers;
}

/** Query только для EventSource — не для обычного REST (логи/Referer). */
function sseAuthQuery(): string {
  const parts: string[] = [];
  const token = getToken();
  if (token) parts.push(`access_token=${encodeURIComponent(token)}`);
  if (VITE_API_KEY) parts.push(`api_key=${encodeURIComponent(VITE_API_KEY)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

function handleUnauthorized(path: string) {
  // 401 на самом логине — это «неверные креды», не редирект
  if (path.startsWith("/auth/")) return;
  setToken(null);
  window.location.assign("/login");
}

function parseErrorDetail(
  status: number,
  detail: unknown,
  fallback: string,
): ApiError {
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as { code?: unknown; message?: unknown; retryable?: unknown };
    const message =
      typeof d.message === "string"
        ? d.message
        : typeof detail === "string"
          ? detail
          : fallback;
    return new ApiError(message, {
      status,
      code: typeof d.code === "string" ? d.code : "INTERNAL",
      retryable: d.retryable === true,
    });
  }
  if (typeof detail === "string") {
    return new ApiError(detail, { status, code: "INTERNAL" });
  }
  return new ApiError(fallback, { status, code: "INTERNAL" });
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
  items: VacancyListItem[];
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
    let detail: unknown = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? j;
    } catch {
      detail = await res.text();
    }
    throw parseErrorDetail(res.status, detail, res.statusText);
  }
  return res.json() as Promise<T>;
}

const LLM_JOB_POLL_MS = 1000;
/**
 * Потолок ожидания LLM-джобы на клиенте — страховка от вечного ожидания. До error
 * джобу доводит backend (LLM_RQ_JOB_TIMEOUT_SECONDS × LLM_JOB_MAX_ATTEMPTS + recover);
 * сама джоба по таймауту клиента не отменяется, матч/письмо попадут в кэш.
 */
const LLM_JOB_WAIT_MS = 30 * 60 * 1000;
const HH_SYNC_POLL_MS = 1500;
const HH_SYNC_TERMINAL: ReadonlySet<HhSyncJob["status"]> = new Set(["completed", "failed"]);
const RUN_POLL_MS = 2000;
/** Как часто SSE переключается на poll при долгом молчании (не hard-fail). */
const SSE_FALLBACK_IDLE_MS = 5 * 60 * 1000;

const RUN_TERMINAL: ReadonlySet<RunStatus> = new Set([
  "completed",
  "partial",
  "failed",
  "cancelled",
]);

async function pollRunUntilTerminal(
  runId: string,
  onProgress?: (data: ProgressEvent) => void,
  signal?: { cancelled: boolean },
): Promise<RunDetail> {
  while (!signal?.cancelled) {
    const run = await request<RunDetail>(`/runs/${runId}`);
    if (run.progress) onProgress?.(run.progress);
    if (RUN_TERMINAL.has(run.status)) return run;
    await sleep(RUN_POLL_MS);
  }
  throw new ApiError("Подписка на прогон отменена", { status: 499, code: "CANCELLED" });
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Промежуточные статусы джобы (у импорта — растущий `import_result`). */
type LlmJobListener = (job: LlmJob) => void;

/** Сигнал ожидания джобы: отмена вызывающим (`signal`) или таймаут LLM_JOB_WAIT_MS. */
function llmJobWaitSignal(signal?: AbortSignal): { signal: AbortSignal; dispose: () => void } {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => {
    ctrl.abort(
      new ApiError("LLM: задача выполняется слишком долго — откройте результат позже", {
        status: 408,
        code: "TIMEOUT",
        retryable: true,
      }),
    );
  }, LLM_JOB_WAIT_MS);
  const onAbort = () => ctrl.abort(signal?.reason);
  if (signal?.aborted) onAbort();
  else signal?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: ctrl.signal,
    dispose: () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
  };
}

async function pollLlmJob(
  jobId: string,
  onUpdate: LlmJobListener | undefined,
  signal: AbortSignal,
): Promise<LlmJob> {
  for (;;) {
    signal.throwIfAborted();
    const job = await request<LlmJob>(`/llm-jobs/${jobId}`, { signal });
    if (job.status === "done" || job.status === "error") return job;
    onUpdate?.(job);
    await sleep(LLM_JOB_POLL_MS, signal);
  }
}

/** Ждать завершения через SSE; при обрыве — poll до terminal или до `signal`. */
function waitLlmJobSse(
  jobId: string,
  onUpdate: LlmJobListener | undefined,
  signal: AbortSignal,
): Promise<LlmJob> {
  const url = `${API_BASE}/llm-jobs/${jobId}/events${sseAuthQuery()}`;

  return new Promise((resolve, reject) => {
    let last: LlmJob | null = null;
    let settled = false;
    const timer = window.setTimeout(() => {
      // Долгое молчание SSE → poll, не ошибка.
      if (!settled) startFallbackPoll();
    }, SSE_FALLBACK_IDLE_MS);

    const es = new EventSource(url);

    const close = () => {
      settled = true;
      window.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      es.close();
    };

    const onAbort = () => {
      if (settled) return;
      close();
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });

    const finish = (job: LlmJob) => {
      if (settled) return;
      close();
      resolve(job);
    };

    const startFallbackPoll = () => {
      if (settled) return;
      close();
      void pollLlmJob(jobId, onUpdate, signal).then(resolve, reject);
    };

    es.addEventListener("status", (ev) => {
      try {
        last = JSON.parse((ev as MessageEvent).data) as LlmJob;
        if (last.status === "done" || last.status === "error") finish(last);
        else onUpdate?.(last);
      } catch {
        /* ignore parse */
      }
    });

    es.addEventListener("done", () => {
      if (last && (last.status === "done" || last.status === "error")) {
        finish(last);
        return;
      }
      startFallbackPoll();
    });

    es.onerror = () => {
      if (settled) return;
      if (last) {
        startFallbackPoll();
        return;
      }
      window.setTimeout(() => {
        if (!settled && !last) startFallbackPoll();
      }, 1500);
    };
  });
}

async function awaitLlmJob(
  jobId: string,
  onUpdate?: LlmJobListener,
  signal?: AbortSignal,
): Promise<LlmJob> {
  const wait = llmJobWaitSignal(signal);
  try {
    return await waitLlmJobSse(jobId, onUpdate, wait.signal);
  } catch (err) {
    if (wait.signal.aborted) throw err;
    return await pollLlmJob(jobId, onUpdate, wait.signal);
  } finally {
    wait.dispose();
  }
}

/**
 * POST мэтч/письмо/импорт: 200 готовый результат или 202 job → SSE (fallback poll).
 * `signal` отменяет и запрос, и ожидание джобы (закрыли модалку, ушли со страницы).
 */
async function postInteractiveLlm<T>(
  path: string,
  init: RequestInit,
  pick: (job: LlmJob) => T | null | undefined,
  onUpdate?: LlmJobListener,
  signal?: AbortSignal,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...init.headers,
    },
  });
  if (res.status === 401) handleUnauthorized(path);
  if (res.status === 200) return (await res.json()) as T;
  if (res.status === 202) {
    const accepted = (await res.json()) as LlmJobAccepted;
    const job = await awaitLlmJob(accepted.job_id, onUpdate, signal);
    if (job.status === "error") {
      throw new ApiError(job.error_message || "LLM: ошибка задачи", {
        status: 500,
        code: "INTERNAL",
        retryable: true,
      });
    }
    const value = pick(job);
    if (value == null) {
      throw new ApiError("LLM: пустой результат задачи", { status: 500, code: "INTERNAL" });
    }
    return value;
  }
  let detail: unknown = res.statusText;
  try {
    const j = await res.json();
    detail = j.detail ?? j;
  } catch {
    detail = await res.text();
  }
  throw parseErrorDetail(res.status, detail, res.statusText);
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

  getVacancy: (id: string) => request<Vacancy>(`/vacancies/${encodeURIComponent(id)}`),

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

  /**
   * Скачать export с Authorization / X-API-Key (вместо голого &lt;a href&gt;).
   * Возвращает пометку об обрезке («выгружено N из M»), если backend упёрся в EXPORT_MAX_ROWS.
   */
  downloadExport: async (url: string, filename: string): Promise<string | null> => {
    const res = await fetch(url, { headers: authHeaders() });
    if (res.status === 401) handleUnauthorized(url);
    if (!res.ok) {
      let detail: unknown = res.statusText;
      try {
        const j = await res.json();
        detail = j.detail ?? j;
      } catch {
        detail = await res.text();
      }
      throw parseErrorDetail(res.status, detail, res.statusText);
    }
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(objectUrl);
    const truncated = res.headers.get("X-Export-Truncated");
    if (!truncated) return null;
    const [exported, total] = truncated.split("/");
    return `Выгружено ${exported} из ${total} — лимит экспорта. Уточните фильтры, чтобы выгрузить остальное.`;
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
  /** Без LLM — сразу 200; с LLM — 202 и джоба, `onProgress` получает частичный итог. */
  importVacancies: (
    texts: string[],
    sourceUrl?: string,
    onProgress?: (partial: VacancyImportResponse) => void,
    signal?: AbortSignal,
  ) =>
    postInteractiveLlm<VacancyImportResponse>(
      "/vacancies/import",
      { method: "POST", body: JSON.stringify({ texts, source_url: sourceUrl || null }) },
      (job) => job.import_result,
      (job) => {
        if (job.import_result) onProgress?.(job.import_result);
      },
      signal,
    ),

  hhStatus: () => request<HhStatus>("/hh/status"),

  /** 202: sync поставлен в очередь; ход и итог — hhSyncJob / subscribeHhSyncJob. */
  hhSync: (body?: HhSyncRequest) =>
    request<HhSyncAccepted>("/hh/sync", {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),

  hhSyncJobs: (limit = 10) => request<HhSyncJob[]>(`/hh/sync-jobs?limit=${limit}`),

  hhSyncJob: (jobId: string) => request<HhSyncJob>(`/hh/sync-jobs/${jobId}`),

  /** SSE со статусом sync до completed/failed; при обрыве — poll. Возвращает отписку. */
  subscribeHhSyncJob(
    jobId: string,
    onUpdate: (job: HhSyncJob) => void,
    onError: (err: Error) => void,
  ): () => void {
    let cancelled = false;
    let polling = false;
    const es = new EventSource(`${API_BASE}/hh/sync-jobs/${jobId}/events${sseAuthQuery()}`);

    const startFallbackPoll = () => {
      if (cancelled || polling) return;
      polling = true;
      es.close();
      void (async () => {
        while (!cancelled) {
          const job = await request<HhSyncJob>(`/hh/sync-jobs/${jobId}`);
          if (cancelled) return;
          onUpdate(job);
          if (HH_SYNC_TERMINAL.has(job.status)) return;
          await sleep(HH_SYNC_POLL_MS);
        }
      })().catch((err: unknown) => {
        if (!cancelled) onError(err instanceof Error ? err : new Error(String(err)));
      });
    };

    es.addEventListener("status", (ev) => {
      try {
        onUpdate(JSON.parse((ev as MessageEvent).data) as HhSyncJob);
      } catch {
        /* ignore parse */
      }
    });
    es.addEventListener("done", () => es.close());
    es.onerror = () => {
      // EventSource сам переподключается; надёжнее дочитать статус опросом.
      if (es.readyState !== EventSource.CLOSED) startFallbackPoll();
    };

    return () => {
      cancelled = true;
      es.close();
    };
  },

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

  /** Матчинг: кэш → сразу; иначе 202 job + poll. */
  matchVacancy: (resumeId: number, vacancyId: string, force = false, signal?: AbortSignal) =>
    postInteractiveLlm<VacancyMatch>(
      `/resumes/${resumeId}/match/${vacancyId}${force ? "?force=true" : ""}`,
      { method: "POST" },
      (job) => job.match,
      undefined,
      signal,
    ),

  getVacancyMatch: (resumeId: number, vacancyId: string) =>
    request<VacancyMatch>(`/resumes/${resumeId}/match/${vacancyId}`),

  /** Письмо: кэш → сразу; иначе 202 job + poll. */
  generateCoverLetter: (
    resumeId: number,
    vacancyId: string,
    tone: CoverLetterTone,
    force = false,
    signal?: AbortSignal,
  ) =>
    postInteractiveLlm<CoverLetter>(
      `/resumes/${resumeId}/cover-letter/${vacancyId}${force ? "?force=true" : ""}`,
      { method: "POST", body: JSON.stringify({ tone }) },
      (job) => job.cover_letter,
      undefined,
      signal,
    ),

  getCoverLetter: (resumeId: number, vacancyId: string) =>
    request<CoverLetter>(`/resumes/${resumeId}/cover-letter/${vacancyId}`),

  subscribeRunEvents(
    runId: string,
    onProgress: (data: ProgressEvent) => void,
    onDone: () => void,
    onError: (err: Error) => void,
  ): () => void {
    let settled = false;
    let gotProgress = false;
    let cancelled = false;
    const cancelFlag = { cancelled: false };

    const es = new EventSource(`${API_BASE}/runs/${runId}/events${sseAuthQuery()}`);

    const finishDone = () => {
      if (settled || cancelled) return;
      settled = true;
      window.clearTimeout(timer);
      es.close();
      onDone();
    };

    const startFallbackPoll = () => {
      if (settled || cancelled) return;
      settled = true;
      window.clearTimeout(timer);
      es.close();
      void pollRunUntilTerminal(runId, onProgress, cancelFlag).then(
        () => {
          if (!cancelled) onDone();
        },
        (err: unknown) => {
          if (!cancelled) {
            onError(err instanceof Error ? err : new Error(String(err)));
          }
        },
      );
    };

    // Idle SSE → poll; без hard-fail по времени.
    const timer = window.setTimeout(() => {
      startFallbackPoll();
    }, SSE_FALLBACK_IDLE_MS);

    es.addEventListener("progress", (ev) => {
      try {
        gotProgress = true;
        onProgress(JSON.parse((ev as MessageEvent).data) as ProgressEvent);
      } catch (e) {
        if (!settled && !cancelled) {
          settled = true;
          window.clearTimeout(timer);
          es.close();
          onError(e instanceof Error ? e : new Error(String(e)));
        }
      }
    });

    es.addEventListener("done", () => {
      finishDone();
    });

    es.onerror = () => {
      if (settled || cancelled) return;
      // Первый error часто при connect — ждём progress; иначе fallback poll
      if (gotProgress) {
        startFallbackPoll();
        return;
      }
      window.setTimeout(() => {
        if (settled || cancelled || gotProgress) return;
        startFallbackPoll();
      }, 1500);
    };

    return () => {
      cancelled = true;
      cancelFlag.cancelled = true;
      window.clearTimeout(timer);
      es.close();
    };
  },
};

export type { MetricsSummary, ProgressEvent, Vacancy };
