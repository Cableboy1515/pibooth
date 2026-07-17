/* pibooth web configuration interface — picture layout designer page */
"use strict";

/* Register after the overlay designer (see designer.js CUSTOM_PAGES.push);
 * script order in index.html guarantees this entry lands last.
 */
CUSTOM_PAGES.push({ id: "LAYOUT", label: "Layout designer", icon: "📐", render: renderLayoutPage });

//: Mirrors pibooth.printer.PAPER_FORMATS (inches, width x height in portrait).
const LAYOUT_PAPER_FORMATS = {
  "2x6": [2, 6],
  "3,5x5": [3.5, 5],
  "4x6": [4, 6],
  "5x7": [5, 7],
  "6x8": [6, 8],
  "6x9": [6, 9],
  custom: null,
};

const LAYOUT_CANVAS_HEIGHT = 520;
const LAYOUT_CAPTURE_COLORS = ["#8fb8ae", "#c9a66b", "#a98fb8", "#6ba3c9"];

const layoutState = {
  templateName: "",
  paper: "4x6",
  dpi: 300,
  pages: [], // working pages: {captures, size:[w,h], dpi, paper, shapes:[...]}
  activeCaptures: 4,
  activeOrientation: "portrait",
  selectedShapeIndex: -1,
  imageCache: new Map(), // asset basename -> HTMLImageElement
  editor: null, // active CanvasEditor instance
};

/* ------------------------------------------------------------------ pages */

function pageOrientationOf(page) {
  return page.size[0] < page.size[1] ? "portrait" : "landscape";
}

function findLayoutPage(captures, orientation) {
  return layoutState.pages.find((p) => p.captures === captures && pageOrientationOf(p) === orientation);
}

function activeLayoutPage() {
  return findLayoutPage(layoutState.activeCaptures, layoutState.activeOrientation);
}

function pageSizeFor(paper, dpi, orientation) {
  const inches = LAYOUT_PAPER_FORMATS[paper];
  let widthIn, heightIn;
  if (inches) {
    [widthIn, heightIn] = inches; // stored smaller-first, i.e. portrait
  } else {
    [widthIn, heightIn] = [4, 6]; // "custom" fallback, matches the server default page
  }
  let width = Math.round(widthIn * dpi);
  let height = Math.round(heightIn * dpi);
  if (orientation === "landscape") [width, height] = [height, width];
  return [width, height];
}

/** Simple default layout: captures tiled in a grid above two footer text
 * slots. Deliberately basic — the user is expected to fine-tune from here.
 */
function generateDefaultShapes(captures, size) {
  const layouts = {
    1: [[0, 0, 1, 1]],
    2: [
      [0, 0, 1, 0.5],
      [0, 0.5, 1, 0.5],
    ],
    3: [
      [0, 0, 1, 1 / 3],
      [0, 1 / 3, 1, 1 / 3],
      [0, 2 / 3, 1, 1 / 3],
    ],
    4: [
      [0, 0, 0.5, 0.5],
      [0.5, 0, 0.5, 0.5],
      [0, 0.5, 0.5, 0.5],
      [0.5, 0.5, 0.5, 0.5],
    ],
  };
  const cells = layouts[captures] || layouts[4];
  const margin = 0.03;
  const textHeight = 0.08;
  const gridHeight = 1 - 2 * textHeight - margin;
  const shapes = cells.map((cell, i) => ({
    type: "capture",
    index: i + 1,
    x: cell[0] + margin / 2,
    y: margin / 2 + cell[1] * gridHeight,
    width: cell[2] - margin,
    height: cell[3] * gridHeight - margin,
    rotation: 0,
  }));
  shapes.push({ type: "text", index: 1, x: margin, y: 1 - 2 * textHeight, width: 1 - 2 * margin, height: textHeight - margin / 2, rotation: 0 });
  shapes.push({ type: "text", index: 2, x: margin, y: 1 - textHeight, width: 1 - 2 * margin, height: textHeight - margin / 2, rotation: 0 });
  return shapes;
}

function nextFreeCaptureIndex(page) {
  const used = new Set(page.shapes.filter((s) => s.type === "capture").map((s) => s.index));
  for (let i = 1; i <= page.captures; i++) if (!used.has(i)) return i;
  return null;
}

function hasTextSlot(page, index) {
  return page.shapes.some((s) => s.type === "text" && s.index === index);
}

/* --------------------------------------------------------------- drawing */

function layoutCanvasSize() {
  const page = activeLayoutPage();
  const ratio = page ? page.size[0] / page.size[1] : 2 / 3;
  const height = LAYOUT_CANVAS_HEIGHT;
  const width = Math.round(height * ratio);
  return { width, height };
}

function getLayoutCachedImage(name) {
  if (!name) return null;
  let image = layoutState.imageCache.get(name);
  if (!image) {
    image = new Image();
    image.src = `/api/assets/${encodeURIComponent(name)}`;
    image.onload = () => redrawLayout();
    image.onerror = () => {
      /* drawing just skips missing images */
    };
    layoutState.imageCache.set(name, image);
  }
  return image;
}

function drawLayoutShape(ctx, shape, size) {
  const x = shape.x * size.width;
  const y = shape.y * size.height;
  const w = Math.max(1, shape.width * size.width);
  const h = Math.max(1, shape.height * size.height);
  ctx.save();
  ctx.translate(x + w / 2, y + h / 2);
  ctx.rotate(((shape.rotation || 0) * Math.PI) / 180);

  if (shape.type === "capture") {
    ctx.fillStyle = LAYOUT_CAPTURE_COLORS[(shape.index - 1) % LAYOUT_CAPTURE_COLORS.length];
    ctx.beginPath();
    ctx.roundRect(-w / 2, -h / 2, w, h, Math.min(w, h) * 0.06);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.font = `700 ${Math.max(10, Math.min(w, h) * 0.3)}px sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(shape.index), 0, 0);
  } else if (shape.type === "text") {
    ctx.strokeStyle = "#8a9490";
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 1.5;
    ctx.strokeRect(-w / 2, -h / 2, w, h);
    ctx.setLineDash([]);
    ctx.fillStyle = "#8a9490";
    ctx.font = `${Math.max(10, Math.min(w, h) * 0.5)}px sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`Text ${shape.index}`, 0, 0);
  } else if (shape.type === "image") {
    const image = getLayoutCachedImage(shape.asset);
    if (image && image.complete && image.naturalWidth) {
      ctx.drawImage(image, -w / 2, -h / 2, w, h);
    } else {
      ctx.strokeStyle = "#bbbbbb";
      ctx.setLineDash([3, 3]);
      ctx.strokeRect(-w / 2, -h / 2, w, h);
      ctx.setLineDash([]);
    }
  }
  ctx.restore();
}

function drawLayoutScene(ctx) {
  const size = layoutCanvasSize();
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, size.width, size.height);
  const page = activeLayoutPage();
  if (!page) return;
  for (const shape of page.shapes) drawLayoutShape(ctx, shape, size);
}

function redrawLayout() {
  if (layoutState.editor) layoutState.editor.requestDraw();
}

function resizeLayoutCanvas(canvas) {
  const size = layoutCanvasSize();
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.style.aspectRatio = `${size.width} / ${size.height}`;
}

/* ------------------------------------------------------ CanvasEditor adapter */

function shapeItemRect(shape) {
  return { x: shape.x, y: shape.y, w: shape.width, h: shape.height, rotation: shape.rotation || 0, centerBased: false };
}

function setShapeItemRect(shape, rect) {
  shape.x = rect.x;
  shape.y = rect.y;
  shape.width = rect.w;
  shape.height = rect.h;
  shape.rotation = rect.rotation;
}

function onLayoutSelect(shape) {
  const page = activeLayoutPage();
  layoutState.selectedShapeIndex = page && shape ? page.shapes.indexOf(shape) : -1;
  renderLayoutProperties();
}

function deleteLayoutShape(index) {
  const page = activeLayoutPage();
  if (!page || index < 0 || !page.shapes[index]) return;
  const removed = page.shapes[index];
  page.shapes.splice(index, 1);
  if (layoutState.selectedShapeIndex === index) layoutState.selectedShapeIndex = -1;
  else if (layoutState.selectedShapeIndex > index) layoutState.selectedShapeIndex -= 1;
  if (layoutState.editor && layoutState.editor.getSelected() === removed) {
    layoutState.editor.select(null);
  } else {
    renderLayoutProperties();
    redrawLayout();
  }
  renderLayoutToolbar();
}

/* ------------------------------------------------------------- properties */

function moveShapeZOrder(page, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= page.shapes.length) return index;
  [page.shapes[index], page.shapes[target]] = [page.shapes[target], page.shapes[index]];
  return target;
}

function renderLayoutProperties() {
  const panel = $("layout-properties");
  if (!panel) return;
  panel.replaceChildren();
  const page = activeLayoutPage();
  const index = layoutState.selectedShapeIndex;
  if (!page || index < 0 || !page.shapes[index]) {
    panel.append(el("div", { class: "field-help" }, "Select a shape to edit its properties."));
    return;
  }
  const shape = page.shapes[index];

  function update(patch) {
    Object.assign(shape, patch);
    redrawLayout();
  }

  panel.append(
    el(
      "div",
      { class: "designer-numeric-grid" },
      numberField("X %", shape.x * 100, 0.1, (v) => update({ x: v / 100 })),
      numberField("Y %", shape.y * 100, 0.1, (v) => update({ y: v / 100 })),
      numberField("W %", shape.width * 100, 0.1, (v) => update({ width: v / 100 })),
      numberField("H %", shape.height * 100, 0.1, (v) => update({ height: v / 100 }))
    ),
    numberField("Rotation °", shape.rotation || 0, 1, (v) => update({ rotation: v }))
  );

  if (shape.type === "capture") {
    const select = el("select");
    for (let i = 1; i <= page.captures; i++) select.append(el("option", { value: i }, `Slot ${i}`));
    select.value = String(shape.index);
    select.onchange = () => {
      const newIndex = parseInt(select.value, 10);
      const collision = page.shapes.find((s) => s.type === "capture" && s.index === newIndex && s !== shape);
      if (collision) collision.index = shape.index;
      shape.index = newIndex;
      redrawLayout();
    };
    panel.append(field("Capture index", select));
  }

  const forwardBtn = el("button", { class: "btn small ghost", title: "Bring forward" }, "↑ Forward");
  forwardBtn.onclick = () => {
    layoutState.selectedShapeIndex = moveShapeZOrder(page, index, 1);
    redrawLayout();
  };
  const backwardBtn = el("button", { class: "btn small ghost", title: "Send backward" }, "↓ Backward");
  backwardBtn.onclick = () => {
    layoutState.selectedShapeIndex = moveShapeZOrder(page, index, -1);
    redrawLayout();
  };
  const deleteBtn = el("button", { class: "btn small ghost", title: "Delete" }, "✕ Delete");
  deleteBtn.onclick = () => deleteLayoutShape(index);

  panel.append(el("div", { class: "row" }, forwardBtn, backwardBtn, deleteBtn));
}

/* ------------------------------------------------------------------ chips */

function onChipClick(captures, orientation) {
  let page = findLayoutPage(captures, orientation);
  if (!page) {
    const size = pageSizeFor(layoutState.paper, layoutState.dpi, orientation);
    page = { captures, size, dpi: layoutState.dpi, paper: layoutState.paper, shapes: generateDefaultShapes(captures, size) };
    layoutState.pages.push(page);
  }
  layoutState.activeCaptures = captures;
  layoutState.activeOrientation = orientation;
  layoutState.selectedShapeIndex = -1;
  renderLayoutPage();
}

function deleteLayoutPageChip(captures, orientation) {
  if (!confirm(`Delete the ${captures} capture${captures > 1 ? "s" : ""} ${orientation} page?`)) return;
  const page = findLayoutPage(captures, orientation);
  if (!page) return;
  layoutState.pages.splice(layoutState.pages.indexOf(page), 1);
  if (layoutState.activeCaptures === captures && layoutState.activeOrientation === orientation) {
    const fallback = layoutState.pages[0];
    layoutState.activeCaptures = fallback ? fallback.captures : 4;
    layoutState.activeOrientation = fallback ? pageOrientationOf(fallback) : "portrait";
  }
  layoutState.selectedShapeIndex = -1;
  renderLayoutPage();
}

function buildPageChips() {
  const grid = el("div", { class: "layout-chip-grid" });
  for (let captures = 1; captures <= 4; captures++) {
    for (const orientation of ["portrait", "landscape"]) {
      const page = findLayoutPage(captures, orientation);
      const active = layoutState.activeCaptures === captures && layoutState.activeOrientation === orientation;
      const chip = el(
        "button",
        { class: `layout-chip${page ? " present" : ""}${active ? " active" : ""}` },
        `${captures} ${orientation}`
      );
      if (active && page) {
        const close = el("span", { class: "layout-chip-close", title: "Delete this page" }, "✕");
        close.onclick = (event) => {
          event.stopPropagation();
          deleteLayoutPageChip(captures, orientation);
        };
        chip.append(close);
      }
      chip.onclick = () => onChipClick(captures, orientation);
      grid.append(chip);
    }
  }
  return grid;
}

/* ---------------------------------------------------------------- toolbar */

function renderLayoutToolbar() {
  const bar = $("layout-add-toolbar");
  if (!bar) return;
  bar.replaceChildren();
  const page = activeLayoutPage();

  const captureBtn = el("button", { class: "btn small" }, "+ Capture slot");
  const freeIndex = page ? nextFreeCaptureIndex(page) : null;
  captureBtn.disabled = !page || freeIndex === null;
  captureBtn.onclick = () => {
    page.shapes.push({ type: "capture", index: freeIndex, x: 0.3, y: 0.3, width: 0.3, height: 0.3, rotation: 0 });
    renderLayoutToolbar();
    redrawLayout();
  };

  const text1Btn = el("button", { class: "btn small" }, "+ Text 1");
  text1Btn.disabled = !page || hasTextSlot(page, 1);
  text1Btn.onclick = () => {
    page.shapes.push({ type: "text", index: 1, x: 0.1, y: 0.85, width: 0.8, height: 0.08, rotation: 0 });
    renderLayoutToolbar();
    redrawLayout();
  };

  const text2Btn = el("button", { class: "btn small" }, "+ Text 2");
  text2Btn.disabled = !page || hasTextSlot(page, 2);
  text2Btn.onclick = () => {
    page.shapes.push({ type: "text", index: 2, x: 0.1, y: 0.94, width: 0.8, height: 0.05, rotation: 0 });
    renderLayoutToolbar();
    redrawLayout();
  };

  const imageBtn = el("button", { class: "btn small" }, "+ Image");
  imageBtn.disabled = !page;
  imageBtn.onclick = () => {
    openAssetPicker((path) => {
      page.shapes.push({ type: "image", asset: basename(path), index: 0, x: 0.1, y: 0.1, width: 0.3, height: 0.2, rotation: 0 });
      renderLayoutToolbar();
      redrawLayout();
    });
  };

  bar.append(captureBtn, text1Btn, text2Btn, imageBtn);
}

/* ----------------------------------------------------------- save / load */

function validateLayoutTemplate() {
  if (!layoutState.pages.length) return "Add at least one page before saving";
  for (const page of layoutState.pages) {
    const seen = new Set();
    for (const shape of page.shapes) {
      if (shape.type !== "capture") continue;
      if (seen.has(shape.index)) {
        return `Duplicate capture index ${shape.index} on the ${page.captures}-capture ${pageOrientationOf(page)} page`;
      }
      seen.add(shape.index);
    }
    for (let i = 1; i <= page.captures; i++) {
      if (!seen.has(i)) {
        return `Missing capture slot ${i} on the ${page.captures}-capture ${pageOrientationOf(page)} page`;
      }
    }
  }
  return null;
}

function loadTemplateData(data) {
  layoutState.templateName = data.name || "";
  layoutState.pages = (data.pages || []).map((page) => ({
    captures: page.captures,
    size: [page.size[0], page.size[1]],
    dpi: page.dpi || 300,
    paper: page.paper || "custom",
    shapes: (page.shapes || []).map((shape) => ({ ...shape })),
  }));
  const first = layoutState.pages[0];
  layoutState.paper = first ? first.paper : "4x6";
  layoutState.dpi = first ? first.dpi : 300;
  layoutState.activeCaptures = first ? first.captures : 4;
  layoutState.activeOrientation = first ? pageOrientationOf(first) : "portrait";
  layoutState.selectedShapeIndex = -1;
  renderLayoutPage();
}

async function openLoadTemplateDialog() {
  let payload;
  try {
    payload = await api("/api/templates");
  } catch (error) {
    toast(`Could not list templates: ${error.message}`, "error");
    return;
  }
  if (!payload.templates.length) {
    toast("No template saved yet");
    return;
  }
  const names = payload.templates.map((t) => t.name);
  const choice = prompt(`Load which template?\n${names.join(", ")}`, names[0]);
  if (!choice || !names.includes(choice)) return;
  try {
    const data = await api(`/api/templates/${encodeURIComponent(choice)}`);
    loadTemplateData(data);
    toast(`Loaded template "${choice}"`);
  } catch (error) {
    toast(`Could not load template: ${error.message}`, "error");
  }
}

function buildLayoutSaveRow() {
  const assignCheckbox = el("input", { type: "checkbox" });
  assignCheckbox.checked = true;

  const saveBtn = el("button", { class: "btn primary" }, "Save layout");
  saveBtn.onclick = async () => {
    const error = validateLayoutTemplate();
    if (error) {
      toast(error, "error");
      return;
    }
    const template = {
      name: layoutState.templateName,
      pages: layoutState.pages.map((page) => ({
        captures: page.captures,
        size: page.size,
        dpi: page.dpi,
        paper: page.paper,
        shapes: page.shapes,
      })),
    };
    try {
      await api("/api/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template, assign: assignCheckbox.checked }),
      });
      toast(
        assignCheckbox.checked
          ? "Layout saved and set as the booth template — the Picture preview follows it ✔"
          : "Layout saved ✔"
      );
    } catch (err) {
      toast(`Could not save layout: ${err.message}`, "error");
    }
  };

  const loadBtn = el("button", { class: "btn" }, "Load…");
  loadBtn.onclick = openLoadTemplateDialog;

  const importLabel = el("label", { class: "btn" }, "Import diagrams.net XML…");
  const importInput = el("input", { type: "file", accept: ".xml", hidden: true });
  importLabel.append(importInput);
  importInput.onchange = async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const data = await api("/api/templates/import", { method: "POST", body: form });
      loadTemplateData(data);
      toast(`Imported template "${data.name}" ✔`);
    } catch (error) {
      toast(`Could not import template: ${error.message}`, "error");
    }
  };

  const newBtn = el("button", { class: "btn ghost" }, "New");
  newBtn.onclick = () => {
    layoutState.templateName = "";
    layoutState.pages = [];
    layoutState.activeCaptures = 4;
    layoutState.activeOrientation = "portrait";
    layoutState.selectedShapeIndex = -1;
    renderLayoutPage();
  };

  return el(
    "div",
    { class: "designer-save-row" },
    el("label", { class: "row" }, assignCheckbox, "Use for the booth"),
    el("div", { class: "row" }, saveBtn, loadBtn, importLabel, newBtn)
  );
}

/* --------------------------------------------------------------- top bar */

function buildTopBar() {
  const nameInput = el("input", { type: "text", placeholder: "e.g. gold-wedding" });
  nameInput.value = layoutState.templateName;
  nameInput.oninput = () => (layoutState.templateName = nameInput.value);

  const paperSelect = el("select");
  for (const key of Object.keys(LAYOUT_PAPER_FORMATS)) paperSelect.append(el("option", { value: key }, key));
  paperSelect.value = layoutState.paper;
  paperSelect.onchange = () => (layoutState.paper = paperSelect.value);

  const dpiSelect = el("select");
  for (const dpi of [300, 600]) dpiSelect.append(el("option", { value: dpi }, `${dpi} dpi`));
  dpiSelect.value = String(layoutState.dpi);
  dpiSelect.onchange = () => (layoutState.dpi = parseInt(dpiSelect.value, 10));

  return el(
    "div",
    { class: "designer-toolbar" },
    field("Template name", nameInput),
    field("Paper preset", paperSelect),
    field("DPI", dpiSelect),
    buildPageChips()
  );
}

function buildLayoutZoomControls() {
  const zoomOut = el("button", { class: "btn small ghost", title: "Zoom out" }, "−");
  zoomOut.onclick = () => layoutState.editor && layoutState.editor.setZoom(layoutState.editor.viewport.scale / 1.25);
  const zoomIn = el("button", { class: "btn small ghost", title: "Zoom in" }, "+");
  zoomIn.onclick = () => layoutState.editor && layoutState.editor.setZoom(layoutState.editor.viewport.scale * 1.25);
  const reset = el("button", { class: "btn small ghost", title: "Reset view" }, "⤢");
  reset.onclick = () => layoutState.editor && layoutState.editor.resetView();
  return el("div", { class: "canvas-zoom-controls" }, zoomOut, zoomIn, reset);
}

/* ------------------------------------------------------------------- init */

function renderLayoutPage() {
  $("section-title").textContent = "Layout designer";
  $("section-hint").textContent = "Design the physical print layout: capture slots, footer texts and decorative images per page.";

  const page = activeLayoutPage();
  const caption = page
    ? `Page: ${page.size[0]}x${page.size[1]}px, ${page.captures} capture${page.captures > 1 ? "s" : ""}, ${pageOrientationOf(page)}`
    : "No page yet — click a chip above to create one.";

  const canvasWrap = el(
    "div",
    { class: "designer-canvas-wrap" },
    el("div", { class: "designer-canvas-stack" }, el("canvas", { id: "layout-canvas" }), buildLayoutZoomControls()),
    el("div", { class: "designer-caption" }, caption),
    el("div", { id: "layout-add-toolbar", class: "row" })
  );

  const controls = el(
    "div",
    { class: "designer-controls" },
    buildTopBar(),
    el("h3", {}, "Properties"),
    el("div", { id: "layout-properties" }),
    buildLayoutSaveRow()
  );

  const body = $("section-body");
  body.replaceChildren(el("div", { class: "designer-layout" }, canvasWrap, controls));

  const canvas = $("layout-canvas");
  resizeLayoutCanvas(canvas);

  if (layoutState.editor) layoutState.editor.destroy();
  layoutState.editor = CanvasEditor.create({
    canvas,
    getItems: () => (activeLayoutPage() ? activeLayoutPage().shapes : []),
    itemRect: shapeItemRect,
    setItemRect: setShapeItemRect,
    draw: drawLayoutScene,
    onSelect: onLayoutSelect,
    onChange: () => renderLayoutProperties(),
    onDelete: (item) => deleteLayoutShape(activeLayoutPage().shapes.indexOf(item)),
    aspectLocked: () => false,
  });
  registerPageCleanup(() => layoutState.editor && layoutState.editor.destroy());

  renderLayoutToolbar();
  renderLayoutProperties();
}
