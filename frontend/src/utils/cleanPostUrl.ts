/** Убирает служебные query HH (`tech_source=api` и т.п.) из ссылки «Открыть пост». */
const HH_TRACKING_PARAMS = new Set(["tech_source", "hhtmfrom", "hhtmfromlabel", "from"]);

export function cleanPostUrl(url: string): string {
  try {
    const parsed = new URL(url);
    for (const key of [...parsed.searchParams.keys()]) {
      if (HH_TRACKING_PARAMS.has(key.toLowerCase())) {
        parsed.searchParams.delete(key);
      }
    }
    return parsed.toString();
  } catch {
    return url;
  }
}
