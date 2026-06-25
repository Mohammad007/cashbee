/* CashBee admin — sidebar collapse/toggle + profile dropdown. */
(function () {
  "use strict";

  var root = document.documentElement;
  var toggle = document.getElementById("sidebarToggle");
  var scrim = document.getElementById("scrim");
  var mobile = window.matchMedia("(max-width: 860px)");

  // --- Sidebar toggle ---------------------------------------------------- //
  // Desktop: collapse to an icon-only rail (persisted in localStorage).
  // Mobile : slide the off-canvas sidebar in/out with a scrim.
  function setCollapsed(on) {
    root.classList.toggle("sidebar-collapsed", on);
    try { localStorage.setItem("cb_sidebar", on ? "collapsed" : "expanded"); } catch (e) {}
  }
  function closeMobile() { root.classList.remove("sidebar-open"); }

  if (toggle) {
    toggle.addEventListener("click", function () {
      if (mobile.matches) {
        root.classList.toggle("sidebar-open");
      } else {
        setCollapsed(!root.classList.contains("sidebar-collapsed"));
      }
    });
  }
  if (scrim) scrim.addEventListener("click", closeMobile);
  // Leaving mobile width should clear the off-canvas state.
  mobile.addEventListener("change", function (e) { if (!e.matches) closeMobile(); });

  // --- Profile dropdown -------------------------------------------------- //
  var trigger = document.getElementById("profileTrigger");
  var profile = document.getElementById("profile");
  if (trigger && profile) {
    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = profile.classList.toggle("open");
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", function (e) {
      if (!profile.contains(e.target)) {
        profile.classList.remove("open");
        trigger.setAttribute("aria-expanded", "false");
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        profile.classList.remove("open");
        trigger.setAttribute("aria-expanded", "false");
      }
    });
  }

  // --- Modals ------------------------------------------------------------ //
  function openModal(modal) {
    if (!modal) return;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    var focusable = modal.querySelector("input, button.btn-primary, button.btn-danger");
    if (focusable) setTimeout(function () { focusable.focus(); }, 50);
  }
  function closeModals() {
    document.querySelectorAll(".modal.open").forEach(function (m) {
      m.classList.remove("open");
      m.setAttribute("aria-hidden", "true");
    });
    document.body.style.overflow = "";
  }
  // Close on overlay / [data-close] / Escape.
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-close]")) closeModals();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModals();
  });

  // Edit-Ad modal: prefill the shared form from the row's data-* attributes.
  var editAdModal = document.getElementById("editAdModal");
  var editAdForm = document.getElementById("editAdForm");
  document.querySelectorAll(".js-edit-ad").forEach(function (btn) {
    btn.addEventListener("click", function () {
      editAdForm.action = btn.dataset.action;
      editAdForm.querySelector("#editAdTitleInput").value = btn.dataset.title || "";
      editAdForm.querySelector("#editAdCoins").value = btn.dataset.coins || "";
      editAdForm.querySelector("#editAdLimit").value = btn.dataset.limit || "";
      editAdForm.querySelector("#editAdActive").checked = btn.dataset.active === "1";
      openModal(editAdModal);
    });
  });

  // Confirmation modal: a single dialog reused for delete / ban / approve / reject.
  var confirmModal = document.getElementById("confirmModal");
  var confirmForm = document.getElementById("confirmForm");
  if (confirmModal && confirmForm) {
    var cTitle = document.getElementById("confirmTitle");
    var cMsg = document.getElementById("confirmMessage");
    var cBtn = document.getElementById("confirmBtn");
    var cIcon = document.getElementById("confirmIcon");
    var cHidden = document.getElementById("confirmHidden");
    var cReasonWrap = document.getElementById("confirmReasonWrap");
    var cReasonLabel = document.getElementById("confirmReasonLabel");
    var cReasonInput = document.getElementById("confirmReasonInput");

    document.querySelectorAll(".js-confirm").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var d = btn.dataset;
        confirmForm.action = d.action;
        cTitle.textContent = d.title || "Are you sure?";
        cMsg.textContent = d.message || "";
        cBtn.textContent = d.confirmText || "Confirm";

        // Variant styling for the confirm button + header icon.
        var primary = d.variant === "primary";
        cBtn.className = "btn " + (primary ? "btn-primary" : "btn-danger");
        cIcon.className = "confirm-icon" + (primary ? " primary" : "");
        cIcon.innerHTML = '<i class="fa-solid ' + (d.icon || (primary ? "fa-circle-question" : "fa-triangle-exclamation")) + '"></i>';

        // Reset + inject hidden fields (e.g. banned=1).
        cHidden.innerHTML = "";
        if (d.fields) {
          try {
            var obj = JSON.parse(d.fields);
            Object.keys(obj).forEach(function (k) {
              var i = document.createElement("input");
              i.type = "hidden"; i.name = k; i.value = obj[k];
              cHidden.appendChild(i);
            });
          } catch (err) {}
        }

        // Optional reason text field (e.g. withdrawal rejection note).
        if (d.reason) {
          cReasonWrap.style.display = "";
          cReasonLabel.textContent = d.reasonLabel || "Reason";
          cReasonInput.name = d.reason;
          cReasonInput.value = "";
          cReasonInput.placeholder = d.reasonPlaceholder || "";
        } else {
          cReasonWrap.style.display = "none";
          cReasonInput.removeAttribute("name");
        }

        openModal(confirmModal);
      });
    });
  }
})();
