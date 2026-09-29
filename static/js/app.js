(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    var submitter = form.querySelector('button[type="submit"], input[type="submit"]');
    if (!submitter || submitter.disabled) return;

    window.setTimeout(function () {
      submitter.disabled = true;
      submitter.dataset.originalText = submitter.dataset.originalText || submitter.textContent;
      submitter.textContent = "Please wait…";
    }, 0);
  });
})();
