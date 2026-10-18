/* Settings: General, Providers (create form — fixed endpoint), Shortcuts
   (rebinding), Internal prompts, Data (export/import incl. ChatGPT/Claude,
   share management), Password. */
"use strict";

import { api, apiJson } from "../api.js";
import { state } from "../state.js";
import { $, $$, download } from "../util.js";
import { toast } from "../toast.js";
import { openModal, closeModal, confirmDialog, formDialog } from "../modal.js";
import { fmtVoiceSettings, saveVoiceSettings, speak, stopAudio } from "../tts.js";
import { applyTheme, applyWide } from "../theme.js";

export function settingsModal(tab = "general") {
  const s = state.settings;
  const tabs = ["general", "providers", "shortcuts", "prompts", "data", "password"];
  openModal(`
    <h2>Settings</h2>
    <div class="tabs">${tabs.map((t) =>
      `<button data-tab="${t}" class="${t === tab ? "on" : ""}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
    <div id="tab-body"></div>`);
  $$("#modal .tabs button").forEach((b) => b.onclick = () => settingsModal(b.dataset.tab));
  const body = $("#tab-body");

  if (tab === "general") {
    body.innerHTML = `
      <div class="row"><label>Theme</label>
        <select id="s-theme">
          <option value="system">System</option><option value="light">Light</option>
          <option value="dark">Dark</option>
        </select></div>
      <div class="row"><label>Enter to send</label>
        <input id="s-enter" type="checkbox" style="flex:0"></div>
      <div class="row"><label>Wide screen layout</label>
        <input id="s-wide" type="checkbox" style="flex:0"></div>
      <div class="row"><label>Offline cache (service worker)</label>
        <input id="s-sw" type="checkbox" style="flex:0"></div>
      <div class="card">
        <div class="row"><label>Voice source</label>
          <select id="s-tts-source">
            <option value="browser">Browser voices</option>
            <option value="edge">Edge neural voices</option>
            <option value="elevenlabs">ElevenLabs</option>
          </select></div>
        <div class="row" id="row-browser-voice"><label>Voice</label>
          <select id="s-voice"></select></div>
        <div class="row" id="row-edge-voice" hidden><label>Edge voice</label>
          <select id="s-edge-voice"></select>
          <button id="s-edge-reload" class="icon-btn" title="Reload voice list">↻</button></div>
        <div class="row" id="row-eleven-voice" hidden><label>ElevenLabs key</label>
          <input id="s-eleven-key" type="password" placeholder="${s.elevenlabs_hint || "not set"}">
          <button id="s-eleven-save">Save key</button></div>
        <div class="row" id="row-eleven-voice2" hidden><label>ElevenLabs voice</label>
          <select id="s-eleven-voice"></select></div>
        <div class="row"><label>Rate</label>
          <input id="s-rate" type="range" min="0.5" max="2" step="0.1" style="flex:1">
          <span class="hint" id="s-rate-v"></span></div>
        <div class="row"><label>Pitch</label>
          <input id="s-pitch" type="range" min="0" max="2" step="0.1" style="flex:1">
          <span class="hint" id="s-pitch-v"></span></div>
        <div class="row"><label>Volume</label>
          <input id="s-vol" type="range" min="0" max="1" step="0.05" style="flex:1">
          <span class="hint" id="s-vol-v"></span></div>
        <div class="row"><label>Speak replies automatically</label>
          <input id="s-autospeak" type="checkbox" style="flex:0"></div>
        <div class="row"><button id="s-voicetest">🔊 Test voice</button>
          <button id="s-voicestop">■ Stop</button></div>
        <p class="hint">Browser TTS on Linux needs speech-dispatcher for voices;
        Edge voices run server-side; ElevenLabs needs a key.</p>
      </div>
      <div class="row"><label>Model prices (JSON)</label></div>
      <div class="row"><textarea id="s-prices" rows="5"
        placeholder='{"gpt-4o": {"in": 2.5, "out": 10}}'></textarea></div>
      <p class="hint">USD per million tokens, longest-prefix match on model id.
        Leave empty to hide costs.</p>
      <div class="card">
        <p class="hint" style="margin-top:0">Embedding-backed knowledge retrieval (optional).
        Uses any OpenAI-compatible /embeddings endpoint to add semantic search to
        documents — hybrid with keyword search.</p>
        <div class="row"><label>Embeddings provider</label>
          <select id="s-emb-provider"><option value="">— none —</option></select></div>
        <div class="row"><label>Embeddings model</label>
          <input id="s-emb-model" placeholder="text-embedding-3-small" value="${s.embedding_model || ""}"></div>
      </div>
      <div class="row"><button class="primary" id="s-save">Save</button></div>`;
    $("#s-theme").value = s.theme || "system";
    $("#s-enter").checked = !!s.enter_to_send;
    $("#s-wide").checked = localStorage.getItem("wide") === "1";
    $("#s-sw").checked = !!s.service_worker;
    $("#s-prices").value = JSON.stringify(s.model_prices || {}, null, 2);
    const embSel = $("#s-emb-provider");
    for (const p of state.providers) {
      const o = document.createElement("option");
      o.value = p.id; o.textContent = p.name;
      embSel.append(o);
    }
    embSel.value = s.embedding_provider_id || "";

    const cfg = fmtVoiceSettings();
    const voiceSel = $("#s-voice");
    const fillVoices = () => {
      const voices = speechSynthesis.getVoices();
      voiceSel.textContent = "";
      const none = document.createElement("option");
      none.value = ""; none.textContent = voices.length ? "Browser default" : "No voices found";
      voiceSel.append(none);
      for (const v of voices) {
        const o = document.createElement("option");
        o.value = v.voiceURI;
        o.textContent = `${v.name} (${v.lang})`;
        voiceSel.append(o);
      }
      voiceSel.value = cfg.voice;
    };
    if ("speechSynthesis" in window) {
      fillVoices();
      speechSynthesis.onvoiceschanged = fillVoices;
    }
    const fillEdgeVoices = async () => {
      const sel = $("#s-edge-voice");
      sel.textContent = "";
      try {
        const voices = await apiJson("/tts/voices");
        voices.sort((a, b) => a.lang.localeCompare(b.lang) || a.id.localeCompare(b.id));
        for (const v of voices) {
          const o = document.createElement("option");
          o.value = v.id;
          o.textContent = `${v.id} — ${v.lang}${v.gender ? " · " + v.gender : ""}`;
          sel.append(o);
        }
        sel.value = cfg.edgeVoice;
        if (!sel.value) sel.selectedIndex = 0;
      } catch (e) {
        const o = document.createElement("option");
        o.value = cfg.edgeVoice; o.textContent = `unavailable: ${e.message}`;
        sel.append(o);
      }
    };
    const fillElevenVoices = async () => {
      const sel = $("#s-eleven-voice");
      sel.textContent = "";
      try {
        const voices = await apiJson("/tts/elevenvoices");
        for (const v of voices) {
          const o = document.createElement("option");
          o.value = v.id;
          o.textContent = `${v.name}${v.lang ? " · " + v.lang : ""}`;
          sel.append(o);
        }
        if (cfg.elevenVoice) sel.value = cfg.elevenVoice;
      } catch (e) {
        const o = document.createElement("option");
        o.value = ""; o.textContent = `unavailable: ${e.message}`;
        sel.append(o);
      }
    };
    $("#s-tts-source").value = cfg.source;
    const syncSourceRows = () => {
      const v = $("#s-tts-source").value;
      $("#row-browser-voice").hidden = v !== "browser";
      $("#row-edge-voice").hidden = v !== "edge";
      $("#row-eleven-voice").hidden = v !== "elevenlabs";
      $("#row-eleven-voice2").hidden = v !== "elevenlabs";
    };
    $("#s-tts-source").onchange = syncSourceRows;
    syncSourceRows();
    fillEdgeVoices();
    fillElevenVoices();
    $("#s-edge-reload").onclick = fillEdgeVoices;
    $("#s-eleven-save").onclick = async () => {
      const v = $("#s-eleven-key").value.trim();
      if (!v) return;
      const r = await apiJson("/settings/secret", { method: "PUT",
        body: { name: "elevenlabs", value: v } });
      toast(`Key saved (${r.hint})`);
    };
    const ranges = [["#s-rate", "#s-rate-v", cfg.rate, (x) => x.toFixed(1) + "×"],
                    ["#s-pitch", "#s-pitch-v", cfg.pitch, (x) => x.toFixed(1)],
                    ["#s-vol", "#s-vol-v", cfg.volume, (x) => Math.round(x * 100) + "%"]];
    for (const [rid, vid, val, fmt] of ranges) {
      $(rid).value = val;
      $(vid).textContent = fmt(val);
      $(rid).oninput = () => { $(vid).textContent = fmt(parseFloat($(rid).value)); };
    }
    $("#s-autospeak").checked = cfg.auto;
    $("#s-voicetest").onclick = () => {
      saveVoiceSettings();
      speak("This is how OpenTyper will read replies aloud.");
    };
    $("#s-voicestop").onclick = () => { stopAudio(); speechSynthesis.cancel(); };

    $("#s-save").onclick = async () => {
      let prices;
      try {
        prices = JSON.parse($("#s-prices").value || "{}");
      } catch { return toast("Prices must be valid JSON", { isError: true }); }
      state.settings = await apiJson("/settings", { method: "PUT", body: {
        theme: $("#s-theme").value, enter_to_send: $("#s-enter").checked,
        model_prices: prices, service_worker: $("#s-sw").checked,
        embedding_provider_id: embSel.value || "",
        embedding_model: $("#s-emb-model").value.trim() } });
      localStorage.setItem("wide", $("#s-wide").checked ? "1" : "0");
      saveVoiceSettings();
      applyWide();
      applyTheme();
      toast("Saved");
    };
  }

  if (tab === "providers") {
    body.innerHTML = state.providers.map((p) => `
      <div class="card" data-pid="${p.id}">
        <div class="row"><strong>${p.name}</strong>
          <span class="hint">${p.is_builtin ? "built-in" : "custom"} · ${p.kind}</span></div>
        <div class="row"><label>API key</label>
          <input class="key" type="password" placeholder="${p.key_hint || "not set"}" aria-label="API key for ${p.name}">
          <button class="savekey">Save key</button>
          ${p.key_hint ? `<button class="delkey">Remove</button>` : ""}</div>
        <div class="row">
          <button class="fetchmodels" title="GET /models from the provider">⤓ Fetch latest models</button>
          <button class="testconn">⚡ Test</button>
          <span class="hint testresult">${p.key_hint ? "" : "set a key first"}</span>
        </div>
        <div class="row"><label>Base URL</label>
          <input class="baseurl" placeholder="${p.base_url || "default"}" aria-label="Base URL">
          <button class="savebase">Save URL</button></div>
        <div class="row"><label>${p.is_builtin ? "Extra models" : "Models"} (comma-sep)</label>
          <input class="models" value="${(p.models || []).join(", ")}" aria-label="Models">
          <button class="savemodels">Save models</button></div>
      </div>`).join("") + `
      <div class="row"><button id="add-provider">＋ Add custom provider</button></div>
      <div class="row"><input id="env-file" type="file" accept=".env,.txt,.local">
        <button id="env-import">⤓ Import keys from .env</button></div>
      <p class="hint" id="env-result">Recognized names: OPENAI_API_KEY, ANTHROPIC_API_KEY,
        GOOGLE_API_KEY / GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY,
        DEEPSEEK_API_KEY, MISTRAL_API_KEY, XAI_API_KEY, PERPLEXITY_API_KEY,
        HUGGINGFACE_API_KEY / HF_TOKEN. Values are stored encrypted; the file
        is parsed in the browser and never uploaded.</p>`;
    $$("#tab-body .card").forEach((card) => {
      const pid = card.dataset.pid;
      $(".fetchmodels", card).onclick = async () => {
        const out = $(".testresult", card);
        out.textContent = "fetching…";
        try {
          const r = await apiJson(`/providers/${pid}/refresh-models`, { method: "POST" });
          out.textContent = `fetched ${r.fetched} models (${r.models.length} total)`;
          state.providers = await apiJson("/providers");
          const { renderModelSelect } = await import("../chat.js");
          renderModelSelect();
        } catch (err) { out.textContent = `error: ${err.message}`; }
      };
      $(".testconn", card).onclick = async () => {
        const out = $(".testresult", card);
        out.textContent = "testing…";
        try {
          const r = await apiJson(`/providers/${pid}/test`, { method: "POST" });
          out.textContent = r.ok
            ? `✓ ${r.model} replied in ${r.latency_ms} ms: “${r.reply}”`
            : `✗ ${r.error}`;
        } catch (err) { out.textContent = `✗ ${err.message}`; }
      };
      $(".savekey", card).onclick = async () => {
        const v = $(".key", card).value.trim();
        if (!v) return;
        await api(`/providers/${pid}/key`, { method: "PUT", body: { api_key: v } });
        toast("Key saved"); await refreshAll(); settingsModal("providers");
      };
      const del = $(".delkey", card);
      if (del) del.onclick = async () => {
        await api(`/providers/${pid}/key`, { method: "DELETE" });
        toast("Key removed"); await refreshAll(); settingsModal("providers");
      };
      $(".savebase", card).onclick = async () => {
        await api(`/providers/${pid}`, { method: "PUT", body: { base_url: $(".baseurl", card).value.trim() } });
        toast("Base URL saved");
      };
      const sm = $(".savemodels", card);
      if (sm) sm.onclick = async () => {
        const models = $(".models", card).value.split(",").map((x) => x.trim()).filter(Boolean);
        await api(`/providers/${pid}`, { method: "PUT", body: { models } });
        toast("Models saved"); await refreshAll();
      };
    });
    // fixed: POST /api/providers (was /api/providers/create — 404)
    $("#add-provider").onclick = async () => {
      const vals = await formDialog("Add custom provider", [
        { id: "cp-name", label: "Name", placeholder: "My proxy" },
        { id: "cp-kind", label: "API kind", value: "openai", placeholder: "openai / anthropic / google" },
        { id: "cp-base", label: "Base URL", placeholder: "OpenAI-style incl. /v1" },
      ], { okLabel: "Create" });
      if (!vals || !vals["cp-name"].trim()) return;
      const kind = (vals["cp-kind"] || "openai").trim().toLowerCase();
      if (!["openai", "anthropic", "google"].includes(kind)) {
        return toast("API kind must be openai, anthropic or google", { isError: true });
      }
      await api("/providers", { method: "POST", body: {
        name: vals["cp-name"].trim(), kind,
        base_url: vals["cp-base"].trim() || null } });
      await refreshAll();
      settingsModal("providers");
      toast("Provider created — now set its API key");
    };
    const ENV_KEYS = {
      OPENAI_API_KEY: "openai", ANTHROPIC_API_KEY: "anthropic",
      GOOGLE_API_KEY: "google", GEMINI_API_KEY: "google",
      OPENROUTER_API_KEY: "openrouter", GROQ_API_KEY: "groq",
      DEEPSEEK_API_KEY: "deepseek", MISTRAL_API_KEY: "mistral",
      XAI_API_KEY: "xai", XAI_TOKEN: "xai", PERPLEXITY_API_KEY: "perplexity",
      HUGGINGFACE_API_KEY: "huggingface", HUGGINGFACEHUB_API_TOKEN: "huggingface",
      HF_TOKEN: "huggingface",
    };
    $("#env-import").onclick = async () => {
      const f = $("#env-file").files[0];
      if (!f) return toast("Choose a .env file first", { isError: true });
      const imported = [], unknown = new Set();
      try {
        for (const line of (await f.text()).split("\n")) {
          const m = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$/);
          if (!m) continue;
          const [, name, raw] = m;
          const value = raw.replace(/^["']|["']$/g, "");
          if (!value) continue;
          const pid = ENV_KEYS[name];
          if (!pid) {
            if (/KEY|TOKEN|SECRET/.test(name)) unknown.add(name);
            continue;
          }
          await api(`/providers/${pid}/key`, { method: "PUT", body: { api_key: value } });
          imported.push(`${name}→${pid}`);
        }
      } catch (e) {
        return toast(`Import failed: ${e.message}`, { isError: true });
      }
      $("#env-result").textContent = imported.length
        ? `Imported ${imported.length} key(s): ${imported.join(", ")}` +
          (unknown.size ? ` · unrecognized: ${[...unknown].join(", ")}` : "")
        : "No known provider keys found in that file.";
      toast(imported.length ? `Imported ${imported.length} API key(s)` : "Nothing imported",
            !imported.length);
      $("#env-file").value = "";
      await refreshAll();
    };
  }

  if (tab === "shortcuts") {
    const actions = [
      ["search", "Search"], ["sidebar", "Toggle sidebar"], ["newChat", "New chat"],
      ["regenerate", "Regenerate"], ["copyLast", "Copy last reply"], ["help", "Shortcuts help"],
    ];
    const cur = { search: "Mod+K", sidebar: "Mod+B", newChat: "Mod+Alt+N",
      regenerate: "Mod+Alt+E", copyLast: "Mod+Alt+C", help: "?",
      ...(state.settings.shortcuts || {}) };
    body.innerHTML = actions.map(([id, label]) => `
      <div class="list-item"><span class="grow">${label}</span>
        <kbd data-action="${id}">${cur[id]}</kbd>
        <button class="rebind" data-action="${id}">Change</button></div>`).join("") +
      `<div class="row"><button id="sc-reset">Reset to defaults</button></div>`;
    $$("#tab-body .rebind").forEach((b) => {
      b.onclick = () => {
        const kbd = $(`kbd[data-action="${b.dataset.action}"]`);
        kbd.textContent = "press keys…";
        const handler = (e) => {
          e.preventDefault();
          e.stopPropagation();
          document.removeEventListener("keydown", handler, true);
          if (e.key === "Escape") { kbd.textContent = cur[b.dataset.action]; return; }
          let combo;
          if (e.key === "?") combo = "?";
          else {
            const parts = [];
            if (e.metaKey || e.ctrlKey) parts.push("Mod");
            if (e.altKey) parts.push("Alt");
            if (e.shiftKey) parts.push("Shift");
            parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
            combo = parts.join("+");
          }
          cur[b.dataset.action] = combo;
          kbd.textContent = combo;
        };
        document.addEventListener("keydown", handler, true);
      };
    });
    $("#sc-reset").onclick = async () => {
      state.settings = await apiJson("/settings", { method: "PUT", body: { shortcuts: {} } });
      settingsModal("shortcuts");
      toast("Shortcuts reset");
    };
    // persist on tab switch away / close via modal onClose
    $("#modal")._onClose = async () => {
      const defaults = { search: "Mod+K", sidebar: "Mod+B", newChat: "Mod+Alt+N",
        regenerate: "Mod+Alt+E", copyLast: "Mod+Alt+C", help: "?" };
      const changed = {};
      let has = false;
      for (const [k, v] of Object.entries(cur)) {
        if (v !== defaults[k]) { changed[k] = v; has = true; }
      }
      state.settings = await apiJson("/settings", { method: "PUT",
        body: { shortcuts: changed } }).catch(() => state.settings);
    };
  }

  if (tab === "prompts") {
    body.innerHTML = `
      <p class="hint">Internal prompts used by app features. Saved with the other
      settings when you leave this tab or press Save.</p>
      <div class="row"><label>Finalize multi-model replies</label></div>
      <div class="row"><textarea id="ip-finalize" rows="3"
        placeholder="Synthesize these AI replies into one best answer…">${s.finalize_prompt || ""}</textarea></div>
      <div class="row"><label>Memory extraction</label></div>
      <div class="row"><textarea id="ip-memory" rows="4"
        placeholder="Extract durable facts about the user as a JSON array…">${s.memory_extract_prompt || ""}</textarea></div>
      <div class="row"><label>Artifact rewrite (Artifacts panel)</label></div>
      <div class="row"><textarea id="ip-rewrite" rows="4"
        placeholder="You are rewriting an existing artifact…">${s.artifact_rewrite_prompt || ""}</textarea></div>
      <div class="row"><button class="primary" id="ip-save">Save</button>
        <button id="ip-reset">Reset to defaults</button></div>`;
    $("#ip-save").onclick = async () => {
      state.settings = await apiJson("/settings", { method: "PUT", body: {
        finalize_prompt: $("#ip-finalize").value.trim() || null,
        memory_extract_prompt: $("#ip-memory").value.trim() || null,
        artifact_rewrite_prompt: $("#ip-rewrite").value.trim() || null } });
      toast("Internal prompts saved");
    };
    $("#ip-reset").onclick = async () => {
      state.settings = await apiJson("/settings", { method: "PUT", body: {
        finalize_prompt: "", memory_extract_prompt: "",
        artifact_rewrite_prompt: "" } });
      settingsModal("prompts");
      toast("Reset");
    };
  }

  if (tab === "data") {
    body.innerHTML = `
      <div class="card">
        <div class="row"><button id="exp-json" class="primary">Export all chats (JSON)</button>
        <button id="exp-md">Export as Markdown</button>
        <button id="exp-html">Export as HTML (print to PDF)</button></div>
        <div class="row"><input id="imp-file" type="file" accept=".json">
        <button id="imp-go">Import</button></div>
        <p class="hint">Import accepts an OpenTyper export, a ChatGPT
        <code>conversations.json</code>, or a Claude <code>chats.json</code> export
        (auto-detected).</p>
      </div>
      <div class="card"><h3 style="margin-top:0">Active share links</h3>
        <div id="shares-list"></div></div>`;
    const downloadAs = async (format) => {
      const res = await api("/export", { method: "POST", body: { format } });
      download(await res.blob(),
        `opentyper-export.${format === "markdown" ? "md" : format}`);
    };
    $("#exp-json").onclick = () => downloadAs("json");
    $("#exp-md").onclick = () => downloadAs("markdown");
    $("#exp-html").onclick = () => downloadAs("html");
    $("#imp-go").onclick = async () => {
      const f = $("#imp-file").files[0];
      if (!f) return toast("Choose a file first", { isError: true });
      try {
        const doc = JSON.parse(await f.text());
        const r = await apiJson("/import", { method: "POST", body: doc });
        toast(`Imported ${r.chats} chats`);
        await refreshAll();
      } catch (e) { toast(`Import failed: ${e.message}`, { isError: true }); }
    };
    (async () => {
      const shares = await apiJson("/shares").catch(() => []);
      const list = $("#shares-list");
      list.innerHTML = shares.length
        ? shares.map((sh) => `<div class="list-item" data-token="${sh.token}">
            <div class="grow"><strong></strong>
              <div class="hint">/s/${sh.token.slice(0, 8)}… · ${sh.created_at?.slice(0, 10)}</div></div>
            <button class="revoke danger">Revoke</button></div>`).join("")
        : `<p class="hint">No active share links.</p>`;
      $$("#shares-list .list-item").forEach((el) => {
        $("strong", el).textContent = el.querySelector("strong").textContent || "";
        const sh = shares.find((x) => x.token === el.dataset.token);
        $("strong", el).textContent = sh.title;
        $(".revoke", el).onclick = async () => {
          await api(`/chats/${sh.chat_id}/share`, { method: "DELETE" });
          toast("Share revoked");
          settingsModal("data");
        };
      });
    })();
  }

  if (tab === "password") {
    body.innerHTML = `
      <div class="card">
        <p class="hint">${s.has_password
          ? "A password is set. Clearing it removes protection."
          : "Optional: require a password to open OpenTyper (min 8 chars)."}</p>
        <div class="row"><input id="pw1" type="password" placeholder="New password"
          autocomplete="new-password"></div>
        <div class="row"><button class="primary" id="pw-set">Set password</button>
        ${s.has_password ? `<button class="danger" id="pw-clear">Clear password</button>` : ""}</div>
      </div>`;
    $("#pw-set").onclick = async () => {
      try {
        await api("/settings/password", { method: "PUT", body: { password: $("#pw1").value } });
        toast("Password set"); await refreshAll(); settingsModal("password");
      } catch (e) { toast(e.message, { isError: true }); }
    };
    const clr = $("#pw-clear");
    if (clr) clr.onclick = async () => {
      if (!await confirmDialog("Remove password protection? Anyone reaching this server can read your chats.")) return;
      await api("/settings/password", { method: "DELETE" });
      toast("Password cleared"); await refreshAll(); settingsModal("password");
    };
  }
}

async function refreshAll() {
  const { refreshAll: ref } = await import("../chat.js");
  await ref();
}
