/* ==================================================================== *
 * Kairo — the carer console
 *
 * Everything that involves a decision. Structured as: derive facts from
 * state, then render. Nothing in here mutates the device except through
 * Kairo.send(), so there is exactly one path from a click to the servo.
 * ==================================================================== */
"use strict";

(() => {
  const { $, $$, clamp, escAttr, store, TUBE_COLOURS, CAPACITY,
          fmtGap, humanGap, fmtWhen, lcdRender, paintLcd, beep, toast,
          Notify, Hist } = K;
  const { App, Bridge, send, liveGap, setSource, setBase, start,
          markSourceTouched } = Kairo;

  let view = "dashboard";
  let refillFor = null;                  // which tube the refill drawer is on
  let dismissed = new Set(store.get("dismissed", []));
  let lastAlertIds = new Set();
  let edHash = "";

  const tubeName = (S, i) =>
    (S.tubes[i] && S.tubes[i].label) || "Tube " + (i + 1);

  /* ================================================================== *
   * ALERTS
   * Derived fresh from state every tick, so an alert cannot outlive the
   * condition that caused it. Refilling a tube makes its alert vanish
   * without anything having to remember to clear it.
   * ================================================================== */
  function computeAlerts(S) {
    const out = [];
    const lowAt = (S.cfg && S.cfg.low_at) || 10;

    if (!S.connected) {
      out.push({ id: "offline", sev: "bad", ic: "⚡",
        title: "Dispenser not connected",
        body: S.error || "The bridge cannot reach the board." });
      return out;                       // nothing else is knowable
    }
    if (!S.clock_set) {
      out.push({ id: "clock", sev: "warn", ic: "◷",
        title: "The box does not know the time",
        body: "No doses will fire until the clock is set. It syncs on its own within a few seconds." });
    }

    S.tubes.forEach((t, i) => {
      const count = +t.count || 0;
      const perDay = (t.times || []).length * (t.dose || 1);
      const days = perDay ? Math.floor(count / perDay) : null;
      if (count === 0) {
        out.push({ id: "empty:" + i, sev: "bad", ic: "⊘",
          title: tubeName(S, i) + " is empty",
          body: "The next dose cannot be dispensed.", refill: i });
      } else if (count <= lowAt) {
        out.push({ id: "low:" + i, sev: "warn", ic: "▾",
          title: tubeName(S, i) + " is running low",
          body: count + " pills left" +
                (days != null ? " — about " + days + (days === 1 ? " day" : " days") + " of doses" : ""),
          refill: i });
      }
    });

    // Anything the box has raised in the last few hours that a person
    // should actually respond to.
    const now = Date.now();
    (S.events || []).forEach(ev => {
      const age = ev.at ? (now - Date.parse(ev.at)) : 0;
      if (age > 6 * 3600 * 1000) return;
      if (ev.k === "help") {
        out.push({ id: "help:" + ev.at, sev: "bad", ic: "☎",
          title: "They pressed “I need help”",
          body: "At " + fmtWhen(ev.at) + " on the patient screen." });
      }
      if (ev.k === "missed") {
        out.push({ id: "missed:" + ev.at + ":" + ev.i, sev: "warn", ic: "✕",
          title: "A dose was not taken",
          body: (ev.label || "Tube " + ((ev.i ?? 0) + 1)) + ", due around " + fmtWhen(ev.at) + "." });
      }
    });

    const servos = (S.hw && S.hw.servos) || [];
    servos.forEach((ok, i) => {
      if (ok === false) out.push({ id: "servo:" + i, sev: "warn", ic: "⚙",
        title: "Tube " + (i + 1) + " gate did not respond",
        body: "Check the servo wiring, then use Test gate on that tube." });
    });

    return out;
  }

  function renderAlerts(S) {
    const all = computeAlerts(S);
    const ids = new Set(all.map(a => a.id));

    // Forget dismissals for conditions that have gone away, so the same
    // alert can fire again next time it is genuinely true.
    let changed = false;
    dismissed.forEach(id => { if (!ids.has(id)) { dismissed.delete(id); changed = true; } });
    if (changed) store.set("dismissed", [...dismissed]);

    const live = all.filter(a => !dismissed.has(a.id));

    // Notify only on genuinely new alerts.
    live.forEach(a => {
      if (!lastAlertIds.has(a.id) && App.primed) {
        Notify.send("Kairo: " + a.title, a.body);
        if (a.sev === "bad") beep("alert");
      }
    });
    lastAlertIds = new Set(live.map(a => a.id));

    const card = $("#alerts");
    const worst = live.some(a => a.sev === "bad") ? "bad"
                : live.length ? "hot" : "calm";
    card.className = "card alerts " + worst;

    const badge = $("#alertBadge");
    badge.hidden = live.length === 0;
    badge.textContent = String(live.length);
    $("#alertCount").textContent = live.length
      ? live.length + (live.length === 1 ? " thing needs" : " things need") + " you"
      : "Nothing needs you";

    const host = $("#alertList");
    if (!live.length) {
      host.innerHTML = '<li><div class="calm-note"><span class="ic">✓</span>' +
        '<span>All three tubes have stock, doses are being taken, and the box is online.</span></div></li>';
      if (refillFor !== null) { refillFor = null; renderRefill(S); }
      return;
    }

    host.innerHTML = live.map(a => {
      const acts = [];
      if (a.refill != null) acts.push(
        '<button class="btn sm primary" data-refill="' + a.refill + '">Refill</button>');
      acts.push('<button class="btn sm ghost" data-dismiss="' + escAttr(a.id) + '">Dismiss</button>');
      return '<li class="alert sev-' + a.sev + '">' +
        '<span class="ic">' + a.ic + '</span>' +
        '<span class="tx"><b>' + escAttr(a.title) + '</b><span>' + escAttr(a.body) + '</span></span>' +
        '<span class="go">' + acts.join("") + '</span></li>';
    }).join("");
  }

  /* The refill drawer. Inline under the alerts, so the carer acts where
     they were told about the problem rather than hunting for a field. */
  function renderRefill(S) {
    const host = $("#refillBox");
    if (refillFor === null) { host.hidden = true; host.innerHTML = ""; return; }
    const i = refillFor, t = S.tubes[i] || {};
    host.hidden = false;
    if (host.dataset.tube === String(i)) return;   // do not clobber typing
    host.dataset.tube = String(i);
    host.innerHTML =
      '<div class="f"><label class="fl" for="refillN">' + escAttr(tubeName(S, i)) + '</label>' +
      '<input type="number" id="refillN" min="0" max="999" value="' + CAPACITY + '"></div>' +
      '<div class="quick">' +
        '<button class="btn sm ghost" data-add="10">+10</button>' +
        '<button class="btn sm ghost" data-add="30">+30</button>' +
        '<button class="btn sm ghost" data-set="' + CAPACITY + '">Full (' + CAPACITY + ')</button>' +
      '</div>' +
      '<button class="btn primary" data-save-refill>Save count</button>' +
      '<button class="btn ghost" data-cancel-refill>Cancel</button>';
  }

  $("#alertList").addEventListener("click", e => {
    const S = App.S; if (!S) return;
    const refill = e.target.closest("[data-refill]");
    const dis = e.target.closest("[data-dismiss]");
    if (refill) { refillFor = +refill.dataset.refill; $("#refillBox").dataset.tube = ""; renderRefill(S); }
    if (dis) {
      dismissed.add(dis.dataset.dismiss);
      store.set("dismissed", [...dismissed]);
      renderAlerts(S);
    }
  });

  $("#refillBox").addEventListener("click", async e => {
    const S = App.S; if (!S || refillFor === null) return;
    const field = $("#refillN");
    const add = e.target.closest("[data-add]");
    const set = e.target.closest("[data-set]");
    if (add) field.value = String(clamp((+field.value || 0) + +add.dataset.add, 0, 999));
    if (set) field.value = set.dataset.set;
    if (e.target.closest("[data-cancel-refill]")) { refillFor = null; renderRefill(S); }
    if (e.target.closest("[data-save-refill]")) {
      const n = clamp(+field.value || 0, 0, 999);
      await send({ c: "pills", i: refillFor, n }, "save the pill count");
      toast("good", "＋", "Count saved", tubeName(S, refillFor) + " set to " + n + " pills.");
      refillFor = null; renderRefill(S); edHash = "";
    }
  });

  /* ================================================================== *
   * HERO
   * ================================================================== */
  function renderHero(S) {
    const hero = $("#hero"), gap = liveGap();
    const idx = S.active != null ? S.active : (S.next ? S.next.tube : null);
    const colour = TUBE_COLOURS[(idx ?? 0) % 3];

    hero.classList.toggle("urgent", S.mode === "due");
    $("#heroWho").textContent = (S.cfg && S.cfg.patient) || "Patient";
    $("#heroSw").style.background = colour;
    $("#heroMed").textContent = idx != null ? tubeName(S, idx) : "No medicine set";

    let ratio = 0, ringCol = colour, lab = "Next dose in", big = "--", sml = "";

    if (!S.connected) {
      lab = "Device"; big = "Offline"; sml = S.error || "not connected";
      ringCol = "#ff6f70"; ratio = 1;
    } else if (!S.clock_set) {
      lab = "Clock"; big = "Not set"; sml = "waiting for the bridge";
      ringCol = "#ffbe55"; ratio = 1;
    } else if (S.mode === "empty") {
      lab = "Could not dispense"; big = "Empty";
      sml = "tube " + ((S.active ?? 0) + 1) + " has no pills";
      ringCol = "#ffbe55"; ratio = 1;
    } else if (S.mode === "dispensing") {
      lab = "Dispensing"; big = "Now";
      sml = "tube " + ((S.active ?? 0) + 1) + " turning"; ratio = 1;
    } else if (S.mode === "due") {
      const w = S.waited || 0;
      lab = w < 0 ? "Reminder in" : "Waiting to be taken";
      big = fmtGap(Math.abs(w));
      sml = w < 0 ? "snoozed" : "dispensed, not yet taken";
      ringCol = "#ff6f70"; ratio = 1;
    } else if (S.mode === "taken") {
      lab = "Logged"; big = "Done"; sml = "thank you"; ringCol = "#4fe0bd"; ratio = 1;
    } else if (gap == null) {
      lab = "Schedule"; big = "Empty"; sml = "add a dose time below";
      ringCol = "#65728a"; ratio = 0;
    } else {
      big = fmtGap(gap);
      sml = "at " + (S.next.at || "--:--");
      // The arc fills over the last twelve hours before a dose, so its
      // shape carries information rather than just spinning.
      ratio = clamp(1 - gap / (12 * 3600), 0, 1);
      if (gap < 3600) ringCol = "#ffbe55";
      if (gap < 300)  ringCol = "#ff6f70";
    }

    const C = 2 * Math.PI * 43;
    const arc = $("#ringArc");
    arc.style.strokeDashoffset = String(C * (1 - ratio));
    arc.style.stroke = ringCol;
    $("#ringGlow").style.background = ringCol;
    $("#ringLab").textContent = lab;
    $("#ringBig").textContent = big;
    $("#ringSml").textContent = sml || " ";

    const times = idx != null && S.tubes[idx] ? (S.tubes[idx].times || []) : [];
    $("#heroAt").textContent = times.length
      ? times.join(" · ") + " · " + times.length + "× a day"
      : "No dose times set";

    $("#btnForce").textContent = S.mode === "due" ? "Mark as taken" : "Dispense next dose";
    $("#btnForce").disabled = !S.connected || S.mode === "dispensing";
  }

  /* ================================================================== *
   * ADHERENCE
   * ================================================================== */
  function renderStats(S) {
    const hist = S.history || Hist.last14();
    const withData = hist.filter(d => d.due > 0);
    const due = withData.reduce((a, d) => a + d.due, 0);
    const taken = withData.reduce((a, d) => a + d.taken, 0);
    const missed = withData.reduce((a, d) => a + d.missed, 0);
    const demo = S.source === "demo";

    const tag = $("#adhTag");
    tag.className = "chip " + (demo ? "demo" : (due ? "live" : ""));
    tag.querySelector("span:last-child").textContent =
      demo ? "Demo data" : (due ? "From the box" : "Collecting");
    $("#adhSub").textContent = demo
      ? "Simulated fortnight, so the panel has something to show"
      : "Counted only from events the box has reported to this browser";

    if (!due) {
      $("#stAdh").textContent = "—"; $("#stAdhN").textContent = "no doses recorded yet";
      $("#stMiss").textContent = "—"; $("#stMissN").textContent = "nothing to report";
    } else {
      $("#stAdh").textContent = Math.round(taken / due * 100) + "%";
      $("#stAdhN").textContent = taken + " of " + due + " doses";
      $("#stMiss").textContent = missed;
      $("#stMissN").textContent = missed === 0 ? "none missed" : "last 14 days";
    }

    const todayDue = S.tubes.reduce((a, t) => a + (t.times || []).length, 0);
    const takenToday = (S.events || []).filter(e => e.k === "taken").length;
    $("#stToday").textContent = todayDue ? Math.min(takenToday, todayDue) + "/" + todayDue : "—";
    $("#stTodayN").textContent = todayDue ? "doses scheduled today" : "no schedule";

    const bar = $("#daysBar");
    if (bar.children.length !== hist.length) bar.innerHTML = hist.map(() => "<i><b></b></i>").join("");
    hist.forEach((d, n) => {
      const box = bar.children[n], fill = box.firstElementChild;
      const pct = d.due ? d.taken / d.due : 0;
      fill.style.height = d.due ? clamp(pct * 100, 12, 100) + "%" : "14%";
      fill.style.background = !d.due ? "rgba(255,255,255,.09)"
        : d.missed === 0 ? "var(--mint)" : pct >= .5 ? "var(--amber)" : "var(--red)";
      box.title = (d.back === 0 ? "Today" : d.back + " days ago") + ": " +
                  (d.due ? d.taken + "/" + d.due + " taken" : "no data");
    });
  }

  /* ================================================================== *
   * TUBES
   * ================================================================== */
  function renderTubes(S) {
    const host = $("#tubeCards");
    if (host.children.length !== 3) {
      host.innerHTML = [0, 1, 2].map(i =>
        '<article class="tube" data-i="' + i + '" style="--tc:' + TUBE_COLOURS[i] + '">' +
        '<div class="glass"><div class="ticks"></div><div class="fill"></div><div class="shine"></div></div>' +
        '<div class="body">' +
          '<div class="tn">Tube ' + (i + 1) + '</div>' +
          '<div class="name"></div><div class="sched"></div>' +
          '<div class="count"><b class="num">0</b><span>pills left</span></div>' +
          '<div class="runway"></div>' +
          '<div class="servo-no" hidden>servo did not respond</div>' +
          '<div class="warn" hidden></div>' +
          '<div class="acts">' +
            '<button class="btn sm" data-act="dispense">Dispense</button>' +
            '<button class="btn sm ghost" data-act="sweep" title="Open and close the gate without logging a dose">Test gate</button>' +
            '<button class="btn sm ghost" data-act="refill">Refill</button>' +
          '</div>' +
        '</div></article>').join("");
      $$(".glass .ticks", host).forEach(t => {
        t.innerHTML = [20, 40, 60, 80].map(p => '<i style="bottom:' + p + '%"></i>').join("");
      });
      host.addEventListener("click", async e => {
        const btn = e.target.closest("button[data-act]"); if (!btn) return;
        const i = +btn.closest(".tube").dataset.i;
        if (btn.dataset.act === "dispense") await send({ c: "dispense", i }, "dispense");
        if (btn.dataset.act === "sweep")    await send({ c: "sweep", i }, "test the gate");
        if (btn.dataset.act === "refill") {
          refillFor = i; $("#refillBox").dataset.tube = ""; renderRefill(App.S);
          $("#alerts").scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    }

    const lowAt = (S.cfg && S.cfg.low_at) || 10;
    const servos = (S.hw && S.hw.servos) || null;

    [0, 1, 2].forEach(i => {
      const el = host.children[i];
      const t = S.tubes[i] || { label: "", times: [], count: 0, dose: 1 };
      const count = +t.count || 0;
      const perDay = (t.times || []).length * (t.dose || 1);
      const low = count > 0 && count <= lowAt, empty = count === 0;

      el.classList.toggle("low", low); el.classList.toggle("empty", empty);
      $(".name", el).textContent = t.label || "Tube " + (i + 1);
      $(".sched", el).textContent = (t.times || []).length
        ? (t.times || []).join(" · ") + "  ·  " + (t.dose || 1) +
          " pill" + ((t.dose || 1) > 1 ? "s" : "") + " a dose"
        : "no dose times set";
      $(".count b", el).textContent = count;
      $(".fill", el).style.height = clamp(count / CAPACITY * 100, 0, 100) + "%";

      const days = perDay ? Math.floor(count / perDay) : null;
      $(".runway", el).textContent = !perDay ? "not scheduled"
        : days >= 1 ? days + " day" + (days === 1 ? "" : "s") + " of doses left"
        : "less than a day left";

      const warn = $(".warn", el);
      warn.hidden = !(low || empty);
      warn.className = "warn " + (empty ? "empty" : "low");
      warn.textContent = empty
        ? "⚠  Empty — refill before the next dose"
        : "⚠  Running low — " + count + " left, at or below " + lowAt;

      // With three servos, one dead gate must not make the others look
      // broken — and a green light is not proof a servo is even wired.
      const gateOk = !servos || servos[i] !== false;
      $(".servo-no", el).hidden = !(S.connected && servos && servos[i] === false);
      $("button[data-act=dispense]", el).disabled = !S.connected || empty || S.mode === "dispensing";
      $("button[data-act=sweep]", el).disabled = !S.connected || S.mode === "dispensing" || !gateOk;
    });
  }

  /* ================================================================== *
   * SCHEDULE EDITOR
   * Rebuilt only when the values change AND nothing inside is focused, so
   * an incoming state frame never yanks a field out from under the carer.
   * ================================================================== */
  function renderEditors(S) {
    const host = $("#editors");
    const hash = JSON.stringify(S.tubes.map(t => [t.label, t.dose, t.times, t.count]));
    if (hash === edHash || host.contains(document.activeElement)) return;
    edHash = hash;

    host.innerHTML = S.tubes.map((t, i) =>
      '<div class="ed" data-i="' + i + '" style="--tc:' + TUBE_COLOURS[i] + '">' +
      '<div class="row1"><span class="dot"></span>' +
        '<div class="f-name"><label class="fl">Tube ' + (i + 1) + ' — medicine</label>' +
          '<input type="text" data-f="label" value="' + escAttr(t.label || "") + '" placeholder="Medicine name"></div>' +
        '<div class="f-dose"><label class="fl">Pills / dose</label>' +
          '<input type="number" data-f="dose" min="1" max="9" value="' + (t.dose || 1) + '"></div>' +
        '<div class="f-count"><label class="fl">Pills left</label>' +
          '<input type="number" data-f="count" min="0" max="999" value="' + (+t.count || 0) + '"></div>' +
      '</div>' +
      '<div class="times">' +
        (t.times || []).map(x =>
          '<span class="time-chip">' + escAttr(x) +
          '<button data-rm="' + escAttr(x) + '" title="Remove ' + escAttr(x) + '">×</button></span>').join("") +
        '<input type="time" data-f="add" step="60" aria-label="Add a dose time for tube ' + (i + 1) + '">' +
        '<button class="btn sm" data-add>Add time</button>' +
      '</div>' +
      '<div class="save"><button class="btn sm primary" data-save>Save to device</button>' +
      '<span class="saved">Saved</span></div></div>').join("");
  }

  $("#editors").addEventListener("click", async e => {
    const ed = e.target.closest(".ed"); if (!ed) return;
    const S = App.S; if (!S) return;
    const i = +ed.dataset.i;
    const times = $$(".time-chip", ed).map(c => c.textContent.replace("×", "").trim());

    if (e.target.dataset.rm != null) {
      await send({ c: "sched", i, times: times.filter(x => x !== e.target.dataset.rm) },
                 "update the schedule");
      edHash = "";
    }
    if (e.target.closest("[data-add]")) {
      const v = $("input[data-f=add]", ed).value;
      if (!v) return toast("warn", "🕑", "Pick a time first", "Use the time field beside the button.");
      if (times.includes(v)) return toast("warn", "🕑", "Already scheduled", v + " is already on this tube.");
      await send({ c: "sched", i, times: [...times, v].sort() }, "add the time");
      edHash = "";
    }
    if (e.target.closest("[data-save]")) {
      const label = $("input[data-f=label]", ed).value.trim() || "Medicine";
      const dose  = clamp(+$("input[data-f=dose]", ed).value || 1, 1, 9);
      const count = clamp(+$("input[data-f=count]", ed).value || 0, 0, 999);
      await send({ c: "sched", i, label, dose, times }, "save the schedule");
      if (count !== (S.tubes[i] || {}).count) await send({ c: "pills", i, n: count }, "set the count");
      const tag = $(".saved", ed);
      tag.classList.add("on"); setTimeout(() => tag.classList.remove("on"), 1600);
      edHash = "";
    }
  });

  /* ================================================================== *
   * WHAT THE BOX ITSELF SHOWS
   * ================================================================== */
  function renderBox(S) {
    const idx = S.active != null ? S.active : (S.next ? S.next.tube : null);
    const t = idx != null ? S.tubes[idx] : null;
    // Live mode shows the lines the hardware actually wrote; demo mode
    // renders them here with the identical logic.
    const lines = (S.lcd && S.lcd[0] != null) ? S.lcd : lcdRender({
      clock_set: S.clock_set, mode: S.mode, tube: idx,
      label: t ? t.label : "", dose: t ? t.dose : 1,
      next_hhmm: S.next && S.next.at, next_in: S.next && S.next.in,
    });
    paintLcd($("#lcd"), lines);

    const p = (S.hw && S.hw.present) || {}, d = (S.hw && S.hw.detail) || {};
    const servos = (S.hw && S.hw.servos) || [], pins = (S.hw && S.hw.pins) || [];
    const rows = servos.length
      ? servos.map((ok, i) => [ok, "Tube " + (i + 1) + " gate servo",
          (pins[i] != null ? "GPIO" + pins[i] : "—") + (ok ? " · PWM ready" : "")])
      : [[!!p.servo, "Gate servos", d.servo || "GPIO21 / 38 / 39"]];
    rows.push([!!p.lcd,   "1602 display",        d.lcd   || "I²C 0x27"]);
    rows.push([!!p.audio, "Speaker / amplifier", d.audio || "I2S 2/41/48"]);

    $("#hwList").innerHTML = rows.map(([on, name, pin]) => {
      const cls = !S.connected ? "" : on ? "ok" : "no";
      const shown = !S.connected ? "no device" : on ? pin : (String(pin).slice(0, 40) || "not detected");
      return '<div class="p ' + cls + '"><span class="dot"></span><span class="nm">' +
             escAttr(name) + '</span><span class="pin" title="' + escAttr(shown) + '">' +
             escAttr(shown) + '</span></div>';
    }).join("");
  }

  /* ================================================================== *
   * SOUND
   * ================================================================== */
  const SOUND_BLURB = {
    jingle: "The recorded tune on the board",
    gentle: "Rising four-note phrase",
    chirp:  "Two short chirps and a low note",
    bells:  "Slow, chime-like",
    urgent: "Insistent — for heavy sleepers",
  };
  let soundHash = "";

  function renderSound(S) {
    const chimes = S.chimes || [];
    const chosen = (S.cfg && S.cfg.chime) || "jingle";
    const vol = Math.round(((S.cfg && S.cfg.volume) ?? 0.35) * 100);
    const hash = JSON.stringify([chimes, chosen, S.connected]);

    if (hash !== soundHash) {
      soundHash = hash;
      $("#soundList").innerHTML = chimes.length
        ? chimes.map(c =>
            '<div class="sound' + (c.ok ? "" : " off") + '" role="radio" data-name="' + escAttr(c.name) + '"' +
            ' aria-checked="' + (c.name === chosen) + '" tabindex="0">' +
            '<span class="tick">✓</span>' +
            '<span><span class="nm">' + escAttr(c.name) + '</span></span>' +
            '<span class="kd">' + escAttr(c.ok ? (SOUND_BLURB[c.name] || c.kind) : "file missing on the board") + '</span>' +
            '<button class="btn sm ghost" data-preview="' + escAttr(c.name) + '"' +
            (c.ok && S.connected ? "" : " disabled") + '>Preview</button></div>').join("")
        : '<div class="empty-note">Connect the box to choose a sound.</div>';
    }

    // The slider is only pushed from state when the carer is not dragging.
    const slider = $("#volume");
    if (document.activeElement !== slider && !slider.dataset.dragging) slider.value = String(vol);
    $("#volVal").textContent = slider.value + "%";
  }

  $("#soundList").addEventListener("click", async e => {
    const prev = e.target.closest("[data-preview]");
    if (prev) {
      await send({ c: "chime", name: prev.dataset.preview }, "play the sound");
      return;
    }
    const row = e.target.closest(".sound");
    if (!row || row.classList.contains("off")) return;
    await send({ c: "cfg", chime: row.dataset.name }, "change the sound");
    soundHash = "";
    toast("good", "♪", "Reminder sound changed",
          row.dataset.name + " — saved on the box, so it survives a power cut.");
  });

  {
    const slider = $("#volume");
    slider.addEventListener("input", () => {
      slider.dataset.dragging = "1";
      $("#volVal").textContent = slider.value + "%";
    });
    // Only send on release: dragging would otherwise fire dozens of
    // commands down a serial link that writes each one to flash.
    const commit = async () => {
      delete slider.dataset.dragging;
      await send({ c: "cfg", volume: (+slider.value) / 100 }, "change the volume");
    };
    slider.addEventListener("change", commit);
  }

  /* ================================================================== *
   * CARE SETTINGS
   * ================================================================== */
  let cfgHash = "";

  function renderCfg(S) {
    const c = S.cfg || {};
    const hash = JSON.stringify([c.patient, c.low_at, c.remind_every, c.missed_after]);
    const host = $("#settings");
    if (hash === cfgHash) return;
    // A state frame lands twice a second; rewriting a field the carer is
    // halfway through typing into would be maddening.
    if (host.contains(document.activeElement)) return;
    cfgHash = hash;
    $("#cfgPatient").value = c.patient && c.patient !== "Patient" ? c.patient : "";
    $("#cfgLow").value     = c.low_at ?? 10;
    $("#cfgRemind").value  = Math.round((c.remind_every ?? 180) / 60);
    $("#cfgMissed").value  = Math.round((c.missed_after ?? 900) / 60);
  }

  $("#btnSaveCfg").onclick = async () => {
    const patient = $("#cfgPatient").value.trim() || "Patient";
    const low_at  = clamp(+$("#cfgLow").value || 10, 1, 200);
    const remind  = clamp(+$("#cfgRemind").value || 3, 1, 60) * 60;
    const missed  = clamp(+$("#cfgMissed").value || 15, 2, 240) * 60;
    await send({ c: "cfg", patient, low_at, remind_every: remind, missed_after: missed },
               "save the settings");
    cfgHash = "";
    const tag = $("#cfgSaved");
    tag.style.opacity = "1"; setTimeout(() => tag.style.opacity = "0", 1600);
  };

  /* ================================================================== *
   * ACTIVITY + CONSOLE
   * ================================================================== */
  const EV = {
    dispensed: ["◐", "Dispensed"], taken: ["✓", "Taken"], missed: ["✕", "Missed"],
    low: ["▾", "Running low"], empty: ["⊘", "Empty tube"], refill: ["＋", "Refilled"],
    snoozed: ["⏾", "Snoozed"], reminded: ["♪", "Reminded"], skipped: ["⤼", "Skipped"],
    tested: ["⟳", "Gate tested"], help: ["☎", "Help requested"],
  };

  function eventNote(ev) {
    switch (ev.k) {
      case "dispensed": return (ev.why || "") + (ev.left != null ? " · " + ev.left + " left" : "") +
                               (ev.servo === false ? " · servo did not respond" : "");
      case "taken":     return ev.why === "kiosk" ? "acknowledged on the patient screen"
                             : ev.why === "carer" ? "marked by the carer" : (ev.why || "");
      case "missed":    return "not taken after " + Math.round((ev.waited || 0) / 60) + " min";
      case "empty":     return "a dose was due but the tube was empty";
      case "low":       return (ev.left ?? "?") + " pills left";
      case "refill":    return "count set to " + (ev.to ?? "?");
      case "snoozed":   return "pushed back " + (ev.mins ?? "?") + " min";
      case "reminded":  return "reminder " + (ev.n ?? 1);
      case "skipped":   return ev.why || "outside the catch-up window";
      case "tested":    return ev.what || "mechanism check, no dose logged";
      case "help":      return "from the patient screen";
      default:          return "";
    }
  }

  function renderLog(S) {
    const list = (S.events || []).slice().reverse();
    const host = $("#logList");
    if (!list.length) {
      host.innerHTML = '<li><div class="empty-note">Nothing yet. Events appear here as doses go out.</div></li>';
      return;
    }
    host.innerHTML = list.map(ev => {
      const [icon, title] = EV[ev.k] || ["·", ev.k];
      const who = ev.label || (ev.i != null ? "Tube " + (ev.i + 1) : "");
      return '<li class="k-' + escAttr(ev.k) + '"><span class="ic">' + icon + '</span>' +
        '<span class="tx"><b>' + escAttr(title) + '</b>' + (who ? " — " + escAttr(who) : "") +
        '<div>' + escAttr(eventNote(ev)) + '</div></span>' +
        '<span class="when">' + escAttr(fmtWhen(ev.at)) + '</span></li>';
    }).join("");
  }

  function renderTerm(S) {
    const host = $("#term");
    if (!S.console || !S.console.length) {
      host.textContent = App.source === "demo"
        ? "demo mode — no serial port in use\nswitch to Live device once bridge.py is running"
        : "waiting for the bridge...";
      return;
    }
    const atBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 40;
    host.innerHTML = S.console.map(l => {
      const cls = l.line.startsWith("device:") ? "dev" : l.line.startsWith("bridge:") ? "br" : "";
      return '<span class="t">' + escAttr(l.t) + '</span> <span class="' + cls + '">' +
             escAttr(l.line) + '</span>';
    }).join("\n");
    if (atBottom) host.scrollTop = host.scrollHeight;
  }

  /* ================================================================== *
   * CAMERA
   * ================================================================== */
  let camOn = false;

  function camBase() {
    let v = $("#camIp").value.trim();
    if (!v) return null;
    if (!/^https?:\/\//i.test(v)) v = "http://" + v;
    return v.replace(/\/$/, "");
  }
  function camStart() {
    const base = camBase();
    if (!base) return toast("warn", "◉", "Camera address needed",
      "Read the IP from the XIAO serial log and paste it in.");
    store.set("camIp", $("#camIp").value.trim());
    const frame = $("#camFrame");
    frame.innerHTML = '<img alt="Live view of the room" id="camImg">' +
      '<div class="cam-live-badge"><i></i>Live · not recorded</div>';
    const img = $("#camImg");
    img.onerror = () => {
      camStop(true);
      toast("bad", "◉", "No stream at " + base,
        "Check the address, that the sketch is running, and that Start Stream was pressed on the camera page.");
    };
    // The CameraWebServer sketch puts MJPEG on port 81; ?t= defeats caching.
    img.src = base.replace(/:\d+$/, "") + ":81/stream?t=" + Date.now();
    camOn = true;
    $("#btnCam").textContent = "Stop check-in";
    $("#camStat").className = "p ok";
    $("#camStat .pin").textContent = "streaming";
  }
  function camStop(quiet) {
    const img = $("#camImg");
    if (img) { img.onerror = null; img.src = ""; }
    $("#camFrame").innerHTML = '<div class="cam-off"><div class="eye">◉</div>' +
      '<h3>Camera is off</h3><p>The stream stays off until a carer asks for it. Enter the ' +
      'camera address and press <b>Start check-in</b>.</p></div>';
    camOn = false;
    $("#btnCam").textContent = "Start check-in";
    $("#camStat").className = "p";
    $("#camStat .pin").textContent = "idle";
    if (!quiet) $("#live").textContent = "Camera stopped.";
  }

  /* ---------------------------------------------------- finding it ---- */
  /* The XIAO's address comes from DHCP and moves every boot, and once the
     board runs off breadboard power there is no serial log to read it from.
     So the bridge sweeps the network instead - see CameraFinder in
     bridge.py. This just drives it and fills the field in. */
  let camFilled = "";

  function renderCameraFinder(S) {
    const c = S.camera;
    const btn = $("#btnFindCam"), msg = $("#findCamMsg");

    if (App.source === "demo") {
      btn.disabled = true;
      msg.textContent = "Needs the bridge running — this is demo mode.";
      return;
    }
    if (!S.connected && !c) {
      btn.disabled = true;
      msg.textContent = "Needs the bridge running.";
      return;
    }
    if (!c) { btn.disabled = false; msg.textContent = ""; return; }

    btn.disabled = !!c.scanning;
    btn.textContent = c.scanning ? "Looking…" : "Find it for me";

    if (c.scanning) {
      msg.textContent = "Checking " + (c.subnets || []).join(", ") +
                        " — " + (c.scanned || 0) + " addresses tried";
    } else if (c.ip) {
      msg.textContent = c.note || ("found at " + c.ip);
      // Fill the field once per discovery, and never over something the
      // carer is in the middle of typing.
      if (camFilled !== c.ip && document.activeElement !== $("#camIp")) {
        camFilled = c.ip;
        $("#camIp").value = c.ip;
        store.set("camIp", c.ip);
      }
    } else {
      msg.textContent = c.note || "";
    }
  }

  /* ================================================================== *
   * CONNECTION
   * ================================================================== */
  function renderConn(S) {
    const chip = $("#srcChip");
    if (App.source === "demo") {
      chip.className = "chip demo"; $("#srcTxt").textContent = "Demo data";
    } else if (S.connected) {
      chip.className = "chip live"; $("#srcTxt").textContent = "Live · " + (S.port || "serial");
    } else {
      chip.className = "chip bad"; $("#srcTxt").textContent = "No device";
    }

    const dot = $("#connDot"), title = $("#connTitle"), msg = $("#connMsg");
    if (App.source === "demo") {
      dot.style.background = "var(--amber)";
      title.textContent = "Demo data — no board attached";
      msg.className = "msg";
      msg.innerHTML = 'Everything works without hardware. Switch to <b>Live device</b> once ' +
                      '<span class="mono">python site/bridge.py</span> is running.';
    } else if (S.connected) {
      dot.style.background = "var(--mint)";
      title.textContent = "Live on " + (S.port || "serial");
      msg.className = "msg good";
      msg.textContent = "Connected. Commands reach the board in about 30 ms.";
    } else {
      dot.style.background = "var(--red)";
      title.textContent = "Not connected";
      msg.className = "msg bad";
      msg.textContent = S.error || "Waiting for the bridge. Start it with: python site/bridge.py";
    }
    $("#kvFw").textContent = S.fw || "—";
    $("#kvAge").textContent = S.frame_age != null ? S.frame_age + "s"
                            : (App.source === "demo" ? "live sim" : "—");
    $("#kvClock").textContent = S.clock_set ? (S.now || "set") : "not set";
  }

  Bridge.onPorts = ports => {
    const sel = $("#portSel"), keep = sel.value;
    sel.innerHTML = '<option value="">auto-detect</option>' + ports.map(p =>
      '<option value="' + escAttr(p.port) + '">' + escAttr(p.port) +
      (p.likely ? " · ESP32" : "") +
      (p.description ? " — " + escAttr(p.description.slice(0, 26)) : "") + "</option>").join("");
    sel.value = keep || "";
  };

  /* ================================================================== *
   * PAINT
   * ================================================================== */
  function paint(S) {
    if (!S) return;
    if (view === "dashboard") {
      renderAlerts(S); renderRefill(S); renderHero(S); renderStats(S);
      renderTubes(S); renderEditors(S); renderBox(S); renderLog(S);
    } else if (view === "settings") {
      renderSound(S); renderCfg(S); renderTerm(S);
    } else if (view === "camera") {
      renderCameraFinder(S);
    }
    renderConn(S);
  }

  /* ================================================================== *
   * WIRING
   * ================================================================== */
  function setView(v) {
    view = v;
    ["dashboard", "camera", "settings"].forEach(name => {
      const el = $("#" + name);
      if (name === "dashboard") el.style.display = v === name ? "" : "none";
      else el.classList.toggle("on", v === name);
    });
    $$("nav.views button").forEach(b => b.setAttribute("aria-current", String(b.dataset.view === v)));
    if (v !== "camera" && camOn) camStop(true);      // never leave it streaming
    history.replaceState(null, "", v === "dashboard" ? location.pathname : "#/" + v);
    paint(App.S);
  }

  function boot() {
    $$("nav.views button").forEach(b => b.onclick = () => setView(b.dataset.view));

    $("#btnForce").onclick = () => {
      const S = App.S;
      if (S && S.mode === "due") send({ c: "taken", i: S.active, why: "carer" }, "log the dose");
      else send({ c: "force" }, "dispense");
    };
    $("#btnProbe").onclick = () => send({ c: "probe" }, "re-probe the pins");

    $("#btnCam").onclick = () => camOn ? camStop() : camStart();
    $("#btnFindCam").onclick = async () => {
      // Whatever is already in the field is worth one cheap check before
      // sweeping 250 addresses.
      const hint = ($("#camIp").value || "").trim().replace(/^https?:\/\//, "");
      const res = await Bridge.findCamera(hint);
      if (res && res.ok === false) {
        toast("bad", "◉", "Could not reach the bridge",
              "Start it with: python site/bridge.py");
      } else {
        toast("good", "◉", "Looking for the camera",
              "Sweeping the network you are both on. A few seconds.");
      }
    };
    $("#camIp").value = store.get("camIp", "");
    $("#btnSnap").onclick = () => {
      const b = camBase();
      if (!b) return toast("warn", "◉", "Camera address needed", "Paste the IP from the serial log first.");
      window.open(b + "/capture?t=" + Date.now(), "_blank", "noopener");
    };
    $("#btnCamUi").onclick = () => {
      const b = camBase();
      if (!b) return toast("warn", "◉", "Camera address needed", "Paste the IP from the serial log first.");
      window.open(b + "/", "_blank", "noopener");
    };

    const paintAlertChip = () => {
      $("#alarmChip").className = "chip" + (Notify.on ? " live" : "");
      $("#alarmTxt").textContent = Notify.on ? "Alerts on" : "Alerts off";
    };
    paintAlertChip();
    $("#alarmChip").onclick = async () => { await Notify.toggle(); paintAlertChip(); };

    $("#connHd").onclick = () => {
      const open = $("#conn").classList.toggle("open");
      $("#connHd").setAttribute("aria-expanded", String(open));
      $("#connChev").textContent = open ? "▼" : "▲";
    };
    $$("#conn .seg button").forEach(b => b.onclick = () => {
      markSourceTouched();
      $$("#conn .seg button").forEach(x => x.setAttribute("aria-current", String(x === b)));
      $("#bridgeCfg").hidden = b.dataset.src !== "bridge";
      setSource(b.dataset.src);
      soundHash = ""; edHash = "";
    });
    $("#baseUrl").value = App.base;
    $("#baseUrl").onchange = e => setBase(e.target.value);
    $("#portSel").onchange = async e => {
      const ok = await Bridge.setPort(e.target.value);
      toast(ok ? "good" : "bad", "⇄", ok ? "Switching port" : "Could not reach the bridge",
            ok ? (e.target.value || "auto-detecting") : App.base);
    };

    // Keyboard, for demoing quickly.
    addEventListener("keydown", e => {
      if (/input|select|textarea/i.test(document.activeElement.tagName)) return;
      if (e.key === "1") setView("dashboard");
      if (e.key === "2") setView("camera");
      if (e.key === "3") setView("settings");
      if (e.key.toLowerCase() === "f") $("#btnForce").click();
    });

    setInterval(() => {
      $("#hostClock").textContent = new Date().toLocaleTimeString([], { hour12: false });
    }, 500);

    start({ role: "carer", paint });

    $("#bridgeCfg").hidden = App.source !== "bridge";
    $$("#conn .seg button").forEach(b =>
      b.setAttribute("aria-current", String(b.dataset.src === App.source)));
    $("#baseUrl").value = App.base;

    const hash = location.hash.replace(/^#\/?/, "");
    setView(["camera", "settings"].includes(hash) ? hash : "dashboard");
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
