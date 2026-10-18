/* Modal machinery: focus trap + restore, Escape, in-app confirm/prompt/preview
   dialogs replacing native confirm()/prompt(). */
"use strict";

import { $, $$, copyText, uuid } from "./util.js";
import { toast } from "./toast.js";

let lastFocus = null;

export function openModal(html, { onClose = null } = {}) {
  const modal = $("#modal");
  lastFocus = document.activeElement;
  modal.innerHTML = html;
  modal.dataset.onClose = onClose ? "1" : "";
  modal._onClose = onClose;
  $("#modal-back").hidden = false;
  const focusables = $$("button, input, select, textarea, [tabindex]", modal);
  (focusables[0] || modal).focus();
  return modal;
}

export function closeModal() {
  const back = $("#modal-back");
  if (back.hidden) return;
  const modal = $("#modal");
  if (modal._onClose) { const fn = modal._onClose; modal._onClose = null; fn(); }
  back.hidden = true;
  modal.innerHTML = "";
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
}

export function isOpen() { return !$("#modal-back").hidden; }

/* focus trap: keep Tab inside the open modal */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Tab" || $("#modal-back").hidden) return;
  const items = $$("button, input, select, textarea, [tabindex]", $("#modal"))
    .filter((el) => !el.disabled && el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

/* --- in-app dialogs ------------------------------------------------------- */

export function confirmDialog(message, { confirmLabel = "Delete", danger = true } = {}) {
  return new Promise((resolve) => {
    openModal(`<h2>Are you sure?</h2>
      <p>${message}</p>
      <div class="row">
        <button class="${danger ? "danger" : "primary"}" id="dlg-ok">${confirmLabel}</button>
        <button id="dlg-cancel">Cancel</button>
      </div>`, { onClose: () => resolve(false) });
    $("#dlg-cancel").onclick = () => closeModal();
    $("#dlg-ok").onclick = () => { $("#modal")._onClose = null; closeModal(); resolve(true); };
    $("#dlg-ok").focus();
  });
}

/* multi-field prompt dialog; fields: [{id, label, value, placeholder, type}] */
export function formDialog(title, fields, { okLabel = "OK" } = {}) {
  return new Promise((resolve) => {
    openModal(`<h2>${title}</h2>
      ${fields.map((f) => `<div class="row"><label for="${f.id}">${f.label}</label>
        <input id="${f.id}" type="${f.type || "text"}" value="${f.value ?? ""}"
               placeholder="${f.placeholder || ""}"></div>`).join("")}
      <div class="row">
        <button class="primary" id="dlg-ok">${okLabel}</button>
        <button id="dlg-cancel">Cancel</button>
      </div>`, { onClose: () => resolve(null) });
    $("#dlg-cancel").onclick = () => closeModal();
    $("#dlg-ok").onclick = () => {
      const out = {};
      for (const f of fields) out[f.id] = $("#" + f.id).value;
      $("#modal")._onClose = null;
      closeModal();
      resolve(out);
    };
    $("#" + fields[0].id).focus();
    $("#modal").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.tagName === "INPUT") $("#dlg-ok").click();
    });
  });
}

/* fill {{variables}} through a real dialog (headless-testable, no prompt()) */
export async function fillTemplate(text) {
  const names = [...new Set((text.match(/\{\{(\w[\w ]*)\}\}/g) || [])
    .map((m) => m.slice(2, -2).trim()))];
  if (!names.length) return text;
  const fields = names.map((n) => ({ id: "var-" + n, label: n, placeholder: n }));
  const vals = await formDialog("Fill in template", fields, { okLabel: "Insert" });
  if (!vals) return null; // cancelled → don't clobber the composer
  let out = text;
  for (const n of names) out = out.replaceAll(`{{${n}}}`, vals["var-" + n] ?? "");
  return out;
}

export function previewArtifact(source) {
  openModal(`<h2>Preview</h2>
    <iframe id="artifact" class="artifact" sandbox="allow-scripts" title="Preview"></iframe>`);
  const frame = $("#artifact");
  frame.className = "";
  frame.style.cssText = "width:100%;height:70vh;border:1px solid var(--border);" +
    "border-radius:10px;background:#fff";
  frame.srcdoc = source;
}

/* click-to-preview an attachment (text/code/csv in-app, pdf via lazy pdf.js) */
export async function previewAttachment(att) {
  if (att.kind === "image") {
    openModal(`<h2>${att.name}</h2><img src="${att.url}" alt="${att.name}"
      style="max-width:100%;border-radius:10px">`);
    return;
  }
  if (/\.pdf$/i.test(att.name || "")) {
    openModal(`<h2>${att.name}</h2><div id="pdfhost">Loading PDF…</div>`);
    try {
      const pdfjs = await import("/vendor/pdf.min.js");
      pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.min.js";
      const doc = await pdfjs.getDocument({ data: att.bytes }).promise;
      const host = $("#pdfhost");
      host.textContent = "";
      for (let i = 1; i <= Math.min(doc.numPages, 20); i++) {
        const page = await doc.getPage(i);
        const vp = page.getViewport({ scale: 1.4 });
        const canvas = document.createElement("canvas");
        canvas.width = vp.width; canvas.height = vp.height;
        canvas.style.cssText = "width:100%;margin-bottom:8px;border:1px solid var(--border)";
        await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
        host.append(canvas);
      }
    } catch (e) {
      $("#pdfhost").textContent = `Could not render PDF: ${e.message}`;
    }
    return;
  }
  openModal(`<h2>${att.name}</h2>
    <pre style="max-height:60dvh;overflow:auto;white-space:pre-wrap;font-family:var(--mono);font-size:13px"></pre>`);
  $("#modal pre").textContent = (att.text || "").slice(0, 200_000);
}

/* share-link dialog with copy + revoke */
export function shareDialog(url, onRevoke) {
  openModal(`<h2>Share this chat</h2>
    <p class="hint">Anyone with this secret link can read the chat — until you revoke it.</p>
    <div class="row"><input id="share-url" readonly value="${url}" aria-label="Share link">
      <button id="share-copy" class="primary">Copy</button></div>
    <div class="row"><button id="share-revoke" class="danger">Revoke access</button>
      <button id="share-done">Done</button></div>`);
  $("#share-copy").onclick = async () => {
    (await copyText(url))
      ? toast("Link copied to clipboard")
      : ($("#share-url").select(), document.execCommand("copy"));
  };
  $("#share-revoke").onclick = async () => {
    await onRevoke();
    closeModal();
  };
  $("#share-done").onclick = closeModal;
  $("#share-url").select();
}
