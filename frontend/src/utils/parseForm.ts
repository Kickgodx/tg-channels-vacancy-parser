import type { ConfigDefaults, ParseFormState, RunCreateBody } from "../types/api";

const TODAY_ALIASES = new Set(["today", "сегодня", "сейчас", "now"]);

/** Текущая дата в локальной таймзоне браузера (YYYY-MM-DD). */
export function todayIso(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Значение для input[type=date]: today/пусто → сегодня в браузере, иначе ISO-дата из конфига. */
export function resolveDateToForForm(dateTo: string | null | undefined): string {
  const raw = (dateTo ?? "").trim().toLowerCase();
  if (!raw || TODAY_ALIASES.has(raw)) return todayIso();
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  return todayIso();
}

export function splitCsv(s: string): string[] {
  return s
    .split(/[,\n]+/)
    .map((p) => p.trim())
    .filter(Boolean);
}

export function formFromDefaults(d: ConfigDefaults): ParseFormState {
  return {
    channelsText: d.channels.join("\n"),
    dateFrom: d.search.date_from ?? "",
    dateTo: resolveDateToForForm(d.search.date_to),
    anySpecialization: d.filters.any_specialization,
    specializations: d.filters.specializations.length
      ? d.filters.specializations
      : ["Python", "Backend", "QA"],
    excludeKeywordsText: d.filters.exclude_keywords.join("\n"),
    countriesText: d.filters.countries.join(", "),
    citiesText: d.filters.cities.join(", "),
    workFormats: d.filters.work_formats.length ? d.filters.work_formats : ["any"],
    vacancyScoreThreshold: d.parser.vacancy_score_threshold,
    vacancyMinSubstantive: d.parser.vacancy_min_substantive_score,
    telethonSession: d.telegram.telethon_session_file,
    useAi: d.ai.enabled,
    aiModel: d.ai.model,
    aiFallbacks: [...d.ai.fallback_models],
    ignoreLlmCache: false,
    channelSearchQuery: "python jobs telegram channel",
  };
}

export function buildRunBody(form: ParseFormState, defaults: ConfigDefaults): RunCreateBody {
  const chans = splitCsv(form.channelsText);
  const channels = chans.length ? chans : defaults.channels;

  const exLines = form.excludeKeywordsText
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

  const filters: Record<string, unknown> = {
    exclude_keywords: exLines.length ? exLines : defaults.filters.exclude_keywords,
    countries: splitCsv(form.countriesText),
    cities: splitCsv(form.citiesText),
    work_formats: form.workFormats.length ? form.workFormats : ["any"],
  };

  if (form.anySpecialization) {
    filters.specializations = [];
    filters.any_specialization = true;
  } else {
    filters.specializations = form.specializations;
    filters.any_specialization = false;
  }

  const ai: Record<string, unknown> = {
    enabled: form.useAi,
  };
  if (form.useAi) {
    if (form.aiModel) ai.model = form.aiModel;
    if (form.aiFallbacks.length) {
      ai.fallback_models = form.aiFallbacks.filter((m) => m !== form.aiModel);
    }
    if (form.ignoreLlmCache) ai.use_cache = false;
  }

  return {
    channels,
    search: {
      date_from: form.dateFrom || defaults.search.date_from,
      date_to: form.dateTo || todayIso(),
    },
    filters,
    parser: {
      vacancy_score_threshold: form.vacancyScoreThreshold,
      vacancy_min_substantive_score: form.vacancyMinSubstantive,
    },
    ai,
    telegram_session_file: form.telethonSession.trim() || undefined,
  };
}
