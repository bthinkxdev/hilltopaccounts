(function () {
  "use strict";

  var searchInput = document.querySelector('.topbar__search input[type="search"]');
  if (searchInput) {
    searchInput.addEventListener("search", function () {
      if (!this.value && location.pathname.includes("/search/")) {
        var from = new URLSearchParams(location.search).get("from") || (document.referrer && !document.referrer.includes("/search/") ? document.referrer : "/");
        location.href = from;
      }
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("a.btn--ghost");
    if (!btn || btn.textContent.trim() !== "Cancel") return;
    if (document.referrer && document.referrer.indexOf(window.location.host) !== -1) {
      var refPath = document.referrer.split("?")[0].replace(/\/+$/, "");
      var curPath = window.location.href.split("?")[0].replace(/\/+$/, "");
      if (refPath !== curPath) {
        e.preventDefault();
        window.location.href = document.referrer;
      }
    }
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;

    if (form.method.toLowerCase() === "get") {
      var q = form.querySelector('input[name="q"]');
      if (q && !q.value.trim()) {
        if (location.pathname.includes("/search/")) {
          e.preventDefault();
          var from = new URLSearchParams(location.search).get("from") || (document.referrer && !document.referrer.includes("/search/") ? document.referrer : "/");
          return (location.href = from);
        }
        if (!new URLSearchParams(location.search).get("q")) return e.preventDefault();
        q.disabled = true;
      }
      return;
    }

    var submitter = form.querySelector('button[type="submit"], input[type="submit"]');
    if (!submitter || submitter.disabled) return;

    window.setTimeout(function () {
      submitter.disabled = true;
      submitter.dataset.originalText = submitter.dataset.originalText || submitter.textContent;
      submitter.textContent = "Please wait…";
    }, 0);
  });
})();
