import { useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Eye, EyeOff } from "lucide-react";

function getStrength(pw: string): { score: number; label: string } {
  if (pw.length === 0) return { score: 0, label: "" };
  if (pw.length < 8) return { score: 1, label: "Too short" };

  let variety = 0;
  if (/[a-z]/.test(pw)) variety++;
  if (/[A-Z]/.test(pw)) variety++;
  if (/[0-9]/.test(pw)) variety++;
  if (/[^a-zA-Z0-9]/.test(pw)) variety++;

  if (pw.length >= 16 && variety >= 2) return { score: 4, label: "Strong" };
  if (pw.length >= 12 && variety >= 2) return { score: 3, label: "Good" };
  if (pw.length >= 10 && variety >= 2) return { score: 3, label: "Good" };
  if (pw.length >= 8 && variety >= 2) return { score: 2, label: "Fair" };
  return { score: 1, label: "Weak" };
}

const strengthColors: Record<number, string> = {
  1: "bg-red-500",
  2: "bg-yellow-500",
  3: "bg-green-500",
  4: "bg-green-400",
};

export function PasswordField({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const [confirm, setConfirm] = useState("");
  const confirmRef = useRef<HTMLInputElement>(null);

  const strength = getStrength(value);
  const needsConfirm = !visible && value.length > 0;
  const mismatch = needsConfirm && confirm.length > 0 && value !== confirm;

  function handleConfirmChange(val: string) {
    setConfirm(val);
    // Use native validation to block form submit on mismatch
    if (confirmRef.current) {
      confirmRef.current.setCustomValidity(
        val && val !== value ? "Passwords don't match" : "",
      );
    }
  }

  // Clear confirm validity when toggling to visible
  function handleToggle() {
    const next = !visible;
    setVisible(next);
    if (next && confirmRef.current) {
      confirmRef.current.setCustomValidity("");
    }
  }

  return (
    <div className="space-y-2">
      <Label htmlFor={id}>Password</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required
          minLength={8}
          className="flex-1"
          autoComplete="new-password"
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="shrink-0 text-muted-foreground hover:text-foreground"
          onClick={handleToggle}
          tabIndex={-1}
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </Button>
      </div>

      {/* Strength meter */}
      {value.length > 0 && (
        <div className="space-y-1">
          <div className="flex gap-1">
            {[1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className={`h-1 flex-1 rounded-full transition-colors ${
                  i <= strength.score
                    ? strengthColors[strength.score]
                    : "bg-muted"
                }`}
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">{strength.label}</p>
        </div>
      )}

      {/* Confirm field — only when password is hidden */}
      {needsConfirm && (
        <div className="space-y-1">
          <Label htmlFor={`${id}-confirm`} className="text-sm">
            Confirm password
          </Label>
          <Input
            ref={confirmRef}
            id={`${id}-confirm`}
            type="password"
            value={confirm}
            onChange={(e) => handleConfirmChange(e.target.value)}
            required
          />
          {mismatch && (
            <p className="text-xs text-destructive">Passwords don't match</p>
          )}
        </div>
      )}
    </div>
  );
}
