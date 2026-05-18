import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import VacancyTable from "../components/VacancyTable";
import type { VacancySortField, VacancySortOrder } from "../types/sort";

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

  const { data, isLoading, error } = useQuery({
    queryKey: ["vacancies", page, q, sourceChannel, grade, workFormat, dateFrom, dateTo, sortBy, sortOrder],
    queryFn: () =>
      api.listVacancies({
        page,
        page_size: 50,
        sort_by: sortBy,
        sort_order: sortOrder,
        ...(q ? { q } : {}),
        ...(sourceChannel ? { source_channel: sourceChannel.replace(/^@/, "") } : {}),
        ...(grade ? { grade } : {}),
        ...(workFormat ? { work_format: workFormat } : {}),
        ...(dateFrom ? { date_from: dateFrom } : {}),
        ...(dateTo ? { date_to: dateTo } : {}),
      }),
  });

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 1;
  const grades = ["", "intern", "junior", "middle", "senior", "lead", "principal", "unknown"];

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
          {grades.map((g) => (
            <option key={g} value={g}>
              {g || "Любой грейд"}
            </option>
          ))}
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

      {isLoading && <p>Загрузка…</p>}
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
    </div>
  );
}
