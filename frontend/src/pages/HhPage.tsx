import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { HhSyncJob, HhSyncProgress, HhSyncWindow } from "../types/api";

const WINDOW_OPTIONS: { value: HhSyncWindow; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "7d", label: "7 дней" },
  { value: "14d", label: "14 дней" },
  { value: "30d", label: "30 дней" },
  { value: "range", label: "Диапазон" },
];

const STATUS_LABEL: Record<HhSyncJob["status"], string> = {
  queued: "в очереди",
  running: "выполняется",
  completed: "завершён",
  failed: "ошибка",
};

const PHASE_LABEL: Record<HhSyncProgress["phase"], string> = {
  search: "поиск",
  details: "детали вакансий",
  purge: "очистка кэша",
};

function isActive(job: HhSyncJob | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

export default function HhPage() {
  const queryClient = useQueryClient();
  const [syncWindow, setSyncWindow] = useState<HhSyncWindow>("7d");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const statusQ = useQuery({
    queryKey: ["hh-status"],
    queryFn: () => api.hhStatus(),
  });
  // Последний sync: после перезагрузки страницы видно идущий или итог прошлого.
  const lastJobQ = useQuery({
    queryKey: ["hh-sync-jobs"],
    queryFn: () => api.hhSyncJobs(1),
  });

  const [liveJob, setLiveJob] = useState<HhSyncJob | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const job = liveJob ?? lastJobQ.data?.[0] ?? null;
  const activeJobId = isActive(job) ? job!.id : null;

  useEffect(() => {
    if (!activeJobId) return;
    return api.subscribeHhSyncJob(
      activeJobId,
      (update) => {
        setStreamError(null);
        setLiveJob(update);
        if (!isActive(update)) {
          void queryClient.invalidateQueries({ queryKey: ["hh-status"] });
          void queryClient.invalidateQueries({ queryKey: ["hh-sync-jobs"] });
          void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
        }
      },
      (err) => setStreamError(err.message),
    );
  }, [activeJobId, queryClient]);

  const syncM = useMutation({
    mutationFn: async () => {
      const accepted = await api.hhSync(
        syncWindow === "range"
          ? { window: syncWindow, date_from: dateFrom, date_to: dateTo }
          : { window: syncWindow },
      );
      return api.hhSyncJob(accepted.job_id);
    },
    onSuccess: (created) => setLiveJob(created),
  });

  const status = statusQ.data;
  const result = job?.result ?? null;
  const progress = isActive(job) ? (job?.progress ?? null) : null;
  const rangeInvalid =
    syncWindow === "range" && (!dateFrom || !dateTo || dateFrom > dateTo);
  const canRun =
    Boolean(status?.enabled && status.token_configured && status.email_configured) &&
    !syncM.isPending &&
    !isActive(job) &&
    !rangeInvalid;

  return (
    <div className="card">
      <h2>Парсинг HH.ru</h2>
      <p className="caption">
        Поиск AQA/Python через официальный API. Деталки запрашиваются только для новых id
        (кэш listings с TTL). Без LLM — поля маппятся напрямую.
      </p>

      {statusQ.isError && (
        <p className="error">Не удалось загрузить статус: {(statusQ.error as Error).message}</p>
      )}

      {status && (
        <dl className="kv-list">
          <div>
            <dt>Включено</dt>
            <dd>{status.enabled ? "да" : "нет"}</dd>
          </div>
          <div>
            <dt>Токен</dt>
            <dd>{status.token_configured ? "задан" : "нет HH_API_TOKEN"}</dd>
          </div>
          <div>
            <dt>Email (User-Agent)</dt>
            <dd>{status.email_configured ? "задан" : "нет HH_CONTACT_EMAIL"}</dd>
          </div>
          <div>
            <dt>TTL listings</dt>
            <dd>{status.listing_ttl_days} дн.</dd>
          </div>
          <div>
            <dt>Кэш listings</dt>
            <dd>
              {status.listings_total} всего · {status.listings_with_detail} с detail ·{" "}
              {status.listings_without_detail} без
            </dd>
          </div>
          <div>
            <dt>Поиск</dt>
            <dd>
              <code className="wrap">{status.search_text}</code>
              <br />
              roles: {status.professional_role.join(", ")}
            </dd>
          </div>
        </dl>
      )}

      <p className="field-label" style={{ marginTop: "1rem" }}>
        Окно поиска
      </p>
      <div className="row" style={{ gap: "1rem", flexWrap: "wrap" }}>
        {WINDOW_OPTIONS.map((opt) => (
          <label key={opt.value} className="checkbox-row">
            <input
              type="radio"
              name="hh-sync-window"
              value={opt.value}
              checked={syncWindow === opt.value}
              onChange={() => setSyncWindow(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </div>

      {syncWindow === "range" && (
        <div className="row" style={{ gap: "0.75rem", marginTop: "0.75rem" }}>
          <label className="field">
            Дата от
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </label>
          <label className="field">
            Дата до
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>
        </div>
      )}
      {rangeInvalid && (
        <p className="error" style={{ marginTop: "0.5rem" }}>
          Укажите обе даты; «от» не позже «до».
        </p>
      )}

      <div className="row" style={{ gap: "0.75rem", marginTop: "1rem" }}>
        <button
          type="button"
          className="primary"
          disabled={!canRun}
          onClick={() => syncM.mutate()}
        >
          {isActive(job) ? "Синхронизация…" : "Запустить sync"}
        </button>
        {isActive(job) && (
          <span className="muted">Идёт в фоне — страницу можно закрыть, итог сохранится.</span>
        )}
      </div>

      {syncM.isError && (
        <p className="error" style={{ marginTop: "1rem" }}>
          {(syncM.error as Error).message}
        </p>
      )}
      {streamError && isActive(job) && (
        <p className="error" style={{ marginTop: "1rem" }}>
          Статус sync не обновляется: {streamError}
        </p>
      )}

      {job && (
        <div style={{ marginTop: "1.25rem" }}>
          <p>
            Последний sync: <strong>{STATUS_LABEL[job.status]}</strong>
            {job.created_at ? ` · запущен ${new Date(job.created_at).toLocaleString()}` : ""}
            {job.attempts > 1 ? ` · попытка ${job.attempts}` : ""}
          </p>
          {progress && (
            <p className="muted">
              Этап: {PHASE_LABEL[progress.phase] ?? progress.phase}
              {progress.phase === "details" && progress.total > 0
                ? ` — ${progress.done} / ${progress.total}`
                : ""}
              {" · "}найдено {progress.searched}, уникальных {progress.unique}, detail{" "}
              {progress.details_fetched}, в БД +{progress.vacancies_inserted}
            </p>
          )}
          {job.error_message && <p className="error">{job.error_message}</p>}
        </div>
      )}

      {result && !isActive(job) && (
        <div style={{ marginTop: "1.25rem" }}>
          <h3>{job?.status === "failed" ? "Итог до сбоя" : "Итог"}</h3>
          <ul className="plain-list">
            <li>
              Окно: {result.window}
              {result.period != null ? ` (period=${result.period})` : ""}
              {result.date_from || result.date_to
                ? ` · ${result.date_from ?? "—"} … ${result.date_to ?? "—"}`
                : ""}
            </li>
            <li>
              Поиск: {result.searched} вакансий ({result.pages} стр.) за{" "}
              {result.duration_seconds.toFixed(1)} с
            </li>
            <li>
              Уникальных / дублей городов: {result.unique} / {result.duplicates}
            </li>
            <li>
              Detail: запрошено {result.details_fetched}, пропущено {result.details_skipped}
            </li>
            <li>
              Вакансии в БД: +{result.vacancies_inserted} новых, обновлено{" "}
              {result.vacancies_updated} ({result.flush_batches} батч
              {result.flush_batches === 1 ? "" : "ей"})
            </li>
            <li>Listings upsert / purge: {result.listings_upserted} / {result.listings_purged}</li>
          </ul>
          {result.errors.length > 0 && (
            <>
              <h4>Ошибки ({result.errors.length})</h4>
              <ul className="plain-list error">
                {result.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
