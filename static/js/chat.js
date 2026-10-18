/* Chat view: model picker, messages (variants, actions, delete), SSE streaming
   (throttled mid-stream render), temp chats, finalize, side chat, wake lock. */
"use strict";

import { api, apiJson } from "./api.js";
import { state } from "./state.js";
import { $, $$, autosize, chime, clockOf, copyText, fmtTok, throttle } from "./util.js";
import { toast as notify } from "./toast.js";
import { openModal, closeModal, confirmDialog, formDialog, fillTemplate,
         previewAttachment, shareDialog } from "./modal.js";
import { renderMd, renderMdLite, finalizeMd } from "./markdown.js";
import { speak, stopAudio, fmtVoiceSettings } from "./tts.js";
import { updateArtifactsVisibility } from "./artifacts.js";

export function currentChat() {
  return state.chats.find((c) => c.id === state.currentId) ||
    (state.currentId ? { id: state.currentId } : null);
}

/* ---------- model picker ---------- */

export function renderModelSelect() {
  const sel = $("#model-select");
  sel.textContent = "";
  for (const p of state.models) {
    const g = document.createElement("optgroup");
    g.label = p.name + (p.has_key ? "" : " (no key)");
    const depre = p.deprecated || [];
    for (const m of p.models) {
      const o = document.createElement("option");
      o.value = `${p.provider_id}:${m}`;
      o.textContent = m + (depre.includes(m) ? " ⚠" : "");
      if (depre.includes(m)) {
        o.title = "Missing from the provider's live catalog — may be deprecated";
        o.classList.add("deprecated");
      }
      g.append(o);
    }
    sel.append(g);
  }
  const c = currentChat();
  if (c) sel.value = `${c.provider_id}:${c.model}`;
  renderSideChatModels();
}

function renderSideChatModels() {
  const sel = $("#sc-model");
  if (!sel || sel.options.length) return;
  for (const p of state.models) {
    for (const m of p.models) {
      const o = document.createElement("option");
      o.value = JSON.stringify([p.provider_id, m]);
      o.textContent = `${p.name} / ${m}`;
      sel.append(o);
    }
  }
}

/* ---------- chat view ---------- */

export function renderEmpty() {
  $("#messages").textContent = "";
  updateArtifactsVisibility();
  const hint = document.createElement("div");
  hint.id = "empty-hint";
  hint.innerHTML = `<h2>OpenTyper</h2>
    <p>Start a new chat, or press <kbd>⌘K</kbd> to search.</p>
    <div class="starters"></div>`;
  const starters = $(".starters", hint);
  for (const q of ["Explain a concept simply", "Review my code", "Brainstorm ideas"]) {
    const b = document.createElement("button");
    b.textContent = q;
    b.onclick = () => { $("#input").value = q + ": "; autosize($("#input")); $("#input").focus(); };
    starters.append(b);
  }
  $("#messages").append(hint);
  $("#chat-title").value = "";
  updateCtxMeter();
}

export async function openChat(id) {
  if (state.controller) state.controller.abort();
  state.currentId = id;
  const detail = await apiJson(`/chats/${id}`);
  renderSidebar(); // active highlight
  const c = currentChat();
  if (c && c.model && detail.model) { c.model = detail.model; c.provider_id = detail.provider_id; }
  $("#chat-title").value = detail.title;
  renderModelSelect();
  const box = $("#messages");
  box.textContent = "";
  const active = detail.messages.filter((m) => m.is_active !== false);
  const RENDER_CAP = 200;
  const hidden = Math.max(0, active.length - RENDER_CAP);
  if (hidden > 0) {
    const earlier = document.createElement("button");
    earlier.className = "ghost";
    earlier.style.cssText = "margin:0 auto;justify-content:center";
    earlier.textContent = `Load ${hidden} earlier message${hidden === 1 ? "" : "s"}`;
    earlier.onclick = () => {
      box.textContent = "";
      for (const mm of active) box.append(messageEl(mm, active));
      earlier.remove();
      box.scrollTop = 0;
    };
    box.append(earlier);
  }
  for (const m of active.slice(hidden)) box.append(messageEl(m, detail.messages));
  box.scrollTop = box.scrollHeight;
  const input = $("#input");
  input.value = localStorage.getItem(`draft:${id}`) || "";
  autosize(input);
  renderAttachments();
  updateCtxMeter(detail.messages);
  updateFinalizeVisibility();
  updateArtifactsVisibility();
  $("#btn-fullscreen")?.classList.remove("on");
  $("#composer").classList.remove("fullscreen");
  if (window.innerWidth <= 760) $("#sidebar").classList.add("closed");
  if (state.lastHit) {
    const hit = box.querySelector(`[data-id="${state.lastHit}"]`);
    if (hit) {
      hit.classList.add("search-hit");
      hit.scrollIntoView({ block: "center" });
      setTimeout(() => hit.classList.remove("search-hit"), 1600);
    }
    state.lastHit = null;
  }
}

function variantSwitcher(m, all) {
  const grp = all.filter((x) => x.variant_group && x.variant_group === m.variant_group);
  if (grp.length < 2) return null;
  const idx = grp.findIndex((x) => x.id === m.id);
  const nav = document.createElement("span");
  nav.className = "variant-nav";
  const mk = (dir, label, dis) => {
    const b = document.createElement("button");
    b.className = "icon-btn";
    b.setAttribute("aria-label", label);
    b.innerHTML = `<svg class="icon"><use href="#i-chevron-${dir}"/></svg>`;
    b.disabled = dis;
    b.onclick = async () => {
      const target = grp[(idx + (dir === "left" ? -1 : 1) + grp.length) % grp.length];
      await api(`/chats/${state.currentId}/messages/${target.id}/activate`,
        { method: "POST", body: { active: true } });
      await openChat(state.currentId);
    };
    return b;
  };
  nav.append(mk("left", "Previous variant", idx === 0),
    Object.assign(document.createElement("span"), { textContent: `${idx + 1}/${grp.length}` }),
    mk("right", "Next variant", idx === grp.length - 1));
  return nav;
}

export function messageEl(m, all = []) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}` + (m.interrupted ? " interrupted" : "") +
    (m.error ? " failed" : "");
  el.dataset.id = m.id || "";
  const who = document.createElement("div");
  who.className = "who";
  const name = document.createElement("span");
  name.className = "who-name";
  name.textContent = m.role === "user" ? "You" : (m.model || "Assistant");
  who.append(name);
  if (m.role === "assistant" && m.model && m.model.includes(":")) {
    const chip = document.createElement("span");
    chip.className = "who-model";
    chip.textContent = m.model;
    who.append(chip);
  }
  const ts = document.createElement("span");
  ts.className = "hint";
  ts.title = clockOf(m.created_at);
  ts.textContent = clockOf(m.created_at);
  who.append(ts);
  const vs = variantSwitcher(m, all || []);
  if (vs) who.append(vs);
  el.append(who);
  if (m.error) {
    const err = document.createElement("div");
    err.className = "error-line";
    const span = document.createElement("span");
    span.textContent = m.error;
    const retry = document.createElement("button");
    retry.textContent = "Retry";
    retry.onclick = () => regenerate();
    err.append(span, retry);
    el.append(err);
  }
  for (const img of m.images || []) {
    const im = document.createElement("img");
    im.className = "attach-img"; im.alt = "attached image";
    im.src = `/api/uploads/${img.file}`;
    im.onclick = () => window.open(im.src, "_blank");
    el.append(im);
  }
  const body = document.createElement("div");
  body.className = "body";
  if (m.role === "user") body.textContent = m.content;
  else body.append(renderMd(m.content, { host: body }));
  el.append(body);
  if (m.usage || m.prompt_tokens) {
    el.append(metaEl(m));
  }
  const actions = document.createElement("div");
  actions.className = "actions";
  const mkBtn = (label, fn, title) => {
    const b = document.createElement("button");
    b.className = "icon-btn"; b.title = title || label; b.setAttribute("aria-label", title || label);
    b.textContent = label;
    b.onclick = () => fn(b);
    return b;
  };
  if (m.role === "user" && m.content) {
    actions.append(mkBtn("Copy", async () => {
      await copyText(m.content) ? notify("Copied")
        : notify("Copy failed", { isError: true });
    }, "Copy message"));
  }
  if (m.role === "assistant" && m.content) {
    actions.append(
      mkBtn("Copy", async (b) => {
        await copyText(m.content)
          ? notify("Copied") : notify("Copy failed", { isError: true });
      }, "Copy reply"),
      mkBtn("Speak", () => speak(m.content), "Text-to-speech"),
      mkBtn("Edit", () => editMessageModal(m), "Edit this reply"),
    );
  } else if (m.id) {
    actions.append(mkBtn("Edit", () => editMessageModal(m),
      "Edit and regenerate from here"));
  }
  if (m.id && m.role === "user") {
    actions.append(mkBtn("Branch", async () => {
      const r = await apiJson(`/chats/${state.currentId}/fork`,
        { method: "POST", body: { message_id: m.id } });
      notify(`Branched (${r.copied_messages} messages)`);
      await refreshAll();
      await openChat(r.id);
    }, "Fork from this message"));
  }
  if (m.id) {
    actions.append(mkBtn("Delete", async () => {
      if (!await confirmDialog("Delete this message?")) return;
      await api(`/chats/${state.currentId}/messages/${m.id}`, { method: "DELETE" });
      el.remove();
      updateCtxMeter();
    }, "Delete message"));
  }
  if (actions.children.length) el.append(actions);
  return el;
}

export function metaEl(m) {
  const meta = document.createElement("div");
  meta.className = "meta";
  const cost = m.cost != null ? ` · $${m.cost}` : "";
  const est = (m.usage?.estimated ?? m.estimated) ? "~" : "";
  const cached = m.usage?.cache_read_tokens ? ` (${fmtTok(m.usage.cache_read_tokens)} cached)` : "";
  meta.textContent = `${est}⇅ ${fmtTok(m.usage?.prompt_tokens ?? m.prompt_tokens)}+`
    + `${fmtTok(m.usage?.completion_tokens ?? m.completion_tokens)} tok${cached}${cost}`;
  return meta;
}

function editMessageModal(m) {
  openModal(`<h2>Edit message</h2>
    <textarea id="edit-content" rows="8"></textarea>
    ${m.role === "user" ? `<p class="hint">The reply regenerates from here.
      The previous reply is kept as a variant you can switch back to.</p>` : ""}
    <div class="row"><button class="primary" id="edit-save">Save${m.role === "user" ? " & re-run" : ""}</button>
    <button id="edit-cancel">Cancel</button></div>`);
  const ta = $("#edit-content");
  ta.value = m.content;
  ta.focus();
  $("#edit-cancel").onclick = closeModal;
  $("#edit-save").onclick = async () => {
    const content = ta.value.trim();
    if (!content) return;
    if (m.role === "user") {
      closeModal();
      const box = $("#messages");
      box.textContent = ""; // server truncates; re-render after stream
      await stream(`/chats/${state.currentId}/messages/${m.id}/rerun`, { content });
    } else {
      await api(`/chats/${state.currentId}/messages/${m.id}`, { method: "PATCH", body: { content } });
      closeModal();
      await openChat(state.currentId);
      notify("Reply updated");
    }
  };
}

/* ---------- context meter / wake lock ---------- */

export function updateCtxMeter(messages = null) {
  const el = $("#ctx-meter");
  if (!el) return;
  const c = currentChat();
  if (!c || !c.model) { el.textContent = ""; return; }
  let toks = 0;
  if (messages) {
    toks = messages.filter((m) => m.is_active !== false)
      .reduce((s, m) => s + Math.ceil((m.content || "").length / 4), 0);
  } else {
    toks = parseInt(el.dataset.toks || "0", 10);
  }
  el.dataset.toks = toks;
  el.textContent = toks > 500 ? `~${(toks / 1000).toFixed(1)}k tok` : "";
}

let wakeLock = null;
async function keepAwake(on) {
  try {
    if (on && "wakeLock" in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    } else if (!on && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch { /* denied or unsupported */ }
}

/* ---------- SSE ---------- */

async function readSSE(res, on) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", eventName = "message";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let data = null;
      eventName = "message";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) data = JSON.parse(line.slice(5));
      }
      if (data) await on(eventName, data);
    }
  }
}

/* ---------- streaming a reply ---------- */

async function stream(path, body, { reappend = true } = {}) {
  const box = $("#messages");
  const empty = $("#empty-hint"); if (empty) empty.remove();
  const userEl = body && body.content !== undefined
    ? messageEl({ role: "user", content: body.content })
    : null;
  if (userEl && reappend) box.append(userEl);

  const bubbles = new Map(); // model tag -> {el, bodyEl, acc}
  const bubble = (tag) => {
    const el = messageEl({ role: "assistant", content: "", model: tag || "" });
    box.append(el);
    const b = { el, bodyEl: $(".body", el), acc: "" };
    bubbles.set(tag, b);
    atBottom() && (box.scrollTop = box.scrollHeight);
    return b;
  };
  // pre-key the main bubble with the chat's provider:model so the server's
  // meta event adopts it instead of spawning a duplicate bubble
  const c0 = currentChat();
  const expectedTag = c0 && c0.provider_id && c0.model
    ? `${c0.provider_id}:${c0.model}` : null;
  let cur = bubble(expectedTag);

  // throttled mid-stream markdown (skips highlight/math/mermaid until done)
  let finished = false;
  const paint = throttle(() => {
    if (finished) return; // trailing timer must not clobber the final render
    for (const b of bubbles.values()) {
      b.bodyEl.textContent = "";
      b.bodyEl.append(renderMdLite(b.acc));
      b.bodyEl.classList.add("cursor-blink");
    }
  }, 150);

  state.controller = new AbortController();
  $("#btn-send").hidden = true;
  $("#btn-stop").hidden = false;
  keepAwake(true);
  let heard = false, aborted = false;
  try {
    const res = await fetch("/api" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal: state.controller.signal,
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    await readSSE(res, async (event, data) => {
      if (event === "meta") {
        if (!bubbles.has(data.model) && bubbles.has(null)
            && !bubbles.get(null).acc) {
          bubbles.set(data.model, bubbles.get(null)); // adopt untitled bubble
          bubbles.delete(null);
        }
        cur = bubbles.has(data.model) ? bubbles.get(data.model) : bubble(data.model);
        $(".who-name", cur.el).textContent = data.model || "Assistant";
        if (data.usage) {
          $(".meta", cur.el)?.remove();
          cur.el.append(metaEl({ usage: data.usage, cost: data.usage.cost }));
        }
      } else if (event === "delta") {
        if (data.model && !bubbles.has(data.model) && bubbles.has(null)
            && !bubbles.get(null).acc) {
          bubbles.set(data.model, bubbles.get(null));
          bubbles.delete(null);
        }
        if (data.model) cur = bubbles.has(data.model) ? bubbles.get(data.model) : bubble(data.model);
        heard = true;
        cur.acc += data.text;
        paint();
        atBottom() && (box.scrollTop = box.scrollHeight);
      } else if (event === "error") {
        if (data.model && !bubbles.has(data.model) && bubbles.has(null)
            && !bubbles.get(null).acc) {
          bubbles.set(data.model, bubbles.get(null));
          bubbles.delete(null);
        }
        if (data.model) cur = bubbles.has(data.model) ? bubbles.get(data.model) : bubble(data.model);
        cur.acc += `\n\n**Error:** ${data.message}`;
        cur.bodyEl.textContent = "";
        cur.bodyEl.append(renderMdLite(cur.acc));
        notify(`Reply failed: ${data.message}`, { isError: true, ttl: 8000 });
      } else if (event === "done") {
        if (data.interrupted) for (const b of bubbles.values()) b.el.classList.add("interrupted");
      }
    });
  } catch (e) {
    if (e.name === "AbortError") aborted = true;
    else {
      cur.acc += `\n\n**Error:** ${e.message}`;
      cur.bodyEl.textContent = "";
      cur.bodyEl.append(renderMdLite(cur.acc));
    }
  } finally {
    state.controller = null;
    $("#btn-send").hidden = false;
    $("#btn-stop").hidden = true;
    keepAwake(false);
    finished = true;
    for (const b of bubbles.values()) {
      b.bodyEl.classList.remove("cursor-blink");
      finalizeMd(b.bodyEl, b.acc);
    }
    if (heard && document.hidden) chime();
    if (heard && fmtVoiceSettings().auto) speak(cur.acc || "");
    // normalize state + sidebar/title without clobbering the streamed view;
    // regenerate/rerun/abort re-render from the server (variants, interrupt flag)
    if (aborted || (body && body.images && body.images.length) ||
        path.endsWith("/rerun") || path.endsWith("/regenerate")) {
      await refreshAllRef();
      if (state.currentId) await openChat(state.currentId);
    } else {
      const prev = $("#messages").dataset.toks;
      await refreshAllRef();
      if (state.currentId) {
        $("#chat-title").value = state.chats.find((c) => c.id === state.currentId)?.title
          || $("#chat-title").value;
        updateCtxMeter();
      }
    }
  }
}

/* circular dep: sidebar.refreshAll lives there; resolved via injection */
let refreshAllRef = () => {};
export function setRefreshAll(fn) { refreshAllRef = fn; }
let renderSidebarRef = () => {};
export function setRenderSidebar(fn) { renderSidebarRef = fn; }
function renderSidebar() { renderSidebarRef(); }
async function refreshAll() { await refreshAllRef(); }
export { refreshAll };

function atBottom() {
  const box = $("#messages");
  return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
}

/* ---------- send / regenerate / finalize ---------- */

export async function send() {
  if (state.controller) return notify("Still streaming — press Stop first", { isError: true });
  const input = $("#input");
  let text = input.value.trim();
  const files = state.pendingFiles;
  if (!text && !files.length) return;
  if (files.length) {
    text += (text ? "\n\n" : "") + files.map((f) =>
      `--- attached file: ${f.name} ---\n\`\`\`\n${f.content}\n\`\`\``).join("\n\n");
  }
  if (!text) return;
  if (!state.currentId) {
    const sel = $("#model-select").value;
    if (!sel) return notify("Add an API key in Settings first", { isError: true });
    const [provider_id, model] = sel.split(":");
    const c = await apiJson("/chats", { method: "POST",
      body: { provider_id, model, temporary: state.tempChat || undefined } });
    state.currentId = c.id;
    await refreshAllRef();
    renderSidebarRef();
  }
  input.value = "";
  localStorage.removeItem(`draft:${state.currentId}`);
  autosize(input);
  const body = { content: text };
  if (state.pendingImages.length) body.images = state.pendingImages.slice();
  if (state.useKnowledge) body.use_knowledge = true;
  state.pendingImages = [];
  state.pendingFiles = [];
  renderAttachments();
  await stream(`/chats/${state.currentId}/messages`, body);
}

export async function regenerate() {
  if (!state.currentId) return;
  const detail = await apiJson(`/chats/${state.currentId}`);
  const active = detail.messages.filter((m) => m.is_active !== false);
  const last = active[active.length - 1];
  if (!last || last.role !== "assistant") return notify("Nothing to regenerate");
  // old reply stays server-side as an inactive variant; drop it from view
  const box = $("#messages");
  if (box.lastElementChild?.classList.contains("assistant")) box.lastElementChild.remove();
  await stream(`/chats/${state.currentId}/regenerate`);
}

export async function finalize() {
  const c = currentChat();
  if (!c) return;
  const btn = $("#btn-finalize");
  btn.disabled = true;
  try {
    await apiJson(`/chats/${c.id}/finalize`, { method: "POST", body: {} });
    notify("Replies synthesized");
    await refreshAllRef();
    await openChat(c.id);
  } catch (e) {
    notify(`Finalize failed: ${e.message}`, { isError: true });
  } finally {
    btn.disabled = false;
  }
}

/* ---------- attachments ---------- */

export function renderAttachments() {
  const box = $("#attachments");
  box.textContent = "";
  const has = state.pendingImages.length || state.pendingFiles.length;
  box.hidden = !has;
  for (const [i, src] of state.pendingImages.entries()) {
    const chip = document.createElement("span");
    chip.className = "attach-chip";
    const im = document.createElement("img");
    im.src = src; im.alt = "image attachment";
    const x = document.createElement("button");
    x.innerHTML = `<svg class="icon" style="width:12px;height:12px"><use href="#i-x"/></svg>`;
    x.className = "icon-btn";
    x.setAttribute("aria-label", "Remove image");
    x.onclick = () => { state.pendingImages.splice(i, 1); renderAttachments(); };
    chip.append(im, x);
    box.append(chip);
  }
  for (const [i, f] of state.pendingFiles.entries()) {
    const chip = document.createElement("span");
    chip.className = "attach-chip file";
    chip.append(Object.assign(document.createElement("span"),
      { textContent: f.name }));
    const x = document.createElement("button");
    x.innerHTML = `<svg class="icon" style="width:12px;height:12px"><use href="#i-x"/></svg>`;
    x.className = "icon-btn";
    x.setAttribute("aria-label", "Remove file");
    x.onclick = () => { state.pendingFiles.splice(i, 1); renderAttachments(); };
    chip.append(x);
    box.append(chip);
  }
}

const TEXT_EXTS = /\.(txt|md|markdown|csv|json|js|py|ts|html|css|xml|ya?ml|log)$/i;

export async function handleFiles(files) {
  for (const f of files) {
    if (f.type.startsWith("image/")) {
      if (state.pendingImages.length >= 4) { notify("Max 4 images", { isError: true }); continue; }
      const url = await new Promise((ok, err) => {
        const r = new FileReader();
        r.onload = () => ok(r.result); r.onerror = err;
        r.readAsDataURL(f);
      });
      state.pendingImages.push(url);
    } else if (f.size <= 512 * 1024 && (TEXT_EXTS.test(f.name) || f.type.startsWith("text/"))) {
      state.pendingFiles.push({ name: f.name, content: await f.text() });
    } else if (/\.pdf$/i.test(f.name) && f.size <= 20 * 1024 * 1024) {
      // extract text server-side, attach as a text file
      try {
        const data = await new Promise((ok, err) => {
          const r = new FileReader();
          r.onload = () => ok(r.result.split(",")[1]); r.onerror = err;
          r.readAsDataURL(f);
        });
        const r = await apiJson("/extract", { method: "POST",
          body: { filename: f.name, data } });
        state.pendingFiles.push({ name: f.name.replace(/\.pdf$/i, ".txt"),
          content: r.text });
      } catch (e) {
        notify(`${f.name}: ${e.message}`, { isError: true, ttl: 8000 });
      }
    } else {
      notify(`Skipped ${f.name}: not an image or small text file`, { isError: true });
    }
  }
  renderAttachments();
}

/* ---------- mic ---------- */

let recognition = null;
export function toggleMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return notify("Voice input not supported in this browser", { isError: true });
  if (recognition) { recognition.stop(); return; }
  recognition = new SR();
  recognition.interimResults = true;
  const input = $("#input");
  let base = input.value;
  recognition.onresult = (e) => {
    let t = "";
    for (const res of e.results) t += res[0].transcript;
    input.value = (base ? base + " " : "") + t;
    autosize(input);
  };
  recognition.onend = () => {
    recognition = null;
    $("#btn-mic").classList.remove("rec");
  };
  recognition.onerror = () => notify("Voice input error", { isError: true });
  recognition.start();
  $("#btn-mic").classList.add("rec");
}

/* ---------- per-chat params ---------- */

export function toggleParams() {
  const pop = $("#params-pop");
  if (!pop.hidden) { pop.hidden = true; return; }
  const c = currentChat();
  if (!c || !c.model) return notify("Open a chat first");
  $("#p-temp").value = c.temperature ?? "";
  $("#p-maxtok").value = c.max_tokens ?? "";
  $("#p-topp").value = c.top_p ?? "";
  $("#p-context").value = c.context_limit ?? "";
  $("#p-freq").value = c.frequency_penalty ?? "";
  $("#p-pres").value = c.presence_penalty ?? "";
  $("#p-effort").value = c.reasoning_effort || "";
  $("#p-lang").value = c.output_language || "";
  $("#p-stream").checked = c.stream !== false;
  $("#p-knowledge").checked = !!c.use_knowledge;
  $("#p-cache").checked = !!c.cache_prompt;
  $("#p-tempchat").checked = !!c.temporary || state.tempChat;
  $("#p-tags").value = (c.tags || []).join(", ");
  $("#p-extras").value = (c.extra_models || []).join(", ");
  $("#p-sys").value = c.system_prompt ?? "";
  $("#p-archive").textContent = c.archived ? "Unarchive" : "Archive";
  $("#p-pin").textContent = c.pinned ? "Unpin" : "Pin";
  renderFolderOptions();
  pop.hidden = false;
}

function renderFolderOptions() {
  const sel = $("#p-folder");
  sel.textContent = "";
  const none = document.createElement("option");
  none.value = ""; none.textContent = "— none —";
  sel.append(none);
  for (const f of state.folders) {
    const o = document.createElement("option");
    o.value = f.id; o.textContent = f.name;
    sel.append(o);
  }
  const c = currentChat();
  if (c) sel.value = c.folder_id || "";
}

export async function saveParams() {
  const c = currentChat();
  if (!c) return;
  const body = {};
  const num = (id) => $(id).value !== "" ? parseFloat($(id).value) : undefined;
  body.system_prompt = $("#p-sys").value;       // "" clears
  body.folder_id = $("#p-folder").value;        // "" clears
  const temp = num("#p-temp"), mt = num("#p-maxtok");
  if (temp !== undefined) body.temperature = temp;
  if (mt !== undefined) body.max_tokens = parseInt(mt, 10);
  const topP = num("#p-topp");
  if (topP !== undefined) body.top_p = topP;
  body.context_limit = parseInt($("#p-context").value || "0", 10) || 0;
  const freq = num("#p-freq"), pres = num("#p-pres");
  if (freq !== undefined) body.frequency_penalty = freq;
  if (pres !== undefined) body.presence_penalty = pres;
  body.reasoning_effort = $("#p-effort").value || null;
  body.output_language = $("#p-lang").value.trim() || "";
  body.stream = $("#p-stream").checked;
  body.use_knowledge = $("#p-knowledge").checked;
  body.cache_prompt = $("#p-cache").checked;
  body.temporary = $("#p-tempchat").checked;
  body.tags = $("#p-tags").value.split(",").map((t) => t.trim()).filter(Boolean);
  body.extra_models = $("#p-extras").value.split(",").map((t) => t.trim())
    .filter((t) => t.includes(":"));
  const updated = await apiJson(`/chats/${c.id}`, { method: "PATCH", body });
  Object.assign(c, updated);
  state.useKnowledge = updated.use_knowledge;
  $("#params-pop").hidden = true;
  renderSidebarRef();
  updateFinalizeVisibility();
  notify("Chat settings saved");
}

export async function toggleArchive() {
  const c = currentChat();
  if (!c) return;
  const updated = await apiJson(`/chats/${c.id}`,
    { method: "PATCH", body: { archived: !c.archived } });
  Object.assign(c, updated);
  $("#params-pop").hidden = true;
  await refreshAllRef();
  notify(c.archived ? "Chat archived" : "Chat unarchived");
}

export async function togglePin() {
  const c = currentChat();
  if (!c || !c.id) return;
  const updated = await apiJson(`/chats/${c.id}`,
    { method: "PATCH", body: { pinned: !c.pinned } });
  Object.assign(c, updated);
  renderSidebarRef();
  notify(c.pinned ? "Pinned" : "Unpinned");
}

export async function shareChat() {
  const c = currentChat();
  if (!c) return;
  const r = await apiJson(`/chats/${c.id}/share`, { method: "POST" });
  shareDialog(location.origin + r.url, async () => {
    await api(`/chats/${c.id}/share`, { method: "DELETE" });
    notify("Share link revoked");
  });
}

export function updateFinalizeVisibility() {
  const btn = $("#btn-finalize");
  if (!btn) return;
  const c = currentChat();
  btn.hidden = !(c && (c.extra_models || []).length);
}

/* ---------- new chat / temp chat ---------- */

export async function newChat(extra = {}) {
  if (state.controller) state.controller.abort();
  const selVal = $("#model-select").value;
  let body = {};
  if (extra.agent_id) body = { agent_id: extra.agent_id };
  else if (extra.folder_id) {
    body = { folder_id: extra.folder_id };
    if (extra.provider_id && extra.model) { body.provider_id = extra.provider_id; body.model = extra.model; }
  } else if (selVal) {
    const [provider_id, model] = selVal.split(":");
    body = { provider_id, model };
  }
  if (state.tempChat && !extra.folder_id) body.temporary = true;
  if (!body.agent_id && !body.provider_id) {
    return notify("Add an API key in Settings first", { isError: true });
  }
  try {
    const c = await apiJson("/chats", { method: "POST", body });
    state.currentId = c.id;
    await refreshAllRef();
    renderEmpty();
    const detail = await apiJson(`/chats/${c.id}`);
    $("#chat-title").value = detail.title;
    if (detail.temporary) showTempBanner();
    if (detail.system_prompt) toggleParams(); // pre-filled: let user see it
  } catch (e) { notify(e.message, { isError: true }); }
}

function showTempBanner() {
  const box = $("#messages");
  const banner = document.createElement("div");
  banner.className = "temp-banner";
  banner.style.cssText = "margin:0 auto var(--s-3);display:flex;gap:10px;align-items:center;" +
    "justify-content:center;background:var(--info-bg);color:var(--info);" +
    "border-radius:var(--radius-2);padding:8px 14px;font-size:14px";
  banner.innerHTML = `Temporary chat — deleted when you switch away.
    <button class="primary" style="padding:4px 12px">Keep</button>`;
  $("button", banner).onclick = async () => {
    await api(`/chats/${state.currentId}`, { method: "PATCH", body: { temporary: false } });
    banner.remove();
    await refreshAllRef();
    notify("Chat kept");
  };
  box.append(banner);
}

export async function discardTempChat() {
  const c = currentChat();
  if (c && (c.temporary || (state.tempChat && c.id && !state.chats.some(
      (x) => x.id === c.id)))) {
    try { await api(`/chats/${c.id}`, { method: "DELETE" }); } catch { /* gone */ }
  }
}

/* ---------- side chat ---------- */

export function openSideChat(context) {
  state.sideChat.context = context || "";
  $("#side-chat").hidden = false;
  $(".sc-context", $("#side-chat")).textContent = context
    ? `“${context.slice(0, 80)}${context.length > 80 ? "…" : ""}”` : "";
  const c = currentChat();
  if (c && c.provider_id && c.model) {
    const want = JSON.stringify([c.provider_id, c.model]);
    for (const o of $("#sc-model").options) if (o.value === want) $("#sc-model").value = want;
  }
  $("#sc-input").focus();
}

export function closeSideChat() {
  $("#side-chat").hidden = true;
  state.sideChat.controller?.abort();
}

export async function sideChatSend() {
  const input = $("#sc-input");
  const text = input.value.trim();
  if (!text || state.sideChat.controller) return;
  const [provider_id, model] = JSON.parse($("#sc-model").value || "null") || [null, null];
  if (!provider_id) return notify("Add an API key first", { isError: true });
  input.value = "";
  const box = $("#sc-messages");
  const userEl = document.createElement("div");
  userEl.className = "sc-msg user";
  userEl.textContent = text;
  box.append(userEl);
  const replyEl = document.createElement("div");
  replyEl.className = "sc-msg assistant";
  box.append(replyEl);
  box.scrollTop = box.scrollHeight;
  state.sideChat.controller = new AbortController();
  let acc = "";
  const paint = throttle(() => {
    replyEl.textContent = "";
    replyEl.append(renderMdLite(acc));
    box.scrollTop = box.scrollHeight;
  }, 120);
  try {
    const res = await fetch("/api/side-chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text, context: state.sideChat.context || undefined,
        provider_id, model }),
      signal: state.sideChat.controller.signal });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    await readSSE(res, async (event, data) => {
      if (event === "delta") { acc += data.text; paint(); }
      else if (event === "error") { acc += `\n\n**Error:** ${data.message}`; }
    });
  } catch (e) {
    if (e.name !== "AbortError") acc += `\n\n**Error:** ${e.message}`;
  } finally {
    state.sideChat.controller = null;
    replyEl.textContent = "";
    finalizeMd(replyEl, acc);
    box.scrollTop = box.scrollHeight;
  }
}

/* ---------- image generation ---------- */

export async function drawImage() {
  const c = currentChat();
  const imageProviders = state.models.filter((p) => p.has_key);
  if (!imageProviders.length) return notify("Add an API key in Settings first", { isError: true });
  const fields = [
    { id: "draw-prompt", label: "Prompt", placeholder: "a watercolor fox in a misty forest" },
    { id: "draw-model", label: "Model (provider:model)", value: c && c.provider_id
        ? `${c.provider_id}:${c.model}` : "openai:gpt-image-1" },
    { id: "draw-size", label: "Size (optional)", placeholder: "1024x1024" },
  ];
  const vals = await formDialog("Generate an image", fields, { okLabel: "Generate" });
  if (!vals || !vals["draw-prompt"].trim()) return;
  const [provider_id, model] = (vals["draw-model"] || "").split(":");
  if (!model) return notify("Model must be provider:model", { isError: true });
  notify("Generating image…");
  try {
    const r = await apiJson("/images/generate", { method: "POST", body: {
      provider_id, model, prompt: vals["draw-prompt"].trim(),
      chat_id: c && c.id && !c.temporary ? c.id : undefined,
      size: vals["draw-size"].trim() || undefined } });
    notify("Image ready");
    if (c && c.id) await openChat(c.id);
  } catch (e) {
    notify(`Image generation failed: ${e.message}`, { isError: true, ttl: 8000 });
  }
}

/* ---------- fullscreen composer ---------- */

export function toggleFullscreen() {
  $("#composer").classList.toggle("fullscreen");
  $("#input").focus();
}
