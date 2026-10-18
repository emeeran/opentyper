/* Boot + global events: shortcuts (rebindable), offline banner, service worker,
   side-chat selection chip, modal wiring. Entry point for all modules. */
"use strict";

import { apiJson } from "./api.js";
import { state, shortcuts } from "./state.js";

window.__state = state; // debugging/tests
import { $, $$, autosize } from "./util.js";
import { toast } from "./toast.js";
import { closeModal, openModal, fillTemplate, confirmDialog } from "./modal.js";
import { applyTheme, applyWide } from "./theme.js";
import { wireTtsBar } from "./tts.js";
import { wireArtifacts, closeArtifacts } from "./artifacts.js";
import * as chat from "./chat.js";
import { wireSidebar, renderSidebar } from "./sidebar.js";
import { showAuthModal, wireAuth } from "./modals/auth.js";
import { settingsModal } from "./modals/settings.js";
import { promptsModal, agentsModal, documentsModal, memoriesModal } from "./modals/library.js";
import { searchModal } from "./modals/search.js";
import { usageModal } from "./modals/dashboard.js";
import { shortcutsModal } from "./modals/shortcuts.js";

export async function refreshAll() {
  [state.chats, state.folders, state.prompts, state.agents, state.models,
    state.settings, state.providers] = await Promise.all([
    apiJson(`/chats?archived=${state.showArchived}`), apiJson("/folders"),
    apiJson("/prompts"), apiJson("/agents"), apiJson("/models"),
    apiJson("/settings"), apiJson("/providers"),
  ]);
  applyTheme();
  renderSidebar();
  chat.renderModelSelect();
}

/* ---------- keyboard shortcuts (rebindable) ---------- */

function comboFromEvent(e) {
  if (e.key === "?") return "?";
  const parts = [];
  if (e.metaKey || e.ctrlKey) parts.push("Mod");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey) parts.push("Shift");
  parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
  return parts.join("+");
}

const ACTIONS = {
  search: () => searchModal(),
  sidebar: () => $("#sidebar").classList.toggle("closed"),
  newChat: () => chat.newChat(),
  regenerate: () => chat.regenerate(),
  copyLast: () => {
    const msgs = $$("#messages .msg.assistant .body");
    if (msgs.length) {
      navigator.clipboard.writeText(msgs.at(-1).textContent);
      toast("Last reply copied");
    }
  },
  help: () => shortcutsModal(),
};

function handleShortcuts(e) {
  const s = shortcuts();
  const combo = comboFromEvent(e);
  for (const [action, bound] of Object.entries(s)) {
    if (bound.toLowerCase() !== combo.toLowerCase()) continue;
    // don't hijack typing for bare-key shortcuts like "?"
    if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) && !e.metaKey && !e.ctrlKey) {
      continue;
    }
    e.preventDefault();
    ACTIONS[action] && ACTIONS[action]();
    return true;
  }
  return false;
}

/* ---------- selection → side chat chip ---------- */

function wireSelectionChip() {
  const chip = document.createElement("button");
  chip.id = "ask-chip";
  chip.textContent = "Ask…";
  chip.className = "fab";
  chip.style.cssText = "position:fixed;top:60px;right:22px;bottom:auto;z-index:45;display:none;padding:6px 14px";
  chip.setAttribute("aria-label", "Ask about the selected text");
  document.body.append(chip);
  chip.onclick = () => {
    const sel = window.getSelection().toString().trim();
    chip.style.display = "none";
    chat.openSideChat(sel || state.sideChat.context);
  };
  const web = document.createElement("button");
  web.id = "web-chip";
  web.textContent = "Search web";
  web.className = "fab";
  web.style.cssText = chip.style.cssText + ";top:100px";
  web.setAttribute("aria-label", "Search the web for the selected text");
  document.body.append(web);
  web.onclick = () => {
    const sel = window.getSelection().toString().trim();
    web.style.display = "none";
    if (sel) window.open(
      "https://duckduckgo.com/?q=" + encodeURIComponent(sel.slice(0, 300)), "_blank");
  };
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    const within = sel.anchorNode && $("#messages").contains(sel.anchorNode);
    const on = within && sel.toString().trim().length > 2;
    chip.style.display = on ? "block" : "none";
    web.style.display = on ? "block" : "none";
  });
}

/* ---------- events ---------- */

function wireEvents() {
  $("#btn-new-chat").onclick = () => chat.newChat();
  $("#btn-search").onclick = searchModal;
  $("#btn-prompts").onclick = promptsModal;
  $("#btn-agents").onclick = agentsModal;
  $("#btn-docs").onclick = documentsModal;
  $("#btn-memory").onclick = memoriesModal;
  $("#btn-usage").onclick = usageModal;
  $("#btn-settings").onclick = () => settingsModal();
  $("#btn-send").onclick = chat.send;
  $("#btn-stop").onclick = () => state.controller?.abort();
  $("#btn-regen").onclick = chat.regenerate;
  $("#btn-finalize").onclick = chat.finalize;
  $("#btn-params").onclick = chat.toggleParams;
  $("#p-save").onclick = chat.saveParams;
  $("#p-archive").onclick = chat.toggleArchive;
  $("#p-pin").onclick = chat.togglePin;
  $("#p-share").onclick = () => chat.shareChat().catch((e) => toast(e.message, { isError: true }));
  $("#btn-fork").onclick = async () => {
    if (!state.currentId) return toast("Open a chat first");
    const r = await apiJson(`/chats/${state.currentId}/fork`, { method: "POST", body: {} });
    toast(`Forked (${r.copied_messages} messages)`);
    await refreshAll();
    await chat.openChat(r.id);
  };
  $("#btn-attach").onclick = () => $("#file-input").click();
  $("#file-input").onchange = async () => {
    await chat.handleFiles([...$("#file-input").files]);
    $("#file-input").value = "";
  };
  $("#btn-mic").onclick = chat.toggleMic;
  $("#btn-draw").onclick = chat.drawImage;
  $("#btn-fullscreen").onclick = chat.toggleFullscreen;
  $("#scroll-bottom").onclick = () => {
    const box = $("#messages");
    box.scrollTop = box.scrollHeight;
  };
  $("#messages").addEventListener("scroll", () => {
    const box = $("#messages");
    const bottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    $("#scroll-bottom").hidden = bottom;
  });
  $("#show-archived").onchange = async () => {
    state.showArchived = $("#show-archived").checked;
    await refreshAll();
  };
  $("#btn-del-chat").onclick = async () => {
    const c = chat.currentChat();
    if (!c || !c.id) return toast("No chat open");
    if (!await confirmDialog(`Delete “${c.title || "this chat"}”? This cannot be undone.`)) return;
    await apiJson(`/chats/${c.id}/share`, { method: "DELETE" }).catch(() => {});
    await fetch(`/api/chats/${c.id}`, { method: "DELETE" });
    state.currentId = null;
    await refreshAll();
    chat.renderEmpty();
    toast("Chat deleted");
  };

  $("#btn-side-toggle").onclick = () => $("#sidebar").classList.toggle("closed");
  $("#btn-side-close").onclick = () => $("#sidebar").classList.add("closed");

  $("#chat-title").onchange = async () => {
    const c = chat.currentChat();
    if (!c) return;
    const title = $("#chat-title").value.trim();
    if (!title) return;
    const updated = await apiJson(`/chats/${c.id}`, { method: "PATCH", body: { title } });
    Object.assign(c, updated);
    renderSidebar();
  };

  const input = $("#input");
  input.addEventListener("input", () => {
    autosize(input);
    if (state.currentId) localStorage.setItem(`draft:${state.currentId}`, input.value);
    if (input.value === "/") renderPromptMenu("");
    else if ($("#prompt-menu") && !$("#prompt-menu").hidden) $("#prompt-menu").hidden = true;
  });
  input.addEventListener("keydown", (e) => {
    const s = state.settings;
    const menuOpen = !$("#prompt-menu").hidden;
    if (menuOpen && (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter")) {
      e.preventDefault();
      promptMenuKeys(e.key);
      return;
    }
    const sendKey = s.enter_to_send === false
      ? (e.key === "Enter" && (e.metaKey || e.ctrlKey))
      : e.key === "Enter" && !e.shiftKey;
    if (sendKey) { e.preventDefault(); chat.send(); }
  });
  $("#model-select").onchange = async () => {
    const c = chat.currentChat();
    if (!c || !c.model) return;
    const [provider_id, model] = $("#model-select").value.split(":");
    const updated = await apiJson(`/chats/${c.id}`, { method: "PATCH", body: { provider_id, model } });
    Object.assign(c, updated);
  };

  // side chat panel
  $("#sc-close").onclick = chat.closeSideChat;
  $("#sc-send").onclick = chat.sideChatSend;
  $("#sc-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chat.sideChatSend(); }
  });

  $("#modal-back").onclick = (e) => { if (e.target.id === "modal-back") closeModal(); };

  document.addEventListener("keydown", (e) => {
    if (handleShortcuts(e)) return;
    const inField = ["INPUT", "TEXTAREA"].includes(e.target.tagName);
    if (e.key === "Escape") {
      closeModal();
      $("#prompt-menu").hidden = true;
      $("#params-pop").hidden = true;
      chat.closeSideChat();
      if (!$("#artifacts").hidden) closeArtifacts();
    } else if (e.key === "/" && !inField && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      $("#input").focus();
      $("#input").value = "/";
      renderPromptMenu("");
    } else if (e.key === "F" && (e.metaKey || e.ctrlKey) && e.shiftKey) {
      e.preventDefault();
      chat.toggleFullscreen();
    }
  });

  // prompt menu keyboard: Enter picks selection (handled above via promptMenuKeys)
  window.promptMenuKeys = promptMenuKeys;

  // drag-and-drop files onto the composer
  const composer = $("#composer");
  composer.addEventListener("dragover", (e) => { e.preventDefault(); });
  composer.addEventListener("drop", async (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) await chat.handleFiles([...e.dataTransfer.files]);
  });

  // warn before losing a temporary chat mid-typing
  window.addEventListener("beforeunload", (e) => {
    if (state.controller || input.value.trim()) {
      chat.discardTempChat();
      e.preventDefault();
      e.returnValue = "";
    }
  });
}

/* ---------- "/" prompt menu (listbox semantics + keyboard) ---------- */

let pmMatches = [], pmSel = -1;

function renderPromptMenu(filter) {
  const menu = $("#prompt-menu");
  menu.textContent = "";
  pmMatches = state.prompts.filter((p) =>
    !filter || p.title.toLowerCase().includes(filter.toLowerCase()));
  pmSel = -1;
  for (const p of pmMatches.slice(0, 12)) {
    const item = document.createElement("div");
    item.className = "pm-item";
    item.setAttribute("role", "option");
    item.innerHTML = `<span></span><small></small>`;
    $("span", item).textContent = p.title;
    $("small", item).textContent = p.content.slice(0, 80);
    item.onclick = () => pickPrompt(p);
    menu.append(item);
  }
  menu.hidden = pmMatches.length === 0;
}

async function pickPrompt(p) {
  $("#prompt-menu").hidden = true;
  const filled = await fillTemplate(p.content);
  if (filled == null) return;
  const input = $("#input");
  input.value = filled;
  autosize(input);
  input.focus();
}

function promptMenuKeys(key) {
  const items = $$("#prompt-menu .pm-item");
  if (!items.length) return;
  pmSel = key === "ArrowDown" ? (pmSel + 1) % items.length
    : key === "ArrowUp" ? (pmSel - 1 + items.length) % items.length : Math.max(pmSel, 0);
  items.forEach((el, i) => el.classList.toggle("sel", i === pmSel));
  items[pmSel].setAttribute("aria-selected", "true");
  if (key === "Enter") pickPrompt(pmMatches[pmSel]);
}

/* ---------- offline banner + service worker ---------- */

function wireOnline() {
  const banner = $("#offline-banner");
  const update = () => { banner.hidden = navigator.onLine; };
  addEventListener("online", update);
  addEventListener("offline", update);
  update();
}

async function wireServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  if (!state.settings.service_worker) {
    const reg = await navigator.serviceWorker.getRegistration();
    if (reg) await reg.unregister();
    return;
  }
  try {
    const rev = await apiJson("/health").then((h) => h.rev).catch(() => "1");
    await navigator.serviceWorker.register(`/sw.js?rev=${rev}`);
  } catch { /* registration failed — app still works online */ }
}

/* ---------- boot ---------- */

(async function boot() {
  applyWide();
  $("#messages").innerHTML =
    '<div class="skeleton"><i></i><i></i><i></i></div>';
  wireEvents();
  wireTtsBar();
  wireArtifacts();
  wireSidebar(refreshAll, chat.openChat.bind(chat), chat.renderEmpty);
  wireAuth(refreshAll);
  chat.setRefreshAll(refreshAll);
  chat.setRenderSidebar(renderSidebar);
  wireSelectionChip();
  try {
    await refreshAll();
  } catch (e) {
    if (e.message === "authentication required") return; // modal shown
    toast(e.message, { isError: true });
    return;
  }
  wireOnline();
  wireServiceWorker();
  chat.renderEmpty();
  const last = state.chats[0];
  const q = new URLSearchParams(location.search).get("q");
  if (last) await chat.openChat(last.id); // awaited: openChat restores drafts over the input
  if (q) { $("#input").value = q; autosize($("#input")); $("#input").focus(); }
})();
