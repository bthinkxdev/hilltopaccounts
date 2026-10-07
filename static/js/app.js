(function () {
  "use strict";

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;

    if (form.method.toLowerCase() === "get") {
      var q = form.querySelector('input[name="q"]');
      if (q && !q.value.trim()) {
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
