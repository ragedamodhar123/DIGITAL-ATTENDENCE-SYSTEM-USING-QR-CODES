// Attendance face check: after a scanned code is accepted, take one photo of the
// student's face and send it. On a match the student is marked present.
(function () {
  "use strict";

  var root = document.getElementById("attend-face");
  if (!root || !window.AttendanceApi || !window.AttendanceCamera) return;
  var Camera = window.AttendanceCamera;

  var faceUrl = root.getAttribute("data-face-url");
  var secondsLeft = parseInt(root.getAttribute("data-seconds"), 10) || 60;
  var camera = root.querySelector(".camera");
  var video = root.querySelector(".camera-video");
  var prompt = root.querySelector(".capture-prompt");
  var errorBox = root.querySelector(".capture-error");
  var doneBox = root.querySelector(".checkin-done");
  var timerText = root.querySelector('[data-field="seconds"]');
  var timerLine = root.querySelector(".checkin-timer");
  var title = root.querySelector('[data-field="title"]');
  var buttons = {
    start: root.querySelector('[data-action="start"]'),
    capture: root.querySelector('[data-action="capture"]'),
    rescan: root.querySelector('[data-action="rescan"]'),
    home: root.querySelector('[data-action="home"]')
  };

  var stream = null;
  var busy = false;
  var finished = false;
  var countdown = null;

  function say(text) { prompt.textContent = text; }
  function showError(text) { errorBox.textContent = text; errorBox.classList.toggle("d-none", !text); }
  function showOnly(names) {
    Object.keys(buttons).forEach(function (name) { buttons[name].classList.toggle("d-none", names.indexOf(name) < 0); });
  }

  function stopCamera() {
    Camera.stop(video, stream);
    stream = null;
    camera.setAttribute("data-state", "idle");
  }

  // The check is over (success or a dead end): camera off, timer off, one way forward.
  function finish(nextButton) {
    finished = true;
    clearInterval(countdown);
    timerLine.classList.add("d-none");
    stopCamera();
    showOnly(nextButton ? [nextButton] : []);
  }

  function succeed(data) {
    finish("home");
    showError("");
    title.textContent = data.status === "already_present" ? "Already marked present" : "Marked present";
    say("");
    doneBox.textContent = "You are marked present for " + data.course + " at " + data.marked_at + ".";
    doneBox.classList.remove("d-none");
  }

  function startCamera() {
    if (finished || busy) return;
    showError("");
    var blocker = Camera.problem();
    if (blocker) { showError(blocker); return; }
    busy = true;
    buttons.start.disabled = true;
    say("Starting the camera...");
    Camera.start(video)
      .then(function (newStream) {
        stream = newStream;
        if (finished) { stopCamera(); return; }  // time ran out while the camera was starting
        camera.setAttribute("data-state", "live");
        showOnly(["capture"]);
        say("Look straight at the camera, then press Check my face.");
      })
      .catch(function (error) {
        stopCamera();
        say("Press \"Start camera\" to try again.");
        showError(Camera.errorMessage(error));
      })
      .then(function () { busy = false; buttons.start.disabled = false; });
  }

  function handleReply(result) {
    var data = result.data || {};
    if (result.ok) { succeed(data); return; }
    var reason = data.reason;

    if (result.status === 401) { window.location.reload(); return; }   // login expired
    if (reason === "face_mismatch") {
      var left = data.attempts_left;
      showError(data.message + (left ? " (" + left + (left === 1 ? " try" : " tries") + " left)" : ""));
      say("Look straight at the camera, then press Check my face.");
      return;
    }
    if (reason === "too_many_attempts" || reason === "already_recorded" || reason === "session_closed") {
      finish("home");
      showError(data.message);
      return;
    }
    if (reason === "checkin_expired" || reason === "no_checkin") {
      finish("rescan");
      showError(data.message);
      return;
    }
    // Photo problems (too dark, no face, ...), server busy, rate limit, connection: try again.
    showError(data.message || "Something went wrong. Try again.");
    say("Look straight at the camera, then press Check my face.");
  }

  function checkFace() {
    if (busy || finished) return;
    var image = Camera.grab(video);
    if (!image) { showError("The camera is not ready yet. Wait a moment and try again."); return; }
    busy = true;
    buttons.capture.disabled = true;
    buttons.capture.textContent = "Checking...";
    showError("");
    window.AttendanceApi.postJSON(faceUrl, { image: image }, { timeoutMs: 30000 }).then(function (result) {
      busy = false;
      buttons.capture.disabled = false;
      buttons.capture.textContent = "Check my face";
      if (!finished) handleReply(result);
    });
  }

  function tick() {
    secondsLeft -= 1;
    if (secondsLeft <= 0) {
      if (!busy) {
        finish("rescan");
        showError("Time ran out. Scan the code on the screen again.");
      }
      // If a check is on its way, its reply decides (the server knows the real deadline).
      return;
    }
    timerText.textContent = String(secondsLeft);
  }

  buttons.start.addEventListener("click", startCamera);
  buttons.capture.addEventListener("click", checkFace);
  countdown = setInterval(tick, 1000);

  document.addEventListener("visibilitychange", function () {
    if (document.hidden && stream && !busy && !finished) {
      stopCamera();
      showOnly(["start"]);
      say("Camera paused. Press \"Start camera\" to continue.");
    }
  });
  window.addEventListener("pagehide", stopCamera);
})();