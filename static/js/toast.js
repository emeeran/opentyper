/* Toasts with optional action buttons. aria-live=polite on the container. */
"use strict";

export function toast(msg, opts = {}) {
  const { isError = false, action = null, onAction = null, ttl = 6000 } =
    typeof opts === "boolean" ? { isError: opts } : opts;
  const box = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.setAttribute("role", isError ? "alert" : "status");
  const span = document.createElement("span");
  span.textContent = msg;
  span.style.flex = "1";
  el.append(span);
  let done = false;
  const dismiss = () => {
    if (done) return;
    done = true;
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 260);
  };
  if (action) {
    const btn = document.createElement("button");
    btn.textContent = action;
    btn.onclick = () => { dismiss(); onAction && onAction(); };
    el.append(btn);
  }
  const x = document.createElement("button");
  x.textContent = "×";
  x.setAttribute("aria-label", "Dismiss");
  x.onclick = dismiss;
  el.append(x);
  box.append(el);
  // keep at most 4 toasts
  while (box.children.length > 4) box.firstElementChild.remove();
  setTimeout(dismiss, ttl);
  return dismiss;
}
