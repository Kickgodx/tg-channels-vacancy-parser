import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ProgressEvent } from "../api/client";
import MetricsCharts from "../components/MetricsCharts";
import VacancyTable from "../components/VacancyTable";
import type { Vacancy } from "../types/api";
import type { VacancySortField, VacancySortOrder } from "../types/sort";
import { filterActionableWarnings } from "../utils/runWarnings";

function formatUsd(v: number): string {
  if (v > 0 && v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

function AiStats({ runParams }: { runParams?: Record<string, unknown> | null }) {
  const aiStats = (runParams?.ai_stats ?? null) as Record<string, number> | null;
  const ai = (runParams?.ai ?? null) as { model?: string } | null;
  if (!aiStats) return null;
  const tin = aiStats.tokens_in ?? 0;
  const tout = aiStats.tokens_out ?? 0;
  const tsum = aiStats.tokens_total ?? tin + tout;
  const costIn = aiStats.cost_usd_input as number | undefined;
  const costOut = aiStats.cost_usd_output as number | undefined;
  const costTot = aiStats.cost_usd_total as number | undefined;
  return (
    <div className="ai-stats card-inner">
      <h4>AI</h4>
      <div className="metrics-row">
        <span>input: {tin.toLocaleString()}</span>
        <span>output: {tout.toLocaleString()}</span>
        <span>всего: {tsum.toLocaleString()}</span>
      </div>
      {ai?.model && (
        <p className="ai-model-line">
          Модель: <code>{ai.model}</code>
        </p>
      )}
      {costTot != null && (
        <div className="metrics-row cost-row">
          <span title="Стоимость input (USD)">input: {formatUsd(costIn ?? 0)}</span>
          <span title="Стоимость output (USD)">output: {formatUsd(costOut ?? 0)}</span>
          <span title="Стоимость всего (USD)">total: {formatUsd(costTot)}</span>
        </div>
      )}
      <p className="caption">
        Вызовов {aiStats.calls ?? 0} · кэш {aiStats.cache_hits ?? 0} · без LLM (score){" "}
        {aiStats.skipped_score ?? 0} · не-вакансий {aiStats.dropped_not_job ?? 0} · ошибок{" "}
        {aiStats.errors ?? 0}
      </p>
    </div>
  );
}

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  const qc = useQueryClient();
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [liveVacancies, setLiveVacancies] = useState<Vacancy[]>([]);
  const [sortBy, setSortBy] = useState<VacancySortField>("post_date");
  const [sortOrder, setSortOrder] = useState<VacancySortOrder>("desc");

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId!),
    enabled: !!runId,
    refetchInterval: (q) =>
      q.state.data?.status === "running" || q.state.data?.status === "pending" ? 2000 : false,
  });

  const runFinished =
    run.data?.status === "completed" ||
    run.data?.status === "failed" ||
    run.data?.status === "cancelled" ||
    progress?.phase === "post_processing_done";

  const vacancies = useQuery({
    queryKey: ["run-vacancies", runId, sortBy, sortOrder],
    queryFn: () => api.listRunVacancies(runId!, 1, 500, { sort_by: sortBy, sort_order: sortOrder }),
    enabled: !!runId && !!runFinished,
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelRun(runId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }),
  });

  useEffect(() => {
    if (!runId || (run.data?.status !== "running" && run.data?.status !== "pending")) return;
    return api.subscribeRunEvents(
      runId,
      (snap) => {
        setProgress(snap);
        if (snap.vacancies_preview?.length) {
          setLiveVacancies(snap.vacancies_preview as Vacancy[]);
        }
      },
      () => {
        qc.invalidateQueries({ queryKey: ["run", runId] });
        qc.invalidateQueries({ queryKey: ["run-vacancies", runId] });
      },
      () => {},
    );
  }, [runId, run.data?.status, qc]);

  if (!runId) return null;

  const pct =
    progress && progress.posts_total > 0
      ? Math.round((100 * progress.posts_processed) / progress.posts_total)
      : 0;

  const displayVacancies =
    vacancies.data?.items?.length ? vacancies.data.items : liveVacancies;
  const vacancyTotal = vacancies.data?.total ?? progress?.vacancies_found ?? displayVacancies.length;

  const isActive = run.data?.status === "running" || run.data?.status === "pending";
  const actionableWarnings = filterActionableWarnings(run.data?.parse_warnings);

  return (
    <>
      <p>
        <Link to="/">← Назад</Link>
      </p>

      <div className="card">
        <h2>Прогон {runId.slice(0, 8)}…</h2>
        {run.data && (
          <>
            <p>
              Статус: <strong>{run.data.status}</strong>
              {run.data.unique_vacancies != null && (
                <>
                  {" "}
                  · уникальных: <strong>{run.data.unique_vacancies}</strong>
                </>
              )}
            </p>
            {actionableWarnings.length > 0 && (
              <ul className="run-warnings">
                {actionableWarnings.map((w: string) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {run.data.error_message && <p className="error">{run.data.error_message}</p>}

            {isActive && (
              <>
                <div className="progress-bar">
                  <span style={{ width: `${pct}%` }} />
                </div>
                <p>
                  Постов: {progress?.posts_processed ?? 0} / {progress?.posts_total ?? "?"}, вакансий:{" "}
                  {progress?.vacancies_found ?? 0}, фаза: {progress?.phase ?? "…"}
                </p>
                {progress?.ai_llm_disabled && (
                  <p className="warning">LLM отключён: {progress.ai_llm_disabled_reason}</p>
                )}
                {(progress?.ai_tokens_in || progress?.ai_tokens_out) ? (
                  <p className="caption">
                    Токены LLM: in={progress.ai_tokens_in}, out={progress.ai_tokens_out}
                  </p>
                ) : null}
                {progress?.phase === "post_processing_done" && run.data.status === "running" && (
                  <p>Сохранение в базу…</p>
                )}
                {run.data.status === "running" && progress?.phase !== "post_processing_done" && (
                  <button className="secondary" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                    Отменить
                  </button>
                )}
              </>
            )}

            {!isActive && run.data.metrics && (
              <>
                <p className="success">
                  Постов: {run.data.posts_fetched}; уникальных: {run.data.metrics.unique_vacancies};
                  дубликатов: {run.data.metrics.duplicate_count}
                </p>
                <AiStats runParams={run.data.run_params} />
                <div className="export-links">
                  <a href={api.exportRunUrl(runId, "xlsx")} download>
                    Скачать Excel
                  </a>
                  <a href={api.exportRunUrl(runId, "json")} download>
                    Скачать JSON
                  </a>
                </div>
              </>
            )}
          </>
        )}
      </div>

      {run.data?.metrics && !isActive && (
        <div className="card">
          <h3>Графики</h3>
          <MetricsCharts metrics={run.data.metrics} />
        </div>
      )}

      {displayVacancies.length > 0 && (
        <div className="card">
          <VacancyTable
            items={displayVacancies}
            title={`Вакансии (${vacancyTotal})`}
            sortBy={sortBy}
            sortOrder={sortOrder}
            onSortChange={
              runFinished && vacancies.data?.items?.length
                ? (by, order) => {
                    setSortBy(by);
                    setSortOrder(order);
                  }
                : undefined
            }
          />
        </div>
      )}

      {runFinished && !displayVacancies.length && !vacancies.isLoading && (
        <div className="card">
          <p>Вакансий не найдено или ещё не сохранены в БД.</p>
        </div>
      )}
    </>
  );
}
