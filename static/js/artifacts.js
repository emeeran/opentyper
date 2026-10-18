/* Artifacts panel: versioned html/svg/markdown/code documents per chat, with a
   hand-edit tab and a one-shot AI rewrite (each save/rewrite = a new version).
   XSS surface: artifact content reaches the DOM only through the sandboxed
   iframe (originless srcdoc), markdown.js renderMd (DOMPurify), or textContent. */
"use strict";

import { api, apiJson } from "./api.js";
import { state } from "./state.js";
import { $, $$, download, copyText, timeAgo } from "./util.js";
import { toast } from "./toast.js";
import { confirmDialog } from "./modal.js";

const cache = new Map(); // chatId -> latest-per-root list (list view + dedupe)

function A() { return state.artifacts; }

function kindFromLang(lang) {
  const l = (lang || "").toLowerCase();
  if (["html", "htm", "xhtml"].includes(l)) return "html";
  if (l === "svg") return "svg";
  if (["md", "markdown", "mdx"].includes(l)) return "markdown";
  return "code";
}

function fileExt(kind, language) {
  if (kind === "html") return "html";
  if (kind === "svg") return "svg";
  if (kind === "markdown") return "md";
  return (language || "txt").replace(/[^\w.-]/g, "") || "txt";
}

function hashOf(str) { // FNV-1a — identity check for open-from-block dedupe
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16);
}

async function ensureList(chatId) {
  if (!cache.has(chatId)) cache.set(chatId, await apiJson(`/chats/${chatId}/artifacts`));
  return cache.get(chatId);
}

/* --- panes + tabs ------------------------------------------------------------ */

const PANES = ["ar-frame", "ar-md", "ar-code", "ar-versions", "ar-list", "ar-empty"];

function setPane(id) { for (const p of PANES) $("#" + p).hidden = p !== id; }

function setTab(name) {
  for (const t of ["preview", "code", "versions"]) {
    const on = t === name;
    const b = $("#ar-tab-" + t);
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  }
}

function previewPaneFor(row) {
  return row && (row.kind === "html" || row.kind === "svg") ? "ar-frame" : "ar-md";
}

function paintPreview() {
  const a = A();
  if (!a.row) return;
  const content = a.content;
  if (a.row.kind === "html" || a.row.kind === "svg") {
    $("#ar-frame").srcdoc = content;
    return;
  }
  const host = $("#ar-md");
  if (a.row.kind === "markdown") {
    // dynamic import keeps the module graph acyclic (markdown.js → artifacts.js)
    import("./markdown.js").then(({ renderMd }) => {
      if (A().content === content) host.replaceChildren(renderMd(content));
    }).catch(() => { host.textContent = content; });
  } else {
    const pre = document.createElement("pre");
    pre.style.cssText = "white-space:pre-wrap;overflow-wrap:break-word;margin:0";
    pre.textContent = content;
    host.replaceChildren(pre);
  }
}

function paint() {
  const a = A();
  if (!a.row) return;
  const atLatest = a.index === a.versions.length - 1;
  $("#ar-title").value = a.row.title;
  $("#ar-ver").textContent = `${a.index + 1}/${a.versions.length}`;
  $("#ar-prev").disabled = a.index <= 0;
  $("#ar-next").disabled = atLatest;
  setTab("preview");
  setPane(previewPaneFor(a.row));
  paintPreview();
  $("#ar-code").value = a.content;
}

async function loadVersion() {
  const a = A();
  const row = await apiJson(`/artifacts/${a.versions[a.index].id}`);
  a.currentId = row.id;
  a.row = row;
  a.content = row.content;
  paint();
}

async function paintList() {
  const a = A();
  setTab(null);
  setPane("ar-list");
  const host = $("#ar-list");
  host.replaceChildren();
  let list;
  try {
    list = await ensureList(a.listChatId ?? state.currentId);
  } catch (e) {
    host.textContent = e.message || "Could not load artifacts";
    return;
  }
  $("#ar-title").value = "Artifacts";
  $("#ar-ver").textContent = `${list.length}`;
  if (!list.length) { setPane("ar-empty"); return; }
  for (const art of list) {
    const b = document.createElement("button");
    b.className = "ar-item";
    const name = document.createElement("span");
    name.className = "grow";
    name.textContent = art.title;
    const meta = document.createElement("span");
    meta.className = "hint";
    meta.textContent = `v${art.version} · ${art.kind} · ${timeAgo(art.created_at)}`;
    b.append(name, meta);
    b.onclick = () => openArtifact(art.id).catch((e) => toast(e.message, true));
    host.append(b);
  }
}

async function paintVersions() {
  const a = A();
  if (!a.currentId) return;
  a.versions = await apiJson(`/artifacts/${a.currentId}/versions`);
  setTab("versions");
  setPane("ar-versions");
  const host = $("#ar-versions");
  host.replaceChildren();
  for (const v of [...a.versions].reverse()) {
    const b = document.createElement("button");
    b.className = "ar-item" + (v.id === a.currentId ? " on" : "");
    const name = document.createElement("span");
    name.className = "grow";
    name.textContent = `v${v.version} · ${v.title}`;
    const meta = document.createElement("span");
    meta.className = "hint";
    meta.textContent = `${(v.size / 1024).toFixed(1)} kB · ${timeAgo(v.created_at)}`;
    b.append(name, meta);
    b.onclick = () => pick(a.versions.findIndex((x) => x.id === v.id));
    host.append(b);
  }
  $("#ar-ver").textContent = `${a.index + 1}/${a.versions.length}`;
}

/* --- open / close -------------------------------------------------------------- */

export async function openArtifact(id) {
  const row = await apiJson(`/artifacts/${id}`);
  const a = A();
  const versions = await apiJson(`/artifacts/${id}/versions`);
  a.currentId = row.id;
  a.rootId = row.root_id;
  a.chatId = row.chat_id;
  a.versions = versions;
  a.index = versions.length - 1;
  a.row = row;
  a.content = row.content;
  show(true);
  paint();
}

export async function openArtifactFromBlock({ content, language }) {
  if (!state.currentId) return toast("Open a chat first", true);
  let list = [];
  try { list = await ensureList(state.currentId); } catch { /* non-fatal */ }
  const key = hashOf(content);
  const hit = list.find((x) => hashOf(x.content) === key);
  const row = hit || await apiJson(`/chats/${state.currentId}/artifacts`, {
    method: "POST",
    body: { content, language: language || null, kind: kindFromLang(language) },
  });
  cache.delete(state.currentId);
  await openArtifact(row.id);
}

export async function openArtifactsList() {
  if (!state.currentId) return toast("Open a chat first", true);
  A().listChatId = state.currentId;
  A().currentId = null;
  A().row = null;
  show(true);
  await paintList();
}

export function closeArtifacts() {
  A().controller?.abort();
  show(false);
}

function show(v) {
  $("#artifacts").hidden = !v;
  A().open = v;
}

export function updateArtifactsVisibility() {
  $("#btn-artifacts").hidden = !state.currentId;
  const a = A();
  if (!a.open || !a.chatId || a.chatId === state.currentId) return;
  // the chat behind the open artifact changed — fall back to the new chat's list
  a.currentId = null;
  a.row = null;
  a.listChatId = state.currentId;
  void paintList();
}

export function artifactsDirty() {
  const a = A();
  const ta = $("#ar-code");
  if (!a.open || !a.currentId || ta.hidden) return false;
  return ta.value !== a.content;
}

/* --- actions -------------------------------------------------------------------- */

async function guardDirty() {
  if (!artifactsDirty()) return true;
  return confirmDialog("Discard unsaved changes?",
    { confirmLabel: "Discard", danger: true });
}

export async function pick(i) {
  const a = A();
  if (!a.row || i === a.index || i < 0 || i >= a.versions.length) return;
  if (!(await guardDirty())) return;
  a.index = i;
  await loadVersion();
}

async function save() {
  const a = A();
  if (!a.currentId) return toast("Open an artifact first", true);
  const content = $("#ar-code").value;
  if (!content.trim()) return toast("Nothing to save", true);
  if (content.length > 200_000) return toast("Too long (max 200,000 chars)", true);
  try {
    const row = await apiJson(`/artifacts/${a.currentId}`, {
      method: "PUT",
      body: { content, title: $("#ar-title").value.trim() || null },
    });
    cache.delete(a.chatId);
    toast(`Saved version ${row.version}`);
    await openArtifact(row.id);
  } catch (e) { toast(e.message, true); }
}

async function rewrite(instruction) {
  const a = A();
  if (!a.currentId) return toast("Open an artifact first", true);
  const btn = $("#ar-rewrite");
  btn.classList.add("busy");
  btn.disabled = true;
  a.controller?.abort();
  a.controller = new AbortController();
  try {
    const res = await api(`/artifacts/${a.currentId}/rewrite`, {
      method: "POST",
      body: { instruction: instruction || null },
      signal: a.controller.signal,
    });
    const out = await res.json();
    cache.delete(a.chatId);
    toast(`Rewritten — version ${out.artifact.version}`);
    await openArtifact(out.artifact.id);
  } catch (e) {
    if (e.name !== "AbortError") toast(e.message, true);
  } finally {
    btn.classList.remove("busy");
    btn.disabled = false;
  }
}

async function destroy() {
  const a = A();
  if (!a.row) return;
  if (!(await confirmDialog(
    `Delete “${a.row.title}” and all ${a.versions.length} versions?`))) return;
  try {
    await api(`/artifacts/${a.currentId}`, { method: "DELETE" });
  } catch (e) { return toast(e.message, true); }
  cache.delete(a.chatId);
  toast("Artifact deleted");
  await openArtifactsList();
}

async function copyArt() {
  const text = $("#ar-code").hidden ? A().content : $("#ar-code").value;
  (await copyText(text)) ? toast("Copied") : toast("Copy failed", true);
}

function downloadArt() {
  const a = A();
  if (!a.row) return;
  const text = $("#ar-code").hidden ? a.content : $("#ar-code").value;
  const name = (a.row.title || "artifact").replace(/[^\w.-]+/g, "_").slice(0, 60);
  download(new Blob([text], { type: "text/plain" }),
    `${name}.${fileExt(a.row.kind, a.row.language)}`);
}

/* --- wiring ----------------------------------------------------------------------- */

export function wireArtifacts() {
  $("#btn-artifacts").onclick = () =>
    ($("#artifacts").hidden ? openArtifactsList() : closeArtifacts());
  $("#ar-close").onclick = closeArtifacts;
  $("#ar-prev").onclick = () => void pick(A().index - 1);
  $("#ar-next").onclick = () => void pick(A().index + 1);
  $("#ar-tab-preview").onclick = () => {
    if (!A().row) return;
    setTab("preview");
    setPane(previewPaneFor(A().row));
    paintPreview();
  };
  $("#ar-tab-code").onclick = () => { setTab("code"); setPane("ar-code"); };
  $("#ar-tab-versions").onclick = () => void paintVersions();
  $("#ar-save").onclick = () => void save();
  $("#ar-rewrite").onclick = () => void rewrite($("#ar-instruction").value.trim());
  $("#ar-instruction").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ar-rewrite").click(); }
  });
  for (const chip of $$(".ar-chip")) {
    chip.onclick = () => void rewrite(chip.dataset.i);
  }
  $("#ar-copy").onclick = () => void copyArt();
  $("#ar-download").onclick = downloadArt;
  $("#ar-delete").onclick = () => void destroy();
  $("#ar-title").addEventListener("input", () => { cache.delete(A().chatId); });
}
