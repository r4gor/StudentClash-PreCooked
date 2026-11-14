(() => {
  const id = pathPart(2);
  const OWN = { strong: ["Strong ownership", "green"], mixed: ["Mixed ownership", "amber"], weak: ["Weak ownership", "red"] };
  const VERDICT = { solid: ["Solid", "green"], partial: ["Partial", "amber"], weak: ["Weak", "red"], dont_know: ["Didn't know", "red"], off_topic: ["Off topic", "amber"] };
  const KIND = { opening: "Opening", ask: "Question", probe: "Follow-up", scaffold: "Hint", next_topic: "New topic", wrap_up: "Closing" };
  let pollId = null;

  function bloomBar(level) {
    const segs = [1, 2, 3, 4, 5, 6].map(l => `<div class="seg ${l <= level ? "on l" + l : ""}"></div>`).join("");
    const labels = [1, 2, 3, 4, 5, 6].map(l => `<span class="${l === level ? "on" : ""}">${BLOOM[l]}</span>`).join("");
    return `<div class="bloom" aria-label="Bloom level ${level}">${segs}</div><div class="bloom-labels">${labels}</div>`;
  }

  function list(items, good) {
    if (!items || !items.length) return `<p class="faint small">None noted.</p>`;
    return `<ul class="clean">${items.map(t => `<li><span class="mk ${good ? "good" : "bad"}">${good ? "✓" : "!"}</span><span>${escapeHtml(t)}</span></li>`).join("")}</ul>`;
  }

  function anchoredList(items, good) {
    if (!items || !items.length) return `<p class="faint small">None noted.</p>`;
    return `<ul class="clean">${items.map(t => `<li><span class="mk ${good ? "good" : "bad"}">${good ? "✓" : "!"}</span><span>${escapeHtml(t.text)} ${t.anchor ? `<span class="anchor">${escapeHtml(t.anchor)}</span>` : ""}</span></li>`).join("")}</ul>`;
  }

  function topicTitle(plan, tid) {
    const t = plan && (plan.topics || []).find(x => x.id === tid);
    return t ? t.title : tid;
  }

  function render(d) {
    const s = d.submission || {}, a = d.assignment || {}, r = d.report, plan = d.plan;
    document.title = `Pythagoras · ${s.student_name || "Report"}`;
    const crumbA = $("#crumb-a");
    crumbA.textContent = a.title || "Assignment";
    crumbA.href = `/prof/assignments/${encodeURIComponent(a.id ?? s.assignment_id ?? "")}`;
    $("#crumb-s").textContent = s.student_name || "";

    const own = r ? OWN[r.ownership] : null;
    let html = `<div class="report-head">
      <div>
        <div class="eyebrow">AI assessment report${a.title ? " · " + escapeHtml(a.title) : ""}</div>
        <h1>${escapeHtml(s.student_name || "Team")}</h1>
        <div class="meta"><span>✉ ${escapeHtml(s.email || "")}</span><span>Completed ${fmtDate(s.completed_at)}</span>${own ? `<span class="pill ${own[1]}">${own[0]}</span>` : ""}</div>
      </div>
      <div class="no-print" style="display:flex;gap:8px"><button class="btn" onclick="window.print()">🖨 Print / PDF</button></div>
    </div>`;

    if (!r) {
      const WAITING = {                                  // no report yet because there is no finished interview yet
        preparing: ["Preparing the interview", "Pythagoras is reading the deliverable and planning the questions."],
        ready: ["Interview not started", "The team has its invite link but hasn't started the conversation yet."],
        in_progress: ["Interview in progress", "The team is talking with Pythagoras right now. The report is written when they finish."],
        error: ["Interview could not be prepared", s.error || "The preparation failed. The team can press Try again on its interview page."],
      }[s.status];
      if (WAITING) {
        html += `<div class="card center-state"><div class="icon">${s.status === "error" ? "⚠️" : "⏳"}</div><h2>${WAITING[0]}</h2><p class="muted">${escapeHtml(WAITING[1])}</p></div>`;
        $("#root").innerHTML = html;
        if (!pollId && s.status !== "error") pollId = setInterval(load, 4000);
        return;
      }
      if (s.report_error) {
        const next = s.report_next_at ? new Date(s.report_next_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "soon";
        html += `<div class="card center-state"><div class="icon">⏳</div><h2>Report queued</h2>
          <p class="muted">The interview is complete and saved. The AI provider didn't respond (often a rate limit on the API key), so Pythagoras will retry automatically — next attempt around <strong>${escapeHtml(next)}</strong> (attempt ${s.report_attempts}). Nothing is lost.</p>
          <p class="small faint">${escapeHtml(s.report_error)}</p>
          <button class="btn primary" id="retry-report">Retry now</button></div>`;
      } else {
        html += `<div class="card center-state"><span class="spinner lg"></span><h2 style="margin-top:14px">Report is being generated…</h2><p class="muted">The interview is complete. Pythagoras is writing the report — this page refreshes by itself.</p></div>`;
      }
      $("#root").innerHTML = html;
      const rb = $("#retry-report");
      if (rb) rb.onclick = async () => {
        setBusy(rb, true, "Retrying…");
        try { await api(`/api/prof/submissions/${encodeURIComponent(id)}/report`, { method: "POST" }); load(); }
        catch (e) { toast(e.message, "error"); setBusy(rb, false); }
      };
      if (!pollId) pollId = setInterval(load, 4000);
      return;
    }
    if (pollId) { clearInterval(pollId); pollId = null; }

    html += `<div class="report-grid">`;
    if (r.unverified_quotes && r.unverified_quotes.length) {
      html += `<div class="notice warn"><strong>${r.unverified_quotes.length} quote${r.unverified_quotes.length > 1 ? "s" : ""} could not be matched to the transcript.</strong>Check them against the transcript below before relying on them.</div>`;
    }
    html += `<section class="card"><h2>Summary</h2><p class="lede">${escapeHtml(r.summary)}</p>
      <p class="small faint" style="margin:0">Pythagoras does not grade. This report is a starting point for your oral exam.</p></section>`;

    html += `<section class="card"><h2>Depth per topic</h2><p class="muted small">Highest level of Bloom's taxonomy the team reached with a solid answer.</p>
      ${(r.topics || []).map(t => `<div class="topic-block">
        <div class="t-head"><h3>${escapeHtml(t.title)}</h3><span class="pill accent nodot">${t.level_reached ? `Level ${t.level_reached} · ${BLOOM[t.level_reached] || ""}` : "Not reached"}</span></div>
        ${bloomBar(t.level_reached || 0)}
        <p>${escapeHtml(t.assessment)}</p>
        ${(t.evidence || []).map(ev => `<blockquote class="ev"><div class="q">“${escapeHtml(ev.quote)}”</div><div class="n"><span class="ts">${mmss(ev.t_s)}</span>${escapeHtml(ev.note)}</div></blockquote>`).join("")}
      </div>`).join("") || `<p class="faint">No topics.</p>`}
    </section>`;

    html += `<div class="two-col">
      <section class="card"><h2>Strengths</h2>${list(r.strengths, true)}</section>
      <section class="card"><h2>Concerns</h2>${list(r.concerns, false)}</section>
    </div>`;

    const qs = r.suggested_questions || [];
    html += `<section class="card"><div class="section-title"><h2>Suggested oral-exam questions</h2>${qs.length ? `<button class="btn sm no-print" id="copy-qs">Copy all</button>` : ""}</div>
      ${qs.length ? `<ol class="qs">${qs.map(q => `<li>${escapeHtml(q)}</li>`).join("")}</ol>` : `<p class="faint">None.</p>`}</section>`;

    if (plan && plan.analysis) {
      const an = plan.analysis;
      html += `<section class="card"><h2>Deliverable analysis</h2><p>${escapeHtml(an.summary)}</p>
        <div class="two-col" style="margin-top:14px">
          <div><h3>Strengths</h3>${anchoredList(an.strengths, true)}</div>
          <div><h3>Weak spots</h3>${anchoredList(an.weak_spots, false)}</div>
        </div>
        ${(an.objectives_coverage || []).length ? `<h3 style="margin-top:20px">Learning objectives coverage</h3><ul class="clean">${an.objectives_coverage.map(o => `<li><span class="mk good" style="background:var(--accent-soft);color:var(--accent)">•</span><span>${escapeHtml(o)}</span></li>`).join("")}</ul>` : ""}
      </section>`;
    }

    const turns = d.turns || [];
    html += `<section class="card"><details class="collapse"><summary><h2 style="margin:0">Transcript <span class="faint small" style="font-family:var(--font);font-weight:500">${turns.length} turns</span></h2></summary>
      <div style="margin-top:14px">${turns.map(t => {
        const v = t.evaluation ? VERDICT[t.evaluation.verdict] : null;
        const tt = t.move && t.move.topic_id ? topicTitle(plan, t.move.topic_id) : "";
        return `<div class="turn"><div><span class="ts">${mmss(t.t_s)}</span></div><div>
          <div class="who">Pythagoras · ${escapeHtml(KIND[t.move && t.move.kind] || "")}${tt ? " · " + escapeHtml(tt) : ""}${t.move && t.move.level ? " · L" + t.move.level : ""}</div>
          <div class="gq">${escapeHtml(t.question)}</div>
          <div class="who" style="margin-top:8px">${escapeHtml(s.student_name || "Team")}</div>
          <div class="sa">${t.answer ? escapeHtml(t.answer) : `<span class="faint">(no answer)</span>`}</div>
          ${t.evaluation ? `<div class="ev-note">${v ? `<span class="pill ${v[1]}">${v[0]}</span>` : ""}${escapeHtml(t.evaluation.note || "")}</div>` : ""}
        </div></div>`;
      }).join("") || `<p class="faint">No turns recorded.</p>`}</div></details></section>`;

    const flags = s.flags || [];
    if (flags.length) html += `<section class="card"><h2>Integrity signals <span class="faint small" style="font-family:var(--font);font-weight:500">for review, not a verdict</span></h2>
      <ul class="clean">${flags.map(f => `<li><span class="mk bad">!</span><span>${escapeHtml(f)}</span></li>`).join("")}</ul></section>`;

    const files = s.files || [];
    html += `<section class="card"><h2>Deliverable files</h2>${files.length ? `<div class="file-list">${files.map(f =>
      `<a class="file-link" href="/api/prof/submissions/${encodeURIComponent(s.id ?? id)}/files/${encodeURIComponent(f)}" target="_blank" rel="noopener">📄 ${escapeHtml(f)}</a>`).join("")}</div>` : `<p class="faint">No files.</p>`}</section>`;
    html += `</div>`;

    $("#root").innerHTML = html;
    const cq = $("#copy-qs");
    if (cq) cq.onclick = () => copyText(qs.map((q, i) => `${i + 1}. ${q}`).join("\n"), "Questions copied");
  }

  async function load() {
    try {
      render(await api(`/api/prof/submissions/${encodeURIComponent(id)}`));
    } catch (e) {
      if (e.status === 401) return;
      if (pollId) { clearInterval(pollId); pollId = null; }
      $("#root").innerHTML = `<div class="notice error">${e.status === 404 ? "This report doesn't exist." : "Couldn't load the report: " + escapeHtml(e.message)}</div>`;
    }
  }
  load();
})();
