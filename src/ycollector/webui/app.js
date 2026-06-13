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

// ── 분석(전사·요약) 보드 ───────────────────────────────────────────────
// 백엔드: GET /api/analysis/overview, POST /api/analyze, POST /api/album,
//         GET /files/{root}/{rel}  (산출물·앨범 서빙)
const anBoard = $("an-board"), anGo = $("an-go"), anRefresh = $("an-refresh");
const anLang = $("an-lang"), anSum = $("an-sum"), anBudget = $("an-budget");
const anSel = new Map();  // "root|rel" → {root, folderRel, rel, name}

function anUpdateGo() {
    anGo.disabled = anSel.size === 0;
    anGo.textContent = `선택 ${anSel.size}개 분석`;
}

function fileUrl(rootKey, rel) {
    // 경로 세그먼트별 인코딩 — '/' 는 살리고 한글/공백/특수문자는 인코딩.
    return `/files/${rootKey}/` + rel.split("/").map(encodeURIComponent).join("/");
}

async function loadAnalysisBoard() {
    let data;
    try {
        const r = await fetch("/api/analysis/overview");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        data = await r.json();
    } catch (e) {
        anBoard.innerHTML = `<p class="empty">분석 보드 로드 실패: ${escapeHtml(e.message)}</p>`;
        return;
    }
    anBoard.innerHTML = "";
    const validKeys = new Set();  // 재렌더 후에도 살아 있는 파일 키들.
    let total = 0;
    for (const root of data.roots || []) {
        for (const folder of root.folders || []) {
            total++;
            anBoard.appendChild(renderFolder(root, folder, validKeys));
        }
    }
    // 잡 완료로 보드가 자동 갱신돼도 사용자의 체크 선택은 보존하고,
    // 사라진 파일의 선택만 정리한다.
    for (const k of [...anSel.keys()]) if (!validKeys.has(k)) anSel.delete(k);
    anUpdateGo();
    if (!total) {
        anBoard.innerHTML =
            '<p class="empty">다운로드 폴더에 미디어 파일이 없습니다. 위에서 먼저 다운로드하세요.</p>';
    }
}

function renderFolder(root, folder, validKeys) {
    const box = document.createElement("div");
    box.className = "an-folder";

    // ── 헤더: 폴더명 + 분석 현황 + 앨범 링크/버튼 ──
    const head = document.createElement("div");
    head.className = "an-folder-head";
    const links = [];
    if (folder.album)
        links.push(`<a class="btn btn-outline btn-sm" target="_blank" href="${fileUrl(root.key, folder.album)}">앨범 보기</a>`);
    for (const st of folder.standalones || [])
        links.push(`<a class="btn btn-outline btn-sm" target="_blank" href="${fileUrl(root.key, st)}" title="${escapeHtml(st)}">단일 합본</a>`);
    head.innerHTML = `
        <h3 title="${escapeHtml(root.path)}">${escapeHtml(folder.name)}</h3>
        <span class="counter">${folder.analyzed}/${folder.items.length} 분석됨</span>
        <span class="an-links">${links.join("")}</span>`;
    if (folder.summarized > 0) {  // 앨범은 summary.md 가 있어야 생성 가능.
        const albumBtn = document.createElement("button");
        albumBtn.className = "btn btn-outline btn-sm";
        albumBtn.textContent = folder.album ? "앨범 갱신" : "앨범 생성";
        albumBtn.title = "ffmpeg 장면 캡쳐 + HTML 앨범북 생성";
        albumBtn.addEventListener("click", async () => {
            albumBtn.disabled = true;
            try {
                const r = await fetch("/api/album", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ root: root.key, dir: folder.rel }),
                });
                if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
                const j = await r.json();
                trackJob(j.job_id, "album", `앨범 — ${folder.name}`);
            } catch (e) {
                alert(`앨범 생성 시작 실패: ${e.message}`);
            } finally {
                albumBtn.disabled = false;
            }
        });
        head.querySelector(".an-links").appendChild(albumBtn);
    }
    box.appendChild(head);

    // ── 파일 목록: 체크박스(분석 대상 선택) + 요약/대본 뷰어 ──
    const ul = document.createElement("ul");
    ul.className = "an-items";
    for (const it of folder.items) {
        const li = document.createElement("li");
        const key = `${root.key}|${it.rel}`;
        validKeys.add(key);
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = anSel.has(key);  // 재렌더에도 선택 유지.
        cb.title = it.summary ? "다시 분석(덮어씀)" : "분석 대상으로 선택";
        cb.addEventListener("change", () => {
            if (cb.checked) anSel.set(key, { root: root.key, folderRel: folder.rel, rel: it.rel, name: it.name });
            else anSel.delete(key);
            anUpdateGo();
        });
        li.appendChild(cb);

        const name = document.createElement("span");
        name.className = "text";
        name.title = it.rel;
        name.textContent = it.name;
        li.appendChild(name);

        const badges = document.createElement("span");
        badges.className = "an-badges";
        if (it.summary) badges.appendChild(docButton("요약", root.key, it.summary, it.name));
        if (it.script) badges.appendChild(docButton("대본", root.key, it.script, it.name));
        if (!it.summary && !it.script) {
            const miss = document.createElement("span");
            miss.className = "an-miss";
            miss.textContent = "미분석";
            badges.appendChild(miss);
        }
        li.appendChild(badges);
        ul.appendChild(li);
    }
    box.appendChild(ul);
    return box;
}

function docButton(label, rootKey, rel, title) {
    const b = document.createElement("button");
    b.className = "an-doc";
    b.textContent = label;
    b.addEventListener("click", () => openDoc(fileUrl(rootKey, rel), `${label} — ${title}`));
    return b;
}

// 선택한 파일들로 분석 시작 — 폴더(root|folderRel) 단위로 묶어 1폴더 = 1잡.
anGo.addEventListener("click", async () => {
    if (anSel.size === 0) return;
    const groups = new Map();
    for (const v of anSel.values()) {
        const gk = `${v.root}|${v.folderRel}`;
        if (!groups.has(gk)) groups.set(gk, { root: v.root, files: [], names: [] });
        groups.get(gk).files.push(v.rel);
        groups.get(gk).names.push(v.name);
    }
    // Claude 키는 서버 .env 의 ANTHROPIC_API_KEY — 없으면 서버가
    // '요약 비활성' 메시지를 보내고 전사만 진행한다(여기서 막지 않음).
    const summarize = anSum.checked;
    for (const g of groups.values()) {
        try {
            const r = await fetch("/api/analyze", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    root: g.root,
                    files: g.files,
                    language: anLang.value,
                    summarize,
                    budget_usd: parseFloat(anBudget.value) || 5,
                }),
            });
            if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
            const j = await r.json();
            const label = g.files.length === 1 ? g.names[0] : `${g.names[0]} 외 ${g.files.length - 1}개`;
            trackJob(j.job_id, "analyze", label);
        } catch (e) {
            alert(`분석 시작 실패: ${e.message}`);
        }
    }
    anSel.clear(); anUpdateGo();
    loadAnalysisBoard();  // 체크박스 초기화.
});
anRefresh.addEventListener("click", loadAnalysisBoard);
loadAnalysisBoard();

// ── markdown 뷰어 모달 (요약/대본 — summary.md/script.md 전용 미니 렌더러) ──
const mdModal = $("md-modal"), mdBody = $("md-body"), mdTitle = $("md-title"), mdOpen = $("md-open");

function mdToHtml(md) {
    const inline = (s) => escapeHtml(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    let html = "", inList = false;
    const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
    for (const ln of md.split(/\r?\n/)) {
        if (/^###\s/.test(ln))      { closeList(); html += `<h3>${inline(ln.slice(4))}</h3>`; }
        else if (/^##\s/.test(ln))  { closeList(); html += `<h2>${inline(ln.slice(3))}</h2>`; }
        else if (/^#\s/.test(ln))   { closeList(); html += `<h1>${inline(ln.slice(2))}</h1>`; }
        else if (/^---+$/.test(ln.trim())) { closeList(); html += "<hr>"; }
        else if (/^-\s/.test(ln))   { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(ln.slice(2))}</li>`; }
        else if (!ln.trim())        { closeList(); }
        else                        { closeList(); html += `<p>${inline(ln)}</p>`; }
    }
    closeList();
    return html;
}

async function openDoc(url, title) {
    let text;
    try {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        text = await r.text();
    } catch (e) {
        alert(`문서 로드 실패: ${e.message}`);
        return;
    }
    mdTitle.textContent = title;
    mdOpen.href = url;
    mdBody.innerHTML = mdToHtml(text);
    mdBody.scrollTop = 0;
    mdModal.hidden = false;
}
$("md-close").addEventListener("click", () => { mdModal.hidden = true; });
mdModal.addEventListener("click", (e) => { if (e.target === mdModal) mdModal.hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") mdModal.hidden = true; });

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
                <span class="kind ${kind}">${{ download: "DL", generate: "GEN", analyze: "분석", album: "앨범" }[kind] || kind}</span>
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
            mid.textContent = [msg.message, msg.out_path].filter(Boolean).join(" → ") || "완료";
            es.close();
            // 분석/앨범 완료 → 보드의 요약·대본·앨범 링크 갱신.
            if (kind === "analyze" || kind === "album") loadAnalysisBoard();
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
