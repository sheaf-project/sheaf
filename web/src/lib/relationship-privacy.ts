import type { PrivacyLevel } from "@/types/api";

/**
 * Shared copy for the per-edge privacy control, so the two places that offer
 * it (the per-member/group editor and the graph's add dialog) cannot drift
 * into saying different things about the same setting.
 *
 * An edge is private unless its owner says otherwise, and "public" on the edge
 * is only ever permission, never a promise: the edge still has to be in a
 * published view, and both members still have to be shown there, before anyone
 * sees it. The one line of help says exactly that and no more - a paragraph
 * next to a select is a paragraph nobody reads.
 */
/** Same three words, in the same order, as the member privacy select. */
export const EDGE_VISIBILITY_LEVELS: { value: PrivacyLevel; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "friends", label: "Friends only" },
  { value: "public", label: "Public" },
];

export const EDGE_VISIBILITY_HELP =
  "Public means this can appear on shared views and public profiles, but only when both members are shown.";
