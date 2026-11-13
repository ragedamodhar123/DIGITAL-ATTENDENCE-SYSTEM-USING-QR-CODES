// Attendance face check with liveness: after a scanned code is accepted, the
// student looks straight, then turns their head slightly one way and then the
// other, in a random order chosen by the server. A few frames are taken
// automatically at each step and sent together. A photo or phone screen held up
// to the camera cannot turn in 3D, so it fails on the server.
(function () {
  "use strict";

  var root = document.getElementById("attend-face");
  if (!root || !window.AttendanceApi || !window.AttendanceCamera) return;
  var Camera = window.AttendanceCamera;

  var STRAIGHT_MAX_SIDE = 960;  // the identity check uses the straight frames: keep detail
  var TURN_MAX_SIDE = 640;      // turn frames only need landmarks: smaller and faster to send
  var SETTLE_MS = 900;          // time to follow a prompt before frames are taken
  var FRAME_GAP_MS = 250;
  var STRAIGHT_FRAMES = 2;
  var TURN_FRAMES = 4;
  var TURN_TEXT = { L: "Turn your head slightly to your LEFT", R: "Turn your head slightly to your RIGHT" };

  var faceUrl = root.getAttribute("data-face-url");
  var challenge = root.getAttribute("data-challenge") === "RL" ? "RL" : "LR";
  var arrow = root.querySelector(".turn-arrow");
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
        say("Press Check my face, then follow the arrows: look straight, then turn your head slightly each way.");
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
    if (reason === "face_mismatch" || reason === "liveness_failed") {
      var left = data.attempts_left;
      showError(data.message + (left ? " (" + left + (left === 1 ? " try" : " tries") + " left)" : ""));
      say("Press Check my face and follow the arrows again.");
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
    say("Press Check my face and follow the arrows again.");
  }

  function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  function showArrow(letter) {
    // The preview is mirrored like a mirror, so "your left" points to the left of the screen.
    if (!arrow) return;
    arrow.setAttribute("data-dir", letter || "");
    arrow.textContent = letter === "L" ? "\u2190" : letter === "R" ? "\u2192" : "";
  }

  // Take `count` frames, FRAME_GAP_MS apart. Resolves to the list, or null if the camera stopped.
  function takeFrames(count, maxSide) {
    var frames = [];
    function next() {
      if (!stream || finished) return Promise.resolve(null);
      var frame = Camera.grab(video, maxSide);
      if (frame) frames.push(frame);
      if (frames.length >= count) return Promise.resolve(frames);
      return wait(FRAME_GAP_MS).then(next);
    }
    return next();
  }

  function step(text, letter, count, maxSide) {
    say(text);
    showArrow(letter);
    return wait(SETTLE_MS).then(function () { return takeFrames(count, maxSide); });
  }

  function checkFace() {
    if (busy || finished) return;
    if (!Camera.grab(video)) { showError("The camera is not ready yet. Wait a moment and try again."); return; }
    busy = true;
    buttons.capture.disabled = true;
    buttons.capture.textContent = "Follow the arrows...";
    showError("");

    var steps = [];
    step("Look straight at the camera", null, STRAIGHT_FRAMES, STRAIGHT_MAX_SIDE)
      .then(function (frames) {
        if (!frames) return null;
        steps.push(frames);
        return step(TURN_TEXT[challenge[0]], challenge[0], TURN_FRAMES, TURN_MAX_SIDE);
      })
      .then(function (frames) {
        if (!frames) return null;
        steps.push(frames);
        return step(TURN_TEXT[challenge[1]], challenge[1], TURN_FRAMES, TURN_MAX_SIDE);
      })
      .then(function (frames) {
        showArrow(null);
        if (!frames) return null;   // camera stopped or time ran out mid-sequence
        steps.push(frames);
        say("Checking...");
        buttons.capture.textContent = "Checking...";
        return window.AttendanceApi.postJSON(faceUrl, { steps: steps }, { timeoutMs: 30000 });
      })
      .then(function (result) {
        busy = false;
        buttons.capture.disabled = false;
        buttons.capture.textContent = "Check my face";
        if (!result) {
          if (!finished) say("Press \"Check my face\" to try again.");
          return;
        }
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