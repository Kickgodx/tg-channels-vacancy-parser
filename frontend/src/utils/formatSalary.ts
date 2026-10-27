/** Зарплата в тысячах рублей: 185000 → «185k»; нет значения — «—». */
export function formatThousands(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${Math.round(v / 1000).toLocaleString("ru-RU")}k`;
}
