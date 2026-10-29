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
    this._areaJob = { kind: "both", suction: "standard", mop: "medium", times: 1 };
    this._homeJob = { times: 1 };
    this._zones = { nogo: [], nomop: [] };
    this._dirty = false;
    this._selected = null;
    this._drag = null;
    this._stationOpen = false;
    this._scenesOpen = false;
    this._advancedOpen = false;
    this._editingZones = false;
    this._levelKey = "";
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
        button, select { font: inherit; color: var(--primary-text-color, #111); background: transparent; border: 0; }
        .map { position: relative; background: #111; }
        .map img.mapshot { display: block; width: 100%; height: auto; }
        svg { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; }
        .badges { position: absolute; inset: 0; pointer-events: none; }
        .badge {
          position: absolute; transform: translate(-50%, -50%);
          width: 26px; height: 26px; border-radius: 50%;
          background: var(--primary-color, #03a9f4); color: #fff; font-weight: 700;
          display: grid; place-items: center;
        }
        .clean { display: flex; align-items: center; gap: 8px; padding: 10px 12px 0; min-width: 0; }
        .clean .label { color: var(--secondary-text-color, #6b6b6b); font-size: 13px; }
        .modes { display: flex; gap: 2px; padding: 2px; background: var(--secondary-background-color, #f2f2f2); border-radius: 10px; flex: 1; min-width: 0; }
        .modes button { flex: 1; min-width: 0; padding: 7px 0; border-radius: 8px; white-space: nowrap; }
        .modes button.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); font-weight: 650; }
        .station { width: 36px; height: 36px; border-radius: 10px; background: #1c1c1c; display: grid; place-items: center; }
        .station img { width: 22px; height: 22px; object-fit: contain; display: block; }
        .station.on { background: var(--primary-color, #03a9f4); }
        .panel { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 12px 12px; }
        #station-panel { padding-top: 12px; }
        .panel[hidden], .advanced-body[hidden] { display: none; }
        .panel button, .edit { background: var(--secondary-background-color, #f2f2f2); border-radius: 999px; padding: 8px 12px; }
        .edit.on, .panel button.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
        .body { padding: 10px 12px 8px; }
        .levels { display: flex; flex-direction: column; gap: 10px; }
        .levels:empty { display: none; }
        .level { display: flex; flex-direction: column; gap: 8px; }
        .level + .level { padding-top: 8px; border-top: 1px solid var(--divider-color, #ececec); }
        .line { display: flex; align-items: center; gap: 8px; }
        .line b { flex: 1 1 auto; min-width: 0; }
        .line.tail { justify-content: flex-end; }
        label { display: flex; flex-direction: row; align-items: center; gap: 6px; font-size: 11px; color: var(--secondary-text-color, #6b6b6b); white-space: nowrap; }
        select { background: var(--secondary-background-color, #f2f2f2); border-radius: 8px; padding: 6px 8px; }
        .picks { display: flex; gap: 4px; }
        .picks button { width: 32px; height: 32px; min-width: 32px; border-radius: 8px; background: var(--secondary-background-color, #f2f2f2); display: grid; place-items: center; overflow: hidden; }
        .picks button.text { width: auto; min-width: 44px; padding: 0 10px; font-size: 13px; }
        .picks img { width: 20px; height: 20px; min-width: 20px; max-width: 20px; object-fit: contain; display: block; }
        .picks button.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
        .picks button.on img { filter: brightness(0) invert(1); }
        .start { width: 100%; margin-top: 10px; padding: 12px; border-radius: 12px; background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); font-weight: 700; }
        .row { width: 100%; display: flex; justify-content: space-between; padding: 12px; border-top: 1px solid var(--divider-color, #ececec); }
        .advanced { display: flex; justify-content: flex-end; padding: 4px 12px 10px; }
        .advanced > button, .sub { color: var(--secondary-text-color, #6b6b6b); font-size: 13px; }
        .advanced-body { padding: 0 12px 12px; }
        .sub { margin: 8px 0 4px; }
        .pad { display: flex; flex-direction: column; align-items: center; gap: 8px; margin-top: 8px; }
        .pad .sides { display: flex; gap: 72px; }
        .pad button { width: 52px; height: 52px; border-radius: 50%; background: var(--secondary-background-color, #f2f2f2); font-size: 18px; }
        .note { min-height: 1.2em; padding: 0 12px; color: var(--secondary-text-color, #6b6b6b); }
      </style>
      <div class="map">
        <img class="mapshot" alt="Map">
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg>
        <div class="badges"></div>
      </div>
      <div class="clean">
        <span class="label">Clean</span>
        <div class="modes"></div>
        <button class="station" type="button" title="Station" aria-label="Station">
          <img src="/eufy_clean/station.webp" alt="">
        </button>
      </div>
      <div class="panel" id="station-panel" hidden></div>
      <div class="body">
        <div class="levels"></div>
        <button class="start" type="button">Start</button>
        <div class="note"></div>
      </div>
      <button class="row" type="button"><span>Scenarios</span><span class="chev">▸</span></button>
      <div class="panel scenes" hidden></div>
      <div class="advanced"><button type="button">Advanced ▸</button></div>
      <div class="advanced-body" hidden>
        <button class="edit" type="button">Edit zones</button>
        <div class="panel zone-tools" hidden></div>
        <div class="sub">Drive</div>
        <div class="pad"></div>
      </div>
    `;
    this.appendChild(root);
    this._img = root.querySelector(".mapshot");
    this._svg = root.querySelector("svg");
    this._badges = root.querySelector(".badges");
    this._modes = root.querySelector(".modes");
    this._station = root.querySelector(".station");
    this._stationPanel = root.querySelector("#station-panel");
    this._levels = root.querySelector(".levels");
    this._start = root.querySelector(".start");
    this._note = root.querySelector(".note");
    this._scenesRow = root.querySelector(".row");
    this._scenesChevron = root.querySelector(".chev");
    this._scenesPanel = root.querySelector(".scenes");
    this._advanced = root.querySelector(".advanced button");
    this._advancedBody = root.querySelector(".advanced-body");
    this._editZones = root.querySelector(".edit");
    this._zoneTools = root.querySelector(".zone-tools");
    this._pad = root.querySelector(".pad");
    for (const [id, label] of [["home", "Home"], ["rooms", "Rooms"], ["area", "Area"]]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.dataset.mode = id;
      button.addEventListener("click", () => {
        this._mode = id;
        this._message("");
        this._paint();
      });
      this._modes.append(button);
    }
    this._station.addEventListener("click", () => {
      this._stationOpen = !this._stationOpen;
      this._paint();
    });
    for (const [action, label] of [["wash", "Wash mop"], ["dry", "Dry mop"], ["empty", "Empty dust"], ["stop", "Stop"]]) {
      this._stationPanel.append(this._chip(label, () => this._stationCmd(action)));
    }
    this._scenesRow.addEventListener("click", () => {
      this._scenesOpen = !this._scenesOpen;
      this._paint();
    });
    this._advanced.addEventListener("click", () => {
      this._advancedOpen = !this._advancedOpen;
      if (!this._advancedOpen) {
        this._editingZones = false;
        this._remote("brake", true);
      }
      this._paint();
    });
    this._editZones.addEventListener("click", () => {
      this._editingZones = !this._editingZones;
      this._paint();
    });
    for (const [id, label] of [["nogo", "No-go"], ["nomop", "No-mop"]]) {
      const button = this._chip(label, () => {
        this._zoneKind = id;
        this._paint();
      });
      button.dataset.zone = id;
      this._zoneTools.append(button);
    }
    this._zoneTools.append(this._chip("Delete", () => this._deleteZone()));
    this._zoneTools.append(this._chip("Save", () => this._saveZones()));
    const sides = document.createElement("div");
    sides.className = "sides";
    sides.append(this._hold("←", "left"), this._hold("→", "right"));
    this._pad.append(this._hold("↑", "forward"), sides, this._hold("↓", "back"));
    this._start.addEventListener("click", () => this._startClean());
    this._svg.addEventListener("pointerdown", (event) => this._down(event));
    this._svg.addEventListener("pointermove", (event) => this._move(event));
    this._svg.addEventListener("pointerup", (event) => this._up(event));
    this._svg.addEventListener("pointercancel", (event) => this._up(event));
    this._timer = setInterval(() => this._refreshImage(), 1000);
  }

  connectedCallback() {
    if (this._built && !this._timer) {
      this._timer = setInterval(() => this._refreshImage(), 1000);
      this._refreshImage();
    }
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  _chip(label, onClick) {
    const button = document.createElement("button");
    button.textContent = label;
    button.addEventListener("click", onClick);
    return button;
  }

  _sync() {
    const camera = this._hass.states[this._config.camera];
    if (!camera) {
      this._message("Map camera is missing");
      return;
    }
    if (!this._img.getAttribute("src")) {
      this._refreshImage();
    }
    if (!this._dirty) {
      const boxes = camera.attributes.zone_boxes || { nogo: [], nomop: [] };
      this._zones = {
        nogo: (boxes.nogo || []).map((box) => [box.x1, box.y1, box.x2, box.y2]),
        nomop: (boxes.nomop || []).map((box) => [box.x1, box.y1, box.x2, box.y2]),
      };
    }
    this._syncScenes(camera);
    this._paint();
  }

  _paint() {
    this._modes.querySelectorAll("button").forEach((button) => {
      button.className = button.dataset.mode === this._mode ? "on" : "";
    });
    this._station.className = "station" + (this._stationOpen ? " on" : "");
    this._stationPanel.hidden = !this._stationOpen;
    this._scenesChevron.textContent = this._scenesOpen ? "▾" : "▸";
    this._scenesPanel.hidden = !this._scenesOpen;
    this._advanced.textContent = "Advanced " + (this._advancedOpen ? "▾" : "▸");
    this._advancedBody.hidden = !this._advancedOpen;
    this._editZones.className = "edit" + (this._editingZones ? " on" : "");
    this._zoneTools.hidden = !this._editingZones;
    this._zoneTools.querySelectorAll("[data-zone]").forEach((button) => {
      button.className = button.dataset.zone === this._zoneKind ? "on" : "";
    });
    const needsArea = this._mode === "area" && !this._area;
    const needsRooms = this._mode === "rooms" && this._rooms.length === 0;
    this._start.disabled = needsArea || needsRooms;
    this._start.textContent = "Start";
    const levelKey = this._mode + ":" + this._rooms.map((room) => room.id).join(",") + ":" + (this._area ? "1" : "0");
    if (levelKey !== this._levelKey) {
      this._levelKey = levelKey;
      this._drawLevels();
    }
    this._drawOverlay();
  }

  _refreshImage() {
    if (!this._hass || !this._img || this._loading) {
      return;
    }
    const camera = this._hass.states[this._config.camera];
    const picture = camera && camera.attributes.entity_picture;
    if (!picture) {
      return;
    }
    const url = picture + (picture.includes("?") ? "&" : "?") + "t=" + Date.now();
    this._loading = true;
    const preload = new Image();
    preload.onload = () => {
      this._loading = false;
      if (this._img) {
        this._img.src = preload.src;
      }
    };
    preload.onerror = () => {
      this._loading = false;
    };
    preload.src = url;
  }

  _message(text) {
    if (this._note) {
      this._note.textContent = text;
    }
  }

  _picks(options, value, onChange) {
    const wrap = document.createElement("div");
    wrap.className = "picks";
    for (const [key, file, label] of options) {
      const button = document.createElement("button");
      button.type = "button";
      button.title = label;
      button.setAttribute("aria-label", label);
      button.className = key === value ? "on" : "";
      if (file) {
        const img = document.createElement("img");
        img.src = "/eufy_clean/icons/" + file;
        img.alt = "";
        button.append(img);
      } else {
        button.textContent = label;
        button.classList.add("text");
      }
      button.addEventListener("click", () => {
        onChange(key);
        wrap.querySelectorAll("button").forEach((item) => {
          item.classList.toggle("on", item === button);
        });
      });
      wrap.append(button);
    }
    return wrap;
  }

  _toggle(on, onChange) {
    return this._picks(
      [["0", "", "Off"], ["1", "", "On"]],
      on ? "1" : "0",
      (value) => onChange(value === "1")
    );
  }

  _labeled(title, control) {
    const label = document.createElement("label");
    label.append(title, control);
    return label;
  }

  _drawLevels() {
    this._levels.replaceChildren();
    const jobs = this._mode === "home"
      ? [this._homeJob]
      : this._mode === "rooms"
        ? this._rooms
        : this._mode === "area" && this._area
          ? [this._areaJob]
          : [];
    const times = (job) => this._labeled("Passes", this._picks(
      [["1", "times1.webp", "1 pass"], ["2", "times2.webp", "2 passes"]],
      String(job.times || 1),
      (value) => { job.times = Number(value); }
    ));
    jobs.forEach((job, index) => {
      if (!job.kind && this._mode !== "home") {
        job.kind = "both";
      }
      if (!job.times) {
        job.times = 1;
      }
      const row = document.createElement("div");
      row.className = "level";
      const name = document.createElement("b");
      name.textContent = this._mode === "rooms"
        ? (index + 1) + " " + (job.name || "Room " + job.id)
        : this._mode === "area"
          ? "Area"
          : "Home";
      const head = document.createElement("div");
      head.className = "line";
      head.append(name, times(job));
      if (this._mode !== "home") {
        head.append(this._labeled("Clean", this._picks(
          [
            ["vacuum", "vacuum.webp", "Vacuum"],
            ["mop", "mop.webp", "Mop"],
            ["both", "both.webp", "Vacuum and mop"],
          ],
          job.kind,
          (value) => {
            job.kind = value;
            this._levelKey = "";
            this._drawLevels();
          }
        )));
      }
      if (this._mode === "home") {
        head.append(this._labeled("Edge", this._toggle(
          !!job.edge,
          (on) => { job.edge = on; }
        )));
      }
      row.append(head);
      const tail = document.createElement("div");
      tail.className = "line tail";
      if (this._mode !== "home" && job.kind !== "mop") {
        tail.append(this._labeled("Suction", this._picks(
          [
            ["quiet", "suction-quiet.webp", "Quiet"],
            ["standard", "suction-standard.webp", "Standard"],
            ["turbo", "suction-turbo.webp", "Turbo"],
            ["max", "suction-max.webp", "Max"],
          ],
          job.suction || "standard",
          (value) => { job.suction = value; }
        )));
      }
      if (this._mode !== "home" && job.kind !== "vacuum") {
        tail.append(this._labeled("Mop", this._picks(
          [
            ["low", "mop-low.webp", "Low"],
            ["medium", "mop-medium.webp", "Medium"],
            ["high", "mop-high.webp", "High"],
          ],
          job.mop || "medium",
          (value) => { job.mop = value; }
        )));
      }
      if (tail.childElementCount) {
        row.append(tail);
      }
      this._levels.append(row);
    });
  }

  _point(event) {
    const box = this._svg.getBoundingClientRect();
    return [
      Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)),
      Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)),
    ];
  }

  _down(event) {
    if (!this._editingZones && this._mode !== "area" && this._mode !== "rooms") {
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
    } else if (this._editingZones) {
      this._zones[this._zoneKind].push([start[0], start[1], end[0], end[1]]);
      this._dirty = true;
      this._selected = { kind: this._zoneKind, i: this._zones[this._zoneKind].length - 1 };
      this._message("Drawn. Save to send it.");
    } else if (this._mode === "area") {
      this._area = [start[0], start[1], end[0], end[1]];
      this._levelKey = "";
      this._message("Area ready");
    }
    this._paint();
  }

  async _tap(point) {
    if (this._editingZones) {
      this._selected = this._hitZone(point);
      this._message(this._selected ? "Zone selected" : "");
      this._paint();
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
      this._rooms.push({
        id: roomId,
        name: body.room_name || "",
        x: point[0],
        y: point[1],
        kind: "both",
        suction: "standard",
        mop: "medium",
        times: 1,
      });
    }
    this._levelKey = "";
    this._paint();
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
    this._paint();
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
    this._dirty = false;
    this._message("Saved");
  }

  async _startClean() {
    if (this._mode === "rooms") {
      await this._hass.callService("eufy_clean", "clean_rooms", {
        entity_id: this._config.vacuum,
        room_ids: this._rooms.map((room) => room.id),
        suction: this._rooms.map((room) => room.suction || "standard"),
        mop: this._rooms.map((room) => room.mop || "medium"),
        kind: this._rooms.map((room) => room.kind || "both"),
        clean_times: this._rooms.map((room) => Number(room.times || 1)),
      });
      this._message("Rooms started");
      return;
    }
    if (this._mode === "area" && this._area) {
      const [x1, y1, x2, y2] = this._area;
      await this._hass.callService("eufy_clean", "clean_area", {
        entity_id: this._config.vacuum,
        x1: Math.min(x1, x2),
        y1: Math.min(y1, y2),
        x2: Math.max(x1, x2),
        y2: Math.max(y1, y2),
        suction: this._areaJob.suction,
        mop: this._areaJob.mop,
        kind: this._areaJob.kind,
        clean_times: Number(this._areaJob.times || 1),
      });
      this._message("Area started");
      return;
    }
    await this._hass.callService("eufy_clean", "start_clean", {
      entity_id: this._config.vacuum,
      clean_times: Number(this._homeJob.times || 1),
      edge_mop: !!this._homeJob.edge,
    });
    this._message("Whole home started");
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
    if (this._editingZones) {
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
    if (this._drag && (this._editingZones || this._mode === "area")) {
      const color = this._editingZones ? (this._zoneKind === "nomop" ? "#ffa500" : "#dc3232") : "#03a9f4";
      add([this._drag.start[0], this._drag.start[1], this._drag.current[0], this._drag.current[1]], color, false);
    }
    this._svg.innerHTML = parts.join("");
    this._drawRoomBadges();
  }

  _drawRoomBadges() {
    if (this._mode !== "rooms") {
      this._badges.replaceChildren();
      return;
    }
    this._badges.replaceChildren(
      ...this._rooms.map((room, index) => {
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.textContent = String(index + 1);
        badge.style.left = room.x * 100 + "%";
        badge.style.top = room.y * 100 + "%";
        return badge;
      })
    );
  }

  _syncScenes(camera) {
    const scenes = camera.attributes.scenes || [];
    const key = scenes.map((scene) => scene.scene_id + ":" + scene.name).join("|");
    if (key === this._sceneKey) {
      return;
    }
    this._sceneKey = key;
    if (!scenes.length) {
      const empty = document.createElement("span");
      empty.textContent = "No scenarios";
      this._scenesPanel.replaceChildren(empty);
      return;
    }
    this._scenesPanel.replaceChildren(
      ...scenes.map((scene) => this._chip(scene.name, () => this._hass.callService("eufy_clean", "start_scene", {
        entity_id: this._config.vacuum,
        scene_id: scene.scene_id,
      })))
    );
  }

  _hold(label, direction) {
    const button = document.createElement("button");
    button.textContent = label;
    let timer = null;
    const send = (start) => this._remote(direction, start);
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      button.setPointerCapture(event.pointerId);
      send(true);
      timer = setInterval(() => send(false), 200);
    });
    const stop = () => {
      if (!timer) {
        return;
      }
      clearInterval(timer);
      timer = null;
      this._remote("brake", false);
    };
    button.addEventListener("pointerup", stop);
    button.addEventListener("pointercancel", stop);
    return button;
  }

  _stationCmd(action) {
    return this._hass.callService("eufy_clean", "station", {
      entity_id: this._config.vacuum,
      action: action,
    });
  }

  _remote(direction, start) {
    const data = {
      entity_id: this._config.vacuum,
      direction: direction,
    };
    if (start) {
      data.start = true;
    }
    return this._hass.callService("eufy_clean", "remote_move", data);
  }
}

customElements.define("eufy-clean-map-card", EufyCleanMapCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "eufy-clean-map-card",
  name: "Eufy Clean Map",
  description: "Clean the whole home, tap rooms, draw an area, or edit no-go and no-mop zones.",
});
