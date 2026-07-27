/* pibooth web configuration interface — picture layout designer page */
"use strict";

/* Register after the overlay designer (see designer.js CUSTOM_PAGES.push);
 * script order in index.html guarantees this entry lands last.
 */
CUSTOM_PAGES.push({
  id: "LAYOUT",
  label: "Layout designer",
  icon: "📐",
  render: renderLayoutPage,
  group: "design",
  order: 0,
  wide: true,
});

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

//: Internal (backing-store) height of the layout canvas in px. The canvas is
//: displayed CSS-scaled to fit its column, so this only sets the drawing
//: resolution — high enough to stay crisp when the designer is given the full
//: window width. Only the width/height *ratio* is semantically meaningful
//: (see snapShapeToRatio), and that is preserved for any value.
const LAYOUT_CANVAS_HEIGHT = 1040;

//: Shape kinds (mirrors pibooth.pictures.template's CAPTURE/TEXT/IMAGE/FRAME).
const FRAME = "frame";

//: Common photo/camera aspect ratios offered for the "snap" capture-slot
//: sizing shortcut, as width/height ratios.
const PHOTO_ASPECT_RATIOS = {
  "1:1": 1,
  "4:3": 4 / 3,
  "3:2": 3 / 2,
  "16:9": 16 / 9,
  "3:4": 3 / 4,
  "2:3": 2 / 3,
  "9:16": 9 / 16,
};

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
  frameStyles: [], // /api/frame-styles payload
  editingStyleId: null, // id of the frame style currently expanded for editing in the properties panel, or null
  styleDraft: null, // in-progress edits for layoutState.editingStyleId, applied on Save
  // Live references to the geometry inputs rendered by renderLayoutProperties,
  // so a canvas drag can write values straight into them instead of rebuilding
  // the whole panel on every pointer move. See syncLayoutPropertyInputs().
  propertyInputs: null, // {shape, x, y, width, height, rotation} or null
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

/** Find the capture-kind shape on `page` with the given slot index, or null. */
function findCaptureShape(page, index) {
  return page.shapes.find((s) => s.type === "capture" && s.index === index) || null;
}

/** Resolve a shape's effective {x, y, width, height, rotation} — in the same
 * fraction-of-page units as an absolute shape — following its `anchor` if
 * set. Mirrors pibooth.pictures.template.resolve_shape_rect() exactly (see
 * that function's docstring for the field-reinterpretation semantics);
 * identity passthrough when `shape.anchor` is falsy, so every existing
 * absolute shape is unaffected. Falls back to absolute if the anchor
 * target doesn't exist (e.g. the capture count changed).
 */
function resolveShapeRect(shape, page) {
  const identity = { x: shape.x, y: shape.y, width: shape.width, height: shape.height, rotation: shape.rotation || 0 };
  if (!shape.anchor) return identity;

  const anchorShape = findCaptureShape(page, shape.anchor);
  if (!anchorShape) return identity;

  const acx = anchorShape.x + anchorShape.width / 2;
  const acy = anchorShape.y + anchorShape.height / 2;
  const width = anchorShape.width * shape.width;
  const height = anchorShape.height * shape.height;
  const cx = acx + shape.x * anchorShape.width;
  const cy = acy + shape.y * anchorShape.height;
  return {
    x: cx - width / 2,
    y: cy - height / 2,
    width,
    height,
    rotation: (anchorShape.rotation || 0) + (shape.rotation || 0),
  };
}

/** Inverse of resolveShapeRect(): given a shape's new absolute (resolved)
 * rect — e.g. the result of a canvas drag — and the capture shape it's
 * anchored to, compute the equivalent anchor-relative x/y/width/height/
 * rotation to persist on `shape` instead of writing the absolute rect
 * directly.
 */
function storeAnchoredRect(shape, anchorShape, resolved) {
  const acx = anchorShape.x + anchorShape.width / 2;
  const acy = anchorShape.y + anchorShape.height / 2;
  const cx = resolved.x + resolved.width / 2;
  const cy = resolved.y + resolved.height / 2;
  shape.width = anchorShape.width ? resolved.width / anchorShape.width : 0;
  shape.height = anchorShape.height ? resolved.height / anchorShape.height : 0;
  shape.x = anchorShape.width ? (cx - acx) / anchorShape.width : 0;
  shape.y = anchorShape.height ? (cy - acy) / anchorShape.height : 0;
  shape.rotation = resolved.rotation - (anchorShape.rotation || 0);
}

/** Sort key placing page.shapes[index] next to the capture slot it's anchored
 * to: the chain of array positions from the anchor root down to the shape,
 * with the shape's own position repeated at the end so a child listed before
 * its parent still draws behind it. A dangling anchor or a cycle degrades to
 * the shape's own position. Mirrors _draw_order_key() in
 * pibooth/pictures/template.py.
 */
function layoutDrawOrderKey(index, page) {
  const chain = [index];
  const seen = new Set([index]);
  let current = page.shapes[index];
  while (current && current.anchor) {
    const parent = page.shapes.findIndex((s) => s.type === "capture" && s.index === current.anchor);
    if (parent < 0 || seen.has(parent)) break;
    chain.unshift(parent);
    seen.add(parent);
    current = page.shapes[parent];
  }
  return chain.concat(index);
}

/** page.shapes in the order they must be drawn — anchored shapes grouped with
 * the capture slot they're anchored to, so a frame on slot 1 is obscured by
 * slots 2 and 3 exactly as slot 1 itself is. Mirrors resolve_draw_order() in
 * pibooth/pictures/template.py; the two MUST agree or the designer preview
 * stops matching the printed picture.
 */
function layoutDrawOrder(page) {
  if (!page) return [];
  const keyed = page.shapes.map((shape, index) => ({ shape, key: layoutDrawOrderKey(index, page) }));
  keyed.sort((a, b) => {
    for (let i = 0; i < Math.min(a.key.length, b.key.length); i++) {
      if (a.key[i] !== b.key[i]) return a.key[i] - b.key[i];
    }
    return a.key.length - b.key.length;
  });
  return keyed.map((entry) => entry.shape);
}

/** Anchor-relative geometry that makes a shape exactly cover its anchor slot:
 * centered on it (offset 0), at 100% of its width/height, with no rotation of
 * its own. This is the natural starting point for a frame. */
const ANCHOR_FILL_RECT = { x: 0, y: 0, width: 1, height: 1, rotation: 0 };

/** Change `shape`'s anchor, converting its geometry so the change isn't a
 * jump — x/y/width/height mean completely different things either side of it
 * (see resolveShapeRect), so writing the anchor alone would teleport and
 * resize the shape.
 *
 * A frame snaps to cover its new slot: anchoring a frame means "frame this
 * photo", and 100% is a round number to adjust from. Anything else keeps the
 * rect it already occupies on the page, as does un-anchoring.
 */
function setShapeAnchor(page, shape, anchor) {
  if ((shape.anchor || 0) === anchor) return;
  const resolved = resolveShapeRect(shape, page); // where it sits right now
  const target = anchor ? findCaptureShape(page, anchor) : null;

  shape.anchor = anchor;
  if (anchor && target && shape.type === FRAME) {
    Object.assign(shape, ANCHOR_FILL_RECT);
  } else if (anchor && target) {
    storeAnchoredRect(shape, target, resolved);
  } else {
    // Absolute again (or a slot that doesn't exist): keep the current rect.
    Object.assign(shape, {
      x: resolved.x,
      y: resolved.y,
      width: resolved.width,
      height: resolved.height,
      rotation: resolved.rotation,
    });
  }
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

function findFrameStyle(styleId) {
  return layoutState.frameStyles.find((s) => s.id === styleId) || null;
}

function drawLayoutShape(ctx, shape, page, size) {
  const resolved = resolveShapeRect(shape, page);
  const x = resolved.x * size.width;
  const y = resolved.y * size.height;
  const w = Math.max(1, resolved.width * size.width);
  const h = Math.max(1, resolved.height * size.height);
  ctx.save();
  ctx.translate(x + w / 2, y + h / 2);
  ctx.rotate((resolved.rotation * Math.PI) / 180);

  if (shape.type === "capture") {
    ctx.fillStyle = SLOT_COLORS[(shape.index - 1) % SLOT_COLORS.length];
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
  } else if (shape.type === FRAME) {
    // While a style is being drafted for this shape, preview the draft live
    // instead of the last-saved style — otherwise slider edits show no change
    // until "Save style" is clicked.
    const isDraftingThisShape = layoutState.styleDraft && page.shapes[layoutState.selectedShapeIndex] === shape;
    const style = isDraftingThisShape ? layoutState.styleDraft : findFrameStyle(shape.styleId);
    if (!style) {
      ctx.strokeStyle = "#cccccc";
      ctx.setLineDash([2, 4]);
      ctx.strokeRect(-w / 2, -h / 2, w, h);
      ctx.setLineDash([]);
    } else if (style.kind === "vector") {
      const minDim = Math.min(w, h);
      ctx.strokeStyle = style.color || "#000000";
      ctx.lineWidth = Math.max(1, style.borderWidth * minDim);
      ctx.beginPath();
      ctx.roundRect(-w / 2, -h / 2, w, h, Math.max(0, style.radius * minDim));
      ctx.stroke();
    } else if (style.kind === "image") {
      const image = getLayoutCachedImage(style.asset);
      if (image && image.complete && image.naturalWidth) {
        ctx.globalAlpha = style.opacity == null ? 1 : style.opacity;
        if (shape.lockAspect) {
          const drawH = w * (image.naturalHeight / image.naturalWidth);
          ctx.drawImage(image, -w / 2, -drawH / 2, w, drawH);
        } else {
          ctx.drawImage(image, -w / 2, -h / 2, w, h);
        }
      }
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
  for (const shape of layoutDrawOrder(page)) drawLayoutShape(ctx, shape, page, size);
}

function redrawLayout() {
  if (layoutState.editor) layoutState.editor.requestDraw();
}

function resizeLayoutCanvas(canvas) {
  const size = layoutCanvasSize();
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.style.aspectRatio = `${size.width} / ${size.height}`;
  // Published for the CSS that caps the canvas wrapper by height budget —
  // see `.layout-canvas-col .designer-canvas-stack` in app.css.
  const stack = canvas.closest(".designer-canvas-stack");
  if (stack) stack.style.setProperty("--page-ratio", String(size.width / size.height));
}

/* ------------------------------------------------------ CanvasEditor adapter */

function shapeItemRect(shape) {
  const page = activeLayoutPage();
  const resolved = page ? resolveShapeRect(shape, page) : { x: shape.x, y: shape.y, width: shape.width, height: shape.height, rotation: shape.rotation || 0 };
  return { x: resolved.x, y: resolved.y, w: resolved.width, h: resolved.height, rotation: resolved.rotation, centerBased: false };
}

function setShapeItemRect(shape, rect) {
  const page = activeLayoutPage();
  const anchorShape = shape.anchor && page ? findCaptureShape(page, shape.anchor) : null;
  if (anchorShape) {
    storeAnchoredRect(shape, anchorShape, { x: rect.x, y: rect.y, width: rect.w, height: rect.h, rotation: rect.rotation });
  } else {
    shape.x = rect.x;
    shape.y = rect.y;
    shape.width = rect.w;
    shape.height = rect.h;
    shape.rotation = rect.rotation;
  }
}

function onLayoutSelect(shape) {
  const page = activeLayoutPage();
  layoutState.selectedShapeIndex = page && shape ? page.shapes.indexOf(shape) : -1;
  renderLayoutProperties();
  renderLayoutToolbar(); // the "+ Frame" button targets the selected slot
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

/** Array index of the capture slot `shape` is anchored to, or -1 if it isn't
 * anchored (or the anchor target no longer exists). */
function shapeParentIndex(page, shape) {
  if (!shape.anchor) return -1;
  return page.shapes.findIndex((s) => s.type === "capture" && s.index === shape.anchor && s !== shape);
}

/** The shapes `shape` can be reordered against, as array indices in ascending
 * order. Because draw order is derived from anchoring, a shape can only move
 * within its own group: an anchored shape moves among its siblings and its
 * anchor slot (so it can be sent behind the photo), while an unanchored shape
 * moves among the other unanchored shapes — taking everything anchored to it
 * along for the ride.
 */
function shapeZOrderPeers(page, shape) {
  const parent = shapeParentIndex(page, shape);
  const peers = [];
  page.shapes.forEach((other, i) => {
    if (other === shape) peers.push(i);
    else if (parent < 0 ? shapeParentIndex(page, other) < 0 : i === parent || shapeParentIndex(page, other) === parent) {
      peers.push(i);
    }
  });
  return peers;
}

/** Move a shape one step through its z-order peers. Returns its new array
 * index (unchanged if it's already at the end of its group). */
function moveShapeZOrder(page, index, delta) {
  const shape = page.shapes[index];
  const peers = shapeZOrderPeers(page, shape);
  const at = peers.indexOf(index);
  const target = peers[at + delta];
  if (at < 0 || target === undefined) return index;
  // Re-insert rather than swap: a shape and its anchor slot can be peers, and
  // swapping their array positions would move the slot's whole group instead
  // of just this shape.
  page.shapes.splice(index, 1);
  const shifted = target > index ? target - 1 : target;
  const insertAt = delta > 0 ? shifted + 1 : shifted;
  page.shapes.splice(insertAt, 0, shape);
  return insertAt;
}

function canMoveShapeZOrder(page, index, delta) {
  const peers = shapeZOrderPeers(page, page.shapes[index]);
  return peers[peers.indexOf(index) + delta] !== undefined;
}

/** Snap a shape's box to a target width/height ratio, keeping its center
 * fixed and shrinking (never growing) to fit within its current bounding
 * box. Aspect ratios are only meaningful in pixel space — the canvas isn't
 * generally square, so comparing width/height fractions directly would be
 * wrong — hence converting through `layoutCanvasSize()` and back.
 */
function snapShapeToRatio(shape, ratio) {
  const size = layoutCanvasSize();
  const boxW = shape.width * size.width;
  const boxH = shape.height * size.height;
  const centerX = (shape.x + shape.width / 2) * size.width;
  const centerY = (shape.y + shape.height / 2) * size.height;

  let newW, newH;
  if (boxW / boxH > ratio) {
    newH = boxH;
    newW = boxH * ratio;
  } else {
    newW = boxW;
    newH = boxW / ratio;
  }

  shape.width = newW / size.width;
  shape.height = newH / size.height;
  shape.x = (centerX - newW / 2) / size.width;
  shape.y = (centerY - newH / 2) / size.height;
}

function makeStyleId() {
  return "style-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

async function saveFrameStyle(draft) {
  return api("/api/frame-styles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ style: draft }),
  });
}

/** Inline editor for the frame style currently being created/edited
 * (`layoutState.styleDraft`), appended into the shape properties panel.
 * `shape` is the frame shape whose "Style" dropdown triggered this, so
 * Save can point it at the (possibly newly created) style immediately.
 */
function renderFrameStyleEditor(panel, shape, draft, isNew) {
  const container = el("div", { class: "frame-style-editor" });

  const nameInput = el("input", { type: "text", value: draft.name });
  nameInput.oninput = () => (draft.name = nameInput.value);

  const kindSelect = el("select");
  kindSelect.append(el("option", { value: "vector" }, "Vector (drawn outline)"));
  kindSelect.append(el("option", { value: "image" }, "Image (transparent PNG)"));
  kindSelect.value = draft.kind;
  kindSelect.onchange = () => {
    draft.kind = kindSelect.value;
    renderLayoutProperties();
    redrawLayout();
  };

  container.append(field("Name", nameInput), field("Kind", kindSelect));

  if (draft.kind === "vector") {
    const colorInput = el("input", { type: "color", value: draft.color || "#000000" });
    colorInput.oninput = () => {
      draft.color = colorInput.value;
      redrawLayout();
    };
    container.append(
      field("Color", colorInput),
      rangeField("Border width", draft.borderWidth, 0.001, 0.2, 0.001, (v) => {
        draft.borderWidth = v;
        redrawLayout();
      }),
      rangeField("Radius", draft.radius, 0, 0.5, 0.005, (v) => {
        draft.radius = v;
        redrawLayout();
      })
    );
  } else {
    const assetName = el("span", { class: "imgpick-name" }, draft.asset || "No image selected");
    const chooseBtn = el("button", { class: "btn small" }, "Choose image…");
    chooseBtn.onclick = () =>
      openAssetPicker((path) => {
        const name = basename(path);
        draft.asset = name;
        assetName.textContent = name;
        redrawLayout();
      });
    container.append(
      field("Image", el("div", { class: "row" }, chooseBtn, assetName)),
      rangeField("Opacity", draft.opacity == null ? 1 : draft.opacity, 0, 1, 0.05, (v) => {
        draft.opacity = v;
        redrawLayout();
      })
    );
  }

  const saveBtn = el("button", { class: "btn small" }, "Save style");
  saveBtn.onclick = async () => {
    try {
      await saveFrameStyle(draft);
      await loadFrameStyles();
      shape.styleId = draft.id;
      layoutState.editingStyleId = null;
      layoutState.styleDraft = null;
      renderLayoutProperties();
      redrawLayout();
      toast(`Frame style "${draft.name}" saved ✔`);
    } catch (error) {
      toast(`Could not save style: ${error.message}`, "error");
    }
  };
  const cancelBtn = el("button", { class: "btn small ghost" }, "Cancel");
  cancelBtn.onclick = () => {
    layoutState.editingStyleId = null;
    layoutState.styleDraft = null;
    renderLayoutProperties();
    redrawLayout();
  };
  const buttons = [saveBtn, cancelBtn];
  if (!isNew) {
    const deleteBtn = el("button", { class: "btn small ghost" }, "Delete style");
    deleteBtn.onclick = async () => {
      if (!confirm(`Delete frame style "${draft.name}"?`)) return;
      try {
        await api(`/api/frame-styles/${encodeURIComponent(draft.id)}`, { method: "DELETE" });
        await loadFrameStyles();
        if (shape.styleId === draft.id) shape.styleId = "";
        layoutState.editingStyleId = null;
        layoutState.styleDraft = null;
        renderLayoutProperties();
        redrawLayout();
        toast(`Frame style "${draft.name}" deleted`);
      } catch (error) {
        toast(`Could not delete style: ${error.message}`, "error");
      }
    };
    buttons.push(deleteBtn);
  }
  container.append(el("div", { class: "row" }, ...buttons));
  panel.append(container);
}

/* ------------------------------------------------------- inspector chrome */

//: Human-readable kind of a shape, for the inspector header.
function shapeKindLabel(shape) {
  if (shape.type === "capture") return "Capture slot";
  if (shape.type === "text") return "Text";
  if (shape.type === "image") return "Image";
  if (shape.type === FRAME) return "Frame";
  return shape.type;
}

//: Short identifying name of a shape, for the header and the shape list.
function shapeLabel(shape) {
  if (shape.type === "capture") return `Slot ${shape.index}`;
  if (shape.type === "text") return `Text ${shape.index}`;
  if (shape.type === "image") return shape.asset || "(no image)";
  if (shape.type === FRAME) {
    const style = findFrameStyle(shape.styleId);
    return style ? style.name : "(no style)";
  }
  return shape.type;
}

function renderLayoutInspectorHeader() {
  const header = $("layout-inspector-header");
  if (!header) return;
  const shape = selectedLayoutShape();
  if (!shape) {
    header.className = "layout-inspector-header empty";
    header.replaceChildren("Nothing selected");
    return;
  }
  header.className = "layout-inspector-header";
  header.replaceChildren(
    el("span", { class: "layout-inspector-kind" }, shapeKindLabel(shape)),
    el("span", {}, shapeLabel(shape))
  );
}

/** The shape list — the layout page has no other way to reach a shape that is
 * small, rotated or hidden under another one. Rows follow the effective draw
 * order (bottom first, matching ↑ Forward / ↓ Backward), with anchored shapes
 * indented under the capture slot whose layer they share.
 */
function renderLayoutShapeList() {
  const list = $("layout-shape-list");
  if (!list) return;
  list.replaceChildren();
  const page = activeLayoutPage();
  if (!page || !page.shapes.length) {
    list.append(el("div", { class: "field-help" }, "No shape yet — add one above the canvas."));
    return;
  }
  const selected = selectedLayoutShape();
  const rows = el("div", { class: "designer-element-list" });
  for (const shape of layoutDrawOrder(page)) {
    const index = page.shapes.indexOf(shape);
    const depth = layoutDrawOrderKey(index, page).length - 2; // 0 for unanchored
    const row = el(
      "div",
      { class: `designer-element-row${shape === selected ? " active" : ""}` },
      el("span", { class: "designer-element-label" }, `${shapeKindLabel(shape)} — ${shapeLabel(shape)}`)
    );
    if (depth > 0) {
      row.style.paddingLeft = `${8 + depth * 14}px`;
      row.title = `Anchored to slot ${shape.anchor} — drawn in that slot's layer`;
    }
    // editor.select() sets the canvas selection *and* fires onSelect, which
    // routes back through onLayoutSelect() to refresh this panel.
    row.onclick = () => layoutState.editor && layoutState.editor.select(shape);
    rows.append(row);
  }
  list.append(rows);
}

function selectedLayoutShape() {
  const page = activeLayoutPage();
  const index = layoutState.selectedShapeIndex;
  if (!page || index < 0 || !page.shapes[index]) return null;
  return page.shapes[index];
}

/** Push the selected shape's current geometry into the already-rendered
 * inputs. Called on every pointer move during a canvas drag, where rebuilding
 * the panel would be wasteful and would blow away focus mid-edit.
 */
function syncLayoutPropertyInputs() {
  const shape = selectedLayoutShape();
  const refs = layoutState.propertyInputs;
  if (!shape || !refs || refs.shape !== shape) {
    renderLayoutProperties();
    return;
  }
  const values = {
    x: shape.x * 100,
    y: shape.y * 100,
    width: shape.width * 100,
    height: shape.height * 100,
    rotation: shape.rotation || 0,
  };
  for (const [key, value] of Object.entries(values)) {
    const input = refs[key];
    // Never fight the field the user is currently typing into.
    if (!input || input === document.activeElement) continue;
    input.value = String(Math.round(value * 100) / 100);
  }
}

/** numberField() wrapper that also records the input on
 * layoutState.propertyInputs under `key`, for syncLayoutPropertyInputs(). */
function trackedNumberField(key, labelText, value, step, onInput) {
  const node = numberField(labelText, value, step, onInput);
  layoutState.propertyInputs[key] = node.querySelector("input");
  return node;
}

function renderLayoutProperties() {
  const panel = $("layout-properties");
  if (!panel) return;
  panel.replaceChildren();
  layoutState.propertyInputs = null;
  renderLayoutInspectorHeader();
  renderLayoutShapeList();
  const page = activeLayoutPage();
  const index = layoutState.selectedShapeIndex;
  if (!page || index < 0 || !page.shapes[index]) {
    panel.append(el("div", { class: "field-help" }, "Select a shape on the canvas or in the list above."));
    return;
  }
  const shape = page.shapes[index];
  layoutState.propertyInputs = { shape };

  function update(patch) {
    Object.assign(shape, patch);
    redrawLayout();
  }

  const anchorSelect = el("select");
  anchorSelect.append(el("option", { value: "0" }, "None (absolute)"));
  for (let i = 1; i <= page.captures; i++) {
    if (shape.type === "capture" && shape.index === i) continue; // no self-anchor
    anchorSelect.append(el("option", { value: String(i) }, `Slot ${i}`));
  }
  anchorSelect.value = String(shape.anchor || 0);
  anchorSelect.onchange = () => {
    setShapeAnchor(page, shape, parseInt(anchorSelect.value, 10));
    redrawLayout();
    renderLayoutProperties();
  };
  panel.append(field("Anchor", anchorSelect));

  const anchored = !!shape.anchor;
  panel.append(
    el(
      "div",
      { class: "designer-numeric-grid" },
      trackedNumberField("x", anchored ? "Offset X %" : "X %", shape.x * 100, 0.1, (v) => update({ x: v / 100 })),
      trackedNumberField("y", anchored ? "Offset Y %" : "Y %", shape.y * 100, 0.1, (v) => update({ y: v / 100 })),
      trackedNumberField("width", anchored ? "Scale W %" : "W %", shape.width * 100, 0.1, (v) => update({ width: v / 100 })),
      trackedNumberField("height", anchored ? "Scale H %" : "H %", shape.height * 100, 0.1, (v) => update({ height: v / 100 }))
    ),
    trackedNumberField("rotation", anchored ? "Extra rotation °" : "Rotation °", shape.rotation || 0, 1, (v) =>
      update({ rotation: v })
    )
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
      renderLayoutProperties(); // slot numbers appear in the header and list
      redrawLayout();
    };
    panel.append(field("Capture index", select));

    const ratioRow = el(
      "div",
      { class: "ratio-snap-row" },
      ...Object.entries(PHOTO_ASPECT_RATIOS).map(([label, ratio]) => {
        const btn = el("button", { class: "btn small ghost", title: `Snap to ${label}` }, label);
        btn.onclick = () => {
          snapShapeToRatio(shape, ratio);
          renderLayoutProperties();
          redrawLayout();
        };
        return btn;
      })
    );
    panel.append(field("Snap to ratio", ratioRow));
  }

  if (shape.type === FRAME) {
    const styleSelect = el("select");
    styleSelect.append(el("option", { value: "" }, "No style selected"));
    for (const style of layoutState.frameStyles) styleSelect.append(el("option", { value: style.id }, style.name));
    styleSelect.value = shape.styleId || "";
    styleSelect.onchange = () => update({ styleId: styleSelect.value });

    const styleRow = el("div", { class: "row" }, styleSelect);
    if (shape.styleId) {
      const editBtn = el("button", { class: "btn small ghost" }, "Edit style…");
      editBtn.onclick = () => {
        const existing = findFrameStyle(shape.styleId);
        if (!existing) return;
        layoutState.styleDraft = { ...existing };
        layoutState.editingStyleId = layoutState.styleDraft.id;
        renderLayoutProperties();
        redrawLayout();
      };
      styleRow.append(editBtn);
    }
    const newBtn = el("button", { class: "btn small ghost" }, "+ New style…");
    newBtn.onclick = () => {
      layoutState.styleDraft = {
        id: makeStyleId(),
        name: "New style",
        kind: "vector",
        color: "#000000",
        borderWidth: 0.01,
        radius: 0.03,
        asset: "",
        opacity: 1,
      };
      layoutState.editingStyleId = layoutState.styleDraft.id;
      renderLayoutProperties();
      redrawLayout();
    };
    styleRow.append(newBtn);
    panel.append(field("Style", styleRow));

    if (layoutState.editingStyleId && layoutState.styleDraft) {
      const isNew = !findFrameStyle(layoutState.styleDraft.id);
      renderFrameStyleEditor(panel, shape, layoutState.styleDraft, isNew);
    }

    const selectedStyle = findFrameStyle(shape.styleId);
    if (selectedStyle && selectedStyle.kind === "image") {
      const lockInput = el("input", { type: "checkbox" });
      lockInput.checked = shape.lockAspect !== false;
      lockInput.onchange = () => update({ lockAspect: lockInput.checked });
      panel.append(field("Lock aspect", el("label", { class: "toggle" }, lockInput, el("span", { class: "slider" }))));
    }
  }

  // Draw order is derived from anchoring, so these move the shape within its
  // own group only (see shapeZOrderPeers) — an anchored shape can't be lifted
  // out of its slot's layer, and moving a slot carries its group along.
  const zHint = shape.anchor
    ? `within slot ${shape.anchor}'s layer`
    : page.shapes.some((s) => shapeParentIndex(page, s) === index)
      ? "(carries anchored shapes along)"
      : "";
  const forwardBtn = el("button", { class: "btn small ghost", title: `Bring forward ${zHint}`.trim() }, "↑ Forward");
  forwardBtn.disabled = !canMoveShapeZOrder(page, index, 1);
  forwardBtn.onclick = () => {
    layoutState.selectedShapeIndex = moveShapeZOrder(page, index, 1);
    renderLayoutProperties(); // the shape list is in draw order, so it must follow
    redrawLayout();
  };
  const backwardBtn = el("button", { class: "btn small ghost", title: `Send backward ${zHint}`.trim() }, "↓ Backward");
  backwardBtn.disabled = !canMoveShapeZOrder(page, index, -1);
  backwardBtn.onclick = () => {
    layoutState.selectedShapeIndex = moveShapeZOrder(page, index, -1);
    renderLayoutProperties();
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

/** Append a freshly built shape to the active page and select it, so the
 * inspector immediately shows its properties (and the shape list refreshes). */
function addLayoutShape(page, shape) {
  page.shapes.push(shape);
  renderLayoutToolbar();
  if (layoutState.editor) layoutState.editor.select(shape);
  else renderLayoutProperties();
  redrawLayout();
}

function renderLayoutToolbar() {
  const bar = $("layout-add-toolbar");
  if (!bar) return;
  bar.replaceChildren();
  const page = activeLayoutPage();

  const captureBtn = el("button", { class: "btn small" }, "+ Capture slot");
  const freeIndex = page ? nextFreeCaptureIndex(page) : null;
  captureBtn.disabled = !page || freeIndex === null;
  captureBtn.onclick = () => {
    addLayoutShape(page, { type: "capture", index: freeIndex, x: 0.3, y: 0.3, width: 0.3, height: 0.3, rotation: 0 });
  };

  const text1Btn = el("button", { class: "btn small" }, "+ Text 1");
  text1Btn.disabled = !page || hasTextSlot(page, 1);
  text1Btn.onclick = () => {
    addLayoutShape(page, { type: "text", index: 1, x: 0.1, y: 0.85, width: 0.8, height: 0.08, rotation: 0 });
  };

  const text2Btn = el("button", { class: "btn small" }, "+ Text 2");
  text2Btn.disabled = !page || hasTextSlot(page, 2);
  text2Btn.onclick = () => {
    addLayoutShape(page, { type: "text", index: 2, x: 0.1, y: 0.94, width: 0.8, height: 0.05, rotation: 0 });
  };

  const imageBtn = el("button", { class: "btn small" }, "+ Image");
  imageBtn.disabled = !page;
  imageBtn.onclick = () => {
    openAssetPicker((path) => {
      addLayoutShape(page, {
        type: "image",
        asset: basename(path),
        index: 0,
        x: 0.1,
        y: 0.1,
        width: 0.3,
        height: 0.2,
        rotation: 0,
      });
    });
  };

  // With a capture slot selected, a new frame is almost always meant to frame
  // that slot — so anchor it and size it to match, rather than dropping a
  // free-floating box the user then has to anchor and resize by hand.
  const selected = selectedLayoutShape();
  const frameTarget = selected && selected.type === "capture" ? selected : null;
  const frameBtn = el("button", { class: "btn small" }, frameTarget ? `+ Frame on slot ${frameTarget.index}` : "+ Frame");
  frameBtn.disabled = !page;
  frameBtn.onclick = () => {
    addLayoutShape(page, {
      type: FRAME,
      index: 0,
      styleId: "",
      anchor: frameTarget ? frameTarget.index : 0,
      ...(frameTarget ? ANCHOR_FILL_RECT : { x: 0.3, y: 0.3, width: 0.3, height: 0.3, rotation: 0 }),
      lockAspect: true,
    });
  };

  bar.append(captureBtn, text1Btn, text2Btn, imageBtn, frameBtn);
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

/* -------------------------------------------------------- export as guide */

async function isTemplateNameSaved(name) {
  try {
    const payload = await api("/api/templates");
    return payload.templates.some((t) => t.name === name);
  } catch (error) {
    return false;
  }
}

function closeExportMenu() {
  const menu = $("layout-export-menu");
  if (menu) menu.remove();
  document.removeEventListener("pointerdown", onExportMenuOutsideClick);
}

function onExportMenuOutsideClick(event) {
  const menu = $("layout-export-menu");
  const anchor = $("layout-export-btn");
  if (menu && !menu.contains(event.target) && event.target !== anchor) closeExportMenu();
}

function buildExportLink(label, href) {
  const link = el("a", { class: "layout-export-link", href, download: "" }, label);
  link.onclick = () => closeExportMenu();
  return link;
}

function openExportMenu(anchorBtn) {
  closeExportMenu();
  const page = activeLayoutPage();
  if (!page) return;
  const name = encodeURIComponent(layoutState.templateName);
  const query = `captures=${page.captures}&orientation=${pageOrientationOf(page)}`;
  const base = `/api/templates/${name}/guide`;

  const menu = el(
    "div",
    { class: "layout-export-menu", id: "layout-export-menu" },
    buildExportLink("SVG (vector)", `${base}.svg?${query}`),
    buildExportLink("PNG (print-size)", `${base}.png?${query}`),
    el(
      "div",
      { class: "layout-export-hint" },
      "Design your artwork over the guides, delete the guide layer, export a transparent PNG and upload it as overlay."
    )
  );

  const rect = anchorBtn.getBoundingClientRect();
  menu.style.left = `${rect.left}px`;
  menu.style.top = `${rect.bottom + 6}px`;
  document.body.append(menu);
  setTimeout(() => document.addEventListener("pointerdown", onExportMenuOutsideClick), 0);
}

function buildExportButton() {
  const exportBtn = el("button", { class: "btn", id: "layout-export-btn" }, "Export for image editor…");
  exportBtn.onclick = async () => {
    if (!layoutState.templateName || !(await isTemplateNameSaved(layoutState.templateName))) {
      toast("Save the layout first", "error");
      return;
    }
    openExportMenu(exportBtn);
  };
  return exportBtn;
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
    { class: "layout-topbar-actions" },
    el("label", { class: "row" }, assignCheckbox, "Use for the booth"),
    saveBtn,
    loadBtn,
    importLabel,
    newBtn,
    buildExportButton()
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

  // Document-level settings and actions only — anything that belongs to the
  // current selection lives in the inspector, so it stays near the canvas.
  return el(
    "div",
    { class: "layout-topbar" },
    field("Template name", nameInput),
    field("Paper preset", paperSelect),
    field("DPI", dpiSelect),
    buildLayoutSaveRow()
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

async function loadFrameStyles() {
  try {
    layoutState.frameStyles = (await api("/api/frame-styles")).styles;
  } catch (error) {
    layoutState.frameStyles = [];
  }
}

function renderLayoutPage() {
  $("section-title").textContent = "Layout designer";
  $("section-hint").textContent = "Design the physical print layout: capture slots, footer texts and decorative images per page.";

  const page = activeLayoutPage();
  const caption = page
    ? `Page: ${page.size[0]}x${page.size[1]}px, ${page.captures} capture${page.captures > 1 ? "s" : ""}, ${pageOrientationOf(page)}`
    : "No page yet — click a chip above to create one.";

  const canvasCol = el(
    "div",
    { class: "layout-canvas-col" },
    el("div", { id: "layout-add-toolbar", class: "row" }),
    el("div", { class: "designer-canvas-stack" }, el("canvas", { id: "layout-canvas" }), buildLayoutZoomControls()),
    el("div", { class: "designer-caption" }, caption)
  );

  const inspector = el(
    "aside",
    { class: "layout-inspector" },
    el("div", { id: "layout-inspector-header", class: "layout-inspector-header" }),
    el(
      "div",
      { class: "layout-inspector-body" },
      el("div", { class: "layout-inspector-section" }, el("h4", {}, "Shapes"), el("div", { id: "layout-shape-list" })),
      el("div", { class: "layout-inspector-section" }, el("h4", {}, "Properties"), el("div", { id: "layout-properties" }))
    )
  );

  const body = $("section-body");
  body.replaceChildren(
    el(
      "div",
      { class: "layout-designer-page" },
      buildTopBar(),
      buildPageChips(),
      el("div", { class: "layout-split" }, canvasCol, inspector)
    )
  );

  const canvas = $("layout-canvas");
  resizeLayoutCanvas(canvas);

  if (layoutState.editor) layoutState.editor.destroy();
  layoutState.editor = CanvasEditor.create({
    canvas,
    // Draw order, not list order: CanvasEditor hit-tests back-to-front, so
    // clicking overlapping shapes must pick the one actually drawn on top.
    getItems: () => layoutDrawOrder(activeLayoutPage()),
    itemRect: shapeItemRect,
    setItemRect: setShapeItemRect,
    draw: drawLayoutScene,
    onSelect: onLayoutSelect,
    onChange: syncLayoutPropertyInputs,
    onDelete: (item) => deleteLayoutShape(activeLayoutPage().shapes.indexOf(item)),
    aspectLocked: () => false,
  });
  registerPageCleanup(() => {
    if (layoutState.editor) layoutState.editor.destroy();
    closeExportMenu();
  });

  // A fresh editor starts with nothing selected; carry over any selection the
  // state still holds so the canvas outline and the inspector can't disagree.
  const preselected = selectedLayoutShape();
  if (preselected) layoutState.editor.select(preselected);

  renderLayoutToolbar();
  renderLayoutProperties();
  loadFrameStyles().then(() => {
    renderLayoutProperties();
    redrawLayout();
  });
}
