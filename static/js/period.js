/* Global month/year switcher + the one central loading screen.
 *
 * The navbar month/year choice is the single period for the whole app: changing it reloads the current page for that
 * month (the server remembers it for the session, so every month-based page follows). The loader names the page and
 * the month being loaded. Pages that are deliberately not month-based simply ignore the period.
 */
(function () {
  "use strict";

  var loader = document.getElementById("app-loader");
  var titleEl = document.getElementById("app-loader-title");
  var subEl = document.getElementById("app-loader-sub");
  var control = document.querySelector("[data-period-switch]");
  var monthSel = document.getElementById("period-month");
  var yearSel = document.getElementById("period-year");
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var hideTimer = null;

  function pageName() {
    var h1 = document.querySelector(".main h1");
    var name = (h1 && h1.textContent) || document.title.split(" — ")[0] || "";
    return name.replace(/\s+/g, " ").trim() || "page";
  }

  function periodLabel(year, month) {
    if (!year) return "";
    return month && month >= 1 ? MONTHS[month - 1] + " " + year : year + " (full year)";
  }

  function currentLabel() {
    if (!control || !monthSel || !yearSel) return "";
    return periodLabel(yearSel.value, parseInt(monthSel.value, 10));
  }

  function showLoader(title, sub) {
    if (!loader) return;
    titleEl.textContent = title;
    subEl.textContent = sub || "";
    loader.hidden = false;
    document.body.classList.add("is-loading");
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(hideLoader, 20000); // never trap the user (e.g. a download that never navigates)
  }

  function hideLoader() {
    if (!loader) return;
    loader.hidden = true;
    document.body.classList.remove("is-loading");
    if (control) control.classList.remove("is-switching");
  }

  window.addEventListener("pageshow", hideLoader);

  function switchPeriod(year, month) {
    var label = periodLabel(year, month);
    var url = new URL(window.location.href);
    url.searchParams.set("period", month >= 1 ? "month" : "year");
    url.searchParams.set("year", year);
    if (month >= 1) url.searchParams.set("month", month); else url.searchParams.delete("month");
    url.searchParams.delete("page");
    if (control) control.classList.add("is-switching");
    showLoader("Switching to " + label, "Loading " + pageName() + " for " + label);
    window.setTimeout(function () { window.location.assign(url.toString()); }, 280);
  }

  // Styled popover menus in place of the native <select> lists (the selects stay as the source of truth + no-JS fallback).
  var openMenu = null;

  function closeMenu() {
    if (!openMenu) return;
    openMenu.wrap.classList.remove("is-open");
    openMenu.button.setAttribute("aria-expanded", "false");
    openMenu = null;
  }

  function enhance(select, kind) {
    var wrap = document.createElement("div");
    wrap.className = "psel psel--" + kind;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "psel__button";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", select.getAttribute("aria-label") || kind);
    var menu = document.createElement("div");
    menu.className = "psel__menu";
    menu.setAttribute("role", "listbox");
    var nowValue = kind === "month" ? control.dataset.currentMonth : control.dataset.currentYear;
    var items = [];

    Array.prototype.forEach.call(select.options, function (opt) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "psel__option";
      item.setAttribute("role", "option");
      item.dataset.value = opt.value;
      item.textContent = kind === "month" && opt.value > 0 ? opt.textContent.trim().slice(0, 3) : opt.textContent.trim();
      item.title = opt.textContent.trim();
      if (opt.value === "0") item.classList.add("psel__option--wide");
      if (opt.value === nowValue) item.classList.add("psel__option--now");
      if (opt.value === "") item.disabled = true;
      item.addEventListener("click", function () {
        closeMenu();
        if (select.value !== opt.value) {
          select.value = opt.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
      items.push(item);
      menu.appendChild(item);
    });

    var chosen = select.options[select.selectedIndex];
    var text = document.createElement("span");
    text.textContent = chosen ? chosen.textContent.trim() : "";
    button.appendChild(text);
    items.forEach(function (it) {
      var on = chosen && it.dataset.value === chosen.value;
      it.classList.toggle("is-selected", !!on);
      it.setAttribute("aria-selected", on ? "true" : "false");
    });

    function open() {
      if (openMenu && openMenu.wrap !== wrap) closeMenu();
      wrap.classList.add("is-open");
      wrap.classList.remove("psel--flip");
      button.setAttribute("aria-expanded", "true");
      openMenu = { wrap: wrap, button: button };
      if (menu.getBoundingClientRect().right > window.innerWidth - 8) wrap.classList.add("psel--flip");
      var selected = menu.querySelector(".is-selected");
      if (selected) selected.scrollIntoView({ block: "nearest" });
    }

    button.addEventListener("click", function (e) {
      e.stopPropagation();
      if (wrap.classList.contains("is-open")) closeMenu(); else open();
    });
    wrap.addEventListener("keydown", function (e) {
      var enabled = items.filter(function (it) { return !it.disabled; });
      var idx = enabled.indexOf(document.activeElement);
      if (e.key === "Escape") { closeMenu(); button.focus(); return; }
      if (e.key === "ArrowDown" || e.key === "ArrowRight" || e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        if (!wrap.classList.contains("is-open")) open();
        var step = (e.key === "ArrowDown" || e.key === "ArrowRight") ? 1 : -1;
        if (kind === "month" && (e.key === "ArrowDown" || e.key === "ArrowUp")) step *= 3;
        var from = idx < 0 ? enabled.indexOf(menu.querySelector(".is-selected")) : idx + step;
        var next = enabled[Math.max(0, Math.min(enabled.length - 1, from))];
        if (next) next.focus();
      }
    });

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(button);
    wrap.appendChild(menu);
    wrap.appendChild(select);
    select.classList.add("psel__native");
    select.tabIndex = -1;
  }

  if (control && monthSel && yearSel) {
    enhance(monthSel, "month");
    enhance(yearSel, "year");
    control.classList.add("is-enhanced");
    document.addEventListener("click", function (e) { if (openMenu && !openMenu.wrap.contains(e.target)) closeMenu(); });
  }

  function onPickerChange() {
    if (!yearSel.value) return;
    switchPeriod(parseInt(yearSel.value, 10), parseInt(monthSel.value, 10));
  }

  if (monthSel && yearSel) {
    monthSel.addEventListener("change", onPickerChange);
    yearSel.addEventListener("change", onPickerChange);
  }

  document.addEventListener("click", function (e) {
    var reset = e.target.closest("[data-period-today]");
    if (!reset || !control) return;
    e.preventDefault();
    switchPeriod(parseInt(control.dataset.currentYear, 10), parseInt(control.dataset.currentMonth, 10));
  });

  // Central loading screen for every navigation: it names the page and the month being loaded.
  function isPlainNavigation(a, e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
    if (a.target && a.target !== "_self") return false;
    if (a.hasAttribute("download") || a.hasAttribute("data-no-loader")) return false;
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#" || /^(mailto|tel|javascript):/i.test(href)) return false;
    var url = new URL(a.href, window.location.href);
    if (url.origin !== window.location.origin) return false;
    if (/export|\.csv|\.pdf|\.xlsx|download/i.test(url.pathname + url.search)) return false;
    return !(url.pathname === window.location.pathname && url.search === window.location.search);
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[href]");
    if (!a || !isPlainNavigation(a, e)) return;
    var url = new URL(a.href, window.location.href);
    var y = url.searchParams.get("year"), m = url.searchParams.get("month");
    var label = y && url.searchParams.get("period") !== "year" && m ? periodLabel(y, parseInt(m, 10)) : (y && url.searchParams.get("period") === "year" ? periodLabel(y, 0) : currentLabel());
    var text = (a.getAttribute("aria-label") || a.textContent || "").replace(/\s+/g, " ").trim();
    if (text.length > 40 || !text) text = "next page";
    showLoader("Loading " + text, label ? "Showing " + label : "");
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || e.defaultPrevented || form.hasAttribute("data-no-loader")) return;
    var action = form.getAttribute("action") || "";
    if (/export/i.test(action)) return;
    var label = currentLabel();
    var posting = form.method.toLowerCase() === "post";
    window.setTimeout(function () {
      if (e.defaultPrevented) return;
      showLoader(posting ? "Saving…" : "Loading…", (posting ? pageName() : "Updating " + pageName()) + (label ? " · " + label : ""));
    }, 0);
  });
})();
