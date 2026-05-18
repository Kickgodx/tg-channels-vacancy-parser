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

  const hasActiveRun = runs.data?.some((r) => r.status === "running" || r.status === "pending");

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
