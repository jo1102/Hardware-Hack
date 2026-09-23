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
  const { clamp, store, localIso, lcdRender, beep, toast, Notify, Hist } = K;

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
   * A believable box running the real flow: a tube's interval comes
   * round, the chime plays, and the pill drops once somebody walks up.
   * One tube is already low, and a fortnight of history gives the
   * adherence panel something to show. Real wall clock throughout.
   * ================================================================== */
  const Sim = {
    cfg: { patient: "Margaret Hale", low_at: 10, remind_every: 30,
           remind_limit: 3, missed_after: 120, volume: .35, chime: "jingle",
           near_cm: 80 },
    tubes: [
      { label: "Aspirin",   dose: 1, every: 60, count: 42 },
      { label: "Vitamin C", dose: 1, every: 30, count: 30 },
      { label: "Iron",      dose: 1, every: 5,  count: 6  },
    ],
    chimes: [{ name: "jingle", kind: "wav", ok: true },
             { name: "gentle", kind: "tune", ok: true },
             { name: "chirp",  kind: "tune", ok: true },
             { name: "bells",  kind: "tune", ok: true },
             { name: "urgent", kind: "tune", ok: true }],
    mode: "idle", active: null, dueAt: 0, reminders: 0, takenUntil: 0,
    emptyAt: 0, nextDue: [0, 0, 0], events: [], history: null,

    boot() {
      this.history = this.fakeHistory();
      // Each tube's last dose went fine, one interval ago.
      const now = Date.now();
      this.tubes.forEach((t, i) => {
        this.nextDue[i] = now + t.every * 60000;
        this.push("taken", i, { why: "at the box, 41 cm", left: t.count, dose: t.dose },
                  now - t.every * 60000);
      });
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

    push(kind, tube, extra, at) {
      const ev = { k: kind, at: localIso(new Date(at || Date.now())), ms: Date.now() };
      if (tube != null) { ev.i = tube; ev.label = this.tubes[tube].label; }
      Object.assign(ev, extra || {});
      this.events.push(ev);
      if (this.events.length > 40) this.events.shift();
    },

    /* A plausible ultrasonic reading. Somebody walks up a few seconds
       after the chime and wanders the room otherwise, so the presence
       panel shows the arrival that makes the pill drop. */
    sonarCm() {
      const now = Date.now(), t = now / 1000;
      if ((this.mode === "due" && now - this.dueAt > 6000) ||
          this.mode === "dispensing" || this.mode === "taken")
        return 38 + 7 * Math.sin(t / 2.5);
      return 170 + 95 * Math.sin(t / 19) + 45 * Math.sin(t / 4.3);
    },

    nextDose() {
      let best = null;
      this.nextDue.forEach((at, i) => { if (at && (best == null || at < this.nextDue[best])) best = i; });
      if (best == null) return { tube: null, at: null, in: null };
      return { tube: best, at: localIso(new Date(this.nextDue[best])).slice(11, 16),
               in: Math.max(0, (this.nextDue[best] - Date.now()) / 1000) };
    },

    lowTube() {
      let worst = null;
      this.tubes.forEach((t, i) => {
        if (t.count <= this.cfg.low_at && (worst === null || t.count < this.tubes[worst].count)) worst = i;
      });
      return worst;
    },

    /* drop() and startDue() mirror the functions of the same names in
       dispenser/main.py. */
    drop(i, kind, why) {
      const t = this.tubes[i];
      this.mode = "dispensing"; this.active = i;
      setTimeout(() => {
        t.count = Math.max(0, t.count - t.dose);
        this.push(kind, i, { why, left: t.count, dose: t.dose, servo: true });
        if (t.count <= this.cfg.low_at) this.push("low", i, { left: t.count });
        const day = this.history[this.history.length - 1];
        if (kind === "taken" && day) { day.taken++; day.due++; }
        this.mode = "taken"; this.takenUntil = Date.now() + 6000;
      }, 1400);
    },

    startDue(i, why) {
      this.active = i; this.dueAt = Date.now(); this.reminders = 0;
      if (this.tubes[i].count <= 0) {          // no pill, no dose
        this.mode = "empty"; this.emptyAt = Date.now();
        this.push("empty", i, { why });
        beep("alert");
        return;
      }
      this.mode = "due";
      beep();
    },

    tick() {
      const now = Date.now();
      if (this.mode === "taken" && now > this.takenUntil) { this.mode = "idle"; this.active = null; }
      if (this.mode === "empty" && now - this.emptyAt > 60000) { this.mode = "idle"; this.active = null; }

      if (this.mode === "idle") {
        const i = this.nextDue.findIndex(at => at && now >= at);
        if (i >= 0) {
          this.nextDue[i] = now + this.tubes[i].every * 60000;
          this.startDue(i, "every " + this.tubes[i].every + " min");
        }
      }
      if (this.mode === "due") {
        const waited = (now - this.dueAt) / 1000, cm = this.sonarCm();
        // Both sensors watch; the simulated person trips the ultrasonic.
        if (cm <= this.cfg.near_cm) {
          this.drop(this.active, "taken", "at the box, " + Math.round(cm) + " cm");
        } else if (waited >= this.cfg.missed_after) {
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
      const next = this.nextDose(), low = this.lowTube(), cm = this.sonarCm(), now = Date.now();
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
        tubes: this.tubes.map((x, i) => ({ ...x,
          in: this.nextDue[i] ? Math.max(0, Math.round((this.nextDue[i] - now) / 1000)) : null })),
        hw: { present: { servo: true, lcd: true, audio: true, sonar: true },
              servos: [true, true, true], pins: [21, 38, 39],
              closed: [10, 10, 10], open: [100, 100, 100],
              detail: { servo: "T1:21 T2:38 T3:39", lcd: "0x27 (sim)",
                        audio: "I2S 2/41/48 (sim)",
                        sonar: "TRIG11 ECHO12 (sim)" } },
        cfg: this.cfg, chimes: this.chimes,
        events: this.events.slice(-20), history: this.history,
        console: [], now: localIso(),
        camera: null,
        sonar: { cm: Math.round(cm * 10) / 10, near: cm <= this.cfg.near_cm, present: true },
        // The simulated camera sees the same person the simulated sonar does.
        vision: cm <= 80
          ? { port: "COM6", note: "reading on COM6", near: true, score: 91, seen: "class 1 at 91%", age: 0 }
          : { port: "COM6", note: "reading on COM6", near: false, score: 7, seen: "class 0 at 93%", age: 0 },
      };
    },

    command(c) {
      const t = this.tubes[c.i];
      switch (c.c) {
        case "dispense":                         // as handle("dispense") on the board
          if (!t || t.count <= 0) return { ok: false, error: "That tube is empty." };
          if (this.mode === "due" && c.i === this.active) this.drop(c.i, "taken", "released by the carer");
          else if (this.mode === "idle") this.drop(c.i, "dispensed", "by the carer");
          else return { ok: false, error: "The box is busy with another dose." };
          break;
        case "force": {
          const n = this.nextDose();
          if (this.mode !== "idle" || n.tube == null) return { ok: false, error: "The box is busy." };
          this.nextDue[n.tube] = Date.now() + this.tubes[n.tube].every * 60000;
          this.startDue(n.tube, "started by the carer");
          break;
        }
        case "snooze":   if (this.mode === "due") {
                           this.dueAt = Date.now() + (c.m || 10) * 60000;
                           this.push("snoozed", this.active, { mins: c.m || 10 });
                         } break;
        case "pills":    if (this.tubes[c.i]) {
                           this.tubes[c.i].count = clamp(c.n | 0, 0, 999);
                           this.push("refill", c.i, { to: this.tubes[c.i].count });
                         } break;
        case "sched":    if (t) {
                           if (c.label != null) t.label = String(c.label).slice(0, 24);
                           if (c.dose != null)  t.dose = clamp(c.dose | 0, 1, 9);
                           if (c.every != null && clamp(c.every | 0, 0, 1440) !== t.every) {
                             t.every = clamp(c.every | 0, 0, 1440);
                             this.nextDue[c.i] = t.every ? Date.now() + t.every * 60000 : 0;
                           }
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
        low: d.low ?? null, lcd: d.lcd || null, sonar: d.sonar || null,
        vision: s.vision || null,
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
      // Only the real box feeds the history Live mode reads. Demo has its
      // own fake fortnight; tallying its events here showed them as
      // "From the box" the moment Live was switched on.
      if (App.source !== "demo") Hist.note(ev);
      if (priming) return;

      // Good news only. An empty tube or a missed dose is an alert on the
      // carer console and its own screen on the kiosk; a toast on top of
      // that was the same news twice, with two beeps.
      const who = ev.label || ("Tube " + ((ev.i ?? 0) + 1));
      if (ev.k === "dispensed") { toast("good", "◐", "Dispensed", who + " · " + (ev.left ?? "?") + " left"); beep(); }
      if (ev.k === "taken")     { toast("good", "✓", "Dose taken", who + " · " + (ev.left ?? "?") + " left"); beep(); }
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
