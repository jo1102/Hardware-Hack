/* ==================================================================== *
 * Kairo — the patient kiosk
 *
 * One screen, one message, at most two buttons. There is no navigation on
 * purpose: the person this is for may not be able to work out how to get
 * back from wherever a stray tap took them, so there is nowhere to go.
 * The carer console is reachable only by a deliberate press-and-hold in
 * the corner.
 * ==================================================================== */
"use strict";

(() => {
  const { $, store, humanGap, fmtGap, beep, toast, TUBE_COLOURS } = K;
  const { App, send, liveGap, start } = Kairo;

  /* ------------------------------------------------------------ render */
  /* Each state is a small description, so the shape of this function is
     the shape of the actual behaviour rather than a pile of branches
     poking at the DOM. */
  function describe(S) {
    const gap = liveGap();
    const idx = S.active != null ? S.active : (S.next ? S.next.tube : null);
    const tube = idx != null ? S.tubes[idx] : null;
    const name = tube ? (tube.label || "Tube " + (idx + 1)) : null;

    if (!S.connected) return {
      cls: "", when: "Dispenser offline", num: "—", med: name,
      note: "Ask whoever looks after your medicine to check it.",
      acts: "help", tail: "",
    };
    if (!S.clock_set) return {
      cls: "", when: "Setting up", num: "…", med: name,
      note: "Nearly ready. Nothing to do just yet.", acts: "", tail: "",
    };
    if (S.mode === "empty") return {
      cls: "empty", when: "Tube " + ((idx ?? 0) + 1) + " is empty", num: "⊘", med: name,
      note: "A dose was due but the tube is empty. Your carer has been told.",
      acts: "help", tail: "",
    };
    if (S.mode === "dispensing") return {
      cls: "", when: "Getting your medicine", num: "•••", pulse: true, med: name,
      note: "The dispenser is turning. One moment.", acts: "", tail: "",
    };
    if (S.mode === "due") return {
      cls: "due", when: "Time for your medicine", num: "TAKE", pulse: true, med: name,
      note: (tube ? (tube.dose || 1) : 1) + " pill from tube " + ((idx ?? 0) + 1) +
            ", in the tray at the front.",
      acts: "due", tube: idx,
      tail: (S.waited || 0) < 0 ? "Reminder paused" : "",
    };
    if (S.mode === "taken") return {
      cls: "", when: "All done", num: "✓", med: name,
      note: "Logged. Your carer can see it.", acts: "", tail: "",
    };
    return {
      cls: "", when: "Next dose in", num: gap == null ? "—" : humanGap(gap), med: name,
      note: S.next && S.next.at
        ? "At " + S.next.at + ". Nothing to do until then."
        : "No dose times set yet.",
      acts: "help", tube: idx,
      tail: tube && tube.count != null
        ? tube.count + " pills left in tube " + ((idx ?? 0) + 1) : "",
    };
  }

  /* Buttons are rebuilt only when the SET of buttons changes, so a tap
     never lands on a button that was replaced mid-press. */
  function renderActions(kind, tube) {
    const host = $("#kActs");
    if (host.dataset.kind === kind) return;
    host.dataset.kind = kind;
    host.textContent = "";

    if (kind === "due") {
      const take = document.createElement("button");
      take.className = "k-big"; take.textContent = "I have taken it";
      take.onclick = () => { send({ c: "taken", i: tube, why: "kiosk" }, "log the dose"); beep(); };

      const later = document.createElement("button");
      later.className = "k-2nd"; later.textContent = "Remind me in 10 minutes";
      later.onclick = () => send({ c: "snooze", m: 10 }, "snooze the reminder");

      host.append(take, later);
    } else if (kind === "help") {
      const help = document.createElement("button");
      help.className = "k-2nd"; help.textContent = "I need help";
      help.onclick = async () => {
        await send({ c: "help" }, "call for help");
        toast("good", "☎", "Your carer has been told",
              "It is showing on their screen now.");
      };
      host.append(help);
    }
  }

  function paint(S) {
    if (!S) return;
    const v = describe(S);
    const box = $("#kiosk");

    box.className = "kiosk " + v.cls;
    $("#kWhen").textContent = v.when;
    $("#kNum").textContent = v.num;
    $("#kNum").classList.toggle("pulse", !!v.pulse);
    $("#kMed").textContent = v.med || "";
    $("#kMedRow").hidden = !v.med;
    $("#kNote").textContent = v.note;
    $("#kTail").textContent = v.tail || " ";

    const idx = v.tube != null ? v.tube : 0;
    $("#kSw").style.background = TUBE_COLOURS[idx % 3];

    renderActions(v.acts, v.tube);

    const corner = $("#corner");
    corner.className = "corner " + (S.connected ? "ok" : "bad") +
                       (corner.classList.contains("holding") ? " holding" : "");
    $("#cornerTxt").textContent = S.source === "demo" ? "Demo"
                                : S.connected ? "Connected" : "Not connected";
  }

  /* ------------------------------------------------ the corner escape */
  /* Press and hold for 1.2 seconds to reach the carer console. Long
     enough that a stray touch or a leaned-on tablet never triggers it. */
  function wireCorner() {
    const corner = $("#corner");
    let timer = null;

    const begin = e => {
      if (e.type === "mousedown" && e.button !== 0) return;
      corner.classList.add("holding");
      timer = setTimeout(() => {
        corner.classList.remove("holding");
        location.href = "index.html";
      }, 1200);
    };
    const cancel = () => {
      corner.classList.remove("holding");
      if (timer) { clearTimeout(timer); timer = null; }
    };

    corner.addEventListener("mousedown", begin);
    corner.addEventListener("touchstart", begin, { passive: true });
    ["mouseup", "mouseleave", "touchend", "touchcancel"].forEach(
      ev => corner.addEventListener(ev, cancel));
  }

  /* ------------------------------------------------------------- boot */
  function boot() {
    if (store.get("hc", false)) document.body.classList.add("hc");
    $("#btnHC").textContent = document.body.classList.contains("hc")
      ? "Normal colours" : "Bright-room colours";
    $("#btnHC").onclick = () => {
      const on = document.body.classList.toggle("hc");
      store.set("hc", on);
      $("#btnHC").textContent = on ? "Normal colours" : "Bright-room colours";
    };

    wireCorner();

    // A kiosk is left running for hours, so keep the tablet awake if the
    // browser allows it. Silently skipped where it is not supported.
    if ("wakeLock" in navigator) {
      const hold = () => navigator.wakeLock.request("screen").catch(() => {});
      hold();
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") hold();
      });
    }

    start({ role: "patient", paint });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
