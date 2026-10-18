/* Text-to-speech: browser Web Speech, Edge neural (server), ElevenLabs (optional).
   Settings persist in localStorage. */
"use strict";

import { $, fmtTime } from "./util.js";
import { toast } from "./toast.js";

const tts = { current: null };
const player = { audio: null };

export function stopAudio() {
  if (player.audio) {
    player.audio.pause();
    player.audio.removeAttribute("src");
    player.audio = null;
  }
  if (player.url) { URL.revokeObjectURL(player.url); player.url = null; }
  $("#tts-bar").hidden = true;
}

export function fmtVoiceSettings() {
  let s;
  try { s = JSON.parse(localStorage.getItem("voice") || "{}"); } catch { s = {}; }
  return { source: ["browser", "edge", "elevenlabs"].includes(s.source) ? s.source : "browser",
           voice: s.voice || "", edgeVoice: s.edgeVoice || "en-US-AriaNeural",
           elevenVoice: s.elevenVoice || "", rate: +s.rate || 1, pitch: s.pitch ?? 1,
           volume: s.volume ?? 1, auto: !!s.auto };
}

export function saveVoiceSettings() {
  if (!$("#s-voice") && !$("#s-tts-source")) return;
  localStorage.setItem("voice", JSON.stringify({
    source: $("#s-tts-source").value,
    voice: $("#s-voice").value,
    edgeVoice: $("#s-edge-voice").value,
    elevenVoice: $("#s-eleven-voice").value,
    rate: parseFloat($("#s-rate").value),
    pitch: parseFloat($("#s-pitch").value), volume: parseFloat($("#s-vol").value),
    auto: $("#s-autospeak").checked }));
}

/* Server voices (edge / elevenlabs): fetch first so an API error surfaces as
   one precise toast instead of the <audio> element's cryptic decode failure.
   Falls back to the browser's Web Speech voices so speech still works. */
async function playServerAudio(path, params, text, cfg, label) {
  stopAudio();
  speechSynthesis?.cancel?.();
  const seq = (player.seq = (player.seq || 0) + 1); // a newer click supersedes this fetch
  $("#tts-voice").textContent = params.voice;
  $("#tts-bar").hidden = false;
  let res;
  try {
    res = await fetch(`/api/${path}?${new URLSearchParams(params)}`);
  } catch (e) {
    return serverFallback(text, cfg, `${label} unreachable: ${e.message}`);
  }
  if (seq !== player.seq) return; // user clicked again while we were fetching
  if (!res.ok || !(res.headers.get("content-type") || "").startsWith("audio/")) {
    let detail = res.statusText || String(res.status);
    try { detail = (await res.json())?.detail || detail; } catch { /* not json */ }
    return serverFallback(text, cfg, `${label} failed: ${detail}`);
  }
  player.url = URL.createObjectURL(await res.blob());
  if (seq !== player.seq) { URL.revokeObjectURL(player.url); player.url = null; return; }
  const a = new Audio(player.url);
  player.audio = a;
  const seek = $("#tts-seek"), time = $("#tts-time");
  a.ontimeupdate = () => {
    if (a.duration) seek.value = (a.currentTime / a.duration) * 1000;
    time.textContent = `${fmtTime(a.currentTime)} / ${fmtTime(a.duration)}`;
  };
  a.onended = () => stopAudio();
  let dead = false;
  a.onerror = () => {
    if (dead) return;
    dead = true;
    toast(`${label} returned audio the browser can't play`, true);
    stopAudio();
  };
  a.play().catch((e) => {
    if (dead) return;
    dead = true;
    toast(`Playback blocked: ${e.message}`, true);
    stopAudio();
  });
}

function serverFallback(text, cfg, reason) {
  stopAudio();
  if ("speechSynthesis" in window) {
    toast(`${reason} — using the browser voice instead`);
    speakBrowser(text, cfg);
  } else {
    toast(reason, true);
  }
}

export function wireTtsBar() {
  $("#tts-play").onclick = () => {
    const a = player.audio;
    if (!a) return;
    if (a.paused) { a.play().catch(() => {}); }
    else { a.pause(); }
  };
  $("#tts-stop").onclick = stopAudio;
  $("#tts-seek").oninput = () => {
    const a = player.audio;
    if (a && a.duration) a.currentTime = ($("#tts-seek").value / 1000) * a.duration;
  };
}

function speakBrowser(text, cfg) {
  if (!("speechSynthesis" in window)) return toast("No speech support in this browser", true);
  if (player.audio) stopAudio();
  if (speechSynthesis.speaking || speechSynthesis.pending) {
    speechSynthesis.cancel();
    return;
  }
  const plain = text.replace(/```[\s\S]*?```/g, " code block ")
    .replace(/[#*_`>|]/g, "").slice(0, 5000);
  const u = new SpeechSynthesisUtterance(plain);
  if (cfg.voice) {
    const v = speechSynthesis.getVoices().find((v) => v.voiceURI === cfg.voice);
    if (v) u.voice = v;
  }
  u.rate = cfg.rate; u.pitch = cfg.pitch; u.volume = cfg.volume;
  u.onerror = (e) => toast(`Speech failed: ${e.error || "unknown"}`, true);
  tts.current = u; // prevent GC cancelling playback
  u.onend = () => { tts.current = null; };
  speechSynthesis.speak(u);
}

export function speak(text) {
  const cfg = fmtVoiceSettings();
  if (cfg.source === "edge") {
    if (player.audio) { stopAudio(); return; }
    return void playServerAudio("tts",
      { text: text.slice(0, 5000), voice: cfg.edgeVoice }, text, cfg, "Edge voices");
  }
  if (cfg.source === "elevenlabs") {
    if (player.audio) { stopAudio(); return; }
    if (!cfg.elevenVoice) return toast("Pick an ElevenLabs voice in Settings first", true);
    return void playServerAudio("tts/elevenlabs",
      { text: text.slice(0, 5000), voice: cfg.elevenVoice }, text, cfg, "ElevenLabs");
  }
  speakBrowser(text, cfg);
}
