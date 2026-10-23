class EufyCleanMapCard extends HTMLElement {
  setConfig(config) {
    if (!config.camera || !config.vacuum) {
      throw new Error("Set camera and vacuum entity ids");
    }
    this._config = config;
    this._mode = "home";
    this._zoneKind = "nogo";
    this._rooms = [];
    this._area = null;
    this._zones = { nogo: [], nomop: [] };
    this._dirty = false;
    this._selected = null;
    this._drag = null;
    this._note = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
    }
    this._sync();
  }

  getCardSize() {
    return 8;
  }

  _build() {
    this._built = true;
    const root = document.createElement("ha-card");
    root.innerHTML = `
      <style>
        .modes, .actions, .kinds { display: flex; gap: 6px; flex-wrap: wrap; padding: 8px 12px 0; }
        button { border: 0; border-radius: 8px; padding: 6px 10px; background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #111); }
        button.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
        .map { position: relative; margin: 8px 12px; }
        img { display: block; width: 100%; height: auto; }
        svg { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; }
        .note { padding: 8px 12px; min-height: 1.2em; }
        .actions { padding-bottom: 12px; }
        .chip { margin-right: 6px; }
      </style>
      <div class="modes"></div>
      <div class="kinds"></div>
      <div class="map"><img alt="Map"><svg viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg></div>
      <div class="note"></div>
      <div class="actions"></div>
    `;
    this.appendChild(root);
    this._modes = root.querySelector(".modes");
    this._kinds = root.querySelector(".kinds");
    this._img = root.querySelector("img");
    this._svg = root.querySelector("svg");
    this._note = root.querySelector(".note");
    this._actions = root.querySelector(".actions");
    this._svg.addEventListener("pointerdown", (event) => this._down(event));
    this._svg.addEventListener("pointermove", (event) => this._move(event));
    this._svg.addEventListener("pointerup", (event) => this._up(event));
    this._svg.addEventListener("pointercancel", (event) => this._up(event));
  }

  _sync() {
    const camera = this._hass.states[this._config.camera];
    if (!camera) {
      this._note.textContent = "Map camera is missing";
      return;
    }
    const picture = camera.attributes.entity_picture;
    if (picture && this._img.getAttribute("src") !== picture) {
      this._img.setAttribute("src", picture);
    }
    if (!this._dirty) {
      const boxes = camera.attributes.zone_boxes || { nogo: [], nomop: [] };
      this._zones = {
        nogo: (boxes.nogo || []).map((box) => [box.x1, box.y1, box.x2, box.y2]),
        nomop: (boxes.nomop || []).map((box) => [box.x1, box.y1, box.x2, box.y2]),
      };
    }
    this._drawChrome();
    this._drawOverlay();
  }

  _drawChrome() {
    const modes = [
      ["home", "Whole home"],
      ["rooms", "Rooms"],
      ["area", "Area"],
      ["zones", "Zones"],
    ];
    this._modes.replaceChildren(
      ...modes.map(([id, label]) => {
        const button = document.createElement("button");
        button.textContent = label;
        button.className = this._mode === id ? "on" : "";
        button.addEventListener("click", () => {
          if (this._mode === "zones" && id !== "zones") {
            this._dirty = false;
          }
          this._mode = id;
          this._selected = null;
          this._message("");
          this._drawChrome();
          this._drawOverlay();
        });
        return button;
      })
    );
    this._kinds.replaceChildren();
    if (this._mode === "zones") {
      const kinds = [];
      for (const [id, label] of [["nogo", "No-go"], ["nomop", "No-mop"]]) {
        const button = document.createElement("button");
        button.textContent = label;
        button.className = this._zoneKind === id ? "on" : "";
        button.addEventListener("click", () => {
          this._zoneKind = id;
          this._selected = null;
          this._drawChrome();
          this._drawOverlay();
        });
        kinds.push(button);
      }
      this._kinds.replaceChildren(...kinds);
    }
    const actions = [];
    if (this._mode === "zones") {
      actions.push(["Delete", () => this._deleteZone(), !this._selected]);
      actions.push(["Save", () => this._saveZones(), false]);
    } else if (this._mode === "rooms") {
      actions.push(["Start", () => this._startRooms(), this._rooms.length === 0]);
    } else if (this._mode === "area") {
      actions.push(["Start", () => this._startArea(), !this._area]);
    } else {
      actions.push(["Start", () => this._startHome(), false]);
    }
    this._actions.replaceChildren(
      ...actions.map(([label, fn, disabled]) => {
        const button = document.createElement("button");
        button.textContent = label;
        button.disabled = disabled;
        button.addEventListener("click", fn);
        return button;
      })
    );
    if (this._mode === "rooms") {
      const chips = this._rooms.map((room) => `${room.name || "Room " + room.id}`).join(", ");
      if (chips) {
        this._note.textContent = chips;
      }
    }
  }

  _message(text) {
    this._note.textContent = text;
  }

  _point(event) {
    const box = this._svg.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const y = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
    return [x, y];
  }

  _down(event) {
    if (this._mode !== "area" && this._mode !== "zones" && this._mode !== "rooms") {
      return;
    }
    this._drag = { start: this._point(event), current: this._point(event) };
    this._svg.setPointerCapture(event.pointerId);
  }

  _move(event) {
    if (!this._drag) {
      return;
    }
    this._drag.current = this._point(event);
    this._drawOverlay();
  }

  async _up(event) {
    if (!this._drag) {
      return;
    }
    const start = this._drag.start;
    const end = this._point(event);
    this._drag = null;
    const moved = Math.hypot(end[0] - start[0], end[1] - start[1]) > 0.02;
    if (!moved) {
      await this._tap(end);
    } else if (this._mode === "area") {
      this._area = [start[0], start[1], end[0], end[1]];
      this._message("Area ready");
    } else if (this._mode === "zones") {
      this._zones[this._zoneKind].push([start[0], start[1], end[0], end[1]]);
      this._dirty = true;
      this._selected = { kind: this._zoneKind, i: this._zones[this._zoneKind].length - 1 };
      this._message("Drawn. Save to send it.");
    }
    this._drawChrome();
    this._drawOverlay();
  }

  async _tap(point) {
    if (this._mode === "zones") {
      this._selected = this._hitZone(point);
      this._message(this._selected ? "Zone selected" : "");
      return;
    }
    if (this._mode !== "rooms") {
      return;
    }
    const result = await this._hass.callService(
      "eufy_clean",
      "map_point",
      { entity_id: this._config.camera, x: point[0], y: point[1] },
      undefined,
      true,
      true
    );
    const body = (result && result.response) || result || {};
    const roomId = Number(body.room_id || 0);
    if (!roomId) {
      this._message("No room there");
      return;
    }
    const existing = this._rooms.findIndex((room) => room.id === roomId);
    if (existing >= 0) {
      this._rooms.splice(existing, 1);
    } else {
      this._rooms.push({ id: roomId, name: body.room_name || "" });
    }
    this._drawChrome();
  }

  _hitZone(point) {
    let best = null;
    for (const kind of ["nogo", "nomop"]) {
      this._zones[kind].forEach((rect, index) => {
        const x1 = Math.min(rect[0], rect[2]);
        const x2 = Math.max(rect[0], rect[2]);
        const y1 = Math.min(rect[1], rect[3]);
        const y2 = Math.max(rect[1], rect[3]);
        if (point[0] < x1 || point[0] > x2 || point[1] < y1 || point[1] > y2) {
          return;
        }
        const area = (x2 - x1) * (y2 - y1);
        if (!best || area < best.area) {
          best = { kind, i: index, area };
        }
      });
    }
    return best ? { kind: best.kind, i: best.i } : null;
  }

  _deleteZone() {
    if (!this._selected) {
      return;
    }
    this._zones[this._selected.kind].splice(this._selected.i, 1);
    this._selected = null;
    this._dirty = true;
    this._message("Removed. Save to send it.");
    this._drawChrome();
    this._drawOverlay();
  }

  _rects(kind) {
    return this._zones[kind].map((rect) => [
      Math.min(rect[0], rect[2]),
      Math.min(rect[1], rect[3]),
      Math.max(rect[0], rect[2]),
      Math.max(rect[1], rect[3]),
    ]);
  }

  async _saveZones() {
    await this._hass.callService("eufy_clean", "set_zones", {
      entity_id: this._config.vacuum,
      nogo: this._rects("nogo"),
      nomop: this._rects("nomop"),
    });
    this._selected = null;
    this._message("Zones sent");
  }

  async _startHome() {
    await this._hass.callService("vacuum", "start", { entity_id: this._config.vacuum });
    this._message("Whole home started");
  }

  async _startRooms() {
    await this._hass.callService("eufy_clean", "clean_rooms", {
      entity_id: this._config.vacuum,
      room_ids: this._rooms.map((room) => room.id),
    });
    this._message("Rooms started");
  }

  async _startArea() {
    if (!this._area) {
      return;
    }
    const [x1, y1, x2, y2] = this._area;
    await this._hass.callService("eufy_clean", "clean_area", {
      entity_id: this._config.vacuum,
      x1: Math.min(x1, x2),
      y1: Math.min(y1, y2),
      x2: Math.max(x1, x2),
      y2: Math.max(y1, y2),
    });
    this._message("Area started");
  }

  _drawOverlay() {
    const parts = [];
    const add = (rect, color, selected) => {
      const x = Math.min(rect[0], rect[2]) * 1000;
      const y = Math.min(rect[1], rect[3]) * 1000;
      const w = Math.abs(rect[2] - rect[0]) * 1000;
      const h = Math.abs(rect[3] - rect[1]) * 1000;
      parts.push(
        `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${color}" fill-opacity="0.25" stroke="${color}" stroke-width="${selected ? 8 : 3}"></rect>`
      );
    };
    if (this._mode === "zones") {
      this._zones.nogo.forEach((rect, index) => {
        const on = this._selected && this._selected.kind === "nogo" && this._selected.i === index;
        add(rect, "#dc3232", on);
      });
      this._zones.nomop.forEach((rect, index) => {
        const on = this._selected && this._selected.kind === "nomop" && this._selected.i === index;
        add(rect, "#ffa500", on);
      });
    }
    if (this._mode === "area" && this._area) {
      add(this._area, "#03a9f4", true);
    }
    if (this._drag && (this._mode === "area" || this._mode === "zones")) {
      add([this._drag.start[0], this._drag.start[1], this._drag.current[0], this._drag.current[1]], "#03a9f4", false);
    }
    this._svg.innerHTML = parts.join("");
  }
}

customElements.define("eufy-clean-map-card", EufyCleanMapCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "eufy-clean-map-card",
  name: "Eufy Clean Map",
  description: "Clean the whole home, tap rooms, draw an area, or edit no-go and no-mop zones.",
});
