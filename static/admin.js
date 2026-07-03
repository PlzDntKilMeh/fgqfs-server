(function () {
  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "Request failed");
    }
    return data;
  }

  async function activateRevision(button) {
    if (!confirm("Make this revision the active gameplay save?")) {
      return;
    }
    button.disabled = true;
    try {
      const data = await postJson("/admin/api/activate-revision", {
        revision_id: Number(button.dataset.revisionId),
        target_player_id: button.dataset.targetPlayerId,
      });
      window.location.assign(data.redirect || "/admin");
    } catch (error) {
      alert(error.message || "Activation failed");
      button.disabled = false;
    }
  }

  function updateSpecificPlayerInput() {
    const targetSelect = document.querySelector('select[name="target_mode"]');
    const specificInput = document.querySelector('input[name="specific_player_id"]');
    if (!targetSelect || !specificInput) {
      return;
    }
    const enabled = targetSelect.value === "specific";
    specificInput.disabled = !enabled;
    specificInput.required = enabled;
    if (!enabled) {
      specificInput.value = "";
    }
  }

  function prepareUploadForm(form) {
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Uploading...";
    }
  }

  async function downloadLiveSave() {
    const button = document.getElementById("live-download-button");
    const email = document.getElementById("live-email").value.trim();
    const password = document.getElementById("live-password").value;
    const target = document.getElementById("live-target").value.trim();

    button.disabled = true;
    button.textContent = "Downloading...";
    try {
      const data = await postJson("/admin/api/download-live-save", {
        email,
        password,
        target_player_id: target,
      });
      window.location.assign(data.redirect || "/admin");
    } catch (error) {
      alert(error.message || "Live download failed");
      button.disabled = false;
      button.textContent = "Download and activate";
    }
  }

  document.addEventListener("click", (event) => {
    const revisionButton = event.target.closest(".js-activate-revision");
    if (revisionButton) {
      activateRevision(revisionButton);
    }
  });

  document.querySelector('select[name="target_mode"]')?.addEventListener("change", updateSpecificPlayerInput);
  document.querySelector('form[action="/admin/upload-save"]')?.addEventListener("submit", (event) => {
    prepareUploadForm(event.currentTarget);
  });
  document.getElementById("live-download-button")?.addEventListener("click", downloadLiveSave);
  updateSpecificPlayerInput();
})();
