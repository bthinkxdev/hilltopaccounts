/* Action pages as popups.
 *
 * A link marked `data-modal` opens its page inside the shared <dialog id="action-modal"> instead of navigating. The page
 * is the same server view, form and validation as before: it is fetched with `X-Modal: 1`, so it comes back without the
 * app shell. Submitting posts the form to the same URL:
 *   - a validation error comes back as the form again, shown in place;
 *   - a success comes back as `X-Modal-Redirect`; the popup closes and the page reloads on that address, so the
 *     success message and every number on screen are the server's.
 * Cancel, the close button and Esc just close the popup; nothing is saved. Without JavaScript the links still work.
 */
(function () {
  "use strict";

  var dlg = document.getElementById("action-modal");
  if (!dlg || typeof dlg.showModal !== "function") return;
  var body = dlg.querySelector("[data-modal-body]");
  var currentUrl = null;
  var busy = false;
  var HEADERS = { "X-Modal": "1", "X-Requested-With": "XMLHttpRequest" };

  function loader(title, sub) {
    if (window.AppLoader) window.AppLoader.show(title, sub);
  }

  function closeDialog() {
    if (dlg.open) dlg.close();
  }

  dlg.addEventListener("close", function () {
    body.innerHTML = "";
    currentUrl = null;
  });

  // After a save: reload on the address the server redirected to, even when it is the page we are already on.
  function go(url) {
    var target = new URL(url, window.location.href);
    var here = new URL(window.location.href);
    loader("Saved", "Refreshing…");
    if (target.pathname === here.pathname && target.search === here.search) {
      if (target.hash) window.location.hash = target.hash;
      window.location.reload();
    } else {
      window.location.assign(target.href);
    }
  }

  // Scripts that arrive with innerHTML are inert; clone each one so it runs.
  function runScripts(root) {
    Array.prototype.forEach.call(root.querySelectorAll("script"), function (old) {
      var s = document.createElement("script");
      Array.prototype.forEach.call(old.attributes, function (a) { s.setAttribute(a.name, a.value); });
      s.text = old.textContent;
      old.parentNode.replaceChild(s, old);
    });
  }

  function show(html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html;
    var fragment = tpl.content.querySelector("[data-modal-fragment]");
    if (!fragment) return false;
    body.innerHTML = "";
    body.appendChild(document.importNode(fragment, true));
    runScripts(body);
    if (!dlg.open) dlg.showModal();
    var target = body.querySelector(".field__error, .form-error");
    var field = target ? target.closest(".field, form").querySelector("input:not([type=hidden]), select, textarea") : body.querySelector("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea");
    if (field) field.focus();
    return true;
  }

  function send(url, options) {
    options = options || {};
    options.headers = HEADERS;
    options.credentials = "same-origin";
    return fetch(url, options);
  }

  // `fallbackUrl`: where to go when the popup cannot be used (no fragment, an error page, a lost connection).
  function handle(response, fallbackUrl, inForm) {
    var redirect = response.headers.get("X-Modal-Redirect");
    if (redirect) {
      closeDialog();
      go(redirect);
      return;
    }
    if (!response.ok) {
      if (inForm) { showFormError("This could not be saved (error " + response.status + "). Nothing was changed — please try again."); return; }
      window.location.assign(fallbackUrl);
      return;
    }
    return response.text().then(function (html) {
      if (!show(html)) {
        if (inForm) showFormError("This could not be saved. Nothing was changed — please try again.");
        else window.location.assign(fallbackUrl);
      }
    });
  }

  function showFormError(message) {
    var form = body.querySelector("form");
    if (!form) return;
    var box = form.querySelector("[data-modal-error]");
    if (!box) {
      box = document.createElement("div");
      box.className = "form-error";
      box.setAttribute("data-modal-error", "");
      form.insertBefore(box, form.firstChild);
    }
    box.textContent = message;
    setSubmitting(form, false);
  }

  function setSubmitting(form, on) {
    Array.prototype.forEach.call(form.querySelectorAll("button[type=submit], input[type=submit]"), function (b) {
      b.disabled = on;
      if (!on && b.dataset.originalText) b.textContent = b.dataset.originalText; // app.js swaps it for "Please wait…"
    });
    form.classList.toggle("is-submitting", on);
  }

  function open(url) {
    if (busy) return;
    busy = true;
    currentUrl = url;
    document.body.classList.add("modal-loading");
    send(url)
      .then(function (r) { return handle(r, url, false); })
      .catch(function () { window.location.assign(url); })
      .then(function () { busy = false; document.body.classList.remove("modal-loading"); });
  }

  // Open: capture phase, so the central loader's link handler sees the click as already handled.
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-modal]");
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    open(a.href);
  }, true);

  // Cancel / close.
  dlg.addEventListener("click", function (e) {
    if (e.target.closest("[data-modal-close], [data-modal-cancel]")) {
      e.preventDefault();
      closeDialog();
    }
  });

  // Save. Only forms that belong to the popup itself (a popup nested in the page keeps its own behaviour).
  dlg.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || form.closest("dialog") !== dlg) return;
    if (form.method.toLowerCase() !== "post") return;
    e.preventDefault();
    if (busy) return;
    busy = true;
    setSubmitting(form, true);
    var data;
    try { data = new FormData(form, e.submitter); } catch (err) { data = new FormData(form); }
    var url = form.getAttribute("action") || currentUrl || window.location.href;
    send(url, { method: "POST", body: data })
      .then(function (r) { return handle(r, url, true); })
      .catch(function () { showFormError("The connection was lost. Nothing was changed — please try again."); })
      .then(function () { busy = false; });
  });
})();
