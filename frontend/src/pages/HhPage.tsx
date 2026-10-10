import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { api } from "../api/client";
import type { HhSyncResult } from "../types/api";

export default function HhPage() {
  const queryClient = useQueryClient();
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me() });
  const statusQ = useQuery({
    queryKey: ["hh-status"],
    queryFn: () => api.hhStatus(),
    enabled: Boolean(meQ.data?.is_admin),
  });

  const syncM = useMutation({
    mutationFn: () => api.hhSync(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hh-status"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
    },
  });

  if (meQ.isLoading) {
    return <p className="muted">Загрузка…</p>;
  }
  if (meQ.data && !meQ.data.is_admin) {
    return <Navigate to="/" replace />;
  }

  const status = statusQ.data;
  const result: HhSyncResult | undefined = syncM.data;
  const canRun =
    Boolean(status?.enabled && status.token_configured && status.email_configured) &&
    !syncM.isPending;

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

      <div className="row" style={{ gap: "0.75rem", marginTop: "1rem" }}>
        <button
          type="button"
          className="primary"
          disabled={!canRun}
          onClick={() => syncM.mutate()}
        >
          {syncM.isPending ? "Синхронизация…" : "Запустить sync"}
        </button>
        {syncM.isPending && <span className="muted">Идёт запрос к HH API, подождите…</span>}
      </div>

      {syncM.isError && (
        <p className="error" style={{ marginTop: "1rem" }}>
          {(syncM.error as Error).message}
        </p>
      )}

      {result && (
        <div style={{ marginTop: "1.25rem" }}>
          <h3>Итог</h3>
          <ul className="plain-list">
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
