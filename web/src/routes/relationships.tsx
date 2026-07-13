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
import { Checkbox } from "@/components/ui/checkbox";
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
  useRelationshipGraph,
  useRelationshipTypes,
} from "@/hooks/use-relationships";
import type { RelationshipEdgeCreate, RelationshipGraph } from "@/types/api";

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
function GraphCanvas({
  graph,
  scope,
}: {
  graph: RelationshipGraph;
  scope: "members" | "groups";
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [transform, setTransform] = useState<Transform>({ k: 1, tx: 0, ty: 0 });
  // "Add relationship" mode: click a source node then a target node.
  const [addMode, setAddMode] = useState(false);
  const [pending, setPending] = useState<SimNode | null>(null);
  const [target, setTarget] = useState<SimNode | null>(null);
  const nodeNoun = scope === "members" ? "member" : "group";

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
    if (addMode) {
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
        <Button
          variant={addMode ? "default" : "outline"}
          size="sm"
          onClick={toggleAddMode}
        >
          {addMode ? "Adding relationships" : "Add relationship"}
        </Button>
        <Button variant="outline" size="sm" onClick={resetView}>
          Reset view
        </Button>
      </div>
      {addMode && (
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

            return (
              <g key={l.id}>
                <path
                  d={`M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`}
                  fill="none"
                  className="stroke-muted-foreground/40"
                  strokeWidth={1.5}
                  markerEnd={l.directed ? "url(#rel-arrow)" : undefined}
                />
                <text
                  x={apexX}
                  y={apexY}
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
                className={addMode ? "cursor-pointer" : "cursor-grab"}
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
      {addMode && pending && target && (
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
    </div>
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
    create.mutate(payload, { onSuccess: onClose });
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
            {types && types.length === 0 && (
              <p className="text-xs text-muted-foreground">
                Define a relationship type in Settings &gt; Relationships first.
              </p>
            )}
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
        </div>
        <DialogFooter>
          <Button onClick={submit} disabled={!typeId || create.isPending}>
            {create.isPending ? "Adding..." : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
        <GraphCanvas graph={graph} scope={scope} />
      )}
    </div>
  );
}
