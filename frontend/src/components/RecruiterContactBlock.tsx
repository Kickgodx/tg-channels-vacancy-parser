import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { RecruiterContact } from "../types/api";

interface Props {
  vacancyId: string;
  /** Текст сопроводительного письма с правками из карточки; пусто — короткий шаблон. */
  letterDraft: string;
  role?: string | null;
  company?: string | null;
  postUrl?: string | null;
}

function shortTemplate(
  role?: string | null,
  company?: string | null,
  postUrl?: string | null,
): string {
  const what = role ? `«${role}»` : "";
  const where = company ? ` в ${company}` : "";
  const link = postUrl ? ` (${postUrl})` : "";
  return (
    `Здравствуйте! Пишу по вакансии ${what}${where}${link}. ` +
    "Мне интересна эта позиция — могу прислать резюме и рассказать об опыте."
  );
}

function contactHref(c: RecruiterContact, text: string, subject: string): string {
  if (c.kind === "email") {
    return `${c.url}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(text)}`;
  }
  // У бота поле ввода не заполняется — просто открываем его.
  if (c.kind === "telegram_bot") return c.url;
  return `${c.url}?text=${encodeURIComponent(text)}`;
}

function label(c: RecruiterContact): string {
  if (c.kind === "email") return `Письмо ${c.value}`;
  if (c.kind === "telegram_bot") return `Бот @${c.value}`;
  return `Написать @${c.value}`;
}

/** Подпись ссылки на отклик по сайту: «Откликнуться на hh.ru», иначе «Откликнуться · сайт». */
function formLabel(url: string): string {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    if (host === "hh.ru" || host.endsWith(".hh.ru")) return "Откликнуться на hh.ru";
    return `Откликнуться · ${host}`;
  } catch {
    return "Откликнуться";
  }
}

/** «Связаться с рекрутером»: Telegram или почта с подставленным текстом отклика. */
export default function RecruiterContactBlock({
  vacancyId,
  letterDraft,
  role,
  company,
  postUrl,
}: Props) {
  const q = useQuery({
    queryKey: ["vacancy-contacts", vacancyId],
    queryFn: () => api.getRecruiterContacts(vacancyId),
    staleTime: 5 * 60_000,
  });
  const [copied, setCopied] = useState(false);
  const contacts = q.data?.contacts ?? [];
  const forms = q.data?.application_urls ?? [];
  if (contacts.length === 0 && forms.length === 0) return null;

  const hasLetter = letterDraft.trim().length > 0;
  const text = hasLetter ? letterDraft : shortTemplate(role, company, postUrl);
  const subject = `Отклик на вакансию${role ? ` ${role}` : ""}${company ? ` — ${company}` : ""}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <aside className="recruiter-contacts">
      <div className="vacancy-match-header">
        <strong>Связаться с рекрутером</strong>
        {contacts.some((c) => c.kind !== "telegram_bot") && (
          <button type="button" onClick={copy}>
            {copied ? "Скопировано" : "Копировать текст"}
          </button>
        )}
      </div>
      <div className="recruiter-contacts-actions">
        {contacts.map((c) => (
          <a
            key={`${c.kind}:${c.value}`}
            href={contactHref(c, text, subject)}
            target="_blank"
            rel="noreferrer"
            className={c.has_contact_hint ? "btn-link" : "btn-link secondary"}
            title={
              c.has_contact_hint
                ? "Указан в вакансии как контакт для отклика"
                : "Упомянут в тексте без пометки «контакт» — проверьте, что это рекрутер"
            }
          >
            {label(c)}
          </a>
        ))}
        {forms.map((u) => (
          <a key={u} href={u} target="_blank" rel="noreferrer" className="btn-link secondary">
            {formLabel(u)}
          </a>
        ))}
      </div>
      {contacts.some((c) => c.kind !== "telegram_bot") && (
        <p className="caption">
          {hasLetter
            ? "В сообщение подставится сопроводительное письмо с вашими правками."
            : "В сообщение подставится короткий шаблон — сгенерируйте сопроводительное, чтобы отправить его."}{" "}
          Если Telegram не подставил текст, вставьте его из буфера.
        </p>
      )}
    </aside>
  );
}
