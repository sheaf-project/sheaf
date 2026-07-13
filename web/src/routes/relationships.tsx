import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useRelationshipGraph } from "@/hooks/use-relationships";
import type { RelationshipGraph } from "@/types/api";

const NODE_R = 22;

interface SimNode extends SimulationNodeDatum {
  id: string;
  name: string;
  avatar_url: string | null;
  color: string | null;
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  id: string;
  label: string;
  directed: boolean;
}

interface Transform {
  k: number;
  tx: number;
  ty: number;
}

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

/** d3-force layout rendered as React-controlled SVG, with hand-rolled pan
 *  (drag the background), zoom (wheel), and node drag (which nudges the
 *  simulation and then lets the node settle back into the organic layout). */
function GraphCanvas({ graph }: { graph: RelationshipGraph }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [transform, setTransform] = useState<Transform>({ k: 1, tx: 0, ty: 0 });

  // d3 mutates node x/y in place; each tick publishes fresh array wrappers to
  // state so the SVG re-renders (reading live refs during render is disallowed
  // by the react-hooks/refs rule). The node objects are shared with the running
  // simulation, so drag handlers can set fx/fy on them directly.
  const [sim, setSim] = useState<{ nodes: SimNode[]; links: SimLink[] }>({
    nodes: [],
    links: [],
  });
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null);
  // Preserve positions across refetches so the graph doesn't jump when an edge
  // is added/removed elsewhere.
  const posRef = useRef<Map<string, { x: number; y: number }>>(new Map());

  // Interaction bookkeeping (kept in a ref so the pointer handlers are stable).
  const drag = useRef<
    | { mode: "node"; node: SimNode }
    | { mode: "pan"; startX: number; startY: number; startTx: number; startTy: number }
    | null
  >(null);

  // Track container size.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: Math.max(360, el.clientHeight) });
    });
    ro.observe(el);
    setSize({ w: el.clientWidth, h: Math.max(360, el.clientHeight) });
    return () => ro.disconnect();
  }, []);

  // (Re)build the simulation when the data or canvas size changes.
  useEffect(() => {
    const { w, h } = size;
    const nodes: SimNode[] = graph.nodes.map((n) => {
      const prior = posRef.current.get(n.id);
      return {
        id: n.id,
        name: n.name,
        avatar_url: n.avatar_url,
        color: n.color,
        x: prior?.x ?? w / 2 + (Math.random() - 0.5) * 200,
        y: prior?.y ?? h / 2 + (Math.random() - 0.5) * 200,
      };
    });
    const links: SimLink[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source_id,
      target: e.target_id,
      label: e.source_label,
      directed: e.directed,
    }));
    const simulation = forceSimulation<SimNode>(nodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .distance(130)
          .strength(0.35),
      )
      .force("charge", forceManyBody().strength(-320))
      .force("center", forceCenter(w / 2, h / 2))
      .force("collide", forceCollide(NODE_R + 14))
      .on("tick", () => {
        for (const n of nodes) {
          if (n.x != null && n.y != null) posRef.current.set(n.id, { x: n.x, y: n.y });
        }
        setSim({ nodes: nodes.slice(), links: links.slice() });
      });
    simRef.current = simulation;
    setSim({ nodes, links });
    return () => {
      simulation.stop();
    };
  }, [graph, size]);

  function toGraphCoords(clientX: number, clientY: number) {
    const rect = svgRef.current!.getBoundingClientRect();
    const localX = clientX - rect.left;
    const localY = clientY - rect.top;
    return {
      x: (localX - transform.tx) / transform.k,
      y: (localY - transform.ty) / transform.k,
      localX,
      localY,
    };
  }

  function onNodePointerDown(e: React.PointerEvent, node: SimNode) {
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { mode: "node", node };
    simRef.current?.alphaTarget(0.3).restart();
  }

  function onBackgroundPointerDown(e: React.PointerEvent) {
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    drag.current = {
      mode: "pan",
      startX: e.clientX,
      startY: e.clientY,
      startTx: transform.tx,
      startTy: transform.ty,
    };
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    if (d.mode === "node") {
      const { x, y } = toGraphCoords(e.clientX, e.clientY);
      d.node.fx = x;
      d.node.fy = y;
    } else {
      setTransform((t) => ({
        ...t,
        tx: d.startTx + (e.clientX - d.startX),
        ty: d.startTy + (e.clientY - d.startY),
      }));
    }
  }

  function onPointerUp() {
    const d = drag.current;
    if (d?.mode === "node") {
      // Release the pin so the node relaxes back into the organic layout.
      d.node.fx = null;
      d.node.fy = null;
      simRef.current?.alphaTarget(0);
    }
    drag.current = null;
  }

  function onWheel(e: React.WheelEvent) {
    const rect = svgRef.current!.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;
    setTransform((t) => {
      const newK = clamp(t.k * (e.deltaY < 0 ? 1.1 : 0.9), 0.25, 4);
      return {
        k: newK,
        tx: localX - (localX - t.tx) * (newK / t.k),
        ty: localY - (localY - t.ty) * (newK / t.k),
      };
    });
  }

  function resetView() {
    setTransform({ k: 1, tx: 0, ty: 0 });
    simRef.current?.alpha(0.6).restart();
  }

  const { nodes, links } = sim;

  return (
    <div
      ref={containerRef}
      className="relative h-[70vh] w-full overflow-hidden rounded-lg border bg-muted/10"
    >
      <div className="absolute right-2 top-2 z-10">
        <Button variant="outline" size="sm" onClick={resetView}>
          Reset view
        </Button>
      </div>
      <svg
        ref={svgRef}
        width={size.w}
        height={size.h}
        className="touch-none select-none"
        onPointerDown={onBackgroundPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onWheel={onWheel}
      >
        <defs>
          <marker
            id="rel-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
          {nodes.map((n) => (
            <clipPath id={`rel-clip-${n.id}`} key={n.id}>
              <circle r={NODE_R} />
            </clipPath>
          ))}
        </defs>
        <g transform={`translate(${transform.tx} ${transform.ty}) scale(${transform.k})`}>
          {links.map((l) => {
            const s = l.source as SimNode;
            const t = l.target as SimNode;
            if (s.x == null || s.y == null || t.x == null || t.y == null) return null;
            const dx = t.x - s.x;
            const dy = t.y - s.y;
            const dist = Math.hypot(dx, dy) || 1;
            const ux = dx / dist;
            const uy = dy / dist;
            // Trim the ends to the node boundary so the arrowhead sits nicely.
            const x1 = s.x + ux * NODE_R;
            const y1 = s.y + uy * NODE_R;
            const x2 = t.x - ux * NODE_R;
            const y2 = t.y - uy * NODE_R;
            const mx = (x1 + x2) / 2;
            const my = (y1 + y2) / 2;
            return (
              <g key={l.id}>
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  className="stroke-muted-foreground/40"
                  strokeWidth={1.5}
                  markerEnd={l.directed ? "url(#rel-arrow)" : undefined}
                />
                <text
                  x={mx}
                  y={my}
                  dy={-3}
                  textAnchor="middle"
                  className="fill-muted-foreground text-[10px]"
                  stroke="var(--background)"
                  strokeWidth={3}
                  paintOrder="stroke"
                >
                  {l.label}
                </text>
              </g>
            );
          })}
          {nodes.map((n) => {
            if (n.x == null || n.y == null) return null;
            return (
              <g
                key={n.id}
                transform={`translate(${n.x} ${n.y})`}
                className="cursor-grab"
                onPointerDown={(e) => onNodePointerDown(e, n)}
              >
                <circle
                  r={NODE_R}
                  fill={n.color ?? "var(--muted)"}
                  className="stroke-background"
                  strokeWidth={2}
                />
                {n.avatar_url ? (
                  <image
                    href={n.avatar_url}
                    x={-NODE_R}
                    y={-NODE_R}
                    width={NODE_R * 2}
                    height={NODE_R * 2}
                    clipPath={`url(#rel-clip-${n.id})`}
                    preserveAspectRatio="xMidYMid slice"
                  />
                ) : (
                  <text
                    textAnchor="middle"
                    dy="0.35em"
                    className="fill-background text-sm font-medium"
                  >
                    {n.name.slice(0, 1).toUpperCase()}
                  </text>
                )}
                <text
                  y={NODE_R + 12}
                  textAnchor="middle"
                  className="fill-foreground text-[11px]"
                  stroke="var(--background)"
                  strokeWidth={3}
                  paintOrder="stroke"
                >
                  {n.name}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

export function RelationshipsPage() {
  const [scope, setScope] = useState<"members" | "groups">("members");
  const { data: graph, isLoading } = useRelationshipGraph(scope);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Relationships</h1>
          <p className="text-sm text-muted-foreground">
            Drag to pan, scroll to zoom, drag a node to nudge it. Manage
            relationships from each member or group; define types in Settings.
          </p>
        </div>
        <div className="flex gap-1 rounded-md border p-1">
          {(["members", "groups"] as const).map((s) => (
            <Button
              key={s}
              variant={scope === s ? "default" : "ghost"}
              size="sm"
              onClick={() => setScope(s)}
              className="capitalize"
            >
              {s}
            </Button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : !graph || graph.edges.length === 0 ? (
        <div className="rounded-lg border p-8 text-center text-sm text-muted-foreground">
          No {scope === "members" ? "member" : "group"} relationships yet. Add
          some from a {scope === "members" ? "member" : "group"}'s editor, then
          they will map out here.
        </div>
      ) : (
        <GraphCanvas graph={graph} />
      )}
    </div>
  );
}
