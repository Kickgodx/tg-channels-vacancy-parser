import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, retryIfRetryable } from "../api/client";
import VacancyModal from "../components/VacancyModal";
import type { MarketMap, MarketMapCluster, Vacancy } from "../types/api";
import { formatThousands as k } from "../utils/formatSalary";

// Три цвета, проверенные на различимость для точечной диаграммы (dataviz, палитра по умолчанию):
// фон — нейтральный, выбранная ниша — синий, вакансии рядом с резюме — оранжевый.
const BASE = "#adb5bd";
const SELECTED = "#2a78d6";
const NEAR_RESUME = "#eb6834";
const INK = "#212529";
const HALO = "#ffffff";
const PAD = 24;
const HIT_PX = 8;

interface View {
  k: number;
  tx: number;
  ty: number;
}

interface Hover {
  index: number;
  left: number;
  top: number;
}

function fmtDelta(d: number | null): string {
  if (d == null) return "—";
  return `${d > 0 ? "▲ +" : d < 0 ? "▼ " : ""}${d.toFixed(1)} п.п.`;
}

function MapCanvas({
  map,
  selected,
  onSelect,
  onOpen,
}: {
  map: MarketMap;
  selected: number | null;
  onSelect: (cluster: number | null) => void;
  onOpen: (id: string) => void;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [width, setWidth] = useState(800);
  const [view, setView] = useState<View>({ k: 1, tx: 0, ty: 0 });
  const [hover, setHover] = useState<Hover | null>(null);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const height = Math.min(width, 640);
  const near = useMemo(() => new Set(map.resume?.nearest ?? []), [map.resume]);
  const labelById = useMemo(
    () => new Map(map.clusters.map((c) => [c.id, c.label])),
    [map.clusters],
  );

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // React вешает wheel пассивным — страница прокручивалась бы вместе с зумом карты.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const stop = (e: WheelEvent) => e.preventDefault();
    canvas.addEventListener("wheel", stop, { passive: false });
    return () => canvas.removeEventListener("wheel", stop);
  }, []);

  // Координаты карты 0–1 → пиксели холста с учётом зума и сдвига.
  const project = useCallback(
    (x: number, y: number): [number, number] => {
      const size = Math.min(width, height) - 2 * PAD;
      const ox = (width - size) / 2;
      const oy = (height - size) / 2;
      return [(ox + x * size) * view.k + view.tx, (oy + y * size) * view.k + view.ty];
    },
    [width, height, view],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const r = Math.min(4, 2.2 * Math.sqrt(view.k));
    const draw = (i: number, color: string, radius: number, alpha: number) => {
      const [px, py] = project(map.x[i], map.y[i]);
      if (px < -5 || py < -5 || px > width + 5 || py > height + 5) return;
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(px, py, radius, 0, Math.PI * 2);
      ctx.fill();
    };
    // Слои: фон → выбранная ниша → рядом с резюме, чтобы выделенное не тонуло под серым.
    for (let i = 0; i < map.ids.length; i++) {
      if (map.cluster[i] !== selected && !near.has(map.ids[i])) {
        draw(i, BASE, r, selected == null ? 0.6 : 0.3);
      }
    }
    if (selected != null) {
      for (let i = 0; i < map.ids.length; i++) {
        if (map.cluster[i] === selected && !near.has(map.ids[i])) draw(i, SELECTED, r + 0.6, 0.9);
      }
    }
    for (let i = 0; i < map.ids.length; i++) {
      if (near.has(map.ids[i])) draw(i, NEAR_RESUME, r + 1.4, 1);
    }
    ctx.globalAlpha = 1;
    // Подписи ниш — прямо на карте: ниша узнаётся по тексту, а не по цвету.
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    for (const c of map.clusters) {
      const [px, py] = project(c.x, c.y);
      ctx.font = `${c.id === selected ? "600 " : ""}12px system-ui, sans-serif`;
      ctx.lineWidth = 4;
      ctx.strokeStyle = HALO;
      ctx.strokeText(c.label, px, py);
      ctx.fillStyle = INK;
      ctx.fillText(c.label, px, py);
    }
    if (map.resume) {
      const [px, py] = project(map.resume.x, map.resume.y);
      ctx.fillStyle = INK;
      ctx.strokeStyle = HALO;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(px, py, 8, 0, Math.PI * 2);
      ctx.stroke();
      ctx.fill();
      ctx.font = "600 13px system-ui, sans-serif";
      ctx.lineWidth = 4;
      ctx.strokeText("Ваше резюме", px, py - 18);
      ctx.fillText("Ваше резюме", px, py - 18);
    }
  }, [map, width, height, project, selected, near, view.k]);

  const hitTest = (cx: number, cy: number): number | null => {
    let best: number | null = null;
    let bestD = HIT_PX * HIT_PX;
    for (let i = 0; i < map.ids.length; i++) {
      const [px, py] = project(map.x[i], map.y[i]);
      const d = (px - cx) ** 2 + (py - cy) ** 2;
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  };

  const local = (e: React.MouseEvent | React.WheelEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top] as const;
  };

  const onWheel = (e: React.WheelEvent) => {
    const [cx, cy] = local(e);
    const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
    setView((v) => {
      const k2 = Math.min(12, Math.max(1, v.k * factor));
      const f = k2 / v.k;
      return k2 === 1
        ? { k: 1, tx: 0, ty: 0 }
        : { k: k2, tx: cx - (cx - v.tx) * f, ty: cy - (cy - v.ty) * f };
    });
  };

  const onMouseMove = (e: React.MouseEvent) => {
    const [cx, cy] = local(e);
    if (drag.current) {
      const dx = cx - drag.current.x;
      const dy = cy - drag.current.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) drag.current.moved = true;
      drag.current.x = cx;
      drag.current.y = cy;
      setView((v) => ({ ...v, tx: v.tx + dx, ty: v.ty + dy }));
      setHover(null);
      return;
    }
    const i = hitTest(cx, cy);
    setHover(i == null ? null : { index: i, left: cx, top: cy });
  };

  const onMouseUp = (e: React.MouseEvent) => {
    const wasDrag = drag.current?.moved;
    drag.current = null;
    if (wasDrag) return;
    const [cx, cy] = local(e);
    const i = hitTest(cx, cy);
    if (i != null) onOpen(map.ids[i]);
    else onSelect(null);
  };

  return (
    <div ref={wrapRef} className="market-map-wrap">
      <canvas
        ref={canvasRef}
        style={{ width, height }}
        className="market-map-canvas"
        onWheel={onWheel}
        onMouseDown={(e) => {
          const [x, y] = local(e);
          drag.current = { x, y, moved: false };
        }}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={() => {
          drag.current = null;
          setHover(null);
        }}
        role="img"
        aria-label="Карта вакансий по смыслу; таблица ниш ниже"
      />
      {view.k !== 1 && (
        <button
          type="button"
          className="secondary market-map-reset"
          onClick={() => setView({ k: 1, tx: 0, ty: 0 })}
        >
          Сбросить масштаб
        </button>
      )}
      {hover && (
        <div
          className="bar-tooltip market-map-tooltip"
          style={{ left: Math.min(hover.left + 12, width - 240), top: hover.top + 12 }}
        >
          <div className="bar-tooltip-title">
            {map.role[hover.index] ?? "Вакансия"}
            {map.grade[hover.index] && `, ${map.grade[hover.index]}`}
          </div>
          {map.company[hover.index] && <div>{map.company[hover.index]}</div>}
          <div>
            {map.salary[hover.index] != null
              ? `${k(map.salary[hover.index])} ₽`
              : "Зарплата не указана"}
            {map.archived[hover.index] && <span className="muted"> · архив</span>}
          </div>
          <div className="muted">{labelById.get(map.cluster[hover.index])}</div>
          <div className="muted">Клик — открыть вакансию</div>
        </div>
      )}
    </div>
  );
}

function ClusterTable({
  clusters,
  selected,
  resumeCluster,
  onSelect,
}: {
  clusters: MarketMapCluster[];
  selected: number | null;
  resumeCluster: number | null;
  onSelect: (id: number | null) => void;
}) {
  const [sortBy, setSortBy] = useState<"size" | "delta" | "salary">("size");
  const rows = useMemo(() => {
    const key = (c: MarketMapCluster) =>
      sortBy === "size"
        ? c.size
        : sortBy === "delta"
          ? (c.delta_pp ?? -1e9)
          : (c.salary_median ?? -1);
    return [...clusters].sort((a, b) => key(b) - key(a));
  }, [clusters, sortBy]);
  const th = (key: typeof sortBy, label: string, title: string) => (
    <th
      title={title}
      className="sortable-th"
      aria-sort={sortBy === key ? "descending" : "none"}
      onClick={() => setSortBy(key)}
    >
      {label}
      {sortBy === key ? " ↓" : ""}
    </th>
  );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Ниша</th>
            {th("size", "Вакансий", "Вакансий на карте")}
            {th("salary", "Медиана, ₽", "Медиана рублёвой зарплаты (от 5 вакансий с зарплатой)")}
            {th("delta", "Рост", "Доля ниши за последние 30 дней против предыдущих 30, п.п.")}
            <th>Технологии</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr
              key={c.id}
              className={`clickable${c.id === selected ? " selected-row" : ""}`}
              onClick={() => onSelect(c.id === selected ? null : c.id)}
              aria-selected={c.id === selected}
            >
              <td>
                <strong>{c.label}</strong>
                {c.id === resumeCluster && <span className="ask-chip">ваше резюме</span>}
              </td>
              <td>{c.size}</td>
              <td>{c.salary_median != null ? k(c.salary_median) : "—"}</td>
              <td
                className={
                  c.delta_pp == null
                    ? undefined
                    : c.delta_pp > 0
                      ? "delta-up"
                      : c.delta_pp < 0
                        ? "delta-down"
                        : undefined
                }
              >
                {fmtDelta(c.delta_pp)}
              </td>
              <td className="muted">
                {c.technologies
                  .slice(0, 4)
                  .map((t) => `${t.name} ${Math.round(t.share * 100)}%`)
                  .join(", ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** «Карта рынка»: вакансии по смыслу (t-SNE по эмбеддингам), ниши и место резюме. */
export default function MarketMapPage() {
  const queryClient = useQueryClient();
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: retryIfRetryable });
  const resumesQ = useQuery({ queryKey: ["resumes"], queryFn: () => api.listResumes() });
  const [resumeId, setResumeId] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [opened, setOpened] = useState<Vacancy | null>(null);
  const mapQ = useQuery({
    queryKey: ["market-map", resumeId],
    queryFn: () => api.getMarketMap(resumeId),
    staleTime: 5 * 60_000,
  });
  const rebuildM = useMutation({
    mutationFn: () => api.rebuildMarketMap(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["market-map"] }),
  });
  const map = mapQ.data;
  const resumes = resumesQ.data?.items ?? [];

  return (
    <div className="card">
      <div className="ask-header">
        <h2>Карта рынка</h2>
        {meQ.data?.is_admin && (
          <button
            type="button"
            className="secondary"
            disabled={rebuildM.isPending}
            onClick={() => rebuildM.mutate()}
          >
            {rebuildM.isPending ? "Пересчёт…" : "Пересчитать"}
          </button>
        )}
      </div>
      <p className="caption">
        Каждая точка — вакансия; рядом стоят похожие по смыслу текста (t-SNE по эмбеддингам), оси
        смысла не имеют. Ниши выделены автоматически и подписаны самой частой ролью и технологиями,
        которых в них больше, чем по рынку. Рост ниши зависит и от набора каналов: новый канал
        поднимет свою нишу.
      </p>
      {rebuildM.isError && <p className="error">{(rebuildM.error as Error).message}</p>}
      {mapQ.error && <p className="error">{(mapQ.error as Error).message}</p>}
      {map && !map.available && (
        <p className="muted">
          Карта ещё не посчитана: её строит фоновая задача после индексации вакансий
          {meQ.data?.is_admin ? " — или кнопка «Пересчитать»." : "."}
        </p>
      )}
      {map?.available && (
        <>
          <div className="filters" style={{ alignItems: "center" }}>
            <label>
              Резюме на карте
              <select
                value={resumeId ?? ""}
                onChange={(e) => setResumeId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">— не показывать —</option>
                {resumes.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <span className="caption">
              {map.points} вакансий за {Math.round((map.window_days ?? 365) / 30)} мес., посчитано{" "}
              {map.created_at ? new Date(map.created_at).toLocaleString("ru-RU") : "—"}
              {mapQ.isFetching && " · обновление…"}
            </span>
          </div>
          {map.resume_error && <p className="muted">{map.resume_error}</p>}
          <div className="market-map-legend caption">
            <span>
              <i style={{ background: BASE }} /> вакансия
            </span>
            <span>
              <i style={{ background: SELECTED }} /> выбранная ниша
            </span>
            {map.resume && (
              <span>
                <i style={{ background: NEAR_RESUME }} /> ближайшие к резюме (
                {map.resume.nearest.length})
              </span>
            )}
            <span className="muted">Колесо — масштаб, перетаскивание — сдвиг</span>
          </div>
          <MapCanvas
            map={map}
            selected={selected}
            onSelect={setSelected}
            onOpen={(id) => void api.getVacancy(id).then(setOpened)}
          />
          <h3>Ниши</h3>
          <ClusterTable
            clusters={map.clusters}
            selected={selected}
            resumeCluster={map.resume?.cluster ?? null}
            onSelect={setSelected}
          />
        </>
      )}
      {opened && (
        <VacancyModal vacancy={opened} resumeId={resumeId} onClose={() => setOpened(null)} />
      )}
    </div>
  );
}
