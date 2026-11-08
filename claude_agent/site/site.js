// Tema claro/oscuro (recordado en este navegador) y ampliación de gráficos.
(function () {
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem("theme");
    if (saved) root.setAttribute("data-theme", saved);
  } catch (e) {}
  var btn = document.querySelector(".theme-toggle");
  if (btn) btn.addEventListener("click", function () {
    var dark = root.getAttribute("data-theme")
      ? root.getAttribute("data-theme") === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) {}
  });
  var box = document.querySelector(".lightbox");
  if (!box) return;
  var big = box.querySelector("img");
  document.querySelectorAll(".prose img").forEach(function (img) {
    img.addEventListener("click", function () {
      big.src = img.src; big.alt = img.alt; box.hidden = false;
    });
  });
  box.addEventListener("click", function () { box.hidden = true; });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") box.hidden = true; });
})();
