import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import VacancyModal from "../components/VacancyModal";
import type { AskMarketFilters, AskMarketResult, AskMarketTurn, Vacancy } from "../types/api";
import { formatThousands as k } from "../utils/formatSalary";

interface Message {
  id: number;
  question: string;
  result?: AskMarketResult;
  error?: string;
}

// История — только во вкладке: sessionStorage, переживает F5, но не уходит на сервер.
const STORAGE_KEY = "ask-market-dialog";
const HISTORY_TURNS = 3;

const SUGGESTIONS = [
  "Сколько платят senior Python-разработчикам на удалёнке?",
  "Какие технологии чаще всего требуют в QA за последний месяц?",
  "Go или Python: где больше вакансий и выше зарплаты у senior?",
  "Какие компании дольше всех не могут закрыть вакансии?",
];

const FILTER_LABELS: Record<keyof AskMarketFilters, string> = {
  specializations: "Роль",
  technologies: "Технологии",
  grade: "Грейд",
  work_format: "Формат",
  date_from: "С",
  date_to: "По",
  has_salary: "Только с зарплатой",
  q: "Поиск",
};

function loadDialog(): Message[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Message[]) : [];
  } catch {
    return [];
  }
}

function saveDialog(messages: Message[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  } catch {
    // Хранилище недоступно (приватный режим) — диалог живёт до перезагрузки.
  }
}

function vacanciesLink(f: AskMarketFilters): string {
  const p = new URLSearchParams();
  for (const s of f.specializations ?? []) p.append("specialization", s);
  for (const t of f.technologies ?? []) p.append("technology", t);
  if (f.grade) p.set("grade", f.grade);
  if (f.work_format) p.set("work_format", f.work_format);
  if (f.date_from) p.set("date_from", f.date_from);
  if (f.date_to) p.set("date_to", f.date_to);
  if (f.has_salary) p.set("has_salary", "true");
  if (f.q) p.set("q", f.q);
  const qs = p.toString();
  return `/vacancies${qs ? `?${qs}` : ""}`;
}

function FilterChips({ filters }: { filters: AskMarketFilters }) {
  const entries = Object.entries(filters).filter(
    ([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length),
  );
  if (entries.length === 0) return <span className="muted">вся база</span>;
  return (
    <>
      {entries.map(([key, value]) => (
        <span key={key} className="ask-chip">
          {FILTER_LABELS[key as keyof AskMarketFilters] ?? key}:{" "}
          {Array.isArray(value) ? value.join(", ") : value === true ? "да" : String(value)}
        </span>
      ))}
    </>
  );
}

function Answer({ result, onOpen }: { result: AskMarketResult; onOpen: (id: string) => void }) {
  const count = result.facts?.count;
  const examples = result.facts?.examples ?? [];
  return (
    <div className="ask-answer">
      <p className="ask-answer-text">{result.answer}</p>
      {result.answerable && (
        <div className="ask-scope">
          <div>
            <span className="caption">Считали по: </span>
            <FilterChips filters={result.filters} />
            {count && (
              <span className="caption">
                {" "}
                — {count.vacancies} вакансий (активных {count.active}), с зарплатой{" "}
                {count.with_salary}
              </span>
            )}{" "}
            <Link to={vacanciesLink(result.filters)}>Открыть в списке</Link>
          </div>
          {result.variants.map((v) => (
            <div key={v.label}>
              <span className="caption">{v.label}: </span>
              <FilterChips filters={v.filters} /> <Link to={vacanciesLink(v.filters)}>Открыть</Link>
            </div>
          ))}
          {result.unrecognized.length > 0 && (
            <p className="caption warning">
              Не нашёл в словаре и не учёл: {result.unrecognized.join(", ")}
            </p>
          )}
        </div>
      )}
      {examples.length > 0 && (
        <ul className="ask-examples">
          {examples.map((e) => (
            <li key={e.id}>
              <button type="button" className="link-button" onClick={() => onOpen(e.id)}>
                {e.roles.join(", ") || "Вакансия"}
                {e.grade && e.grade !== "unknown" && `, ${e.grade}`}
                {e.company && ` — ${e.company}`}
              </button>
              <span className="muted">
                {" "}
                {e.salary != null && `${k(e.salary)} ₽ · `}
                {e.date}
              </span>
            </li>
          ))}
        </ul>
      )}
      {result.model && <p className="caption">{result.model}</p>}
    </div>
  );
}

/** «Спросить базу»: вопрос о рынке → цифры по каталогу → короткий ответ модели только по ним. */
export default function AskPage() {
  const [messages, setMessages] = useState<Message[]>(loadDialog);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [opened, setOpened] = useState<Vacancy | null>(null);
  const jobRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => saveDialog(messages), [messages]);
  useEffect(
    () => bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }),
    [messages, pending],
  );
  useEffect(() => () => abortRef.current?.abort(), []);

  const ask = async (text: string) => {
    const question = text.trim();
    if (question.length < 3 || pending) return;
    const history: AskMarketTurn[] = messages
      .filter((m) => m.result?.answerable)
      .slice(-HISTORY_TURNS)
      .map((m) => ({ question: m.question, answer: m.result!.answer, filters: m.result!.filters }));
    const id = messages.reduce((max, m) => Math.max(max, m.id), 0) + 1;
    setMessages((prev) => [...prev, { id, question }]);
    setDraft("");
    setPending(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const result = await api.askMarket(question, history, controller.signal, (jobId) => {
        jobRef.current = jobId;
      });
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, result } : m)));
    } catch (e) {
      const message =
        e instanceof ApiError && e.code === "CANCELLED" ? "Вопрос отменён." : (e as Error).message;
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, error: message } : m)));
    } finally {
      setPending(false);
      jobRef.current = null;
      abortRef.current = null;
    }
  };

  const cancel = () => {
    if (jobRef.current) void api.cancelLlmJob(jobRef.current).catch(() => undefined);
  };

  const open = (vacancyId: string) => {
    void api.getVacancy(vacancyId).then(setOpened);
  };

  return (
    <div className="card ask-page">
      <div className="ask-header">
        <h2>Спросить базу</h2>
        {messages.length > 0 && !pending && (
          <button type="button" className="secondary" onClick={() => setMessages([])}>
            Новый диалог
          </button>
        )}
      </div>
      <p className="caption">
        Вопрос о рынке вакансий: модель переводит его в фильтры каталога, цифры считает база, а
        ответ модель пишет только по ним. Уточнения («а для middle?») учитывают прошлые вопросы.
        Каждый вопрос — один вызов LLM из суточной квоты.
      </p>

      {messages.length === 0 && (
        <div className="ask-suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="secondary" onClick={() => void ask(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="ask-dialog">
        {messages.map((m) => (
          <div key={m.id} className="ask-turn">
            <div className="ask-question">{m.question}</div>
            {m.result && <Answer result={m.result} onOpen={open} />}
            {m.error && <p className="error">{m.error}</p>}
            {!m.result && !m.error && pending && (
              <p className="muted">
                Считаю…{" "}
                <button type="button" className="link-button" onClick={cancel}>
                  Отменить
                </button>
              </p>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(draft);
        }}
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask(draft);
            }
          }}
          placeholder="Например: сколько вакансий DevOps в офисе за последний месяц?"
          maxLength={500}
          rows={2}
          disabled={pending}
        />
        <button type="submit" disabled={pending || draft.trim().length < 3}>
          {pending ? "Считаю…" : "Спросить"}
        </button>
      </form>
      {opened && <VacancyModal vacancy={opened} onClose={() => setOpened(null)} />}
    </div>
  );
}
