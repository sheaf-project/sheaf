/**
 * Build-time flags baked into the bundle by Vite.
 *
 * These are not runtime settings. Vite inlines `import.meta.env.VITE_*` at
 * build time, so changing one means rebuilding the bundle - which is the
 * point: nothing a running instance serves, and nothing a user does, can
 * turn one on. Every flag defaults to off, so an image built without them
 * behaves exactly as it did before the flag existed.
 */

/**
 * Skip the "Welcome to Sheaf" onboarding dialog entirely.
 *
 * For screenshot automation and UI harnesses, where the dialog lands over
 * whatever surface is being captured and has to be clicked away before every
 * shot. Dismissing it the normal way is a cookie-carrying PATCH, so on a
 * scratch stack whose origin is not in `CSRF_TRUSTED_ORIGINS` it 403s and the
 * dialog resurrects on every navigation.
 *
 * Do not set this on a build real people will use. The dialog is how a new
 * account gets pointed at two-factor auth and recovery codes, and suppressing
 * it is NOT the same as someone choosing to skip it: `onboarding_complete` is
 * never written, so nothing records that they were ever asked, and they will
 * see the prompt the first time they load a build without the flag.
 */
export const SKIP_ONBOARDING = import.meta.env.VITE_SKIP_ONBOARDING === "true";

if (SKIP_ONBOARDING) {
  // Deliberately noisy. This should never be true on a build serving real
  // accounts, so if it is, say so somewhere a person will trip over it.
  console.warn(
    "[sheaf] VITE_SKIP_ONBOARDING is set: the welcome and security prompt " +
      "is disabled in this build. Intended for screenshot and test " +
      "harnesses only.",
  );
}
