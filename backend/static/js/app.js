/* Small progressive-enhancement layer.
 *
 * Deliberately not a framework (spec sections 50, 67, 68): plain forms work
 * with JavaScript disabled, and this only upgrades four interactions:
 *   - the mobile navigation drawer
 *   - confirmation before an important action (data-confirm)
 *   - inline form submits that swap a single row (data-partial)
 *   - periodic refresh of a live region (data-poll)
 */
(function () {
  "use strict";

  function csrfToken() {
    var input = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (input) return input.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  /* ---- mobile drawer ---- */
  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-menu-toggle]");
    if (!toggle) return;
    var sidebar = document.getElementById("sidebar");
    if (!sidebar) return;
    var open = sidebar.classList.toggle("open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });

  /* ---- confirmation dialog ----
   *
   * This never calls window.confirm. Native dialogs are suppressed in
   * embedded webviews, in automated browsers, and by the browser's own
   * "prevent this page from creating additional dialogs" checkbox - and a
   * suppressed confirm() returns false, which silently cancelled the action.
   * A button that appears to do nothing is worse than no confirmation at all,
   * so the prompt is drawn in the page instead.
   */
  var dialogRoot = null;
  var dialogIsNative = false;

  function confirmDialog() {
    if (dialogRoot) return dialogRoot;

    dialogIsNative = typeof window.HTMLDialogElement === "function";
    dialogRoot = document.createElement(dialogIsNative ? "dialog" : "div");
    dialogRoot.className =
      "confirm-dialog" + (dialogIsNative ? "" : " confirm-dialog-fallback");
    dialogRoot.setAttribute("role", "dialog");
    dialogRoot.setAttribute("aria-modal", "true");
    dialogRoot.innerHTML =
      '<div class="confirm-panel">' +
      '<p class="confirm-question"></p>' +
      '<div class="confirm-actions">' +
      '<button type="button" class="btn" data-confirm-cancel>Mégse</button>' +
      '<button type="button" class="btn primary" data-confirm-ok>Megerősítem</button>' +
      "</div></div>";
    if (!dialogIsNative) dialogRoot.hidden = true;
    document.body.appendChild(dialogRoot);
    return dialogRoot;
  }

  function askConfirmation(question, onConfirm) {
    var root = confirmDialog();
    var ok = root.querySelector("[data-confirm-ok]");
    var cancel = root.querySelector("[data-confirm-cancel]");
    var previousFocus = document.activeElement;

    root.querySelector(".confirm-question").textContent = question;

    function cleanup() {
      ok.removeEventListener("click", accept);
      cancel.removeEventListener("click", dismiss);
      root.removeEventListener("click", backdrop);
      root.removeEventListener("close", cleanup);
      document.removeEventListener("keydown", onKey);
      if (previousFocus && previousFocus.focus) previousFocus.focus();
    }

    function close() {
      if (dialogIsNative) {
        root.close(); // fires "close", which runs cleanup
      } else {
        root.hidden = true;
        cleanup();
      }
    }

    function accept() {
      close();
      onConfirm();
    }

    function dismiss() {
      close();
    }

    function onKey(event) {
      if (event.key === "Escape") close();
      if (event.key === "Tab") {
        // Keep focus inside the dialog in the non-native fallback.
        if (!dialogIsNative && !root.contains(event.target)) {
          event.preventDefault();
          ok.focus();
        }
      }
    }

    function backdrop(event) {
      if (event.target === root) close();
    }

    ok.addEventListener("click", accept);
    cancel.addEventListener("click", dismiss);
    root.addEventListener("click", backdrop);
    if (dialogIsNative) root.addEventListener("close", cleanup);
    document.addEventListener("keydown", onKey);

    if (dialogIsNative) root.showModal();
    else root.hidden = false;
    ok.focus();
  }

  /* Registered before the data-partial handler so confirmation always comes
   * first; stopImmediatePropagation holds the other handlers back until the
   * form is re-submitted with the confirmed flag set. */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var question = form.dataset.confirm;
    if (!question || form.dataset.confirmed === "1") return;

    event.preventDefault();
    event.stopImmediatePropagation();

    askConfirmation(question, function () {
      form.dataset.confirmed = "1";
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
      delete form.dataset.confirmed;
    });
  });

  /* ---- inline row submit ----
   * <form data-partial data-target="#row-12"> posts in the background and
   * replaces the target with the HTML fragment the server returns. */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches("form[data-partial]")) return;
    event.preventDefault();

    var target = document.querySelector(form.dataset.target) || form.closest("tr");
    if (!target) { form.submit(); return; }

    form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });

    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Partial": "1", "X-CSRFToken": csrfToken() },
      credentials: "same-origin"
    })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var wrapper = document.createElement("tbody");
        wrapper.innerHTML = html.trim();
        var replacement = wrapper.firstElementChild;
        if (replacement) {
          target.replaceWith(replacement);
          replacement.classList.add("saved");
          window.setTimeout(function () { replacement.classList.remove("saved"); }, 1200);
        }
      })
      .catch(function () { form.submit(); });
  });

  /* ---- auto-submit selects inside a partial form ---- */
  document.addEventListener("change", function (event) {
    var select = event.target;
    if (!select.matches("select[data-autosubmit]")) return;
    var form = select.closest("form");
    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  });

  /* ---- polling live region ----
   * <div data-poll="/presence/" data-poll-interval="30"> */
  document.querySelectorAll("[data-poll]").forEach(function (region) {
    var url = region.dataset.poll;
    var seconds = parseInt(region.dataset.pollInterval || "30", 10);
    if (!url || seconds < 5) return;

    window.setInterval(function () {
      if (document.hidden) return;
      fetch(url + (url.indexOf("?") === -1 ? "?" : "&") + "_=" + Date.now(), {
        headers: { "X-Partial": "1" },
        credentials: "same-origin"
      })
        .then(function (response) { return response.ok ? response.text() : null; })
        .then(function (html) { if (html !== null) region.innerHTML = html; })
        .catch(function () { /* transient network error: try again next tick */ });
    }, seconds * 1000);
  });
})();
