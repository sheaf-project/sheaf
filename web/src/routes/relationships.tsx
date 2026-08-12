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

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ColorDot } from "@/components/color-dot";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateGroupRelationship,
  useCreateMemberRelationship,
  useDeleteGroupRelationship,
  useDeleteMemberRelationship,
  useGroupRelationships,
  useMemberRelationships,
  useRelationshipGraph,
  useRelationshipTypes,
  useUpdateGroupRelationship,
  useUpdateMemberRelationship,
} from "@/hooks/use-relationships";
import { RelationshipPrivacyControl } from "@/components/relationship-privacy-control";
import { RelationshipTypeDialog } from "@/components/relationship-type-dialog";
import {
  EDGE_VISIBILITY_HELP,
  EDGE_VISIBILITY_LEVELS,
} from "@/lib/relationship-privacy";
import type {
  PrivacyLevel,
  RelationshipEdgeCreate,
  RelationshipGraph,
  RelationshipGraphEdge,
} from "@/types/api";

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
function GraphCanvas({
  graph,
  scope,
  editMode,
}: {
  graph: RelationshipGraph;
  scope: "members" | "groups";
  /** Off: the graph is something to read, and an edge opens read-only. On:
   *  relationships can be added, reoriented, republished and removed. */
  editMode: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [transform, setTransform] = useState<Transform>({ k: 1, tx: 0, ty: 0 });
  // "Add relationship" mode: click a source node then a target node.
  const [addMode, setAddMode] = useState(false);
  const [pending, setPending] = useState<SimNode | null>(null);
  const [target, setTarget] = useState<SimNode | null>(null);
  // The edge whose dialog is open, and the one under the pointer.
  const [openEdgeId, setOpenEdgeId] = useState<string | null>(null);
  const [hoverEdgeId, setHoverEdgeId] = useState<string | null>(null);
  const nodeNoun = scope === "members" ? "member" : "group";
  const { data: types } = useRelationshipTypes();

  // Add mode is derived, not stored twice: leaving edit mode suspends it
  // rather than needing an effect to go and switch it off.
  const adding = editMode && addMode;

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
  // Where a press that landed on an edge started, so pointer-up can tell a
  // click on that edge from a pan that happened to begin on top of it.
  const edgePress = useRef<{ id: string; x: number; y: number } | null>(null);

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
    if (adding) {
      // Pick source, then a distinct target opens the add dialog.
      if (!pending) setPending(node);
      else if (pending.id === node.id) setPending(null);
      else setTarget(node);
      return;
    }
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { mode: "node", node };
    simRef.current?.alphaTarget(0.3).restart();
  }

  /** Deliberately does NOT stop propagation: the press still reaches the
   *  background handler and starts a pan, so a drag that begins on an edge
   *  pans like a drag anywhere else. Only a press that ends without travelling
   *  (see `onPointerUp`) opens the edge. */
  function onEdgePointerDown(e: React.PointerEvent, id: string) {
    edgePress.current = { id, x: e.clientX, y: e.clientY };
  }

  function toggleAddMode() {
    setAddMode((m) => !m);
    setPending(null);
    setTarget(null);
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

    const press = edgePress.current;
    edgePress.current = null;
    if (
      press &&
      e &&
      Math.hypot(e.clientX - press.x, e.clientY - press.y) <= CLICK_SLOP
    ) {
      setOpenEdgeId(press.id);
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

  const { nodes, links } = sim;

  // The edge dialog reads the payload edge, not the simulation link: the raw
  // edge keeps the labels, mutual flag and type id d3 has no use for. An edge
  // that disappears (removed here or elsewhere) simply closes the dialog.
  const openEdge = graph.edges.find((e) => e.id === openEdgeId) ?? null;
  const nodeName = (id: string) =>
    graph.nodes.find((n) => n.id === id)?.name ?? id.slice(0, 8);

  // Edge colour is a client-side join: the graph payload carries the type id,
  // the type carries the colour. Resolved at render rather than inside the
  // simulation so recolouring a type repaints without relaying out the graph.
  const colorByType = new Map(
    (types ?? []).map((t) => [t.id, t.color] as const),
  );
  const edgeColor = new Map<string, string>();
  for (const e of graph.edges) {
    const c = colorByType.get(e.relationship_type_id);
    if (c) edgeColor.set(e.id, c);
  }
  const arrowColors = [...new Set(edgeColor.values())];

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
      className="relative h-[70vh] w-full overflow-hidden rounded-lg border bg-muted/10"
    >
      <div className="absolute right-2 top-2 z-10 flex gap-2">
        {editMode && (
          <Button
            variant={adding ? "default" : "outline"}
            size="sm"
            onClick={toggleAddMode}
          >
            {adding ? "Adding relationships" : "Add relationship"}
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={resetView}>
          Reset view
        </Button>
      </div>
      {adding && (
        <div className="absolute left-2 top-2 z-10 rounded-md border bg-background/90 px-2 py-1 text-xs text-muted-foreground">
          {pending
            ? `${pending.name} selected. Click another ${nodeNoun} to connect them.`
            : `Click a ${nodeNoun} to start.`}
        </div>
      )}
      <svg
        ref={svgRef}
        width={size.w}
        height={size.h}
        className="touch-none select-none"
        onPointerDown={onBackgroundPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => onPointerUp()}
        onWheel={onWheel}
      >
        <defs>
          <marker id="rel-arrow" {...ARROW_MARKER_PROPS}>
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
          {arrowColors.map((c, i) => (
            <marker key={c} id={`rel-arrow-${i}`} {...ARROW_MARKER_PROPS}>
              <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
            </marker>
          ))}
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
            const stroke = edgeColor.get(l.id);
            const marker = stroke
              ? `url(#rel-arrow-${arrowColors.indexOf(stroke)})`
              : "url(#rel-arrow)";

            const d = `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`;
            // Picking an edge is picking a relationship, which is a thing you
            // can do while just reading the graph; only what the dialog then
            // offers depends on edit mode. Add mode is the exception: there,
            // every click is part of "pick two nodes".
            const pickable = !adding;
            const lit = hoverEdgeId === l.id || openEdgeId === l.id;

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
                  markerEnd={l.directed ? marker : undefined}
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
                  {l.label}
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
          {nodes.map((n) => {
            if (n.x == null || n.y == null) return null;
            return (
              <g
                key={n.id}
                transform={`translate(${n.x} ${n.y})`}
                className={adding ? "cursor-pointer" : "cursor-grab"}
                onPointerDown={(e) => onNodePointerDown(e, n)}
              >
                {pending?.id === n.id && (
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
      {adding && pending && target && (
        <AddEdgeDialog
          scope={scope}
          source={{ id: pending.id, name: pending.name }}
          target={{ id: target.id, name: target.name }}
          onClose={() => {
            setTarget(null);
            setPending(null);
          }}
        />
      )}
      {openEdge && (
        <EdgeDialog
          scope={scope}
          edge={openEdge}
          sourceName={nodeName(openEdge.source_id)}
          targetName={nodeName(openEdge.target_id)}
          editable={editMode}
          onClose={() => setOpenEdgeId(null)}
        />
      )}
    </div>
  );
}

/**
 * One existing edge, opened by clicking it on the graph. The graph is where
 * relationships are actually looked at, so it is also where they are managed:
 * this is the edge's whole management surface (privacy, direction, mutual,
 * removal), the same set of things the per-member editor offers, on the same
 * shared privacy control.
 *
 * One component, two presentations. Without edit mode it states what the edge
 * is and stops there - no select, no buttons, nothing that can change anything
 * by being clicked at.
 */
function EdgeDialog({
  scope,
  edge,
  sourceName,
  targetName,
  editable,
  onClose,
}: {
  scope: "members" | "groups";
  edge: RelationshipGraphEdge;
  sourceName: string;
  targetName: string;
  editable: boolean;
  onClose: () => void;
}) {
  const isMember = scope === "members";
  const nodeNoun = isMember ? "member" : "group";
  const { data: types } = useRelationshipTypes();
  const type = types?.find((t) => t.id === edge.relationship_type_id);

  // The graph payload carries no privacy fields, and that is fine: the
  // per-endpoint relationship list does, it is already a cached query, and the
  // same key prefix invalidates it whenever anything about an edge changes.
  // Either endpoint's list holds this edge, so one fetch answers it - cheaper
  // and less to keep in step than widening the graph response.
  const memberRows = useMemberRelationships(isMember ? edge.source_id : null);
  const groupRows = useGroupRelationships(isMember ? null : edge.source_id);
  const rows = isMember ? memberRows.data : groupRows.data;
  const row = rows?.find((e) => e.id === edge.id);

  const updateMember = useUpdateMemberRelationship();
  const updateGroup = useUpdateGroupRelationship();
  const update = isMember ? updateMember : updateGroup;
  const deleteMember = useDeleteMemberRelationship();
  const deleteGroup = useDeleteGroupRelationship();
  const remove = isMember ? deleteMember : deleteGroup;

  // A symmetric type has no direction to reverse, and a mutual either-edge
  // reads the same label at both ends, so neither offers a flip.
  const canFlip = !!type && type.symmetry !== "symmetric" && !edge.mutual;
  const canBeMutual = type?.symmetry === "either";

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Relationship</DialogTitle>
          <DialogDescription>
            {editable
              ? `Between these two ${nodeNoun}s. Changes save as you make them.`
              : `Between these two ${nodeNoun}s. Turn on Edit to change it.`}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5 text-sm">
            <ColorDot color={type?.color ?? null} />
            <span className="font-medium">{edge.type_name}</span>
            {edge.mutual && (
              <Badge variant="outline" className="text-[10px]">
                mutual
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            {sourceName} is the {edge.source_label}. {targetName} is the{" "}
            {edge.target_label}.
          </p>

          {row ? (
            <RelationshipPrivacyControl
              scope={isMember ? "member" : "group"}
              edge={row}
              layout="stacked"
              label="Visibility"
              readOnly={!editable}
            />
          ) : (
            <p className="text-xs text-muted-foreground">
              {rows ? "This relationship is gone." : "Loading visibility..."}
            </p>
          )}

          {editable && canBeMutual && type && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={edge.mutual}
                disabled={update.isPending}
                onCheckedChange={(v) =>
                  update.mutate({
                    edgeId: edge.id,
                    data: { mutual: v === true },
                  })
                }
              />
              Mutual (both are {type.forward_label})
            </label>
          )}

          {editable && canFlip && (
            <div className="space-y-1">
              <Button
                variant="outline"
                size="sm"
                disabled={update.isPending}
                onClick={() =>
                  update.mutate({ edgeId: edge.id, data: { flip: true } })
                }
              >
                Reverse direction
              </Button>
              <p className="text-[11px] text-muted-foreground">
                Makes {targetName} the {edge.source_label} and {sourceName} the{" "}
                {edge.target_label}.
              </p>
            </div>
          )}
        </div>
        <DialogFooter>
          {editable ? (
            <Button
              variant="destructive"
              size="sm"
              disabled={remove.isPending}
              onClick={() => remove.mutate(edge.id, { onSuccess: onClose })}
            >
              {remove.isPending ? "Removing..." : "Remove relationship"}
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={onClose}>
              Close
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The little form shown once two nodes are picked in "add relationship" mode.
 *  Mirrors the direction/mutual logic of the per-node editor, but source and
 *  target are the two explicitly-picked nodes. */
function AddEdgeDialog({
  scope,
  source,
  target,
  onClose,
}: {
  scope: "members" | "groups";
  source: { id: string; name: string };
  target: { id: string; name: string };
  onClose: () => void;
}) {
  const { data: types } = useRelationshipTypes();
  const createMember = useCreateMemberRelationship();
  const createGroup = useCreateGroupRelationship();
  const create = scope === "members" ? createMember : createGroup;

  const [typeId, setTypeId] = useState("");
  const [role, setRole] = useState<"forward" | "reverse">("forward");
  const [mutual, setMutual] = useState(false);
  // Private until said otherwise, same as the per-member editor.
  const [visibility, setVisibility] = useState<PrivacyLevel>("private");
  const [showNewType, setShowNewType] = useState(false);

  const type = types?.find((t) => t.id === typeId);
  const symmetry = type?.symmetry;
  const showRole = symmetry === "directional" || symmetry === "either";
  const showMutual = symmetry === "either";
  const roleHidden = showMutual && mutual;

  function onTypeChange(v: string) {
    setTypeId(v);
    setRole("forward");
    setMutual(false);
  }

  function submit() {
    if (!type) return;
    let payload: RelationshipEdgeCreate;
    if (symmetry === "symmetric") {
      payload = { source_id: source.id, target_id: target.id, relationship_type_id: type.id };
    } else if (showMutual && mutual) {
      payload = { source_id: source.id, target_id: target.id, relationship_type_id: type.id, mutual: true };
    } else if (role === "forward") {
      payload = { source_id: source.id, target_id: target.id, relationship_type_id: type.id };
    } else {
      payload = { source_id: target.id, target_id: source.id, relationship_type_id: type.id };
    }
    create.mutate({ ...payload, visibility }, { onSuccess: onClose });
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add relationship</DialogTitle>
          <DialogDescription>
            Between {source.name} and {target.name}.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">Type</Label>
            <Select value={typeId} onValueChange={onTypeChange}>
              <SelectTrigger>
                <SelectValue placeholder="Choose a type..." />
              </SelectTrigger>
              <SelectContent>
                {(types ?? []).map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setShowNewType(true)}
            >
              New relationship type
            </Button>
          </div>
          {showRole && !roleHidden && type && (
            <div className="space-y-1">
              <Label className="text-xs">Direction</Label>
              <Select value={role} onValueChange={(v) => setRole(v as "forward" | "reverse")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="forward">
                    {source.name} is the {type.forward_label}
                  </SelectItem>
                  <SelectItem value="reverse">
                    {source.name} is the {type.reverse_label}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          {showMutual && type && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={mutual}
                onCheckedChange={(v) => setMutual(v === true)}
              />
              Mutual (both are {type.forward_label})
            </label>
          )}
          <div className="space-y-1">
            <Label className="text-xs">Visibility</Label>
            <Select
              value={visibility}
              onValueChange={(v) => setVisibility(v as PrivacyLevel)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EDGE_VISIBILITY_LEVELS.map((l) => (
                  <SelectItem key={l.value} value={l.value}>
                    {l.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground">
              {EDGE_VISIBILITY_HELP}
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button onClick={submit} disabled={!typeId || create.isPending}>
            {create.isPending ? "Adding..." : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
      {showNewType && (
        <RelationshipTypeDialog
          onOpenChange={(open) => !open && setShowNewType(false)}
          onCreated={(created) => onTypeChange(created.id)}
        />
      )}
    </Dialog>
  );
}

export function RelationshipsPage() {
  const [scope, setScope] = useState<"members" | "groups">("members");
  // Off by default: this page is mostly something you look at, and a graph you
  // are dragging around is not a place to be one stray click from changing
  // who is whose anything.
  const [editMode, setEditMode] = useState(false);
  const { data: graph, isLoading } = useRelationshipGraph(scope);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Relationships</h1>
          <p className="text-sm text-muted-foreground">
            Drag to pan, scroll to zoom, drag a node to nudge it. Click a line
            between two to see the relationship it draws. Turn on Edit to add,
            reverse, republish or remove them; existing types are edited in
            Settings.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={editMode ? "default" : "outline"}
            size="sm"
            onClick={() => setEditMode((m) => !m)}
            aria-pressed={editMode}
          >
            {editMode ? "Editing" : "Edit"}
          </Button>
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
        <GraphCanvas graph={graph} scope={scope} editMode={editMode} />
      )}
    </div>
  );
}
