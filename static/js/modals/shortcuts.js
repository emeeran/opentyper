/* "?" shortcuts overlay — reads the (possibly rebound) combo table. */
"use strict";

import { shortcuts } from "../state.js";
import { openModal } from "../modal.js";

export function shortcutsModal() {
  const s = shortcuts();
  const row = (label, combo) =>
    `<div class="list-item"><span class="grow">${label}</span><kbd>${combo}</kbd></div>`;
  openModal(`<h2>Keyboard shortcuts</h2>
    ${row("Search", s.search)}
    ${row("Toggle sidebar", s.sidebar)}
    ${row("New chat", s.newChat)}
    ${row("Regenerate", s.regenerate)}
    ${row("Copy last reply", s.copyLast)}
    ${row("Prompt menu", "/")}
    ${row("Full-screen editor", "⌘⇧F")}
    ${row("This help", s.help)}
    ${row("Send / newline", "Enter / ⇧Enter")}`);
}
