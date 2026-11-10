// Shared front-camera helpers for the face pages (registration and attendance check).
(function () {
  "use strict";

  var MAX_SIDE = 960;       // photos are scaled down before upload (faster, enough detail)
  var JPEG_QUALITY = 0.9;

  function problem() {
    if (!window.isSecureContext) {
      return "The camera only works on a secure (https://) page. Ask your faculty for the correct link.";
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return "This browser cannot use the camera. Use Chrome on Android or Safari on iPhone.";
    }
    return null;
  }

  function errorMessage(error) {
    var name = error && error.name;
    if (name === "NotAllowedError" || name === "SecurityError") {
      return "Camera permission was blocked. Allow the camera for this site (see \"Camera not working?\" below), then press Start camera.";
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") return "No camera was found on this device.";
    if (name === "NotReadableError" || name === "AbortError") {
      return "The camera is busy. Close other apps using it (video calls, camera app) and try again.";
    }
    return "The camera could not be started. Reload the page and try again.";
  }

  // Starts the front camera in `video`. Resolves to the stream.
  function start(video) {
    var preferred = { audio: false, video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } } };
    var stream = null;
    return navigator.mediaDevices.getUserMedia(preferred)
      .catch(function (error) {
        // Some phones reject the size hints; try again with "any camera".
        if (error && error.name === "OverconstrainedError") {
          return navigator.mediaDevices.getUserMedia({ audio: false, video: true });
        }
        throw error;
      })
      .then(function (newStream) {
        stream = newStream;
        video.srcObject = stream;
        return video.play();
      })
      .then(function () { return stream; });
  }

  function stop(video, stream) {
    if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
    if (video) video.srcObject = null;
  }

  // One JPEG frame as a data URL, or null if the camera is not ready yet.
  // Drawn un-mirrored: the preview is mirrored (like a mirror), the photo is not.
  function grab(video) {
    var width = video.videoWidth;
    var height = video.videoHeight;
    if (!width || !height) return null;
    var scale = Math.min(1, MAX_SIDE / Math.max(width, height));
    var canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  }

  window.AttendanceCamera = { problem: problem, errorMessage: errorMessage, start: start, stop: stop, grab: grab };
})();