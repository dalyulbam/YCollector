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
        diarAvailable = !!j.diarize_available;
        applyDiarAvailability();
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

// ── 분석(전사·요약) 보드 — 탐색기식 폴더 네비게이션 ──────────────────────
// 백엔드: GET /api/analysis/browse?root=&rel= (한 폴더씩), POST /api/analyze,
//         POST /api/album, GET /files/{root}/{rel}  (산출물·앨범 서빙)
const anBoard = $("an-board"), anGo = $("an-go"), anRefresh = $("an-refresh");
const anLang = $("an-lang"), anSum = $("an-sum"), anBudget = $("an-budget");
const anDiar = $("an-diar"), anVad = $("an-vad"), anBatched = $("an-batched");
const anSel = new Map();  // "root|rel" → {root, folderRel, rel, name}
let diarAvailable = false;  // /api/health 의 diarize_available — 화자 구분 가용성.

// 분석 공통 옵션(언어·요약·예산·화자 구분·무음 분할·배치) — 세 입력 모드 공통.
function anOptions() {
    const vadMap = {
        standard: null,
        fine: { min_silence_ms: 700, speech_pad_ms: 250 },
        fineous: { min_silence_ms: 300, speech_pad_ms: 150 },
    };
    const o = {
        language: anLang.value,
        summarize: anSum.checked,
        budget_usd: parseFloat(anBudget.value) || 5,
        diarize: !!(anDiar && anDiar.checked && !anDiar.disabled),
    };
    const vad = vadMap[anVad ? anVad.value : "standard"];
    if (vad) o.vad = vad;
    if (anBatched && anBatched.checked) o.batched = true;
    return o;
}

// 화자 구분 가용성(deps 설치 여부)을 UI에 반영 — 체크박스·버튼 활성/비활성.
function applyDiarAvailability() {
    const wrap = $("an-diar-wrap");
    if (anDiar) {
        anDiar.disabled = !diarAvailable;
        if (!diarAvailable) anDiar.checked = false;
    }
    if (wrap) wrap.title = diarAvailable
        ? "전사 후 말하는 사람(화자)을 구분해 대본에 화자 라벨"
        : "화자 구분 비활성 — 서버에서  uv sync --extra diarize --native-tls  설치 필요";
    loadAnalysisBoard();  // 폴더 액션의 '화자 구분' 버튼 가용성도 반영.
}

function anUpdateGo() {
    anGo.disabled = anSel.size === 0;
    anGo.textContent = `선택 ${anSel.size}개 분석`;
}

function fileUrl(rootKey, rel) {
    // 경로 세그먼트별 인코딩 — '/' 는 살리고 한글/공백/특수문자는 인코딩.
    return `/files/${rootKey}/` + rel.split("/").map(encodeURIComponent).join("/");
}

// 탐색기식 네비게이션 상태(현재 루트키 + 폴더 rel).
const anState = { root: "", rel: "" };

async function loadAnalysisBoard() {
    return browseTo(anState.root, anState.rel);  // 현재 위치 새로고침.
}

async function browseTo(root, rel) {
    let data;
    try {
        const qs = new URLSearchParams({ root: root || "", rel: rel || "" });
        const r = await fetch("/api/analysis/browse?" + qs.toString());
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        data = await r.json();
    } catch (e) {
        anBoard.innerHTML = `<p class="empty">분석 보드 로드 실패: ${escapeHtml(e.message)}</p>`;
        return;
    }
    if (anState.root !== data.root || anState.rel !== data.rel) {
        anSel.clear(); anUpdateGo();  // 다른 폴더로 이동 → 선택 초기화.
    }
    anState.root = data.root || "";
    anState.rel = data.rel || "";
    renderBrowser(data);
}

function renderBrowser(data) {
    anBoard.innerHTML = "";

    // 루트 전환 탭(루트가 여럿일 때).
    const roots = data.roots || [];
    if (roots.length > 1) {
        const tabs = document.createElement("div");
        tabs.className = "an-roots";
        for (const rt of roots) {
            const b = document.createElement("button");
            b.className = "an-root-tab" + (rt.key === data.root ? " active" : "");
            b.textContent = rt.name;
            b.addEventListener("click", () => browseTo(rt.key, ""));
            tabs.appendChild(b);
        }
        anBoard.appendChild(tabs);
    }

    // 경로(breadcrumb) + 상위로.
    const bc = document.createElement("div");
    bc.className = "an-crumbs";
    (data.crumbs || []).forEach((c, i, arr) => {
        if (i) {
            const sep = document.createElement("span");
            sep.className = "an-crumb-sep"; sep.textContent = "›";
            bc.appendChild(sep);
        }
        const a = document.createElement("button");
        a.className = "an-crumb" + (i === arr.length - 1 ? " current" : "");
        a.textContent = c.name;
        a.addEventListener("click", () => browseTo(data.root, c.rel));
        bc.appendChild(a);
    });
    if (data.rel) {
        const up = document.createElement("button");
        up.className = "btn btn-outline btn-sm an-up";
        up.textContent = "↑ 상위";
        const parent = data.rel.includes("/") ? data.rel.slice(0, data.rel.lastIndexOf("/")) : "";
        up.addEventListener("click", () => browseTo(data.root, parent));
        bc.appendChild(up);
    }
    anBoard.appendChild(bc);

    // 현재 폴더 액션(앨범 보기/합본/생성).
    const acts = renderFolderActions(data);
    if (acts) anBoard.appendChild(acts);

    // 하위 폴더 그리드(클릭하면 진입).
    if ((data.folders || []).length) {
        const grid = document.createElement("div");
        grid.className = "an-grid";
        for (const f of data.folders) grid.appendChild(renderFolderCard(data.root, f));
        anBoard.appendChild(grid);
    }

    // 이 폴더의 파일들.
    if ((data.items || []).length) {
        const head = document.createElement("div");
        head.className = "an-files-head";
        head.textContent = `파일 ${data.items.length}개`;
        anBoard.appendChild(head);
        const ul = document.createElement("ul");
        ul.className = "an-items";
        for (const it of data.items) ul.appendChild(renderFileRow(data.root, it));
        anBoard.appendChild(ul);
    }

    if (!(data.folders || []).length && !(data.items || []).length && !data.album) {
        const p = document.createElement("p");
        p.className = "empty";
        p.textContent = data.rel
            ? "이 폴더에 표시할 항목이 없습니다."
            : "표시할 미디어가 없습니다. 위에서 다운로드/전사하세요.";
        anBoard.appendChild(p);
    }
}

function renderFolderCard(rootKey, f) {
    const card = document.createElement("button");
    card.className = "an-folder-card";
    const meta = [];
    if (f.media) meta.push(`영상 ${f.analyzed}/${f.media}`);
    if (f.subdirs) meta.push(`폴더 ${f.subdirs}`);
    if (f.album) meta.push("앨범✓");
    card.innerHTML = `
        <span class="an-fc-icon">📁</span>
        <span class="an-fc-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        <span class="an-fc-meta">${escapeHtml(meta.join(" · "))}</span>`;
    card.addEventListener("click", () => browseTo(rootKey, f.rel));
    return card;
}

function renderFolderActions(data) {
    const hasAlbum = !!data.album, stands = data.standalones || [], canMake = data.summarized > 0;
    const transcribed = data.transcribed || 0, diarized = data.diarized || 0;
    if (!hasAlbum && !stands.length && !canMake && transcribed <= 0) return null;
    const bar = document.createElement("div");
    bar.className = "an-folder-actions";
    if (hasAlbum) {
        const a = document.createElement("a");
        a.className = "btn btn-primary btn-sm"; a.target = "_blank";
        a.href = fileUrl(data.root, data.album); a.textContent = "📖 앨범 보기";
        bar.appendChild(a);
    }
    for (const st of stands) {
        const a = document.createElement("a");
        a.className = "btn btn-outline btn-sm"; a.target = "_blank";
        a.href = fileUrl(data.root, st); a.textContent = "단일 합본"; a.title = st;
        bar.appendChild(a);
    }
    if (canMake) {
        const b = document.createElement("button");
        b.className = "btn btn-outline btn-sm";
        b.textContent = hasAlbum ? "앨범 갱신" : "앨범 생성";
        b.title = "ffmpeg 장면 캡쳐 + HTML 앨범북";
        b.addEventListener("click", async () => {
            b.disabled = true;
            try {
                const r = await fetch("/api/album", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ root: data.root, dir: data.rel }),
                });
                if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
                const j = await r.json();
                trackJob(j.job_id, "album", `앨범 — ${data.rel || data.root}`);
            } catch (e) { alert(`앨범 생성 실패: ${e.message}`); }
            finally { b.disabled = false; }
        });
        bar.appendChild(b);
    }
    if (transcribed > 0) {
        const d = document.createElement("button");
        d.className = "btn btn-outline btn-sm";
        const allDone = diarized >= transcribed;
        d.textContent = `🗣 화자 구분${allDone ? " 갱신" : ""}` + ` (${diarized}/${transcribed})`;
        d.disabled = !diarAvailable;
        d.title = diarAvailable
            ? "전사 대본에 화자(말하는 사람) 라벨을 붙입니다 — speechbrain ECAPA"
            : "화자 구분 비활성 — 서버에서  uv sync --extra diarize --native-tls  설치 필요";
        d.addEventListener("click", async () => {
            // 화자 수를 알면 지정 — 긴 인터뷰는 자동 추정이 과분할되기 쉬워(예: 2인 → 16명)
            // 인원 수를 주면 정확해진다. 비우면 자동 추정.
            const raw = prompt("화자 수를 알면 입력하세요 (모르면 비워두면 자동 추정).\n예: 인터뷰=2, 단독 내레이션=1", "");
            if (raw === null) return;  // 취소
            const body = { root: data.root, dir: data.rel };
            const n = parseInt(raw, 10);
            if (Number.isFinite(n) && n > 0) body.num_speakers = n;
            d.disabled = true;
            try {
                const r = await fetch("/api/diarize", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
                const j = await r.json();
                const tag = body.num_speakers ? ` (${body.num_speakers}명)` : "";
                trackJob(j.job_id, "diarize", `화자 구분 — ${data.rel || data.root}${tag}`);
            } catch (e) { alert(`화자 구분 실패: ${e.message}`); d.disabled = false; }
        });
        bar.appendChild(d);
    }
    return bar;
}

function renderFileRow(rootKey, it) {
    const li = document.createElement("li");
    const key = `${rootKey}|${it.rel}`;
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = anSel.has(key);
    cb.title = it.summary ? "다시 분석(덮어씀)" : "분석 대상으로 선택";
    cb.addEventListener("change", () => {
        const folderRel = it.rel.includes("/") ? it.rel.slice(0, it.rel.lastIndexOf("/")) : "";
        if (cb.checked) anSel.set(key, { root: rootKey, folderRel, rel: it.rel, name: it.name });
        else anSel.delete(key);
        anUpdateGo();
    });
    li.appendChild(cb);
    const name = document.createElement("span");
    name.className = "text"; name.title = it.rel; name.textContent = it.name;
    li.appendChild(name);
    const badges = document.createElement("span");
    badges.className = "an-badges";
    if (it.summary) badges.appendChild(docButton("요약", rootKey, it.summary, it.name));
    if (it.script) badges.appendChild(docButton("대본", rootKey, it.script, it.name));
    if (it.html) {
        // 캡쳐뷰: 전사 전문 + 화면 캡쳐 + 타임스탬프의 standalone HTML — 새 탭으로.
        const a = document.createElement("a");
        a.className = "an-doc an-html";
        a.textContent = "🖼 캡쳐뷰";
        a.href = fileUrl(rootKey, it.html);
        a.target = "_blank"; a.rel = "noopener";
        a.title = "전사 전문 + 화면 캡쳐 + 타임스탬프 (새 탭)";
        badges.appendChild(a);
    }
    if (it.diarized) {
        const sp = document.createElement("span");
        sp.className = "an-spk"; sp.textContent = "화자✓";
        sp.title = "화자 구분됨 — 대본에 화자 라벨 포함";
        badges.appendChild(sp);
    }
    if (!it.summary && !it.script) {
        const miss = document.createElement("span");
        miss.className = "an-miss"; miss.textContent = "미분석";
        badges.appendChild(miss);
    }
    li.appendChild(badges);
    return li;
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
    for (const g of groups.values()) {
        try {
            const r = await fetch("/api/analyze", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ root: g.root, files: g.files, ...anOptions() }),
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

// ── 분석 입력 모드 (폴더에서 선택 / URL 입력) ──────────────────────────────
// URL 모드는 다운로드 패널과 비슷 — URL(또는 재생목록)을 받아 서버가
// 영상을 받은 뒤 전사한다. POST /api/analyze 에 files 대신 url 을 보낸다.
const anTabFolder = $("an-tab-folder"), anTabUrl = $("an-tab-url"), anTabUpload = $("an-tab-upload");
const anFolderMode = $("an-folder-mode"), anUrlMode = $("an-url-mode"), anUploadMode = $("an-upload-mode");
const anUrl = $("an-url"), anUrlGo = $("an-url-go");
const anUrlPaste = $("an-url-paste"), anUrlPlaylist = $("an-url-playlist");
const anUrlPlaylistLabel = $("an-url-playlist-label");
const anDrop = $("an-drop"), anFile = $("an-file"), anUploadList = $("an-upload-list");

function setAnMode(mode) {  // 'folder' | 'url' | 'upload'
    const tabs = { folder: anTabFolder, url: anTabUrl, upload: anTabUpload };
    const panes = { folder: anFolderMode, url: anUrlMode, upload: anUploadMode };
    for (const k of Object.keys(tabs)) {
        const on = k === mode;
        tabs[k].classList.toggle("active", on);
        tabs[k].setAttribute("aria-selected", String(on));
        panes[k].hidden = !on;
    }
}
anTabFolder.addEventListener("click", () => setAnMode("folder"));
anTabUrl.addEventListener("click", () => setAnMode("url"));
anTabUpload.addEventListener("click", () => setAnMode("upload"));

let anPlaylistAll = false;
anUrlPlaylist.addEventListener("click", () => {
    anPlaylistAll = !anPlaylistAll;
    anUrlPlaylist.setAttribute("aria-pressed", String(anPlaylistAll));
    anUrlPlaylist.classList.toggle("btn-active", anPlaylistAll);
    anUrlPlaylist.classList.toggle("btn-outline", !anPlaylistAll);
    anUrlPlaylist.title = anPlaylistAll ? "재생목록 전체 전사 (ON)" : "클릭하면 재생목록 전체 전사";
    anUrlPlaylistLabel.textContent = anPlaylistAll ? "전체" : "단일";
});
anUrlPaste.addEventListener("click", async () => {
    try { const t = await navigator.clipboard.readText(); if (t) anUrl.value = t.trim(); } catch (e) { /* ignore */ }
});

async function startUrlAnalyze() {
    if (anUrlGo.disabled) return;  // 진행 중 — Enter 연타로 인한 중복 제출 방지.
    const url = anUrl.value.trim();
    if (!url) return;
    anUrlGo.disabled = true;
    try {
        const r = await fetch("/api/analyze", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, playlist_all: anPlaylistAll, ...anOptions() }),
        });
        if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
        const j = await r.json();
        trackJob(j.job_id, "analyze", `${anPlaylistAll ? "재생목록" : "URL"} 전사 — ${url}`);
        anUrl.value = "";
    } catch (e) {
        alert(`전사 시작 실패: ${e.message}`);
    } finally {
        anUrlGo.disabled = false;
    }
}
anUrlGo.addEventListener("click", startUrlAnalyze);
anUrl.addEventListener("keydown", e => { if (e.key === "Enter") startUrlAnalyze(); });

// ── 업로드 모드 — 로컬 음원·영상 → 서버 저장(download2read/업로드) → 바로 전사 ──
let anUploadBusy = false;
function renderUploadChips(list) {
    anUploadList.innerHTML = "";
    for (const u of list) {
        const li = document.createElement("li");
        li.innerHTML = `<span class="kmini">${u.kind === "video" ? "영상" : "음원"}</span>
            <span class="text" title="${escapeHtml(u.name)}">${escapeHtml(u.name)}</span>
            <span class="idx">${escapeHtml(u.state)}</span>`;
        anUploadList.appendChild(li);
    }
}
async function uploadAndAnalyze(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length || anUploadBusy) return;
    anUploadBusy = true;
    const chips = files.map(f => ({
        name: f.name, state: "업로드 중…",
        kind: (f.type || "").startsWith("video") ? "video" : "audio",
    }));
    renderUploadChips(chips);
    try {
        const fd = new FormData();
        for (const f of files) fd.append("files", f);
        let res;
        try {
            const r = await fetch("/api/analyze/upload", { method: "POST", body: fd });
            if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
            res = await r.json();
        } catch (e) {
            chips.forEach(c => c.state = "실패"); renderUploadChips(chips);
            alert(`업로드 실패: ${e.message}`); return;
        }
        if (res.rejected && res.rejected.length)
            alert("제외된 파일:\n" + res.rejected.map(x => `- ${x.name}: ${x.reason}`).join("\n"));
        const rels = (res.saved || []).map(s => s.rel);
        chips.forEach(c => c.state = "저장됨"); renderUploadChips(chips);
        if (!rels.length) return;
        const r2 = await fetch("/api/analyze", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ root: res.root, files: rels, ...anOptions() }),
        });
        if (!r2.ok) throw new Error(await r2.text() || `HTTP ${r2.status}`);
        const j = await r2.json();
        const label = rels.length === 1 ? res.saved[0].name : `업로드 ${rels.length}개`;
        trackJob(j.job_id, "analyze", label);
        anUploadList.innerHTML = "";
        setAnMode("folder");
        browseTo("read", res.folder);  // 업로드 폴더로 이동해 진행을 보여줌.
    } catch (e) {
        alert(`전사 시작 실패: ${e.message}`);
    } finally {
        anUploadBusy = false;
    }
}
anDrop.addEventListener("click", () => anFile.click());
anDrop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); anFile.click(); } });
anFile.addEventListener("change", () => { uploadAndAnalyze(anFile.files); anFile.value = ""; });
["dragenter", "dragover"].forEach(ev => anDrop.addEventListener(ev, e => { e.preventDefault(); anDrop.classList.add("drag"); }));
anDrop.addEventListener("dragleave", e => { e.preventDefault(); anDrop.classList.remove("drag"); });
anDrop.addEventListener("drop", e => {
    e.preventDefault(); anDrop.classList.remove("drag");
    if (e.dataTransfer && e.dataTransfer.files) uploadAndAnalyze(e.dataTransfer.files);
});

// ── 작업 큐 (영속 — 중단/실패 관리·재시도) ────────────────────────────────
// 백엔드: GET /api/queue, POST /api/queue/{id}/retry · /retry-all · /scan · /clear,
//         DELETE /api/queue/{id}
const qList = $("q-list"), qCounts = $("q-counts");
const qScan = $("q-scan"), qRetryAll = $("q-retry-all"), qClear = $("q-clear"), qRefresh = $("q-refresh");
const qAlbumScan = $("q-album-scan"), qDiarScan = $("q-diar-scan");
const Q_STATUS = {
    pending:     { txt: "대기",   cls: "run" },
    running:     { txt: "실행 중", cls: "run" },
    done:        { txt: "완료",   cls: "ok" },
    failed:      { txt: "실패",   cls: "fail" },
    interrupted: { txt: "중단됨", cls: "warn" },
};
const Q_KIND = { "download": "DL", "analyze-url": "전사·URL", "analyze-files": "전사", "album": "앨범", "diarize": "화자" };
// 큐 task kind → 작업 카드(trackJob) kind. analyze-url/analyze-files 는 'analyze' 로,
// download/album/diarize 는 그대로(라벨이 올바르게 표시되도록).
function trackKind(taskKind) {
    if (taskKind === "download" || taskKind === "album" || taskKind === "diarize") return taskKind;
    return "analyze";
}

let qBusy = false;     // 폴링 중복 방지(느린 서버에서 요청이 쌓여 순서가 꼬이는 것 방지).
let qLastSig = null;   // 직전 렌더 시그니처 — 변화 없으면 DOM 재구성 생략(깜빡임 방지).
async function loadQueue(force) {
    // force: 삭제/재시도 등 액션 직후 즉시 반영(폴링 중복 가드 무시 + 강제 재렌더).
    if (qBusy && !force) return;
    if (force) qLastSig = null;
    qBusy = true;
    let data;
    try {
        const r = await fetch("/api/queue");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        data = await r.json();
    } catch (e) {
        qList.innerHTML = `<p class="empty">큐 로드 실패: ${escapeHtml(e.message)}</p>`;
        qLastSig = null;
        return;
    } finally {
        qBusy = false;
    }
    const tasks = data.tasks || [], c = data.counts || {}, lane = data.lane || {};
    qCounts.textContent =
        `대기 ${c.pending || 0} · 실행 ${c.running || 0} · 완료 ${c.done || 0} · 실패 ${c.failed || 0} · 중단 ${c.interrupted || 0}`;
    // 상태/진행률이 그대로면 재구성하지 않는다(깜빡임·재시도 버튼 부활 방지).
    const sig = JSON.stringify([lane.busy, lane.current, lane.current_msg, lane.waiting,
        tasks.map(t => [t.id, t.status, t.attempts, t.error, t.title, t.wait_pos,
                        t.live && t.live.progress, t.live && t.live.message])]);
    if (sig === qLastSig) return;
    qLastSig = sig;
    qList.innerHTML = "";
    qList.appendChild(renderLane(lane));
    if (!tasks.length) {
        const p = document.createElement("p"); p.className = "empty";
        p.textContent = "큐가 비어 있습니다.";
        qList.appendChild(p);
        return;
    }
    for (const t of tasks) qList.appendChild(renderTask(t));
}

// 전사 레인(1개씩 순차) 현재 상태 배너 — '지금 뭐가 도는지/얼마나 대기인지'.
function renderLane(lane) {
    const d = document.createElement("div");
    d.className = "q-lane";
    if (lane.busy) {
        const cur = escapeHtml(lane.current || "");
        const msg = lane.current_msg ? ` — ${escapeHtml(lane.current_msg)}` : "";
        d.innerHTML = `<b>전사 레인</b> ▶ 실행 중 · <span title="${cur}">${cur}</span>${msg}` +
            (lane.waiting ? ` <span class="q-lane-wait">· 뒤에 ${lane.waiting}개 대기</span>` : "");
    } else if (lane.waiting) {
        d.innerHTML = `<b>전사 레인</b> ⏳ 곧 시작 · 대기 ${lane.waiting}개`;
        d.classList.add("idle");
    } else {
        d.innerHTML = `<b>전사 레인</b> · 유휴 (대기 작업 없음)`;
        d.classList.add("idle");
    }
    return d;
}

function renderTask(t) {
    const st = Q_STATUS[t.status] || { txt: t.status, cls: "run" };
    const row = document.createElement("div");
    row.className = "q-item";
    const live = t.live || {};
    const pct = (t.status === "running" && typeof live.progress === "number")
        ? Math.round(live.progress * 100) : null;
    // 서브텍스트: 실행 중=라이브 메시지, 대기=순번 안내, 중단=재시도 안내.
    let sub = "";
    if (t.status === "running") sub = live.message || "처리 중…";
    else if (t.status === "pending" && t.wait_pos) sub = `대기 ${t.wait_pos}번째 — 전사 레인 비는 대로 자동 시작`;
    else if (t.status === "pending") sub = "대기 중";
    else if (t.status === "interrupted") sub = "중단됨 — 재시도로 이어서 진행(완료분은 건너뜀)";
    row.innerHTML = `
        <span class="status-tag ${st.cls}">${st.txt}${pct !== null ? " " + pct + "%" : ""}</span>
        <span class="q-kind">${escapeHtml(Q_KIND[t.kind] || t.kind)}</span>
        <div class="q-main">
            <span class="q-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
            ${sub ? `<span class="q-sub">${escapeHtml(sub)}</span>` : ""}
            ${pct !== null ? `<div class="q-bar"><div style="width:${pct}%"></div></div>` : ""}
        </div>
        ${t.attempts ? `<span class="q-attempts">시도 ${t.attempts}</span>` : ""}
        ${t.error ? `<span class="q-err" title="${escapeHtml(t.error)}">${escapeHtml(t.error)}</span>` : ""}
        <span class="q-actions"></span>`;
    const acts = row.querySelector(".q-actions");
    if (t.status === "failed" || t.status === "interrupted") {
        const b = document.createElement("button");
        b.className = "btn btn-outline btn-sm"; b.textContent = "재시도";
        b.addEventListener("click", async () => {
            b.disabled = true;
            try {
                const r = await fetch(`/api/queue/${t.id}/retry`, { method: "POST" });
                if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
                const j = await r.json();
                if (j.job_id) trackJob(j.job_id, trackKind(t.kind), t.title);
                loadQueue(true);
            } catch (e) { alert(`재시도 실패: ${e.message}`); b.disabled = false; }
        });
        acts.appendChild(b);
    }
    const del = document.createElement("button");
    del.className = "btn btn-outline btn-sm"; del.textContent = "삭제";
    del.title = "목록에서 제거 (실행 중이면 작업 자체는 계속됨)";
    del.addEventListener("click", async () => {
        try {
            const r = await fetch(`/api/queue/${t.id}`, { method: "DELETE" });
            if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
            row.remove();          // 즉시 사라지게(서버 반영은 아래 새로고침으로 확정).
            loadQueue(true);
        } catch (e) { alert(`삭제 실패: ${e.message}`); }
    });
    acts.appendChild(del);
    return row;
}

qScan.addEventListener("click", async () => {
    if (!confirm("다운로드 폴더에서 전사 안 된 영상을 모두 큐에 추가할까요?\n전사는 순차로 처리되어 오래 걸릴 수 있습니다.")) return;
    qScan.disabled = true;
    try {
        const r = await fetch("/api/queue/scan", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ summarize: anSum.checked, language: anLang.value, budget_usd: parseFloat(anBudget.value) || 5 }),
        });
        if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
        const j = await r.json();
        alert(`큐에 ${j.enqueued}개 폴더(${j.files}개 파일)를 추가했습니다.`);
        loadQueue(true);
    } catch (e) { alert(`스캔 실패: ${e.message}`); }
    finally { qScan.disabled = false; }
});
qAlbumScan.addEventListener("click", async () => {
    if (!confirm("전사된 폴더 중 앨범이 없는 곳에 앨범(시각 HTML)을 생성할까요?\n이미 앨범이 있는 폴더는 건너뜁니다. ffmpeg 장면 캡쳐라 시간이 걸립니다.")) return;
    qAlbumScan.disabled = true;
    try {
        const r = await fetch("/api/queue/scan-albums", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        });
        if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
        const j = await r.json();
        alert(`앨범 큐에 ${j.enqueued}개 폴더를 추가했습니다.`);
        loadQueue(true);
    } catch (e) { alert(`앨범 스캔 실패: ${e.message}`); }
    finally { qAlbumScan.disabled = false; }
});
qDiarScan.addEventListener("click", async () => {
    if (!diarAvailable) { alert("화자 구분 비활성 — 서버에서  uv sync --extra diarize --native-tls  로 설치하세요."); return; }
    if (!confirm("전사됐지만 화자 구분이 안 된 폴더를 모두 화자 구분 큐에 추가할까요?\n이미 화자 구분된 폴더는 건너뜁니다. (ECAPA 임베딩이라 시간이 걸립니다)")) return;
    qDiarScan.disabled = true;
    try {
        const r = await fetch("/api/queue/scan-diarize", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        });
        if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
        const j = await r.json();
        alert(`화자 구분 큐에 ${j.enqueued}개 폴더를 추가했습니다.`);
        loadQueue(true);
    } catch (e) { alert(`화자 구분 스캔 실패: ${e.message}`); }
    finally { qDiarScan.disabled = false; }
});
qRetryAll.addEventListener("click", async () => {
    try {
        const r = await fetch("/api/queue/retry-all", { method: "POST" });
        const j = await r.json();
        // 재시도된 각 작업을 라이브 카드로도 추적(개별 재시도와 동일한 피드백).
        for (const job of j.jobs || []) {
            trackJob(job.job_id, trackKind(job.kind), job.title || job.job_id);
        }
        alert(`${j.retried || 0}개 작업을 재시도합니다.`);
        loadQueue(true);
    } catch (e) { alert(`재시도 실패: ${e.message}`); }
});
qClear.addEventListener("click", async () => {
    try { await fetch("/api/queue/clear", { method: "POST" }); loadQueue(true); }
    catch (e) { alert(`비우기 실패: ${e.message}`); }
});
qRefresh.addEventListener("click", () => loadQueue(true));
loadQueue();
rehydrateJobs();
// 탭이 보일 때만 폴링(숨겨진 탭에서 불필요한 요청 방지). qBusy 가 중복도 막는다.
setInterval(() => { if (!document.hidden) loadQueue(); }, 4000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadQueue(); });

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
                <span class="kind ${kind}">${{ download: "DL", generate: "GEN", analyze: "분석", album: "앨범", diarize: "화자" }[kind] || kind}</span>
                <span class="title-line" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
            </div>
            <div class="meta-line">id: ${jobId} · <span class="meta-mid">대기 중</span></div>
            <div class="progress"><div></div></div>
            <div class="error-line"></div>
        </div>
        <div class="job-side">
            <span class="status-tag run">queued</span>
            <button class="job-dismiss" title="목록에서 제거 (실행 중이어도 작업은 계속됨)" aria-label="제거">✕</button>
        </div>`;
    jobsEl.prepend(card);

    const tag = card.querySelector(".status-tag");
    const bar = card.querySelector(".progress > div");
    const mid = card.querySelector(".meta-mid");
    const errLine = card.querySelector(".error-line");

    const es = new EventSource(`/api/jobs/${jobId}/events`);
    jobs.set(jobId, { card, es });

    // 카드 제거(✕) — 서버 in-memory job 에서도 빼서 새로고침 시 다시 안 뜨게.
    card.querySelector(".job-dismiss").addEventListener("click", async () => {
        try { await fetch(`/api/jobs/${jobId}`, { method: "DELETE" }); } catch (e) { /* ignore */ }
        es.close();
        jobs.delete(jobId);
        card.remove();
        if (!jobsEl.children.length)
            jobsEl.innerHTML = '<p class="empty">아직 작업이 없습니다. 위에서 다운로드하거나 생성하세요.</p>';
    });

    es.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        const e = msg.event;
        if (e === "snapshot" || e === "status") {
            applyStatus(msg.status, tag);
            if (typeof msg.progress === "number") bar.style.width = (msg.progress * 100).toFixed(1) + "%";
            if (msg.message) mid.textContent = msg.message;
            // 새로고침 복원: 스냅샷이 이미 종료 상태면 최종 표시 + SSE 닫기.
            if (e === "snapshot" && (msg.status === "done" || msg.status === "failed" || msg.status === "cancelled")) {
                if (msg.status === "done") {
                    bar.style.width = "100%";
                    mid.textContent = [msg.message, msg.out_path].filter(Boolean).join(" → ") || mid.textContent || "완료";
                } else if (msg.status === "failed" && msg.error) {
                    errLine.textContent = `[${msg.error.category || "?"}] ${msg.error.message || "실패"}`;
                }
                es.close();
            }
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
            // 분석/앨범/화자 구분 완료 → 보드의 요약·대본·앨범·화자 라벨 갱신.
            if (kind === "analyze" || kind === "album" || kind === "diarize") loadAnalysisBoard();
            loadQueue(true);  // 큐 패널도 즉시 갱신(4초 폴링 기다리지 않게).
        } else if (e === "error") {
            applyStatus("failed", tag);
            errLine.textContent = `[${msg.category || "?"}] ${msg.message || "실패"}`;
            es.close();
            loadQueue(true);
        } else if (e === "cancelled") {
            applyStatus("cancelled", tag);
            es.close();
        }
    };
    es.onerror = () => {
        applyStatus("offline", tag);
    };
}

// 페이지 로드/새로고침 시 서버의 현재 작업들을 다시 카드로 복원(패널 ④).
// in-memory job 목록(/api/jobs)에서 최근 것들을 가져와 SSE 재연결 — 진행 중이면
// 라이브로 이어지고, 이미 끝난 건 스냅샷으로 최종 상태만 표시(SSE 즉시 닫힘).
async function rehydrateJobs() {
    let items;
    try {
        const r = await fetch("/api/jobs");
        if (!r.ok) return;
        items = (await r.json()).items || [];
    } catch (e) { return; }
    // 오래된 것부터 추가(trackJob 이 prepend 라 최신이 위로). 너무 많으면 최근 60개만.
    for (const job of items.slice(0, 60).reverse()) {
        if (jobs.has(job.id)) continue;
        trackJob(job.id, job.kind, job.title || job.input || job.id);
    }
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
