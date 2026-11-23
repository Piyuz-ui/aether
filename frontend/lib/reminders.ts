"use client";

/**
 * Deadline reminders. Polls the task list and raises a desktop notification (plus an in-app
 * toast) once per task when it's due within 24 hours, and again if it becomes overdue.
 */
import { useEffect, useRef } from "react";

import { api } from "./api";
import type { Task } from "./types";

const KEY = "aether.reminded.v1";

function dueTime(t: Task): number | null {
  if (!t.due) return null;
  const iso = t.due.includes("T") ? t.due : `${t.due}T23:59`;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

function remembered(): Record<string, true> {
  try {
    return JSON.parse(window.localStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function useReminders(enabled: boolean, revision: number, toast: (title: string, body: string) => void) {
  const toastRef = useRef(toast);
  toastRef.current = toast;

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    const check = async () => {
      let tasks: Task[];
      try {
        tasks = await api.tasks();
      } catch {
        return;
      }
      if (stopped) return;
      const seen = remembered();
      const now = Date.now();
      for (const t of tasks) {
        const due = dueTime(t);
        if (due === null) continue;
        const stage = due < now ? "overdue" : due - now < 24 * 3600 * 1000 ? "soon" : null;
        if (!stage) continue;
        const key = `${t.id}:${t.due}:${stage}`;
        if (seen[key]) continue;
        seen[key] = true;
        const title = stage === "overdue" ? `Overdue: ${t.title}` : `Coming up: ${t.title}`;
        const body = `${t.kind[0].toUpperCase()}${t.kind.slice(1)} · ${t.when}`;
        toastRef.current(title, body);
        if (typeof Notification !== "undefined" && Notification.permission === "granted") {
          new Notification(title, { body, tag: key });
        }
      }
      try {
        window.localStorage.setItem(KEY, JSON.stringify(seen));
      } catch {
        /* private mode */
      }
    };
    void check();
    const timer = window.setInterval(check, 60_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [enabled, revision]);
}

export async function requestNotifications(): Promise<boolean> {
  if (typeof Notification === "undefined") return false;
  if (Notification.permission === "granted") return true;
  return (await Notification.requestPermission()) === "granted";
}
