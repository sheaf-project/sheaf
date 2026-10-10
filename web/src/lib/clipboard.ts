import { toast } from "sonner";

/**
 * Copy `text` to the clipboard, or say plainly that it did not happen.
 *
 * `navigator.clipboard` is undefined outside a secure context (a plain-http
 * selfhost, say), and `writeText` can also be refused by the browser. Either
 * way the user must not walk away believing the copy worked, which matters
 * most for the things that are shown exactly once: recovery codes, a new API
 * key, a generated password, an activation link. `label` names the thing in
 * the fallback instruction ("select the recovery codes and copy them manually").
 *
 * Resolves true when the text is on the clipboard. Callers own the success
 * UI, since it differs per surface (a "Copied" toast, a tick on the button).
 */
export async function copyToClipboard(text: string, label: string): Promise<boolean> {
  const fallback = `Couldn't copy - select the ${label} and copy manually`;
  if (!navigator.clipboard) {
    toast.error(fallback);
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    toast.error(fallback);
    return false;
  }
}
