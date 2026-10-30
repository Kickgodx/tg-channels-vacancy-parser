import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { VacancyListItem } from "../types/api";
import { formatThousands } from "../utils/formatSalary";

interface Props {
  vacancyId: string;
  onOpen: (vacancy: VacancyListItem) => void;
}

const k = formatThousands;

function title(v: VacancyListItem): string {
  const role = v.detected_specializations.join(", ") || "Вакансия";
  return v.company ? `${role} · ${v.company}` : role;
}

/** Ориентировочная зарплата вакансии без вилки: по ближайшим по смыслу вакансиям с зарплатой. */
export default function SalaryEstimateBlock({ vacancyId, onOpen }: Props) {
  const q = useQuery({
    queryKey: ["salary-estimate", vacancyId],
    queryFn: () => api.getSalaryEstimate(vacancyId),
    staleTime: 60_000,
    retry: false,
  });
  const e = q.data;
  if (!e || e.status === "has_salary" || e.status === "unavailable") return null;
  if (e.status === "pending") {
    return (
      <aside className="market-salary">
        <strong>Ориентировочная зарплата</strong>
        <p className="caption">Вакансия ещё не проиндексирована — оценка появится через несколько минут.</p>
      </aside>
    );
  }
  const scope = e.grade ? `грейда ${e.grade}` : "любого грейда (грейд вакансии неизвестен)";
  if (e.status === "insufficient") {
    return (
      <aside className="market-salary">
        <strong>Ориентировочная зарплата</strong>
        <p className="caption">
          Похожих вакансий {scope} с зарплатой за год мало ({e.neighbours} из нужных {e.min_neighbours}) —
          оценить не получится.
        </p>
      </aside>
    );
  }
  return (
    <aside className="market-salary">
      <div className="vacancy-match-header">
        <strong>Ориентировочная зарплата</strong>
        <span className="caption">оценка — в вакансии зарплата не указана</span>
      </div>
      <p>
        ≈ <strong>{k(e.median)}</strong>
        <span className="muted">
          {" "}
          (обычно {k(e.p25)} – {k(e.p75)}), ₽ в месяц
        </span>
      </p>
      <p className="caption">
        По {e.neighbours} самым похожим по смыслу вакансиям {scope} с зарплатой за год. Ближайшие:
      </p>
      <ul className="salary-estimate-list">
        {e.examples.map((v) => (
          <li key={v.id}>
            <button type="button" className="similar-item" onClick={() => onOpen(v)}>
              <span className="similar-title">{title(v)}</span>
              <span className="muted">{k(v.detected_salary_avg_rub)} ₽</span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
