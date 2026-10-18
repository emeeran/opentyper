/* Small shared helpers. No dependencies. */
"use strict";

export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function fmtTok(n) { return n == null ? "?" : (+n).toLocaleString(); }

export function fmtTime(s) {
  if (!isFinite(s)) return "0:00";
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

export function timeAgo(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function clockOf(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" :
    d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.frequency.value = 880; gain.gain.value = 0.06;
    osc.connect(gain).connect(ctx.destination);
    osc.start(); osc.stop(ctx.currentTime + 0.12);
    osc.onended = () => ctx.close();
  } catch { /* audio unavailable */ }
}

export function autosize(t) {
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 200) + "px";
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function throttle(fn, ms) {
  let last = 0, timer = null, pending = null;
  return (...a) => {
    pending = a;
    if (timer) return;
    const run = () => {
      last = Date.now(); timer = null;
      fn(...pending);
    };
    const wait = Math.max(0, ms - (Date.now() - last));
    if (wait === 0) run();
    else timer = setTimeout(run, wait);
  };
}

export function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/* effective theme (system-aware) for code-highlight stylesheet swapping */
export function resolvedTheme() {
  const t = document.documentElement.dataset.theme || "system";
  if (t !== "system") return t;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function uuid() {
  return (crypto.randomUUID && crypto.randomUUID()) ||
    "x" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}
