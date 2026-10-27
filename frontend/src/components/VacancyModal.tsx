import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import MarketSalaryBlock from "./MarketSalaryBlock";
import InterviewPrepPanel from "./InterviewPrepPanel";
import ResumeTailoringPanel from "./ResumeTailoringPanel";
import SimilarVacanciesBlock from "./SimilarVacanciesBlock";
import VacancyFlagsBlock from "./VacancyFlagsBlock";
import VacancyHistoryBlock from "./VacancyHistoryBlock";
import type {
  CoverLetter,
  CoverLetterTone,
  MatchImportance,
  SkillMatch,
  Vacancy,
  VacancyListItem,
  VacancyMatch,
} from "../types/api";
import { cleanPostUrl } from "../utils/cleanPostUrl";
import { useUnmountSignal } from "../utils/useUnmountSignal";
import { toVacancyListItem } from "../utils/vacancyListItem";

interface Props {
  vacancy: VacancyListItem | Vacancy;
  /** Номер строки в таблице; без него заголовок — только канал. */
  index?: number;
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

/** Полный ответ GET/POST матча, а не урезанный снимок из списка вакансий. */
function hasMatchDetails(m: VacancyMatch): boolean {
  return (
    m.breakdown != null ||
    Boolean(m.model) ||
    m.matching_skills.length > 0 ||
    m.missing_skills.length > 0 ||
    (m.strengths?.length ?? 0) > 0 ||
    (m.risks?.length ?? 0) > 0
  );
}

const TONE_OPTIONS: { value: CoverLetterTone; label: string }[] = [
  { value: "neutral", label: "Нейтральный" },
  { value: "friendly", label: "Дружелюбный" },
  { value: "formal", label: "Формальный" },
];

export default function VacancyModal({ vacancy: incoming, index, onClose, resumeId }: Props) {
  const queryClient = useQueryClient();
  const unmountSignal = useUnmountSignal();
  const hasFullText = "cleaned_text" in incoming;
  const listItem = toVacancyListItem(incoming);
  const detailQ = useQuery({
    queryKey: ["vacancy", listItem.id],
    queryFn: () => api.getVacancy(listItem.id),
    enabled: !hasFullText,
    staleTime: 60_000,
    initialData: hasFullText ? (incoming as Vacancy) : undefined,
  });
  const detail = detailQ.data;
  const v = detail
    ? {
        ...listItem,
        ...detail,
        match_score: listItem.match_score ?? detail.match_score,
        match_explanation: listItem.match_explanation ?? detail.match_explanation,
      }
    : listItem;

  // Оценка из списка (join vacancy_matches) — краткий снимок до «Подробнее» / пересчёта.
  const [match, setMatch] = useState<VacancyMatch | null>(
    listItem.match_score != null
      ? {
          resume_id: resumeId ?? 0,
          vacancy_id: listItem.id,
          overall_score: listItem.match_score,
          explanation: listItem.match_explanation,
          matching_skills: [],
          missing_skills: [],
          model: "",
          created_at: "",
          cached: true,
          stale: listItem.match_stale ?? false,
        }
      : null,
  );
  // Повторный вход: кратко; полный блок — после GET или свежего матчинга.
  const [matchExpanded, setMatchExpanded] = useState(false);

  const matchM = useMutation({
    mutationFn: ({ force }: { force: boolean }) =>
      api.matchVacancy(resumeId as number, listItem.id, force, unmountSignal()),
    onSuccess: (data) => {
      setMatch(data);
      setMatchExpanded(true);
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
      void queryClient.invalidateQueries({ queryKey: ["skill-gaps"] });
    },
  });

  const expandMatchM = useMutation({
    mutationFn: () => api.getVacancyMatch(resumeId as number, listItem.id),
    onSuccess: (data) => {
      setMatch(data);
      setMatchExpanded(true);
    },
  });

  // Похожая вакансия открывается поверх текущей (своя модалка, закрытие возвращает сюда).
  const [nested, setNested] = useState<VacancyListItem | null>(null);
  const canMatch = resumeId != null;
  const matchTitle = canMatch
    ? "LLM оценит, насколько выбранное резюме подходит этой вакансии"
    : "Сначала выберите резюме в панели над таблицей";

  // Сопроводительное письмо: сохранённый вариант — база, правки и генерация — поверх.
  const [generatedLetter, setGeneratedLetter] = useState<CoverLetter | null>(null);
  const [draftOverride, setDraftOverride] = useState<string | null>(null);
  const [toneOverride, setToneOverride] = useState<CoverLetterTone | null>(null);
  const [letterCopied, setLetterCopied] = useState(false);

  const savedLetterQ = useQuery({
    queryKey: ["cover-letter", resumeId, listItem.id],
    queryFn: () =>
      api.getCoverLetter(resumeId as number, listItem.id).catch(() => null),
    enabled: resumeId != null,
    staleTime: Infinity,
    retry: false,
  });

  const savedLetter = savedLetterQ.data ?? null;
  const letter = generatedLetter ?? savedLetter;
  const letterDraft = draftOverride ?? letter?.text ?? "";
  const savedTone =
    !generatedLetter && (savedLetter?.tone === "friendly" || savedLetter?.tone === "formal")
      ? savedLetter.tone
      : null;
  const letterTone = toneOverride ?? savedTone ?? "neutral";

  const letterM = useMutation({
    mutationFn: ({ force }: { force: boolean }) =>
      api.generateCoverLetter(resumeId as number, listItem.id, letterTone, force, unmountSignal()),
    onSuccess: (data) => {
      setGeneratedLetter(data);
      setDraftOverride(null);
      setLetterCopied(false);
    },
  });

  const copyLetter = async () => {
    try {
      await navigator.clipboard.writeText(letterDraft);
      setLetterCopied(true);
    } catch {
      setLetterCopied(false);
    }
  };

  const letterTitle = canMatch
    ? "LLM напишет черновик сопроводительного письма под эту вакансию"
    : "Сначала выберите резюме в панели над таблицей";

  return (
    <div className="modal-overlay" onClick={onClose} role="presentation">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Закрыть">
          ×
        </button>
        <h3>
          {index != null && `№${index} — `}@{v.source_channel}
        </h3>
        <div className="modal-actions">
          {v.post_url && (
            <a href={cleanPostUrl(v.post_url)} target="_blank" rel="noreferrer" className="btn-link">
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
          <select
            value={letterTone}
            onChange={(e) => setToneOverride(e.target.value as CoverLetterTone)}
            disabled={!canMatch || letterM.isPending}
            title="Тон сопроводительного письма"
          >
            {TONE_OPTIONS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={!canMatch || letterM.isPending}
            title={letterTitle}
            onClick={() => letterM.mutate({ force: letter != null })}
          >
            {letterM.isPending
              ? "Генерация…"
              : letter != null
                ? "Перегенерировать письмо"
                : "Сопроводительное"}
          </button>
        </div>
        {matchM.isError && (
          <p className="error">{(matchM.error as Error).message}</p>
        )}
        {expandMatchM.isError && (
          <p className="error">{(expandMatchM.error as Error).message}</p>
        )}
        {letterM.isError && (
          <p className="error">{(letterM.error as Error).message}</p>
        )}
        {match && (
          <aside className="vacancy-match">
            <div className="vacancy-match-header">
              <strong>Соответствие резюме</strong>
              <span className={scoreClass(match.overall_score)}>
                {Math.round(match.overall_score)} / 100
              </span>
              {matchExpanded && match.confidence != null && (
                <span
                  className="caption"
                  title="Уверенность модели в качестве оценки (полнота данных), а не сам score"
                >
                  уверенность {match.confidence}%
                </span>
              )}
              {matchExpanded && match.model && (
                <span className="caption">{match.model}</span>
              )}
            </div>
            {match.explanation && (
              <p>
                <strong>Почему такая оценка:</strong> {match.explanation}
              </p>
            )}
            {matchExpanded && hasMatchDetails(match) && (
              <>
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
                        <li key={i} className="match-note">
                          {s}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {match.risks && match.risks.length > 0 && (
                  <div className="match-skills">
                    <strong className="warning">Риски:</strong>
                    <ul className="match-skill-list">
                      {match.risks.map((s, i) => (
                        <li key={i} className="match-note">
                          {s}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
            {canMatch &&
              (hasMatchDetails(match) ? (
                <button
                  type="button"
                  className="match-toggle"
                  onClick={() => setMatchExpanded((open) => !open)}
                >
                  {matchExpanded ? "Свернуть" : "Подробнее"}
                </button>
              ) : (
                <button
                  type="button"
                  className="match-toggle"
                  disabled={expandMatchM.isPending}
                  onClick={() => expandMatchM.mutate()}
                >
                  {expandMatchM.isPending ? "Загрузка…" : "Подробнее"}
                </button>
              ))}
            {match.stale && (
              <p className="caption">
                <span className="warning">
                  Оценка получена другой моделью или версией промпта — переоцените резюме.
                </span>
              </p>
            )}
            {match.cached && !match.stale && !matchM.isSuccess && (
              <p className="caption">
                Сохранённая оценка — «Переоценить резюме» запустит LLM заново.
              </p>
            )}
          </aside>
        )}
        {letter && (
          <aside className="cover-letter">
            <div className="vacancy-match-header">
              <strong>Сопроводительное письмо</strong>
              {letter.model && <span className="caption">{letter.model}</span>}
              <button type="button" onClick={copyLetter}>
                {letterCopied ? "Скопировано" : "Копировать"}
              </button>
            </div>
            <textarea
              className="cover-letter-text"
              value={letterDraft}
              onChange={(e) => setDraftOverride(e.target.value)}
              rows={12}
            />
            {letter.stale && (
              <p className="caption">
                <span className="warning">
                  Письмо получено другой моделью или версией промпта — перегенерируйте его.
                </span>
              </p>
            )}
            {letter.cached && !letterM.isSuccess && (
              <p className="caption">
                Сохранённый вариант — «Перегенерировать письмо» запустит LLM заново.
                Правки в поле не сохраняются — они для копирования.
              </p>
            )}
          </aside>
        )}
        {resumeId != null && <ResumeTailoringPanel resumeId={resumeId} vacancyId={listItem.id} />}
        {resumeId != null && <InterviewPrepPanel resumeId={resumeId} vacancyId={listItem.id} />}
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
          {v.company && (
            <span>
              Компания:{" "}
              <Link to={`/company?name=${encodeURIComponent(v.company)}`} title="Профиль компании">
                {v.company}
              </Link>
            </span>
          )}
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
        <VacancyFlagsBlock vacancyId={listItem.id} />
        <VacancyHistoryBlock
          vacancyId={listItem.id}
          onOpenId={(id) => {
            void api.getVacancy(id).then((full) => setNested(toVacancyListItem(full)));
          }}
        />
        <MarketSalaryBlock vacancyId={listItem.id} />
        <SimilarVacanciesBlock vacancyId={listItem.id} onOpen={setNested} />
        <hr />
        <div className="vacancy-full-text">
          {!hasFullText && detailQ.isLoading
            ? "Загрузка текста…"
            : detailQ.isError && !hasFullText
              ? listItem.preview || "Не удалось загрузить текст"
              : detail?.cleaned_text || detail?.raw_text || "_(пусто)_"}
        </div>
        {(detail?.ai_notes ?? v.ai_notes) && (
          <aside className="vacancy-ai-notes">
            <div className="vacancy-ai-notes-header">
              <strong>Заметки AI</strong>
              {(detail?.ai_confidence ?? v.ai_confidence) != null && (
                <span className="vacancy-ai-notes-confidence">
                  уверенность {(((detail?.ai_confidence ?? v.ai_confidence) as number) * 100).toFixed(0)}%
                </span>
              )}
            </div>
            <p>{detail?.ai_notes ?? v.ai_notes}</p>
          </aside>
        )}
        {/* Внутри .modal: клик по фону вложенной не всплывает до оверлея этой и не закрывает её. */}
        {nested && <VacancyModal vacancy={nested} resumeId={resumeId} onClose={() => setNested(null)} />}
      </div>
    </div>
  );
}
