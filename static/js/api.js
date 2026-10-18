/* Fetch wrapper: JSON body, 401 → sign-in modal, friendly errors. */
"use strict";

import { toast } from "./toast.js";
import { showAuthModal } from "./modals/auth.js";

export async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    headers: opts.body && !(opts.body instanceof FormData)
      ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  if (res.status === 401 && !path.startsWith("/auth")) {
    showAuthModal(); // openModal alone wouldn't bind the sign-in button
    throw new Error("authentication required");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res;
}

export const apiJson = async (...a) => await (await api(...a)).json();
