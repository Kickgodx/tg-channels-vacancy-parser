import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Vacancy, VacancyMatch } from "../types/api";

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

export default function VacancyModal({ vacancy: v, index, onClose, resumeId }: Props) {
  const queryClient = useQueryClient();
  // Оценка из списка (join vacancy_matches) — стартовое состояние до пересчёта.
  const [match, setMatch] = useState<VacancyMatch | null>(
    v.match_score != null
      ? {
          resume_id: resumeId ?? 0,
          vacancy_id: v.id,
          score: v.match_score,
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
              <span className={scoreClass(match.score)}>{Math.round(match.score)} / 100</span>
              {match.model && <span className="caption">{match.model}</span>}
            </div>
            {match.explanation && <p>{match.explanation}</p>}
            {match.matching_skills.length > 0 && (
              <p className="match-skills">
                <strong className="success">Совпадает:</strong>{" "}
                {match.matching_skills.join(", ")}
              </p>
            )}
            {match.missing_skills.length > 0 && (
              <p className="match-skills">
                <strong className="warning">Не хватает:</strong>{" "}
                {match.missing_skills.join(", ")}
              </p>
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
