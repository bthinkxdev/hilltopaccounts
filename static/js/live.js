/* Live refresh of server-rendered figures, without reloading the page.
 *
 * A page marks the parts that show money with data-live="name" (the Collections page: summary, tabs, lists and the pinned total
 * bar, plus the flash messages). After any money action on the page, those parts are re-fetched from the server and swapped in
 * place, so every number is the server's own, never computed in the browser. The scroll position, the filters being typed and
 * any open popup are left alone. If anything looks wrong (a failed request, an expired session, a changed page) the page simply
 * reloads, so stale numbers are never left on screen.
 *
 * Triggers: a collect form on the page (rent / advance popups), a successful action popup that returns to this same page
 * (modal.js: Revert, Record payment...), and coming back to the tab after a while (someone else may have collected meanwhile).
 */
(function () {
  "use strict";

  var REGION = "[data-live]";
  // The flash-message area exists on every page; only the other regions (figures, lists) can be refreshed in place.
  var SWAPPABLE = '[data-live]:not([data-live="messages"])';
  var STALE_AFTER_MS = 15000;
  var inflight = null;
  var queued = false;
  var lastRun = Date.now();

  // True on a page that declares figures to refresh in place (Collections). Other pages reload after a save instead.
  function available() {
    return !!document.querySelector(SWAPPABLE);
  }

  function text(el) {
    return el ? el.textContent.replace(/\s+/g, " ").trim() : "";
  }

  function swap(doc) {
    var changedTotal = false;
    Array.prototype.forEach.call(document.querySelectorAll(REGION), function (current) {
      var name = current.getAttribute("data-live");
      var fresh = doc.querySelector('[data-live="' + name + '"]');
      if (!fresh) return;
      var before = text(current.querySelector("[data-total=collected]"));
      var node = document.importNode(fresh, true);
      current.replaceWith(node);
      if (name === "total") {
        var after = text(node.querySelector("[data-total=collected]"));
        changedTotal = before !== "" && after !== before;
        if (changedTotal) node.classList.add("is-updated"); // a brief highlight so the change is noticed
      }
    });
    return changedTotal;
  }

  function refresh() {
    if (!available()) {
      window.location.reload(); // a page without live parts: show the server's new state with a normal reload
      return Promise.resolve();
    }
    if (inflight) {
      queued = true; // a refresh is already running: run once more afterwards so the latest state wins
      return inflight;
    }
    inflight = fetch(window.location.href, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin", cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("status " + response.status);
        return response.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        if (!doc.querySelector(SWAPPABLE)) throw new Error("not the same page"); // e.g. redirected to the login page
        swap(doc);
        lastRun = Date.now();
      })
      .catch(function () {
        window.location.reload(); // never leave stale numbers on screen
      })
      .then(function () {
        inflight = null;
        if (queued) {
          queued = false;
          return refresh();
        }
      });
    return inflight;
  }

  window.LiveRefresh = { available: available, refresh: refresh };

  // ---- the collect popups of this page (rent, advance) ------------------------------------------------------------
  function showError(form, message) {
    var box = form.querySelector("[data-live-error]");
    if (!box) {
      box = document.createElement("div");
      box.className = "form-error";
      box.setAttribute("data-live-error", "");
      form.insertBefore(box, form.firstChild);
    }
    box.textContent = message;
  }

  function setBusy(form, on) {
    Array.prototype.forEach.call(form.querySelectorAll("button[type=submit]"), function (b) {
      b.disabled = on;
      if (!on && b.dataset.originalText) b.textContent = b.dataset.originalText; // app.js swaps it for "Please wait…"
    });
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.id !== "rent-collect-form" && form.id !== "dep-modal-form") return;
    e.preventDefault();
    if (form.dataset.sending === "1") return; // a double click must not post twice
    form.dataset.sending = "1";
    setBusy(form, true);
    var data;
    try { data = new FormData(form, e.submitter); } catch (err) { data = new FormData(form); }
    var old = form.querySelector("[data-live-error]");
    if (old) old.remove();
    fetch(form.action, { method: "POST", body: data, headers: { "X-Modal": "1", "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
      .then(function (response) {
        if (response.headers.get("X-Modal-Redirect")) {
          var dlg = form.closest("dialog");
          if (dlg && dlg.open) dlg.close();
          return refresh(); // the server's reply (success or its own error message) is shown by the refreshed message area
        }
        showError(form, "This could not be saved (error " + response.status + "). Nothing was changed — please try again.");
        return refresh();
      })
      .catch(function () {
        showError(form, "The connection was lost, so it is not certain this was saved. The list has been refreshed — check it before trying again.");
        return refresh();
      })
      .then(function () {
        form.dataset.sending = "0";
        setBusy(form, false);
      });
  }, true);

  // ---- coming back to the tab ------------------------------------------------------------------------------------
  function refreshIfStale() {
    if (document.visibilityState === "visible" && available() && !document.querySelector("dialog[open]") && Date.now() - lastRun > STALE_AFTER_MS) refresh();
  }
  document.addEventListener("visibilitychange", refreshIfStale);
  window.addEventListener("focus", refreshIfStale);
})();
