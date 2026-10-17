import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import ParseForm from "../components/ParseForm";
import type { RunCreateBody } from "../types/api";

export default function DashboardPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 30_000,
  });

  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.listRuns(),
    refetchInterval: 5_000,
  });

  const startRun = useMutation({
    mutationFn: (body: RunCreateBody) => api.createRun(body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${data.run_id}`);
    },
  });

  const cacheStats = useQuery({
    queryKey: ["ai-cache-stats"],
    queryFn: () => api.getAiCacheStats(),
    enabled: Boolean(health.data?.database),
    refetchInterval: 30_000,
  });

  const clearCache = useMutation({
    mutationFn: () => api.clearAiCache(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-cache-stats"] });
    },
  });

  const hasActiveRun = runs.data?.some(
    (r) => r.status === "queued" || r.status === "running",
  );

  return (
    <div className="dashboard-layout">
      <div className="main-col">
        <div className="card">
          <h2>Статус backend</h2>
          {health.data ? (
            <p>
              API: <strong>{health.data.status}</strong>, БД:{" "}
              {health.data.database ? "подключена" : <span className="error">нет</span>}
            </p>
          ) : health.error ? (
            <p className="error">Backend недоступен. Запустите docker compose up.</p>
          ) : (
            <p>Загрузка…</p>
          )}
        </div>

        <div className="card">
          <h2>Кэш LLM</h2>
          <p className="muted">
            Ответы модели хранятся в PostgreSQL. TTL задаётся в config.yaml (
            <code>ai.cache_ttl_days</code>).
          </p>
          <p>
            Записей в кэше: <strong>{cacheStats.data?.count ?? "—"}</strong>
          </p>
          <button
            type="button"
            className="secondary danger"
            disabled={!health.data?.database || clearCache.isPending || hasActiveRun}
            onClick={() => {
              if (
                !window.confirm(
                  "Удалить все записи кэша LLM? Следующий прогон снова вызовет API для всех постов.",
                )
              ) {
                return;
              }
              clearCache.mutate();
            }}
          >
            {clearCache.isPending ? "Очистка…" : "Очистить кэш LLM"}
          </button>
          {hasActiveRun && <p className="warning">Очистка недоступна во время активного прогона.</p>}
          {clearCache.error && <p className="error">{(clearCache.error as Error).message}</p>}
          {clearCache.isSuccess && (
            <p className="success">
              Удалено записей: {clearCache.data.deleted}, осталось: {clearCache.data.remaining}
            </p>
          )}
        </div>

        <div className="card">
          <h2>Новый прогон</h2>
          <ParseForm
            onSubmit={(body) => startRun.mutate(body)}
            disabled={hasActiveRun || !health.data?.database}
            isSubmitting={startRun.isPending}
          />
          {hasActiveRun && <p className="warning">Уже выполняется прогон — дождитесь завершения.</p>}
          {startRun.error && <p className="error">{(startRun.error as Error).message}</p>}
        </div>
      </div>

      <aside className="sidebar-col">
        <div className="card">
          <h2>История прогонов</h2>
          {runs.isLoading && <p>Загрузка…</p>}
          {runs.data?.length === 0 && <p>Прогонов пока нет.</p>}
          {runs.data && runs.data.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Статус</th>
                  <th>Уник.</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}…</Link>
                    </td>
                    <td>{r.status}</td>
                    <td>{r.unique_vacancies ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </aside>
    </div>
  );
}
