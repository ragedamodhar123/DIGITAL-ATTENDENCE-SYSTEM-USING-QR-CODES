// "Tick everyone" / "Clear" buttons: data-select-all="<checkbox name>", data-select-none="<checkbox name>".
document.addEventListener("click", function (event) {
  var button = event.target;
  var name = button.getAttribute && (button.getAttribute("data-select-all") || button.getAttribute("data-select-none"));
  if (!name) return;
  var tick = button.hasAttribute("data-select-all");
  document.querySelectorAll('input[type="checkbox"][name="' + name + '"]').forEach(function (box) { box.checked = tick; });
});