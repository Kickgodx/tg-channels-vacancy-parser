import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { MatchImportance, SkillMatch, Vacancy, VacancyMatch } from "../types/api";

interface Props {
  vacancy: Vacancy;
  index: number;
  onClose: () => void;
  /** Резюме, выбранное на странице вакансий; без него матчинг недоступен. */
  resumeId?: number | null;
}

function scoreClass(score: number): string {
  if (score >= 80) return "match-score strong";
  if (score >= 60) return "match-score partial";
  return "match-score weak";
}

const IMPORTANCE_LABEL: Record<MatchImportance, string> = {
  critical: "Критично",
  important: "Важно",
  secondary: "Желательно",
};

function SkillRow({ item, matched }: { item: SkillMatch; matched: boolean }) {
  return (
    <li className="match-skill" title={item.evidence || undefined}>
      <span className={matched ? "match-skill-mark ok" : "match-skill-mark miss"}>
        {matched ? "✓" : "!"}
      </span>
      <span className="match-skill-name">{item.skill}</span>
      <span className={`match-skill-importance ${item.importance}`}>
        {IMPORTANCE_LABEL[item.importance] ?? item.importance}
      </span>
    </li>
  );
}

const BREAKDOWN_ROWS: [keyof NonNullable<VacancyMatch["breakdown"]>, string][] = [
  ["role_match", "Роль"],
  ["skills_match", "Навыки"],
  ["seniority_match", "Грейд"],
  ["domain_match", "Домен"],
  ["work_format_match", "Формат работы"],
];

export default function VacancyModal({ vacancy: v, index, onClose, resumeId }: Props) {
  const queryClient = useQueryClient();
  // Оценка из списка (join vacancy_matches) — стартовое состояние до пересчёта.
  const [match, setMatch] = useState<VacancyMatch | null>(
    v.match_score != null
      ? {
          resume_id: resumeId ?? 0,
          vacancy_id: v.id,
          overall_score: v.match_score,
          explanation: v.match_explanation,
          matching_skills: [],
          missing_skills: [],
          model: "",
          created_at: "",
          cached: true,
        }
      : null,
  );

  const matchM = useMutation({
    mutationFn: ({ force }: { force: boolean }) =>
      api.matchVacancy(resumeId as number, v.id, force),
    onSuccess: (data) => {
      setMatch(data);
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
    },
  });

  const canMatch = resumeId != null;
  const matchTitle = canMatch
    ? "LLM оценит, насколько выбранное резюме подходит этой вакансии"
    : "Сначала выберите резюме в панели над таблицей";

  return (
    <div className="modal-overlay" onClick={onClose} role="presentation">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Закрыть">
          ×
        </button>
        <h3>№{index} — @{v.source_channel}</h3>
        <div className="modal-actions">
          {v.post_url && (
            <a href={v.post_url} target="_blank" rel="noreferrer" className="btn-link">
              Открыть пост
            </a>
          )}
          {v.source_url && (
            <a href={v.source_url} target="_blank" rel="noreferrer" className="btn-link secondary">
              Канал
            </a>
          )}
          <button
            type="button"
            disabled={!canMatch || matchM.isPending}
            title={matchTitle}
            onClick={() => matchM.mutate({ force: match != null })}
          >
            {matchM.isPending
              ? "Оценка…"
              : match != null
                ? "Переоценить резюме"
                : "Оценить резюме"}
          </button>
        </div>
        {matchM.isError && (
          <p className="error">{(matchM.error as Error).message}</p>
        )}
        {match && (
          <aside className="vacancy-match">
            <div className="vacancy-match-header">
              <strong>Соответствие резюме</strong>
              <span className={scoreClass(match.overall_score)}>
                {Math.round(match.overall_score)} / 100
              </span>
              {match.confidence != null && (
                <span
                  className="caption"
                  title="Уверенность модели в качестве оценки (полнота данных), а не сам score"
                >
                  уверенность {match.confidence}%
                </span>
              )}
              {match.model && <span className="caption">{match.model}</span>}
            </div>
            {match.breakdown && (
              <div className="match-breakdown">
                {BREAKDOWN_ROWS.map(([key, label]) => (
                  <div key={key} className="match-breakdown-row">
                    <span className="match-breakdown-label">{label}</span>
                    <span className="match-breakdown-value">
                      {match.breakdown![key]}%
                    </span>
                  </div>
                ))}
              </div>
            )}
            {match.explanation && (
              <p>
                <strong>Почему такая оценка:</strong> {match.explanation}
              </p>
            )}
            {match.matching_skills.length > 0 && (
              <div className="match-skills">
                <strong className="success">Совпадает:</strong>
                <ul className="match-skill-list">
                  {match.matching_skills.map((s) => (
                    <SkillRow key={s.skill} item={s} matched />
                  ))}
                </ul>
              </div>
            )}
            {match.missing_skills.length > 0 && (
              <div className="match-skills">
                <strong className="warning">Не хватает:</strong>
                <ul className="match-skill-list">
                  {match.missing_skills.map((s) => (
                    <SkillRow key={s.skill} item={s} matched={false} />
                  ))}
                </ul>
              </div>
            )}
            {match.strengths && match.strengths.length > 0 && (
              <div className="match-skills">
                <strong className="success">Сильные стороны:</strong>
                <ul className="match-skill-list">
                  {match.strengths.map((s, i) => (
                    <li key={i} className="match-note">{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {match.risks && match.risks.length > 0 && (
              <div className="match-skills">
                <strong className="warning">Риски:</strong>
                <ul className="match-skill-list">
                  {match.risks.map((s, i) => (
                    <li key={i} className="match-note">{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {match.cached && !matchM.isSuccess && (
              <p className="caption">
                Сохранённая оценка — «Переоценить резюме» запустит LLM заново.
              </p>
            )}
          </aside>
        )}
        <div className="meta-line">
          {v.post_date && <span>Дата: {new Date(v.post_date).toLocaleString()}</span>}
          {v.detected_salary_avg != null && (
            <span>
              Зарплата: ~{v.detected_salary_avg.toLocaleString()} {v.salary_currency ?? ""}
            </span>
          )}
          {v.work_format && <span>Формат: {v.work_format}</span>}
          {v.detected_grade && v.detected_grade !== "unknown" && <span>Грейд: {v.detected_grade}</span>}
          {v.country && <span>Страна: {v.country}</span>}
          {v.city && <span>Город: {v.city}</span>}
          {v.company && <span>Компания: {v.company}</span>}
          {v.contact && <span>Контакт: {v.contact}</span>}
        </div>
        {v.detected_specializations?.length > 0 && (
          <p>
            <strong>Специализации:</strong> {v.detected_specializations.join(", ")}
          </p>
        )}
        {v.technologies && v.technologies.length > 0 && (
          <p>
            <strong>Технологии:</strong> {v.technologies.join(", ")}
          </p>
        )}
        <hr />
        <div className="vacancy-full-text">{v.cleaned_text || v.raw_text || "_(пусто)_"}</div>
        {v.ai_notes && (
          <aside className="vacancy-ai-notes">
            <div className="vacancy-ai-notes-header">
              <strong>Заметки AI</strong>
              {v.ai_confidence != null && (
                <span className="vacancy-ai-notes-confidence">
                  уверенность {(v.ai_confidence * 100).toFixed(0)}%
                </span>
              )}
            </div>
            <p>{v.ai_notes}</p>
          </aside>
        )}
      </div>
    </div>
  );
}
