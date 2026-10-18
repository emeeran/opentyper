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

function playServerAudio(kind, text, voice) {
  stopAudio();
  speechSynthesis?.cancel?.();
  const a = new Audio(`/api/tts/${kind}?text=${encodeURIComponent(text.slice(0, 5000))}`
    + `&voice=${encodeURIComponent(voice)}`);
  player.audio = a;
  const bar = $("#tts-bar");
  const seek = $("#tts-seek"), time = $("#tts-time");
  $("#tts-voice").textContent = voice;
  bar.hidden = false;
  a.ontimeupdate = () => {
    if (a.duration) seek.value = (a.currentTime / a.duration) * 1000;
    time.textContent = `${fmtTime(a.currentTime)} / ${fmtTime(a.duration)}`;
  };
  a.onended = () => stopAudio();
  a.onerror = () => {
    toast(kind === "elevenlabs"
      ? "ElevenLabs speech failed — check the key in Settings"
      : "Speech failed — is the server's edge-tts able to reach the service?", true);
    stopAudio();
  };
  a.play().catch((e) => toast(`Playback blocked: ${e.message}`, true));
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

export function speak(text) {
  const cfg = fmtVoiceSettings();
  if (cfg.source === "edge") {
    if (player.audio) { stopAudio(); return; }
    return playServerAudio("tts", text, cfg.edgeVoice);
  }
  if (cfg.source === "elevenlabs") {
    if (player.audio) { stopAudio(); return; }
    if (!cfg.elevenVoice) return toast("Pick an ElevenLabs voice in Settings first", true);
    return playServerAudio("tts/elevenlabs", text, cfg.elevenVoice);
  }
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
