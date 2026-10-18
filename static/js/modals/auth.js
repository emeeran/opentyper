/* Sign-in modal (shown on 401 when an app password is set). */
"use strict";

import { api } from "../api.js";
import { $ } from "../util.js";
import { openModal, closeModal } from "../modal.js";
import { toast } from "../toast.js";

let refreshAllFn = null;
export function wireAuth(refreshAll) { refreshAllFn = refreshAll; }

export function showAuthModal() {
  if (document.querySelector("#auth-pw")) return; // already open
  openModal(`<h2>Sign in</h2>
    <div class="row"><input id="auth-pw" type="password" placeholder="Password"
      autocomplete="current-password" aria-label="Password"></div>
    <div class="row"><button class="primary" id="auth-go">Sign in</button></div>`);
  $("#auth-go").onclick = async () => {
    try {
      await api("/auth/login", { method: "POST", body: { password: $("#auth-pw").value } });
      closeModal();
      if (refreshAllFn) await refreshAllFn();
      // pick up where boot left off: open the most recent chat
      const { state } = await import("../state.js");
      const chat = await import("../chat.js");
      if (state.chats[0]) await chat.openChat(state.chats[0].id);
      else chat.renderEmpty();
    } catch (e) { toast(e.message, { isError: true }); }
  };
  $("#auth-pw").focus();
  $("#auth-pw").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#auth-go").click();
  });
}
