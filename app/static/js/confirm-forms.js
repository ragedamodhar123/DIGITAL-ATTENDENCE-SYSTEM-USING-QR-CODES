// Forms with a data-confirm="..." attribute ask before submitting.
// Kept in a file (not inline onsubmit=) so a strict Content-Security-Policy can be added later.
document.addEventListener("submit", function (event) {
  var message = event.target.getAttribute && event.target.getAttribute("data-confirm");
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});