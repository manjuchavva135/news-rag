// OCR sanity check (ticket 009): link a block on the scan with the same block in the text list,
// and add rows to the sign-off form.
(function () {
  "use strict";

  function same(order) {
    return document.querySelectorAll('[data-order="' + order + '"]');
  }

  document.querySelectorAll(".box[data-order], .block[data-order]").forEach(function (el) {
    var order = el.getAttribute("data-order");
    el.addEventListener("mouseenter", function () {
      same(order).forEach(function (e) { e.classList.add("hot"); });
    });
    el.addEventListener("mouseleave", function () {
      same(order).forEach(function (e) { e.classList.remove("hot"); });
    });
    el.addEventListener("click", function () {
      var wasSelected = el.classList.contains("sel");
      document.querySelectorAll(".sel").forEach(function (e) { e.classList.remove("sel"); });
      if (wasSelected) return;
      same(order).forEach(function (e) {
        e.classList.add("sel");
        if (e !== el) e.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
      });
    });
  });

  var add = document.getElementById("add-row");
  var rows = document.getElementById("rows");
  var template = document.getElementById("row-template");
  if (add && rows && template) {
    add.addEventListener("click", function () {
      var index = rows.querySelectorAll("tr").length;
      rows.insertAdjacentHTML("beforeend", template.innerHTML.replace(/__I__/g, String(index)));
    });
  }
})();
