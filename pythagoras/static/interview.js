(() => {
  const token = pathPart(1);
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const canSpeak = "speechSynthesis" in window;
  const SECTIONS = ["loading", "preparing", "error", "completed", "intro", "live", "done"];
  const show = name => SECTIONS.forEach(s => $("#s-" + s).classList.toggle("hidden", s !== name));

  let info = null;          // GET /api/interview/{token}
  let step = null;          // current Step
  let stepAt = 0;           // performance.now() when step arrived
  let typing = !SR;         // typing mode
  let rec = null, listening = false, finalText = "", firstSpeechAt = null;
  let pending = null;       // payload being sent (for retry)
  let timerId = null;
  let lv = null;            // LocalVoice when the server has the local models (natural voice, automatic turns)
  let ackDone = Promise.resolve();   // the "Okay." being played while Pythagoras thinks
  let manualFinish = false, lastAck = "";
  const LANG = () => (info && info.language === "it") ? "it-IT" : "en-US";
  const ACKS = { en: ["Okay.", "Alright.", "Got it.", "I see.", "Right."], it: ["Va bene.", "D'accordo.", "Capito.", "Certo.", "Okay."] };
  const acks = () => ACKS[(info && info.language) || "en"] || ACKS.en;

  // ---------- voice out ----------
  let voice = null;
  function pickVoice() {
    if (!canSpeak) return;
    const lang = LANG().slice(0, 2);
    const vs = speechSynthesis.getVoices().filter(v => v.lang.toLowerCase().startsWith(lang));
    voice = vs.find(v => /natural|neural|premium|enhanced/i.test(v.name))
      || vs.find(v => /Google|Samantha|Aria|Jenny|Daniel|Alice|Elsa/i.test(v.name))
      || vs.find(v => v.lang === LANG()) || vs[0] || null;
  }
  if (canSpeak) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

  function renderWords(text) {
    const box = $("#q-text");
    box.innerHTML = text.split(/(\s+)/).map(w => /^\s+$/.test(w) ? w : `<span class="word">${escapeHtml(w)}</span>`).join("");
    return $$(".word", box);
  }

  async function speakLocal(text, words) {
    const revealAll = () => words.forEach(w => w.classList.add("on"));
    try {
      await lv.speak(text, () => { revealAll(); stage("speaking"); $("#mic-label").textContent = "Pythagoras is speaking"; });
    } catch (e) { toast("Pythagoras's voice had a problem — read the question on screen", "error"); }
    revealAll(); stage("");
  }

  function speak(text) {
    const words = renderWords(text);
    const revealAll = () => words.forEach(w => w.classList.add("on"));
    if (lv && text) return speakLocal(text, words);
    if (!canSpeak || !text) { revealAll(); return Promise.resolve(); }
    speechSynthesis.cancel();
    return new Promise(resolve => {
      const u = new SpeechSynthesisUtterance(text);
      if (voice) u.voice = voice;
      u.lang = (voice && voice.lang) || LANG();
      u.rate = 1; u.pitch = 1;
      // map character offsets to word spans for the reveal
      const offsets = []; let pos = 0;
      text.split(/(\s+)/).forEach(w => { if (!/^\s+$/.test(w) && w) offsets.push(pos); pos += w.length; });
      let gotBoundary = false;
      u.onboundary = e => {
        if (e.name && e.name !== "word") return;
        gotBoundary = true;
        words.forEach((w, i) => { if (offsets[i] <= e.charIndex) w.classList.add("on"); });
      };
      const fallback = setTimeout(() => { if (!gotBoundary) revealAll(); }, 900);
      let done = false;
      const finish = () => { if (done) return; done = true; clearTimeout(fallback); clearInterval(keep); revealAll(); stage(""); resolve(); };
      u.onend = finish; u.onerror = finish;
      // Chrome pauses long utterances after ~15 s; keep it alive
      const keep = setInterval(() => { if (speechSynthesis.speaking && !speechSynthesis.paused) { speechSynthesis.pause(); speechSynthesis.resume(); } }, 10000);
      // safety net if onend never fires
      setTimeout(finish, Math.max(6000, text.length * 120));
      stage("speaking");
      speechSynthesis.speak(u);
    });
  }

  // ---------- voice in ----------
  function stage(kind) {                               // "" | "speaking" | "listening": the orb and the waveform
    $("#orb-wrap").className = "orb-wrap " + kind;
    $("#waveform").classList.toggle("active", !!kind);
  }

  function setMic(state, label) {
    $("#mic-dot").classList.toggle("live", state === "live");
    $("#mic-label").textContent = label;
    stage(state === "live" ? "listening" : "");
  }

  function processing(on) { $(".conversation-panel").classList.toggle("processing", on); }   // the answer is being judged
  function setDone(off) { $("#done-btn").disabled = off; $("#send-btn").disabled = off; }

  function addEntry(who, text, mine) {                 // the running transcript under the room
    const box = $("#transcript"), el = document.createElement("div");
    el.className = "transcript-entry" + (mine ? " answer" : "");
    el.innerHTML = `<strong>${escapeHtml(who)}</strong>${mine || !step ? "" : `<small>${mmss(step.elapsed_s || 0)}${step.level ? " · " + escapeHtml(step.level_name || "") : ""}</small>`}<p>${escapeHtml(text)}</p>`;
    box.appendChild(el); box.scrollTop = box.scrollHeight;
    $("#turn-count").textContent = `${box.children.length} turns`;
  }

  function startListening() {
    if (typing || (!SR && !lv)) { setMic("off", "Type your answer below"); $("#answer").focus(); return; }
    stopListening();
    listening = true;
    if (lv) { lv.listen(true); setMic("live", "Listening… take your time"); return; }
    rec = new SR();
    rec.lang = LANG(); rec.continuous = true; rec.interimResults = true;
    rec.onresult = e => {
      if (firstSpeechAt == null) firstSpeechAt = performance.now();
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText = (finalText + " " + r[0].transcript).replace(/\s+/g, " ").trim();
        else interim += r[0].transcript;
      }
      $("#answer").value = (finalText + (interim ? " " + interim : "")).trim();
      setMic("live", "Listening… take your time");
    };
    rec.onerror = e => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed" || e.error === "audio-capture") {
        listening = false; setTyping(true);
        toast("Microphone not available — you can type your answer", "error");
      }
    };
    rec.onend = () => { if (listening) { try { rec.start(); } catch { /* already started */ } } };
    try { rec.start(); setMic("live", "Listening… take your time"); } catch { setMic("off", "Microphone busy — type instead"); }
  }

  function stopListening() {
    listening = false;
    if (lv) lv.listen(false);
    if (rec) { try { rec.onend = null; rec.stop(); } catch { } rec = null; }
  }

  // ---------- webcam: on and visible during the interview. Live preview only: nothing is recorded or sent ----------
  let cam = null;
  const flag = text => api(`/api/interview/${encodeURIComponent(token)}/flag`, { method: "POST", body: { text } }).catch(() => { });

  async function openCamera() {                        // asked inside the Start click, with the microphone
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      cam = stream;
      $("#cam-video").srcObject = stream;
      $("#cam-placeholder").classList.add("hidden");
      stream.getVideoTracks().forEach(t => t.addEventListener("ended", () => { if (cam === stream) { closeCamera(); flag("The camera was turned off or disconnected"); } }));
    } catch (e) {
      cam = null;
      $("#cam-placeholder small").textContent = "Camera not available";
      toast("Camera not available — the interview continues and your professor is told", "error");
      flag("Camera not available during the interview");
    }
  }

  function closeCamera() {
    if (cam) cam.getTracks().forEach(t => t.stop());
    cam = null;
    $("#cam-video").srcObject = null;
    $("#cam-placeholder").classList.remove("hidden");
  }

  // ---------- local voice: the server tells us when the answer is over ----------
  const localHandlers = {
    onSpeechStart: () => { if (firstSpeechAt == null) firstSpeechAt = performance.now(); setMic("live", "Listening… press I'm done when you've finished"); },
    onTurnEnd: () => {                                 // at once: a short spoken "Okay." covers the thinking time
      listening = false; answerUI("thinking");
      const choices = acks().filter(a => a !== lastAck);
      lastAck = choices[Math.floor(Math.random() * choices.length)];
      ackDone = lv.speak(lastAck).catch(() => { });
    },
    onTranscript: (text, speech_s) => { manualFinish = false; $("#answer").value = text; send({ text, duration_s: speech_s }); },
    onEmpty: () => {
      listening = false;
      if (manualFinish) { manualFinish = false; if (confirm("Send an empty answer (counts as 'I don't know')?")) { send({ text: "", duration_s: null }); return; } }
      answerUI("input"); startListening();
      if (!typing) setMic("live", "I didn't catch that — go ahead");
    },
    onError: msg => { toast(msg + " You can type your answers.", "error"); answerUI("input"); setTyping(true); },
  };

  async function connectLocalVoice(voice) {            // voice.unlock() already ran inside the click
    try {
      await voice.connect();
      lv = voice;
      lv.preload(acks());                              // in the background: ready long before the first answer ends
    } catch (e) {
      voice.close(); lv = null;
      toast("Natural voice unavailable (" + e.message + ") — using the browser voice", "error");
    }
  }

  function setTyping(on) {
    typing = on || (!SR && !lv);
    $("#mode-btn").innerHTML = typing ? "<span>♩</span> Use voice" : "<span>⌨</span> Type instead";
    $("#mode-btn").classList.toggle("hidden", !SR && !lv);
    $("#voice-note").classList.toggle("hidden", typing || !lv);
    $("#answer").placeholder = lv && !typing ? "Your words appear here after you speak." : "Your answer will appear here as you speak. You can fix it before sending.";
    if (typing) { stopListening(); setMic("off", "Type your answer below"); $("#send-hint").textContent = "Send when you're done"; $("#answer").focus(); }
    else { $("#send-hint").innerHTML = lv ? 'Press <b>I\'m done</b> or <span class="kbd">Space</span> when you\'ve finished' : 'Press <span class="kbd">Space</span> when you\'re done (outside the text box)'; startListening(); }
  }

  // ---------- rendering ----------
  const seenTopics = {};                               // topic number -> title, as the conversation reaches them
  function renderLadder(level) {
    $$("#bloom-bars i").forEach((el, n) => el.classList.toggle("on", level >= 2 && n <= level - 2));
    $("#bloom-label").textContent = level ? `0${level} · ${BLOOM[level]}` : "—";
  }

  function renderStep(s) {
    step = s; stepAt = performance.now();
    const total = s.topics_total || 0, idx = s.topic_index || 0, pad = n => String(n).padStart(2, "0");
    if (idx && s.topic_title) seenTopics[idx] = s.topic_title;
    $("#topic-title").textContent = s.topic_title || (s.kind === "opening" ? "Introduction" : s.kind === "wrap_up" ? "Wrapping up" : "—");
    $("#progress-label").textContent = s.done ? "FINISHED" : idx ? `TOPIC ${idx} OF ${total}` : "GETTING STARTED";
    $("#topic-progress").textContent = `${pad(idx)} / ${pad(total)}`;
    $("#level-badge").textContent = s.level && s.level_name ? `Level ${s.level} · ${s.level_name}` : "";
    renderLadder(s.level || 0);
    $("#topic-list").innerHTML = Array.from({ length: total }, (_, k) => {
      const n = k + 1, cls = s.done || n < idx ? "done" : n === idx ? "active" : "";
      return `<div class="topic-item ${cls}"><span class="topic-index">${cls === "done" ? "✓" : n}</span><div>${escapeHtml(seenTopics[n] || "Topic " + n)}<small>${cls === "done" ? "Explored" : cls === "active" ? "We're talking about it" : "To explore"}</small></div></div>`;
    }).join("");
    if (s.transcript) addEntry(info && info.student_name || "Your team", s.transcript, true);
    tick();
  }

  function tick() {
    if (!step) return;
    const rec = $("#cam-timer"); if (rec) rec.textContent = mmss((step.elapsed_s || 0) + (performance.now() - stepAt) / 1000);
    const el = (step.elapsed_s || 0) + (performance.now() - stepAt) / 1000;
    const dur = step.duration_s || (info && info.duration_s) || 600;
    const t = $("#timer");
    t.textContent = `${mmss(el)} / ${mmss(dur)}`;
    t.classList.toggle("late", el > dur);
  }

  function answerUI(mode) { // input | thinking | error | off
    if (mode === "thinking") setMic("off", "Pythagoras is thinking…");
    processing(mode === "thinking");
    $("#answer-input").classList.toggle("hidden", mode !== "input");
    $("#answer-thinking").classList.toggle("hidden", mode !== "thinking");
    $("#answer-error").classList.toggle("hidden", mode !== "error");
  }

  async function ask(s) {
    renderStep(s);
    finalText = ""; firstSpeechAt = null;
    $("#answer").value = "";
    answerUI("input");
    setDone(true);
    setMic("off", "Pythagoras is speaking…");
    addEntry("Pythagoras", s.question, false);
    await ackDone;
    await speak(s.question);
    if (s.done) { finish(); return; }
    setDone(false);
    startListening();
  }

  function finish() {
    stopListening();
    if (lv) { lv.close(); lv = null; }
    closeCamera();
    clearInterval(timerId);
    show("done");
  }

  // ---------- answering ----------
  async function send(payload) {
    pending = payload = { ...payload, turn: step.turn };   // which question this answers: a resend can't land on the next one
    stopListening();
    answerUI("thinking");
    try {
      const next = await api(`/api/interview/${encodeURIComponent(token)}/answer`, { method: "POST", body: payload });
      pending = null;
      await ask(next);
    } catch (e) {
      if (e.status === 409) { $("#answer-error-msg").textContent = " The interview state changed on the server. Reload the page to continue."; }
      else $("#answer-error-msg").textContent = " " + e.message;
      answerUI("error");
    }
  }

  function done() {
    if ($("#done-btn").disabled || $("#answer-input").classList.contains("hidden")) return;
    const text = $("#answer").value.trim();
    if (lv && !typing && !text) {                      // spoken answer: close the turn now, the transcript follows
      manualFinish = true;
      if (lv.finish()) { setMic("off", "Got it…"); processing(true); return; }
      manualFinish = false;
    }
    if (!text && !confirm("Send an empty answer (counts as 'I don't know')?")) return;
    const duration_s = firstSpeechAt != null ? Math.round((performance.now() - firstSpeechAt) / 100) / 10 : null;
    send({ text, duration_s });
  }

  $("#end-btn").onclick = async () => {
    if (!confirm("End the interview now? Your answers so far (including this one) are saved and sent to your professor.")) return;
    const btn = $("#end-btn");
    setBusy(btn, true, "Saving…");
    try {
      const last = await api(`/api/interview/${encodeURIComponent(token)}/finish`, { method: "POST", body: pending || { text: $("#answer").value.trim(), duration_s: null, turn: step && step.turn } });
      pending = null;
      await ask(last);
    } catch (e) {
      setBusy(btn, false);
      toast(e.message, "error");
    }
  };

  $("#done-btn").onclick = done;
  $("#send-btn").onclick = done;
  $("#end-top").onclick = () => $("#end-btn").onclick();
  $("#toggle-transcript").onclick = () => {
    const hidden = $("#transcript").classList.toggle("hidden");
    $("#toggle-transcript").textContent = hidden ? "Show transcript ⌄" : "Hide transcript ⌃";
  };
  $("#answer").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); done(); } });
  $("#retry-btn").onclick = () => pending && send(pending);
  $("#edit-btn").onclick = () => { answerUI("input"); if (pending) $("#answer").value = pending.text; finalText = $("#answer").value; pending = null; };
  $("#mode-btn").onclick = () => setTyping(!typing);
  $("#repeat-btn").onclick = async () => {
    if (!step || $("#answer-input").classList.contains("hidden")) return;   // not while an answer is being judged
    const wasListening = listening; stopListening();
    setDone(true); setMic("off", "Pythagoras is speaking…");
    await speak(step.question);
    setDone(false);
    if (wasListening || !typing) startListening(); else setMic("off", "Type your answer below");
  };
  $("#answer").addEventListener("input", () => { finalText = $("#answer").value; });
  document.addEventListener("keydown", e => {
    if (e.code !== "Space" || $("#s-live").classList.contains("hidden")) return;
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "BUTTON") return;
    e.preventDefault(); done();
  });

  // ---------- intro: mic check ----------
  let testStream = null, testRec = null, testRaf = null;
  function stopMicTest() {
    if (testRaf) cancelAnimationFrame(testRaf);
    if (testStream) testStream.getTracks().forEach(t => t.stop());
    if (testRec) { try { testRec.stop(); } catch { } }
    testStream = testRec = testRaf = null;
  }
  $("#mic-test").onclick = async () => {
    stopMicTest();
    try {
      testStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const an = ctx.createAnalyser(); an.fftSize = 512;
      ctx.createMediaStreamSource(testStream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      const loop = () => {
        an.getByteTimeDomainData(buf);
        let sum = 0; for (const v of buf) sum += (v - 128) ** 2;
        const rms = Math.sqrt(sum / buf.length);
        $("#mic-meter").style.width = Math.min(100, rms * 4) + "%";
        testRaf = requestAnimationFrame(loop);
      };
      loop();
      $("#mic-msg").textContent = "Microphone on. Say something…";
      if (SR) {
        testRec = new SR(); testRec.lang = LANG(); testRec.interimResults = true; testRec.continuous = true;
        testRec.onresult = e => { const r = e.results[e.results.length - 1]; $("#mic-heard").textContent = `✓ We hear you: "${r[0].transcript.trim()}"`; };
        try { testRec.start(); } catch { }
      }
      setTimeout(() => { stopMicTest(); $("#mic-meter").style.width = "0"; $("#mic-msg").textContent = $("#mic-heard").textContent ? "Microphone works." : "Check finished. If the bar moved, you're good."; }, 8000);
    } catch (e) {
      $("#mic-msg").textContent = "We couldn't access your microphone. Allow it in the browser bar, or type your answers.";
    }
  };

  $("#start-btn").onclick = async () => {
    stopMicTest();
    const btn = $("#start-btn");
    setBusy(btn, true, "Starting…");
    // a user gesture unlocks audio: AudioContext for the local voice, speechSynthesis on Safari/iOS
    let connecting = Promise.resolve();
    if (info.voice === "local" && window.AudioWorkletNode && !lv) {
      const voice = new LocalVoice(token, localHandlers);
      voice.unlock();
      connecting = connectLocalVoice(voice);
    } else if (canSpeak) { const warm = new SpeechSynthesisUtterance(""); speechSynthesis.speak(warm); }
    try {
      const [first] = await Promise.all([api(`/api/interview/${encodeURIComponent(token)}/start`, { method: "POST" }), connecting]);
      if (info.camera && !cam) openCamera();           // after the start: its "not available" flag needs a running interview
      pickVoice();
      show("live");
      setTyping(!SR && !lv);
      timerId = setInterval(tick, 500);
      await ask(first);
    } catch (e) {
      setBusy(btn, false);
      if (e.status === 409) { load(); return; }
      toast(e.message, "error");
    }
  };

  // ---------- boot ----------
  $("#error-retry").onclick = async () => {
    if (!info || info.status !== "error") { location.reload(); return; }
    const btn = $("#error-retry");
    setBusy(btn, true, "Retrying…");
    try {
      await api(`/api/interview/${encodeURIComponent(token)}/retry`, { method: "POST" });
      info = null;
      load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(btn, false);
    }
  };

  async function load() {
    try {
      info = await api(`/api/interview/${encodeURIComponent(token)}`);
    } catch (e) {
      $("#error-msg").textContent = e.status === 404 ? "This interview link isn't valid. Check the link in your email." : e.message;
      show("error"); return;
    }
    if (info.status === "preparing") { show("preparing"); setTimeout(load, 3000); return; }
    if (info.status === "error") { $("#error-msg").textContent = info.error || "We couldn't prepare your interview. Please contact your professor."; show("error"); return; }
    if (info.status === "completed") { show("completed"); return; }
    $("#intro-hello").textContent = info.student_name ? `Hi ${info.student_name}, ready when you are.` : "Ready when you are.";
    $("#intro-assignment").textContent = info.assignment_title || "";
    $("#intro-minutes").textContent = Math.round((info.duration_s || 600) / 60);
    $("#no-speech-note").classList.toggle("hidden", !!SR || info.voice === "local");
    $("#cam-rule").classList.toggle("hidden", !info.camera);
    $("#cam-tile").classList.toggle("hidden", !info.camera);
    $("#room-title").textContent = info.assignment_title || "Your project";
    $("#mode-tag").textContent = info.voice === "local" ? "NATURAL VOICE · ON THIS MAC" : "BROWSER VOICE";
    if (info.status === "in_progress") $("#start-btn").innerHTML = "Continue the interview <span>↗</span>";
    show("intro");
  }
  let hiddenAt = null;
  document.addEventListener("visibilitychange", () => {  // a review prompt for the professor, never a sanction
    if ($("#s-live").classList.contains("hidden")) return;
    if (document.hidden) { hiddenAt = performance.now(); return; }
    if (hiddenAt == null) return;
    const s = Math.round((performance.now() - hiddenAt) / 1000); hiddenAt = null;
    if (s >= 2) flag(`Left the interview tab for ${s} s`);
  });
  window.addEventListener("beforeunload", e => {
    if (!$("#s-live").classList.contains("hidden")) { e.preventDefault(); e.returnValue = ""; }
  });
  load();
})();
