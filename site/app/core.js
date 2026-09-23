/* ==================================================================== *
 * Kairo — core helpers, shared by the carer console and the patient kiosk
 *
 * Plain script, no modules: ES modules are blocked by CORS on file://, and
 * the site has to survive being double-clicked off a USB stick. Everything
 * here hangs off the single global `K`.
 * ==================================================================== */
"use strict";

const K = (() => {

  const $  = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  const pad = n => String(n).padStart(2, "0");
  const escAttr = s => String(s).replace(/[&"<>]/g,
    c => ({ "&": "&amp;", '"': "&quot;", "<": "&lt;", ">": "&gt;" }[c]));
  /* innerHTML, but only when it changed. Pages repaint ten times a second,
     and a button replaced between mousedown and mouseup never gets its
     click - which is why Dismiss and Refill used to need a second press. */
  const setHtml = (el, html) => { if (el._html !== html) el.innerHTML = el._html = html; };

  /* Tube identity, used everywhere a tube is named. */
  const TUBE_COLOURS = ["#4fe0bd", "#6aa6ff", "#a98bfa"];
  const CAPACITY = 60;                        // a full tube, for the gauge

  /* ---------------------------------------------------------- storage */
  /* Wrapped because localStorage throws outright in a few contexts —
     private windows, blocked site data, some file:// setups — and a
     remembered UI preference is never worth taking the page down for. */
  const store = {
    get(k, d) {
      try { const v = localStorage.getItem("kairo." + k); return v === null ? d : JSON.parse(v); }
      catch { return d; }
    },
    set(k, v) { try { localStorage.setItem("kairo." + k, JSON.stringify(v)); } catch {} },
  };

  /* ------------------------------------------------------------- time */
  /* Precise, for the carer: 4:52:31. */
  function fmtGap(sec) {
    if (sec == null) return "--";
    sec = Math.max(0, Math.round(sec));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600),
          m = Math.floor(sec % 3600 / 60), s = sec % 60;
    if (d) return d + "d " + pad(h) + "h";
    if (h) return h + ":" + pad(m) + ":" + pad(s);
    return m + ":" + pad(s);
  }
  /* Rounded, for the patient and the LCD: "4h 52m". Same seven-character
     budget as dispenser/lcdview.py so the two never disagree. */
  function humanGap(sec) {
    if (sec == null) return "--";
    sec = Math.max(0, sec);
    if (sec < 60) return "<1m";
    const mins = Math.floor(sec / 60);
    if (mins < 60) return mins + "m";
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h " + pad(mins % 60) + "m";
    return Math.floor(hrs / 24) + "d " + pad(hrs % 24) + "h";
  }
  /* Wall-clock ISO, "2026-09-23T16:02:18". The board stamps events in local
     time (the bridge sets its clock from this PC) and everything here reads
     them that way, so whatever the browser stamps must be local too -
     toISOString() is UTC, which put demo events hours out of place. */
  const localIso = (d = new Date()) =>
    new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
  function fmtWhen(iso) {
    if (!iso) return "";
    return (String(iso).split("T")[1] || "").slice(0, 5);
  }

  /* -------------------------------------------------------------- LCD */
  /* A mirror of dispenser/lcdview.py, used when the board has not sent its
     own rendered lines (i.e. in demo mode). In live mode the carer console
     shows what the hardware actually wrote, not this.
     Note what is absent: any low-stock screen. Running low is the carer's
     problem and appears on the carer console — the box does not nag the
     person taking the medicine about restocking it. */
  const lcdFit = s => { s = String(s); return s.length > 16 ? s.slice(0, 16) : s.padEnd(16, " "); };

  function lcdRender(v) {
    if (!v.clock_set)            return [lcdFit("KAIRO"), lcdFit("SET CLOCK")];
    if (v.mode === "dispensing") return [lcdFit("DISPENSING"), lcdFit("TUBE " + ((v.tube || 0) + 1))];
    if (v.mode === "empty")      return [lcdFit("TUBE " + ((v.tube || 0) + 1) + " EMPTY"), lcdFit("CARER ALERTED")];
    if (v.mode === "due") {
      const label = String(v.label || "Medicine").slice(0, 11).padEnd(11, " ");
      return [lcdFit("COME TO THE BOX"), lcdFit(label + " x" + (v.dose || 1))];
    }
    if (v.mode === "taken")      return [lcdFit("TAKE YOUR PILL"), lcdFit("FROM THE TRAY")];
    if (!v.next_hhmm)            return [lcdFit("KAIRO  READY"), lcdFit("NO DOSES SET")];
    return [lcdFit("NEXT " + v.next_hhmm + "  T" + ((v.tube || 0) + 1)),
            lcdFit("IN " + humanGap(v.next_in))];
  }

  /* Paint 32 character cells. Only touches cells that changed, so the
     display does not flicker on every frame. */
  function paintLcd(host, lines) {
    if (host.children.length !== 32) host.innerHTML = "<s></s>".repeat(32);
    const flat = String(lines && lines[0] || "").padEnd(16, " ").slice(0, 16) +
                 String(lines && lines[1] || "").padEnd(16, " ").slice(0, 16);
    for (let n = 0; n < 32; n++) {
      if (host.children[n].textContent !== flat[n]) host.children[n].textContent = flat[n];
    }
  }

  /* ------------------------------------------------------------ sound */
  /* A short arpeggio in the browser, so a tablet acting as the kiosk makes
     a noise even when the box speaker is across the room. Separate from
     the chime the board plays — this one is per-device and per-browser. */
  let AC = null;
  function beep(pattern) {
    if (!store.get("sound", true)) return;
    const notes = pattern === "alert" ? [880, 880, 698] : [392, 523, 659, 784];
    try {
      AC = AC || new (window.AudioContext || window.webkitAudioContext)();
      const t0 = AC.currentTime;
      notes.forEach((f, i) => {
        const o = AC.createOscillator(), g = AC.createGain();
        o.type = "sine"; o.frequency.value = f;
        const t = t0 + i * 0.16;
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(0.16, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.001, t + 0.34);
        o.connect(g).connect(AC.destination); o.start(t); o.stop(t + 0.36);
      });
    } catch {}
  }

  /* ----------------------------------------------------------- toasts */
  function toast(kind, icon, title, body, ms = 6500) {
    const host = $("#toasts");
    if (!host) return;
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.innerHTML = '<span class="i"></span><div class="c"><b></b><p></p></div>';
    el.querySelector(".i").textContent = icon;
    el.querySelector("b").textContent = title;
    el.querySelector("p").textContent = body || "";
    host.append(el);
    while (host.children.length > 3) host.firstElementChild.remove();
    const live = $("#live");
    if (live) live.textContent = title + ". " + (body || "");
    setTimeout(() => {
      el.style.transition = "opacity .3s"; el.style.opacity = "0";
      setTimeout(() => el.remove(), 320);
    }, ms);
  }

  /* Browser notification, for when the carer is not looking at the tab.
     Opt-in and silent about it if refused. */
  const Notify = {
    on: store.get("alerts", false),
    supported: "Notification" in window,
    async toggle() {
      if (!this.on) {
        if (this.supported && Notification.permission === "default") {
          try { await Notification.requestPermission(); } catch {}
        }
        this.on = !this.supported || Notification.permission === "granted";
        if (!this.on) {
          toast("warn", "\u{1F514}", "Alerts blocked",
                "Your browser refused notifications, so warnings will only appear on this page.");
        }
      } else this.on = false;
      store.set("alerts", this.on);
      return this.on;
    },
    send(title, body) {
      if (!this.on || !this.supported || Notification.permission !== "granted") return;
      try { new Notification(title, { body, tag: title }); } catch {}
    },
  };

  /* -------------------------------------------------------- adherence */
  /* The board only keeps its last few dozen events, so the console
     accumulates a per-day tally in this browser as it observes them. Live
     mode therefore starts empty and fills up honestly, rather than showing
     a percentage nobody can account for. */
  const Hist = {
    data: store.get("hist", {}),
    note(ev) {
      if (!["taken", "missed"].includes(ev.k)) return;
      const id = ev.k + "|" + ev.at + "|" + (ev.i ?? "");
      const seen = store.get("histSeen", []);
      if (seen.includes(id)) return;
      seen.push(id); if (seen.length > 400) seen.shift();
      store.set("histSeen", seen);
      const day = String(ev.at || localIso()).slice(0, 10);
      const row = this.data[day] || (this.data[day] = { due: 0, taken: 0, missed: 0 });
      if (ev.k === "taken")  { row.taken++; row.due++; }
      if (ev.k === "missed") { row.missed++; row.due++; }
      store.set("hist", this.data);
    },
    last14() {
      const out = [];
      for (let back = 13; back >= 0; back--) {
        const d = new Date(); d.setDate(d.getDate() - back);
        const row = this.data[localIso(d).slice(0, 10)] || { due: 0, taken: 0, missed: 0 };
        out.push({ back, ...row });
      }
      return out;
    },
  };

  return { $, $$, clamp, pad, escAttr, setHtml, TUBE_COLOURS, CAPACITY, store,
           fmtGap, humanGap, fmtWhen, localIso,
           lcdFit, lcdRender, paintLcd, beep, toast, Notify, Hist };
})();
