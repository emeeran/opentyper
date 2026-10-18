/* Theme + wide layout + code-highlight stylesheet swapping. */
"use strict";

import { state } from "./state.js";
import { resolvedTheme } from "./util.js";

export function applyTheme() {
  document.documentElement.dataset.theme = state.settings.theme || "system";
  const resolved = resolvedTheme();
  for (const link of document.querySelectorAll('link[data-hl]')) {
    link.disabled = link.dataset.hl !== resolved;
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = resolved === "dark" ? "#16181d" : "#ffffff";
}

export function applyWide() {
  document.body.classList.toggle("wide", localStorage.getItem("wide") === "1");
}
