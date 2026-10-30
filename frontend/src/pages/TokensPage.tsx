import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { ApiTokenCreated, ApiTokenScope } from "../types/api";

function date(v: string | null): string {
  return v ? new Date(v).toLocaleString("ru-RU") : "—";
}

/** Персональные токены доступа: для MCP-сервера (Claude Desktop / Claude Code) и скриптов. */
export default function TokensPage() {
  const queryClient = useQueryClient();
  const tokensQ = useQuery({ queryKey: ["api-tokens"], queryFn: () => api.listApiTokens() });
  const [name, setName] = useState("Claude");
  const [llm, setLlm] = useState(false);
  const [days, setDays] = useState(90);
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);
  // Момент открытия страницы — для пометки «истёк»; без пересчёта на каждый рендер.
  const [now] = useState(() => Date.now());

  const createM = useMutation({
    mutationFn: () => {
      const scopes: ApiTokenScope[] = llm ? ["read", "llm"] : ["read"];
      return api.createApiToken(name.trim(), scopes, days);
    },
    onSuccess: (t) => {
      setCreated(t);
      setCopied(false);
      void queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });
  const revokeM = useMutation({
    mutationFn: (id: number) => api.revokeApiToken(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["api-tokens"] }),
  });

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const tokens = tokensQ.data ?? [];

  return (
    <div>
      <div className="card">
        <h2>Токены доступа</h2>
        <p className="caption">
          Токен даёт MCP-серверу (Claude Desktop, Claude Code) и скриптам доступ к вашим данным:
          вакансии, аналитика, резюме. Админские функции (парсинг, импорт, HH) токен не открывает.
          Значение показывается один раз — сохраните его сразу.
        </p>
        <label className="field">
          Название
          <input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          Срок, дней
          <input
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(e) => setDays(Math.min(365, Math.max(1, Number(e.target.value) || 1)))}
          />
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={llm} onChange={(e) => setLlm(e.target.checked)} /> Разрешить
          LLM-задачи (оценка резюме, письма, «Спросить базу») — тратят вашу суточную квоту
        </label>
        <div className="modal-actions">
          <button
            type="button"
            className="primary"
            disabled={!name.trim() || createM.isPending}
            onClick={() => createM.mutate()}
          >
            Создать токен
          </button>
        </div>
        {createM.isError && <p className="error">{(createM.error as Error).message}</p>}
        {created && (
          <aside className="market-salary">
            <p>
              Токен <strong>{created.name}</strong> — скопируйте сейчас, потом его не увидеть:
            </p>
            <pre className="vacancy-full-text">{created.token}</pre>
            <div className="modal-actions">
              <button type="button" className="secondary" onClick={() => void copy(created.token)}>
                {copied ? "Скопировано" : "Копировать"}
              </button>
            </div>
            <p className="caption">Подключение к Claude Code (из корня проекта):</p>
            <pre className="vacancy-full-text">
              {`claude mcp add tg-jobs -e TGJOBS_TOKEN=${created.token} -- .venv/Scripts/python.exe -m tg_jobs_mcp`}
            </pre>
          </aside>
        )}
      </div>

      <div className="card">
        <h3>Действующие токены</h3>
        {tokensQ.isLoading && <p>Загрузка…</p>}
        {tokensQ.isError && <p className="error">{(tokensQ.error as Error).message}</p>}
        {!tokensQ.isLoading && tokens.length === 0 && <p className="muted">Токенов нет.</p>}
        {tokens.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Название</th>
                <th>Начало</th>
                <th>Права</th>
                <th>Последний вход</th>
                <th>Действует до</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => {
                const expired = new Date(t.expires_at).getTime() <= now;
                return (
                  <tr key={t.id}>
                    <td>
                      <strong>{t.name}</strong>
                    </td>
                    <td>
                      <code>{t.prefix}…</code>
                    </td>
                    <td>{t.scopes.includes("llm") ? "чтение + LLM" : "чтение"}</td>
                    <td>{date(t.last_used_at)}</td>
                    <td className={expired ? "error" : undefined}>
                      {date(t.expires_at)}
                      {expired && " (истёк)"}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="secondary danger"
                        disabled={revokeM.isPending}
                        onClick={() => {
                          if (window.confirm(`Отозвать токен «${t.name}»? Он перестанет работать сразу.`)) {
                            revokeM.mutate(t.id);
                          }
                        }}
                      >
                        Отозвать
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {revokeM.isError && <p className="error">{(revokeM.error as Error).message}</p>}
      </div>
    </div>
  );
}
