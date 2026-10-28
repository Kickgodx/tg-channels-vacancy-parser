import { useQuery } from "@tanstack/react-query";
import { Fragment } from "react";
import { ApiError, api } from "../api/client";
import type { CareerPath, CareerSegment } from "../types/api";
import { formatThousands as k } from "../utils/formatSalary";

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function signedPct(v: number): string {
  const s = Math.round(v * 100);
  return s > 0 ? `+${s}%` : `${s}%`;
}

function segmentLabel(s: CareerSegment): string {
  return s.grade ? `${s.role}, ${s.grade}` : `${s.role}, все грейды`;
}

function Route({ path }: { path: CareerPath }) {
  if (path.route.length === 0) {
    return <span className="muted">частых недостающих технологий нет</span>;
  }
  return (
    <>
      {path.route.map((s, i) => (
        <Fragment key={s.technology}>
          {i > 0 && " → "}
          <strong
            title={`Нужна в ${pct(s.demand_share)} вакансий; после этого шага подходит ${pct(s.fit_share)}, покрытие ${pct(s.coverage)}`}
          >
            {s.technology}
          </strong>
          <span className="muted"> ({pct(s.fit_share)})</span>
        </Fragment>
      ))}
    </>
  );
}

/** «Куда расти»: следующий грейд своей роли и соседние роли — что выучить и сколько это стоит. */
export default function CareerPathsPanel({ resumeId }: { resumeId: number }) {
  const q = useQuery({
    queryKey: ["career-paths", resumeId],
    queryFn: () => api.getCareerPaths(resumeId),
    staleTime: 60_000,
    retry: false,
  });
  const m = q.data;
  const unavailable = q.error instanceof ApiError && q.error.code === "UPSTREAM_UNAVAILABLE";
  const cur = m?.current;

  return (
    <div className="card">
      <h3>Куда расти</h3>
      {q.isLoading && <p>Загрузка…</p>}
      {unavailable && (
        <p className="muted">Нужны эмбеддинги (смысловой поиск): {(q.error as Error).message}</p>
      )}
      {q.error && !unavailable && <p className="error">{(q.error as Error).message}</p>}
      {m && !cur && (
        <p className="muted">
          Ближайших вакансий с ролью пока нет — индексация идёт в фоне.
        </p>
      )}
      {m && cur && (
        <>
          <p>
            Сейчас: <strong>{segmentLabel(cur)}</strong> — подходит {pct(cur.fit_share)} из{" "}
            {cur.vacancies} вакансий
            {cur.salary_median != null && (
              <>
                , медиана <strong>{k(cur.salary_median)}</strong>
              </>
            )}
            .
          </p>
          {m.paths.length === 0 ? (
            <p className="muted">
              Ни грейда выше, ни соседних ролей с заметным числом вакансий рядом с резюме нет.
            </p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Куда</th>
                  <th title="Доля роли среди вакансий, ближайших к резюме по смыслу">Близость</th>
                  <th title="Доля вакансий цели, которым резюме подходит уже сейчас">Подходит</th>
                  <th title="Что выучить по порядку; в скобках — сколько вакансий цели подойдёт после шага">
                    Маршрут
                  </th>
                  <th title="Медиана зарплаты вакансий цели">Медиана</th>
                  <th title="Медиана цели против медианы текущей позиции">К текущей</th>
                </tr>
              </thead>
              <tbody>
                {m.paths.map((p) => (
                  <tr key={`${p.kind}-${p.role}-${p.grade ?? ""}`}>
                    <td>
                      <strong>{segmentLabel(p)}</strong>
                      {p.kind === "grade" && <span className="muted"> · грейд выше</span>}
                      <div className="muted">{p.vacancies} вакансий</div>
                    </td>
                    <td>{p.closeness != null ? pct(p.closeness) : "—"}</td>
                    <td title={`Покрытие технологий ${pct(p.coverage)}`}>{pct(p.fit_share)}</td>
                    <td>
                      <Route path={p} />
                    </td>
                    <td>{p.salary_median != null ? k(p.salary_median) : "—"}</td>
                    <td>
                      {p.salary_uplift != null ? (
                        <span className={p.salary_uplift >= 0 ? "delta-up" : "delta-down"}>
                          {signedPct(p.salary_uplift)}
                        </span>
                      ) : (
                        <span className="muted">мало данных</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="caption">
            Позиция — самые частые роль и грейд среди {Math.min(m.peers, 50)} вакансий, ближайших к
            резюме по смыслу за {Math.round(m.window_days / 30)} месяцев; соседние роли — частые
            среди {m.peers} ближайших. Вакансия подходит, если резюме называет не меньше{" "}
            {pct(m.fit_threshold)} её технологий. Маршрут: каждая следующая технология открывает
            больше всего вакансий цели вместе с уже выбранными. Зарплата — по рынку роли: это не
            обещание, а то, что платят в таких вакансиях.
          </p>
        </>
      )}
    </div>
  );
}
