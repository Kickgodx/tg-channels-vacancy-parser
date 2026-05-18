import type { Vacancy } from "../types/api";

interface Props {
  vacancy: Vacancy;
  index: number;
  onClose: () => void;
}

export default function VacancyModal({ vacancy: v, index, onClose }: Props) {
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
        </div>
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
      </div>
    </div>
  );
}
