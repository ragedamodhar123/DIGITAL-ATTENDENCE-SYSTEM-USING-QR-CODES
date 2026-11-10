// Small helper for calling the app's JSON endpoints from pages.
// - sends the CSRF token (from <meta name="csrf-token">) with every POST
// - never throws: always resolves to { ok, status, data } (postJSON and getJSON)
// - turns network problems and timeouts into a readable message
(function () {
  "use strict";

  var DEFAULT_TIMEOUT_MS = 20000;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function postJSON(url, body, options) {
    var timeoutMs = (options && options.timeoutMs) || DEFAULT_TIMEOUT_MS;
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;

    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(body),
      signal: controller ? controller.signal : undefined
    })
      .then(function (response) {
        return response.json().then(
          function (data) { return { ok: response.ok, status: response.status, data: data || {} }; },
          function () {
            return { ok: false, status: response.status, data: { message: "The server sent an unexpected reply. Try again." } };
          }
        );
      })
      .catch(function (error) {
        var timedOut = error && error.name === "AbortError";
        return {
          ok: false,
          status: 0,
          data: {
            message: timedOut
              ? "The server is taking too long. Check your connection and try again."
              : "Could not reach the server. Check your connection and try again."
          }
        };
      })
      .finally(function () { if (timer) clearTimeout(timer); });
  }

  function getJSON(url, options) {
    var timeoutMs = (options && options.timeoutMs) || DEFAULT_TIMEOUT_MS;
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;

    return fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      cache: "no-store",
      signal: controller ? controller.signal : undefined
    })
      .then(function (response) {
        return response.json().then(
          function (data) { return { ok: response.ok, status: response.status, data: data || {} }; },
          function () { return { ok: false, status: response.status, data: {} }; }
        );
      })
      .catch(function () { return { ok: false, status: 0, data: {} }; })
      .finally(function () { if (timer) clearTimeout(timer); });
  }

  window.AttendanceApi = { postJSON: postJSON, getJSON: getJSON };
})();