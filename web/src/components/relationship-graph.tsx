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
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { isDirectedEdge, type GraphEdge, type GraphNode } from "@/lib/relationship-graph";
import { cn } from "@/lib/utils";

const NODE_R = 22;

/** How far a pointer may travel between down and up and still count as a click
 *  rather than a drag. The graph pans from anywhere, edges included, so this is
 *  the only thing separating "I grabbed the canvas here" from "I picked this
 *  edge" - a couple of pixels of hand tremor must not lose either. */
const CLICK_SLOP = 4;

/** Shared geometry for the edge arrowheads. One marker per colour in use has
 *  to be declared, since SVG markers can't inherit the path's stroke. */
const ARROW_MARKER_PROPS = {
  viewBox: "0 0 10 10",
  refX: "9",
  refY: "5",
  markerWidth: 7,
  markerHeight: 7,
  orient: "auto-start-reverse",
} as const;

/** The simulation is topology only: an id, and whatever position d3 has given
 *  it. Everything drawn (names, colours, labels) is read from the props at
 *  render time, so a rename or a recolour repaints without relaying out. */
interface SimNode extends SimulationNodeDatum {
  id: string;
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  id: string;
}

interface Transform {
  k: number;
  tx: number;
  ty: number;
}

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

export interface RelationshipGraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Clicking an edge does this. Leave it off and edges become scenery: no
   *  pointer cursor, no hover, nothing to hit. That is the whole of what a
   *  look-only graph has to do to be look-only. */
  onEdgeClick?: (edgeId: string) => void;
  /** Clicking a node does this. See `nodePress` for what counts as a click. */
  onNodeClick?: (nodeId: string) => void;
  /** What pressing a node means. "drag" (the default) nudges the node and lets
   *  the simulation settle it back; a press that ends where it started is also
   *  a click, the same slop rule the edges use. "pick" skips the drag
   *  entirely and reports the press immediately, for a graph being used to
   *  choose nodes rather than to look at. */
  nodePress?: "drag" | "pick";
  /** Ringed, for the node picked so far in a two-node pick. */
  highlightNodeId?: string | null;
  /** Kept lit as though hovered - the edge whose dialog is open, so the line
   *  you are reading about is the line you can see. */
  activeEdgeId?: string | null;
  /** Extra controls, placed left of the built-in "Reset view" button. */
  toolbar?: ReactNode;
  /** Free-floating content inside the canvas. Positioned by the caller (the
   *  container is `relative`), for hints and legends over the drawing. */
  overlay?: ReactNode;
  /** Merged over the container's own classes; `h-*` wins over the default. */
  className?: string;
  /** Which touch gestures the canvas keeps for itself. "none" (the default)
   *  takes the lot, which is right for a graph that is the whole page. On a
   *  page that scrolls underneath it, "pan-y" leaves vertical swipes to the
   *  page so a full-width canvas isn't a place scrolling goes to die; a
   *  sideways drag still pans, and a tap still lands. */
  touchAction?: "none" | "pan-y";
  /** What this picture is, for anyone who cannot see it. Say where the same
   *  information is available as text - the graph is spatial, not readable. */
  ariaLabel?: string;
}

/**
 * A d3-force layout rendered as React-controlled SVG, with hand-rolled pan
 * (drag the background), zoom (wheel), and node drag (which nudges the
 * simulation and then lets the node settle back into the organic layout).
 *
 * Render-only: it draws nodes and edges and reports clicks. It holds no idea
 * of what a relationship is, who may change one, or what a click should open -
 * dialogs, toolbars and edit modes belong to whoever mounts it.
 *
 * `nodes` and `edges` should be stable references (memoise them): they are
 * effect inputs. Changing either only relays the graph out when the *shape*
 * changes - a new node, a new edge, an edge repointed - so refetches and
 * recolours leave the layout exactly where the viewer left it.
 */
export function RelationshipGraphCanvas({
  nodes,
  edges,
  onEdgeClick,
  onNodeClick,
  nodePress = "drag",
  highlightNodeId = null,
  activeEdgeId = null,
  toolbar,
  overlay,
  className,
  touchAction = "none",
  ariaLabel,
}: RelationshipGraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [transform, setTransform] = useState<Transform>({ k: 1, tx: 0, ty: 0 });
  // The edge under the pointer.
  const [hoverEdgeId, setHoverEdgeId] = useState<string | null>(null);

  // SVG ids are document-global, so two graphs on one page would fight over
  // the arrow markers and the avatar clips. Colons are legal in an id but
  // awkward everywhere else, so they go.
  const uid = useId().replace(/:/g, "");

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
  // The shape the running simulation was built for.
  const builtFor = useRef<string>("");

  // Interaction bookkeeping (kept in a ref so the pointer handlers are stable).
  const drag = useRef<
    | { mode: "node"; node: SimNode }
    | { mode: "pan"; startX: number; startY: number; startTx: number; startTy: number }
    | null
  >(null);
  // Where a press that landed on an edge or a node started, so pointer-up can
  // tell a click on it from a pan or a drag that happened to begin on it.
  const edgePress = useRef<{ id: string; x: number; y: number } | null>(null);
  const nodeTap = useRef<{ id: string; x: number; y: number } | null>(null);

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

  // What the layout actually depends on: which nodes exist, and what joins
  // what. Names, colours and labels are absent on purpose.
  const shape =
    nodes.map((n) => n.id).join(",") +
    "|" +
    edges.map((e) => `${e.id}:${e.source_id}>${e.target_id}`).join(",") +
    `@${size.w}x${size.h}`;

  // (Re)build the simulation when that shape changes, and only then.
  useEffect(() => {
    if (shape === builtFor.current && simRef.current) return;
    builtFor.current = shape;
    const { w, h } = size;
    const simNodes: SimNode[] = nodes.map((n) => {
      const prior = posRef.current.get(n.id);
      return {
        id: n.id,
        x: prior?.x ?? w / 2 + (Math.random() - 0.5) * 200,
        y: prior?.y ?? h / 2 + (Math.random() - 0.5) * 200,
      };
    });
    const simLinks: SimLink[] = edges.map((e) => ({
      id: e.id,
      source: e.source_id,
      target: e.target_id,
    }));
    simRef.current?.stop();
    const simulation = forceSimulation<SimNode>(simNodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(simLinks)
          .id((d) => d.id)
          .distance(130)
          .strength(0.35),
      )
      .force("charge", forceManyBody().strength(-320))
      .force("center", forceCenter(w / 2, h / 2))
      .force("collide", forceCollide(NODE_R + 14))
      .on("tick", () => {
        for (const n of simNodes) {
          if (n.x != null && n.y != null) posRef.current.set(n.id, { x: n.x, y: n.y });
        }
        setSim({ nodes: simNodes.slice(), links: simLinks.slice() });
      });
    simRef.current = simulation;
    setSim({ nodes: simNodes, links: simLinks });
  }, [nodes, edges, shape, size]);

  // Stopping belongs to going away, not to every re-run of the effect above:
  // it re-runs on any new array and mostly decides to leave the simulation
  // alone. Clearing the shape here is what lets a remount rebuild.
  useEffect(() => {
    return () => {
      simRef.current?.stop();
      simRef.current = null;
      builtFor.current = "";
    };
  }, []);

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
    if (nodePress === "pick") {
      onNodeClick?.(node.id);
      return;
    }
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { mode: "node", node };
    // Same slop rule as the edges: a press that goes nowhere is a click, and
    // dragging the node is still dragging the node.
    if (onNodeClick) nodeTap.current = { id: node.id, x: e.clientX, y: e.clientY };
    simRef.current?.alphaTarget(0.3).restart();
  }

  /** Deliberately does NOT stop propagation: the press still reaches the
   *  background handler and starts a pan, so a drag that begins on an edge
   *  pans like a drag anywhere else. Only a press that ends without travelling
   *  (see `onPointerUp`) opens the edge. */
  function onEdgePointerDown(e: React.PointerEvent, id: string) {
    edgePress.current = { id, x: e.clientX, y: e.clientY };
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

  /** Called with the event on a real pointer-up, and without one when the
   *  pointer leaves the canvas - which ends the drag but is not a click. */
  function onPointerUp(e?: React.PointerEvent) {
    const d = drag.current;
    if (d?.mode === "node") {
      // Release the pin so the node relaxes back into the organic layout.
      d.node.fx = null;
      d.node.fy = null;
      simRef.current?.alphaTarget(0);
    }
    drag.current = null;

    const tap = nodeTap.current;
    nodeTap.current = null;
    if (tap && e && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) <= CLICK_SLOP) {
      onNodeClick?.(tap.id);
    }

    const press = edgePress.current;
    edgePress.current = null;
    if (
      press &&
      e &&
      Math.hypot(e.clientX - press.x, e.clientY - press.y) <= CLICK_SLOP
    ) {
      onEdgeClick?.(press.id);
    }
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

  const { nodes: simNodes, links } = sim;

  // Everything drawn is looked up from the props by id, never held in the
  // simulation: recolouring a type or renaming a member repaints without
  // touching the layout.
  const nodeById = new Map(nodes.map((n) => [n.id, n] as const));
  const edgeById = new Map(edges.map((e) => [e.id, e] as const));
  const arrowColors = [
    ...new Set(edges.map((e) => e.color).filter((c): c is string => Boolean(c))),
  ];

  // Fan out multiple relationships between the same pair: group by unordered
  // node pair and give each edge a slot so it draws as its own curve instead of
  // overlapping (which otherwise hid all but one).
  const edgeSlot = new Map<string, { slot: number; count: number }>();
  {
    const counts = new Map<string, number>();
    const pairKey = (l: SimLink) =>
      [(l.source as SimNode).id, (l.target as SimNode).id].sort().join("|");
    for (const l of links) counts.set(pairKey(l), (counts.get(pairKey(l)) ?? 0) + 1);
    const seen = new Map<string, number>();
    for (const l of links) {
      const key = pairKey(l);
      const slot = seen.get(key) ?? 0;
      seen.set(key, slot + 1);
      edgeSlot.set(l.id, { slot, count: counts.get(key) ?? 1 });
    }
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative h-[70vh] w-full overflow-hidden rounded-lg border bg-muted/10",
        className,
      )}
    >
      <div className="absolute right-2 top-2 z-10 flex gap-2">
        {toolbar}
        <Button variant="outline" size="sm" onClick={resetView}>
          Reset view
        </Button>
      </div>
      {overlay}
      <svg
        ref={svgRef}
        width={size.w}
        height={size.h}
        className={cn(
          "select-none",
          touchAction === "pan-y" ? "touch-pan-y" : "touch-none",
        )}
        role={ariaLabel ? "img" : undefined}
        aria-label={ariaLabel}
        onPointerDown={onBackgroundPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => onPointerUp()}
        // The browser taking the gesture over (a swipe that turned into a page
        // scroll) ends the drag, and is not a click.
        onPointerCancel={() => onPointerUp()}
        onWheel={onWheel}
      >
        <defs>
          <marker id={`${uid}-arrow`} {...ARROW_MARKER_PROPS}>
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
          {arrowColors.map((c, i) => (
            <marker key={c} id={`${uid}-arrow-${i}`} {...ARROW_MARKER_PROPS}>
              <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
            </marker>
          ))}
          {simNodes.map((n) => (
            <clipPath id={`${uid}-clip-${n.id}`} key={n.id}>
              <circle r={NODE_R} />
            </clipPath>
          ))}
        </defs>
        <g transform={`translate(${transform.tx} ${transform.ty}) scale(${transform.k})`}>
          {links.map((l) => {
            const edge = edgeById.get(l.id);
            const s = l.source as SimNode;
            const t = l.target as SimNode;
            if (!edge) return null;
            if (s.x == null || s.y == null || t.x == null || t.y == null) return null;
            const { slot, count } = edgeSlot.get(l.id) ?? { slot: 0, count: 1 };
            const offset = (slot - (count - 1) / 2) * 26;

            // Trim the ends to the node boundary along the straight chord.
            const sdx = t.x - s.x;
            const sdy = t.y - s.y;
            const sdist = Math.hypot(sdx, sdy) || 1;
            const sux = sdx / sdist;
            const suy = sdy / sdist;
            const x1 = s.x + sux * NODE_R;
            const y1 = s.y + suy * NODE_R;
            const x2 = t.x - sux * NODE_R;
            const y2 = t.y - suy * NODE_R;

            // Curve control + label apex, offset perpendicular from a canonical
            // orientation (min id -> max id) so every edge in the pair fans to a
            // consistent side. offset 0 (a lone edge) yields a straight line.
            const [a, b] = s.id < t.id ? [s, t] : [t, s];
            const cdx = (b.x ?? 0) - (a.x ?? 0);
            const cdy = (b.y ?? 0) - (a.y ?? 0);
            const cdist = Math.hypot(cdx, cdy) || 1;
            const perpX = -cdy / cdist;
            const perpY = cdx / cdist;
            const mx = (x1 + x2) / 2;
            const my = (y1 + y2) / 2;
            const cx = mx + perpX * offset * 2;
            const cy = my + perpY * offset * 2;
            const apexX = mx + perpX * offset;
            const apexY = my + perpY * offset;

            // A type with no colour keeps the neutral default.
            const stroke = edge.color ?? undefined;
            const marker = stroke
              ? `url(#${uid}-arrow-${arrowColors.indexOf(stroke)})`
              : `url(#${uid}-arrow)`;

            const d = `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`;
            const pickable = Boolean(onEdgeClick);
            const lit = hoverEdgeId === l.id || activeEdgeId === l.id;

            return (
              <g key={l.id}>
                <path
                  d={d}
                  fill="none"
                  className={
                    stroke
                      ? undefined
                      : lit
                        ? "stroke-muted-foreground/80"
                        : "stroke-muted-foreground/40"
                  }
                  stroke={stroke}
                  strokeWidth={lit ? 3 : 1.5}
                  markerEnd={isDirectedEdge(edge) ? marker : undefined}
                />
                <text
                  x={apexX}
                  y={apexY}
                  dy={-3}
                  textAnchor="middle"
                  className="pointer-events-none fill-muted-foreground text-[10px]"
                  stroke="var(--background)"
                  strokeWidth={3}
                  paintOrder="stroke"
                >
                  {edge.source_label}
                </text>
                {/* Invisible, fat, and on top: a 1.5px line is a miserable
                    thing to hit. `pointerEvents: stroke` makes only this band
                    clickable, whatever it is painted (or not painted) with. */}
                <path
                  d={d}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={14}
                  className={pickable ? "cursor-pointer" : undefined}
                  style={{ pointerEvents: pickable ? "stroke" : "none" }}
                  onPointerDown={(e) => onEdgePointerDown(e, l.id)}
                  onPointerEnter={() => setHoverEdgeId(l.id)}
                  onPointerLeave={() =>
                    setHoverEdgeId((cur) => (cur === l.id ? null : cur))
                  }
                />
              </g>
            );
          })}
          {simNodes.map((sn) => {
            const n = nodeById.get(sn.id);
            if (!n) return null;
            if (sn.x == null || sn.y == null) return null;
            return (
              <g
                key={sn.id}
                transform={`translate(${sn.x} ${sn.y})`}
                className={nodePress === "pick" ? "cursor-pointer" : "cursor-grab"}
                onPointerDown={(e) => onNodePointerDown(e, sn)}
              >
                {highlightNodeId === sn.id && (
                  <circle
                    r={NODE_R + 4}
                    fill="none"
                    className="stroke-primary"
                    strokeWidth={2}
                  />
                )}
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
                    clipPath={`url(#${uid}-clip-${sn.id})`}
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
