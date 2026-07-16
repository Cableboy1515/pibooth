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
  dragging: false,
  dragOffset: { x: 0, y: 0 },
};

const DESIGNER_CANVAS_HEIGHT = 520;

/* ------------------------------------------------------------- geometry */

function designerCanvasSize() {
  const ratio = designerState.orientation === "landscape" ? 3 / 2 : 2 / 3;
  const height = DESIGNER_CANVAS_HEIGHT;
  const width = Math.round(height * ratio);
  return { width, height };
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

function redraw() {
  const canvas = $("designer-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const size = designerCanvasSize();
  ctx.clearRect(0, 0, size.width, size.height);

  if (designerState.showSample && designerState.sampleImage && designerState.sampleImage.complete) {
    ctx.save();
    ctx.globalAlpha = 0.6;
    ctx.drawImage(designerState.sampleImage, 0, 0, size.width, size.height);
    ctx.restore();
  }

  designerState.elements.forEach((element) => drawElement(ctx, element, size));

  if (designerState.selected >= 0 && designerState.elements[designerState.selected]) {
    const element = designerState.elements[designerState.selected];
    const bounds = elementBounds(ctx, element, size);
    ctx.save();
    ctx.translate(bounds.cx, bounds.cy);
    ctx.rotate(((bounds.rotation || 0) * Math.PI) / 180);
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = "#239587";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(-bounds.width / 2 - 4, -bounds.height / 2 - 4, bounds.width + 8, bounds.height + 8);
    ctx.restore();
  }
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

/* --------------------------------------------------------- hit-testing */

function hitTest(px, py, canvasSize) {
  const ctx = $("designer-canvas").getContext("2d");
  for (let i = designerState.elements.length - 1; i >= 0; i--) {
    const element = designerState.elements[i];
    const bounds = elementBounds(ctx, element, canvasSize);
    // Translate point into the element's unrotated local space
    const angle = (-(bounds.rotation || 0) * Math.PI) / 180;
    const dx = px - bounds.cx;
    const dy = py - bounds.cy;
    const localX = dx * Math.cos(angle) - dy * Math.sin(angle);
    const localY = dx * Math.sin(angle) + dy * Math.cos(angle);
    if (Math.abs(localX) <= bounds.width / 2 && Math.abs(localY) <= bounds.height / 2) {
      return i;
    }
  }
  return -1;
}

/* ------------------------------------------------------------- controls */

function selectElement(index) {
  designerState.selected = index;
  renderElementList();
  renderPropertiesPanel();
  redraw();
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
  designerState.selected = index + 1;
  renderElementList();
  renderPropertiesPanel();
  redraw();
}

function deleteElement(index) {
  designerState.elements.splice(index, 1);
  if (designerState.selected === index) designerState.selected = -1;
  else if (designerState.selected > index) designerState.selected -= 1;
  renderElementList();
  renderPropertiesPanel();
  redraw();
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
      rangeField("Rotation", element.rotation || 0, -180, 180, 1, (value) => update({ rotation: value })),
      field("Align", alignSelect)
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
      rangeField("Rotation", element.rotation || 0, -180, 180, 1, (value) => update({ rotation: value })),
      rangeField("Opacity", element.opacity == null ? 1 : element.opacity, 0, 1, 0.05, (value) => update({ opacity: value }))
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

/* ---------------------------------------------------------- canvas events */

function bindCanvasEvents(canvas) {
  canvas.onmousedown = (event) => {
    const rect = canvas.getBoundingClientRect();
    const size = designerCanvasSize();
    const px = ((event.clientX - rect.left) / rect.width) * size.width;
    const py = ((event.clientY - rect.top) / rect.height) * size.height;
    const index = hitTest(px, py, size);
    selectElement(index);
    if (index >= 0) {
      designerState.dragging = true;
      const element = designerState.elements[index];
      designerState.dragOffset = { x: px / size.width - element.x, y: py / size.height - element.y };
    }
  };

  canvas.onmousemove = (event) => {
    if (!designerState.dragging || designerState.selected < 0) return;
    const rect = canvas.getBoundingClientRect();
    const size = designerCanvasSize();
    const px = ((event.clientX - rect.left) / rect.width) * size.width;
    const py = ((event.clientY - rect.top) / rect.height) * size.height;
    const element = designerState.elements[designerState.selected];
    element.x = Math.min(1, Math.max(0, px / size.width - designerState.dragOffset.x));
    element.y = Math.min(1, Math.max(0, py / size.height - designerState.dragOffset.y));
    redraw();
  };

  const stopDrag = () => {
    designerState.dragging = false;
  };
  canvas.onmouseup = stopDrag;
  canvas.onmouseleave = stopDrag;
}

function bindKeyboardEvents() {
  document.addEventListener("keydown", (event) => {
    if (state.active !== "DESIGNER") return;
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (event.key === "Delete" || event.key === "Backspace") {
      if (designerState.selected >= 0) {
        event.preventDefault();
        deleteElement(designerState.selected);
      }
    }
  });
}

let designerKeyboardBound = false;

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
    const canvas = $("designer-canvas");
    resizeCanvas(canvas);
    redraw();
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

function renderDesignerPage() {
  $("section-title").textContent = "Overlay designer";
  $("section-hint").textContent = "Design a transparent print overlay: texts, images and frames laid over the final picture.";

  const canvasWrap = el(
    "div",
    { class: "designer-canvas-wrap" },
    el("canvas", { id: "designer-canvas" }),
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
  bindCanvasEvents(canvas);
  if (!designerKeyboardBound) {
    bindKeyboardEvents();
    designerKeyboardBound = true;
  }
  if (designerState.showSample) loadSampleBackdrop();

  renderElementList();
  renderPropertiesPanel();
  redraw();
}
