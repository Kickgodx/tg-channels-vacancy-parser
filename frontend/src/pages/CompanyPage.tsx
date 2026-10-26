import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import VacancyModal from "../components/VacancyModal";
import type { NamedCount, Vacancy } from "../types/api";

function k(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${Math.round(v / 1000).toLocaleString("ru-RU")}k`;
}

function day(v: string | null): string {
  return v ? new Date(v).toLocaleDateString("ru-RU") : "—";
}

function Counts({ title, items }: { title: string; items: NamedCount[] }) {
  if (items.length === 0) return null;
  return (
    <div className="company-counts">
      <h4>{title}</h4>
      <ul>
        {items.map((c) => (
          <li key={c.name}>
            {c.name} <span className="muted">{c.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Профиль компании: все её вакансии в базе (с архивом), роли, зарплаты, каналы. */
export default function CompanyPage() {
  const [params] = useSearchParams();
  const name = params.get("name") ?? "";
  const q = useQuery({
    queryKey: ["company-profile", name],
    queryFn: () => api.getCompanyProfile(name),
    enabled: name.length > 0,
  });
  const [opened, setOpened] = useState<Vacancy | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);

  const open = (id: string) => {
    setOpenError(null);
    api
      .getVacancy(id)
      .then(setOpened)
      .catch((e: Error) => setOpenError(e.message));
  };

  if (!name) return <p className="muted">Не указана компания.</p>;
  if (q.isLoading) return <p>Загрузка…</p>;
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  const p = q.data;
  if (!p) return null;
  const repostsPerVacancy =
    p.telegram_post_days > 0 ? (p.telegram_post_days / Math.max(1, p.vacancies)).toFixed(1) : null;
  return (
    <>
      <div className="card">
        <p className="caption">
          <Link to="/vacancies">← Вакансии</Link>
        </p>
        <h2>{p.name}</h2>
        {p.variants.length > 1 && (
          <p className="caption">Написания в базе: {p.variants.join(" · ")}</p>
        )}
        <div className="stat-tiles">
          <div className="stat-tile">
            <span className="stat-value">{p.vacancies}</span>
            <span className="stat-label">вакансий в базе (активных {p.active})</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">
              {k(p.salary_p25)} – {k(p.salary_p75)}
            </span>
            <span className="stat-label">
              зарплата p25–p75, медиана {k(p.salary_median)} ({p.with_salary} с зарплатой)
            </span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">
              {day(p.first_post)} – {day(p.last_post)}
            </span>
            <span className="stat-label">первая и последняя публикация</span>
          </div>
          {repostsPerVacancy && (
            <div className="stat-tile">
              <span className="stat-value">{repostsPerVacancy}</span>
              <span className="stat-label">
                дней публикации на вакансию в Telegram ({p.telegram_posts} постов)
              </span>
            </div>
          )}
        </div>
        <div className="company-counts-grid">
          <Counts title="Роли" items={p.roles} />
          <Counts title="Технологии" items={p.technologies} />
          <Counts title="Грейды" items={p.grades} />
          <Counts title="Каналы" items={p.channels} />
        </div>
      </div>

      <div className="card">
        <h3>Последние вакансии</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Дата</th>
                <th>Роль</th>
                <th>Грейд</th>
                <th>Зарплата, ₽</th>
                <th>Канал</th>
              </tr>
            </thead>
            <tbody>
              {p.recent.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => open(r.id)}>
                  <td>
                    {day(r.post_date)}
                    {r.archived && <span className="muted"> · архив</span>}
                  </td>
                  <td>{r.specializations.join(", ") || "—"}</td>
                  <td>{r.grade}</td>
                  <td>{k(r.salary_rub)}</td>
                  <td>@{r.source_channel}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {openError && <p className="error">{openError}</p>}
      </div>
      {opened && <VacancyModal vacancy={opened} onClose={() => setOpened(null)} />}
    </>
  );
}
