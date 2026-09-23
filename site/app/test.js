/* ==================================================================== *
 * Kairo — the testing page
 *
 * The bench tools that used to sit on the carer console: start a dose,
 * dispense or swing a gate on demand, both presence sensors raw, the LCD
 * mirror, the hardware list and the board's serial console. Same data
 * layer as the other two pages, so demo and live work here too.
 * ==================================================================== */
"use strict";

(() => {
  const { $, escAttr, setHtml, lcdRender, paintLcd } = K;
  const { App, send, start } = Kairo;

  const row = (on, name, detail, extra) =>
    '<div class="p ' + (on == null ? "" : on ? "ok" : "no") + '"><span class="dot"></span>' +
    '<span class="nm">' + escAttr(name) + '</span><span class="pin" title="' + escAttr(detail) + '">' +
    escAttr(detail) + '</span>' + (extra || "") + '</div>';

  function paint(S) {
    if (!S) return;
    const busy = !S.connected || S.mode !== "idle";

    $("#srcChip").className = "chip " + (App.source === "demo" ? "demo" : S.connected ? "live" : "bad");
    $("#srcTxt").textContent = App.source === "demo" ? "Demo data" : S.connected ? "Live · " + (S.port || "serial") : "No device";

    $("#doseSub").textContent = !S.connected ? "The dispenser is not connected."
      : S.mode === "idle" ? "Idle. Next: " + ((S.tubes[S.next && S.next.tube] || {}).label || "nothing scheduled") +
                            (S.next && S.next.at ? " at " + S.next.at : "")
      : "Busy: " + S.mode + (S.active != null ? " · tube " + (S.active + 1) : "");
    $("#btnForce").disabled = busy;

    // Presence, raw: what each sensor says right now.
    const so = S.sonar || {}, v = S.vision || {};
    const camLive = v.age != null && v.age <= 10;
    setHtml($("#presRows"),
      row(so.cm != null ? true : null, "Ultrasonic",
          so.cm != null ? so.cm + " cm · " + (so.near ? "somebody" : "clear") : "no echo") +
      row(camLive, "Camera",
          camLive ? v.seen + " · " + (v.near ? "somebody" : "clear") : (v.note || "not reading")));

    // Gates: one row per tube, built once so a click never lands on a
    // button that was replaced mid-press.
    const gates = $("#gates"), servos = (S.hw && S.hw.servos) || [];
    if (gates.children.length !== 3) {
      gates.innerHTML = [0, 1, 2].map(i =>
        '<div class="p" data-i="' + i + '"><span class="dot"></span><span class="nm"></span>' +
        '<span class="pin"></span>' +
        '<button class="btn sm" data-act="dispense">Dispense</button>' +
        '<button class="btn sm ghost" data-act="sweep">Test gate</button></div>').join("");
    }
    [0, 1, 2].forEach(i => {
      const el = gates.children[i], t = S.tubes[i] || {};
      el.className = "p " + (servos[i] === false ? "no" : servos[i] ? "ok" : "");
      $(".nm", el).textContent = "Tube " + (i + 1) + " · " + (t.label || "—");
      $(".pin", el).textContent = (t.count ?? "?") + " left · GPIO" + ((S.hw && S.hw.pins || [])[i] ?? "?");
      $("[data-act=dispense]", el).disabled = busy || !(t.count > 0);
      $("[data-act=sweep]", el).disabled = busy || servos[i] === false;
    });

    renderBox(S);
    renderTerm(S);
  }

  /* What the 1602 shows: the lines the hardware actually wrote when live,
     the same logic rendered here in demo. */
  function renderBox(S) {
    const idx = S.active != null ? S.active : (S.next ? S.next.tube : null);
    const t = idx != null ? S.tubes[idx] : null;
    paintLcd($("#lcd"), (S.lcd && S.lcd[0] != null) ? S.lcd : lcdRender({
      clock_set: S.clock_set, mode: S.mode, tube: idx,
      label: t ? t.label : "", dose: t ? t.dose : 1,
      next_hhmm: S.next && S.next.at, next_in: S.next && S.next.in,
    }));

    const p = (S.hw && S.hw.present) || {}, d = (S.hw && S.hw.detail) || {};
    const servos = (S.hw && S.hw.servos) || [], pins = (S.hw && S.hw.pins) || [];
    const rows = servos.map((ok, i) => [ok, "Tube " + (i + 1) + " gate servo",
      (pins[i] != null ? "GPIO" + pins[i] : "—") + (ok ? " · PWM ready" : "")]);
    rows.push([!!p.lcd, "1602 display", d.lcd || "I²C 0x27"]);
    rows.push([!!p.audio, "Speaker / amplifier", d.audio || "I2S 2/41/48"]);
    rows.push([!!p.sonar, "Ultrasonic sensor", d.sonar || "TRIG11 ECHO12"]);
    setHtml($("#hwList"), rows.map(([on, name, pin]) =>
      row(S.connected ? on : null, name, S.connected ? (on ? pin : String(pin).slice(0, 40) || "not detected") : "no device")).join(""));
  }

  function renderTerm(S) {
    const host = $("#term");
    if (!S.console || !S.console.length) {
      return setHtml(host, App.source === "demo"
        ? "demo mode - no serial port in use" : "waiting for bridge...");
    }
    const atBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 40;
    setHtml(host, S.console.map(l => {
      const cls = l.line.startsWith("device:") ? "dev" : l.line.startsWith("bridge:") ? "br" : "";
      return '<span class="t">' + escAttr(l.t) + '</span> <span class="' + cls + '">' +
             escAttr(l.line) + '</span>';
    }).join("\n"));
    if (atBottom) host.scrollTop = host.scrollHeight;
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#btnForce").onclick = () => send({ c: "force" }, "start the next dose");
    $("#btnProbe").onclick = () => send({ c: "probe" }, "re-probe the pins");
    $("#gates").addEventListener("click", e => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const i = +b.closest("[data-i]").dataset.i;
      send({ c: b.dataset.act, i }, b.dataset.act === "sweep" ? "test the gate" : "dispense");
    });
    start({ role: "test", paint });
  });
})();
