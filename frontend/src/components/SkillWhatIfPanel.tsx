import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import type { SkillWhatIfOption, SkillWhatIfPeer } from "../types/api";
import { formatThousands as k } from "../utils/formatSalary";

function pct(v: number, digits = 0): string {
  return `${(v * 100).toFixed(digits)}%`;
}

function signedPct(v: number): string {
  const s = Math.round(v * 100);
  return s > 0 ? `+${s}%` : `${s}%`;
}

/** Подходящие вакансии и среднее покрытие, если добавить в резюме выбранные технологии. */
function simulate(peers: SkillWhatIfPeer[], added: Set<string>, threshold: number) {
  let fit = 0;
  let coverage = 0;
  for (const p of peers) {
    const covered = p.covered + p.missing.filter((t) => added.has(t)).length;
    if (covered / p.total >= threshold - 1e-9) fit += 1;
    coverage += covered / p.total;
  }
  return { fit, coverage: peers.length ? coverage / peers.length : 0 };
}

function premiumCell(o: SkillWhatIfOption, grade: string | null, role: string | null) {
  if (o.premium == null) return <span className="muted">мало данных</span>;
  const title =
    o.premium_scope === "grade"
      ? `Медиана с технологией против медианы без неё у ${grade ?? "грейда"} ${role ?? ""}`
      : `Средневзвешенно по грейдам роли ${role ?? ""}`;
  return (
    <span className={o.premium >= 0 ? "delta-up" : "delta-down"} title={title}>
      {signedPct(o.premium)}
      {o.premium_scope === "role" && <span className="muted"> (роль)</span>}
    </span>
  );
}

/** «Что если выучить»: какие недостающие технологии сильнее всего расширят выбор вакансий. */
export default function SkillWhatIfPanel({ resumeId }: { resumeId: number }) {
  const q = useQuery({
    queryKey: ["skill-what-if", resumeId],
    queryFn: () => api.getSkillWhatIf(resumeId),
    staleTime: 60_000,
    retry: false,
  });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const m = q.data;
  const sim = useMemo(
    () => (m ? simulate(m.peer_gaps, picked, m.fit_threshold) : null),
    [m, picked],
  );
  const unavailable = q.error instanceof ApiError && q.error.code === "UPSTREAM_UNAVAILABLE";

  const toggle = (tech: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(tech)) next.delete(tech);
      else next.add(tech);
      return next;
    });

  return (
    <div className="card">
      <h3>Что если выучить</h3>
      {q.isLoading && <p>Загрузка…</p>}
      {unavailable && (
        <p className="muted">Нужны эмбеддинги (смысловой поиск): {(q.error as Error).message}</p>
      )}
      {q.error && !unavailable && <p className="error">{(q.error as Error).message}</p>}
      {m && m.with_technologies === 0 && (
        <p className="muted">
          Ближайших вакансий с технологиями пока нет — индексация идёт в фоне.
        </p>
      )}
      {m && m.with_technologies > 0 && (
        <>
          <p className="caption">
            Сегмент — {m.with_technologies} вакансий с технологиями из {m.peers}, ближайших к резюме
            по смыслу за {Math.round(m.window_days / 30)} месяцев
            {m.role && (
              <>
                {" "}
                (чаще всего <strong>{m.role}</strong>
                {m.grade && `, ${m.grade}`})
              </>
            )}
            . Вакансия подходит, если резюме называет не меньше {pct(m.fit_threshold)} её
            технологий.
          </p>
          {m.options.length === 0 ? (
            <p className="muted">
              Недостающих технологий, которые встречаются в сегменте заметно часто, нет.
            </p>
          ) : (
            <>
              <div className="stat-tiles">
                <div className="stat-tile">
                  <span className="stat-value">
                    {m.baseline_fit}
                    {sim && sim.fit !== m.baseline_fit && (
                      <span className="delta-up"> → {sim.fit}</span>
                    )}
                  </span>
                  <span className="stat-label">подходящих вакансий в сегменте</span>
                </div>
                <div className="stat-tile">
                  <span className="stat-value">
                    {pct(m.baseline_coverage ?? 0)}
                    {sim && picked.size > 0 && (
                      <span className="delta-up"> → {pct(sim.coverage)}</span>
                    )}
                  </span>
                  <span className="stat-label">в среднем технологий вакансии названо в резюме</span>
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th title="Отметьте, чтобы посчитать эффект нескольких технологий вместе">
                      Добавить
                    </th>
                    <th>Технология</th>
                    <th title="Вакансий сегмента, где технология нужна, а в резюме её нет">
                      Нужна в
                    </th>
                    <th title="Сколько вакансий станут подходящими, если добавить только её">
                      +подходящих
                    </th>
                    <th title="Медиана с технологией против медианы без неё внутри грейда">
                      Премия
                    </th>
                    <th title="Медиана зарплаты вакансий сегмента с технологией">Медиана с ней</th>
                  </tr>
                </thead>
                <tbody>
                  {m.options.map((o) => (
                    <tr key={o.technology}>
                      <td>
                        <input
                          type="checkbox"
                          checked={picked.has(o.technology)}
                          onChange={() => toggle(o.technology)}
                          aria-label={`Добавить ${o.technology}`}
                        />
                      </td>
                      <td>
                        <strong>{o.technology}</strong>
                      </td>
                      <td>
                        {o.demand} <span className="muted">({pct(o.demand_share)})</span>
                      </td>
                      <td>{o.new_fit > 0 ? <strong>+{o.new_fit}</strong> : "0"}</td>
                      <td>{premiumCell(o, m.grade, m.role)}</td>
                      <td>{o.median_with != null ? k(o.median_with) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="caption">
                Технология засчитывается резюме, если названа в тексте. «+подходящих» по одной
                технологии бывает 0, а вместе с другой — больше: отметьте несколько. Премия — по
                рынку роли за год: зарплата выше не потому, что технологию выучили, а потому, что её
                требуют в таких вакансиях.
              </p>
            </>
          )}
        </>
      )}
    </div>
  );
}
