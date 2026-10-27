import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, retryIfRetryable } from "../api/client";
import type { DigestCount, DigestFacts, DigestTechnology } from "../types/api";
import { formatThousands as k } from "../utils/formatSalary";

const GRADE_LABEL: Record<string, string> = {
  all: "все грейды",
  junior: "junior",
  middle: "middle",
  senior: "senior",
  lead: "lead",
};

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function day(v: string): string {
  return new Date(`${v}T00:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

function Delta({ now, before }: { now: number; before: number }) {
  const d = now - before;
  if (d === 0) return <span className="muted">±0</span>;
  return <span className={d > 0 ? "delta-up" : "delta-down"}>{d > 0 ? `▲ +${d}` : `▼ ${d}`}</span>;
}

function CountTable({ title, items, hint }: { title: string; items: DigestCount[]; hint?: string }) {
  if (items.length === 0) return null;
  return (
    <div className="digest-block">
      <h4>{title}</h4>
      {hint && <p className="caption">{hint}</p>}
      <table>
        <tbody>
          {items.map((c) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td>
                <strong>{c.count}</strong>
              </td>
              <td>
                <Delta now={c.count} before={c.previous} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TechTable({ title, items }: { title: string; items: DigestTechnology[] }) {
  if (items.length === 0) return null;
  return (
    <div className="digest-block">
      <h4>{title}</h4>
      <table>
        <tbody>
          {items.map((t) => (
            <tr key={t.name}>
              <td>{t.name}</td>
              <td>
                {pct(t.previous_share)} → <strong>{pct(t.share)}</strong>
              </td>
              <td className={t.delta_pp > 0 ? "delta-up" : "delta-down"}>
                {t.delta_pp > 0 ? "▲ +" : "▼ "}
                {t.delta_pp.toFixed(1)} п.п.
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Facts({ f }: { f: DigestFacts }) {
  const salaries = f.salaries.filter((s) => s.median != null || s.previous_median != null);
  return (
    <>
      <div className="stat-tiles">
        <div className="stat-tile">
          <span className="stat-value">{f.vacancies}</span>
          <span className="stat-label">
            новых вакансий; прошлая неделя — {f.previous_vacancies}, в среднем за 4 недели —{" "}
            {Math.round(f.avg_4w_vacancies)}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-value">{pct(f.with_salary_share)}</span>
          <span className="stat-label">с указанной зарплатой</span>
        </div>
      </div>
      <div className="digest-grid">
        <CountTable title="Роли" items={f.roles} hint="Число вакансий и изменение к прошлой неделе" />
        <CountTable title="Формат работы" items={f.work_formats} />
        {salaries.length > 0 && (
          <div className="digest-block">
            <h4>Медиана зарплаты</h4>
            <p className="caption">
              За {f.salary_window_days} дней до конца недели против {f.salary_window_days} дней перед
              ними, ₽ в месяц
            </p>
            <table>
              <tbody>
                {salaries.map((s) => (
                  <tr key={s.grade}>
                    <td>{GRADE_LABEL[s.grade] ?? s.grade}</td>
                    <td>
                      {k(s.previous_median)} → <strong>{k(s.median)}</strong>
                    </td>
                    <td className="muted">{s.count} вак.</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <TechTable title="Технологии: растут" items={f.technologies_up} />
        <TechTable title="Технологии: падают" items={f.technologies_down} />
        <CountTable title="Больше всего вакансий" items={f.top_companies} />
        <CountTable
          title="Новые компании"
          items={f.new_companies}
          hint="Первая вакансия компании в базе — на этой неделе"
        />
        <CountTable title="Источники" items={f.sources} />
      </div>
    </>
  );
}

/** «Обзор рынка»: еженедельная сводка по базе — факты недели и короткий текст LLM по ним. */
export default function DigestPage() {
  const queryClient = useQueryClient();
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: retryIfRetryable });
  const listQ = useQuery({ queryKey: ["digests"], queryFn: () => api.listDigests() });
  const items = listQ.data?.items ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const current = picked ?? items[0]?.period_start ?? null;
  const digestQ = useQuery({
    queryKey: ["digest", current],
    queryFn: () => api.getDigest(current as string),
    enabled: current != null,
  });
  const [weekStart, setWeekStart] = useState("");
  const generateM = useMutation({
    mutationFn: ({ week, force }: { week: string | null; force: boolean }) => api.generateDigest(week, force),
    onSuccess: (d) => {
      queryClient.setQueryData(["digest", d.period_start], d);
      void queryClient.invalidateQueries({ queryKey: ["digests"] });
      setPicked(d.period_start);
    },
  });
  const d = digestQ.data;

  return (
    <>
      <div className="card">
        <h2>Обзор рынка</h2>
        <p className="caption">
          Раз в неделю по вакансиям базы (каналы Telegram и HH, с архивом): что изменилось за неделю
          понедельник–воскресенье. Цифры посчитаны по базе; текст пишет LLM только по этим цифрам.
        </p>
        {listQ.isLoading && <p>Загрузка…</p>}
        {listQ.error && <p className="error">{(listQ.error as Error).message}</p>}
        {listQ.data && items.length === 0 && (
          <p className="muted">
            Обзоров пока нет: первый появится после недели, в которой были вакансии.
          </p>
        )}
        {items.length > 0 && (
          <div className="digest-weeks">
            {items.map((it) => (
              <button
                key={it.period_start}
                type="button"
                className={it.period_start === current ? "" : "secondary"}
                onClick={() => setPicked(it.period_start)}
              >
                {day(it.period_start)} – {day(it.period_end)}
                <span className="muted"> · {it.vacancies}</span>
              </button>
            ))}
          </div>
        )}
        {meQ.data?.is_admin && (
          <div className="digest-admin">
            <label>
              Неделя с понедельника{" "}
              <input type="date" value={weekStart} onChange={(e) => setWeekStart(e.target.value)} />
            </label>
            <button
              type="button"
              disabled={generateM.isPending}
              title="Посчитать факты и запросить текст LLM; пустая дата — прошедшая неделя"
              onClick={() => generateM.mutate({ week: weekStart || null, force: true })}
            >
              {generateM.isPending ? "Формируем…" : "Сформировать"}
            </button>
            {generateM.isError && <span className="error">{(generateM.error as Error).message}</span>}
          </div>
        )}
      </div>
      {digestQ.isLoading && current && <p>Загрузка…</p>}
      {d && (
        <div className="card">
          <h3>
            {day(d.period_start)} – {day(d.period_end)}
          </h3>
          {d.summary ? (
            <div className="digest-summary">{d.summary}</div>
          ) : (
            d.summary_error && <p className="muted">{d.summary_error}</p>
          )}
          {d.summary && (
            <p className="caption">
              {d.model}
              {d.stale && <span className="warning"> · получено другой моделью или версией промпта</span>}
            </p>
          )}
          <Facts f={d.facts} />
        </div>
      )}
    </>
  );
}
