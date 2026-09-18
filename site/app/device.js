/* ==================================================================== *
 * Kairo — the data layer
 *
 * Two sources, one shape:
 *
 *   Sim     a full simulation of the box, so the site is presentable with
 *           nothing plugged in
 *   Bridge  polls site/bridge.py, which is holding the USB serial port open
 *
 * Both produce the same state object, so no view above this file has to
 * care which one is live. Exposed as the global `Kairo`.
 * ==================================================================== */
"use strict";

const Kairo = (() => {
  const { clamp, store, hhmmToSec, lcdRender, beep, toast, Notify, Hist } = K;

  const App = {
    source: store.get("source", "demo"),
    base:   store.get("base", "http://localhost:9000"),
    S:      null,     // the normalised state
    at:     0,        // performance.now() when it arrived
    primed: false,    // has the first frame been absorbed silently?
    seen:   new Set(),
    role:   "carer",
    everConnected: false,  // has a real board ever answered?
    startedAt: 0,
    onState: [],      // called with every new state
  };

  /* ================================================================== *
   * SIMULATION
   * A believable box: a patient-shaped schedule, one tube already low,
   * and a fortnight of history so the adherence panel means something.
   * It runs off the real wall clock, so the countdown genuinely counts.
   * ================================================================== */
  const Sim = {
    cfg: { patient: "Margaret Hale", low_at: 10, remind_every: 180,
           remind_limit: 3, missed_after: 900, volume: .35,
           chime: "jingle", catch_up: 3600 },
    tubes: [
      { label: "Metformin 500mg",   dose: 1, times: ["08:00", "20:00"], count: 42 },
      { label: "Ramipril 5mg",      dose: 1, times: ["08:00"],          count: 18 },
      { label: "Atorvastatin 20mg", dose: 1, times: ["20:00"],          count: 6  },
    ],
    chimes: [{ name: "jingle", kind: "wav", ok: true },
             { name: "gentle", kind: "tune", ok: true },
             { name: "chirp",  kind: "tune", ok: true },
             { name: "bells",  kind: "tune", ok: true },
             { name: "urgent", kind: "tune", ok: true }],
    mode: "idle", active: null, dueAt: 0, reminders: 0, takenUntil: 0,
    emptyAt: 0, fired: new Set(), events: [], history: null,

    boot() {
      this.history = this.fakeHistory();
      // A couple of plausible things that already happened today.
      const now = new Date(), nowSec = now.getHours() * 3600 + now.getMinutes() * 60;
      this.tubes.forEach((t, i) => t.times.forEach(hhmm => {
        const sec = hhmmToSec(hhmm);
        if (sec != null && sec < nowSec) {
          this.fired.add(i + "|" + hhmm);
          this.push("dispensed", i, { why: "scheduled " + hhmm, left: t.count, dose: t.dose }, hhmm);
          this.push("taken", i, { why: "button" }, hhmm);
        }
      }));
      if (this.tubes[2].count <= this.cfg.low_at)
        this.push("low", 2, { left: this.tubes[2].count });
    },

    /* Deliberately imperfect — a flat 100% looks fabricated. */
    fakeHistory() {
      const out = [], missOn = { 3: 1, 9: 1, 11: 1 };
      for (let back = 13; back >= 0; back--) {
        const due = 4, missed = missOn[back] || 0;
        out.push({ back, due, taken: due - missed, missed });
      }
      return out;
    },

    push(kind, tube, extra, hhmm) {
      const d = new Date();
      if (hhmm) { const [h, m] = hhmm.split(":"); d.setHours(+h, +m, 0, 0); }
      const ev = { k: kind, at: d.toISOString().slice(0, 19), ms: Date.now() };
      if (tube != null) { ev.i = tube; ev.label = this.tubes[tube].label; }
      Object.assign(ev, extra || {});
      this.events.push(ev);
      if (this.events.length > 40) this.events.shift();
    },

    nextDose() {
      const n = new Date();
      const now = n.getHours() * 3600 + n.getMinutes() * 60 + n.getSeconds();
      let best = null;
      this.tubes.forEach((t, i) => t.times.forEach(hhmm => {
        const target = hhmmToSec(hhmm); if (target == null) return;
        let d = target - now; if (d <= 0) d += 86400;
        if (!best || d < best.in) best = { tube: i, at: hhmm, in: d };
      }));
      return best || { tube: null, at: null, in: null };
    },

    lowTube() {
      let worst = null;
      this.tubes.forEach((t, i) => {
        if (t.count <= this.cfg.low_at && (worst === null || t.count < this.tubes[worst].count)) worst = i;
      });
      return worst;
    },

    dispense(i, why) {
      const t = this.tubes[i]; if (!t) return;
      if (t.count <= 0) {          // mirrors the firmware: no pill, no dose
        this.mode = "empty"; this.active = i; this.emptyAt = Date.now();
        this.push("empty", i, { why });
        beep("alert");
        return;
      }
      this.mode = "dispensing"; this.active = i;
      setTimeout(() => {
        t.count = Math.max(0, t.count - t.dose);
        this.mode = "due"; this.dueAt = Date.now(); this.reminders = 0;
        this.push("dispensed", i, { why, left: t.count, dose: t.dose, servo: true });
        if (t.count <= this.cfg.low_at) this.push("low", i, { left: t.count });
        beep();
      }, 1400);
    },

    taken(i) {
      const t = i == null ? this.active : i; if (t == null) return;
      this.push("taken", t, { why: "button" });
      const day = this.history[this.history.length - 1];
      if (day) { day.taken++; day.due++; }
      this.mode = "taken"; this.takenUntil = Date.now() + 4000;
    },

    tick() {
      const n = new Date();
      const now = n.getHours() * 3600 + n.getMinutes() * 60 + n.getSeconds();

      if (this.mode === "taken" && Date.now() > this.takenUntil) { this.mode = "idle"; this.active = null; }
      if (this.mode === "empty" && Date.now() - this.emptyAt > 60000) { this.mode = "idle"; this.active = null; }

      if (this.mode === "idle") {
        for (let i = 0; i < this.tubes.length; i++) {
          for (const hhmm of this.tubes[i].times) {
            const target = hhmmToSec(hhmm), key = i + "|" + hhmm;
            if (target == null || this.fired.has(key)) continue;
            if (now >= target && now - target <= this.cfg.catch_up) {
              this.fired.add(key); this.dispense(i, "scheduled " + hhmm); return;
            }
          }
        }
      }
      if (this.mode === "due") {
        const waited = (Date.now() - this.dueAt) / 1000;
        if (waited >= this.cfg.missed_after) {
          this.push("missed", this.active, { waited: Math.round(waited) });
          const day = this.history[this.history.length - 1];
          if (day) { day.missed++; day.due++; }
          this.mode = "idle"; this.active = null;
        } else if (this.reminders < this.cfg.remind_limit &&
                   waited >= this.cfg.remind_every * (this.reminders + 1)) {
          this.reminders++;
          this.push("reminded", this.active, { n: this.reminders });
        }
      }
    },

    state() {
      const next = this.nextDose(), low = this.lowTube();
      const shown = this.active != null ? this.active : next.tube;
      const t = shown != null ? this.tubes[shown] : {};
      return {
        source: "demo", connected: true, fw: "simulated", port: null, error: "",
        clock_set: true, mode: this.mode, active: this.active,
        waited: this.mode === "due" ? Math.round((Date.now() - this.dueAt) / 1000) : 0,
        next, low,
        lcd: lcdRender({ clock_set: true, mode: this.mode, tube: shown,
                         label: t.label, dose: t.dose,
                         next_hhmm: next.at, next_in: next.in }),
        tubes: this.tubes.map(x => ({ ...x })),
        hw: { present: { servo: true, lcd: true, audio: true },
              servos: [true, true, true], pins: [21, 38, 39],
              closed: [10, 10, 10], open: [100, 100, 100],
              detail: { servo: "T1:21 T2:38 T3:39", lcd: "0x27 (sim)",
                        audio: "I2S 2/41/48 (sim)" } },
        cfg: this.cfg, chimes: this.chimes,
        events: this.events.slice(-20), history: this.history,
        console: [], now: new Date().toISOString().slice(0, 19),
        camera: null,
      };
    },

    command(c) {
      switch (c.c) {
        case "dispense": this.dispense(c.i | 0, "manual"); break;
        case "force":    { const n = this.nextDose(); this.dispense(n.tube ?? 0, "forced " + n.at); break; }
        case "taken":    this.taken(c.i); break;
        case "snooze":   if (this.mode === "due") {
                           this.dueAt = Date.now() + (c.m || 10) * 60000;
                           this.push("snoozed", this.active, { mins: c.m || 10 });
                         } break;
        case "pills":    if (this.tubes[c.i]) {
                           this.tubes[c.i].count = clamp(c.n | 0, 0, 999);
                           this.push("refill", c.i, { to: this.tubes[c.i].count });
                         } break;
        case "sched":    if (this.tubes[c.i]) {
                           const t = this.tubes[c.i];
                           if (c.times) t.times = [...new Set(c.times.filter(x => hhmmToSec(x) != null))].sort();
                           if (c.label != null) t.label = String(c.label).slice(0, 24);
                           if (c.dose != null)  t.dose = clamp(c.dose | 0, 1, 9);
                         } break;
        case "sweep":    this.push("tested", c.i | 0, { what: "gate sweep" }); break;
        case "chime":    beep(c.name === "urgent" ? "alert" : "dose"); break;
        case "cfg":      Object.assign(this.cfg, c); delete this.cfg.c; break;
      }
      return { ok: true };
    },
  };

  /* ================================================================== *
   * BRIDGE
   * ================================================================== */
  const Bridge = {
    snap: null, fail: 0, ports: [], portsAt: 0, onPorts: null,

    url(path) { return App.base.replace(/\/$/, "") + path; },

    async poll() {
      try {
        const r = await fetch(this.url("/api/state"), { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        this.snap = await r.json(); this.fail = 0;
        if (Date.now() - this.portsAt > 8000) this.loadPorts();
      } catch {
        this.fail++;
        this.snap = { bridge: { connected: false,
                       error: this.fail > 1 ? "Cannot reach the bridge at " + App.base : "" },
                      device: {}, console: [] };
      }
    },

    async loadPorts() {
      this.portsAt = Date.now();
      try {
        const j = await (await fetch(this.url("/api/ports"), { cache: "no-store" })).json();
        this.ports = j.ports || [];
        if (this.onPorts) this.onPorts(this.ports);
      } catch {}
    },

    async command(c) {
      try {
        const r = await fetch(this.url("/api/command"), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(c),
        });
        return await r.json();
      } catch (e) { return { ok: false, error: String(e) }; }
    },

    async findCamera(hint) {
      try {
        const r = await fetch(this.url("/api/camera/find"), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hint: hint || null }),
        });
        return await r.json();
      } catch (e) { return { ok: false, error: String(e) }; }
    },

    async setPort(port) {
      try {
        await fetch(this.url("/api/port"), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ port: port || null }),
        });
        return true;
      } catch { return false; }
    },

    state() {
      const s = this.snap; if (!s) return null;
      const b = s.bridge || {}, d = s.device || {};
      return {
        source: "bridge", connected: !!b.connected, fw: d.fw || null,
        port: b.port || null, error: b.error || "", frame_age: b.frame_age,
        clock_set: !!d.clock_set, mode: d.mode || "idle", active: d.active ?? null,
        waited: d.waited || 0,
        next: d.next || { tube: null, at: null, in: null },
        low: d.low ?? null, lcd: d.lcd || null,
        tubes: d.tubes || [], hw: d.hw || { present: {}, detail: {} },
        cfg: d.cfg || {}, chimes: d.chimes || [], events: d.events || [],
        history: null, console: s.console || [], now: d.now || null,
        camera: s.camera || null,
      };
    },
  };

  /* ================================================================== *
   * COMMANDS
   * ================================================================== */
  async function send(cmd, label) {
    const res = App.source === "demo" ? Sim.command(cmd) : await Bridge.command(cmd);
    if (res && res.ok === false) {
      toast("bad", "⚠", "Could not " + (label || cmd.c),
            res.error || "The device did not accept it.");
    }
    return res;
  }

  /* Countdown interpolated between polls, so seconds tick smoothly rather
     than jumping when a frame lands. */
  function liveGap() {
    const S = App.S;
    if (!S || !S.next || S.next.in == null) return null;
    return S.next.in - (performance.now() - App.at) / 1000;
  }

  /* ================================================================== *
   * EVENTS -> notifications
   * ================================================================== */
  function watchEvents(S) {
    // The first frame carries whatever already happened today. That is
    // history, not news — announcing five of them the moment the page opens
    // buries the interface. So the first pass only primes the set.
    const priming = !App.primed;
    App.primed = true;

    (S.events || []).forEach(ev => {
      const id = ev.k + "|" + ev.at + "|" + (ev.i ?? "") + "|" + (ev.ms ?? "");
      if (App.seen.has(id)) return;
      App.seen.add(id);
      if (App.seen.size > 300) App.seen = new Set([...App.seen].slice(-200));
      Hist.note(ev);
      if (priming) return;

      const who = ev.label || ("Tube " + ((ev.i ?? 0) + 1));
      if (ev.k === "dispensed") { toast("good", "◐", "Dose dispensed", who + " · " + (ev.left ?? "?") + " left"); beep(); }
      if (ev.k === "taken")     { toast("good", "✓", "Dose taken", who); }
      if (ev.k === "empty")     { toast("bad", "⊘", "Nothing to dispense", who + " is empty — a dose was due."); beep("alert"); }
      if (ev.k === "missed")    { toast("bad", "✕", "Dose missed", who + " was not taken."); }
    });
  }

  /* ================================================================== *
   * LOOP
   * ================================================================== */
  let pullTimer = null, paintTimer = null, paint = null;

  async function pull() {
    if (App.source === "demo") {
      Sim.tick(); App.S = Sim.state(); App.at = performance.now();
    } else {
      await Bridge.poll();
      const s = Bridge.state();
      if (s) { App.S = s; App.at = performance.now(); }
      if (s && s.connected) App.everConnected = true;

      // Served over HTTP, so we assumed a bridge was there and went looking
      // for one. If nothing answers in the first few seconds and the user
      // has never picked a source themselves, fall back to the simulation
      // rather than showing a judge an empty "No device" screen. This is
      // what makes a GitHub Pages copy of the site presentable.
      // Once they choose a source explicitly, this never fires again - a
      // bridge that drops mid-demo must NOT silently become demo data.
      if (!App.everConnected && !store.get("touchedSource", false) &&
          Bridge.fail >= 4 && performance.now() - App.startedAt < 15000) {
        toast("warn", "◌", "No dispenser found — showing demo data",
              "Start site/bridge.py and pick Live device in the corner to connect one.");
        setSource("demo");
        return;
      }
    }
    if (App.S) {
      watchEvents(App.S);
      App.onState.forEach(fn => { try { fn(App.S); } catch (e) { console.error(e); } });
    }
  }

  function rate() { return App.source === "demo" ? 250 : 500; }

  function setSource(src) {
    App.source = src; store.set("source", src);
    App.primed = false; App.seen = new Set();
    if (src === "bridge") { Bridge.snap = null; Bridge.portsAt = 0; }
    clearInterval(pullTimer);
    pull();
    pullTimer = setInterval(pull, rate());
    if (paint) paint(App.S);
  }

  function setBase(url) {
    App.base = (url || "").trim() || "http://localhost:9000";
    store.set("base", App.base);
    Bridge.snap = null; Bridge.portsAt = 0;
  }

  /* Called once by carer.js or patient.js. `paintFn` runs at ~10Hz so the
     countdown is smooth without hammering the bridge. */
  function start(opts) {
    opts = opts || {};
    App.role = opts.role || "carer";
    paint = opts.paint || null;
    Sim.boot();

    // Served over HTTP means the bridge is right there and its origin is
    // the correct address, so default to the real device. Opened off disk,
    // stay in demo. Either way an explicit choice wins.
    if (location.protocol.startsWith("http") && !store.get("touchedSource", false)) {
      setBase(location.origin);
      App.source = "bridge"; store.set("source", "bridge");
    }

    App.S = Sim.state(); App.at = performance.now();
    App.startedAt = performance.now();
    pull();
    pullTimer = setInterval(pull, rate());
    if (paint) paintTimer = setInterval(() => paint(App.S), 100);
    return App;
  }

  return { App, Sim, Bridge, send, liveGap, setSource, setBase, start,
           markSourceTouched: () => store.set("touchedSource", true) };
})();
