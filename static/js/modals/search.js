/* Full-text search modal: debounced FTS, keyboard-navigable listbox,
   opens the chat scrolled to the hit message. */
"use strict";

import { apiJson } from "../api.js";
import { state } from "../state.js";
import { $ } from "../util.js";
import { openModal, closeModal } from "../modal.js";

export function searchModal() {
  openModal(`
    <h2>Search</h2>
    <div class="row"><input id="sq" placeholder="Search all messages…"
      role="combobox" aria-expanded="true" aria-controls="sres" aria-label="Search query"></div>
    <div id="sres" role="listbox" aria-label="Search results"></div>`);
  const input = $("#sq");
  input.focus();
  let t, hits = [], selIdx = -1;

  const render = () => {
    const res = $("#sres");
    res.innerHTML = hits.length
      ? hits.map((h, i) => `<div class="list-item" data-id="${h.chat_id}"
          data-mid="${h.message_id || ""}" role="option" aria-selected="${i === selIdx}"
          style="${i === selIdx ? "background:var(--bg3)" : ""}">
          <div class="grow"><strong></strong><div class="hint"></div></div></div>`).join("")
      : (input.value.trim() ? `<p class="hint">No results.</p>` : "");
    [...res.querySelectorAll(".list-item")].forEach((el, i) => {
      const h = hits[i];
      $("strong", el).textContent = h.title;
      const hint = $(".hint", el);
      hint.textContent = "";
      // snippet uses » « markers — render as <mark>
      const [pre, mid, ...rest] = h.snippet.split("»");
      hint.append(pre);
      if (rest.length) {
        const [inner, post] = rest.join("»").split("«");
        const mk = document.createElement("mark"); mk.textContent = inner;
        hint.append(mk, post || "");
      }
      el.onclick = () => choose(i);
    });
  };

  const choose = (i) => {
    const h = hits[i];
    if (!h) return;
    closeModal();
    state.lastHit = h.message_id || null;
    import("../chat.js").then((m) => m.openChat(h.chat_id));
  };

  const move = (dir) => {
    if (!hits.length) return;
    selIdx = (selIdx + dir + hits.length) % hits.length;
    render();
    const el = $(`#sres .list-item:nth-child(${selIdx + 1})`);
    el?.scrollIntoView({ block: "nearest" });
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); choose(selIdx >= 0 ? selIdx : 0); }
  });

  input.oninput = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const q = input.value.trim();
      if (!q) { hits = []; render(); return; }
      hits = await apiJson(`/search?q=${encodeURIComponent(q)}`);
      selIdx = -1;
      render();
    }, 250);
  };
}
