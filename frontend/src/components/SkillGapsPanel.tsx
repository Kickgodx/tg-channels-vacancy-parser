import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { SkillGapItem, Vacancy } from "../types/api";
import VacancyModal from "./VacancyModal";

interface Props {
  resumeId: number;
}

function pct(share: number): string {
  return `${Math.round(share * 100)}%`;
}

function Priority({ item }: { item: SkillGapItem }) {
  const parts: string[] = [];
  if (item.critical) parts.push(`критично ${item.critical}`);
  if (item.important) parts.push(`важно ${item.important}`);
  if (item.secondary) parts.push(`желательно ${item.secondary}`);
  return (
    <span className={item.critical ? "match-skill-importance critical" : "match-skill-importance"}>
      {parts.join(" · ")}
    </span>
  );
}

function GapTable({
  items,
  market,
  onOpen,
}: {
  items: SkillGapItem[];
  market: boolean;
  onOpen: (id: string) => void;
}) {
  return (
    <table>
      <thead>
        <tr>
          <th>Навык</th>
          <th>Не хватает</th>
          <th>Приоритет</th>
          {market && <th title="Активных вакансий каталога с этой технологией">Спрос</th>}
          <th>Примеры</th>
        </tr>
      </thead>
      <tbody>
        {items.map((g) => (
          <tr key={g.name}>
            <td>
              <strong>{g.name}</strong>
            </td>
            <td>
              {g.vacancies} <span className="muted">({pct(g.share)})</span>
            </td>
            <td>
              <Priority item={g} />
            </td>
            {market && <td>{g.market_vacancies ?? "—"}</td>}
            <td>
              {g.vacancy_ids.map((id, i) => (
                <button key={id} type="button" className="btn-link" onClick={() => onOpen(id)}>
                  #{i + 1}
                </button>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** «Чего не хватает»: недостающие навыки по всем сохранённым оценкам резюме. */
export default function SkillGapsPanel({ resumeId }: Props) {
  const gapsQ = useQuery({
    queryKey: ["skill-gaps", resumeId],
    queryFn: () => api.getSkillGaps(resumeId),
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

  const data = gapsQ.data;
  return (
    <div className="card">
      <h3>Чего не хватает</h3>
      {gapsQ.isLoading && <p>Загрузка…</p>}
      {gapsQ.error && <p className="error">{(gapsQ.error as Error).message}</p>}
      {data && data.evaluated === 0 && (
        <p className="muted">
          Оценок пока нет: откройте вакансию на странице «Вакансии» и нажмите «Оценить резюме».
        </p>
      )}
      {data && data.evaluated > 0 && (
        <>
          <p className="caption">
            По {data.evaluated} оценённым вакансиям. «Спрос» — сколько активных вакансий в базе
            требуют технологию.
          </p>
          {data.technologies.length > 0 && (
            <>
              <h4>Технологии и навыки</h4>
              <GapTable items={data.technologies} market onOpen={open} />
            </>
          )}
          {data.other.length > 0 && (
            <>
              <h4>Прочее</h4>
              <GapTable items={data.other} market={false} onOpen={open} />
            </>
          )}
          {data.technologies.length === 0 && data.other.length === 0 && (
            <p className="muted">В оценках нет недостающих навыков.</p>
          )}
        </>
      )}
      {openError && <p className="error">{openError}</p>}
      {opened && (
        <VacancyModal
          vacancy={opened}
          resumeId={resumeId}
          onClose={() => setOpened(null)}
        />
      )}
    </div>
  );
}
