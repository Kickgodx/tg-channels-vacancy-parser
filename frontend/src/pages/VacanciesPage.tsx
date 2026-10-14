import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type VacancyQueryParams } from "../api/client";
import AnalyticsPanel from "../components/AnalyticsPanel";
import MetricsCharts from "../components/MetricsCharts";
import ResumePanel from "../components/ResumePanel";
import VacancyTable from "../components/VacancyTable";
import type { SavedFilter, SavedFilterParams } from "../types/api";
import { SORTABLE_COLUMNS, type VacancySortField, type VacancySortOrder } from "../types/sort";

/** Совпадает с MAX_LIST_DEPTH в backend/api/params.py. */
const MAX_LIST_DEPTH = 10_000;

type VacanciesTab = "list" | "analytics";

function vacancyFilterParams(
  q: string,
  sourceChannel: string,
  grade: string,
  specializations: string[],
  workFormat: string,
  dateFrom: string,
  dateTo: string,
  hasSalaryOnly: boolean,
): VacancyQueryParams {
  const params: VacancyQueryParams = {};
  if (q) params.q = q;
  if (sourceChannel) params.source_channel = sourceChannel.replace(/^@/, "");
  if (grade) params.grade = grade;
  if (specializations.length > 0) params.specialization = specializations;
  if (workFormat) params.work_format = workFormat;
  if (dateFrom) params.date_from = dateFrom;
  if (dateTo) params.date_to = dateTo;
  if (hasSalaryOnly) params.has_salary = true;
  return params;
}

export default function VacanciesPage() {
  const queryClient = useQueryClient();
  const defaultsQ = useQuery({ queryKey: ["config-defaults"], queryFn: () => api.getConfigDefaults() });

  const fxRatesQ = useQuery({
    queryKey: ["fx-rates"],
    queryFn: () => api.getFxRates(),
    staleTime: 60_000,
  });

  // Обновление курсов и пересчёт ₽ — admin-only на backend.
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false });
  const isAdmin = Boolean(meQ.data?.is_admin);

  const fxRefreshM = useMutation({
    mutationFn: () => api.refreshFxRates(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["fx-rates"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancy-metrics"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
    },
  });

  const fxRecalcRubM = useMutation({
    mutationFn: () => api.recalculateSalaryRub(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["vacancy-metrics"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
    },
  });

  const [searchParams, setSearchParams] = useSearchParams();

  const rawPage = Number(searchParams.get("page"));
  const page = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1;
  const q = searchParams.get("q") ?? "";
  const sourceChannel = searchParams.get("source_channel") ?? "";
  const grade = searchParams.get("grade") ?? "";
  const specializations = useMemo(() => searchParams.getAll("specialization"), [searchParams]);
  const workFormat = searchParams.get("work_format") ?? "";
  const dateFrom = searchParams.get("date_from") ?? "";
  const dateTo = searchParams.get("date_to") ?? "";
  const hasSalaryOnly = searchParams.get("has_salary") === "true";
  const rawSortBy = searchParams.get("sort_by");
  const sortBy: VacancySortField = SORTABLE_COLUMNS.includes(rawSortBy as VacancySortField)
    ? (rawSortBy as VacancySortField)
    : "post_date";
  const sortOrder: VacancySortOrder = searchParams.get("sort_order") === "asc" ? "asc" : "desc";
  const tab: VacanciesTab = searchParams.get("tab") === "analytics" ? "analytics" : "list";
  const rawResumeId = Number(searchParams.get("resume_id"));
  const resumeId = Number.isInteger(rawResumeId) && rawResumeId > 0 ? rawResumeId : null;

  const [selectedPreset, setSelectedPreset] = useState("");
  const [presetName, setPresetName] = useState("");
  const [qInput, setQInput] = useState(q);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportNote, setExportNote] = useState<string | null>(null);

  const updateParams = (patch: Record<string, string | number | string[] | null>) => {
    const next = new URLSearchParams(searchParams);
    if (!("page" in patch)) next.delete("page");
    Object.entries(patch).forEach(([k, v]) => {
      next.delete(k);
      if (v == null) return;
      if (Array.isArray(v)) {
        v.forEach((item) => {
          if (item !== "") next.append(k, item);
        });
      } else if (v !== "") {
        next.set(k, String(v));
      }
    });
    setSearchParams(next, { replace: true });
  };

  useEffect(() => {
    setQInput(q);
  }, [q]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      if (qInput === q) return;
      updateParams({ q: qInput || null, page: 1 });
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- debounce q only
  }, [qInput, q]);

  const filterParams = useMemo(
    () =>
      vacancyFilterParams(
        q,
        sourceChannel,
        grade,
        specializations,
        workFormat,
        dateFrom,
        dateTo,
        hasSalaryOnly,
      ),
    [q, sourceChannel, grade, specializations, workFormat, dateFrom, dateTo, hasSalaryOnly],
  );

  const metricsQ = useQuery({
    queryKey: ["vacancy-metrics", filterParams],
    queryFn: () => api.getVacancyMetrics(filterParams),
    enabled: tab === "analytics",
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["vacancies", page, filterParams, sortBy, sortOrder, resumeId],
    queryFn: () =>
      api.listVacancies({
        page,
        page_size: 50,
        sort_by: sortBy,
        sort_order: sortOrder,
        ...(resumeId != null ? { resume_id: resumeId } : {}),
        ...filterParams,
      }),
    placeholderData: keepPreviousData,
  });

  const presetsQ = useQuery({
    queryKey: ["saved-filters"],
    queryFn: () => api.listFilters(),
    staleTime: 60_000,
  });

  const savePresetM = useMutation({
    mutationFn: (body: { name: string; params: SavedFilterParams }) =>
      api.createFilter(body.name, body.params),
    onSuccess: () => {
      setPresetName("");
      void queryClient.invalidateQueries({ queryKey: ["saved-filters"] });
    },
  });

  const deletePresetM = useMutation({
    mutationFn: (id: number) => api.deleteFilter(id),
    onSuccess: () => {
      setSelectedPreset("");
      void queryClient.invalidateQueries({ queryKey: ["saved-filters"] });
    },
  });

  const buildPresetParams = (): SavedFilterParams => ({
    q: q || null,
    source_channel: sourceChannel ? sourceChannel.replace(/^@/, "") : null,
    grade: grade || null,
    specializations,
    work_format: workFormat || null,
    date_from: dateFrom || null,
    date_to: dateTo || null,
    has_salary: hasSalaryOnly || null,
    sort_by: sortBy,
    sort_order: sortOrder,
  });

  const applyPreset = (f: SavedFilter) => {
    const p = f.params;
    updateParams({
      q: p.q ?? null,
      source_channel: p.source_channel ?? null,
      grade: p.grade ?? null,
      specialization: p.specializations ?? [],
      work_format: p.work_format ?? null,
      date_from: p.date_from ?? null,
      date_to: p.date_to ?? null,
      has_salary: p.has_salary ? "true" : null,
      sort_by: SORTABLE_COLUMNS.includes(p.sort_by as VacancySortField) ? p.sort_by ?? null : null,
      sort_order: p.sort_order === "asc" || p.sort_order === "desc" ? p.sort_order : null,
    });
  };

  const presets = presetsQ.data?.items ?? [];
  // Backend листает только первые MAX_LIST_DEPTH позиций (OFFSET дальше дорог) — дальше 422.
  const pagesByTotal = data ? Math.ceil(data.total / data.page_size) : 1;
  const maxPages = data ? Math.max(1, Math.floor(MAX_LIST_DEPTH / data.page_size)) : 1;
  const totalPages = Math.min(pagesByTotal, maxPages);
  const gradeOptions = defaultsQ.data?.grade_options ?? [];
  const specOptions = defaultsQ.data?.specialization_options ?? [];

  return (
    <div className="card">
      <h2>Все уникальные вакансии</h2>
      <p className="caption">Данные из единой базы PostgreSQL (после успешных прогонов).</p>

      <div className="filters-panel saved-filters-bar">
        <select
          value={selectedPreset}
          onChange={(e) => setSelectedPreset(e.target.value)}
          title="Сохранённые наборы фильтров"
        >
          <option value="">Пресет фильтров…</option>
          {presets.map((f) => (
            <option key={f.id} value={String(f.id)}>
              {f.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn-secondary"
          disabled={!selectedPreset}
          onClick={() => {
            const f = presets.find((x) => String(x.id) === selectedPreset);
            if (f) applyPreset(f);
          }}
        >
          Применить
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!selectedPreset || deletePresetM.isPending}
          onClick={() => deletePresetM.mutate(Number(selectedPreset))}
        >
          Удалить
        </button>
        <input
          placeholder="Имя нового пресета"
          value={presetName}
          onChange={(e) => setPresetName(e.target.value)}
        />
        <button
          type="button"
          className="btn-secondary"
          disabled={!presetName.trim() || savePresetM.isPending}
          onClick={() =>
            savePresetM.mutate({ name: presetName.trim(), params: buildPresetParams() })
          }
        >
          {savePresetM.isPending ? "Сохранение…" : "Сохранить текущие"}
        </button>
        {savePresetM.isError && (
          <span className="error">{(savePresetM.error as Error).message}</span>
        )}
        {deletePresetM.isError && (
          <span className="error">{(deletePresetM.error as Error).message}</span>
        )}
      </div>

      <ResumePanel
        selectedId={resumeId}
        onSelect={(id) =>
          updateParams({
            resume_id: id,
            sort_by: id != null ? "match_score" : null,
          })
        }
      />

      <div className="filters-panel">
        <input
          type="search"
          placeholder="Поиск по тексту…"
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
        />
        <input
          placeholder="Канал (@name)"
          value={sourceChannel}
          onChange={(e) => updateParams({ source_channel: e.target.value })}
        />
        <select value={grade} onChange={(e) => updateParams({ grade: e.target.value })}>
          <option value="">Любой грейд</option>
          {gradeOptions.filter((g) => g !== "unknown").map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
          <option value="unknown">unknown</option>
        </select>
        <select value={workFormat} onChange={(e) => updateParams({ work_format: e.target.value })}>
          <option value="">Любой формат</option>
          {(defaultsQ.data?.work_format_options ?? []).map((wf) => (
            <option key={wf} value={wf}>
              {wf}
            </option>
          ))}
        </select>
        <label>
          Дата от
          <input type="date" value={dateFrom} onChange={(e) => updateParams({ date_from: e.target.value })} />
        </label>
        <label>
          Дата до
          <input type="date" value={dateTo} onChange={(e) => updateParams({ date_to: e.target.value })} />
        </label>
        <label className="checkbox-row filters-salary-only">
          <input
            type="checkbox"
            checked={hasSalaryOnly}
            onChange={(e) => updateParams({ has_salary: e.target.checked ? "true" : null })}
          />
          Только с указанной зарплатой
        </label>
      </div>

      <p className="field-label">Специализации (пусто — любые)</p>
      <div className="chip-grid filters-spec-chips">
        {specOptions.map((s) => (
          <label key={s} className="chip">
            <input
              type="checkbox"
              checked={specializations.includes(s)}
              onChange={() =>
                updateParams({
                  specialization: specializations.includes(s)
                    ? specializations.filter((x) => x !== s)
                    : [...specializations, s],
                })
              }
            />
            {s}
          </label>
        ))}
      </div>

      <div className="export-links vacancies-export">
        <button
          type="button"
          className="btn-link"
          onClick={() => {
            setExportError(null);
            setExportNote(null);
            void api
              .downloadExport(api.exportVacanciesUrl(filterParams), "vacancies.xlsx")
              .then(setExportNote)
              .catch((e: Error) => setExportError(e.message));
          }}
        >
          Скачать Excel (все по фильтрам)
        </button>
        {exportError && <p className="error">{exportError}</p>}
        {exportNote && <p className="warning">{exportNote}</p>}
        {isAdmin && (
          <>
            <button
              type="button"
              className="btn-secondary"
              disabled={fxRefreshM.isPending || fxRecalcRubM.isPending}
              onClick={() => fxRefreshM.mutate()}
              title="Загрузить курсы ЦБ и пересчитать зарплаты в рублях для всех вакансий"
            >
              {fxRefreshM.isPending ? "Обновление курсов…" : "Обновить курсы ЦБ"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={fxRefreshM.isPending || fxRecalcRubM.isPending}
              onClick={() => fxRecalcRubM.mutate()}
              title="Пересчитать колонку «Зарплата, ₽» по последнему курсу из БД (без запроса к ЦБ)"
            >
              {fxRecalcRubM.isPending ? "Пересчёт ₽…" : "Пересчитать зарплаты в ₽"}
            </button>
          </>
        )}
        {fxRatesQ.data?.rate_date && fxRatesQ.data.rates.length > 0 && (
          <span className="caption">
            Курсы в БД на {fxRatesQ.data.rate_date}:{" "}
            {fxRatesQ.data.rates
              .filter((r) => r.currency !== "RUB")
              .slice(0, 6)
              .map((r) => `${r.currency}=${r.rate_to_rub.toFixed(2)}`)
              .join(", ")}
            {fxRatesQ.data.rates.length > 7 ? "…" : ""}
          </span>
        )}
        {fxRefreshM.isSuccess && (
          <span className="caption">
            Курсы ЦБ: обновлено {fxRefreshM.data.vacancies_updated_rub} / {fxRefreshM.data.vacancies_processed}
            {fxRefreshM.data.vacancies_skipped_no_rate > 0
              ? ` (без курса: ${fxRefreshM.data.vacancies_skipped_no_rate})`
              : ""}
          </span>
        )}
        {fxRecalcRubM.isSuccess && (
          <span className="caption">
            Пересчёт ₽ (курс на {fxRecalcRubM.data.rate_date ?? "—"}):{" "}
            {fxRecalcRubM.data.vacancies_updated_rub} / {fxRecalcRubM.data.vacancies_processed}
            {fxRecalcRubM.data.vacancies_skipped_no_rate > 0
              ? ` (без курса: ${fxRecalcRubM.data.vacancies_skipped_no_rate})`
              : ""}
          </span>
        )}
        {(fxRefreshM.isError || fxRecalcRubM.isError) && (
          <span className="caption" style={{ color: "var(--color-danger, #c92a2a)" }}>
            {(fxRecalcRubM.error ?? fxRefreshM.error)?.message}
          </span>
        )}
      </div>

      <div className="page-tabs" role="tablist" aria-label="Раздел вакансий">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "list"}
          className={`page-tab${tab === "list" ? " active" : ""}`}
          onClick={() => updateParams({ tab: null, page: page > 1 ? page : null })}
        >
          Список
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "analytics"}
          className={`page-tab${tab === "analytics" ? " active" : ""}`}
          onClick={() => updateParams({ tab: "analytics", page: page > 1 ? page : null })}
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
              {pagesByTotal > maxPages && (
                <p className="caption">
                  Листаются первые {MAX_LIST_DEPTH.toLocaleString()} вакансий — уточните фильтры или
                  смените сортировку, чтобы увидеть остальные.
                </p>
              )}
              {data.items.length > 0 ? (
                <VacancyTable
                  items={data.items}
                  resumeId={resumeId}
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  onSortChange={(by, order) => updateParams({ sort_by: by, sort_order: order })}
                />
              ) : (
                <p>Нет вакансий по выбранным фильтрам.</p>
              )}
              <div className="pager">
                <button
                  disabled={page <= 1}
                  onClick={() => updateParams({ page: page - 1 > 1 ? page - 1 : null })}
                >
                  Назад
                </button>
                <button
                  disabled={page >= totalPages}
                  onClick={() => updateParams({ page: page + 1 })}
                >
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
              <AnalyticsPanel params={filterParams} />
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
