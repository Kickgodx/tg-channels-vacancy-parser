import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { VacancyListItem } from "../types/api";

interface Props {
  vacancyId: string;
  onOpen: (vacancy: VacancyListItem) => void;
}

function k(v: number | null | undefined): string | null {
  return v == null ? null : `${Math.round(v / 1000).toLocaleString("ru-RU")}k ₽`;
}

function title(v: VacancyListItem): string {
  const role = v.detected_specializations.join(", ") || "Вакансия";
  const grade = v.detected_grade && v.detected_grade !== "unknown" ? ` · ${v.detected_grade}` : "";
  return `${role}${grade}`;
}

/** Похожие активные вакансии по эмбеддингу; клик открывает вакансию поверх текущей. */
export default function SimilarVacanciesBlock({ vacancyId, onOpen }: Props) {
  const q = useQuery({
    queryKey: ["similar-vacancies", vacancyId],
    queryFn: () => api.getSimilarVacancies(vacancyId),
    staleTime: 60_000,
    retry: false,
  });
  const data = q.data;
  if (!data || (data.items.length === 0 && !data.pending)) return null;
  return (
    <aside className="similar-vacancies">
      <strong>Похожие вакансии</strong>
      {data.pending && (
        <p className="caption">
          Вакансия ещё не проиндексирована — похожие появятся через несколько минут.
        </p>
      )}
      <ul>
        {data.items.map(({ vacancy: v, similarity }) => (
          <li key={v.id}>
            <button type="button" className="similar-item" onClick={() => onOpen(v)}>
              <span className="similar-title">{title(v)}</span>
              <span className="muted">
                {[v.company, k(v.detected_salary_avg_rub), v.work_format, `@${v.source_channel}`]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              <span className="similar-score" title="Близость по смыслу (1 — тот же смысл)">
                {Math.round(similarity * 100)}%
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
