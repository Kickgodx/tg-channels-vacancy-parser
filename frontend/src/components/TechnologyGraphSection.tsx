import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type VacancyQueryParams } from "../api/client";
import type { TechnologyGraph, TechnologyGraphEdge, TechnologyGraphNode } from "../types/api";

interface Props {
  params: VacancyQueryParams;
}

// Категориальная палитра (как у линий спроса); роли сверх неё — серым.
const ROLE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const OTHER_COLOR = "#adb5bd";
const W = 860;
const H = 540;
// Рёбер на узел: самые сильные по lift; больше пяти — граф превращается в клубок.
const EDGES_PER_NODE_OPTIONS = [3, 4, 5];

interface Point {
  x: number;
  y: number;
}

/** Рёбра для рисования: у каждого узла — до perNode самых сильных с lift не ниже порога. */
function visibleEdges(edges: TechnologyGraphEdge[], minLift: number, perNode: number): TechnologyGraphEdge[] {
  const used = new Map<string, number>();
  const out: TechnologyGraphEdge[] = [];
  for (const e of edges) {
    if (e.lift < minLift) continue;
    const a = used.get(e.source) ?? 0;
    const b = used.get(e.target) ?? 0;
    if (a >= perNode && b >= perNode) continue;
    used.set(e.source, a + 1);
    used.set(e.target, b + 1);
    out.push(e);
  }
  return out;
}

/**
 * Силовой расклад (Фрухтерман — Рейнгольд): отталкивание узлов, пружины по рёбрам (сильнее
 * связь — сильнее тянет), притяжение к центру. Считается в свободном пространстве и вписывается
 * в рамку; узлы без видимых связей — рядом внизу, иначе они растягивают рамку.
 */
function layout(
  nodes: TechnologyGraphNode[],
  edges: TechnologyGraphEdge[],
  radius: (count: number) => number,
): Map<string, Point> {
  const linked = new Set(edges.flatMap((e) => [e.source, e.target]));
  const main = nodes.filter((nd) => linked.has(nd.name));
  const lone = nodes.filter((nd) => !linked.has(nd.name));
  const n = main.length;
  const pos = main.map((_, i) => {
    const a = (2 * Math.PI * i) / Math.max(n, 1);
    return { x: W / 2 + (W / 3) * Math.cos(a), y: H / 2 + (H / 3) * Math.sin(a) };
  });
  const index = new Map(main.map((nd, i) => [nd.name, i]));
  const springs = edges
    .map((e) => ({ a: index.get(e.source), b: index.get(e.target), w: Math.log2(1 + e.lift) }))
    .filter((s): s is { a: number; b: number; w: number } => s.a != null && s.b != null);
  const k = Math.sqrt((W * H) / Math.max(n, 1)) * 0.8;
  for (let iter = 0, temp = W / 6; iter < 500; iter++, temp *= 0.99) {
    const disp = pos.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i].x - pos[j].x;
        const dy = pos[i].y - pos[j].y;
        const d2 = Math.max(dx * dx + dy * dy, 1);
        const f = (k * k) / d2;
        disp[i].x += dx * f;
        disp[i].y += dy * f;
        disp[j].x -= dx * f;
        disp[j].y -= dy * f;
      }
    }
    for (const s of springs) {
      const dx = pos[s.a].x - pos[s.b].x;
      const dy = pos[s.a].y - pos[s.b].y;
      const d = Math.max(Math.hypot(dx, dy), 1);
      const f = ((d * d) / k) * s.w * 0.3;
      disp[s.a].x -= (dx / d) * f;
      disp[s.a].y -= (dy / d) * f;
      disp[s.b].x += (dx / d) * f;
      disp[s.b].y += (dy / d) * f;
    }
    for (let i = 0; i < n; i++) {
      disp[i].x += (W / 2 - pos[i].x) * 0.05;
      disp[i].y += (H / 2 - pos[i].y) * 0.05;
      const len = Math.max(Math.hypot(disp[i].x, disp[i].y), 1e-6);
      const step = Math.min(len, temp);
      pos[i].x += (disp[i].x / len) * step;
      pos[i].y += (disp[i].y / len) * step;
    }
  }
  // Вписать в рамку с полями под подписи; под узлы без связей — нижняя полоса.
  const bottom = lone.length > 0 ? H - 60 : H;
  const xs = pos.map((p) => p.x);
  const ys = pos.map((p) => p.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const fit = (v: number, lo: number, hi: number, size: number, pad: number) =>
    hi - lo < 1 ? size / 2 : pad + ((v - lo) / (hi - lo)) * (size - 2 * pad);
  const placed = main.map((nd, i) => ({
    name: nd.name,
    r: radius(nd.count),
    x: fit(pos[i].x, x0, x1, W, 60),
    y: fit(pos[i].y, y0, y1, bottom, 30),
  }));
  // Раздвинуть налезающие узлы (с запасом под подпись снизу).
  for (let iter = 0; iter < 60; iter++) {
    let moved = false;
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i];
        const b = placed[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d = Math.max(Math.hypot(dx, dy), 0.01);
        const need = a.r + b.r + 16;
        if (d >= need) continue;
        const push = (need - d) / 2;
        const ux = d > 0.01 ? dx / d : 1;
        const uy = d > 0.01 ? dy / d : 0;
        a.x -= ux * push;
        a.y -= uy * push;
        b.x += ux * push;
        b.y += uy * push;
        moved = true;
      }
    }
    for (const p of placed) {
      p.x = Math.min(W - 40, Math.max(40, p.x));
      p.y = Math.min(bottom - 20, Math.max(20, p.y));
    }
    if (!moved) break;
  }
  const out = new Map<string, Point>(placed.map((p) => [p.name, { x: p.x, y: p.y }]));
  lone.forEach((nd, i) => out.set(nd.name, { x: ((i + 1) * W) / (lone.length + 1), y: H - 30 }));
  return out;
}

function nodeRadius(count: number, maxCount: number): number {
  return 5 + 15 * Math.sqrt(count / maxCount);
}

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** С какими технологиями просят выбранную: доля её вакансий с напарником и lift. */
function companions(graph: TechnologyGraph, name: string) {
  const own = graph.nodes.find((nd) => nd.name === name);
  if (!own || own.count === 0) return [];
  return graph.edges
    .filter((e) => e.source === name || e.target === name)
    .map((e) => ({ name: e.source === name ? e.target : e.source, count: e.count, lift: e.lift, share: e.count / own.count }))
    .filter((c) => c.lift > 1)
    .sort((a, b) => b.share - a.share || b.lift - a.lift)
    .slice(0, 8);
}

/** Граф «какие технологии просят вместе»: узлы — частые технологии, рёбра — пары с lift. */
export default function TechnologyGraphSection({ params }: Props) {
  const [nodes, setNodes] = useState(40);
  const [minLift, setMinLift] = useState(1.5);
  const [perNode, setPerNode] = useState(5);
  const [selected, setSelected] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["technology-graph", params, nodes],
    queryFn: () => api.getTechnologyGraph(params, nodes),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
  const graph = q.data;
  const edges = useMemo(
    () => (graph ? visibleEdges(graph.edges, minLift, perNode) : []),
    [graph, minLift, perNode],
  );
  const pos = useMemo(() => {
    if (!graph) return new Map<string, Point>();
    const maxCount = Math.max(...graph.nodes.map((nd) => nd.count), 1);
    return layout(graph.nodes, edges, (c) => nodeRadius(c, maxCount));
  }, [graph, edges]);
  const roleColor = useMemo(() => {
    const counts = new Map<string, number>();
    for (const nd of graph?.nodes ?? []) if (nd.role) counts.set(nd.role, (counts.get(nd.role) ?? 0) + 1);
    const roles = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([r]) => r);
    return new Map(roles.slice(0, ROLE_COLORS.length).map((r, i) => [r, ROLE_COLORS[i]]));
  }, [graph]);

  if (!graph || graph.nodes.length < 2) {
    return null;
  }
  const maxCount = Math.max(...graph.nodes.map((nd) => nd.count), 1);
  const radius = (c: number) => nodeRadius(c, maxCount);
  const active = selected && graph.nodes.some((nd) => nd.name === selected) ? selected : null;
  const neighbours = new Set<string>();
  if (active) {
    for (const e of edges) {
      if (e.source === active) neighbours.add(e.target);
      if (e.target === active) neighbours.add(e.source);
    }
  }
  const dim = (name: string) => active != null && name !== active && !neighbours.has(name);
  const maxLift = Math.max(...edges.map((e) => e.lift), 2);
  const list = active ? companions(graph, active) : [];
  const activeNode = graph.nodes.find((nd) => nd.name === active);

  return (
    <div className="card chart-card chart-wide">
      <h4>Какие технологии просят вместе</h4>
      <p className="caption">
        {graph.nodes.length} самых частых технологий из {graph.total.toLocaleString("ru-RU")} вакансий под
        фильтрами. Линия — пару просят в одной вакансии заметно чаще, чем случайно (lift); у каждой технологии —
        до {perNode} самых сильных связей. Размер — число вакансий, цвет — роль, с которой технология
        встречается чаще всего. Клик по технологии — с чем её просят.
      </p>
      <div className="tech-graph-controls">
        <label>
          Технологий{" "}
          <select value={nodes} onChange={(e) => setNodes(Number(e.target.value))}>
            {[20, 30, 40, 60, 80].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          Связь сильнее{" "}
          <select value={minLift} onChange={(e) => setMinLift(Number(e.target.value))}>
            {[1.2, 1.5, 2, 3, 5].map((v) => (
              <option key={v} value={v}>
                ×{v}
              </option>
            ))}
          </select>
        </label>
        <label>
          Связей у технологии{" "}
          <select value={perNode} onChange={(e) => setPerNode(Number(e.target.value))}>
            {EDGES_PER_NODE_OPTIONS.map((v) => (
              <option key={v} value={v}>
                до {v}
              </option>
            ))}
          </select>
        </label>
        <span className="tech-graph-legend">
          {[...roleColor.entries()].map(([role, color]) => (
            <span key={role}>
              <i style={{ background: color }} /> {role}
            </span>
          ))}
        </span>
      </div>
      <div className="tech-graph">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="Граф технологий, которые просят вместе"
          onClick={() => setSelected(null)}
        >
          {edges.map((e) => {
            const a = pos.get(e.source);
            const b = pos.get(e.target);
            if (!a || !b) return null;
            const on = active != null && (e.source === active || e.target === active);
            return (
              <line
                key={`${e.source}|${e.target}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={on ? "tech-graph-edge on" : "tech-graph-edge"}
                strokeWidth={1 + 3 * (Math.log(e.lift) / Math.log(maxLift))}
                opacity={active == null ? 0.45 : on ? 0.9 : 0.08}
              >
                <title>
                  {e.source} + {e.target}: {e.count} вакансий, ×{e.lift.toFixed(1)}
                </title>
              </line>
            );
          })}
          {graph.nodes.map((nd) => {
            const p = pos.get(nd.name);
            if (!p) return null;
            const r = radius(nd.count);
            return (
              <g
                key={nd.name}
                data-tech={nd.name}
                className="tech-graph-node"
                opacity={dim(nd.name) ? 0.25 : 1}
                onClick={(ev) => {
                  ev.stopPropagation();
                  setSelected(nd.name === active ? null : nd.name);
                }}
              >
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={r}
                  fill={(nd.role && roleColor.get(nd.role)) || OTHER_COLOR}
                  stroke={nd.name === active ? "#1a1a2e" : "#fff"}
                  strokeWidth={nd.name === active ? 2.5 : 1.5}
                />
                <text x={p.x} y={p.y + r + 12} textAnchor="middle">
                  {nd.name}
                </text>
                <title>
                  {nd.name}: {nd.count} вакансий ({pct(nd.share)}){nd.role ? `, чаще с ролью ${nd.role}` : ""}
                </title>
              </g>
            );
          })}
        </svg>
        {activeNode && (
          <aside className="tech-graph-side">
            <strong>{activeNode.name}</strong>
            <p className="caption">
              {activeNode.count} вакансий ({pct(activeNode.share)})
              {activeNode.role ? `, ${pct(activeNode.role_share)} из них — ${activeNode.role}` : ""}
            </p>
            {list.length === 0 ? (
              <p className="caption">Устойчивых пар нет.</p>
            ) : (
              <>
                <p className="caption">
                  Вместе с ней чаще всего просят (доля её вакансий; ×N — во сколько раз чаще случайного, линии
                  на графе — самые сильные из таких пар):
                </p>
                <ul>
                  {list.map((c) => (
                    <li key={c.name}>
                      <button type="button" className="link-button" onClick={() => setSelected(c.name)}>
                        {c.name}
                      </button>{" "}
                      — {pct(c.share)} вакансий{" "}
                      <span className="muted" title="Во сколько раз чаще, чем случайно">
                        ×{c.lift.toFixed(1)}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
