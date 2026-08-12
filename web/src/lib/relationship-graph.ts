/**
 * The one model every graph Sheaf draws is fed with.
 *
 * There are (or will be) three consumers of the same picture: the owner's
 * relationships page, the public profile, and the subsystem map. They read
 * from three different endpoints with three different shapes, so the renderer
 * is given this instead - the small set of facts a drawing actually needs.
 * Anything a particular consumer knows and the picture does not draw (privacy
 * levels, type ids, who may edit what) stays with that consumer.
 *
 * The field names deliberately match the API shapes, so a payload row can
 * usually be handed over as-is or with one spread.
 */

export interface GraphNode {
  id: string;
  name: string;
  /** Fills the node circle; the neutral default when null or absent. */
  color?: string | null;
  /** Drawn inside the circle when present, initial letter otherwise. Callers
   *  on a public surface should only pass images they are willing to have a
   *  stranger's browser fetch (see `isPublicImageAllowed`). */
  avatar_url?: string | null;
}

export interface GraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  /** How the edge reads from each end ("parent" / "child"). The source label
   *  is the one drawn along the line; the target label decides, with `mutual`,
   *  whether the line carries an arrow. */
  source_label: string;
  target_label: string;
  /** True only for an `either` edge its owner marked mutual. */
  mutual?: boolean;
  /** Stroke colour, usually the relationship type's; neutral when absent. */
  color?: string | null;
  /** Set it to override the derivation below. The owner-side graph payload
   *  computes this server-side and passes it straight through; a consumer
   *  whose payload doesn't carry it can leave it off. */
  directed?: boolean;
}

/**
 * Whether the line gets an arrowhead.
 *
 * An undirected edge (a symmetric type, or an `either` type marked mutual)
 * reads the same from both ends, and the server says so twice over: `mutual`,
 * and two labels that come back identical. Either one means there is no
 * direction to draw.
 */
export function isDirectedEdge(edge: {
  source_label: string;
  target_label: string;
  mutual?: boolean;
  directed?: boolean;
}): boolean {
  if (edge.directed !== undefined) return edge.directed;
  return !edge.mutual && edge.source_label !== edge.target_label;
}
