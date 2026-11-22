// Applied from <head> so there is no flash of the wrong theme. Choice is remembered per browser; default follows the OS.
(function () {
  try { var t = localStorage.getItem("mp-theme"); if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t); } catch (e) { /* storage unavailable */ }
})();
