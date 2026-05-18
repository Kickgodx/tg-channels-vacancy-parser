import type {
  ConfigDefaults,
  LlmModelsCatalog,
  MetricsSummary,
  ProgressEvent,
  RunCreateBody,
  RunDetail,
  RunStatus,
  Vacancy,
} from "../types/api";

const API_BASE =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000/api/v1";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
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

  listVacancies: (params?: Record<string, string | number>) => {
    const q = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== "" && v !== undefined) q.set(k, String(v));
      });
    }
    const qs = q.toString();
    return request<VacancyListResponse>(`/vacancies${qs ? `?${qs}` : ""}`);
  },

  /** Агрегаты для графиков (те же фильтры, что у listVacancies, без пагинации). */
  getVacancyMetrics: (params?: Record<string, string>) => {
    const q = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== "" && v !== undefined) q.set(k, String(v));
      });
    }
    const qs = q.toString();
    return request<MetricsSummary>(`/vacancies/metrics${qs ? `?${qs}` : ""}`);
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
