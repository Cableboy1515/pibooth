/* pibooth web configuration interface — event profiles page */
"use strict";

/* Pages injected before the config sections in the sidebar nav (see app.js
 * renderNav()). Each entry renders itself into #section-title/#section-hint/
 * #section-body when clicked.
 */
const CUSTOM_PAGES = [{ id: "EVENTS", label: "Events", icon: "🎉", render: renderEventsPage }];

const eventsState = {
  loaded: false,
  active: "",
  events: [],
};

async function loadEvents() {
  const payload = await api("/api/events");
  eventsState.active = payload.active;
  eventsState.events = payload.events;
  eventsState.loaded = true;
}

function formatEventDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function buildEventCard(event) {
  const isActive = event.name === eventsState.active;
  const imagesLabel = event.images.length
    ? `${event.images.length} image${event.images.length > 1 ? "s" : ""}`
    : "No images";

  const applyBtn = el("button", { class: "btn small primary" }, "Apply");
  applyBtn.onclick = async () => {
    if (!confirm(`Apply event "${event.name}" to the booth?`)) return;
    try {
      await api(`/api/events/${encodeURIComponent(event.name)}/apply`, { method: "POST" });
      await Promise.all([loadEvents(), loadConfig()]);
      renderNav();
      renderEventsPage();
      loadStatus();
      toast("Event applied — booth updated ✔");
    } catch (error) {
      toast(`Could not apply event: ${error.message}`, "error");
    }
  };

  const updateBtn = el("button", { class: "btn small" }, "Update from current");
  updateBtn.onclick = async () => {
    if (!confirm(`Update "${event.name}" with the current settings?`)) return;
    try {
      await api(`/api/events/${encodeURIComponent(event.name)}`, { method: "PUT" });
      await loadEvents();
      renderEventsPage();
      toast(`Event "${event.name}" updated ✔`);
    } catch (error) {
      toast(`Could not update event: ${error.message}`, "error");
    }
  };

  const deleteBtn = el("button", { class: "btn small ghost" }, "Delete");
  deleteBtn.onclick = async () => {
    if (!confirm(`Delete event "${event.name}"? This cannot be undone.`)) return;
    try {
      await api(`/api/events/${encodeURIComponent(event.name)}`, { method: "DELETE" });
      await Promise.all([loadEvents(), loadConfig()]);
      renderNav();
      renderEventsPage();
      loadStatus();
      toast(`Event "${event.name}" deleted`);
    } catch (error) {
      toast(`Could not delete event: ${error.message}`, "error");
    }
  };

  return el(
    "div",
    { class: `event-card${isActive ? " active" : ""}` },
    el(
      "div",
      { class: "event-card-head" },
      el("span", { class: "event-icon" }, "🎉"),
      el(
        "div",
        { class: "event-card-title" },
        el("div", { class: "event-name" }, event.name),
        isActive ? el("span", { class: "badge" }, "applied") : null
      )
    ),
    el("div", { class: "event-meta" }, `Saved ${formatEventDate(event.modified)} · ${imagesLabel}`),
    el("div", { class: "row" }, applyBtn, updateBtn, deleteBtn)
  );
}

function buildEventsHeader() {
  const input = el("input", { type: "text", placeholder: "e.g. Smith Wedding" });
  const saveBtn = el("button", { class: "btn primary" }, "Save current look as new event");

  async function save() {
    const name = input.value.trim();
    if (!name) {
      toast("Enter a name for the event", "error");
      return;
    }
    try {
      await api("/api/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      input.value = "";
      await loadEvents();
      renderEventsPage();
      toast(`Event "${name}" saved ✔`);
    } catch (error) {
      toast(`Could not save event: ${error.message}`, "error");
    }
  }

  saveBtn.onclick = save;
  input.onkeydown = (event) => {
    if (event.key === "Enter") save();
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
        el("div", { class: "field-label" }, "New event"),
        el(
          "div",
          { class: "field-help" },
          "Snapshot the current look-and-feel settings (layout, texts, colors, images) as a reusable " +
            "event profile you can re-apply later."
        )
      ),
      el("div", { class: "field-widget", style: "width:auto; flex-direction:row" }, input, saveBtn)
    )
  );
}

function buildEventsGrid() {
  if (!eventsState.events.length) {
    return el("div", { class: "asset-empty" }, "No event saved yet.");
  }
  return el("div", { class: "event-grid" }, ...eventsState.events.map(buildEventCard));
}

async function renderEventsPage() {
  $("section-title").textContent = "Events";
  $("section-hint").textContent =
    "Save the current look as a reusable event profile (e.g. a wedding or a party), and re-apply it later.";

  const body = $("section-body");

  if (!eventsState.loaded) {
    body.replaceChildren(el("div", { class: "preview-loading" }, "Loading events…"));
    try {
      await loadEvents();
    } catch (error) {
      body.replaceChildren(el("p", {}, `Cannot load events: ${error.message}`));
      return;
    }
  }

  body.replaceChildren(buildEventsHeader(), buildEventsGrid());
}
