// In-app QR scanner. Reads the code on the faculty's screen and opens the
// check-in page. Only codes that point to THIS site's /a/<code> check-in page
// are followed: a QR code leading anywhere else (for example a fake code stuck
// over the real one) is refused instead of being opened.
(function () {
  "use strict";

  var root = document.getElementById("qr-scan");
  if (!root) return;

  var TOKEN_PATTERN = /^\d{1,10}\.\d{1,12}\.[A-Za-z0-9_-]{22}$/;
  var scanPath = root.getAttribute("data-scan-path") || "/a/";
  var statusText = root.querySelector(".scan-status");
  var errorBox = root.querySelector(".scan-error");
  var startButton = root.querySelector('[data-action="start"]');
  var scanner = null;
  var running = false;
  var handled = false;

  function say(text) { statusText.textContent = text; }
  function showError(text) {
    errorBox.textContent = text;
    errorBox.classList.toggle("d-none", !text);
  }

  // Exposed for testing: returns the same-site path to open, or null.
  function checkinPathFrom(text) {
    var url;
    try { url = new URL(String(text).trim(), window.location.origin); } catch (e) { return null; }
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.pathname.indexOf(scanPath) !== 0) return null;
    var token = decodeURIComponent(url.pathname.slice(scanPath.length));
    if (!TOKEN_PATTERN.test(token)) return null;
    // Always open it on this site, whatever address is inside the code.
    return scanPath + token;
  }
  window.__checkinPathFrom = checkinPathFrom;

  function cameraErrorMessage(error) {
    var text = String((error && (error.name || error.message)) || error || "");
    if (/NotAllowed|Permission|denied/i.test(text)) {
      return "Camera permission was blocked. Allow the camera for this site, then press Start scanning.";
    }
    if (/NotFound|no camera|Requested device not found/i.test(text)) return "No camera was found on this device.";
    if (/NotReadable|in use|Could not start/i.test(text)) {
      return "The camera is busy. Close other apps using it and try again.";
    }
    return "The camera could not be started. You can scan the code with your phone's camera app instead.";
  }

  function stop() {
    if (scanner && running) {
      running = false;
      return scanner.stop().catch(function () {});
    }
    return Promise.resolve();
  }

  function onDecoded(text) {
    if (handled) return;
    var path = checkinPathFrom(text);
    if (!path) {
      showError("That QR code is not an attendance code. Scan the code on your faculty's screen.");
      return;
    }
    handled = true;
    say("Code found. Opening...");
    stop().then(function () { window.location.assign(path); });
  }

  function start() {
    showError("");
    if (!window.isSecureContext) {
      showError("The camera only works on a secure (https://) page. Ask your faculty for the correct link.");
      return;
    }
    if (typeof window.Html5Qrcode !== "function") {
      showError("The scanner is not available. Scan the code with your phone's camera app instead.");
      return;
    }
    if (!scanner) scanner = new window.Html5Qrcode("qr-reader", { verbose: false });
    startButton.disabled = true;
    say("Starting the camera...");
    scanner
      .start(
        { facingMode: "environment" },
        {
          fps: 10,
          qrbox: function (width, height) {
            var side = Math.floor(Math.min(width, height) * 0.7);
            return { width: side, height: side };
          }
        },
        onDecoded,
        function () { /* no code in this frame: normal, ignore */ }
      )
      .then(function () {
        running = true;
        startButton.classList.add("d-none");
        say("Point the camera at the code on the screen.");
      })
      .catch(function (error) {
        startButton.disabled = false;
        say("Press \"Start scanning\" to try again.");
        showError(cameraErrorMessage(error));
      });
  }

  startButton.addEventListener("click", start);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden && running && !handled) {
      stop();
      startButton.classList.remove("d-none");
      startButton.disabled = false;
      say("Scanning paused. Press \"Start scanning\" to continue.");
    }
  });
  window.addEventListener("pagehide", stop);
})();