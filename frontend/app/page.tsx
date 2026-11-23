"use client";

import { AetherApp } from "@/components/AetherApp";
import { Backdrop } from "@/components/Backdrop";
import { PrefsProvider } from "@/lib/prefs";
import { VoiceProvider } from "@/lib/useNeuralVoice";

export default function Home() {
  return (
    <PrefsProvider>
      <VoiceProvider>
        <Backdrop />
        <AetherApp />
      </VoiceProvider>
    </PrefsProvider>
  );
}
