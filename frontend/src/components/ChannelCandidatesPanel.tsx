import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { ChannelCandidate } from "../types/api";

interface Props {
  /** Каналы, уже вписанные в форму (lower, без @): у них вместо «Добавить» — пометка. */
  inForm: string[];
  onAdd: (username: string) => void;
}

function day(v: string | null): string {
  return v ? new Date(v).toLocaleDateString("ru-RU") : "—";
}

function share(c: ChannelCandidate): string {
  return c.forwards > 0 ? `${Math.round((c.vacancy_forwards / c.forwards) * 100)}%` : "—";
}

/** Кандидаты в источники: каналы, из которых пересылают посты в читаемые каналы. */
export default function ChannelCandidatesPanel({ inForm, onAdd }: Props) {
  const qc = useQueryClient();
  const [showIgnored, setShowIgnored] = useState(false);
  const q = useQuery({
    queryKey: ["channel-candidates", showIgnored],
    queryFn: () => api.listChannelCandidates(showIgnored),
  });
  const statusM = useMutation({
    mutationFn: ({ id, status }: { id: number; status: ChannelCandidate["status"] }) =>
      api.setChannelCandidateStatus(id, status),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["channel-candidates"] }),
  });
  const items = q.data?.items ?? [];

  return (
    <details className="channel-candidates">
      <summary>
        Кандидаты из пересылок{q.data ? ` (${items.length})` : ""}
      </summary>
      <p className="caption">
        Каналы, из которых пересылают посты в читаемые каналы. Их ещё нет ни в config.yaml, ни среди
        прочитанных. «Вакансии» — сколько пересланных постов прошли как вакансии.
      </p>
      <label className="checkbox-row">
        <input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} />
        Показать скрытые
      </label>
      {q.isLoading && <p>Загрузка…</p>}
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {q.data && items.length === 0 && (
        <p className="muted">Пока пусто: кандидаты появятся после прогонов с пересланными постами.</p>
      )}
      {items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Канал</th>
              <th title="Разных пересланных постов в читаемых каналах">Пересылок</th>
              <th title="Из них прошли как вакансии">Вакансии</th>
              <th>Куда пересылают</th>
              <th>Последняя</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.channel_id} className={c.status === "ignored" ? "muted" : ""}>
                <td>
                  <a href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer">
                    @{c.username}
                  </a>
                  {c.title && <div className="caption">{c.title}</div>}
                </td>
                <td>{c.forwards}</td>
                <td>
                  {c.vacancy_forwards} <span className="muted">({share(c)})</span>
                </td>
                <td className="caption">{c.seen_in.map((s) => `@${s}`).join(", ")}</td>
                <td>
                  {c.example_url ? (
                    <a href={c.example_url} target="_blank" rel="noreferrer">
                      {day(c.last_post_date)}
                    </a>
                  ) : (
                    day(c.last_post_date)
                  )}
                </td>
                <td className="channel-candidate-actions">
                  {inForm.includes(c.username.toLowerCase()) ? (
                    <span className="muted">в форме</span>
                  ) : (
                    <button type="button" className="btn-link" onClick={() => onAdd(c.username)}>
                      Добавить
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-link secondary"
                    disabled={statusM.isPending}
                    onClick={() =>
                      statusM.mutate({ id: c.channel_id, status: c.status === "ignored" ? "new" : "ignored" })
                    }
                  >
                    {c.status === "ignored" ? "Вернуть" : "Скрыть"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {statusM.isError && <p className="error">{(statusM.error as Error).message}</p>}
    </details>
  );
}
