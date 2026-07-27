/* pibooth web configuration interface */
"use strict";

const state = {
  schema: null, // /api/config payload
  status: null, // /api/status payload
  printers: null, // /api/printers payload
  printerCaps: null, // /api/printers/<name>/capabilities payload, refreshed per PRINTER section render
  printerCapsFor: null, // printer name the current printerCaps was fetched for
  assets: [], // /api/assets payload
  designs: [], // /api/designs payload (overlay-designer creations)
  dirty: {}, // {SECTION: {option: rawConfigString}}
  active: null, // active section name
  previewVariant: 0,
  assetCallback: null, // callback of the currently open asset picker
  pickerDesignLayer: null, // "overlay" | "background" | null: whether the open picker also offers designs, and for which layer
};

//: Same inches as pibooth.printer.PAPER_FORMATS, duplicated here so the
//: frontend can annotate paper size choices without an extra round-trip.
const PAPER_FORMATS_INCHES = {
  "2x6": [2, 6],
  "3,5x5": [3.5, 5],
  "4x6": [4, 6],
  "5x7": [5, 7],
  "6x8": [6, 8],
  "6x9": [6, 9],
};

const SECTION_ICONS = {
  GENERAL: "⚙️",
  WEB: "🌐",
  WINDOW: "🖥️",
  PICTURE: "🖼️",
  CAMERA: "📷",
  PRINTER: "🖨️",
  UPLOAD: "📤",
  CONTROLS: "🎛️",
};

const SECTION_HINTS = {
  GENERAL: "Language, picture folders and startup behaviour.",
  WEB: "Settings of this configuration interface (applied at next startup).",
  WINDOW: "What guests see on the booth screen.",
  PICTURE: "Layout and look of the final picture — check the preview below.",
  CAMERA: "Camera backend and capture settings.",
  PRINTER: "Printing through CUPS.",
  UPLOAD: "Where the final picture is uploaded after each session — see the Uploads page for queue status.",
  CONTROLS: "Hardware buttons and LEDs wiring (GPIO).",
};

/* ------------------------------------------------------------------ utils */

function $(id) {
  return document.getElementById(id);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let described = false;
    try {
      const body = await response.json();
      if (body.description) {
        message = body.description;
        described = true;
      }
    } catch (e) {
      /* not json */
    }
    if (!described && (response.status === 405 || response.status === 404)) {
      // A bare 404/405 (no JSON description) means the URL never reached our
      // API — typical when the frontend files are newer than the running server
      message += " — if pibooth was updated recently, restart it and reload this page";
    }
    throw new Error(message);
  }
  return response.json();
}

function toast(message, type = "", timeout = 3500) {
  const node = el("div", { class: `toast ${type}` }, message);
  $("toasts").append(node);
  setTimeout(() => node.remove(), timeout);
}

function parseColor(value) {
  const match = /^\(?\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)?$/.exec(value || "");
  if (!match) return null;
  const [r, g, b] = [match[1], match[2], match[3]].map(Number);
  if ([r, g, b].some((c) => c > 255)) return null;
  const hex = (c) => c.toString(16).padStart(2, "0");
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

function hexToTuple(hex) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `(${r}, ${g}, ${b})`;
}

function parseCaptures(value) {
  const numbers = (value || "").match(/\d/g) || [];
  return numbers.map(Number).filter((n) => n >= 1 && n <= 4);
}

function basename(path) {
  return (path || "").split(/[\\/]/).pop();
}

// Mirrors pibooth.printer.parse_pwg_media: parses the trailing "WxHunit"
// chunk of a PWG self-describing media name, portrait-normalized.
function parsePwgMedia(name) {
  if ((name || "").startsWith("custom_")) return null; // range bound, not a size
  const match = /_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(in|mm)$/.exec(name || "");
  if (!match) return null;
  let width = Number(match[1]);
  let height = Number(match[2]);
  if (match[3] === "mm") {
    width /= 25.4;
    height /= 25.4;
  }
  return width <= height ? [width, height] : [height, width];
}

function paperSizeSupported(paperKey, caps) {
  const target = PAPER_FORMATS_INCHES[paperKey];
  if (!target || !caps || !caps.available) return false;
  const [targetWidth, targetHeight] = target[0] <= target[1] ? target : [target[1], target[0]];
  return (caps.media || []).some((entry) => {
    const parsed = entry.inches || parsePwgMedia(entry.name);
    return parsed && Math.abs(parsed[0] - targetWidth) <= 0.08 && Math.abs(parsed[1] - targetHeight) <= 0.08;
  });
}

/* ---------------------------------------------------------- dirty tracking */

function encodeValue(opt, displayValue) {
  if (opt.kind === "image" || (opt.kind === "color_or_image" && displayValue && !parseColor(displayValue))) {
    return displayValue ? `"${displayValue}"` : "";
  }
  if (opt.quoted && displayValue !== "") {
    return `"${displayValue}"`;
  }
  return String(displayValue);
}

function change(section, opt, displayValue) {
  const raw = encodeValue(opt, displayValue);
  const original = encodeValue(opt, opt.value);
  if (!state.dirty[section]) state.dirty[section] = {};
  if (raw === original) {
    delete state.dirty[section][opt.name];
    if (!Object.keys(state.dirty[section]).length) delete state.dirty[section];
  } else {
    state.dirty[section][opt.name] = raw;
  }
  renderNav();
  updateApplybar();
}

function dirtyCount() {
  return Object.values(state.dirty).reduce((acc, options) => acc + Object.keys(options).length, 0);
}

function updateApplybar() {
  const count = dirtyCount();
  $("applybar").classList.toggle("hidden", count === 0);
  $("applybar-text").textContent = count === 1 ? "1 unsaved change" : `${count} unsaved changes`;
}

async function applyChanges() {
  const cameraChanged = state.dirty.CAMERA && "type" in state.dirty.CAMERA;
  const webChanged = !!state.dirty.WEB;
  try {
    await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values: state.dirty }),
    });
  } catch (error) {
    toast(`Could not save: ${error.message}`, "error");
    return;
  }
  state.dirty = {};
  await loadConfig();
  loadStatus();
  renderNav();
  renderActive();
  updateApplybar();
  toast("Settings applied — the booth has been updated ✔");
  if (cameraChanged) toast("Camera type changes take effect after restarting pibooth", "warn", 6000);
  if (webChanged) toast("Web interface changes take effect after restarting pibooth", "warn", 6000);
}

function discardChanges() {
  state.dirty = {};
  renderNav();
  renderActive();
  updateApplybar();
}

/* ---------------------------------------------------------------- widgets */

function widgetBool(section, opt) {
  const input = el("input", { type: "checkbox" });
  input.checked = opt.value === "True";
  input.onchange = () => change(section, opt, input.checked ? "True" : "False");
  return el("label", { class: "toggle" }, input, el("span", { class: "slider" }));
}

function widgetChoice(section, opt) {
  const select = el("select");
  const choices = [...opt.choices];
  if (!choices.includes(opt.value)) choices.unshift(opt.value);
  for (const choice of choices) {
    select.append(el("option", { value: choice }, choice));
  }
  select.value = opt.value;
  select.onchange = () => change(section, opt, select.value);
  return select;
}

function widgetNumber(section, opt) {
  const input = el("input", { type: "number", step: opt.kind === "float" ? "any" : "1" });
  input.value = opt.value;
  input.onchange = () => change(section, opt, input.value);
  return input;
}

function widgetText(section, opt, listId = null) {
  const attrs = { type: "text" };
  if (listId) attrs.list = listId;
  const input = el("input", attrs);
  input.value = opt.value;
  input.onchange = () => change(section, opt, input.value);
  return input;
}

function widgetColor(section, opt) {
  const hex = parseColor(opt.value);
  if (hex === null) {
    // Not a single color (e.g. a list of colors): keep an editable raw value
    return widgetText(section, opt);
  }
  const input = el("input", { type: "color", value: hex });
  const text = el("span", { class: "imgpick-name" }, opt.value);
  input.oninput = () => {
    const tuple = hexToTuple(input.value);
    text.textContent = tuple;
    change(section, opt, tuple);
  };
  return el("div", { class: "row" }, input, text);
}

/** Whether opt's picker should also offer overlay-designer creations, and
 * for which layer — only the Picture section's overlay/background fields
 * are wired into the picture-assembly pipeline the designer targets.
 */
function designLayerFor(section, opt) {
  if (section !== "PICTURE") return null;
  if (opt.name === "overlays") return "overlay";
  if (opt.name === "backgrounds") return "background";
  return null;
}

function widgetImage(section, opt) {
  let current = parseColor(opt.value) ? "" : opt.value;
  const designLayer = designLayerFor(section, opt);

  const thumb = el("div", { class: "thumb empty" }, "none");
  const name = el("span", { class: "imgpick-name" });

  function matchingDesign() {
    if (!current) return null;
    return (
      state.designs.find((d) => d.png_path === current) ||
      state.designs.find((d) => d.background_png_path === current) ||
      null
    );
  }

  function refresh() {
    const assetName = basename(current);
    const isAsset = state.assets.some((asset) => asset.name === assetName);
    const design = designLayer ? matchingDesign() : null;
    name.textContent = current ? (design ? `${design.name} (design)` : assetName) : "No image selected";
    if (design) {
      thumb.className = "thumb";
      const layer = design.png_path === current ? "overlay" : "background";
      thumb.style.backgroundImage = `url('/api/designs/${encodeURIComponent(design.name)}/image?layer=${layer}')`;
    } else if (current && isAsset) {
      thumb.className = "thumb";
      thumb.style.backgroundImage = `url('/api/assets/${encodeURIComponent(assetName)}')`;
    } else {
      thumb.className = "thumb empty";
      thumb.style.backgroundImage = "";
      thumb.textContent = current ? "🖼" : "none";
    }
  }

  const choose = el("button", { class: "btn small" }, "Choose image…");
  choose.onclick = () =>
    openAssetPicker(
      (path) => {
        current = path;
        refresh();
        change(section, opt, current);
      },
      { designLayer }
    );
  const clear = el("button", { class: "btn small ghost" }, "Clear");
  clear.onclick = () => {
    current = "";
    refresh();
    change(section, opt, "");
  };

  refresh();
  return el(
    "div",
    {},
    el("div", { class: "imgpick" }, thumb, el("div", {}, el("div", { class: "row" }, choose, clear), name))
  );
}

function widgetColorOrImage(section, opt) {
  const container = el("div", {});
  const body = el("div", {});
  let mode = parseColor(opt.value) || !opt.value ? "color" : "image";

  const colorBtn = el("button", {}, "Color");
  const imageBtn = el("button", {}, "Image");

  function renderBody() {
    colorBtn.className = mode === "color" ? "active" : "";
    imageBtn.className = mode === "image" ? "active" : "";
    body.replaceChildren();
    if (mode === "color") {
      const colorOpt = { ...opt, value: parseColor(opt.value) ? opt.value : "(255, 255, 255)" };
      body.append(widgetColor(section, colorOpt));
    } else {
      body.append(widgetImage(section, opt));
    }
  }

  colorBtn.onclick = () => {
    mode = "color";
    renderBody();
    change(section, opt, parseColor(opt.value) ? opt.value : "(255, 255, 255)");
  };
  imageBtn.onclick = () => {
    mode = "image";
    renderBody();
  };

  renderBody();
  container.append(el("div", { class: "segmented" }, colorBtn, imageBtn), body);
  return container;
}

function widgetCaptures(section, opt) {
  const current = parseCaptures(opt.value);
  const first = el("select");
  const second = el("select");
  for (const n of [1, 2, 3, 4]) first.append(el("option", { value: n }, `${n} photo${n > 1 ? "s" : ""}`));
  second.append(el("option", { value: "" }, "no second choice"));
  for (const n of [1, 2, 3, 4]) second.append(el("option", { value: n }, `${n} photo${n > 1 ? "s" : ""}`));
  first.value = current[0] || 4;
  second.value = current.length > 1 ? current[1] : "";

  function update() {
    const value = second.value === "" ? String(first.value) : `(${first.value}, ${second.value})`;
    change(section, opt, value);
    if (state.active === "PICTURE") renderPreviewTabs();
  }
  first.onchange = update;
  second.onchange = update;

  return el(
    "div",
    {},
    el("div", { class: "row" }, first),
    el("div", { class: "row" }, second),
    el("div", { class: "field-help" }, "Guests choose between the two on the booth screen.")
  );
}

function widgetPrinter(section, opt) {
  const container = el("div", {});

  function render() {
    container.replaceChildren();
    if (!state.printers || !state.printers.available) {
      container.append(widgetText(section, opt));
      container.append(
        el(
          "div",
          { class: "field-help" },
          state.printers === null ? "Loading printers…" : "CUPS is not reachable — type the printer name manually."
        )
      );
      return;
    }
    const select = el("select");
    const names = ["default", ...state.printers.printers];
    if (!names.includes(opt.value)) names.unshift(opt.value);
    for (const printerName of names) {
      let label = printerName;
      if (printerName === "default" && state.printers.default) label = `default (${state.printers.default})`;
      select.append(el("option", { value: printerName }, label));
    }
    select.value = opt.value;
    select.onchange = () => change(section, opt, select.value);
    container.append(select);
  }

  if (state.printers === null) {
    api("/api/printers")
      .then((payload) => {
        state.printers = payload;
        render();
      })
      .catch(() => {
        state.printers = { available: false, printers: [], default: null };
        render();
      });
  }
  render();
  return container;
}

function widgetPaper(section, opt) {
  const select = el("select");
  const choices = [...opt.choices];
  if (!choices.includes(opt.value)) choices.unshift(opt.value);
  for (const choice of choices) {
    const supported = choice !== "auto" && choice !== "default" && paperSizeSupported(choice, state.printerCaps);
    select.append(el("option", { value: choice }, supported ? `${choice} ✓` : choice));
  }
  select.value = opt.value;
  select.onchange = () => change(section, opt, select.value);
  return select;
}

function widgetTray(section, opt) {
  const caps = state.printerCaps;
  if (!caps || !caps.available || !caps.trays || !caps.trays.length) {
    return widgetText(section, opt);
  }
  const select = el("select");
  const choices = ["default", ...caps.trays];
  if (!choices.includes(opt.value)) choices.unshift(opt.value);
  for (const choice of choices) select.append(el("option", { value: choice }, choice));
  select.value = opt.value;
  select.onchange = () => change(section, opt, select.value);
  return select;
}

function widgetFor(section, opt) {
  switch (opt.kind) {
    case "bool":
      return widgetBool(section, opt);
    case "choice":
      return widgetChoice(section, opt);
    case "int":
    case "float":
      return widgetNumber(section, opt);
    case "color":
      return widgetColor(section, opt);
    case "color_or_image":
      return widgetColorOrImage(section, opt);
    case "image":
      return widgetImage(section, opt);
    case "font":
      return widgetText(section, opt, "fontlist");
    case "captures":
      return widgetCaptures(section, opt);
    case "printer":
      return widgetPrinter(section, opt);
    case "paper":
      return widgetPaper(section, opt);
    case "tray":
      return widgetTray(section, opt);
    default:
      return widgetText(section, opt);
  }
}

/* ---------------------------------------------------------------- preview */

let previewTabsNode = null;

function currentPrinterName() {
  const dirtyValue = state.dirty.PRINTER && state.dirty.PRINTER.printer_name;
  if (dirtyValue !== undefined) return dirtyValue.replace(/^"|"$/g, "");
  const section = state.schema.sections.find((s) => s.name === "PRINTER");
  const opt = section && section.options.find((o) => o.name === "printer_name");
  return opt ? opt.value : "default";
}

function fetchPrinterCapabilities(printerName) {
  api(`/api/printers/${encodeURIComponent(printerName)}/capabilities`)
    .then((payload) => {
      state.printerCaps = payload;
      if (state.active === "PRINTER") renderSection("PRINTER");
    })
    .catch(() => {
      state.printerCaps = { available: false };
      if (state.active === "PRINTER") renderSection("PRINTER");
    });
}

function buildPrinterCapsCard() {
  const caps = state.printerCaps;
  if (!caps || !caps.available) return null;
  const sizes =
    caps.media
      .filter((entry) => entry.inches || parsePwgMedia(entry.name))
      .map((entry) => entry.label)
      .join(", ") || "none reported";
  return el(
    "div",
    { class: "preview-card" },
    el("div", {}, `${caps.model || "Unknown model"} — ${caps.state_message || "ready"}`),
    el("div", {}, `Supported paper sizes: ${sizes}`)
  );
}

function currentCapturesValue() {
  const dirtyValue = state.dirty.PICTURE && state.dirty.PICTURE.captures;
  if (dirtyValue !== undefined) return dirtyValue;
  const section = state.schema.sections.find((s) => s.name === "PICTURE");
  const opt = section.options.find((o) => o.name === "captures");
  return opt ? opt.value : "(4, 1)";
}

function renderPreviewTabs() {
  if (!previewTabsNode) return;
  const choices = parseCaptures(currentCapturesValue());
  previewTabsNode.replaceChildren();
  choices.forEach((count, index) => {
    const button = el("button", {}, `${count} photo${count > 1 ? "s" : ""}`);
    if (index === Math.min(state.previewVariant, choices.length - 1)) button.className = "active";
    button.onclick = () => {
      state.previewVariant = index;
      renderPreviewTabs();
      refreshPreview();
    };
    previewTabsNode.append(button);
  });
}

function refreshPreview() {
  const body = $("preview-body");
  if (!body) return;
  body.replaceChildren(el("div", { class: "preview-loading" }, "Rendering preview…"));
  const image = new Image();
  image.onload = () => body.replaceChildren(image);
  image.onerror = () =>
    body.replaceChildren(el("div", { class: "preview-loading" }, "Preview unavailable (see pibooth logs)"));
  image.src = `/api/preview?variant=${state.previewVariant}&_=${Date.now()}`;
}

function buildPreviewCard() {
  previewTabsNode = el("div", { class: "preview-tabs" });
  const refreshBtn = el("button", { class: "btn small" }, "↻ Refresh");
  refreshBtn.onclick = refreshPreview;
  const card = el(
    "div",
    { class: "preview-card" },
    el("div", { class: "preview-head" }, el("h2", {}, "Print preview"), previewTabsNode, refreshBtn),
    el("div", { class: "preview-body", id: "preview-body" }),
    el(
      "div",
      { class: "preview-note" },
      "Uses your latest real captures when available, sample pictures otherwise. " +
        "The preview reflects saved settings — apply your changes to update it."
    )
  );
  renderPreviewTabs();
  return card;
}

/* ------------------------------------------------------------ asset picker */

async function loadAssets() {
  try {
    state.assets = (await api("/api/assets")).assets;
  } catch (error) {
    state.assets = [];
  }
}

async function loadDesigns() {
  try {
    state.designs = (await api("/api/designs")).designs;
  } catch (error) {
    state.designs = [];
  }
}

function renderAssetGrid() {
  const grid = $("asset-grid");
  grid.replaceChildren();

  if (state.pickerDesignLayer) {
    const layer = state.pickerDesignLayer;
    const designs = state.designs.filter((d) => layer !== "background" || d.has_background);
    grid.append(
      el("div", { class: "asset-section-title" }, layer === "background" ? "Design backgrounds" : "Overlay designs")
    );
    if (!designs.length) {
      grid.append(el("div", { class: "asset-empty" }, "No overlay design has a matching layer yet."));
    } else {
      for (const design of designs) {
        const path = layer === "background" ? design.background_png_path : design.png_path;
        const item = el(
          "div",
          { class: "asset-item" },
          el("img", { src: `/api/designs/${encodeURIComponent(design.name)}/image?layer=${layer}`, loading: "lazy" }),
          el("div", { class: "asset-name" }, design.name)
        );
        item.onclick = () => {
          if (state.assetCallback) state.assetCallback(path);
          closeAssetPicker();
        };
        grid.append(item);
      }
    }
    grid.append(el("div", { class: "asset-section-title" }, "Uploaded images"));
  }

  if (!state.assets.length) {
    grid.append(el("div", { class: "asset-empty" }, "No image uploaded yet."));
    return;
  }
  for (const asset of state.assets) {
    const remove = el("button", { class: "asset-del", title: "Delete" }, "✕");
    remove.onclick = async (event) => {
      event.stopPropagation();
      try {
        await api(`/api/assets/${encodeURIComponent(asset.name)}`, { method: "DELETE" });
        await loadAssets();
        renderAssetGrid();
      } catch (error) {
        toast(`Could not delete: ${error.message}`, "error");
      }
    };
    const item = el(
      "div",
      { class: "asset-item" },
      el("img", { src: `/api/assets/${encodeURIComponent(asset.name)}`, loading: "lazy" }),
      el("div", { class: "asset-name" }, asset.name),
      remove
    );
    item.onclick = () => {
      if (state.assetCallback) state.assetCallback(asset.path);
      closeAssetPicker();
    };
    grid.append(item);
  }
}

function openAssetPicker(callback, options = {}) {
  state.assetCallback = callback;
  state.pickerDesignLayer = options.designLayer || null;
  $("asset-modal").classList.remove("hidden");
  if (state.pickerDesignLayer) {
    Promise.all([loadAssets(), loadDesigns()]).then(renderAssetGrid);
  } else {
    loadAssets().then(renderAssetGrid);
  }
}

function closeAssetPicker() {
  state.assetCallback = null;
  state.pickerDesignLayer = null;
  $("asset-modal").classList.add("hidden");
}

async function uploadAsset(file) {
  const form = new FormData();
  form.append("file", file);
  try {
    const asset = await api("/api/assets", { method: "POST", body: form });
    await loadAssets();
    renderAssetGrid();
    toast(`Uploaded ${asset.name} ✔`);
    if (state.assetCallback) {
      state.assetCallback(asset.path);
      closeAssetPicker();
    }
  } catch (error) {
    toast(`Upload failed: ${error.message}`, "error");
  }
}

/* -------------------------------------------------------------- rendering */

// Pages that own resources tied to document-level listeners (e.g. a
// CanvasEditor's keyboard nudge/delete handling) register a cleanup callback
// here; it is invoked right before navigating to a *different* page/section
// so those listeners don't stay active while the page is no longer visible.
let activePageCleanup = null;

function registerPageCleanup(fn) {
  activePageCleanup = fn;
}

function leaveActivePage() {
  if (activePageCleanup) {
    activePageCleanup();
    activePageCleanup = null;
  }
}

function renderCustomPageGroup(nav, groupName, headerLabel) {
  const pages = CUSTOM_PAGES.filter((page) => page.group === groupName).sort((a, b) => (a.order || 0) - (b.order || 0));
  if (!pages.length) return;
  nav.append(el("div", { class: "nav-group-header" }, headerLabel));
  for (const page of pages) {
    const item = el(
      "button",
      { class: `nav-item${page.id === state.active ? " active" : ""}` },
      el("span", {}, page.icon),
      page.label
    );
    item.onclick = () => {
      if (state.active !== page.id) leaveActivePage();
      state.active = page.id;
      renderNav();
      page.render();
    };
    nav.append(item);
  }
}

function renderNav() {
  // Every navigation path calls renderNav() before rendering, so this is the
  // one place that needs to know whether the incoming page wants full width.
  const activePage = CUSTOM_PAGES.find((page) => page.id === state.active);
  document.querySelector(".content").classList.toggle("wide", !!(activePage && activePage.wide));

  const nav = $("nav");
  nav.replaceChildren();
  renderCustomPageGroup(nav, "design", "Design");
  renderCustomPageGroup(nav, "event", "Event");

  nav.append(el("div", { class: "nav-group-header" }, "Settings"));
  for (const section of state.schema.sections) {
    const dirty = state.dirty[section.name] ? Object.keys(state.dirty[section.name]).length : 0;
    const item = el(
      "button",
      { class: `nav-item${section.name === state.active ? " active" : ""}` },
      el("span", {}, SECTION_ICONS[section.name] || "🔌"),
      section.label,
      dirty ? el("span", { class: "badge" }, dirty) : null
    );
    item.onclick = () => {
      if (state.active !== section.name) leaveActivePage();
      state.active = section.name;
      renderNav();
      renderSection(section.name);
    };
    nav.append(item);
  }
}

function renderActive() {
  const page = CUSTOM_PAGES.find((p) => p.id === state.active);
  if (page) page.render();
  else renderSection(state.active);
}

function renderSection(name) {
  const section = state.schema.sections.find((s) => s.name === name);
  if (!section) return;
  $("section-title").textContent = section.label;
  $("section-hint").textContent = SECTION_HINTS[name] || `Options of the [${name}] configuration section.`;

  const body = $("section-body");
  body.replaceChildren();

  if (name === "PICTURE") {
    body.append(buildPreviewCard());
    refreshPreview();
  }
  if (name === "CAMERA" && state.status && state.status.camera.detected) {
    body.append(
      el(
        "div",
        { class: "preview-card" },
        el("div", {}, `Currently running camera backend: ${state.status.camera.detected}`)
      )
    );
  }
  if (name === "PRINTER") {
    const printerName = currentPrinterName();
    if (state.printerCaps === null || state.printerCapsFor !== printerName) {
      state.printerCapsFor = printerName;
      fetchPrinterCapabilities(printerName);
    }
    const capsCard = buildPrinterCapsCard();
    if (capsCard) body.append(capsCard);
  }

  const card = el("div", { class: "card" });
  for (const opt of section.options) {
    const dirtyValue = state.dirty[name] && state.dirty[name][opt.name];
    const optShown = dirtyValue !== undefined ? { ...opt, value: dirtyValue.replace(/^"|"$/g, "") } : opt;
    const info = el(
      "div",
      { class: "field-info" },
      el("div", { class: "field-label" }, opt.label),
      el("div", { class: "field-help" }, opt.help),
      opt.plugin ? el("span", { class: "field-plugin" }, `${opt.plugin} plugin`) : null
    );
    card.append(el("div", { class: "field" }, info, el("div", { class: "field-widget" }, widgetFor(name, optShown))));
  }
  body.append(card);
}

function activeEventName() {
  if (!state.schema) return "";
  const section = state.schema.sections.find((s) => s.name === "GENERAL");
  const opt = section && section.options.find((o) => o.name === "event");
  return opt ? opt.value : "";
}

function renderStatus() {
  const status = $("status");
  status.replaceChildren();
  if (!state.status) return;
  const camera = state.status.camera;
  const printer = state.status.printer;
  status.append(
    el(
      "div",
      {},
      el("span", { class: `dot ${camera.detected ? "ok" : "ko"}` }),
      camera.detected ? `Camera: ${camera.detected}` : "Camera: not detected"
    ),
    el(
      "div",
      {},
      el("span", { class: `dot ${printer.connected ? "ok" : "ko"}` }),
      printer.connected ? `Printer: ${printer.name}` : "Printer: not connected"
    )
  );
  const eventName = activeEventName();
  if (eventName) {
    status.append(el("div", {}, `🎉 ${eventName}`));
  }
}

/* ------------------------------------------------------------------- init */

async function loadConfig() {
  state.schema = await api("/api/config");
  $("version").textContent = `v${state.schema.version}`;
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
  } catch (error) {
    state.status = null;
  }
  renderStatus();
}

async function loadFonts() {
  try {
    const payload = await api("/api/fonts");
    const list = $("fontlist");
    list.replaceChildren();
    for (const font of payload.fonts) list.append(el("option", { value: font }));
  } catch (error) {
    /* fonts list is optional */
  }
}

function bindGlobalEvents() {
  $("apply-btn").onclick = applyChanges;
  $("discard-btn").onclick = discardChanges;
  $("asset-close").onclick = closeAssetPicker;
  $("asset-modal").onclick = (event) => {
    if (event.target === $("asset-modal")) closeAssetPicker();
  };
  $("asset-file").onchange = (event) => {
    if (event.target.files.length) uploadAsset(event.target.files[0]);
    event.target.value = "";
  };
  const dropzone = $("dropzone");
  dropzone.ondragover = (event) => {
    event.preventDefault();
    dropzone.classList.add("dragover");
  };
  dropzone.ondragleave = () => dropzone.classList.remove("dragover");
  dropzone.ondrop = (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
    if (event.dataTransfer.files.length) uploadAsset(event.dataTransfer.files[0]);
  };
}

async function init() {
  bindGlobalEvents();
  try {
    await loadConfig();
  } catch (error) {
    document.body.replaceChildren(el("p", { style: "padding:40px" }, `Cannot load configuration: ${error.message}`));
    return;
  }
  await Promise.all([loadStatus(), loadFonts(), loadAssets(), loadDesigns()]);
  state.active = CUSTOM_PAGES.length ? CUSTOM_PAGES[0].id : state.schema.sections[0].name;
  renderNav();
  renderActive();
}

init();
