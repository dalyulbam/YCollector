// YCollector webui — vanilla JS. 빌드 단계 없음.
// 백엔드 API: server.py 의 FastAPI 라우트와 1:1.

(() => {
"use strict";

// ── theme ───────────────────────────────────────────────────────────────
function applyTheme() {
    const saved = localStorage.getItem("theme");
    const sysDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const dark = saved ? saved === "dark" : sysDark;
    document.documentElement.classList.toggle("dark", dark);
}
applyTheme();
document.getElementById("themeToggle")?.addEventListener("click", () => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
});

// ── 가격표 (server / generator/base.py 와 동기화) ──────────────────────
const SORA_PRICING = {
    "sora-2|720p":      0.10,
    "sora-2|1080p":     0.20,
    "sora-2-pro|720p":  0.30,
    "sora-2-pro|1024p": 0.50,
    "sora-2-pro|1080p": 0.70,
};
function resolutionBucket(size) {
    const m = /(\d+)x(\d+)/i.exec(size || "");
    if (!m) return "720p";
    const h = parseInt(m[2], 10);
    if (h >= 1080) return "1080p";
    if (h >= 1024) return "1024p";
    return "720p";
}
function perSecond(model, size) {
    const k = `${model}|${resolutionBucket(size)}`;
    return SORA_PRICING[k] ?? Math.max(...Object.values(SORA_PRICING));
}

// ── DOM refs ────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const dlUrl = $("dl-url"), dlGo = $("dl-go"), dlPaste = $("dl-paste"), dlPlaylist = $("dl-playlist");
const refInput = $("ref-input"), refAdd = $("ref-add"), refList = $("ref-list"), refCount = $("ref-count");
const refDrop = $("ref-drop"), refFile = $("ref-file"), chainNote = $("chain-note");
const promptInput = $("prompt-input"), promptAdd = $("prompt-add"), promptList = $("prompt-list"), promptCount = $("prompt-count");
const genModel = $("gen-model"), genSize = $("gen-size"), genSeconds = $("gen-seconds");
const genEst = $("gen-est"), genEstDetail = $("gen-est-detail"), genGo = $("gen-go"), genWarn = $("gen-warn");
const jobsEl = $("jobs");
const badgeKey = $("badge-key"), badgeCookies = $("badge-cookies");

// ── state ──────────────────────────────────────────────────────────────
const refs = [];
const prompts = [];
const jobs = new Map();  // id → { row, eventSource }

// ── health ──────────────────────────────────────────────────────────────
async function refreshHealth() {
    try {
        const r = await fetch("/api/health");
        const j = await r.json();
        badgeKey.textContent = `key: ${j.openai_api_key ? "ok" : "missing"}`;
        badgeKey.className = `badge ${j.openai_api_key ? "ok" : "miss"}`;
        badgeCookies.textContent = `cookies: ${j.cookies_present ? "ok" : "—"}`;
        badgeCookies.className = `badge ${j.cookies_present ? "ok" : "warn"}`;
    } catch (e) {
        badgeKey.textContent = "key: ?";
        badgeKey.className = "badge miss";
    }
}
refreshHealth();

// ── lists rendering ────────────────────────────────────────────────────
function renderRefs() {
    refList.innerHTML = "";
    refs.forEach((r, i) => {
        const kindLabel = r.kind === "video" ? "영상" : r.kind === "image" ? "이미지" : "URL";
        const li = document.createElement("li");
        li.innerHTML = `
            <span class="idx">${i + 1}</span>
            <span class="kmini">${kindLabel}</span>
            <span class="text" title="${escapeHtml(r.value || r.label)}">${escapeHtml(r.label)}</span>
            <button class="btn-danger-ghost" data-i="${i}" aria-label="제거" title="제거">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5">
                    <line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/>
                </svg>
            </button>`;
        li.querySelector("button").addEventListener("click", (e) => {
            refs.splice(parseInt(e.currentTarget.dataset.i, 10), 1);
            renderRefs(); updateEstimate();
        });
        refList.appendChild(li);
    });
    refCount.textContent = String(refs.length);
}
function renderPrompts() {
    promptList.innerHTML = "";
    prompts.forEach((txt, i) => {
        const li = document.createElement("li");
        li.innerHTML = `
            <span class="idx">${i + 1}</span>
            <span class="text" title="${escapeHtml(txt)}">${escapeHtml(txt)}</span>
            <button class="btn-danger-ghost" data-i="${i}" aria-label="제거" title="제거">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5">
                    <line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/>
                </svg>
            </button>`;
        li.querySelector("button").addEventListener("click", (e) => {
            prompts.splice(parseInt(e.currentTarget.dataset.i, 10), 1);
            renderPrompts(); updateEstimate();
        });
        promptList.appendChild(li);
    });
    promptCount.textContent = String(prompts.length);
}

function updateEstimate() {
    const secs = parseInt(genSeconds.value, 10);
    const chain = (prompts.length >= 2) || (refs.length >= 2);
    const n = (prompts.length >= 2) ? prompts.length : Math.max(1, refs.length);
    const per = perSecond(genModel.value, genSize.value) * secs;
    const total = per * n;
    genEst.textContent = `$${total.toFixed(2)}`;
    genEstDetail.textContent = chain ? `${n} 세그먼트 × $${per.toFixed(2)} → 1 영상` : `1 × $${per.toFixed(2)}`;
    if (chainNote) {
        chainNote.hidden = !chain;
        if (chain) chainNote.textContent = `연속형 ON — ${n}개 세그먼트를 이어 1개 영상(약 ${n * secs}초). 첫 reference가 시작 앵커.`;
    }
    const hasPrompt = prompts.length > 0;
    const pending = refs.some(r => !r.value);
    genGo.disabled = !hasPrompt || pending;
    genWarn.textContent = !hasPrompt
        ? "프롬프트를 1개 이상 추가하세요."
        : pending ? "업로드 진행 중…"
        : (total > 5 ? `⚠ 견적 $${total.toFixed(2)} — 확인 후 진행됩니다.` : "");
}
[genModel, genSize, genSeconds].forEach(el => el.addEventListener("change", updateEstimate));

// ── adders ─────────────────────────────────────────────────────────────
function addRef() {
    const v = refInput.value.trim();
    if (!v) return;
    refs.push({ value: v, label: v, kind: "url" });
    refInput.value = ""; renderRefs(); updateEstimate();
    refInput.focus();
}

// 로컬 파일 업로드 → /api/upload → refs 에 서버 경로로 추가.
async function uploadFiles(fileList) {
    for (const file of Array.from(fileList || [])) {
        const item = {
            value: "", label: `${file.name} (업로드 중…)`,
            kind: (file.type || "").startsWith("video") ? "video" : "image",
        };
        refs.push(item); renderRefs(); updateEstimate();
        try {
            const fd = new FormData(); fd.append("file", file);
            const r = await fetch("/api/upload", { method: "POST", body: fd });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const j = await r.json();
            item.value = j.path; item.label = j.name || file.name; item.kind = j.kind;
        } catch (e) {
            const idx = refs.indexOf(item);
            if (idx >= 0) refs.splice(idx, 1);
            alert(`업로드 실패 (${file.name}): ${e.message}`);
        }
        renderRefs(); updateEstimate();
    }
}
function addPrompt() {
    const v = promptInput.value.trim();
    if (!v) return;
    prompts.push(v); promptInput.value = ""; renderPrompts(); updateEstimate();
    promptInput.focus();
}
refAdd.addEventListener("click", addRef);
promptAdd.addEventListener("click", addPrompt);
refInput.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addRef(); } });
promptInput.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addPrompt(); } });

// 파일 업로드 (선택 + 드래그드롭)
refDrop.addEventListener("click", () => refFile.click());
refDrop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); refFile.click(); } });
refFile.addEventListener("change", () => { uploadFiles(refFile.files); refFile.value = ""; });
["dragenter", "dragover"].forEach(ev => refDrop.addEventListener(ev, e => { e.preventDefault(); refDrop.classList.add("drag"); }));
refDrop.addEventListener("dragleave", e => { e.preventDefault(); refDrop.classList.remove("drag"); });
refDrop.addEventListener("drop", e => {
    e.preventDefault(); refDrop.classList.remove("drag");
    if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
});

// ── download ───────────────────────────────────────────────────────────
let playlistAll = false;
const dlPlaylistLabel = $("dl-playlist-label");
dlPlaylist.addEventListener("click", () => {
    playlistAll = !playlistAll;
    dlPlaylist.setAttribute("aria-pressed", String(playlistAll));
    dlPlaylist.classList.toggle("btn-active", playlistAll);
    dlPlaylist.classList.toggle("btn-outline", !playlistAll);
    dlPlaylist.title = playlistAll ? "재생목록 전체 받기 (ON)" : "클릭하면 재생목록 전체 다운로드";
    dlPlaylistLabel.textContent = playlistAll ? "전체" : "단일";
});
dlPaste.addEventListener("click", async () => {
    try {
        const t = await navigator.clipboard.readText();
        if (t) dlUrl.value = t.trim();
    } catch (e) { /* ignore */ }
});
async function startDownload() {
    const url = dlUrl.value.trim();
    if (!url) return;
    const body = { url };
    if (playlistAll) body.playlist_mode = "expand";
    try {
        const r = await fetch("/api/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        trackJob(j.job_id, "download", url);
        dlUrl.value = "";
    } catch (e) {
        alert(`다운로드 시작 실패: ${e.message}`);
    }
}
dlGo.addEventListener("click", startDownload);
dlUrl.addEventListener("keydown", e => { if (e.key === "Enter") startDownload(); });

// ── generate ───────────────────────────────────────────────────────────
genGo.addEventListener("click", async () => {
    if (prompts.length === 0) return;
    if (refs.some(r => !r.value)) { alert("업로드가 끝나지 않은 reference 가 있습니다. 잠시 후 다시 시도하세요."); return; }
    const secs = parseInt(genSeconds.value, 10);
    const chain = (prompts.length >= 2) || (refs.length >= 2);
    const n = (prompts.length >= 2) ? prompts.length : Math.max(1, refs.length);
    const total = perSecond(genModel.value, genSize.value) * secs * n;
    const msg = chain
        ? `연속형: ${n}개 세그먼트를 이어 1개 영상(약 ${n * secs}초).\n예상 $${total.toFixed(2)} 진행할까요?`
        : `예상 $${total.toFixed(2)} 진행할까요?`;
    if (total > 1 && !confirm(msg)) return;
    try {
        const r = await fetch("/api/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                prompts,
                references: refs.map(x => x.value),
                model: genModel.value,
                size: genSize.value,
                seconds: parseInt(genSeconds.value, 10),
            }),
        });
        if (!r.ok) {
            const t = await r.text();
            throw new Error(t || `HTTP ${r.status}`);
        }
        const j = await r.json();
        const combinedPrompt = prompts.join(" ");
        if (j.chain) {
            trackJob(j.job_ids[0], "generate", `${combinedPrompt}  (chain ×${refs.length})`);
        } else {
            j.job_ids.forEach((id, i) => {
                trackJob(id, "generate", combinedPrompt + (refs[i] ? `  (ref: ${refs[i].label})` : ""));
            });
        }
    } catch (e) {
        alert(`생성 시작 실패: ${e.message}`);
    }
});

// ── job tracker ────────────────────────────────────────────────────────
function trackJob(jobId, kind, label) {
    // 빈 상태 메시지 제거.
    if (jobsEl.querySelector(".empty")) jobsEl.innerHTML = "";

    const card = document.createElement("div");
    card.className = "job-card";
    card.dataset.jobId = jobId;
    card.innerHTML = `
        <div>
            <div class="head">
                <span class="kind ${kind}">${kind === "generate" ? "GEN" : "DL"}</span>
                <span class="title-line" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
            </div>
            <div class="meta-line">id: ${jobId} · <span class="meta-mid">대기 중</span></div>
            <div class="progress"><div></div></div>
            <div class="error-line"></div>
        </div>
        <div><span class="status-tag run">queued</span></div>`;
    jobsEl.prepend(card);

    const tag = card.querySelector(".status-tag");
    const bar = card.querySelector(".progress > div");
    const mid = card.querySelector(".meta-mid");
    const errLine = card.querySelector(".error-line");

    const es = new EventSource(`/api/jobs/${jobId}/events`);
    jobs.set(jobId, { card, es });

    es.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        const e = msg.event;
        if (e === "snapshot" || e === "status") {
            applyStatus(msg.status, tag);
            if (msg.message) mid.textContent = msg.message;
        } else if (e === "meta") {
            mid.textContent = `${msg.title || ""}  ${msg.channel ? "· " + msg.channel : ""}  ${msg.duration ? "· " + msg.duration : ""}`;
        } else if (e === "progress") {
            if (typeof msg.progress === "number") bar.style.width = (msg.progress * 100).toFixed(1) + "%";
            if (msg.status) applyStatus(msg.status, tag);
            const parts = [];
            if (msg.speed) parts.push(humanBytes(msg.speed) + "/s");
            if (msg.eta != null) parts.push("ETA " + msg.eta + "s");
            if (msg.message) parts.push(msg.message);
            if (parts.length) mid.textContent = parts.join(" · ");
        } else if (e === "estimate") {
            mid.textContent = `예상 $${(msg.cost_usd || 0).toFixed(2)}`;
        } else if (e === "done") {
            bar.style.width = "100%";
            applyStatus("done", tag);
            mid.textContent = msg.out_path || "완료";
            es.close();
        } else if (e === "error") {
            applyStatus("failed", tag);
            errLine.textContent = `[${msg.category || "?"}] ${msg.message || "실패"}`;
            es.close();
        } else if (e === "cancelled") {
            applyStatus("cancelled", tag);
            es.close();
        }
    };
    es.onerror = () => {
        applyStatus("offline", tag);
    };
}

function applyStatus(s, tag) {
    tag.textContent = s;
    tag.className = "status-tag";
    if (s === "done") tag.classList.add("ok");
    else if (s === "failed") tag.classList.add("fail");
    else if (s === "cancelled") tag.classList.add("warn");
    else tag.classList.add("run");
}

function humanBytes(n) {
    if (!isFinite(n) || n <= 0) return "—";
    const u = ["B","KB","MB","GB"];
    let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}
function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
        ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#39;" }[c]));
}

// 초기 estimate
updateEstimate();
})();
