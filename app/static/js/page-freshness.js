// Logged-in pages only.
// Browsers keep a frozen copy of recent pages (the "back-forward cache") so the
// Back button feels instant. After logging out, that copy would still show the
// last page. When a page is restored from that cache, reload it from the server
// instead: the server then sees there is no login and sends the browser to /login.
window.addEventListener("pageshow", function (event) {
  if (event.persisted) {
    window.location.reload();
  }
});