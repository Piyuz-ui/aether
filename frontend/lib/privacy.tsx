"use client";

/**
 * Hide network addresses on screen (for screenshots, screen sharing, streaming). One switch,
 * remembered in this browser, used wherever AETHER shows a computer's IP or URL.
 */
import { Eye, EyeOff } from "lucide-react";

import { usePrefs } from "./prefs";

export const DOTS = "•••.•••.•.•";

/** "http://192.168.1.9:11434" → "http://•••.•••.•.•:11434" (scheme and port stay readable). */
export function maskAddress(text: string): string {
  return text
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, DOTS)                       // IPv4
    .replace(/\[[0-9a-f:]+\]/gi, "[••••]")                                // IPv6
    .replace(/\b([a-z0-9-]+\.)+(?:ts\.net|local|lan|home)\b/gi, "•••••••"); // hostnames
}

export function useHideAddresses() {
  const { prefs, setPref } = usePrefs();
  const hidden = prefs.hideAddresses;
  return {
    hidden,
    toggle: () => setPref("hideAddresses", !hidden),
    show: (text: string) => (hidden ? maskAddress(text) : text),
  };
}

/** The 👁 button: shows or hides addresses everywhere. */
export function AddressEye() {
  const { hidden, toggle } = useHideAddresses();
  return (
    <button type="button" onClick={toggle} aria-pressed={hidden}
      aria-label={hidden ? "Show addresses" : "Hide addresses"} title={hidden ? "Show addresses" : "Hide addresses"}
      className="grid size-7 place-items-center rounded-lg text-mist transition-colors hover:bg-white/[0.07] hover:text-frost">
      {hidden ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
    </button>
  );
}
