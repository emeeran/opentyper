/* Library modals: prompts (CRUD + edit), agents (CRUD + skills), documents
   (CRUD + preview), memories (CRUD + toggle). */
"use strict";

import { api, apiJson } from "../api.js";
import { state } from "../state.js";
import { $, $$, autosize } from "../util.js";
import { toast } from "../toast.js";
import { openModal, closeModal, confirmDialog, fillTemplate, previewAttachment } from "../modal.js";

/* ---------- prompt library ---------- */

export async function promptsModal() {
  openModal(`
    <h2>Prompt library</h2>
    <div id="pl-list"></div>
    <h3 style="margin-top:16px">New prompt</h3>
    <div class="row"><input id="np-title" placeholder="Title" aria-label="Prompt title"></div>
    <div class="row"><textarea id="np-content" rows="4" placeholder="Content — use {{variables}} for fill-ins" aria-label="Prompt content"></textarea></div>
    <div class="row"><button class="primary" id="np-add">Add prompt</button></div>`);
  const renderList = () => {
    $("#pl-list").innerHTML = state.prompts.length
      ? state.prompts.map((p) => `
        <div class="list-item" data-id="${p.id}">
          <div class="grow"><strong></strong><div class="hint"></div></div>
          <button class="use">Use</button><button class="edit" title="Edit">✎</button>
          <button class="rm" title="Delete" aria-label="Delete prompt">🗑</button>
        </div>`).join("")
      : `<p class="hint">No prompts yet.</p>`;
    $$("#pl-list .list-item").forEach((el) => {
      const p = state.prompts.find((x) => x.id === el.dataset.id);
      $("strong", el).textContent = p.title;
      $(".hint", el).textContent = p.content.slice(0, 90);
      $(".use", el).onclick = async () => {
        const filled = await fillTemplate(p.content);
        if (filled == null) return; // cancelled
        closeModal();
        $("#input").value = filled; autosize($("#input")); $("#input").focus();
      };
      $(".edit", el).onclick = async () => {
        const vals = await editPromptDialog(p);
        if (!vals) return;
        await api(`/prompts/${p.id}`, { method: "PATCH", body: {
          title: vals["ep-title"], content: vals["ep-content"] } });
        state.prompts = await apiJson("/prompts");
        renderList();
        toast("Prompt updated");
      };
      $(".rm", el).onclick = async () => {
        if (!await confirmDialog(`Delete prompt “${p.title}”?`)) return;
        await api(`/prompts/${p.id}`, { method: "DELETE" });
        state.prompts = await apiJson("/prompts"); renderList();
      };
    });
  };
  renderList();
  $("#np-add").onclick = async () => {
    const title = $("#np-title").value.trim(), content = $("#np-content").value.trim();
    if (!title || !content) return toast("Title and content required", { isError: true });
    await api("/prompts", { method: "POST", body: { title, content } });
    state.prompts = await apiJson("/prompts");
    $("#np-title").value = ""; $("#np-content").value = "";
    renderList();
  };
}

function editPromptDialog(p) {
  return import("../modal.js").then(({ formDialog }) => formDialog("Edit prompt", [
    { id: "ep-title", label: "Title", value: p.title },
    { id: "ep-content", label: "Content", value: p.content },
  ], { okLabel: "Save" }));
}

/* ---------- agents + skills ---------- */

export async function agentsModal() {
  state.skills = await apiJson("/skills").catch(() => []);
  openModal(`
    <h2>AI agents</h2>
    <div id="ag-list"></div>
    <h3 style="margin-top:16px">New agent</h3>
    <div class="row"><input id="na-name" placeholder="Name" aria-label="Agent name"></div>
    <div class="row"><textarea id="na-sys" rows="3" placeholder="System prompt" aria-label="System prompt"></textarea></div>
    <div class="row"><select id="na-model" aria-label="Model"></select>
      <input id="na-temp" type="number" step="0.1" min="0" max="2" placeholder="temp" style="max-width:90px"></div>
    <div class="row"><button class="primary" id="na-add">Create agent</button></div>
    <h3 style="margin-top:16px">Skills</h3>
    <p class="hint">Skills are instruction packs (SKILL.md) attached to agents —
    never executed. Import one, then tick it on an agent above.</p>
    <div id="skill-list"></div>
    <div class="row"><input id="skill-file" type="file" accept=".md,.markdown,.txt,.zip" style="flex:1">
      <button id="skill-import">Import skill</button></div>`);
  const sel = $("#na-model");
  for (const p of state.models) {
    for (const m of p.models) {
      const o = document.createElement("option");
      o.value = JSON.stringify([p.provider_id, m]);
      o.textContent = `${p.name} / ${m}`;
      sel.append(o);
    }
  }
  const renderList = async () => {
    $("#ag-list").innerHTML = state.agents.length
      ? state.agents.map((a) => `
        <div class="list-item" data-id="${a.id}">
          <div class="grow"><strong></strong><div class="hint"></div></div>
          <button class="use">New chat</button><button class="edit" title="Edit">✎</button>
          <button class="rm" title="Delete" aria-label="Delete agent">🗑</button>
        </div>`).join("")
      : `<p class="hint">No agents yet — create one below, or start a plain chat.</p>`;
    $$("#ag-list .list-item").forEach((el) => {
      const a = state.agents.find((x) => x.id === el.dataset.id);
      $("strong", el).textContent = a.name;
      $(".hint", el).textContent = (a.model ? a.model + " · " : "") +
        (a.system_prompt || "").slice(0, 80);
      $(".use", el).onclick = async () => {
        closeModal();
        const c = await apiJson("/chats", { method: "POST", body: { agent_id: a.id } });
        state.chats = await apiJson("/chats");
        await import("../chat.js").then((m) => m.openChat(c.id));
      };
      $(".edit", el).onclick = () => editAgentDialog(a, renderList);
      $(".rm", el).onclick = async () => {
        if (!await confirmDialog(`Delete agent “${a.name}”?`)) return;
        await api(`/agents/${a.id}`, { method: "DELETE" });
        state.agents = await apiJson("/agents"); renderList();
      };
    });
  };
  const renderSkills = () => {
    $("#skill-list").innerHTML = state.skills.length
      ? state.skills.map((s) => `
        <div class="list-item" data-id="${s.id}">
          <div class="grow"><strong></strong><div class="hint"></div></div>
          <button class="rm" title="Delete skill" aria-label="Delete skill">🗑</button>
        </div>`).join("")
      : `<p class="hint">No skills imported.</p>`;
    $$("#skill-list .list-item").forEach((el) => {
      const s = state.skills.find((x) => x.id === el.dataset.id);
      $("strong", el).textContent = s.name;
      $(".hint", el).textContent = (s.content || "").slice(0, 90);
      $(".rm", el).onclick = async () => {
        await api(`/skills/${s.id}`, { method: "DELETE" });
        state.skills = await apiJson("/skills"); renderSkills();
      };
    });
  };
  renderList();
  renderSkills();
  $("#na-add").onclick = async () => {
    const name = $("#na-name").value.trim(), sys = $("#na-sys").value.trim();
    if (!name || !sys) return toast("Name and system prompt required", { isError: true });
    const [provider_id, model] = JSON.parse(sel.value);
    await api("/agents", { method: "POST", body: {
      name, system_prompt: sys, provider_id, model,
      temperature: $("#na-temp").value ? parseFloat($("#na-temp").value) : null } });
    state.agents = await apiJson("/agents");
    $("#na-name").value = ""; $("#na-sys").value = "";
    renderList();
  };
  $("#skill-import").onclick = async () => {
    const f = $("#skill-file").files[0];
    if (!f) return toast("Choose a SKILL.md or .zip first", { isError: true });
    const data = await new Promise((ok, err) => {
      const r = new FileReader();
      r.onload = () => ok(r.result.split(",")[1]); r.onerror = err;
      r.readAsDataURL(f);
    });
    try {
      await apiJson("/skills/import", { method: "POST", body: { filename: f.name, data } });
      state.skills = await apiJson("/skills");
      renderSkills();
      toast("Skill imported — attach it to an agent");
    } catch (e) { toast(`Import failed: ${e.message}`, { isError: true }); }
  };
}

async function editAgentDialog(a, done) {
  const { formDialog } = await import("../modal.js");
  const vals = await formDialog("Edit agent", [
    { id: "ea-name", label: "Name", value: a.name },
    { id: "ea-model", label: "Model (provider:model)", value: a.model || "" },
    { id: "ea-sys", label: "System prompt", value: a.system_prompt },
  ], { okLabel: "Save" });
  if (!vals) return;
  const [provider_id, model] = (vals["ea-model"] || ":").split(":");
  await api(`/agents/${a.id}`, { method: "PATCH", body: {
    name: vals["ea-name"], system_prompt: vals["ea-sys"],
    provider_id: provider_id || null, model: model || null,
    temperature: a.temperature, max_tokens: a.max_tokens,
    description: a.description } });
  state.agents = await apiJson("/agents");
  // skill checkboxes
  const attached = await apiJson(`/agents/${a.id}/skills`).catch(() => []);
  const { formDialog: fd } = await import("../modal.js");
  if (state.skills.length) {
    const fields = state.skills.map((s) => ({ id: "sk-" + s.id, label: s.name }));
    const skillVals = await new Promise((resolve) => {
      import("../modal.js").then(({ openModal, closeModal }) => {
        openModal(`<h2>Skills for ${a.name}</h2>
          ${state.skills.map((s) => `<div class="row checkrow" style="gap:8px">
            <input type="checkbox" id="sk-${s.id}" style="flex:0"
              ${attached.includes(s.id) ? "checked" : ""}>
            <label for="sk-${s.id}">${s.name}</label></div>`).join("")}
          <div class="row"><button class="primary" id="sk-ok">Save</button>
          <button id="sk-cancel">Cancel</button></div>`);
        $("#sk-cancel").onclick = () => { $("#modal")._onClose = null; closeModal(); resolve(null); };
        $("#sk-ok").onclick = () => {
          const out = state.skills.filter((s) => $(`#sk-${s.id}`).checked).map((s) => s.id);
          $("#modal")._onClose = null;
          closeModal();
          resolve(out);
        };
      });
    });
    if (skillVals) await api(`/agents/${a.id}/skills`, { method: "PUT",
      body: { skill_ids: skillVals } });
  }
  done && done();
  toast("Agent updated");
}

/* ---------- documents ---------- */

export async function documentsModal() {
  openModal(`<h2>Documents <span class="hint">(knowledge base)</span></h2>
    <p class="hint">Text files the AI can pull context from when a chat has
    "Use documents" enabled. Hybrid retrieval: keyword (FTS) + embeddings when
    a vector provider is configured in Settings.</p>
    <div id="doc-list"></div>
    <div class="row" style="margin-top:12px">
      <input id="doc-file" type="file" multiple
        accept=".txt,.md,.markdown,.csv,.json,.js,.py,.ts,.html,.css,.xml,.yaml,.yml,.log">
    </div>`);
  const renderList = async () => {
    const docs = await apiJson("/documents");
    $("#doc-list").innerHTML = docs.length
      ? docs.map((d) => `<div class="list-item" data-id="${d.id}">
          <div class="grow"><strong></strong><div class="hint"></div></div>
          <button class="view" title="View">👁</button>
          <button class="edit" title="Rename">✎</button>
          <button class="rm" title="Delete" aria-label="Delete document">🗑</button></div>`).join("")
      : `<p class="hint">No documents yet.</p>`;
    $$("#doc-list .list-item").forEach((el) => {
      const d = docs.find((x) => x.id === el.dataset.id);
      $("strong", el).textContent = d.name;
      $(".hint", el).textContent = `${(d.size || 0).toLocaleString()} chars · ${d.created_at?.slice(0, 10)}`;
      $(".view", el).onclick = () => previewAttachment({ kind: "text", name: d.name, text: d.content });
      $(".edit", el).onclick = async () => {
        const vals = await import("../modal.js").then(({ formDialog }) =>
          formDialog("Rename document", [{ id: "dr-name", label: "Name", value: d.name }],
            { okLabel: "Save" }));
        if (!vals) return;
        await api(`/documents/${d.id}`, { method: "PATCH",
          body: { name: vals["dr-name"], content: d.content } });
        renderList();
      };
      $(".rm", el).onclick = async () => {
        if (!await confirmDialog(`Delete document “${d.name}”?`)) return;
        await api(`/documents/${d.id}`, { method: "DELETE" });
        renderList();
      };
    });
  };
  renderList();
  $("#doc-file").onchange = async () => {
    for (const f of $("#doc-file").files) {
      if (f.size > 2 * 1024 * 1024) { toast(`${f.name}: too large (max 2 MB)`, { isError: true }); continue; }
      await api("/documents", { method: "POST",
        body: { name: f.name, content: await f.text() } });
    }
    $("#doc-file").value = "";
    renderList();
    toast("Documents added");
  };
}

/* ---------- memories ---------- */

export async function memoriesModal() {
  openModal(`<h2>Memory</h2>
    <div class="row checkrow" style="gap:8px">
      <input type="checkbox" id="mem-enabled" style="flex:0"
        ${state.settings.memory_enabled ? "checked" : ""}>
      <label for="mem-enabled">Remember facts about me across chats
        (extracted automatically after each reply; editable here)</label></div>
    <div id="mem-list"></div>
    <div class="row" style="margin-top:10px">
      <input id="mem-new" placeholder="Add a memory manually…" aria-label="New memory" style="flex:1">
      <button id="mem-add" class="primary">Add</button></div>`);
  const renderList = async () => {
    const mems = await apiJson("/memories");
    $("#mem-list").innerHTML = mems.length
      ? mems.map((m) => `<div class="list-item" data-id="${m.id}">
          <div class="grow"><span></span>
            <div class="hint">${m.source === "auto" ? "learned automatically" : "added by you"}</div></div>
          <button class="rm" title="Forget" aria-label="Forget memory">🗑</button></div>`).join("")
      : `<p class="hint">Nothing remembered yet.</p>`;
    $$("#mem-list .list-item").forEach((el) => {
      const m = mems.find((x) => x.id === el.dataset.id);
      $("span", el).textContent = m.content;
      $(".rm", el).onclick = async () => {
        await api(`/memories/${m.id}`, { method: "DELETE" });
        renderList();
      };
    });
  };
  renderList();
  $("#mem-enabled").onchange = async () => {
    state.settings = await apiJson("/settings", { method: "PUT",
      body: { memory_enabled: $("#mem-enabled").checked } });
    toast(state.settings.memory_enabled ? "Memory on" : "Memory off");
  };
  const add = async () => {
    const v = $("#mem-new").value.trim();
    if (v.length < 4) return toast("A memory needs at least 4 characters", { isError: true });
    await api("/memories", { method: "POST", body: { content: v } });
    $("#mem-new").value = "";
    renderList();
  };
  $("#mem-add").onclick = add;
  $("#mem-new").addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
}
