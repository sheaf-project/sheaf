import { useEffect, useRef } from "react";
import "altcha";
import "altcha/types/react";

declare global {
  interface Window {
    $altcha?: {
      i18n?: { set: (lang: string, strings: Record<string, string>) => void };
    };
  }
}

const SHEAF_STRINGS: Record<string, string> = {
  ariaLinkLabel: "Altcha",
  cancel: "Cancel",
  enterCode: "Enter code",
  enterCodeAria: "Enter code you hear. Press Space to play audio.",
  enterCodeFromImage: "To proceed, please enter the code from the image below.",
  error: "Verification failed. Try again.",
  expired: "Verification expired. Try again.",
  footer: "",
  getAudioChallenge: "Get an audio challenge",
  label: "I am a sentient being",
  loading: "Loading...",
  reload: "Reload",
  verify: "Verify",
  verificationRequired: "Verification required",
  verified: "Verified",
  verifying: "Verifying...",
  waitAlert: "Verifying...",
};

if (typeof window !== "undefined" && window.$altcha?.i18n?.set) {
  window.$altcha.i18n.set("sheaf", SHEAF_STRINGS);
}

interface Props {
  onVerified: (payload: string) => void;
  onReset?: () => void;
}

export function Captcha({ onVerified, onReset }: Props) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const handler = (ev: Event) => {
      const detail = (ev as CustomEvent<{ state: string; payload?: string }>).detail;
      if (detail?.state === "verified" && detail.payload) {
        onVerified(detail.payload);
      } else if (detail?.state === "unverified") {
        onReset?.();
      }
    };
    el.addEventListener("statechange", handler);
    return () => el.removeEventListener("statechange", handler);
  }, [onVerified, onReset]);

  const configuration = JSON.stringify({
    challenge: "/v1/auth/captcha/challenge",
    hideFooter: true,
  });

  return (
    <altcha-widget
      ref={ref}
      language="sheaf"
      configuration={configuration}
    />
  );
}
