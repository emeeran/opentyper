/* Global SPA state. One plain object — see app architecture in js/main.js. */
"use strict";

export const state = {
  chats: [], folders: [], prompts: [], agents: [], models: [],
  skills: [], memories: [],
  settings: {}, providers: [],
  currentId: null,
  controller: null,          // AbortController for the in-flight stream
  pendingImages: [], pendingFiles: [],
  tagFilter: null, showArchived: false,
  selectMode: false, selected: new Set(),
  tempChat: false,           // next/new chat is temporary
  useKnowledge: false,       // send-time knowledge override
  sideChat: { open: false, controller: null, context: "" },
  shortcuts: {},             // action -> combo string (rebinding)
  lastHit: null,             // search hit to scroll to
};

export const DEFAULT_SHORTCUTS = {
  search: "Mod+K", sidebar: "Mod+B", newChat: "Mod+Alt+N",
  regenerate: "Mod+Alt+E", copyLast: "Mod+Alt+C", help: "?",
};

export function shortcuts() {
  return { ...DEFAULT_SHORTCUTS, ...(state.settings.shortcuts || {}) };
}
