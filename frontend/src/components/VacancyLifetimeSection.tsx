import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type VacancyQueryParams } from "../api/client";
import type { LifetimeGroup, Vacancy } from "../types/api";
import VacancyModal from "./VacancyModal";

interface Props {
  params: VacancyQueryParams;
}

const BAR = "#2a78d6";

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDays(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${Number.isInteger(v) ? v : v.toFixed(1)} дн.`;
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU");
}

function GroupTable({
  title,
  rows,
  longDays,
}: {
  title: string;
  rows: LifetimeGroup[];
  longDays: number;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="card chart-card">
      <h4>{title}</h4>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th />
              <th>Вакансий</th>
              <th title="Публиковали в 2+ разных дня">Повторно</th>
              <th title={`Между первой и последней публикацией ${longDays}+ дней`}>
                {longDays}+ дн.
              </th>
              <th title="Медиана срока у повторно публикуемых">Медиана срока</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((g) => (
              <tr key={g.name}>
                <td>{g.name}</td>
                <td>{g.vacancies}</td>
                <td>
                  {g.republished} <span className="muted">({fmtPct(g.republished_share)})</span>
                </td>
                <td>{g.long_open}</td>
                <td>{fmtDays(g.median_span_days)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Блок «Жизнь вакансии» во вкладке аналитики: срок по публикациям в Telegram. */
export default function VacancyLifetimeSection({ params }: Props) {
  const q = useQuery({
    queryKey: ["analytics-lifetime", params],
    queryFn: () => api.getLifetimeAnalytics(params),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
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

  const data = q.data;
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  if (!data || data.vacancies === 0) return null;
  const sameDay = data.buckets.find((b) => b.min_days === 0)?.count ?? 0;
  const spread = data.buckets.filter((b) => b.min_days > 0);
  const long = data.long_open_days;

  return (
    <>
      <div className="card chart-card chart-wide">
        <h4>Жизнь вакансии</h4>
        <p className="muted" style={{ margin: "0 0 0.5rem" }}>
          Срок — от первой до последней публикации текста в Telegram (с перепостами и перепостами с
          правками). Это видимость на рынке, а не дата закрытия. HH не входит: синк читает окно по
          дате публикации, и пропажа из выдачи не значит, что вакансию закрыли.
        </p>
        <div className="stat-tiles">
          <div className="stat-tile">
            <span className="stat-value">{data.vacancies}</span>
            <span className="stat-label">вакансий Telegram под фильтрами</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">
              {data.republished} ({fmtPct(data.republished_share)})
            </span>
            <span className="stat-label">публиковали повторно — в 2+ разных дня</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{data.long_open}</span>
            <span className="stat-label">висят {long}+ дней — долго не могут закрыть</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">
              {fmtDays(data.span_p50_days)}
              {data.span_p90_days != null && (
                <span className="muted"> · p90 {fmtDays(data.span_p90_days)}</span>
              )}
            </span>
            <span className="stat-label">медиана срока у повторно публикуемых</span>
          </div>
        </div>
        {spread.some((b) => b.count > 0) && (
          <>
            <h5 className="momentum-title">
              Срок повторно публикуемых (ещё {sameDay} — только в один день)
            </h5>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={spread}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip cursor={{ fill: "#f1f3f5" }} />
                <Bar
                  dataKey="count"
                  name="Вакансий"
                  fill={BAR}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={48}
                />
              </BarChart>
            </ResponsiveContainer>
          </>
        )}
      </div>

      <GroupTable
        title={`Жизнь вакансии по ролям (от ${data.min_group} вакансий)`}
        rows={data.by_role}
        longDays={long}
      />
      <GroupTable title="Жизнь вакансии по грейдам" rows={data.by_grade} longDays={long} />

      {data.companies.length > 0 && (
        <div className="card chart-card chart-wide">
          <h4>Компании, которые публикуют вакансии повторно</h4>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Компания</th>
                  <th>Вакансий</th>
                  <th>Повторно</th>
                  <th>{long}+ дн.</th>
                  <th>Самый долгий срок</th>
                </tr>
              </thead>
              <tbody>
                {data.companies.map((c) => (
                  <tr key={c.company}>
                    <td>
                      <Link to={`/company?name=${encodeURIComponent(c.company)}`}>{c.company}</Link>
                    </td>
                    <td>{c.vacancies}</td>
                    <td>{c.republished}</td>
                    <td>{c.long_open}</td>
                    <td>{fmtDays(c.max_span_days)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {data.longest.length > 0 && (
        <div className="card chart-card chart-wide">
          <h4>Дольше всех на рынке</h4>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Срок</th>
                  <th>Публикации</th>
                  <th>Компания</th>
                  <th>Роль</th>
                  <th>Грейд</th>
                  <th>Канал</th>
                </tr>
              </thead>
              <tbody>
                {data.longest.map((v) => (
                  <tr key={v.id} className="clickable" onClick={() => open(v.id)}>
                    <td>
                      <strong>{fmtDays(v.span_days)}</strong>
                      {v.archived && <span className="muted"> · архив</span>}
                    </td>
                    <td>
                      {v.publication_days} дн.{" "}
                      <span className="muted">
                        ({day(v.first_at)} – {day(v.last_at)})
                      </span>
                    </td>
                    <td>{v.company ?? "—"}</td>
                    <td>{v.specializations.join(", ") || "—"}</td>
                    <td>{v.grade}</td>
                    <td>@{v.source_channel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {openError && <p className="error">{openError}</p>}
        </div>
      )}
      {opened && <VacancyModal vacancy={opened} onClose={() => setOpened(null)} />}
    </>
  );
}
