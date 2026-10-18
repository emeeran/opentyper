/* Sidebar: chats, folders (drag & drop), pin, tags, archive, bulk selection. */
"use strict";

import { api, apiJson } from "./api.js";
import { state } from "./state.js";
import { $, $$ } from "./util.js";
import { toast as notify } from "./toast.js";
import { confirmDialog, formDialog } from "./modal.js";

let refreshAllFn = async () => {};
let openChatFn = async () => {};
let renderEmptyFn = () => {};
export function wireSidebar(refreshAll, openChat, renderEmpty) {
  refreshAllFn = refreshAll; openChatFn = openChat; renderEmptyFn = renderEmpty;
}

export function chatItem(c) {
  const el = document.createElement("div");
  el.className = "chat-item" + (c.id === state.currentId ? " active" : "") +
    (state.selected.has(c.id) ? " selected" : "");
  el.dataset.id = c.id;
  el.dataset.folder = c.folder_id || "";
  el.draggable = state.selectMode ? false : true;
  if (state.selectMode) {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.selected.has(c.id);
    cb.setAttribute("aria-label", `Select ${c.title}`);
    cb.onclick = (e) => e.stopPropagation();
    cb.onchange = () => {
      cb.checked ? state.selected.add(c.id) : state.selected.delete(c.id);
      el.classList.toggle("selected", cb.checked);
      updateBulkBar();
    };
    el.append(cb);
  }
  el.innerHTML = `<span class="name"></span>`;
  $(".name", el).textContent = c.title;
  if (c.pinned) {
    const pin = document.createElement("span");
    pin.className = "pin";
    pin.title = "Pinned";
    pin.innerHTML = `<svg class="icon" style="width:12px;height:12px"><use href="#i-star-filled"/></svg>`;
    el.append(pin);
  }
  if (c.temporary) {
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = "temp";
    el.append(flag);
  }
  if (c.archived) {
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.title = "Archived";
    flag.innerHTML = `<svg class="icon" style="width:12px;height:12px"><use href="#i-archive"/></svg>`;
    el.append(flag);
  }
  el.title = (c.tags || []).join(", ");
  el.onclick = (e) => { if (!e.target.closest(".del")) openChatFn(c.id); };
  el.ondragstart = (e) => { e.dataTransfer.setData("text/chat-id", c.id); };
  el.ondragover = (e) => e.preventDefault();
  const del = document.createElement("button");
  del.className = "del";
  del.title = "Delete";
  del.setAttribute("aria-label", `Delete ${c.title}`);
  del.innerHTML = `<svg class="icon" style="width:13px;height:13px"><use href="#i-trash"/></svg>`;
  del.onclick = async (e) => {
    e.stopPropagation();
    if (!await confirmDialog(`Delete “${c.title}”? This cannot be undone.`)) return;
    await api(`/chats/${c.id}`, { method: "DELETE" });
    if (state.currentId === c.id) state.currentId = null;
    await refreshAllFn();
    if (!state.currentId) renderEmptyFn();
  };
  el.append(del);
  return el;
}

function updateBulkBar() {
  const bar = $("#bulk-bar");
  if (!bar) return;
  bar.hidden = !state.selectMode;
  $("#bulk-count").textContent = `${state.selected.size} selected`;
}

export function renderSidebar() {
  const nav = $("#chat-nav");
  nav.textContent = "";
  let chats = state.chats;
  if (state.tagFilter) chats = chats.filter((c) => (c.tags || []).includes(state.tagFilter));
  if (state.tagFilter) {
    const head = document.createElement("div");
    head.className = "folder-head";
    head.innerHTML = `<svg class="icon" style="width:12px;height:12px"><use href="#i-tag"/></svg><span class="fname"></span>` +
      `<button class="icon-btn rmdir" title="Clear tag filter" aria-label="Clear filter">✕</button>`;
    $(".fname", head).textContent = state.tagFilter;
    $(".rmdir", head).onclick = () => { state.tagFilter = null; renderSidebar(); };
    nav.append(head);
  }
  const inFolder = new Set(chats.filter((c) => c.folder_id).map((c) => c.id));
  for (const f of state.folders) {
    const head = document.createElement("div");
    head.className = "folder-head";
    head.dataset.folder = f.id;
    head.innerHTML = `<svg class="icon" style="width:13px;height:13px"><use href="#i-folder"/></svg><span class="fname"></span>` +
      `<button class="icon-btn add" title="New chat in folder" aria-label="New chat in folder"><svg class="icon" style="width:12px;height:12px"><use href="#i-plus"/></svg></button>` +
      `<button class="icon-btn rmdir" title="Delete folder" aria-label="Delete folder"><svg class="icon" style="width:12px;height:12px"><use href="#i-trash"/></svg></button>`;
    $(".fname", head).textContent = f.name;
    $(".add", head).onclick = () => import("./chat.js").then((m) =>
      m.newChat({ folder_id: f.id, provider_id: f.provider_id,
        model: f.model, system_prompt: f.system_prompt, agent_id: f.agent_id }));
    $(".rmdir", head).onclick = async () => {
      if (!await confirmDialog(`Delete folder “${f.name}”? Chats are kept.`)) return;
      await api(`/folders/${f.id}`, { method: "DELETE" });
      await refreshAllFn();
    };
    // drag & drop chats into folders
    head.ondragover = (e) => { e.preventDefault(); head.classList.add("drop-target"); };
    head.ondragleave = () => head.classList.remove("drop-target");
    head.ondrop = async (e) => {
      e.preventDefault();
      head.classList.remove("drop-target");
      const id = e.dataTransfer.getData("text/chat-id");
      if (id) {
        await api(`/chats/${id}`, { method: "PATCH", body: { folder_id: f.id } });
        await refreshAllFn();
        notify(`Moved to ${f.name}`);
      }
    };
    nav.append(head);
    for (const c of chats.filter((c) => c.folder_id === f.id)) nav.append(chatItem(c));
  }
  const unfiled = chats.filter((c) => !inFolder.has(c.id));
  if (unfiled.length || !state.folders.length) {
    const head = document.createElement("div");
    head.className = "folder-head";
    head.innerHTML = `<span>Chats</span>` +
      `<button class="icon-btn mkfolder" title="New folder" aria-label="New folder"><svg class="icon" style="width:12px;height:12px"><use href="#i-folder-plus"/></svg></button>` +
      `<button class="icon-btn select-mode" title="Select chats" aria-label="Select chats"><svg class="icon" style="width:12px;height:12px"><use href="#i-check"/></svg></button>`;
    $(".mkfolder", head).onclick = async () => {
      const vals = await formDialog("New folder", [{ id: "nf-name", label: "Name" }]);
      if (vals && vals["nf-name"].trim()) {
        await api("/folders", { method: "POST", body: { name: vals["nf-name"].trim() } });
        await refreshAllFn();
      }
    };
    $(".select-mode", head).onclick = () => {
      state.selectMode = !state.selectMode;
      state.selected.clear();
      renderSidebar();
    };
    nav.append(head);
  }
  for (const c of unfiled) nav.append(chatItem(c));
  if (state.selectMode) {
    const bar = document.createElement("div");
    bar.id = "bulk-bar";
    bar.style.cssText = "display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap";
    bar.innerHTML = `<span id="bulk-count" class="hint"></span>
      <button id="bulk-move" class="ghost" style="padding:4px 8px;font-size:12px">Move…</button>
      <button id="bulk-archive" class="ghost" style="padding:4px 8px;font-size:12px">Archive</button>
      <button id="bulk-delete" class="ghost" style="padding:4px 8px;font-size:12px;color:var(--danger)">Delete</button>`;
    nav.append(bar);
    $("#bulk-move").onclick = pickFolderAndMove;
    $("#bulk-archive").onclick = () => bulkPatch({ archived: true });
    $("#bulk-delete").onclick = async () => {
      if (!await confirmDialog(`Delete ${state.selected.size} chat(s)? This cannot be undone.`)) return;
      for (const id of state.selected) {
        await api(`/chats/${id}`, { method: "DELETE" });
        if (state.currentId === id) state.currentId = null;
      }
      state.selected.clear();
      state.selectMode = false;
      await refreshAllFn();
      if (!state.currentId) renderEmptyFn();
    };
    updateBulkBar();
  }
}

async function pickFolderAndMove() {
  if (!state.folders.length) return notify("No folders yet — create one first");
  const f = state.folders[0];
  const vals = await formDialog(`Move ${state.selected.size} chat(s) to folder`,
    [{ id: "bf-folder", label: "Folder name", value: f.name }], { okLabel: "Move" });
  if (!vals) return;
  const folder = state.folders.find((x) => x.name === vals["bf-folder"].trim());
  if (!folder) return notify("No folder with that name", { isError: true });
  await bulkPatch({ folder_id: folder.id });
}

async function bulkPatch(body) {
  for (const id of state.selected) {
    await api(`/chats/${id}`, { method: "PATCH", body });
  }
  state.selected.clear();
  state.selectMode = false;
  await refreshAllFn();
}
