/* pibooth web configuration interface — overlay designer page */
"use strict";

/* Register this page alongside the events page (see events.js). Both files
 * push into the same global CUSTOM_PAGES array read by app.js renderNav().
 */
CUSTOM_PAGES.push({ id: "DESIGNER", label: "Overlay designer", icon: "🎨", render: renderDesignerPage });

const designerState = {
  loaded: false,
  name: "",
  orientation: "portrait",
  elements: [], // {type, ...props}
  selected: -1, // index in elements
  showSample: false,
  fontsLoaded: new Set(), // font names already registered with document.fonts
  imageCache: new Map(), // asset basename -> HTMLImageElement
  sampleImage: null, // HTMLImageElement of the backdrop preview
  designs: [], // /api/designs payload cache
  geometry: { width: 800, height: 1200, orientation: "portrait", source: "default" },
  editor: null, // active CanvasEditor instance
};

const DESIGNER_CANVAS_HEIGHT = 520;

/* ------------------------------------------------------------- geometry */

function designerCanvasSize() {
  const ratio = designerState.geometry.width / designerState.geometry.height;
  const height = DESIGNER_CANVAS_HEIGHT;
  const width = Math.round(height * ratio);
  return { width, height };
}

async function fetchDesignerGeometry() {
  try {
    designerState.geometry = await api("/api/geometry?variant=0");
  } catch (error) {
    designerState.geometry = { width: 800, height: 1200, orientation: "portrait", source: "default" };
  }
  const canvas = $("designer-canvas");
  if (canvas) {
    resizeCanvas(canvas);
    redraw();
  }
  const caption = $("designer-geometry-caption");
  if (caption) {
    const g = designerState.geometry;
    caption.textContent = `Layout: ${g.width}x${g.height}px (${g.source})`;
  }
}

function elementLabel(element) {
  if (element.type === "text") return `Text: ${element.text || "(empty)"}`;
  if (element.type === "image") return `Image: ${element.asset || "(none)"}`;
  if (element.type === "frame") return "Frame";
  return element.type;
}

function newElement(type) {
  if (type === "text") {
    return { type: "text", text: "Sample text", font: "Amatic-Bold", color: "#000000", x: 0.5, y: 0.5, size: 0.06, rotation: 0, align: "center" };
  }
  if (type === "image") {
    return { type: "image", asset: "", x: 0.5, y: 0.5, width: 0.2, rotation: 0, opacity: 1 };
  }
  return { type: "frame", color: "#d4af37", width: 0.01, radius: 0.03, inset: 0.02 };
}

/* -------------------------------------------------------- canvas drawing */

function getCachedAssetImage(name) {
  if (!name) return null;
  let image = designerState.imageCache.get(name);
  if (!image) {
    image = new Image();
    image.src = `/api/assets/${encodeURIComponent(name)}`;
    image.onload = () => redraw();
    image.onerror = () => {
      /* leave a broken placeholder, drawing just skips it */
    };
    designerState.imageCache.set(name, image);
  }
  return image;
}

function ensureFontLoaded(name) {
  if (!name || designerState.fontsLoaded.has(name)) return;
  designerState.fontsLoaded.add(name);
  try {
    const face = new FontFace(name, `url('/api/fonts/${encodeURIComponent(name)}/file')`);
    face.load().then(
      (loaded) => {
        document.fonts.add(loaded);
        redraw();
      },
      () => {
        /* fall back silently to the default canvas font */
      }
    );
  } catch (e) {
    /* FontFace not supported: fall back silently */
  }
}

/** Compute an element's on-canvas bounding box, in PIXELS of `canvasSize`
 * (center + half-extents + rotation). Shared by drawing, the CanvasEditor
 * adapter and hit-testing used to happen here before CanvasEditor took over.
 */
function elementBounds(ctx, element, canvasSize) {
  const cx = element.x * canvasSize.width;
  const cy = element.y * canvasSize.height;
  if (element.type === "text") {
    ensureFontLoaded(element.font);
    const size = Math.max(1, element.size * canvasSize.height);
    ctx.font = `${size}px "${element.font}", sans-serif`;
    const lines = String(element.text || "").split("\n");
    const width = Math.max(1, ...lines.map((line) => ctx.measureText(line).width));
    const height = size * 1.2 * lines.length;
    return { cx, cy, width, height, rotation: element.rotation || 0 };
  }
  if (element.type === "image") {
    const image = getCachedAssetImage(element.asset);
    const width = Math.max(1, element.width * canvasSize.width);
    const ratio = image && image.naturalWidth ? image.naturalHeight / image.naturalWidth : 1;
    const height = Math.max(1, width * ratio);
    return { cx, cy, width, height, rotation: element.rotation || 0 };
  }
  // frame: bounding box is the whole canvas inset area
  const minDim = Math.min(canvasSize.width, canvasSize.height);
  const inset = element.inset * minDim;
  return {
    cx: canvasSize.width / 2,
    cy: canvasSize.height / 2,
    width: canvasSize.width - 2 * inset,
    height: canvasSize.height - 2 * inset,
    rotation: 0,
  };
}

function drawElement(ctx, element, canvasSize) {
  ctx.save();
  if (element.type === "text") {
    const size = Math.max(1, element.size * canvasSize.height);
    ctx.translate(element.x * canvasSize.width, element.y * canvasSize.height);
    ctx.rotate(((element.rotation || 0) * Math.PI) / 180);
    ctx.font = `${size}px "${element.font}", sans-serif`;
    ctx.fillStyle = element.color || "#000000";
    ctx.textAlign = element.align === "left" ? "left" : element.align === "right" ? "right" : "center";
    ctx.textBaseline = "middle";
    const lines = String(element.text || "").split("\n");
    const lineHeight = size * 1.2;
    const startY = -((lines.length - 1) * lineHeight) / 2;
    lines.forEach((line, i) => ctx.fillText(line, 0, startY + i * lineHeight));
  } else if (element.type === "image") {
    const image = getCachedAssetImage(element.asset);
    const width = Math.max(1, element.width * canvasSize.width);
    const ratio = image && image.naturalWidth ? image.naturalHeight / image.naturalWidth : 1;
    const height = Math.max(1, width * ratio);
    ctx.translate(element.x * canvasSize.width, element.y * canvasSize.height);
    ctx.rotate(((element.rotation || 0) * Math.PI) / 180);
    ctx.globalAlpha = element.opacity == null ? 1 : element.opacity;
    if (image && image.complete && image.naturalWidth) {
      ctx.drawImage(image, -width / 2, -height / 2, width, height);
    }
  } else if (element.type === "frame") {
    const minDim = Math.min(canvasSize.width, canvasSize.height);
    const inset = element.inset * minDim;
    const lineWidth = Math.max(1, element.width * minDim);
    const radius = Math.max(0, element.radius * minDim);
    ctx.strokeStyle = element.color || "#000000";
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    ctx.roundRect(inset, inset, canvasSize.width - 2 * inset, canvasSize.height - 2 * inset, radius);
    ctx.stroke();
  }
  ctx.restore();
}

/** CanvasEditor draw() callback: paint the sample backdrop then every element,
 * in canvas-logical pixel space (the editor has already applied the zoom/pan
 * transform and cleared the canvas by the time this runs).
 */
function drawScene(ctx) {
  const size = designerCanvasSize();
  if (designerState.showSample && designerState.sampleImage && designerState.sampleImage.complete) {
    ctx.save();
    ctx.globalAlpha = 0.6;
    ctx.drawImage(designerState.sampleImage, 0, 0, size.width, size.height);
    ctx.restore();
  }
  designerState.elements.forEach((element) => drawElement(ctx, element, size));
}

function redraw() {
  if (designerState.editor) designerState.editor.requestDraw();
}

function loadSampleBackdrop() {
  const image = new Image();
  image.onload = () => {
    designerState.sampleImage = image;
    redraw();
  };
  image.onerror = () => {
    designerState.sampleImage = null;
  };
  image.src = `/api/preview?variant=0&overlay=0&_=${Date.now()}`;
}

/* ------------------------------------------------------ CanvasEditor adapter */

/** Text/image elements are center-based; frames have no canvas position and
 * are not selectable on the canvas (list-selected only).
 */
function elementItemRect(element) {
  if (element.type === "frame") return null;
  const size = designerCanvasSize();
  const ctx = $("designer-canvas").getContext("2d");
  const bounds = elementBounds(ctx, element, size);
  return {
    x: bounds.cx / size.width,
    y: bounds.cy / size.height,
    w: bounds.width / size.width,
    h: bounds.height / size.height,
    rotation: bounds.rotation,
    centerBased: true,
  };
}

/** Text has no independently stored width (it is measured from the font
 * size), so a resize drag scales `size` by however much the drag changed
 * the measured height. Images store `width` directly.
 */
function setElementItemRect(element, rect) {
  const size = designerCanvasSize();
  if (element.type === "text") {
    const ctx = $("designer-canvas").getContext("2d");
    const before = elementBounds(ctx, element, size);
    const oldHeightFraction = before.height / size.height;
    const scale = oldHeightFraction > 0 ? rect.h / oldHeightFraction : 1;
    element.size = Math.max(0.005, element.size * scale);
  } else if (element.type === "image") {
    element.width = Math.max(0.01, rect.w);
  }
  element.x = rect.x;
  element.y = rect.y;
  element.rotation = rect.rotation;
  renderElementList();
}

/* ------------------------------------------------------------- controls */

function selectElement(index) {
  const item = index >= 0 ? designerState.elements[index] : null;
  if (designerState.editor) {
    designerState.editor.select(item);
  } else {
    onCanvasSelect(item);
  }
}

/** Called by the CanvasEditor whenever selection changes (canvas click or
 * a programmatic editor.select() from the element list).
 */
function onCanvasSelect(item) {
  designerState.selected = item ? designerState.elements.indexOf(item) : -1;
  renderElementList();
  renderPropertiesPanel();
}

function moveElement(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= designerState.elements.length) return;
  const [item] = designerState.elements.splice(index, 1);
  designerState.elements.splice(target, 0, item);
  designerState.selected = target;
  renderElementList();
  renderPropertiesPanel();
  redraw();
}

function duplicateElement(index) {
  const clone = { ...designerState.elements[index] };
  designerState.elements.splice(index + 1, 0, clone);
  selectElement(index + 1);
}

function deleteElement(index) {
  const removed = designerState.elements[index];
  designerState.elements.splice(index, 1);
  if (designerState.selected === index) designerState.selected = -1;
  else if (designerState.selected > index) designerState.selected -= 1;
  if (designerState.editor && designerState.editor.getSelected() === removed) {
    designerState.editor.select(null);
  } else {
    renderElementList();
    renderPropertiesPanel();
    redraw();
  }
}

function renderElementList() {
  const list = $("designer-elements");
  if (!list) return;
  list.replaceChildren();
  if (!designerState.elements.length) {
    list.append(el("div", { class: "asset-empty" }, "No element yet — add one below."));
    return;
  }
  designerState.elements.forEach((element, index) => {
    const up = el("button", { class: "btn small ghost", title: "Move up" }, "↑");
    up.onclick = (event) => {
      event.stopPropagation();
      moveElement(index, -1);
    };
    const down = el("button", { class: "btn small ghost", title: "Move down" }, "↓");
    down.onclick = (event) => {
      event.stopPropagation();
      moveElement(index, 1);
    };
    const dup = el("button", { class: "btn small ghost", title: "Duplicate" }, "⧉");
    dup.onclick = (event) => {
      event.stopPropagation();
      duplicateElement(index);
    };
    const del = el("button", { class: "btn small ghost", title: "Delete" }, "✕");
    del.onclick = (event) => {
      event.stopPropagation();
      deleteElement(index);
    };
    const row = el(
      "div",
      { class: `designer-element-row${index === designerState.selected ? " active" : ""}` },
      el("span", { class: "designer-element-label" }, elementLabel(element)),
      el("div", { class: "row" }, up, down, dup, del)
    );
    row.onclick = () => selectElement(index);
    list.append(row);
  });
}

function field(labelText, widget) {
  return el("div", { class: "designer-field" }, el("label", {}, labelText), widget);
}

function rangeField(labelText, value, min, max, step, onInput) {
  const input = el("input", { type: "range", min, max, step, value });
  const readout = el("span", { class: "designer-range-value" }, String(value));
  input.oninput = () => {
    readout.textContent = input.value;
    onInput(parseFloat(input.value));
  };
  return field(labelText, el("div", { class: "row" }, input, readout));
}

/** A compact numeric input (used for the X/Y/size/rotation percent fields);
 * edits mutate the element live and redraw the canvas.
 */
function numberField(labelText, value, step, onInput) {
  const input = el("input", { type: "number", step: String(step) });
  input.value = String(Math.round(value * 100) / 100);
  input.oninput = () => {
    const parsed = parseFloat(input.value);
    if (!Number.isNaN(parsed)) onInput(parsed);
  };
  return field(labelText, input);
}

/** X %, Y %, rotation ° numeric fields plus Center H/V buttons, shared by
 * the text and image property panels. `update` is the panel's local patch
 * helper (mutates the element, refreshes the list and redraws).
 */
function positionFields(element, update) {
  const centerH = el("button", { class: "btn small" }, "Center H");
  centerH.onclick = () => update({ x: 0.5 });
  const centerV = el("button", { class: "btn small" }, "Center V");
  centerV.onclick = () => update({ y: 0.5 });
  return [
    el(
      "div",
      { class: "designer-numeric-grid" },
      numberField("X %", element.x * 100, 0.1, (v) => update({ x: v / 100 })),
      numberField("Y %", element.y * 100, 0.1, (v) => update({ y: v / 100 })),
      numberField("Rotation °", element.rotation || 0, 1, (v) => update({ rotation: v }))
    ),
    el("div", { class: "row" }, centerH, centerV),
  ];
}

function renderPropertiesPanel() {
  const panel = $("designer-properties");
  if (!panel) return;
  panel.replaceChildren();
  const index = designerState.selected;
  if (index < 0 || !designerState.elements[index]) {
    panel.append(el("div", { class: "field-help" }, "Select an element to edit its properties."));
    return;
  }
  const element = designerState.elements[index];

  function update(patch) {
    Object.assign(element, patch);
    renderElementList();
    redraw();
  }

  if (element.type === "text") {
    const textInput = el("textarea", { rows: "2" });
    textInput.value = element.text || "";
    textInput.oninput = () => update({ text: textInput.value });

    const fontSelect = el("select");
    api("/api/fonts")
      .then((payload) => {
        fontSelect.replaceChildren();
        for (const name of payload.fonts) fontSelect.append(el("option", { value: name }, name));
        fontSelect.value = element.font;
      })
      .catch(() => {});
    fontSelect.onchange = () => update({ font: fontSelect.value });

    const colorInput = el("input", { type: "color", value: element.color || "#000000" });
    colorInput.oninput = () => update({ color: colorInput.value });

    const alignSelect = el("select");
    for (const value of ["left", "center", "right"]) alignSelect.append(el("option", { value }, value));
    alignSelect.value = element.align || "center";
    alignSelect.onchange = () => update({ align: alignSelect.value });

    panel.append(
      field("Text", textInput),
      field("Font", fontSelect),
      field("Color", colorInput),
      rangeField("Size", element.size, 0.01, 0.3, 0.005, (value) => update({ size: value })),
      numberField("Size %", element.size * 100, 0.1, (v) => update({ size: v / 100 })),
      rangeField("Rotation", element.rotation || 0, -180, 180, 1, (value) => update({ rotation: value })),
      field("Align", alignSelect),
      ...positionFields(element, update)
    );
  } else if (element.type === "image") {
    const assetName = el("span", { class: "imgpick-name" }, element.asset || "No image selected");
    const chooseBtn = el("button", { class: "btn small" }, "Choose image…");
    chooseBtn.onclick = () =>
      openAssetPicker((path) => {
        const name = basename(path);
        assetName.textContent = name;
        update({ asset: name });
      });
    panel.append(
      field("Image", el("div", { class: "row" }, chooseBtn, assetName)),
      rangeField("Width", element.width, 0.02, 1, 0.01, (value) => update({ width: value })),
      numberField("Width %", element.width * 100, 0.1, (v) => update({ width: v / 100 })),
      rangeField("Rotation", element.rotation || 0, -180, 180, 1, (value) => update({ rotation: value })),
      rangeField("Opacity", element.opacity == null ? 1 : element.opacity, 0, 1, 0.05, (value) => update({ opacity: value })),
      ...positionFields(element, update)
    );
  } else if (element.type === "frame") {
    const colorInput = el("input", { type: "color", value: element.color || "#000000" });
    colorInput.oninput = () => update({ color: colorInput.value });
    panel.append(
      field("Color", colorInput),
      rangeField("Line width", element.width, 0.001, 0.05, 0.001, (value) => update({ width: value })),
      rangeField("Radius", element.radius, 0, 0.2, 0.005, (value) => update({ radius: value })),
      rangeField("Inset", element.inset, 0, 0.2, 0.005, (value) => update({ inset: value }))
    );
  }
}

/* --------------------------------------------------------------- layout */

function resizeCanvas(canvas) {
  const size = designerCanvasSize();
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.style.aspectRatio = `${size.width} / ${size.height}`;
}

function buildToolbar() {
  const nameInput = el("input", { type: "text", placeholder: "e.g. gold-frame" });
  nameInput.value = designerState.name;
  nameInput.oninput = () => (designerState.name = nameInput.value);

  const orientationSelect = el("select");
  orientationSelect.append(el("option", { value: "portrait" }, "Portrait"), el("option", { value: "landscape" }, "Landscape"));
  orientationSelect.value = designerState.orientation;
  orientationSelect.onchange = () => {
    designerState.orientation = orientationSelect.value;
  };

  return el(
    "div",
    { class: "designer-toolbar" },
    field("Design name", nameInput),
    field("Orientation", orientationSelect)
  );
}

function buildAddButtons() {
  const textBtn = el("button", { class: "btn small" }, "+ Text");
  textBtn.onclick = () => {
    designerState.elements.push(newElement("text"));
    selectElement(designerState.elements.length - 1);
  };
  const imageBtn = el("button", { class: "btn small" }, "+ Image");
  imageBtn.onclick = () => {
    openAssetPicker((path) => {
      const element = newElement("image");
      element.asset = basename(path);
      designerState.elements.push(element);
      selectElement(designerState.elements.length - 1);
    });
  };
  const frameBtn = el("button", { class: "btn small" }, "+ Frame");
  frameBtn.onclick = () => {
    designerState.elements.push(newElement("frame"));
    selectElement(designerState.elements.length - 1);
  };
  return el("div", { class: "row" }, textBtn, imageBtn, frameBtn);
}

function resetDesignerState() {
  designerState.name = "";
  designerState.orientation = "portrait";
  designerState.elements = [];
  designerState.selected = -1;
}

async function loadDesignIntoEditor(name) {
  try {
    const spec = await api(`/api/designs/${encodeURIComponent(name)}`);
    designerState.name = spec.name || name;
    designerState.orientation = spec.orientation === "landscape" ? "landscape" : "portrait";
    designerState.elements = Array.isArray(spec.elements) ? spec.elements : [];
    designerState.selected = -1;
    renderDesignerPage();
    toast(`Loaded design "${designerState.name}"`);
  } catch (error) {
    toast(`Could not load design: ${error.message}`, "error");
  }
}

async function openLoadDesignDialog() {
  try {
    const payload = await api("/api/designs");
    designerState.designs = payload.designs;
  } catch (error) {
    toast(`Could not list designs: ${error.message}`, "error");
    return;
  }
  if (!designerState.designs.length) {
    toast("No design saved yet");
    return;
  }
  const names = designerState.designs.map((design) => design.name);
  const choice = prompt(`Load which design?\n${names.join(", ")}`, names[0]);
  if (choice && names.includes(choice)) loadDesignIntoEditor(choice);
}

function buildSaveRow() {
  const assignCheckbox = el("input", { type: "checkbox" });
  assignCheckbox.checked = true;

  const saveBtn = el("button", { class: "btn primary" }, "Save design");
  saveBtn.onclick = async () => {
    const spec = {
      name: designerState.name,
      orientation: designerState.orientation,
      elements: designerState.elements,
    };
    try {
      await api("/api/designs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec, assign: assignCheckbox.checked }),
      });
      if (assignCheckbox.checked) {
        toast("Design saved and set as overlay — check the Picture page preview ✔");
      } else {
        toast("Design saved ✔");
      }
    } catch (error) {
      toast(`Could not save design: ${error.message}`, "error");
    }
  };

  const loadBtn = el("button", { class: "btn" }, "Load…");
  loadBtn.onclick = openLoadDesignDialog;

  const newBtn = el("button", { class: "btn ghost" }, "New");
  newBtn.onclick = () => {
    resetDesignerState();
    renderDesignerPage();
  };

  return el(
    "div",
    { class: "designer-save-row" },
    el("label", { class: "row" }, assignCheckbox, "Use as print overlay"),
    el("div", { class: "row" }, saveBtn, loadBtn, newBtn)
  );
}

function buildSampleToggle() {
  const checkbox = el("input", { type: "checkbox" });
  checkbox.checked = designerState.showSample;
  checkbox.onchange = () => {
    designerState.showSample = checkbox.checked;
    if (designerState.showSample && !designerState.sampleImage) loadSampleBackdrop();
    redraw();
  };
  return el("label", { class: "row" }, checkbox, "Show sample picture");
}

function buildZoomControls() {
  const zoomOut = el("button", { class: "btn small ghost", title: "Zoom out" }, "−");
  zoomOut.onclick = () => designerState.editor && designerState.editor.setZoom(designerState.editor.viewport.scale / 1.25);
  const zoomIn = el("button", { class: "btn small ghost", title: "Zoom in" }, "+");
  zoomIn.onclick = () => designerState.editor && designerState.editor.setZoom(designerState.editor.viewport.scale * 1.25);
  const reset = el("button", { class: "btn small ghost", title: "Reset view" }, "⤢");
  reset.onclick = () => designerState.editor && designerState.editor.resetView();
  return el("div", { class: "canvas-zoom-controls" }, zoomOut, zoomIn, reset);
}

function renderDesignerPage() {
  $("section-title").textContent = "Overlay designer";
  $("section-hint").textContent = "Design a transparent print overlay: texts, images and frames laid over the final picture.";

  const canvasWrap = el(
    "div",
    { class: "designer-canvas-wrap" },
    el("div", { class: "designer-canvas-stack" }, el("canvas", { id: "designer-canvas" }), buildZoomControls()),
    el("div", { class: "designer-caption", id: "designer-geometry-caption" }, "Layout: …"),
    buildSampleToggle()
  );

  const controls = el(
    "div",
    { class: "designer-controls" },
    buildToolbar(),
    buildAddButtons(),
    el("div", { id: "designer-elements", class: "designer-element-list" }),
    el("h3", {}, "Properties"),
    el("div", { id: "designer-properties" }),
    buildSaveRow()
  );

  const body = $("section-body");
  body.replaceChildren(el("div", { class: "designer-layout" }, canvasWrap, controls));

  const canvas = $("designer-canvas");
  resizeCanvas(canvas);

  if (designerState.editor) designerState.editor.destroy();
  designerState.editor = CanvasEditor.create({
    canvas,
    getItems: () => designerState.elements,
    itemRect: elementItemRect,
    setItemRect: setElementItemRect,
    draw: drawScene,
    onSelect: onCanvasSelect,
    onChange: () => renderPropertiesPanel(),
    onDelete: (item) => deleteElement(designerState.elements.indexOf(item)),
    aspectLocked: (item) => !!item && item.type === "image",
  });
  registerPageCleanup(() => designerState.editor && designerState.editor.destroy());

  if (designerState.showSample) loadSampleBackdrop();

  renderElementList();
  renderPropertiesPanel();
  fetchDesignerGeometry();
}
