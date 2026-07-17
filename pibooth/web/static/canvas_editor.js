/* pibooth web configuration interface — shared canvas interaction library
 *
 * Dependency-free interaction layer for a <canvas> manipulating a list of
 * abstract "items" (whatever shape the caller's data model uses — overlay
 * design elements, template shapes, ...). The caller supplies an adapter
 * (itemRect/setItemRect) that translates between its own data model and the
 * fraction-of-canvas rectangle {x, y, w, h, rotation, centerBased} this
 * library works with internally, plus a draw() callback that paints the
 * scene itself (background + items). This library then owns selection,
 * move, resize, snapping, keyboard nudge and zoom/pan on top of that.
 *
 * Coordinate spaces used throughout (see CanvasEditor.create):
 *  - "fraction" space: 0..1 across the canvas width/height, as stored in the
 *    caller's data model (via the adapter).
 *  - "local" space: fraction * canvas.width / canvas.height, i.e. the
 *    logical drawing surface the caller's draw() callback paints into. This
 *    is the same regardless of zoom — it's what canvas.width/height mean.
 *  - "view" space: actual canvas pixel space as touched by the mouse
 *    (canvas.getBoundingClientRect() maps CSS pixels to it). The
 *    viewport {scale, offsetX, offsetY} transform maps local -> view:
 *      view = local * scale + offset
 *    At scale=1/offset=0 local and view coincide.
 */
"use strict";

//: Muted slot palette shared by the layout designer (capture slot fill) and
//: the overlay designer (layout guide layer drawn behind its elements) —
//: mirrors GUIDE_COLORS in pibooth/pictures/template.py; keep both in sync.
const SLOT_COLORS = ["#8fb8ae", "#c9a66b", "#a98fb8", "#6ba3c9"];

const CanvasEditor = (function () {
  const HANDLE_SIZE = 9; // constant screen/view px, regardless of zoom
  const HANDLE_HIT_PAD = 3; // extra px tolerance around a handle for hit-testing
  const MOVE_MARGIN = 0.1; // fraction of item that must stay on canvas when moving
  const MIN_SIZE_FRACTION = 0.01; // minimum item size, as a fraction of canvas
  const NUDGE_STEP = 0.002;
  const NUDGE_STEP_SHIFT = 0.02;
  const SNAP_PX = 6; // view-space px snap threshold
  const MIN_ZOOM = 0.5;
  const MAX_ZOOM = 4;
  const ACCENT = "#239587";

  // Sign of each handle along the item's own (unrotated) width/height axes.
  // 0 means that axis is not affected by this handle (edge handles).
  const HANDLE_SIGNS = {
    nw: { sx: -1, sy: -1 },
    ne: { sx: 1, sy: -1 },
    se: { sx: 1, sy: 1 },
    sw: { sx: -1, sy: 1 },
    n: { sx: 0, sy: -1 },
    s: { sx: 0, sy: 1 },
    e: { sx: 1, sy: 0 },
    w: { sx: -1, sy: 0 },
  };
  const HANDLE_CODES = Object.keys(HANDLE_SIGNS);
  const CORNER_CODES = ["nw", "ne", "se", "sw"];

  function clamp(value, lo, hi) {
    return Math.min(hi, Math.max(lo, value));
  }

  /** Rotate vector (x, y) by `angleDeg` clockwise (canvas convention). */
  function rotate(x, y, angleDeg) {
    const angle = (angleDeg * Math.PI) / 180;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    return { x: x * cos - y * sin, y: x * sin + y * cos };
  }

  /** Rotate vector (x, y) by `angleDeg` counter-clockwise (the inverse of `rotate`). */
  function rotateInverse(x, y, angleDeg) {
    return rotate(x, y, -angleDeg);
  }

  /**
   * Clamp a center coordinate (in canvas-dimension units, e.g. px or
   * fraction-with-dim=1) so at least `MOVE_MARGIN` of the item's extent
   * along that axis stays within [0, dim].
   */
  function clampCenter(center, size, dim) {
    const lo = (MOVE_MARGIN - 0.5) * size;
    const hi = dim - (MOVE_MARGIN - 0.5) * size;
    return clamp(center, lo, hi);
  }

  function create(options) {
    const canvas = options.canvas;
    const adapter = { itemRect: options.itemRect, setItemRect: options.setItemRect };
    const aspectLocked = options.aspectLocked || (() => false);
    const onSelect = options.onSelect || (() => {});
    const onChange = options.onChange || (() => {});
    const onDelete = options.onDelete || (() => {});
    // Optional extra (non-selectable) snap targets, e.g. a layout guide
    // layer: () => [{x, y, w, h}] in FRACTIONS of the canvas, top-left based.
    const extraSnapRects = options.extraSnapRects || (() => []);

    const viewport = { scale: 1, offsetX: 0, offsetY: 0 };
    let selectedItem = null;
    let drag = null; // active pointer interaction, see below
    let spaceHeld = false;
    let activeGuideX = null; // local-space x of the active vertical snap guide, or null
    let activeGuideY = null;
    let rafId = null;
    let destroyed = false;

    /* ------------------------------------------------------- coordinate helpers */

    function canvasSize() {
      return { width: canvas.width, height: canvas.height };
    }

    /** Map a client (mouse event) coordinate to view space (canvas own pixel grid). */
    function clientToView(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      return {
        x: ((clientX - rect.left) * canvas.width) / rect.width,
        y: ((clientY - rect.top) * canvas.height) / rect.height,
      };
    }

    /** Map view space to local (logical, unzoomed) space — inverse of the viewport transform. */
    function viewToLocal(vx, vy) {
      return { x: (vx - viewport.offsetX) / viewport.scale, y: (vy - viewport.offsetY) / viewport.scale };
    }

    function localToView(lx, ly) {
      return { x: lx * viewport.scale + viewport.offsetX, y: ly * viewport.scale + viewport.offsetY };
    }

    /** Convert an item's fraction rect (via the adapter) to a local-space pixel rect
     * {cx, cy, w, h, rotation, centerBased} with cx/cy the item's CENTER. */
    function pixelRectOf(item) {
      const r = adapter.itemRect(item);
      if (!r) return null;
      const size = canvasSize();
      const w = r.w * size.width;
      const h = r.h * size.height;
      const cx = r.centerBased ? r.x * size.width : r.x * size.width + w / 2;
      const cy = r.centerBased ? r.y * size.height : r.y * size.height + h / 2;
      return { cx, cy, w, h, rotation: r.rotation || 0, centerBased: !!r.centerBased };
    }

    /** Write a local-space pixel rect (center-based) back through the adapter, in fractions. */
    function writePixelRect(item, pr) {
      const size = canvasSize();
      const x = pr.centerBased ? pr.cx / size.width : (pr.cx - pr.w / 2) / size.width;
      const y = pr.centerBased ? pr.cy / size.height : (pr.cy - pr.h / 2) / size.height;
      adapter.setItemRect(item, {
        x,
        y,
        w: pr.w / size.width,
        h: pr.h / size.height,
        rotation: pr.rotation,
        centerBased: pr.centerBased,
      });
    }

    /** The 8 handle anchor points, in local space, for a given pixel rect. */
    function localHandles(pr) {
      return HANDLE_CODES.map((code) => {
        const sign = HANDLE_SIGNS[code];
        const local = rotate((sign.sx * pr.w) / 2, (sign.sy * pr.h) / 2, pr.rotation);
        return { code, lx: pr.cx + local.x, ly: pr.cy + local.y };
      });
    }

    /* -------------------------------------------------------------- hit-testing */

    /** Rotation-aware point-in-rect test: rotate the point into the item's own
     * unrotated local frame (centered on the item) before comparing to its half-extents. */
    function rectContains(pr, lx, ly) {
      const local = rotateInverse(lx - pr.cx, ly - pr.cy, pr.rotation);
      return Math.abs(local.x) <= pr.w / 2 && Math.abs(local.y) <= pr.h / 2;
    }

    function hitTestItem(lx, ly) {
      const items = options.getItems();
      for (let i = items.length - 1; i >= 0; i--) {
        const pr = pixelRectOf(items[i]);
        if (!pr) continue; // not selectable on canvas (e.g. frame elements)
        if (rectContains(pr, lx, ly)) return items[i];
      }
      return null;
    }

    function hitTestHandle(viewX, viewY) {
      if (!selectedItem) return null;
      const pr = pixelRectOf(selectedItem);
      if (!pr) return null;
      const half = HANDLE_SIZE / 2 + HANDLE_HIT_PAD;
      for (const handle of localHandles(pr)) {
        const v = localToView(handle.lx, handle.ly);
        if (Math.abs(v.x - viewX) <= half && Math.abs(v.y - viewY) <= half) return handle.code;
      }
      return null;
    }

    /* ------------------------------------------------------------------ snapping */

    /** Collect snap-target coordinates (local space) for one axis: canvas center,
     * canvas edges, and other items' centers/edges. `dim` is canvas width or height. */
    function snapCandidates(axis, dim, excludeItem) {
      const candidates = [0, dim / 2, dim];
      for (const item of options.getItems()) {
        if (item === excludeItem) continue;
        const pr = pixelRectOf(item);
        if (!pr) continue;
        const center = axis === "x" ? pr.cx : pr.cy;
        const size = axis === "x" ? pr.w : pr.h;
        candidates.push(center - size / 2, center, center + size / 2);
      }
      const size = canvasSize();
      for (const rect of extraSnapRects()) {
        if (axis === "x") {
          const left = rect.x * size.width;
          const width = rect.w * size.width;
          candidates.push(left, left + width / 2, left + width);
        } else {
          const top = rect.y * size.height;
          const height = rect.h * size.height;
          candidates.push(top, top + height / 2, top + height);
        }
      }
      return candidates;
    }

    /** Find the best snap among `offsets` (candidate positions relative to `center`,
     * e.g. [-halfSize, 0, halfSize] for left/center/right edges) against `candidates`.
     * Returns {delta, guide} (delta to apply to `center`) or null. */
    function bestSnap(center, offsets, candidates, threshold) {
      let best = null;
      for (const offset of offsets) {
        const pos = center + offset;
        for (const candidate of candidates) {
          const diff = candidate - pos;
          if (Math.abs(diff) <= threshold && (!best || Math.abs(diff) < Math.abs(best.delta))) {
            best = { delta: diff, guide: candidate };
          }
        }
      }
      return best;
    }

    /* ------------------------------------------------------------- render loop */

    function scheduleRender() {
      if (rafId !== null || destroyed) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        render();
      });
    }

    function requestDraw() {
      scheduleRender();
    }

    function render() {
      if (destroyed) return;
      const items = options.getItems();
      if (selectedItem && !items.includes(selectedItem)) selectedItem = null;

      const ctx = canvas.getContext("2d");
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      ctx.save();
      ctx.translate(viewport.offsetX, viewport.offsetY);
      ctx.scale(viewport.scale, viewport.scale);
      options.draw(ctx, viewport);
      ctx.restore();

      drawChrome(ctx);
    }

    function drawChrome(ctx) {
      if (activeGuideX !== null || activeGuideY !== null) {
        ctx.save();
        ctx.strokeStyle = ACCENT;
        ctx.lineWidth = 1;
        if (activeGuideX !== null) {
          const top = localToView(activeGuideX, 0);
          const bottom = localToView(activeGuideX, canvas.height);
          ctx.beginPath();
          ctx.moveTo(top.x, 0);
          ctx.lineTo(bottom.x, canvas.height);
          ctx.stroke();
        }
        if (activeGuideY !== null) {
          const left = localToView(0, activeGuideY);
          const right = localToView(canvas.width, activeGuideY);
          ctx.beginPath();
          ctx.moveTo(0, left.y);
          ctx.lineTo(canvas.width, right.y);
          ctx.stroke();
        }
        ctx.restore();
      }

      if (!selectedItem) return;
      const pr = pixelRectOf(selectedItem);
      if (!pr) return;

      const corners = CORNER_CODES.map((code) => {
        const sign = HANDLE_SIGNS[code];
        const local = rotate((sign.sx * pr.w) / 2, (sign.sy * pr.h) / 2, pr.rotation);
        return localToView(pr.cx + local.x, pr.cy + local.y);
      });

      ctx.save();
      ctx.strokeStyle = ACCENT;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      corners.forEach((c, i) => (i === 0 ? ctx.moveTo(c.x, c.y) : ctx.lineTo(c.x, c.y)));
      ctx.closePath();
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = "#fff";
      for (const handle of localHandles(pr)) {
        const v = localToView(handle.lx, handle.ly);
        ctx.fillRect(v.x - HANDLE_SIZE / 2, v.y - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
        ctx.strokeRect(v.x - HANDLE_SIZE / 2, v.y - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
      }
      ctx.restore();
    }

    /* ------------------------------------------------------------ move / resize */

    function beginMove(item, viewPoint) {
      const local = viewToLocal(viewPoint.x, viewPoint.y);
      drag = { mode: "move", item, startLocal: local, startRect: pixelRectOf(item) };
    }

    function beginResize(item, handleCode, viewPoint) {
      const startRect = pixelRectOf(item);
      const sign = HANDLE_SIGNS[handleCode];
      const anchorLocalOffset = rotate((-sign.sx * startRect.w) / 2, (-sign.sy * startRect.h) / 2, startRect.rotation);
      const anchor = { x: startRect.cx + anchorLocalOffset.x, y: startRect.cy + anchorLocalOffset.y };
      drag = { mode: "resize", item, handleCode, sign, startRect, anchor };
    }

    function updateMove(viewPoint, altKey) {
      const size = canvasSize();
      const local = viewToLocal(viewPoint.x, viewPoint.y);
      const dx = local.x - drag.startLocal.x;
      const dy = local.y - drag.startLocal.y;
      let cx = drag.startRect.cx + dx;
      let cy = drag.startRect.cy + dy;

      activeGuideX = null;
      activeGuideY = null;
      if (!altKey) {
        const threshold = SNAP_PX / viewport.scale;
        const xCandidates = snapCandidates("x", size.width, drag.item);
        const yCandidates = snapCandidates("y", size.height, drag.item);
        const offsetsX = [-drag.startRect.w / 2, 0, drag.startRect.w / 2];
        const offsetsY = [-drag.startRect.h / 2, 0, drag.startRect.h / 2];
        const snapX = bestSnap(cx, offsetsX, xCandidates, threshold);
        const snapY = bestSnap(cy, offsetsY, yCandidates, threshold);
        if (snapX) {
          cx += snapX.delta;
          activeGuideX = snapX.guide;
        }
        if (snapY) {
          cy += snapY.delta;
          activeGuideY = snapY.guide;
        }
      }

      cx = clampCenter(cx, drag.startRect.w, size.width);
      cy = clampCenter(cy, drag.startRect.h, size.height);

      writePixelRect(drag.item, { ...drag.startRect, cx, cy });
      onChange(drag.item);
      requestDraw();
    }

    function updateResize(viewPoint, shiftKey, altKey) {
      const size = canvasSize();
      const { startRect, sign, anchor } = drag;
      const minW = MIN_SIZE_FRACTION * size.width;
      const minH = MIN_SIZE_FRACTION * size.height;

      const local = viewToLocal(viewPoint.x, viewPoint.y);
      const vecWorld = { x: local.x - anchor.x, y: local.y - anchor.y };
      const vecLocal = rotateInverse(vecWorld.x, vecWorld.y, startRect.rotation);

      let newWidth = sign.sx !== 0 ? Math.max(minW, sign.sx * vecLocal.x) : startRect.w;
      let newHeight = sign.sy !== 0 ? Math.max(minH, sign.sy * vecLocal.y) : startRect.h;

      const locked = aspectLocked(drag.item) !== shiftKey; // shift toggles the lock live
      if (locked && sign.sx !== 0 && sign.sy !== 0) {
        const scaleW = newWidth / startRect.w;
        const scaleH = newHeight / startRect.h;
        const scale = Math.abs(scaleW - 1) >= Math.abs(scaleH - 1) ? scaleW : scaleH;
        newWidth = Math.max(minW, startRect.w * scale);
        newHeight = Math.max(minH, startRect.h * scale);
      }

      activeGuideX = null;
      activeGuideY = null;
      // Snapping the free edge to guides is only geometrically exact for
      // axis-aligned items (rotation ~ 0); skip it for rotated items rather
      // than snap to a visually-misleading position.
      if (!altKey && Math.abs(startRect.rotation) < 0.01) {
        const threshold = SNAP_PX / viewport.scale;
        if (sign.sx !== 0) {
          const freeX = anchor.x + sign.sx * newWidth;
          const snap = bestSnap(freeX, [0], snapCandidates("x", size.width, drag.item), threshold);
          if (snap) {
            newWidth = Math.max(minW, sign.sx * (freeX + snap.delta - anchor.x));
            activeGuideX = snap.guide;
          }
        }
        if (sign.sy !== 0) {
          const freeY = anchor.y + sign.sy * newHeight;
          const snap = bestSnap(freeY, [0], snapCandidates("y", size.height, drag.item), threshold);
          if (snap) {
            newHeight = Math.max(minH, sign.sy * (freeY + snap.delta - anchor.y));
            activeGuideY = snap.guide;
          }
        }
      }

      const centerOffset = rotate((sign.sx * newWidth) / 2, (sign.sy * newHeight) / 2, startRect.rotation);
      const cx = anchor.x + centerOffset.x;
      const cy = anchor.y + centerOffset.y;

      writePixelRect(drag.item, { cx, cy, w: newWidth, h: newHeight, rotation: startRect.rotation, centerBased: startRect.centerBased });
      onChange(drag.item);
      requestDraw();
    }

    /* ---------------------------------------------------------------- panning */

    function beginPan(viewPoint) {
      drag = { mode: "pan", start: viewPoint, startOffsetX: viewport.offsetX, startOffsetY: viewport.offsetY };
      canvas.style.cursor = "grabbing";
    }

    function updatePan(viewPoint) {
      viewport.offsetX = drag.startOffsetX + (viewPoint.x - drag.start.x);
      viewport.offsetY = drag.startOffsetY + (viewPoint.y - drag.start.y);
      requestDraw();
    }

    /* ----------------------------------------------------------- pointer events */

    function onMouseDown(event) {
      const viewPoint = clientToView(event.clientX, event.clientY);
      if (event.button === 1 || (event.button === 0 && spaceHeld)) {
        event.preventDefault();
        beginPan(viewPoint);
        attachDragListeners();
        return;
      }
      if (event.button !== 0) return;

      const handleCode = hitTestHandle(viewPoint.x, viewPoint.y);
      if (handleCode) {
        beginResize(selectedItem, handleCode, viewPoint);
        attachDragListeners();
        return;
      }

      const local = viewToLocal(viewPoint.x, viewPoint.y);
      const hit = hitTestItem(local.x, local.y);
      if (hit !== selectedItem) {
        selectedItem = hit;
        onSelect(selectedItem);
      }
      if (hit) {
        beginMove(hit, viewPoint);
        attachDragListeners();
      }
      requestDraw();
    }

    function onMouseMove(event) {
      if (!drag) return;
      const viewPoint = clientToView(event.clientX, event.clientY);
      if (drag.mode === "move") updateMove(viewPoint, event.altKey);
      else if (drag.mode === "resize") updateResize(viewPoint, event.shiftKey, event.altKey);
      else if (drag.mode === "pan") updatePan(viewPoint);
    }

    function endDrag() {
      if (drag && drag.mode === "pan") canvas.style.cursor = spaceHeld ? "grab" : "default";
      drag = null;
      activeGuideX = null;
      activeGuideY = null;
      detachDragListeners();
      requestDraw();
    }

    function attachDragListeners() {
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", endDrag);
    }

    function detachDragListeners() {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", endDrag);
    }

    function onWheel(event) {
      event.preventDefault();
      const viewPoint = clientToView(event.clientX, event.clientY);
      const before = viewToLocal(viewPoint.x, viewPoint.y);
      const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
      viewport.scale = clamp(viewport.scale * factor, MIN_ZOOM, MAX_ZOOM);
      // Re-anchor so the point under the cursor stays put after the zoom.
      viewport.offsetX = viewPoint.x - before.x * viewport.scale;
      viewport.offsetY = viewPoint.y - before.y * viewport.scale;
      requestDraw();
    }

    /* ---------------------------------------------------------------- keyboard */

    function isTypingTarget() {
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      return ["input", "textarea", "select"].includes(tag.toLowerCase());
    }

    function nudge(dx, dy) {
      const item = selectedItem;
      const r = adapter.itemRect(item);
      if (!r) return;
      let x = r.x + dx;
      let y = r.y + dy;
      const centerX = r.centerBased ? x : x + r.w / 2;
      const centerY = r.centerBased ? y : y + r.h / 2;
      const clampedCenterX = clampCenter(centerX, r.w, 1);
      const clampedCenterY = clampCenter(centerY, r.h, 1);
      x = r.centerBased ? clampedCenterX : clampedCenterX - r.w / 2;
      y = r.centerBased ? clampedCenterY : clampedCenterY - r.h / 2;
      adapter.setItemRect(item, { ...r, x, y });
      onChange(item);
      requestDraw();
    }

    function onKeyDown(event) {
      if (event.key === " ") {
        spaceHeld = true;
        if (!drag) canvas.style.cursor = "grab";
        return;
      }
      if (!selectedItem || isTypingTarget()) return;

      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        const item = selectedItem;
        selectedItem = null;
        onDelete(item);
        onSelect(null);
        requestDraw();
        return;
      }

      const step = event.shiftKey ? NUDGE_STEP_SHIFT : NUDGE_STEP;
      const deltas = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
      if (deltas[event.key]) {
        event.preventDefault();
        nudge(deltas[event.key][0], deltas[event.key][1]);
      }
    }

    function onKeyUp(event) {
      if (event.key === " ") {
        spaceHeld = false;
        if (!drag) canvas.style.cursor = "default";
      }
    }

    /* -------------------------------------------------------------------- init */

    canvas.addEventListener("mousedown", onMouseDown);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);

    const editor = {
      viewport,

      /** Request a redraw on the next animation frame (batched — safe to call often). */
      requestDraw,

      /** Set the current selection (e.g. from a list panel outside the canvas). */
      select(item) {
        selectedItem = item;
        onSelect(item);
        requestDraw();
      },

      /** Return the currently selected item, or null. */
      getSelected() {
        return selectedItem;
      },

      /** Set the zoom level (0.5–4), keeping the canvas center stationary. */
      setZoom(scale) {
        const center = { x: canvas.width / 2, y: canvas.height / 2 };
        const before = viewToLocal(center.x, center.y);
        viewport.scale = clamp(scale, MIN_ZOOM, MAX_ZOOM);
        viewport.offsetX = center.x - before.x * viewport.scale;
        viewport.offsetY = center.y - before.y * viewport.scale;
        requestDraw();
      },

      /** Reset zoom/pan to the identity transform. */
      resetView() {
        viewport.scale = 1;
        viewport.offsetX = 0;
        viewport.offsetY = 0;
        requestDraw();
      },

      /** Remove all listeners and stop the render loop. Call before dropping
       * or replacing this editor instance (e.g. when a page re-renders). */
      destroy() {
        destroyed = true;
        if (rafId !== null) {
          cancelAnimationFrame(rafId);
          rafId = null;
        }
        detachDragListeners();
        canvas.removeEventListener("mousedown", onMouseDown);
        canvas.removeEventListener("wheel", onWheel);
        document.removeEventListener("keydown", onKeyDown);
        document.removeEventListener("keyup", onKeyUp);
      },
    };

    requestDraw();
    return editor;
  }

  return { create };
})();
