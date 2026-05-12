// PackingPlan GUI

const state = {
  catalog: [],
  items: [],
  containers: [],
  containerIdx: 0,
  summary: null,
};

const el = (id) => document.getElementById(id);

async function init() {
  const res = await fetch("/api/catalog");
  state.catalog = await res.json();
  populateFamilies();
  populateSizes();
  el("family").addEventListener("change", populateSizes);
  el("add-btn").addEventListener("click", addItem);
  el("plan-btn").addEventListener("click", buildPlan);
  el("prev").addEventListener("click", () => navigate(-1));
  el("next").addEventListener("click", () => navigate(1));
  setupDropzone();
  renderItems();
}

function families() {
  return [...new Set(state.catalog.map((c) => c.family))];
}

function populateFamilies() {
  const sel = el("family");
  sel.innerHTML = "";
  for (const f of families()) {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    sel.appendChild(opt);
  }
}

function populateSizes() {
  const family = el("family").value;
  const sel = el("size");
  sel.innerHTML = "";
  for (const c of state.catalog.filter((c) => c.family === family)) {
    const opt = document.createElement("option");
    opt.value = c.size;
    opt.textContent = c.size;
    sel.appendChild(opt);
  }
}

function addItem() {
  const family = el("family").value;
  const size = el("size").value;
  const length_m = parseFloat(el("length").value);
  const qty = parseInt(el("qty").value, 10);
  if (!family || !size || !qty || qty <= 0) return;

  // merge with same key
  const existing = state.items.find(
    (i) => i.family === family && i.size === size && i.length_m === length_m,
  );
  if (existing) {
    existing.qty += qty;
  } else {
    state.items.push({ family, size, length_m, qty });
  }
  renderItems();
}

function removeItem(idx) {
  state.items.splice(idx, 1);
  renderItems();
}

function renderItems() {
  const ul = el("order-list");
  ul.innerHTML = "";
  for (let i = 0; i < state.items.length; i++) {
    const it = state.items[i];
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="oi-name">${escapeHtml(it.family)} <span class="sz">${escapeHtml(
        it.size,
      )}</span></div>
      <div class="oi-meta">${fmt(it.qty)} × ${it.length_m}m</div>
      <button class="oi-remove" data-i="${i}" title="Remove">×</button>
    `;
    ul.appendChild(li);
  }
  ul.querySelectorAll(".oi-remove").forEach((btn) => {
    btn.addEventListener("click", () => removeItem(parseInt(btn.dataset.i, 10)));
  });
  el("item-count").textContent = state.items.length;
  el("plan-btn").disabled = state.items.length === 0;
}

async function buildPlan() {
  el("plan-btn").disabled = true;
  el("plan-btn").textContent = "Building…";
  try {
    const res = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: state.items }),
    });
    const data = await res.json();
    state.containers = data.containers || [];
    state.summary = data.summary || null;
    state.containerIdx = 0;
    renderContainer();
    renderSummary();
  } finally {
    el("plan-btn").disabled = false;
    el("plan-btn").textContent = "Build packing plan →";
  }
}

function navigate(delta) {
  if (!state.containers.length) return;
  state.containerIdx = Math.max(
    0,
    Math.min(state.containers.length - 1, state.containerIdx + delta),
  );
  renderContainer();
}

function renderContainer() {
  if (!state.containers.length) {
    el("empty-state").style.display = "flex";
    el("cross-section").style.display = "none";
    el("metrics").style.display = "none";
    el("container-label").textContent = "—";
    el("prev").disabled = true;
    el("next").disabled = true;
    return;
  }

  const c = state.containers[state.containerIdx];
  el("empty-state").style.display = "none";
  el("cross-section").style.display = "block";
  el("metrics").style.display = "grid";

  el("container-label").textContent = `Container ${state.containerIdx + 1} of ${
    state.containers.length
  }`;
  el("prev").disabled = state.containerIdx === 0;
  el("next").disabled = state.containerIdx >= state.containers.length - 1;

  drawCrossSection(c);

  const hostCount = c.units_in_container;
  const innerCount = c.cross_section.circles.reduce(
    (a, b) => a + b.inners.length,
    0,
  );
  const pipesTotal = hostCount + innerCount;
  el("m-pipes").textContent = `${fmt(pipesTotal)}`;
  if (innerCount > 0) {
    el("m-pipes").innerHTML = `${fmt(pipesTotal)}<span style="font-size:13px;color:var(--muted)"> &nbsp;${fmt(
      hostCount,
    )}+${fmt(innerCount)}</span>`;
  }
  el("m-weight").textContent = fmt(Math.round(c.weight_kg));
  el("m-fill").textContent = `${c.weight_pct}%`;
  el("m-pattern").textContent = `${c.length_positions} × stack`;
  el("m-pattern").title = c.loading_pattern;
}

function drawCrossSection(container) {
  const svg = el("cross-section");
  svg.innerHTML = "";

  const cs = container.cross_section;
  const frameW = cs.frame_w_m;
  const frameH = cs.frame_h_m;
  // viewport sized so frame fits nicely
  const padding = 18;
  const targetW = 600;
  const targetH = 400;
  const innerW = targetW - 2 * padding;
  const innerH = targetH - 2 * padding;
  const scale = Math.min(innerW / frameW, innerH / frameH);
  const offsetX = (targetW - frameW * scale) / 2;
  const offsetY = (targetH - frameH * scale) / 2;

  svg.setAttribute("viewBox", `0 0 ${targetW} ${targetH}`);

  // container outline — rounded rectangle representing the container interior
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", offsetX);
  rect.setAttribute("y", offsetY);
  rect.setAttribute("width", frameW * scale);
  rect.setAttribute("height", frameH * scale);
  rect.setAttribute("rx", 8);
  rect.setAttribute("fill", "#FFFFFF");
  rect.setAttribute("stroke", "#DCDCD7");
  rect.setAttribute("stroke-width", 1.25);
  svg.appendChild(rect);

  // dimensions label
  const dim = document.createElementNS("http://www.w3.org/2000/svg", "text");
  dim.setAttribute("x", targetW / 2);
  dim.setAttribute("y", targetH - 4);
  dim.setAttribute("text-anchor", "middle");
  dim.setAttribute("font-size", 10);
  dim.setAttribute("fill", "#9A9A9A");
  dim.setAttribute("font-family", "-apple-system, system-ui, sans-serif");
  dim.textContent = `${frameW.toFixed(2)} m × ${frameH.toFixed(2)} m  •  ${
    container.length_positions
  }× along ${(12.032).toFixed(2)} m length`;
  svg.appendChild(dim);

  // pipes
  for (const circ of cs.circles) {
    const cx = offsetX + circ.cx_m * scale;
    // flip y because SVG y grows downward but our packing origin is bottom-left
    const cy = offsetY + (frameH - circ.cy_m) * scale;
    const r = (circ.od_mm / 1000 / 2) * scale;

    const host = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    host.setAttribute("cx", cx);
    host.setAttribute("cy", cy);
    host.setAttribute("r", r);
    host.setAttribute("fill", "#FFFFFF");
    host.setAttribute("stroke", "#1A1A1A");
    host.setAttribute("stroke-width", Math.max(0.6, r * 0.06));
    svg.appendChild(host);

    // draw the inner pipes — first inner concentric for visual clarity
    drawInners(svg, cx, cy, r, circ.inners, scale);
  }
}

function drawInners(svg, cx, cy, hostRadius, inners, scale) {
  if (!inners || inners.length === 0) return;
  // group inners by size for consistent color
  const palette = ["#1A1A1A", "#6E6E6E", "#B5B5B5"];
  const bySize = {};
  for (const it of inners) {
    bySize[it.size] = bySize[it.size] || [];
    bySize[it.size].push(it);
  }
  const sizeList = Object.keys(bySize);

  // place inner pipes using the same min-circle-of-n geometry
  // arranged purely by quantity in this group (largest first)
  const sizeOrder = sizeList.sort(
    (a, b) => bySize[b][0].od_mm - bySize[a][0].od_mm,
  );

  // Group total counts
  let curRadius = hostRadius;
  let curCx = cx;
  let curCy = cy;
  for (let s = 0; s < sizeOrder.length; s++) {
    const size = sizeOrder[s];
    const group = bySize[size];
    const innerOdScreen = (group[0].od_mm / 1000) * scale;
    const innerR = innerOdScreen / 2;
    const positions = ringPositions(group.length, curCx, curCy, innerR);
    for (let i = 0; i < positions.length; i++) {
      const [x, y] = positions[i];
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("cx", x);
      c.setAttribute("cy", y);
      c.setAttribute("r", innerR);
      c.setAttribute("fill", palette[s % palette.length]);
      c.setAttribute("stroke", "#FFFFFF");
      c.setAttribute("stroke-width", Math.max(0.4, innerR * 0.08));
      svg.appendChild(c);
    }
  }
}

function ringPositions(n, cx, cy, r) {
  // place n circles of radius r inside the same host frame, by the same
  // min-circle-of-n approximation we use in the backend
  if (n === 1) return [[cx, cy]];
  const positions = [];
  const ringR = n === 2 ? r : r / Math.sin(Math.PI / n);
  for (let i = 0; i < n; i++) {
    const theta = (2 * Math.PI * i) / n - Math.PI / 2;
    positions.push([cx + ringR * Math.cos(theta), cy + ringR * Math.sin(theta)]);
  }
  return positions;
}

function renderSummary() {
  if (!state.summary) {
    el("summary").innerHTML = "";
    return;
  }
  const s = state.summary;
  const totalKg = Math.round(s.total_weight_kg);
  el("summary").innerHTML = `
    <strong>${fmt(s.total_containers)}</strong> × 40ft container${
    s.total_containers === 1 ? "" : "s"
  } &nbsp;·&nbsp;
    ${fmt(totalKg)} kg total &nbsp;·&nbsp;
    ${fmt(s.nested_pipes)} pipes nested
  `;
}

function setupDropzone() {
  const dz = el("dropzone");
  const fi = el("file-input");
  dz.addEventListener("click", () => fi.click());
  dz.addEventListener("dragover", (e) => {
    e.preventDefault();
    dz.classList.add("drag");
  });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
  });
  fi.addEventListener("change", () => {
    if (fi.files[0]) uploadFile(fi.files[0]);
  });
}

async function uploadFile(file) {
  const dz = el("dropzone");
  const prev = dz.innerHTML;
  dz.innerHTML = `<span class="dz-title">Parsing ${escapeHtml(file.name)}…</span>`;
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    const data = await res.json();
    if (data.items) {
      for (const it of data.items) {
        const existing = state.items.find(
          (i) => i.family === it.family && i.size === it.size && i.length_m === it.length_m,
        );
        if (existing) existing.qty += it.qty;
        else state.items.push(it);
      }
      renderItems();
    }
  } finally {
    dz.innerHTML = prev;
    // re-bind: rewrite the inner static markup
    dz.innerHTML = `
      <span class="dz-title">Drop a PI Excel here</span>
      <span class="dz-sub">or click to browse</span>
      <input id="file-input" type="file" accept=".xlsx,.xls" hidden />
    `;
    setupDropzone();
  }
}

function fmt(n) {
  return new Intl.NumberFormat("en-US").format(n);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );
}

init();
