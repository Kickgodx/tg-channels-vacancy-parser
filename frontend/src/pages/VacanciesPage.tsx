import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import MetricsCharts from "../components/MetricsCharts";
import VacancyTable from "../components/VacancyTable";
import type { VacancySortField, VacancySortOrder } from "../types/sort";

type VacanciesTab = "list" | "analytics";

function vacancyFilterParams(
  q: string,
  sourceChannel: string,
  grade: string,
  workFormat: string,
  dateFrom: string,
  dateTo: string,
): Record<string, string> {
  return {
    ...(q ? { q } : {}),
    ...(sourceChannel ? { source_channel: sourceChannel.replace(/^@/, "") } : {}),
    ...(grade ? { grade } : {}),
    ...(workFormat ? { work_format: workFormat } : {}),
    ...(dateFrom ? { date_from: dateFrom } : {}),
    ...(dateTo ? { date_to: dateTo } : {}),
  };
}

export default function VacanciesPage() {
  const defaultsQ = useQuery({ queryKey: ["config-defaults"], queryFn: () => api.getConfigDefaults() });

  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [sourceChannel, setSourceChannel] = useState("");
  const [grade, setGrade] = useState("");
  const [workFormat, setWorkFormat] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sortBy, setSortBy] = useState<VacancySortField>("post_date");
  const [sortOrder, setSortOrder] = useState<VacancySortOrder>("desc");
  const [tab, setTab] = useState<VacanciesTab>("list");

  const filterParams = useMemo(
    () => vacancyFilterParams(q, sourceChannel, grade, workFormat, dateFrom, dateTo),
    [q, sourceChannel, grade, workFormat, dateFrom, dateTo],
  );

  const metricsQ = useQuery({
    queryKey: ["vacancy-metrics", filterParams],
    queryFn: () => api.getVacancyMetrics(filterParams),
    enabled: tab === "analytics",
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["vacancies", page, filterParams, sortBy, sortOrder],
    queryFn: () =>
      api.listVacancies({
        page,
        page_size: 50,
        sort_by: sortBy,
        sort_order: sortOrder,
        ...filterParams,
      }),
  });

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 1;
  const gradeOptions = defaultsQ.data?.grade_options ?? [];

  return (
    <div className="card">
      <h2>Все уникальные вакансии</h2>
      <p className="caption">Данные из единой базы PostgreSQL (после успешных прогонов).</p>

      <div className="filters-panel">
        <input
          type="search"
          placeholder="Поиск по тексту…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <input
          placeholder="Канал (@name)"
          value={sourceChannel}
          onChange={(e) => {
            setSourceChannel(e.target.value);
            setPage(1);
          }}
        />
        <select value={grade} onChange={(e) => { setGrade(e.target.value); setPage(1); }}>
          <option value="">Любой грейд</option>
          {gradeOptions.filter((g) => g !== "unknown").map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
          <option value="unknown">unknown</option>
        </select>
        <select value={workFormat} onChange={(e) => { setWorkFormat(e.target.value); setPage(1); }}>
          <option value="">Любой формат</option>
          {(defaultsQ.data?.work_format_options ?? []).map((wf) => (
            <option key={wf} value={wf}>
              {wf}
            </option>
          ))}
        </select>
        <label>
          Дата от
          <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} />
        </label>
        <label>
          Дата до
          <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} />
        </label>
      </div>

      <div className="page-tabs" role="tablist" aria-label="Раздел вакансий">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "list"}
          className={`page-tab${tab === "list" ? " active" : ""}`}
          onClick={() => setTab("list")}
        >
          Список
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "analytics"}
          className={`page-tab${tab === "analytics" ? " active" : ""}`}
          onClick={() => setTab("analytics")}
        >
          Аналитика
        </button>
      </div>

      {tab === "list" && (
        <>
          {isLoading && !data && <p>Загрузка…</p>}
          {error && <p className="error">{(error as Error).message}</p>}
          {data && (
            <>
              <p>
                Всего: <strong>{data.total}</strong> (стр. {data.page} / {totalPages})
              </p>
              {data.items.length > 0 ? (
                <VacancyTable
                  items={data.items}
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  onSortChange={(by, order) => {
                    setSortBy(by);
                    setSortOrder(order);
                    setPage(1);
                  }}
                />
              ) : (
                <p>Нет вакансий по выбранным фильтрам.</p>
              )}
              <div className="pager">
                <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Назад
                </button>
                <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                  Вперёд
                </button>
              </div>
            </>
          )}
        </>
      )}

      {tab === "analytics" && (
        <>
          <p className="caption">
            Агрегация на сервере, без загрузки всех строк. Учитываются те же фильтры, что и в списке.
          </p>
          {metricsQ.isLoading && !metricsQ.data && <p>Загрузка графиков…</p>}
          {metricsQ.error && <p className="error">{(metricsQ.error as Error).message}</p>}
          {metricsQ.data && metricsQ.data.unique_vacancies > 0 && (
            <>
              <p>
                Вакансий в выборке: <strong>{metricsQ.data.unique_vacancies}</strong>
              </p>
              <MetricsCharts metrics={metricsQ.data} />
            </>
          )}
          {metricsQ.data && metricsQ.data.unique_vacancies === 0 && !metricsQ.isLoading && (
            <p className="muted">Нет данных для графиков по текущим фильтрам.</p>
          )}
        </>
      )}
    </div>
  );
}
