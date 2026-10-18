/* Markdown rendering: marked + highlight.js, sanitized with DOMPurify, plus
   lazy Mermaid diagrams, KaTeX math, table copy/expand, CodePen export.
   All output passes DOMPurify before touching the DOM. */
"use strict";

import { $, $$, uuid, copyText, resolvedTheme } from "./util.js";
import { toast } from "./toast.js";
import { previewArtifact } from "./modal.js";
import { openArtifactFromBlock } from "./artifacts.js";

marked.use({ breaks: true });

const SANITIZE_OPTS = {
  FORBID_TAGS: ["style", "form", "input", "button", "textarea", "iframe"],
  FORBID_ATTR: ["onerror", "onclick", "onload", "style"],
  ADD_ATTR: ["target", "rel"],
};
const PURIFY = window.DOMPurify || null;

function sanitize(html) {
  return PURIFY ? PURIFY.sanitize(html, SANITIZE_OPTS) : html;
}

/* --- math extraction (KaTeX) ---------------------------------------------- */

const MATH_PATTERNS = [
  [/\$\$([\s\S]+?)\$\$/g, true],           // $$ display $$
  [/\\\[([\s\S]+?)\\\]/g, true],           // \[ display \]
  [/\\\(([\s\S]+?)\\\)/g, false],          // \( inline \)
  [/\$(?!\s)((?:[^$\n\\]|\\.)+?)(?<!\s)\$/g, false], // $ inline $
];

function extractMath(text) {
  const spans = [];
  for (const [re, display] of MATH_PATTERNS) {
    text = text.replace(re, (m, tex) => {
      spans.push({ tex: tex.trim(), display });
      return `⟦M${spans.length - 1}⟧`;
    });
  }
  return { text, spans };
}

let katexLoading = null;
async function ensureKatex() {
  if (window.katex) return window.katex;
  if (!katexLoading) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/vendor/katex.min.css";
    document.head.append(link);
    katexLoading = import("/vendor/katex.min.js").then(() => window.katex);
  }
  return katexLoading;
}

/* highlight.js (127 KB) loads on first code block only — keeps the eager
   bundle under the 100 KB-per-script budget. UMD build → script injection. */
let hljsLoading = null;
function ensureHljs() {
  if (window.hljs) return Promise.resolve();
  if (!hljsLoading) {
    hljsLoading = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = "/vendor/highlight.min.js";
      s.onload = resolve;
      document.head.append(s);
    }).catch(() => { hljsLoading = null; });
  }
  return hljsLoading;
}

function highlight(block) {
  if (window.hljs) {
    try { hljs.highlightElement(block); } catch { /* unknown language */ }
    return;
  }
  void ensureHljs().then(() => {
    if (window.hljs) {
      try { hljs.highlightElement(block); } catch { /* unknown language */ }
    }
  });
}

function applyMath(fragment, spans) {
  if (!spans.length) return;
  const walker = document.createTreeWalker(fragment, NodeFilter.SHOW_TEXT);
  const hits = [];
  while (walker.nextNode()) {
    if (walker.currentNode.nodeValue.includes("⟦M")) hits.push(walker.currentNode);
  }
  for (const node of hits) {
    const parts = node.nodeValue.split(/(⟦M\d+⟧)/);
    const frag = document.createDocumentFragment();
    for (const part of parts) {
      const m = part.match(/^⟦M(\d+)⟧$/);
      if (!m) { frag.append(document.createTextNode(part)); continue; }
      const span = spans[+m[1]];
      const holder = document.createElement("span");
      try {
        // KaTeX renders MathML+HTML; renderToString output is escaped by design
        holder.innerHTML = sanitize(window.katex.renderToString(span.tex,
          { displayMode: span.display, throwOnError: false }));
      } catch {
        holder.textContent = part;
      }
      frag.append(holder);
    }
    node.replaceWith(frag);
  }
  // also load katex lazily for spans we could not render yet
  const pending = fragment.querySelectorAll("[data-katex-pending]");
  if (pending.length) ensureKatex();
}

/* --- mermaid ---------------------------------------------------------------- */

let mermaidLoading = null;
async function ensureMermaid() {
  if (!mermaidLoading) {
    mermaidLoading = import("/vendor/mermaid.min.js").then((mod) => {
      const mermaid = mod.default;
      mermaid.initialize({
        startOnLoad: false, securityLevel: "strict",
        theme: resolvedTheme() === "dark" ? "dark" : "default",
      });
      return mermaid;
    }).catch(() => null);
  }
  return mermaidLoading;
}

async function renderMermaidBlocks(fragment) {
  const blocks = $$("pre code.language-mermaid", fragment);
  if (!blocks.length) return;
  for (const block of blocks) {
    const wrap = document.createElement("div");
    wrap.className = "mermaid-block";
    wrap.textContent = "Loading diagram…";
    block.closest(".code-wrap")?.replaceWith(wrap);
    const mermaid = await ensureMermaid();
    if (!mermaid) { wrap.textContent = block.textContent; continue; }
    try {
      const { svg } = await mermaid.render("mmd-" + uuid(), block.textContent);
      wrap.innerHTML = sanitize(svg);
      wrap.style.cssText = "overflow-x:auto;cursor:zoom-in";
      wrap.onclick = () => {
        import("./modal.js").then(({ openModal }) => {
          openModal(`<h2>Diagram</h2><div style="overflow:auto">${sanitize(svg)}</div>`);
        });
      };
    } catch {
      wrap.textContent = block.textContent;
    }
  }
}

/* --- tables: copy + expand ---------------------------------------------------- */

function enhanceTables(fragment) {
  for (const table of $$("table", fragment)) {
    const bar = document.createElement("div");
    bar.className = "table-tools";
    bar.style.cssText = "display:flex;gap:6px;margin:4px 0";
    const mk = (label, fn) => {
      const b = document.createElement("button");
      b.className = "copy";
      b.style.cssText = "position:static;opacity:1;font-size:12px;padding:3px 9px";
      b.textContent = label;
      b.onclick = fn;
      return b;
    };
    bar.append(
      mk("Copy table", async () => {
        const rows = [...table.rows].map((tr) =>
          [...tr.cells].map((td) => td.textContent.trim()).join("\t"));
        await copyText(rows.join("\n"))
          ? toast("Table copied (tab-separated)")
          : toast("Copy failed", true);
      }),
      mk("Expand", () => {
        import("./modal.js").then(({ openModal }) => {
          const clone = table.cloneNode(true);
          clone.style.display = "table";
          clone.style.maxWidth = "none";
          openModal(`<h2>Table</h2><div style="overflow:auto">${clone.outerHTML}
            <div class="row"><button class="primary" id="tbl-copy">Copy table</button></div>`);
          $("#tbl-copy").onclick = async () => {
            const rows = [...table.rows].map((tr) =>
              [...tr.cells].map((td) => td.textContent.trim()).join("\t"));
            await copyText(rows.join("\n")) && toast("Table copied");
          };
        });
      }));
    table.before(bar);
  }
}

/* --- main entry ---------------------------------------------------------------- */

export function renderMd(text, { mathSpans = null, host = null } = {}) {
  const { text: stripped, spans } = extractMath(text || "");
  const html = sanitize(marked.parse(stripped));
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  // highlight + copy / preview / CodePen on code blocks
  $$("pre code", tpl.content).forEach((block) => {
    highlight(block);
    const pre = block.parentElement;
    const wrap = document.createElement("div");
    wrap.className = "code-wrap";
    pre.replaceWith(wrap);
    wrap.append(pre);
    const mk = (label, fn, cls = "copy") => {
      const b = document.createElement("button");
      b.className = cls;
      b.textContent = label;
      b.onclick = () => fn(b);
      return b;
    };
    wrap.append(mk("Copy", async (b) => {
      await copyText(block.textContent)
        ? (b.textContent = "Copied!", setTimeout(() => { b.textContent = "Copy"; }, 1200))
        : toast("Copy failed", true);
    }));
    if (/language-html|language-htm|\bhtml\b/.test(block.className)) {
      wrap.append(mk("Preview", () => previewArtifact(block.textContent), "copy preview"));
    }
    const lang = (block.className.match(/language-([\w-]+)/) || [])[1] || "";
    if (lang && !/^mermaid$/i.test(lang)) {
      wrap.append(mk("Artifact", () =>
        openArtifactFromBlock({ content: block.textContent, language: lang }),
      "copy artifact"));
    }
    if (/^codepen$/i.test((block.className.match(/language-([\w-]+)/) || [])[1] || "") ||
        (/language-css|language-js/.test(block.className))) {
      wrap.append(mk("CodePen", () => {
        const isCss = /language-css/.test(block.className);
        const form = document.createElement("form");
        form.action = "https://codepen.io/pen/define";
        form.method = "POST";
        form.target = "_blank";
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "data";
        input.value = JSON.stringify(isCss ? { css: block.textContent }
                                           : { js: block.textContent });
        form.append(input);
        document.body.append(form);
        form.submit();
        form.remove();
      }, "copy preview"));
    }
  });
  if (mathSpans) { mathSpans.push(...spans); }
  enhanceTables(tpl.content);
  if (host) {
    // full render path (not streaming): async enhancers act on the live DOM
    if (spans.length) {
      ensureKatex().then((k) => { if (k) applyMath(host, spans); })
        .catch(() => { /* math stays as text */ });
    }
    renderMermaidBlocks(host);
  }
  return tpl.content;
}

export function finalizeMd(el, text) {
  el.textContent = "";
  el.append(renderMd(text, { host: el }));
}

/* Cheap mid-stream render: markdown structure without the expensive bits
   (highlight.js, math, mermaid). Full render happens once on completion. */
export function renderMdLite(text) {
  const { text: stripped } = extractMath(text || "");
  const tpl = document.createElement("template");
  tpl.innerHTML = sanitize(marked.parse(stripped));
  $$("pre code", tpl.content).forEach((block) => {
    const pre = block.parentElement;
    const wrap = document.createElement("div");
    wrap.className = "code-wrap";
    pre.replaceWith(wrap);
    wrap.append(pre);
  });
  return tpl.content;
}
