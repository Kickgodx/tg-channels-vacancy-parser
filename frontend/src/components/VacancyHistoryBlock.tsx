import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { VacancyChange, VacancySighting } from "../types/api";

const KIND_LABEL: Record<VacancySighting["kind"], string> = {
  original: "первая публикация",
  repost: "перепост",
  edited: "перепост с правками",
};

const FORMAT_LABEL: Record<string, string> = {
  remote: "удалёнка",
  office: "офис",
  hybrid: "гибрид",
  relocation: "релокация",
};

interface Salary {
  min: number | null;
  max: number | null;
  currency: string | null;
}

function money(v: number | null): string {
  return v == null ? "" : `${Math.round(v / 1000).toLocaleString("ru-RU")}k`;
}

function salary(s: Salary | null): string {
  if (!s) return "не указана";
  const range = s.min != null && s.max != null ? `${money(s.min)}–${money(s.max)}` : money(s.min ?? s.max);
  return `${range} ${s.currency ?? ""}`.trim();
}

function describe(c: VacancyChange): string {
  switch (c.field) {
    case "salary":
      return `зарплата: ${salary(c.old as Salary | null)} → ${salary(c.new as Salary)}`;
    case "work_format":
      return `формат: ${FORMAT_LABEL[String(c.old)] ?? c.old ?? "—"} → ${FORMAT_LABEL[String(c.new)] ?? c.new}`;
    case "city":
      return `город: ${c.old ?? "—"} → ${c.new}`;
    case "technologies":
      return [
        c.added.length > 0 ? `+ ${c.added.join(", ")}` : "",
        c.removed.length > 0 ? `− ${c.removed.join(", ")}` : "",
      ]
        .filter(Boolean)
        .join("; ");
  }
}

function day(v: string | null): string {
  return v ? new Date(v).toLocaleDateString("ru-RU") : "—";
}

/** «История публикаций»: первая публикация, перепосты и что в них поменялось. */
export default function VacancyHistoryBlock({
  vacancyId,
  onOpenId,
}: {
  vacancyId: string;
  onOpenId: (id: string) => void;
}) {
  const q = useQuery({
    queryKey: ["vacancy-history", vacancyId],
    queryFn: () => api.getVacancyHistory(vacancyId),
    staleTime: 60_000,
  });
  const h = q.data;
  if (!h) return null;
  if (h.duplicate_of) {
    return (
      <aside className="vacancy-history">
        <p>
          Это более поздняя копия другой вакансии — она скрыта из списка.{" "}
          <button type="button" className="btn-link" onClick={() => onOpenId(h.duplicate_of as string)}>
            Открыть исходную
          </button>
        </p>
      </aside>
    );
  }
  if (h.sightings.length < 2) return null;
  return (
    <aside className="vacancy-history">
      <strong>История публикаций</strong>
      <span className="caption">
        {" "}
        · {h.sightings.length} публикаций{h.edits > 0 ? `, с правками — ${h.edits}` : ""}
      </span>
      <ul>
        {h.sightings.map((s, i) => (
          <li key={i}>
            <span className="muted">{day(s.post_date)}</span>{" "}
            {s.post_url ? (
              <a href={s.post_url} target="_blank" rel="noreferrer">
                @{s.source_channel}
              </a>
            ) : (
              `@${s.source_channel}`
            )}{" "}
            — {KIND_LABEL[s.kind]}
            {s.changes.length > 0 && (
              <ul className="vacancy-history-changes">
                {s.changes.map((c, j) => (
                  <li key={j}>{describe(c)}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </aside>
  );
}
