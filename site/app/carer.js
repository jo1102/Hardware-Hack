/* ==================================================================== *
 * Kairo — the carer console
 *
 * Everything that involves a decision. Structured as: derive facts from
 * state, then render. Nothing in here mutates the device except through
 * Kairo.send(), so there is exactly one path from a click to the servo.
 * ==================================================================== */
"use strict";

(() => {
  const { $, $$, clamp, escAttr, setHtml, store, TUBE_COLOURS, CAPACITY,
          fmtGap, humanGap, fmtWhen, beep, toast,
          Notify, Hist } = K;
  const { App, Bridge, send, liveGap, setSource, setBase, start,
          markSourceTouched } = Kairo;

  let view = "dashboard";
  let dismissed = new Set(store.get("dismissed", []));
  let lastAlertIds = new Set();
  const TITLE = document.title;

  const tubeName = (S, i) =>
    (S.tubes[i] && S.tubes[i].label) || "Tube " + (i + 1);

  // Tubes run on an interval in minutes: "every hour", "every 5 min".
  const fmtEvery = m => !m ? "not scheduled" : m % 60 ? "every " + m + " min"
    : m === 60 ? "every hour" : "every " + m / 60 + " hours";
  // How long a tube's stock lasts at its interval, in minutes, or null.
  const stockMins = t => t.every > 0 ? (+t.count || 0) / Math.max(1, t.dose || 1) * t.every : null;
  const fmtSpan = m => m >= 2880 ? Math.floor(m / 1440) + " days" : m >= 120
    ? Math.floor(m / 60) + " hours" : m >= 60 ? "1 hour" : Math.floor(m) + " min";

  /* ================================================================== *
   * ALERTS
   * Derived fresh from state every tick, so an alert cannot outlive the
   * condition that caused it. Refilling a tube makes its alert vanish
   * without anything having to remember to clear it.
   * ================================================================== */
  function computeAlerts(S) {
    const out = [];
    const lowAt = (S.cfg && S.cfg.low_at) || 10;

    // `act` is the one button that resolves the alert; see #alertList below.
    if (!S.connected) {
      out.push({ id: "offline", sev: "bad", ic: "⚡",
        title: "Dispenser not connected",
        body: S.error || "", act: "Connection" });
      return out;                       // nothing else is knowable
    }
    if (!S.clock_set) {
      out.push({ id: "clock", sev: "warn", ic: "◷",
        title: "The box does not know the time",
        body: "No doses fire until it does. It syncs on its own in a few seconds." });
    }

    // Anything the box has raised in the last few hours that a person
    // should actually respond to. First, so a call for help heads the list.
    const now = Date.now();
    (S.events || []).forEach(ev => {
      const age = ev.at ? (now - Date.parse(ev.at)) : 0;
      if (age > 6 * 3600 * 1000) return;
      if (ev.k === "help") {
        out.push({ id: "help:" + ev.at, sev: "bad", ic: "☎", at: ev.at, act: "Check in",
          title: "Help requested",
          body: "They pressed “I need help” on the patient screen." });
      }
      if (ev.k === "missed") {
        out.push({ id: "missed:" + ev.at + ":" + ev.i, sev: "warn", ic: "✕", at: ev.at,
          title: "Dose not taken",
          body: (ev.label || "Tube " + ((ev.i ?? 0) + 1)) + " — nobody came to the box" });
      }
    });

    S.tubes.forEach((t, i) => {
      const count = +t.count || 0, left = stockMins(t);
      if (count === 0) {
        out.push({ id: "empty:" + i, sev: "bad", ic: "⊘", i, act: "Refill",
          title: tubeName(S, i) + " is empty",
          body: "The next dose cannot be dispensed." });
      } else if (count <= lowAt) {
        out.push({ id: "low:" + i, sev: "warn", ic: "▾", i, act: "Refill",
          title: tubeName(S, i) + " is running low",
          body: count + " left" + (left != null ? ", about " + fmtSpan(left) : "") });
      }
    });

    // A gate that failed stays failed until the pins are probed again - the
    // tube's own Test gate button is disabled for exactly that reason.
    const servos = (S.hw && S.hw.servos) || [];
    servos.forEach((ok, i) => {
      if (ok === false) out.push({ id: "servo:" + i, sev: "warn", ic: "⚙", act: "Re-probe",
        title: "Tube " + (i + 1) + " gate did not respond",
        body: "Check the servo wiring, then re-probe." });
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

    // Worst first, so the top row is always the thing to do next.
    const live = all.filter(a => !dismissed.has(a.id))
                    .sort((a, b) => (b.sev === "bad") - (a.sev === "bad"));

    // Notify only on genuinely new alerts.
    live.forEach(a => {
      if (!lastAlertIds.has(a.id) && App.primed) {
        Notify.send("Kairo: " + a.title, a.body);
        if (a.sev === "bad") beep("alert");
      }
    });
    lastAlertIds = new Set(live.map(a => a.id));

    // The count rides in the tab title, so it shows while the tab is behind.
    document.title = (live.length ? "(" + live.length + ") " : "") + TITLE;

    $("#alerts").className = "card alerts " +
      (live.some(a => a.sev === "bad") ? "bad" : live.length ? "hot" : "calm");
    $("#alertHead").textContent = !live.length ? "All clear — nothing needs you"
      : live.length === 1 ? "1 thing needs you" : live.length + " things need you";

    setHtml($("#alertList"), live.map(a =>
      '<li class="alert sev-' + a.sev + '">' +
        '<span class="ic">' + a.ic + '</span>' +
        '<span class="tx"><b>' + escAttr(a.title) + '</b>' +
          (a.body ? '<span>' + escAttr(a.body) + '</span>' : "") + '</span>' +
        (a.at ? '<span class="when">' + escAttr(fmtWhen(a.at)) + '</span>' : "") +
        (a.act ? '<button class="btn sm' + (a.sev === "bad" ? " primary" : "") + '" data-act="' +
                 a.act + '" data-i="' + (a.i ?? "") + '">' + a.act + '</button>' : "") +
        '<button class="x" data-dismiss="' + escAttr(a.id) + '" title="Dismiss" aria-label="Dismiss">×</button>' +
      '</li>').join(""));
  }

  $("#alertList").addEventListener("click", e => {
    const dis = e.target.closest("[data-dismiss]");
    if (dis) {
      dismissed.add(dis.dataset.dismiss);
      store.set("dismissed", [...dismissed]);
      return renderAlerts(App.S);
    }
    const b = e.target.closest("[data-act]"); if (!b) return;
    const act = b.dataset.act, i = +b.dataset.i;
    if (act === "Refill") {
      openForm(i, "refill");
      $("#tubeCards").children[i].scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (act === "Check in") setView("camera");
    if (act === "Connection") $("#conn").showPopover();
    if (act === "Re-probe") send({ c: "probe" }, "re-probe the pins");
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
      lab = "Device"; big = "Offline"; sml = S.error || "";
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
      // The chime has played and the pill is still in the tube: it drops
      // when somebody comes to the box (the panel on the right).
      const w = S.waited || 0;
      lab = w < 0 ? "Reminder in" : "Waiting at the box";
      big = fmtGap(Math.abs(w));
      sml = w < 0 ? "snoozed" : "drops when somebody comes";
      ringCol = "#ff6f70"; ratio = 1;
    } else if (S.mode === "taken") {
      lab = "Dropped"; big = "Done"; sml = "pill in the tray"; ringCol = "#4fe0bd"; ratio = 1;
    } else if (gap == null) {
      lab = "Schedule"; big = "Off"; sml = "no tube has an interval";
      ringCol = "#65728a"; ratio = 0;
    } else {
      big = fmtGap(gap);
      sml = "at " + (S.next.at || "--:--");
      // The arc fills over the tube's interval, so its shape carries
      // information rather than just spinning.
      const every = ((S.tubes[idx] || {}).every || 60) * 60;
      ratio = clamp(1 - gap / every, 0, 1);
      if (gap < 60) ringCol = "#ffbe55";
    }

    const C = 2 * Math.PI * 43;
    const arc = $("#ringArc");
    arc.style.strokeDashoffset = String(C * (1 - ratio));
    arc.style.stroke = ringCol;
    $("#ringGlow").style.background = ringCol;
    $("#ringLab").textContent = lab;
    $("#ringBig").textContent = big;
    $("#ringSml").textContent = sml || " ";

    const t = (idx != null && S.tubes[idx]) || {};
    $("#heroAt").textContent = t.every
      ? fmtEvery(t.every) + " · " + (t.dose || 1) + " pill" + ((t.dose || 1) > 1 ? "s" : "")
      : "No interval set";

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
      : "Doses taken on time";

    if (!due) {
      $("#stAdh").textContent = "—"; $("#stAdhN").textContent = "";
      $("#stMiss").textContent = "—"; $("#stMissN").textContent = "";
    } else {
      $("#stAdh").textContent = Math.round(taken / due * 100) + "%";
      $("#stAdhN").textContent = taken + " of " + due + " doses";
      $("#stMiss").textContent = missed;
      $("#stMissN").textContent = missed === 0 ? "none missed" : "last 14 days";
    }

    const today = hist[hist.length - 1] || { due: 0, taken: 0, missed: 0 };
    $("#stToday").textContent = today.due ? today.taken + "/" + today.due : "—";
    $("#stTodayN").textContent = today.due ? "taken of the doses due" : "none due yet";

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
   * Each card reads out its tube and is also where that tube is refilled
   * and rescheduled. A form is filled from the state once, when it opens,
   * and incoming frames never touch it - so nothing is yanked out from
   * under the carer mid-edit, and Cancel really does discard.
   * ================================================================== */
  function renderTubes(S) {
    const host = $("#tubeCards");
    if (host.children.length !== 3) {
      host.innerHTML = [0, 1, 2].map(i =>
        '<article class="tube" data-i="' + i + '" style="--tc:' + TUBE_COLOURS[i] + '">' +
        '<div class="glass"><div class="ticks"></div><div class="fill"></div><div class="shine"></div></div>' +
        '<div class="body">' +
          '<div class="tn">Tube ' + (i + 1) + '</div>' +
          '<div class="view">' +
            '<div class="name"></div><div class="sched"></div>' +
            '<div class="count"><b class="num">0</b><span>pills left</span></div>' +
            '<div class="runway"></div>' +
            '<div class="servo-no" hidden>servo did not respond</div>' +
            '<div class="warn" hidden></div>' +
            '<div class="acts">' +
              '<button class="btn sm" data-act="refill">Refill</button>' +
              '<button class="btn sm" data-act="edit">Edit</button>' +
            '</div>' +
          '</div>' +
          '<form class="form" hidden></form>' +
        '</div></article>').join("");
      $$(".glass .ticks", host).forEach(t => {
        t.innerHTML = [20, 40, 60, 80].map(p => '<i style="bottom:' + p + '%"></i>').join("");
      });
    }

    const lowAt = (S.cfg && S.cfg.low_at) || 10;
    const servos = (S.hw && S.hw.servos) || null;

    [0, 1, 2].forEach(i => {
      const el = host.children[i];
      const t = S.tubes[i] || { label: "", every: 0, count: 0, dose: 1 };
      const count = +t.count || 0, left = stockMins(t);
      // No frame from the box yet means no count, not an empty tube.
      const low = count > 0 && count <= lowAt, empty = !!S.tubes[i] && count === 0;

      el.classList.toggle("low", low); el.classList.toggle("empty", empty);
      $(".name", el).textContent = t.label || "Tube " + (i + 1);
      $(".sched", el).textContent = fmtEvery(t.every) + (t.every
        ? " · " + (t.dose || 1) + " pill" + ((t.dose || 1) > 1 ? "s" : "") +
          (t.in != null ? " · next in " + humanGap(t.in) : "")
        : "");
      $(".count b", el).textContent = count;
      $(".fill", el).style.height = clamp(count / CAPACITY * 100, 0, 100) + "%";
      $(".runway", el).textContent = left == null ? "no interval set"
        : "about " + fmtSpan(left) + " of doses left";

      const warn = $(".warn", el);
      warn.hidden = !(low || empty);
      warn.className = "warn " + (empty ? "empty" : "low");
      warn.textContent = empty
        ? "⚠  Empty — refill before the next dose"
        : "⚠  Running low — " + count + " left, at or below " + lowAt;

      // With three servos, one dead gate must not make the others look broken.
      $(".servo-no", el).hidden = !(S.connected && servos && servos[i] === false);
      $("button[data-act=refill]", el).classList.toggle("primary", low || empty);
    });
  }

  function openForm(i, kind) {
    const card = $("#tubeCards").children[i], form = $(".form", card), id = "t" + i;
    const t = (App.S && App.S.tubes[i]) || {};
    form.dataset.kind = kind;
    form.innerHTML = (kind === "refill"
      ? '<div><label class="fl" for="' + id + 'n">Pills in the tube now</label>' +
        '<div class="refill-row">' +
          '<input type="number" id="' + id + 'n" name="n" min="0" max="999" required value="' + (+t.count || 0) + '">' +
          '<button type="button" class="btn sm ghost" data-add="10">+10</button>' +
          '<button type="button" class="btn sm ghost" data-add="30">+30</button>' +
          '<button type="button" class="btn sm ghost" data-set="' + CAPACITY + '">Full</button></div></div>'
      : '<div><label class="fl" for="' + id + 'l">Medicine</label>' +
          '<input type="text" id="' + id + 'l" name="label" maxlength="24" required value="' + escAttr(t.label || "") + '"></div>' +
        '<div class="pair">' +
          '<div><label class="fl" for="' + id + 'e">Every (minutes)</label>' +
            '<input type="number" id="' + id + 'e" name="every" min="0" max="1440" required value="' + (+t.every || 0) + '"></div>' +
          '<div><label class="fl" for="' + id + 'd">Pills</label>' +
            '<input type="number" id="' + id + 'd" name="dose" min="1" max="9" required value="' + (+t.dose || 1) + '"></div></div>') +
      '<div class="row"><button class="btn sm primary">Save</button>' +
        '<button type="button" class="btn sm ghost" data-cancel>Cancel</button></div>';
    $(".view", card).hidden = true;
    form.hidden = false;
    form.elements[0].focus();
  }

  function closeForm(card) {
    const form = $(".form", card);
    form.hidden = true; form.innerHTML = "";
    $(".view", card).hidden = false;
  }

  $("#tubeCards").addEventListener("click", e => {
    const card = e.target.closest(".tube"); if (!card) return;
    const i = +card.dataset.i, form = $(".form", card);
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act) return openForm(i, act);                       // refill | edit
    if (e.target.closest("[data-cancel]")) return closeForm(card);
    const add = e.target.closest("[data-add]"), set = e.target.closest("[data-set]");
    if (add) form.elements.n.value = clamp((+form.elements.n.value || 0) + +add.dataset.add, 0, 999);
    if (set) form.elements.n.value = set.dataset.set;
  });

  $("#tubeCards").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target, card = form.closest(".tube"), i = +card.dataset.i, f = form.elements;
    if (form.dataset.kind === "refill") {
      const n = clamp(+f.n.value || 0, 0, 999);
      if ((await send({ c: "pills", i, n }, "save the pill count")).ok === false) return;
      toast("good", "＋", "Count saved", tubeName(App.S, i) + " now has " + n + " pills.");
    } else {
      const label = f.label.value.trim() || "Medicine", dose = clamp(+f.dose.value || 1, 1, 9);
      const every = clamp(+f.every.value || 0, 0, 1440);
      if ((await send({ c: "sched", i, label, dose, every }, "save the schedule")).ok === false) return;
      toast("good", "🕑", "Schedule saved", label + " · " + fmtEvery(every));
    }
    closeForm(card);
  });

  /* ================================================================== *
   * PRESENCE
   *
   * A due pill only drops once somebody is at the box. Both sensors watch
   * at once, with nothing to choose:
   *
   *   ultrasonic   the HC-SR04 on the dispenser, trusted first - two close
   *                readings in a row and the board drops the pill itself
   *   camera       the SenseCraft model on the XIAO, the backup - the bridge
   *                drops the pill on any frame 70% sure of a person
   *
   * Whichever sees them first wins. This panel shows both side by side.
   * ================================================================== */
  function presence(S) {
    const so = S.sonar || {}, v = S.vision || {};
    const cm = so.cm == null ? null : +so.cm;
    const sonar = !!S.connected && (!!so.present || cm != null);
    // A camera that stopped talking is not an empty room.
    const camera = v.age != null && v.age <= 10;
    return { sonar, camera, cm, v, near: (sonar && !!so.near) || (camera && !!v.near) };
  }

  function renderPresence(S) {
    const p = presence(S), nearCm = (S.cfg && S.cfg.near_cm) || 80;
    $("#presChip").className = "chip" + (p.near ? " live" : p.sonar || p.camera ? "" : " demo");
    $("#presSrc").textContent = p.sonar && p.camera ? "Ultrasonic + camera"
      : p.sonar ? "Ultrasonic" : p.camera ? "Camera" : "No sensor";
    $("#pres").classList.toggle("near", p.near);
    $("#presIc").textContent = p.near ? "◉" : "◌";
    // While a dose waits, "nobody" is a state that should end - say so.
    $("#presWhat").textContent = !p.sonar && !p.camera ? "Not sensing"
      : p.near ? "Somebody at the box" : S.mode === "due" ? "Nobody at the box yet" : "Nobody at the box";
    $("#presWhen").textContent =
      (!S.connected ? "the dispenser is not connected"
        : "ultrasonic " + (p.cm != null ? Math.round(p.cm) + " cm" : p.sonar ? "no echo" : "not reading")) +
      " · camera " + (p.camera ? "sees " + p.v.seen : (p.v.note || "not reading"));
    $("#presVal").textContent = p.cm != null ? Math.round(p.cm)
      : p.camera && p.v.score != null ? Math.round(p.v.score) : "—";
    $("#presVal").nextElementSibling.textContent = p.cm != null ? "cm away"
      : p.camera ? "% person" : " ";

    // The bar is a distance scale, so only the ultrasonic gets one.
    const show = p.cm != null;
    $("#presBarWrap").hidden = $("#presScale").hidden = !show;
    if (show) {
      $("#presBar").style.width = clamp(p.cm / 4, 2, 100) + "%";
      $("#presBar").style.background = p.near
        ? "var(--mint)" : "linear-gradient(90deg,var(--mint),var(--blue))";
      $("#presMark").style.left = clamp(nearCm / 4, 0, 100) + "%";
    }
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
    if (hash === cfgHash) return;
    // A state frame lands twice a second; rewriting a field the carer is
    // halfway through typing into would be maddening.
    if ($("#cfgForm").contains(document.activeElement)) return;
    cfgHash = hash;
    $("#cfgPatient").value = c.patient && c.patient !== "Patient" ? c.patient : "";
    $("#cfgLow").value     = c.low_at ?? 10;
    $("#cfgRemind").value  = c.remind_every ?? 30;
    $("#cfgMissed").value  = c.missed_after ?? 120;
  }

  $("#cfgForm").addEventListener("submit", async e => {
    e.preventDefault();
    const patient = $("#cfgPatient").value.trim() || "Patient";
    const low_at  = clamp(+$("#cfgLow").value || 10, 1, 200);
    // Seconds, not minutes: with doses every 5 minutes, waits are short.
    const remind  = clamp(+$("#cfgRemind").value || 30, 5, 3600);
    const missed  = clamp(+$("#cfgMissed").value || 120, 30, 14400);
    const res = await send({ c: "cfg", patient, low_at, remind_every: remind, missed_after: missed },
                           "save the settings");
    cfgHash = "";
    if (res.ok !== false) toast("good", "✓", "Care settings saved", "Stored on the box.");
  });

  /* ================================================================== *
   * ACTIVITY + CONSOLE
   * ================================================================== */
  const EV = {
    dispensed: ["◐", "Dispensed"], taken: ["✓", "Taken"], missed: ["✕", "Missed"],
    low: ["▾", "Running low"], empty: ["⊘", "Empty tube"], refill: ["＋", "Refilled"],
    snoozed: ["⏾", "Snoozed"], reminded: ["♪", "Reminded"], skipped: ["⤼", "Skipped"],
    tested: ["⟳", "Gate tested"], help: ["☎", "Help requested"],
    approached: ["👣", "Came to the box"],
  };

  function eventNote(ev) {
    const w = ev.waited || 0;
    switch (ev.k) {
      case "dispensed":
      case "taken":     return (ev.why || "") + (ev.left != null ? " · " + ev.left + " left" : "") +
                               (ev.servo === false ? " · servo did not respond" : "");
      case "missed":    return "nobody came to the box in " + (w >= 120 ? Math.round(w / 60) + " min" : w + " s");
      case "empty":     return "a dose was due but the tube was empty";
      case "low":       return (ev.left ?? "?") + " pills left";
      case "refill":    return "count set to " + (ev.to ?? "?");
      case "snoozed":   return "pushed back " + (ev.mins ?? "?") + " min";
      case "reminded":  return "reminder " + (ev.n ?? 1);
      case "skipped":   return ev.why || "outside the catch-up window";
      case "tested":    return ev.what || "mechanism check, no dose logged";
      case "help":      return "from the patient screen";
      case "approached": return "detected " + (ev.cm ?? "?") + "cm away while a dose was waiting";
      default:          return "";
    }
  }

  function renderLog(S) {
    const list = (S.events || []).slice().reverse();
    setHtml($("#logList"), !list.length ? '<li><div class="empty-note">No events</div></li>'
      : list.map(ev => {
        const [icon, title] = EV[ev.k] || ["·", ev.k];
        const who = ev.label || (ev.i != null ? "Tube " + (ev.i + 1) : "");
        return '<li class="k-' + escAttr(ev.k) + '"><span class="ic">' + icon + '</span>' +
          '<span class="tx"><b>' + escAttr(title) + '</b>' + (who ? " — " + escAttr(who) : "") +
          '<div>' + escAttr(eventNote(ev)) + '</div></span>' +
          '<span class="when">' + escAttr(fmtWhen(ev.at)) + '</span></li>';
      }).join(""));
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
  // The SenseCraft camera's frames, relayed by the bridge over USB - the
  // same pictures the person detection runs on, so one XIAO does both.
  const usbCam = () => App.source !== "demo" && App.S && App.S.vision &&
                       App.S.vision.frame_age != null && App.S.vision.frame_age < 10;

  function camStart() {
    const usb = usbCam(), base = usb ? Bridge.url("/api/camera/stream") : camBase();
    if (!base) return toast("warn", "◉", "No camera yet",
      "Plug the SenseCraft XIAO into the laptop running the bridge, or put a WiFi camera's address in.");
    if (!usb) store.set("camIp", $("#camIp").value.trim());

    // Sound the box, not this laptop. The point is that the person being
    // looked at hears the camera come on - a light they might not be facing
    // is not consent. Fire-and-forget: no dispenser attached just means no
    // chime, which must never stop the carer seeing the stream.
    send({ c: "chime", name: "chirp" });

    const frame = $("#camFrame");
    frame.innerHTML = '<img alt="Live view of the room" id="camImg">' +
      '<div class="cam-live-badge"><i></i>Live · not recorded</div>';
    const img = $("#camImg");
    img.onerror = () => {
      camStop(true);
      toast("bad", "◉", "No stream at " + base,
        "Check the camera is plugged in and running, and that nothing else is watching it.");
    };
    // The CameraWebServer sketch puts MJPEG on port 81; ?t= defeats caching.
    img.src = usb ? base + "?t=" + Date.now()
                  : base.replace(/:\d+$/, "") + ":81/stream?t=" + Date.now();
    camOn = true;
    $("#btnCam").textContent = "Stop check-in";
    $("#camStat").className = "p ok";
    $("#camStat .pin").textContent = usb ? "streaming · USB, 240×240, model paused" : "streaming · WiFi";
  }
  function camStop(quiet) {
    const img = $("#camImg");
    if (img) { img.onerror = null; img.src = ""; }
    $("#camFrame").innerHTML = '<div class="cam-off"><div class="eye">◉</div>' +
      '<h3>Camera is off</h3></div>';
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
    const btn = $("#btnFindCam"), msg = $("#findCamMsg"), v = S.vision || {};
    // Which feed Start check-in will use: the USB one wins when it is live.
    $("#camFeed").textContent = usbCam() ? "USB · SenseCraft on " + v.port : "WiFi · the address above";
    // The address, the finder and the webcam's own settings page are all
    // WiFi-sketch things - noise while the USB feed is the one in use.
    $("#camWifi").hidden = $("#btnCamUi").hidden = usbCam();

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

    const msg = $("#connMsg");
    msg.className = "msg" + (App.source === "demo" ? "" : S.connected ? " good" : " bad");
    msg.textContent = App.source === "demo" ? "A simulated box - nothing is plugged in."
      : S.connected ? "Connected. Commands reach the board in about 30 ms."
      : S.error || "Waiting for bridge";
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
      renderAlerts(S); renderHero(S); renderPresence(S); renderTubes(S);
      renderStats(S); renderLog(S);
    } else if (view === "settings") {
      renderSound(S); renderCfg(S);
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
      $("#alarmChip").setAttribute("aria-pressed", String(Notify.on));
      $("#alarmChip").textContent = Notify.on ? "Desktop alerts on" : "Desktop alerts off";
    };
    paintAlertChip();
    $("#alarmChip").onclick = async () => { await Notify.toggle(); paintAlertChip(); };

    $$("#conn .seg button").forEach(b => b.onclick = () => {
      markSourceTouched();
      $$("#conn .seg button").forEach(x => x.setAttribute("aria-current", String(x === b)));
      $("#bridgeCfg").hidden = b.dataset.src !== "bridge";
      setSource(b.dataset.src);
      soundHash = "";
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
    });

    // Written cell by cell rather than as one string, so the blinking
    // colons keep their animation instead of restarting on every tick.
    const clock = $("#hostClock");
    clock.innerHTML = "<span></span><i>:</i><span></span><i>:</i><span></span>";
    const digits = $$("span", clock);
    setInterval(() => {
      const d = new Date();
      [d.getHours(), d.getMinutes(), d.getSeconds()].forEach((v, i) => {
        const t = String(v).padStart(2, "0");
        if (digits[i].textContent !== t) digits[i].textContent = t;
      });
    }, 250);

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
