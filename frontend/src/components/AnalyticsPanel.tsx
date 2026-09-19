import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type VacancyQueryParams } from "../api/client";
import type { SalaryAnalytics, SalaryGroupStats } from "../types/api";

interface Props {
  params: VacancyQueryParams;
}

function fmtRub(v?: number | null): string {
  if (v == null) return "—";
  return Math.round(v).toLocaleString("ru-RU");
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}

function salarySuffix(data?: SalaryAnalytics): string {
  const sd = data?.salary_display;
  if (!sd || sd.display_currency !== "RUB") return "";
  return sd.rates_as_of ? `, ₽ (курс ЦБ ${sd.rates_as_of})` : ", ₽";
}

function QuantileTable({ title, rows }: { title: string; rows: SalaryGroupStats[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="card chart-card">
      <h4>{title}</h4>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Группа</th>
              <th>n</th>
              <th>min</th>
              <th>p25</th>
              <th>Медиана</th>
              <th>p75</th>
              <th>max</th>
              <th>Среднее</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.group}>
                <td>{r.group}</td>
                <td>{r.count}</td>
                <td>{fmtRub(r.min)}</td>
                <td>{fmtRub(r.q1)}</td>
                <td>
                  <strong>{fmtRub(r.median)}</strong>
                </td>
                <td>{fmtRub(r.q3)}</td>
                <td>{fmtRub(r.max)}</td>
                <td>{fmtRub(r.mean)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function AnalyticsPanel({ params }: Props) {
  const salariesQ = useQuery({
    queryKey: ["analytics-salaries", params],
    queryFn: () => api.getSalaryAnalytics(params),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const sourcesQ = useQuery({
    queryKey: ["analytics-sources", params],
    queryFn: () => api.getSourceStats(params),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const companiesQ = useQuery({
    queryKey: ["analytics-companies", params],
    queryFn: () => api.getCompanyStats(params),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });

  const salaries = salariesQ.data;
  const suffix = salarySuffix(salaries);
  const monthlyData = (salaries?.monthly ?? []).map((b) => ({
    period: b.period,
    median: b.median != null ? Math.round(b.median) : null,
    mean: b.mean != null ? Math.round(b.mean) : null,
  }));
  const distData = salaries?.distribution ?? [];
  const sources = sourcesQ.data?.items ?? [];
  const companies = companiesQ.data?.items ?? [];
  const anyError = salariesQ.error ?? sourcesQ.error ?? companiesQ.error;

  return (
    <>
      {anyError && <p className="error">{(anyError as Error).message}</p>}
      <div className="charts-grid">
        <QuantileTable title={`Зарплата по грейдам${suffix}`} rows={salaries?.by_grade ?? []} />
        <QuantileTable
          title={`Зарплата по формату работы${suffix}`}
          rows={salaries?.by_work_format ?? []}
        />
        <QuantileTable
          title={`Зарплата по специализациям${suffix}`}
          rows={salaries?.by_specialization ?? []}
        />

        {monthlyData.length > 0 && (
          <div className="card chart-card">
            <h4>Медианная зарплата по месяцам{suffix}</h4>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={monthlyData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip formatter={(v: number) => fmtRub(v)} />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="median"
                  name="Медиана"
                  stroke="#7950f2"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="mean"
                  name="Среднее"
                  stroke="#adb5bd"
                  strokeWidth={1.5}
                  strokeDasharray="4 4"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {distData.some((b) => b.count > 0) && (
          <div className="card chart-card">
            <h4>Распределение зарплат{suffix}</h4>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={distData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="range" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip />
                <Bar dataKey="count" name="Вакансий" fill="#12b886" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {sources.length > 0 && (
          <div className="card chart-card chart-wide">
            <h4>Качество каналов</h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Канал</th>
                    <th>Вакансий</th>
                    <th>Дублей</th>
                    <th>% уникальных</th>
                    <th>Ср. score</th>
                    <th>% с зарплатой</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((s) => (
                    <tr key={s.channel}>
                      <td>@{s.channel}</td>
                      <td>{s.vacancies}</td>
                      <td>{s.duplicates}</td>
                      <td>{fmtPct(s.unique_share)}</td>
                      <td>{s.avg_score != null ? s.avg_score.toFixed(1) : "—"}</td>
                      <td>{fmtPct(s.with_salary_share)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {companies.length > 0 && (
          <div className="card chart-card chart-wide">
            <h4>Топ компаний</h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Компания</th>
                    <th>Вакансий</th>
                    <th>Ср. score</th>
                    <th>С зарплатой</th>
                    <th>Ср. зарплата{suffix}</th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map((c) => (
                    <tr key={c.company}>
                      <td>{c.company}</td>
                      <td>{c.vacancies}</td>
                      <td>{c.avg_score != null ? c.avg_score.toFixed(1) : "—"}</td>
                      <td>{c.with_salary}</td>
                      <td>{fmtRub(c.avg_salary_rub)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
