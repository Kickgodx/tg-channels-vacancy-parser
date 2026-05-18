/** Отделяет реальные предупреждения от служебного текста прогона (логи, AI-конфиг, токены). */

const INFO_PATTERNS: RegExp[] = [
  /^Telegram:\s*Telethon/i,
  /^AI:\s*OpenRouter,/i,
  /^Токены LLM за прогон:/i,
  /^Оценка стоимости LLM/i,
];

export function splitRunMessages(raw: string | null | undefined): string[] {
  if (!raw?.trim()) return [];
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function isInformationalRunMessage(msg: string): boolean {
  return INFO_PATTERNS.some((re) => re.test(msg));
}

export function filterActionableWarnings(raw: string | null | undefined): string[] {
  return splitRunMessages(raw).filter((m) => !isInformationalRunMessage(m));
}
