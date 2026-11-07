"use strict";

// Progress of a running fetch / AI catch-up: ask the server every 2 s, reload when it is done.
(() => {
  const panel = document.getElementById("abruf");
  if (!panel || panel.dataset.running !== "1") return;
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  const poll = async () => {
    let job;
    try {
      job = await (await fetch("/abruf/status", { cache: "no-store" })).json();
    } catch {
      setTimeout(poll, 5000);  // agent busy or restarting: try again a little later
      return;
    }
    if (!job.running) { window.location.reload(); return; }
    const bar = document.getElementById("job-progress");
    if (bar) { bar.max = job.total || 1; bar.value = job.done; }
    set("job-phase", job.phase_text);
    set("job-count", job.total
      ? `${job.done} von ${job.total} · ${job.imported} neu${job.ai_pending ? ` · ${job.ai_pending} warten auf die KI` : ""}`
      : "Suche läuft …");
    setTimeout(poll, 2000);
  };
  setTimeout(poll, 1000);
})();

// Read aloud: one controller for the Windows voice audio and the browser voice, with a bar at the
// bottom – pause/continue, stop (also Esc) and speed (remembered in this browser). Only voices
// installed on this PC: online voices ("… Online (Natural)") would send the text to a cloud service.
const reader = (() => {
  const $ = (id) => document.getElementById(id);
  const bar = $("speech-bar"), state = $("speech-state"), pauseButton = $("speech-pause");
  const rateInput = $("speech-rate"), rateValue = $("speech-rate-value");
  const synth = "speechSynthesis" in window ? window.speechSynthesis : null;
  const STORE = "mail-agent-vorlese-tempo";
  let rate = Math.min(2, Math.max(0.6, parseFloat(localStorage.getItem(STORE)) || 1));
  let text = "", offset = 0, spoken = 0, paused = false, audio = null, session = 0;
  let current = null;  // keep the utterance referenced: Chrome may drop the events of a collected one
  let lastCancel = 0, hideTimer = 0;
  const NOTHING_READ = "Der Browser hat nichts vorgelesen. Bitte die Seite neu laden (F5) und erneut versuchen.";

  const showRate = () => {
    if (rateInput) rateInput.value = String(rate);
    if (rateValue) rateValue.textContent = `${rate.toFixed(1).replace(".", ",")}×`;
  };
  const show = (visible) => { clearTimeout(hideTimer); if (bar) bar.hidden = !visible; };
  const label = () => { if (pauseButton) pauseButton.textContent = paused ? "▶ Weiter" : "⏸ Pause"; };
  const localVoice = () => synth.getVoices().find((v) => v.lang.startsWith("de") && v.localService);
  const cancelSpeech = () => {
    if (synth && (synth.speaking || synth.pending)) { synth.cancel(); lastCancel = Date.now(); }
  };

  const stop = () => {
    session += 1;
    cancelSpeech();
    if (audio) {
      audio.pause();
      URL.revokeObjectURL(audio.src);
      audio = null;
    }
    current = null; paused = false; text = ""; show(false);
  };

  // A failure is explained in the bar instead of closing it silently.
  const fail = (message) => {
    session += 1;
    cancelSpeech();
    if (audio) { audio.pause(); audio = null; }
    current = null; paused = false; text = "";
    if (state) state.textContent = message;
    label(); show(true);
    hideTimer = setTimeout(() => { if (bar) bar.hidden = true; }, 9000);
  };

  // A running utterance cannot change its speed: continue from the last spoken word instead.
  // Chrome sometimes swallows an utterance queued right after cancel(), or reports it as
  // "interrupted": wait a moment after a cancel, and try once more if nothing was read.
  const speakFrom = (start, attempt = 1) => {
    const id = (session += 1);
    cancelSpeech();
    offset = start; spoken = start;
    const utterance = new SpeechSynthesisUtterance(text.slice(start));
    utterance.lang = "de-DE";
    utterance.voice = localVoice();
    utterance.rate = rate;
    let started = false;
    const retryOrFail = () => { if (attempt < 2) speakFrom(start, attempt + 1); else fail(NOTHING_READ); };
    utterance.onstart = () => { started = true; };
    utterance.onboundary = (e) => { if (id === session) { started = true; spoken = offset + e.charIndex; } };
    utterance.onend = () => {
      if (id !== session || paused) return;
      if (started) stop(); else retryOrFail();
    };
    utterance.onerror = (e) => {
      if (id !== session || paused) return;
      if (e.error === "interrupted" || e.error === "canceled") { retryOrFail(); return; }
      fail(e.error === "not-allowed"
        ? "Der Browser hat das Vorlesen blockiert – bitte noch einmal auf „Vorlesen“ klicken."
        : `Vorlesen nicht möglich (${e.error}).`);
    };
    current = utterance;
    const go = () => {
      if (id !== session) return;
      if (synth.paused) synth.resume();  // Chrome can stay "paused" after a page change
      synth.speak(current);
      setTimeout(() => {  // neither sound nor an event: Chrome dropped it silently
        if (id === session && !started && !paused && !synth.speaking) retryOrFail();
      }, 2500);
    };
    const wait = 200 - (Date.now() - lastCancel);
    if (wait > 0) setTimeout(go, wait); else go();
  };

  // Fallback only (agent without Windows voice): the browser's own speech output.
  const speakInBrowser = (newText, onMissing) => {
    if (!synth) { fail("Dieser Browser kann nicht vorlesen."); return; }
    if (!newText) return;
    const start = () => {
      stop();
      text = newText; paused = false;
      if (state) state.textContent = "🔊 Liest vor …";
      label(); show(true); speakFrom(0);
    };
    if (localVoice()) { start(); return; }
    // Chrome first lists only its online voices; the Windows voices follow a moment later.
    if (state) state.textContent = "🔊 Stimme wird geladen …";
    show(true);
    let done = false;
    const retry = (final) => {
      if (done) return;
      if (localVoice()) { done = true; start(); return; }
      if (!final) return;
      done = true;
      fail("Keine deutsche Windows-Stimme gefunden (Einstellungen › Zeit und Sprache › Sprache: "
        + "Deutsch mit Sprachausgabe). Online-Stimmen nutzt der Agent aus Datenschutzgründen nicht.");
      if (onMissing) onMissing();
    };
    synth.addEventListener("voiceschanged", () => retry(false));
    [300, 800, 1500].forEach((ms) => setTimeout(() => retry(false), ms));
    setTimeout(() => retry(true), 3000);
  };
  // Chrome loads its voices only on the first request: ask early, so they are ready at the click.
  if (synth) synth.getVoices();

  const play = (player, message) => {
    stop();
    audio = player; audio.playbackRate = rate; paused = false;
    if (state) state.textContent = message;
    label(); show(true);
    player.addEventListener("ended", () => { if (audio === player) stop(); }, { once: true });
  };

  // Read aloud: the agent speaks the text with the German Windows voice (offline on this PC) and the
  // page plays it like any audio. Chrome's own speech output proved unreliable on Windows.
  const speak = async (newText, onMissing) => {
    if (!newText) return;
    stop();
    const id = session;
    if (state) state.textContent = "🔊 Stimme wird vorbereitet …";
    label(); show(true);
    let blob = null;
    try {
      const response = await fetch("/vorlesen", { method: "POST", body: new URLSearchParams({ text: newText }) });
      if (response.ok) blob = await response.blob();
    } catch { blob = null; }
    if (id !== session) return;  // stopped or replaced in the meantime
    if (!blob) { speakInBrowser(newText, onMissing); return; }  // agent without Windows voice
    const player = new Audio(URL.createObjectURL(blob));
    play(player, "🔊 Liest vor …");
    try {
      await player.play();
    } catch (error) {
      if (audio === player) fail(`Wiedergabe nicht möglich (${error.name}) – bitte noch einmal auf „Vorlesen“ klicken.`);
    }
  };

  if (pauseButton) pauseButton.addEventListener("click", () => {
    if (audio) { if (paused) audio.play(); else audio.pause(); }
    else if (synth && text) {
      if (paused) { paused = false; speakFrom(spoken); }
      else { paused = true; session += 1; cancelSpeech(); }
    }
    paused = audio ? audio.paused : paused;
    label();
  });
  if ($("speech-stop")) $("speech-stop").addEventListener("click", stop);
  if (rateInput) {
    rateInput.addEventListener("input", () => {
      rate = parseFloat(rateInput.value) || 1;
      localStorage.setItem(STORE, String(rate));
      showRate();
      if (audio) audio.playbackRate = rate;
    });
    rateInput.addEventListener("change", () => { if (synth && text && !paused && !audio) speakFrom(spoken); });
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && bar && !bar.hidden) stop(); });
  window.addEventListener("pagehide", stop);
  showRate();
  return { speak, stop };
})();
const speakLocally = (text, onMissing) => reader.speak(text, onMissing);

// Unsaved draft: the browser asks before the page is left (other button, link, reload).
(() => {
  const draft = document.getElementById("entwurf");
  if (!draft) return;
  const original = draft.value;
  let saving = false;
  if (draft.form) draft.form.addEventListener("submit", () => { saving = true; });
  window.addEventListener("beforeunload", (event) => {
    if (saving || window.mailAgentOpensMailProgram || draft.value === original) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();

// Copy buttons: data-copy="<selector of input/textarea>"; reminds of placeholders left in [brackets]
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const field = document.querySelector(button.dataset.copy);
  if (!field) return;
  try {
    await navigator.clipboard.writeText(field.value);
  } catch {
    field.select();
    document.execCommand("copy");
  }
  const open = (field.value.match(/\[[^\]\n]{1,60}\]/g) || []).length;
  const label = button.textContent;
  button.textContent = open ? `Kopiert – noch ${open} Platzhalter [ ] ersetzen` : "Kopiert ✓";
  setTimeout(() => { button.textContent = label; }, open ? 4000 : 1500);
});

// "In Outlook öffnen": a new mail in the default mail program with recipient, subject and the current
// text (unsaved edits included). Nothing is sent – that happens only in Outlook. Long texts do not
// fit into a mailto link; then an .eml file is downloaded that Outlook opens as an unsent message.
(() => {
  const crlf = (s) => s.replace(/\r?\n/g, "\r\n");
  const base64 = (s) => {
    let binary = "";
    for (const byte of new TextEncoder().encode(s)) binary += String.fromCharCode(byte);
    return btoa(binary);
  };
  const header = (s) => (/^[\x20-\x7e]*$/.test(s) ? s : `=?UTF-8?B?${base64(s)}?=`);
  const leave = (open) => {
    window.mailAgentOpensMailProgram = true;  // no "unsaved changes" question for this
    open();
    setTimeout(() => { window.mailAgentOpensMailProgram = false; }, 1500);
  };
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mail-open]");
    if (!button) return;
    const to = (document.getElementById("to") || {}).value || "";
    const subject = (document.getElementById("subject") || {}).value || "";
    const body = (document.getElementById("entwurf") || {}).value || "";
    const note = document.getElementById("mail-open-note");
    const mailto = `mailto:${encodeURIComponent(to).replace(/%40/g, "@")}`
      + `?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(crlf(body))}`;
    const link = document.createElement("a");
    if (mailto.length <= 1900) {
      link.href = mailto;
      if (note) note.textContent = "Outlook öffnet eine neue Mail. Erst dort prüfen und senden – danach hier „Beantwortet“ klicken.";
    } else {
      const eml = [
        `To: ${to}`, `Subject: ${header(subject)}`, "X-Unsent: 1", "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8", "Content-Transfer-Encoding: base64", "",
        base64(crlf(body)).replace(/.{76}/g, "$&\r\n"),
      ].join("\r\n");
      link.href = URL.createObjectURL(new Blob([eml], { type: "message/rfc822" }));
      link.download = "Antwort.eml";
      if (note) note.textContent = "Der Text ist zu lang für die direkte Übergabe: Die Datei „Antwort.eml“ wurde heruntergeladen – öffnen, Outlook zeigt sie als neue Mail.";
    }
    if (note) note.hidden = false;
    leave(() => link.click());
  });
})();

// Confirmation for destructive form submissions: data-confirm="<question>"
document.addEventListener("submit", (event) => {
  const question = event.target.dataset.confirm;
  if (question && !window.confirm(question)) event.preventDefault();
});

// Read aloud with the browser's speech output (local Windows voice): data-speak="<text>"
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-speak]");
  if (!button) return;
  speakLocally(button.dataset.speak);  // problems are explained in the read-aloud bar
});

// Push-to-talk: record while the button is held, send a WAV file to the local server.
// The audio stays on this computer: recognition runs locally (Whisper), nothing is stored.
(() => {
  const button = document.getElementById("ptt");
  if (!button) return;
  const status = document.getElementById("ptt-status");
  let context = null, source = null, processor = null, stream = null, chunks = [];

  const say = (text) => speakLocally(text, () => { status.textContent = "Keine lokale deutsche Stimme installiert."; });

  const toWav = (buffers, rate) => {
    const length = buffers.reduce((sum, b) => sum + b.length, 0);
    const view = new DataView(new ArrayBuffer(44 + length * 2));
    const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
    text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, length * 2, true);
    let offset = 44;
    for (const buffer of buffers) {
      for (const sample of buffer) {
        view.setInt16(offset, Math.max(-1, Math.min(1, sample)) * 0x7fff, true);
        offset += 2;
      }
    }
    return new Blob([view], { type: "audio/wav" });
  };

  const start = async (event) => {
    event.preventDefault();
    if (stream) return;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      status.textContent = "Kein Zugriff auf das Mikrofon.";
      return;
    }
    context = new AudioContext();
    source = context.createMediaStreamSource(stream);
    processor = context.createScriptProcessor(4096, 1, 1);
    chunks = [];
    processor.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(processor);
    processor.connect(context.destination);
    button.classList.add("recording");
    status.textContent = "Ich höre zu …";
  };

  const stop = async () => {
    if (!stream) return;
    const rate = context.sampleRate;
    processor.disconnect(); source.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    await context.close();
    stream = null;
    button.classList.remove("recording");
    status.textContent = "Erkenne …";
    const form = new FormData();
    form.append("audio", toWav(chunks, rate), "aufnahme.wav");
    form.append("nachricht", document.body.dataset.messageId || "");
    chunks = [];
    try {
      const response = await fetch("/sprache", { method: "POST", body: form });
      const result = await response.json();
      if (result.error) { status.textContent = result.error; return; }
      status.textContent = `„${result.transcript}“`;
      if (result.redirect) { window.location.href = result.redirect; return; }
      say(result.speech);
    } catch {
      status.textContent = "Spracheingabe fehlgeschlagen.";
    }
  };

  button.addEventListener("pointerdown", start);
  button.addEventListener("pointerup", stop);
  button.addEventListener("pointerleave", stop);
  button.addEventListener("keydown", (e) => { if (e.key === " " && !e.repeat) start(e); });
  button.addEventListener("keyup", (e) => { if (e.key === " ") stop(); });
})();
