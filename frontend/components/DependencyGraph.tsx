"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDownIcon, ArrowUpIcon } from "@phosphor-icons/react";
import { api, type GraphData, type GraphEdge, type GraphNode } from "@/lib/api";

// Dependency graph (§22). Dependency-free SVG so it stays React 19-safe and
// keeps the build's tsc/lint green. Each API is a node; each proven
// producer→consumer correlation is a directed edge labelled with its variable
// and confidence. Layout is layered by dependency depth (edges only ever point
// forward, matching the engine invariant), so the flow reads left→right.

const NODE_W = 200;
const NODE_H = 64;
const COL_W = 260;
const ROW_H = 90;

type Sel = { type: "node"; id: string } | { type: "edge"; id: string } | null;

interface Pos {
  x: number;
  y: number;
}

const CONF_STROKE: Record<string, string> = {
  high: "rgb(var(--graph-edge-high))",
  medium: "rgb(var(--graph-edge-medium))",
  low: "rgb(var(--graph-edge-low))",
};
const ROLE_ACCENT: Record<string, string> = {
  producer: "rgb(var(--graph-produce))",
  consumer: "rgb(var(--graph-consume))",
  both: "rgb(var(--graph-produce))",
  none: "rgb(var(--graph-node-border))",
};
// Marker defs keyed by role name instead of hex (colours are now CSS vars).
const MARKERS: [string, string][] = [
  ["high", CONF_STROKE.high],
  ["medium", CONF_STROKE.medium],
  ["low", CONF_STROKE.low],
  ["produce", ROLE_ACCENT.producer],
];

export function DependencyGraph({ analysisId }: { analysisId: string }) {
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);

  // view transform
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [scale, setScale] = useState(1);

  // filters
  const [conf, setConf] = useState<Set<string>>(new Set(["high", "medium", "low"]));
  const [variable, setVariable] = useState("all");
  const [location, setLocation] = useState("all");
  const [folders, setFolders] = useState<Set<string>>(new Set());
  const [showRejected, setShowRejected] = useState(false);
  const [showUnlinked, setShowUnlinked] = useState(true);
  const [aggregate, setAggregate] = useState(false);
  const [search, setSearch] = useState("");

  const [selected, setSelected] = useState<Sel>(null);
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const pan = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    api
      .getGraph(analysisId)
      .then((g) => {
        if (!alive) return;
        setData(g);
        setFolders(new Set(g.facets.folders));
        setConf(new Set(g.facets.confidence_levels.length ? g.facets.confidence_levels : ["high", "medium", "low"]));
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [analysisId]);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    data?.nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [data]);

  // Edges kept after applying every filter (confidence, variable, location,
  // folder, rejected). Variable filter doubles as "focus one variable".
  const activeEdges = useMemo(() => {
    if (!data) return [];
    return data.edges.filter((e) => {
      if (!conf.has(e.confidence)) return false;
      if (variable !== "all" && e.variable !== variable) return false;
      if (location !== "all" && e.consumer_location !== location) return false;
      if (!showRejected && e.state === "rejected") return false;
      const s = nodeById.get(e.source);
      const t = nodeById.get(e.target);
      if (s && !folders.has(s.folder)) return false;
      if (t && !folders.has(t.folder)) return false;
      return true;
    });
  }, [data, conf, variable, location, showRejected, folders, nodeById]);

  const linkedIds = useMemo(() => {
    const s = new Set<string>();
    activeEdges.forEach((e) => {
      s.add(e.source);
      s.add(e.target);
    });
    return s;
  }, [activeEdges]);

  // Up/down closure of a node over the active edges (for focus + highlight).
  const closure = useCallback(
    (id: string) => {
      const up = new Set<string>();
      const down = new Set<string>();
      const inc = new Map<string, string[]>();
      const out = new Map<string, string[]>();
      activeEdges.forEach((e) => {
        (out.get(e.source) ?? out.set(e.source, []).get(e.source)!).push(e.target);
        (inc.get(e.target) ?? inc.set(e.target, []).get(e.target)!).push(e.source);
      });
      const walk = (start: string, adj: Map<string, string[]>, acc: Set<string>) => {
        const stack = [...(adj.get(start) ?? [])];
        while (stack.length) {
          const n = stack.pop()!;
          if (acc.has(n)) continue;
          acc.add(n);
          (adj.get(n) ?? []).forEach((x) => stack.push(x));
        }
      };
      walk(id, out, down);
      walk(id, inc, up);
      return { up, down, all: new Set<string>([id, ...up, ...down]) };
    },
    [activeEdges],
  );

  const visibleNodes = useMemo(() => {
    if (!data) return [];
    let ns = data.nodes.filter((n) => folders.has(n.folder));
    if (variable !== "all") {
      ns = ns.filter((n) => linkedIds.has(n.id));
    } else if (focusNodeId) {
      const c = closure(focusNodeId);
      ns = ns.filter((n) => c.all.has(n.id));
    } else if (!showUnlinked) {
      ns = ns.filter((n) => linkedIds.has(n.id));
    }
    return ns;
  }, [data, folders, variable, focusNodeId, showUnlinked, linkedIds, closure]);

  const visibleIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);

  const drawEdges = useMemo(() => {
    const es = activeEdges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target));
    if (!aggregate) return es.map((e) => ({ ...e, group: [e] as GraphEdge[] }));
    const byPair = new Map<string, GraphEdge[]>();
    es.forEach((e) => {
      const k = `${e.source}->${e.target}`;
      (byPair.get(k) ?? byPair.set(k, []).get(k)!).push(e);
    });
    return [...byPair.values()].map((group) => ({ ...group[0], group }));
  }, [activeEdges, visibleIds, aggregate]);

  // Layered layout: unlinked nodes in column 0, then dependency depth.
  const layout = useMemo(() => {
    const pos = new Map<string, Pos>();
    if (!visibleNodes.length) return { pos, width: 400, height: 300 };
    const incoming = new Map<string, string[]>();
    activeEdges.forEach((e) => {
      if (visibleIds.has(e.source) && visibleIds.has(e.target)) {
        (incoming.get(e.target) ?? incoming.set(e.target, []).get(e.target)!).push(e.source);
      }
    });
    const linked = visibleNodes.filter((n) => linkedIds.has(n.id));
    const unlinked = visibleNodes.filter((n) => !linkedIds.has(n.id));
    const base = unlinked.length ? 1 : 0;
    const depth = new Map<string, number>();
    [...linked]
      .sort((a, b) => a.index - b.index)
      .forEach((n) => {
        const preds = (incoming.get(n.id) ?? []).filter((p) => depth.has(p));
        depth.set(n.id, preds.length ? Math.max(...preds.map((p) => depth.get(p)! + 1)) : 0);
      });
    const cols = new Map<number, GraphNode[]>();
    unlinked.forEach((n) => (cols.get(0) ?? cols.set(0, []).get(0)!).push(n));
    linked.forEach((n) => {
      const c = base + (depth.get(n.id) ?? 0);
      (cols.get(c) ?? cols.set(c, []).get(c)!).push(n);
    });
    let maxRows = 0;
    let maxCol = 0;
    cols.forEach((arr, col) => {
      arr.sort((a, b) => a.index - b.index);
      arr.forEach((n, row) => pos.set(n.id, { x: 40 + col * COL_W, y: 40 + row * ROW_H }));
      maxRows = Math.max(maxRows, arr.length);
      maxCol = Math.max(maxCol, col);
    });
    return { pos, width: 80 + (maxCol + 1) * COL_W, height: 80 + Math.max(1, maxRows) * ROW_H };
  }, [visibleNodes, activeEdges, visibleIds, linkedIds]);

  const fit = useCallback(() => {
    const wrap = wrapRef.current;
    if (!wrap || !visibleNodes.length) return;
    const cw = wrap.clientWidth;
    const ch = wrap.clientHeight;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    visibleNodes.forEach((n) => {
      const p = layout.pos.get(n.id);
      if (!p) return;
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + NODE_W);
      maxY = Math.max(maxY, p.y + NODE_H);
    });
    if (!isFinite(minX)) return;
    const gw = maxX - minX + 80;
    const gh = maxY - minY + 80;
    const s = Math.max(0.05, Math.min(1.3, Math.min(cw / gw, ch / gh)));
    setScale(s);
    setTx((cw - gw * s) / 2 - (minX - 40) * s);
    setTy((ch - gh * s) / 2 - (minY - 40) * s);
  }, [visibleNodes, layout]);

  // auto-fit when the visible layout changes
  const layoutKey = `${visibleNodes.length}:${layout.width}:${layout.height}:${drawEdges.length}`;
  useEffect(() => {
    const t = setTimeout(fit, 0);
    const observer = new ResizeObserver(() => fit());
    if (wrapRef.current) observer.observe(wrapRef.current);
    return () => { clearTimeout(t); observer.disconnect(); };
  }, [layoutKey, fit]);

  // highlight sets from selection / search
  const searchTerm = search.trim().toLowerCase();
  const highlight = useMemo(() => {
    if (selected?.type === "node") return closure(selected.id).all;
    if (searchTerm) {
      const s = new Set<string>();
      visibleNodes.forEach((n) => {
        if (
          n.name.toLowerCase().includes(searchTerm) ||
          n.path.toLowerCase().includes(searchTerm) ||
          n.method.toLowerCase().includes(searchTerm)
        )
          s.add(n.id);
      });
      return s;
    }
    return null;
  }, [selected, searchTerm, visibleNodes, closure]);

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const rect = svgRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const ns = Math.max(0.15, Math.min(2.5, scale * factor));
    setTx(px - (px - tx) * (ns / scale));
    setTy(py - (py - ty) * (ns / scale));
    setScale(ns);
  };
  const onMouseDown = (e: React.MouseEvent) => {
    pan.current = { x: e.clientX, y: e.clientY, tx, ty };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (!pan.current) return;
    setTx(pan.current.tx + (e.clientX - pan.current.x));
    setTy(pan.current.ty + (e.clientY - pan.current.y));
  };
  const endPan = () => (pan.current = null);
  const zoomBtn = (f: number) => {
    const wrap = wrapRef.current!;
    const px = wrap.clientWidth / 2;
    const py = wrap.clientHeight / 2;
    const ns = Math.max(0.15, Math.min(2.5, scale * f));
    setTx(px - (px - tx) * (ns / scale));
    setTy(py - (py - ty) * (ns / scale));
    setScale(ns);
  };

  const toggleSet = (set: Set<string>, v: string, apply: (s: Set<string>) => void) => {
    const n = new Set(set);
    n.has(v) ? n.delete(v) : n.add(v);
    apply(n);
  };

  const resetView = () => {
    setSelected(null);
    setFocusNodeId(null);
    setSearch("");
    setVariable("all");
    setLocation("all");
    setShowRejected(false);
    setShowUnlinked(true);
    if (data) {
      setFolders(new Set(data.facets.folders));
      setConf(new Set(data.facets.confidence_levels.length ? data.facets.confidence_levels : ["high", "medium", "low"]));
    }
  };

  if (error) return <div className="card p-4 text-sm text-danger">Failed to load graph: {error}</div>;
  if (!data) return <div className="card p-6 text-sm text-fg-muted">Building dependency graph...</div>;

  const selEdge =
    selected?.type === "edge" ? drawEdges.find((e) => e.id === selected.id) : null;
  const selNode =
    selected?.type === "node" ? nodeById.get(selected.id) ?? null : null;

  return (
    <div className="space-y-3">
      {!data.edges.length && (
        <p className="card p-4 text-sm text-fg">
          Showing all {data.stats.apis} APIs. No dependency rules are available yet.
          Use Auto-correlate in Candidates, or add a reviewed rule, to connect them.
        </p>
      )}
      {/* toolbar */}
      <div className="card flex flex-wrap items-center gap-2 p-3 text-xs">
        <input
          className="input w-44 px-2 py-1 text-xs"
          placeholder="Search API..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setSelected(null);
          }}
        />
        <label className="flex items-center gap-1">
          <span className="text-fg-muted">Variable</span>
          <select
            className="input px-2 py-1 text-xs"
            value={variable}
            onChange={(e) => {
              setVariable(e.target.value);
              setFocusNodeId(null);
              setSelected(null);
            }}
          >
            <option value="all">all ({data.facets.variables.length})</option>
            {data.facets.variables.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="text-fg-muted">Location</span>
          <select
            className="input px-2 py-1 text-xs"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          >
            <option value="all">all</option>
            {data.facets.locations.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-center gap-1">
          <span className="text-fg-muted">Confidence</span>
          {["high", "medium", "low"].map((c) => (
            <button
              key={c}
              onClick={() => toggleSet(conf, c, setConf)}
              className={`badge ${
                conf.has(c)
                  ? c === "high"
                    ? "badge-high"
                    : c === "medium"
                      ? "badge-medium"
                      : "badge-low"
                  : "badge-low opacity-40"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={aggregate} onChange={(e) => setAggregate(e.target.checked)} />
          <span className="text-fg-muted">Aggregate edges</span>
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showUnlinked} onChange={(e) => setShowUnlinked(e.target.checked)} />
          <span className="text-fg-muted">Show unlinked</span>
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showRejected} onChange={(e) => setShowRejected(e.target.checked)} />
          <span className="text-fg-muted">Show rejected</span>
        </label>
        <div className="ml-auto flex items-center gap-1">
          <button className="btn-ghost px-2 py-1" onClick={() => zoomBtn(1 / 1.2)}>
            −
          </button>
          <button className="btn-ghost px-2 py-1" onClick={() => zoomBtn(1.2)}>
            +
          </button>
          <button className="btn-ghost px-2 py-1" onClick={fit}>
            Fit
          </button>
          <button className="btn-ghost px-2 py-1" onClick={resetView}>
            Reset
          </button>
        </div>
      </div>

      {/* folder filter */}
      {data.facets.folders.length > 1 && (
        <div className="card flex flex-wrap items-center gap-2 p-2 text-xs">
          <span className="text-fg-muted">Folders:</span>
          {data.facets.folders.map((f) => (
            <button
              key={f}
              onClick={() => toggleSet(folders, f, setFolders)}
              className={`badge ${folders.has(f) ? "badge-medium" : "badge-low opacity-40"}`}
            >
              {f}
            </button>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-3 lg:flex-row">
        {/* canvas */}
        <div
          ref={wrapRef}
          className="card relative h-[560px] w-full min-w-0 shrink-0 overflow-hidden lg:flex-1"
          style={{ cursor: pan.current ? "grabbing" : "grab" }}
        >
          <div className="pointer-events-none absolute left-2 top-2 z-10 rounded-[10px] bg-surface2/80 px-2 py-1 text-[11px] text-fg">
            {data.stats.apis} APIs · {data.stats.producers} producers · {data.stats.consumers} consumers ·{" "}
            {data.stats.variables} variables · {drawEdges.length}/{data.stats.edges} edges shown
          </div>
          {(focusNodeId || variable !== "all" || selected) && (
            <button
              className="absolute right-2 top-2 z-10 rounded-[10px] bg-surface2/80 px-2 py-1 text-[11px] text-accent-soft-ink hover:underline"
              onClick={() => {
                setFocusNodeId(null);
                setSelected(null);
              }}
            >
              Clear focus
            </button>
          )}
          <svg
            ref={svgRef}
            className="h-full w-full"
            onWheel={onWheel}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={endPan}
            onMouseLeave={endPan}
          >
            <defs>
              {MARKERS.map(([key, c]) => (
                <marker
                  key={key}
                  id={`arrow-${key}`}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M0,0 L10,5 L0,10 z" style={{ fill: c }} />
                </marker>
              ))}
            </defs>
            <g transform={`translate(${tx},${ty}) scale(${scale})`}>
              {/* edges */}
              {drawEdges.map((e) => {
                const s = layout.pos.get(e.source);
                const t = layout.pos.get(e.target);
                if (!s || !t) return null;
                const x1 = s.x + NODE_W;
                const y1 = s.y + NODE_H / 2;
                const x2 = t.x;
                const y2 = t.y + NODE_H / 2;
                const mx = (x1 + x2) / 2;
                const confKey = e.confidence in CONF_STROKE ? e.confidence : "low";
                const col = CONF_STROKE[confKey];
                const dim = highlight ? !(highlight.has(e.source) && highlight.has(e.target)) : false;
                const isSel = selEdge?.id === e.id;
                const label = aggregate && e.group.length > 1 ? `${e.group.length} vars` : e.variable;
                return (
                  <g key={e.id} opacity={dim ? 0.12 : 1} style={{ cursor: "pointer" }}
                     onMouseDown={(ev) => ev.stopPropagation()}
                     onClick={() => setSelected({ type: "edge", id: e.id })}>
                    <path
                      d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
                      fill="none"
                      style={{ stroke: col }}
                      strokeWidth={isSel ? 3.5 : 2}
                      strokeDasharray={e.state === "suggested" || e.state === "rejected" ? "6 4" : undefined}
                      markerEnd={`url(#arrow-${confKey})`}
                    />
                    <rect x={mx - label.length * 3.4 - 6} y={(y1 + y2) / 2 - 9} rx={3}
                      width={label.length * 6.8 + 12} height={16}
                      style={{ fill: "rgb(var(--bg))", stroke: col }} strokeOpacity={0.5} />
                    <text x={mx} y={(y1 + y2) / 2 + 3} textAnchor="middle" fontSize={11}
                      style={{ fill: "rgb(var(--graph-text))" }} className="mono">
                      {label}
                    </text>
                  </g>
                );
              })}
              {/* nodes */}
              {visibleNodes.map((n) => {
                const p = layout.pos.get(n.id);
                if (!p) return null;
                const dim = highlight ? !highlight.has(n.id) : false;
                const isSel = selNode?.id === n.id;
                const accent = ROLE_ACCENT[n.role];
                return (
                  <g
                    key={n.id}
                    transform={`translate(${p.x},${p.y})`}
                    opacity={dim ? 0.2 : 1}
                    style={{ cursor: "pointer" }}
                    onMouseDown={(ev) => ev.stopPropagation()}
                    onClick={() => setSelected({ type: "node", id: n.id })}
                  >
                    <rect
                      width={NODE_W}
                      height={NODE_H}
                      rx={8}
                      style={{ fill: "rgb(var(--graph-node))", stroke: isSel ? "rgb(var(--graph-produce))" : accent }}
                      strokeWidth={isSel ? 3 : 1.5}
                    />
                    <rect width={5} height={NODE_H} rx={2} style={{ fill: accent }} />
                    <text x={14} y={20} fontSize={11} style={{ fill: "rgb(var(--graph-edge-low))" }} className="mono">
                      #{n.index + 1} {n.method}
                    </text>
                    <text x={14} y={38} fontSize={13} style={{ fill: "rgb(var(--graph-text))" }} fontWeight={600}>
                      {n.name.length > 24 ? n.name.slice(0, 23) + "..." : n.name}
                    </text>
                    <text x={14} y={54} fontSize={10} style={{ fill: "rgb(var(--graph-edge-low))" }} className="mono">
                      {n.path.length > 28 ? n.path.slice(0, 27) + "..." : n.path}
                    </text>
                    {n.produces.length > 0 && (
                      <text x={NODE_W - 10} y={20} textAnchor="end" fontSize={10} style={{ fill: "rgb(var(--graph-produce))" }}>
                        ▲{n.produces.length}
                      </text>
                    )}
                    {n.consumes.length > 0 && (
                      <text x={NODE_W - 10} y={34} textAnchor="end" fontSize={10} style={{ fill: "rgb(var(--graph-consume))" }}>
                        ▼{n.consumes.length}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>

          {/* legend */}
          <div className="pointer-events-none absolute bottom-2 left-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[10px] bg-surface2/80 px-2 py-1 text-[10px] text-fg">
            <span className="inline-flex items-center gap-1">
              <ArrowUpIcon size={14} color={ROLE_ACCENT.producer} aria-hidden /> producer
            </span>
            <span className="inline-flex items-center gap-1">
              <ArrowDownIcon size={14} color={ROLE_ACCENT.consumer} aria-hidden /> consumer
            </span>
            <span><span style={{ color: ROLE_ACCENT.both }}>◆</span> both</span>
            <span><span style={{ color: CONF_STROKE.high }}>━</span> high</span>
            <span><span style={{ color: CONF_STROKE.medium }}>━</span> medium</span>
            <span><span style={{ color: CONF_STROKE.low }}>┅</span> suggested</span>
          </div>
        </div>

        {/* detail panel */}
        {selected && <div className="card w-full shrink-0 overflow-auto p-3 text-xs lg:w-72">
          {!selected && (
            <p className="text-fg-muted">
              Click a node to inspect an API and highlight its upstream/downstream chain. Click an edge
              to see the correlation. Drag to pan, scroll to zoom.
            </p>
          )}
          {selNode && (
            <NodeDetail
              node={selNode}
              onFocus={() => {
                setFocusNodeId(selNode.id);
                setVariable("all");
              }}
            />
          )}
          {selEdge && <EdgeDetail edge={selEdge} nodeById={nodeById} onFocusVar={() => setVariable(selEdge.variable)} />}
        </div>}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2 border-b border-line/40 py-1">
      <span className="text-fg-muted">{k}</span>
      <span className="mono text-right text-fg break-all">{v}</span>
    </div>
  );
}

function NodeDetail({ node, onFocus }: { node: GraphNode; onFocus: () => void }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-fg">{node.name}</h4>
        <button className="btn-ghost px-2 py-0.5 text-[11px]" onClick={onFocus}>
          Focus
        </button>
      </div>
      <Row k="Sequence" v={`#${node.index}`} />
      <Row k="Method" v={node.method} />
      <Row k="Path" v={node.path} />
      <Row k="Folder" v={node.folder} />
      <Row k="Status" v={node.status_code ?? "-"} />
      <Row k="Role" v={node.role} />
      {node.produces.length > 0 && (
        <div>
          <p className="mt-2 text-fg-muted">Produces</p>
          {node.produces.map((v) => (
            <span key={v} className="badge badge-high mr-1 mt-1">{v}</span>
          ))}
        </div>
      )}
      {node.consumes.length > 0 && (
        <div>
          <p className="mt-2 text-fg-muted">Consumes</p>
          {node.consumes.map((v) => (
            <span key={v} className="badge badge-medium mr-1 mt-1">{v}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function EdgeDetail({
  edge,
  nodeById,
  onFocusVar,
}: {
  edge: GraphEdge & { group?: GraphEdge[] };
  nodeById: Map<string, GraphNode>;
  onFocusVar: () => void;
}) {
  const group = edge.group && edge.group.length > 1 ? edge.group : [edge];
  const s = nodeById.get(edge.source);
  const t = nodeById.get(edge.target);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-fg">Correlation</h4>
        <button className="btn-ghost px-2 py-0.5 text-[11px]" onClick={onFocusVar}>
          Focus var
        </button>
      </div>
      <Row k="Producer" v={s ? `#${s.index} ${s.name}` : edge.source} />
      <Row k="Consumer" v={t ? `#${t.index} ${t.name}` : edge.target} />
      {group.map((g, i) => (
        <div key={g.id} className="mt-2 rounded-[10px] border border-line/60 p-2">
          {group.length > 1 && <p className="text-[10px] text-fg-subtle">#{i + 1}</p>}
          <Row k="Variable" v={`\${${g.variable}}`} />
          <Row k="Response key" v={g.response_key} />
          <Row k="Producer path" v={g.producer_path} />
          <Row k="Consumer" v={`${g.consumer_location}: ${g.consumer_path}`} />
          {g.wrapper && <Row k="Wrapper" v={g.wrapper} />}
          <Row k="Value" v={g.value_masked} />
          <Row
            k="Confidence"
            v={<span className={`badge ${g.confidence === "high" ? "badge-high" : g.confidence === "medium" ? "badge-medium" : "badge-low"}`}>{g.confidence}</span>}
          />
          <Row k="Classification" v={g.classification.replace(/_/g, " ")} />
          <Row k="State" v={g.state} />
        </div>
      ))}
    </div>
  );
}
