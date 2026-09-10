import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/hooks/use-auth";
import type { PrivacyLevel } from "@/types/api";

/**
 * The one definition of the privacy select: the three levels, and the rule
 * about when "Public" may be chosen at all.
 *
 * There used to be four copies of the level list plus two inline sets of
 * `<SelectItem>`s, one per surface, and that drift is exactly what let the bug
 * this file exists for happen: with `PUBLIC_PROFILES_ENABLED=false` the backend
 * refuses every raise to `public` with a 403, and every one of those selects
 * cheerfully offered Public anyway, so choosing it was a guaranteed dead end
 * with a permissions error at the end of it. A seventh surface must not be able
 * to reintroduce that, so the levels, the copy and the rule live in this file
 * and nowhere else - deliberately unexported, so the only way to offer a
 * privacy level anywhere is to render one of the components below.
 *
 * Two of them have to be used together at every surface that offers the
 * control: `PrivacyLevelSelect` renders the select, `PublishingOffNote` renders
 * the one line explaining a disabled Public. They are separate rather than one
 * component because the note's position differs by surface (a dense list line
 * puts the select in a flex row and the note underneath it, and the custom
 * fields card carries one note for a whole list), but they share one predicate,
 * so they can never disagree about whether Public is available.
 */

/** Same three words, in the same order, at every surface: "who may see this"
 *  is one question, so it gets one vocabulary. */
const PRIVACY_LEVELS: { value: PrivacyLevel; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "friends", label: "Friends only" },
  { value: "public", label: "Public" },
];

/**
 * The one line shown beside a select whose Public option is disabled.
 *
 * Deliberately the same vocabulary as `SharingOffCard` on the sharing page
 * ("Sharing is turned off on this instance") and as the backend's own 403
 * detail ("Anything already published is kept, and unpublishing still works"),
 * so somebody who meets this state on three different screens meets ONE
 * explanation of it rather than three descriptions that almost agree. It makes
 * no new claim about what the setting does; the card and the 403 are the two
 * authoritative wordings and this is their short form.
 */
const PUBLISHING_OFF_NOTE =
  "Sharing is turned off on this instance, so nothing new can be set to Public. Anything already public is kept, and you can still lower it.";

interface PublicAvailability {
  /**
   * The level the SERVER currently holds for this record - NOT the form's
   * in-progress value, and NOT a staged `pending_privacy`. The backend refuses
   * only an actual raise (`requested == public AND stored != public`), so
   * anything already stored as `public` must keep Public offered: that is how
   * its own value still displays, and above all it is how somebody LOWERS it.
   * Nothing may ever stand between a user and reducing their exposure.
   *
   * `undefined`/`null` means there is no stored record yet (a create form), so
   * choosing Public would be a raise from nothing - which the backend refuses
   * on the create paths too.
   */
  savedValue?: PrivacyLevel | null;
  /**
   * Whether the backend refuses a raise to public here at all. Defaults to
   * true. Pass `false` for the surfaces it deliberately does not gate - group
   * relationship edges, which `share_projection` never queries, so a public
   * group edge is visible to nobody and the server stores it as asked. Offering
   * Public there is honest; disabling it would invent a restriction the backend
   * does not have.
   */
  gated?: boolean;
}

/**
 * Is the backend going to refuse a raise to `public` on this record right now?
 *
 * The single predicate behind both the disabled option and the note. Mirrors
 * `refuse_raise_when_publishing_unavailable` in sheaf/services/sharing.py: it
 * fires on the instance setting alone, regardless of System Safety, and never
 * on a lowering.
 *
 * Reads `public_profiles_enabled` off the session rather than taking it as a
 * prop, for the same reason the sharing page's `useSharingOff` does: it is
 * instance state, not a decision any of these forms make, and the controls that
 * have to react to it sit several components deep.
 */
function usePublicRefused({
  savedValue,
  gated = true,
}: PublicAvailability): boolean {
  const { user } = useAuth();
  if (!gated) return false;
  // No session loaded yet: do not invent a restriction. The backend is still
  // the thing that decides, and a false lock here would be the same kind of
  // lie in the other direction.
  if (!user) return false;
  if (savedValue === "public") return false;
  return !user.public_profiles_enabled;
}

/**
 * The privacy select, everywhere one is offered.
 *
 * Public is rendered but DISABLED when the instance cannot publish, rather than
 * removed: dropping it would make the feature look absent, which is a different
 * lie from the one being fixed. A disabled option plus `PublishingOffNote` says
 * what is actually true - the option exists, this instance has it switched off.
 */
export function PrivacyLevelSelect({
  value,
  onValueChange,
  savedValue,
  gated,
  disabled,
  id,
  className,
  ariaLabel,
}: PublicAvailability & {
  value: PrivacyLevel;
  onValueChange: (value: PrivacyLevel) => void;
  /** Whole-control disable, e.g. while a mutation is in flight. */
  disabled?: boolean;
  /** Trigger id, for surfaces that pair the control with a `<Label htmlFor>`. */
  id?: string;
  /** Trigger classes, for the dense-row surfaces that shrink it. */
  className?: string;
  /** Trigger label, for surfaces with no visible `<Label>`. */
  ariaLabel?: string;
}) {
  const publicRefused = usePublicRefused({ savedValue, gated });
  return (
    <Select
      value={value}
      onValueChange={(v) => onValueChange(v as PrivacyLevel)}
      disabled={disabled}
    >
      <SelectTrigger id={id} className={className} aria-label={ariaLabel}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {PRIVACY_LEVELS.map((l) => (
          <SelectItem
            key={l.value}
            value={l.value}
            disabled={l.value === "public" && publicRefused}
          >
            {l.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * The explanation for a disabled Public option. Renders nothing when Public is
 * available, so it is always safe to place next to the select: it appears in
 * exactly the cases `PrivacyLevelSelect` greys the option out, and in no
 * others. Takes the same props for that reason.
 */
export function PublishingOffNote(props: PublicAvailability) {
  const publicRefused = usePublicRefused(props);
  if (!publicRefused) return null;
  return (
    <p className="text-[11px] text-muted-foreground">{PUBLISHING_OFF_NOTE}</p>
  );
}

/**
 * A level's label, for the display-only surfaces that show the value without a
 * select. A component rather than an exported helper so the level list stays
 * private to this file. Falls back to the raw value, so an unknown level from a
 * newer server still renders as something.
 */
export function PrivacyLevelLabel({ level }: { level: PrivacyLevel }) {
  return <>{PRIVACY_LEVELS.find((l) => l.value === level)?.label ?? level}</>;
}
