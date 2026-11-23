"use client";

import { AetherApp } from "@/components/AetherApp";
import { Backdrop } from "@/components/Backdrop";
import { PrefsProvider } from "@/lib/prefs";

export default function Home() {
  return (
    <PrefsProvider>
      <Backdrop />
      <AetherApp />
    </PrefsProvider>
  );
}
