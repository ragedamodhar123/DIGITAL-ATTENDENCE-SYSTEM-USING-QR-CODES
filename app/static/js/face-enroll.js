// Face registration: phone camera -> N checked photos -> register.
// Each photo is checked by the server straight away, so the student fixes
// problems (too dark, too far, two faces...) before moving on.
(function () {
  "use strict";

  var root = document.getElementById("face-enroll");
  if (!root || !window.AttendanceApi || !window.AttendanceCamera) return;
  var Camera = window.AttendanceCamera;
  var PROMPTS = [
    "Look straight at the camera, then press Take photo.",
    "Tilt your head slightly, hold still, then press Take photo.",
    "Look straight again with a natural expression, then press Take photo."
  ];

  var needed = parseInt(root.getAttribute("data-captures"), 10) || 3;
  var camera = root.querySelector(".camera");
  var video = root.querySelector(".camera-video");
  var prompt = root.querySelector(".capture-prompt");
  var errorBox = root.querySelector(".capture-error");
  var startButton = root.querySelector('[data-action="start"]');
  var captureButton = root.querySelector('[data-action="capture"]');
  var steps = root.querySelectorAll(".capture-step");

  var stream = null;
  var captures = [];
  var busy = false;

  // ---- display helpers ----------------------------------------------------

  function say(text) { prompt.textContent = text; }

  function showError(text) {
    errorBox.textContent = text;
    errorBox.classList.toggle("d-none", !text);
  }

  function show(button, visible) { button.classList.toggle("d-none", !visible); }

  function setBusy(value, label) {
    busy = value;
    captureButton.disabled = value;
    startButton.disabled = value;
    if (label) captureButton.textContent = label;
  }

  function renderSteps() {
    for (var i = 0; i < steps.length; i++) {
      steps[i].classList.toggle("done", i < captures.length);
      steps[i].classList.toggle("current", i === captures.length);
    }
  }

  function promptForNext() {
    say("Photo " + (captures.length + 1) + " of " + needed + ": " + (PROMPTS[captures.length] || PROMPTS[0]));
  }

  // ---- camera ---------------------------------------------------------------

  function startCamera() {
    showError("");
    var blocker = Camera.problem();
    if (blocker) { showError(blocker); return; }
    setBusy(true);
    say("Starting the camera...");
    Camera.start(video)
      .then(function (newStream) {
        stream = newStream;
        camera.setAttribute("data-state", "live");
        show(startButton, false);
        show(captureButton, true);
        setBusy(false, "Take photo");
        renderSteps();
        promptForNext();
      })
      .catch(function (error) {
        stopCamera();
        setBusy(false);
        say("Press \"Start camera\" to try again.");
        showError(Camera.errorMessage(error));
      });
  }

  function stopCamera() {
    Camera.stop(video, stream);
    stream = null;
    camera.setAttribute("data-state", "idle");
    show(captureButton, false);
    show(startButton, true);
  }

  // ---- server replies ---------------------------------------------------------

  function handleCommonFailure(result) {
    if (result.status === 401) {
      window.location.href = root.getAttribute("data-login-url");
      return true;
    }
    if (result.status === 409) {
      window.location.reload(); // already registered (e.g. in another tab)
      return true;
    }
    return false;
  }

  function firstDetail(details) {
    // details.images may be a list, or an object keyed by photo number ("0", "1", ...)
    if (!details || !details.images) return null;
    var images = details.images;
    if (Array.isArray(images)) return images[0];
    var keys = Object.keys(images).sort();
    if (!keys.length) return null;
    return "Photo " + (parseInt(keys[0], 10) + 1) + ": " + images[keys[0]][0];
  }

  // ---- flow -------------------------------------------------------------------

  function takePhoto() {
    if (busy) return;
    var image = Camera.grab(video);
    if (!image) {
      showError("The camera is not ready yet. Wait a moment and try again.");
      return;
    }
    showError("");
    setBusy(true, "Checking...");

    window.AttendanceApi.postJSON(root.getAttribute("data-check-url"), { image: image }).then(function (result) {
      setBusy(false, "Take photo");
      if (result.ok) {
        captures.push(image);
        renderSteps();
        if (captures.length >= needed) {
          register();
        } else {
          promptForNext();
        }
        return;
      }
      if (handleCommonFailure(result)) return;
      showError((result.data && result.data.message) || "That photo could not be used. Try again.");
      promptForNext();
    });
  }

  function register() {
    setBusy(true, "Saving...");
    say("Saving your registration...");

    window.AttendanceApi.postJSON(root.getAttribute("data-enroll-url"), { images: captures }, { timeoutMs: 45000 })
      .then(function (result) {
        if (result.ok) {
          stopCamera();
          show(startButton, false);
          say("Your face is registered. Taking you to your home page...");
          window.location.href = (result.data && result.data.next) || "/";
          return;
        }
        if (handleCommonFailure(result)) return;
        // Start the photos again from the beginning.
        captures = [];
        renderSteps();
        setBusy(false, "Take photo");
        showError(firstDetail(result.data && result.data.details) || (result.data && result.data.message) ||
          "Registration did not work. Take the photos again.");
        promptForNext();
      });
  }

  startButton.addEventListener("click", startCamera);
  captureButton.addEventListener("click", takePhoto);

  // Turn the camera off when the student leaves the page or switches app;
  // photos already taken are kept, and "Start camera" picks up where they left off.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden && stream && !busy) {
      stopCamera();
      say("Camera paused. Press \"Start camera\" to continue.");
    }
  });
  window.addEventListener("pagehide", stopCamera);
})();