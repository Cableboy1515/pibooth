/* pibooth web configuration interface — photo upload queue page */
"use strict";

/* Pushed into the same global CUSTOM_PAGES array read by app.js renderNav()
 * (see events.js for the array declaration).
 */
CUSTOM_PAGES.push({ id: "UPLOADS", label: "Uploads", icon: "☁️", render: renderUploadsPage, group: "event", order: 1 });

const uploadsState = {
  loaded: false,
  backend: "none",
  entries: [],
  refreshTimer: null,
};

async function loadUploads() {
  const payload = await api("/api/uploads");
  uploadsState.backend = payload.backend;
  uploadsState.entries = payload.entries;
  uploadsState.loaded = true;
}

function currentUploadBackend() {
  if (!state.schema) return "none";
  const section = state.schema.sections.find((s) => s.name === "UPLOAD");
  const opt = section && section.options.find((o) => o.name === "upload_backend");
  return opt ? opt.value : "none";
}

function goToUploadSettings() {
  state.active = "UPLOAD";
  renderNav();
  renderSection("UPLOAD");
}

function buildServiceCard() {
  const goBtn = el("button", { class: "btn" }, "Open upload settings");
  goBtn.onclick = goToUploadSettings;

  const testBtn = el("button", { class: "btn primary" }, "Test connection");
  testBtn.onclick = async () => {
    testBtn.disabled = true;
    testBtn.textContent = "Testing…";
    try {
      const result = await api("/api/uploads/test", { method: "POST" });
      toast(result.message);
    } catch (error) {
      toast(`Test failed: ${error.message}`, "error", 6000);
    } finally {
      testBtn.disabled = false;
      testBtn.textContent = "Test connection";
    }
  };

  return el(
    "div",
    { class: "card" },
    el(
      "div",
      { class: "field" },
      el(
        "div",
        { class: "field-info" },
        el("div", { class: "field-label" }, "Service"),
        el(
          "div",
          { class: "field-help" },
          "Configure the service (folder, WebDAV or Google Photos) in the Upload settings section."
        )
      ),
      el("div", { class: "field-widget", style: "width:auto; flex-direction:row" }, goBtn, testBtn)
    )
  );
}

function buildGooglePhotosCard() {
  if (currentUploadBackend() !== "gphotos") return null;

  const fileInput = el("input", { type: "file", accept: ".json", hidden: true });
  const chooseBtn = el("button", { class: "btn primary" }, "Upload JSON file…");
  chooseBtn.onclick = () => fileInput.click();

  fileInput.onchange = async () => {
    if (!fileInput.files.length) return;
    const form = new FormData();
    form.append("file", fileInput.files[0]);
    fileInput.value = "";
    try {
      const result = await api("/api/uploads/google/credentials", { method: "POST", body: form });
      toast(`Saved the ${result.kind} file ✔`);
    } catch (error) {
      toast(`Could not save file: ${error.message}`, "error", 6000);
    }
  };

  return el(
    "div",
    { class: "card" },
    el("h2", { style: "font-size:16px; margin: 14px 0 4px" }, "Google Photos setup"),
    el(
      "ol",
      { class: "field-help", style: "padding-left: 18px" },
      el("li", {}, "Create a Desktop OAuth client with the Photos Library API enabled in the Google Cloud console."),
      el("li", {}, "Run 'pibooth-google-auth' on a computer with a browser to authorize pibooth."),
      el("li", {}, "Upload the two generated JSON files here, one at a time (client secret, then token).")
    ),
    el("div", { class: "field-widget", style: "width:auto; flex-direction:row; margin: 10px 0 16px" }, chooseBtn, fileInput)
  );
}

function statusBadge(status) {
  return el("span", { class: `status-badge ${status}` }, status);
}

function basenameOf(path) {
  return (path || "").split(/[\\/]/).pop();
}

function formatUploadDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function buildQueueCard() {
  const retryBtn = el("button", { class: "btn small" }, "Retry failed");
  retryBtn.onclick = async () => {
    try {
      await api("/api/uploads/retry", { method: "POST" });
      await loadUploads();
      renderUploadsPage();
      toast("Failed uploads queued for retry");
    } catch (error) {
      toast(`Could not retry: ${error.message}`, "error");
    }
  };

  const body = el("div", {});
  if (!uploadsState.entries.length) {
    body.append(el("div", { class: "asset-empty" }, "No upload yet."));
  } else {
    const table = el(
      "table",
      { class: "upload-table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {}, "Time"),
          el("th", {}, "File"),
          el("th", {}, "Status"),
          el("th", {}, "Attempts"),
          el("th", {}, "Error")
        )
      )
    );
    const tbody = el("tbody", {});
    for (const entry of uploadsState.entries) {
      const error = entry.error ? entry.error.slice(0, 140) : "";
      tbody.append(
        el(
          "tr",
          {},
          el("td", {}, formatUploadDate(entry.updated)),
          el("td", {}, basenameOf(entry.filename)),
          el("td", {}, statusBadge(entry.status)),
          el("td", {}, String(entry.attempts)),
          el("td", { class: "upload-error" }, error)
        )
      );
    }
    table.append(tbody);
    body.append(table);
  }

  return el(
    "div",
    { class: "card" },
    el(
      "div",
      { style: "display:flex; align-items:center; gap:12px; padding-top:14px" },
      el("h2", { style: "font-size:16px; margin:0; flex:1" }, "Queue"),
      retryBtn
    ),
    body
  );
}

function stopUploadsAutoRefresh() {
  if (uploadsState.refreshTimer) {
    clearInterval(uploadsState.refreshTimer);
    uploadsState.refreshTimer = null;
  }
}

function startUploadsAutoRefresh() {
  stopUploadsAutoRefresh();
  uploadsState.refreshTimer = setInterval(async () => {
    if (state.active !== "UPLOADS") {
      stopUploadsAutoRefresh();
      return;
    }
    try {
      await loadUploads();
      renderUploadsPage();
    } catch (error) {
      /* keep showing the last known state, next tick will retry */
    }
  }, 5000);
}

async function renderUploadsPage() {
  $("section-title").textContent = "Uploads";
  $("section-hint").textContent = "Status of the background photo upload queue for the current service.";

  const body = $("section-body");

  if (!uploadsState.loaded) {
    body.replaceChildren(el("div", { class: "preview-loading" }, "Loading uploads…"));
    try {
      await loadUploads();
    } catch (error) {
      body.replaceChildren(el("p", {}, `Cannot load uploads: ${error.message}`));
      return;
    }
  }

  const children = [buildServiceCard()];
  const googleCard = buildGooglePhotosCard();
  if (googleCard) children.push(googleCard);
  children.push(buildQueueCard());

  body.replaceChildren(...children);
  startUploadsAutoRefresh();
}
