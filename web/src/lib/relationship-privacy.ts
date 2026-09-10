/**
 * Shared copy for the per-edge privacy control, so the three places that offer
 * it (the per-member/group editor, the graph's add dialog, the graph's edge
 * dialog) cannot drift into saying different things about the same setting.
 *
 * An edge is private unless its owner says otherwise, and "public" on the edge
 * is only ever permission, never a promise: the edge still has to be in a
 * published view, and both members still have to be shown there, before anyone
 * sees it. The one line of help says exactly that and no more - a paragraph
 * next to a select is a paragraph nobody reads.
 *
 * The levels themselves are NOT here: they live with the publishing-off rule in
 * `@/components/privacy-level-select`, which every privacy select on every
 * surface now renders. This file owns the per-surface wording and nothing else.
 */

export const EDGE_VISIBILITY_HELP =
  "Public means this can appear on shared views and public profiles, but only when both members are shown.";
