const state = { data: null, csrf: null, setupRequired: false, activeTab: "patch", pendingEntityId: null, editingTopology: false, topologyPositions: new Map(), selectedNodes: new Set(), groupSelected: false };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") => String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const formatTime = value => value ? new Intl.DateTimeFormat("nl-NL", {day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"}).format(new Date(value)) : "nog nooit";
const formatBytes = bytes => bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;

function applyBranding(settings = {}) {
  const title = settings.title || "Network Patch Manager";
  document.title = title;
  $$('[data-app-title]').forEach(element => { element.textContent = title; });
}

function errorMessage(body, status) {
  const detail = body && typeof body === "object" ? body.detail : body;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) return detail.map(item => (item && item.msg) || JSON.stringify(item)).join(" · ");
  if (detail) return JSON.stringify(detail);
  return `HTTP ${status}`;
}

async function api(path, options = {}) {
  const headers = {"Content-Type":"application/json", ...(options.headers || {})};
  if (state.csrf && options.method && !["GET", "HEAD"].includes(options.method)) headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, {...options, headers});
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401 && !path.includes("/auth/")) showAuth(false);
    throw new Error(errorMessage(body, response.status));
  }
  return body;
}

function toast(message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("#toast-stack").append(item);
  setTimeout(() => item.remove(), 4200);
}

async function initialize() {
  try {
    applyBranding(await api("/api/public/settings"));
    const auth = await api("/api/auth/status");
    state.setupRequired = auth.setup_required;
    if (auth.authenticated) {
      state.csrf = auth.csrf_token;
      $("#username").textContent = auth.username;
      $("#avatar").textContent = auth.username.slice(0, 1).toUpperCase();
      await showApp();
    } else showAuth(auth.setup_required);
  } catch (error) {
    showAuth(false);
    $("#auth-error").textContent = `App niet bereikbaar: ${error.message}`;
  }
}

function showAuth(setup) {
  state.setupRequired = setup;
  $("#app-shell").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
  $("#auth-title").textContent = setup ? "Eerste beheerder" : "Inloggen";
  $("#auth-eyebrow").textContent = setup ? "Eenmalige veilige configuratie" : "Lokale netwerkadministratie";
  $("#auth-copy").textContent = setup ? "Maak het lokale adminaccount aan. Daarna wordt deze setup definitief gesloten." : "Alleen beheerders hebben toegang tot patch- en providergegevens.";
  $("#auth-submit-label").textContent = setup ? "Beheerder aanmaken" : "Inloggen";
  $("#auth-form input[name=password]").autocomplete = setup ? "new-password" : "current-password";
}

async function showApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-shell").classList.remove("hidden");
  await loadData();
  // Na de allereerste setup meteen begeleid inrichten; daarna alleen op verzoek.
  try {
    const info = await api("/api/wizard/info");
    if (!info.dismissed) await openWizard();
  } catch { /* wizard is optioneel */ }
}

async function loadData(silent = false) {
  try {
    state.data = await api("/api/bootstrap");
    renderAll();
    stampRefresh();
    if (!silent) toast("Gegevens bijgewerkt");
  } catch (error) { toast(error.message, "error"); }
}

function stampRefresh() {
  $("#last-refresh").textContent = `bijgewerkt ${new Date().toLocaleTimeString("nl-NL", {hour:"2-digit",minute:"2-digit"})}`;
}

// De 30s-poll haalt alleen statussen op; de volledige bootstrap komt bij het
// laden en na elke mutatie. Scheelt bij groei de hele inventaris per tick.
async function pollSummary() {
  // Niet verversen tijdens een sleepactie of een open dialoog: de DOM eronder
  // opnieuw opbouwen breekt de handeling waar de gebruiker mee bezig is.
  if (!state.data || dragPayload || document.querySelector("dialog[open]")) return;
  try {
    const summary = await api("/api/summary");
    const byId = new Map(summary.entities.map(item => [item.id, item]));
    state.data.entities.forEach(entity => {
      const fresh = byId.get(entity.id);
      if (fresh) Object.assign(entity, fresh);
    });
    (state.data.topology?.nodes || []).forEach(node => {
      if (node.reference_type === "entity") {
        const fresh = byId.get(node.reference_id);
        if (fresh) node.status = fresh.status;
        node.metrics = summary.metrics[node.reference_id] || node.metrics;
      }
    });
    state.data.physical_devices.forEach(device => device.ports.forEach(port => {
      if (port.entity_id && byId.has(port.entity_id)) port.entity_status = byId.get(port.entity_id).status;
    }));
    state.data.counts = summary.counts;
    state.data.speedtest = {...state.data.speedtest, ...summary.speedtest};
    renderAll();
    stampRefresh();
  } catch (error) { /* stille poll: fouten verschijnen bij de volgende actie */ }
}

function renderAll() {
  applyBranding(state.data.site);
  $("#app-settings-form").elements.title.value = state.data.site.title;
  renderSummary();
  renderPatch();
  renderTopology();
  renderAdmin();
  renderSpeedtest();
}

function statusDot(status) { return `<i class="status-dot ${["up","down","degraded"].includes(status) ? status : "unknown"}"></i>`; }

function renderSummary() {
  const c = state.data.counts;
  $("#summary-chips").innerHTML = [
    [`${c.patched}/${c.ports}`, "poorten gepatcht", ""],
    [c.up, "devices up", "up"],
    [c.down, "devices down", "down"],
    [c.unlinked, "ongekoppeld", "unknown"],
    [c.conflicts, "conflicten", c.conflicts ? "degraded" : "up"],
  ].map(([value,label,status]) => `<span class="chip">${status ? statusDot(status) : ""}<b>${value}</b> ${label}</span>`).join("");
  const attention = c.unlinked + c.conflicts;
  $("#admin-badge").textContent = attention;
  $("#admin-badge").classList.toggle("hidden", attention === 0);
}

// Kabelkleur uit het label; onbekende namen krijgen de neutrale accentkleur.
const CABLE_COLORS = {blauw:"#4d8cff",rood:"#ff6262",geel:"#ffd166",groen:"#3ddc97",zwart:"#5a6474",wit:"#e6ecf5",grijs:"#8b95a6",oranje:"#ffb454",paars:"#a99cff"};
function cableColor(name) { return CABLE_COLORS[String(name || "").toLowerCase().trim()] || "#6f7a8c"; }

function portLabelFor(port) {
  if (port.link_kind === "port") return `→ ${port.target_port_label || "poort"}${port.entity_name ? ` · ${port.entity_name}` : ""}`;
  return port.entity_name || "Vrije poort";
}

// Eén poort in het apparaatfront: klikbaar, sleepbaar en keyboard-bereikbaar.
function portFace(port, device) {
  const status = port.entity_id ? (port.entity_status || "unknown") : "free";
  const tip = `${device.name} · poort ${port.number}${port.side === "rear" ? " (achter)" : ""}: ${portLabelFor(port)}${port.cable_label ? ` · kabel ${port.cable_label}` : ""}`;
  return `<button class="port-face ${port.cable_id ? "occupied" : ""} status-${esc(status)}"
      data-port-id="${esc(port.id)}" data-device-id="${esc(device.id)}" data-drop-port="${esc(port.id)}"
      ${port.cable_id ? 'draggable="true"' : ""} title="${esc(tip)}" aria-label="${esc(tip)}">
    <span class="face-num">${String(port.number).padStart(2, "0")}${port.side === "rear" ? "r" : ""}</span>
    <span class="face-cable" style="background:${port.cable_id ? cableColor(port.cable_color) : "transparent"}"></span>
  </button>`;
}

function deviceFace(device) {
  const fronts = device.ports.filter(port => port.side !== "rear");
  const rears = device.ports.filter(port => port.side === "rear");
  const used = device.ports.filter(port => port.cable_id).length;
  return `
    <article class="device-card" data-device-card="${esc(device.id)}">
      <header class="device-head">
        <div><div class="device-title">${esc(device.name)}</div><div class="device-meta">${esc(device.model || device.type)}${device.location ? ` · ${esc(device.location)}` : ""} · ${used}/${device.ports.length} bezet</div></div>
        <div class="device-head-actions"><span class="device-kind">${device.type === "mesh_ap" ? "MESH AP" : device.type.replace("_", " ").toUpperCase()}</span><button class="icon-button" data-physical-edit="${device.id}" title="Bewerken">✎</button><button class="icon-button danger-icon" data-physical-delete="${device.id}" title="Netwerkapparaat verwijderen" aria-label="${esc(device.name)} verwijderen">×</button></div>
      </header>
      <div class="device-front">${fronts.map(port => portFace(port, device)).join("")}</div>
      ${rears.length ? `<div class="front-label">achterzijde</div><div class="device-front rear">${rears.map(port => portFace(port, device)).join("")}</div>` : ""}
      <div class="port-legend">${device.ports.filter(port => port.cable_id).slice(0, 6).map(port =>
        `<span class="legend-item"${port.entity_id ? ` data-entity-open="${esc(port.entity_id)}" role="button" tabindex="0"` : ""}><i style="background:${cableColor(port.cable_color)}"></i>${String(port.number).padStart(2,"0")}${port.side === "rear" ? "r" : ""} ${esc(shorten(portLabelFor(port), 22))}</span>`).join("") || `<span class="muted tiny">Nog niets gepatcht — sleep een device op een poort of klik erop.</span>`}</div>
    </article>`;
}

function renderPatch() {
  const devices = state.data.physical_devices;
  $("#device-count").textContent = `${devices.length} apparaten · ${state.data.counts.ports} poorten`;
  $("#physical-grid").innerHTML = devices.map(deviceFace).join("");
  renderUnpatched();
  const manual=state.data.entities.filter(entity=>entity.origin==="manual");
  $("#manual-entity-count").textContent=`${manual.length} devices`;
  $("#manual-entities-list").innerHTML=manual.length?manual.map(entity=>`<div class="data-row entity-row"><div class="data-main"><span class="data-icon">${entityIcon(entity.type)}</span><div><strong class="link" data-entity-open="${esc(entity.id)}" role="button" tabindex="0">${esc(entity.name)}</strong><span>${esc(entity.type)} · handmatig</span></div></div><div><span class="data-cell-label">Adres</span><br>${esc(entity.ip_address||entity.hostname||"—")}</div><div><span class="data-cell-label">Status</span><br>${statusDot(entity.status)} ${esc(entity.status)}</div><div class="row-actions"><button class="button" data-entity-edit="${entity.id}">Wijzig</button><button class="button danger" data-entity-delete="${entity.id}">Verwijder</button></div></div>`).join(""):`<div class="empty-state">Nog geen handmatige devices.</div>`;
}

// Zijlijst: devices zonder kabel, sleepbaar naar een poort.
function renderUnpatched() {
  const linked = new Set(state.data.physical_devices.flatMap(device =>
    device.ports.filter(port => port.link_kind === "entity").map(port => port.entity_id)));
  const free = state.data.entities.filter(entity =>
    !linked.has(entity.id) && !entity.archived && !["service", "container", "vm", "lxc"].includes(entity.type));
  $("#unpatched-count").textContent = `${free.length} vrij`;
  $("#unpatched-list").innerHTML = free.length ? free.map(entity => `
    <button class="chip-device" draggable="true" data-drag-entity="${esc(entity.id)}" title="Sleep op een poort of klik om te koppelen">
      ${statusDot(entity.status)}<span>${esc(entity.name)}</span><small>${esc(entity.vendor || entity.ip_address || entity.type)}</small>
    </button>`).join("") : `<div class="empty-state">Alles is gekoppeld.</div>`;
}

/* ------------------------------------------------- koppelen (muis/touch/toets) */
// Eén gedeeld pad voor alle koppelacties, zodat slepen en klikken exact
// hetzelfde doen: kabel leggen, verplaatsen of loskoppelen.
let dragPayload = null;

// Een poort overschrijven gooit de bestaande kabel weg (label, kleur, notities).
// Bij slepen is dat niet zichtbaar, dus daar vragen we het eerst.
function confirmOverwrite(portId) {
  const port = findPort(portId);
  if (!port || !port.cable_id) return true;
  const occupant = port.link_kind === "port" ? (port.target_port_label || "een andere poort") : (port.entity_name || "een device");
  return confirm(`Deze poort is al bezet door ${occupant}${port.cable_label ? ` (kabel ${port.cable_label})` : ""}.\n\nDie kabel wordt losgekoppeld. Doorgaan?`);
}

async function linkEntityToPort(entityId, portId, {ask = true} = {}) {
  if (ask && !confirmOverwrite(portId)) return;
  const previous = findPortByEntity(entityId);
  try {
    await api(`/api/ports/${encodeURIComponent(portId)}/cable`, {method: "PUT", body: JSON.stringify({b_entity_id: entityId})});
    await loadData(true);
    const name = state.data.entities.find(item => item.id === entityId)?.name || "Device";
    toast(previous ? `${name} verplaatst` : `${name} gekoppeld`);
  } catch (error) { toast(error.message, "error"); }
}

function cablePayload(port) {
  const shared = {label: port.cable_label, color: port.cable_color, notes: port.cable_notes};
  return port.link_kind === "entity"
    ? {...shared, b_entity_id: port.entity_id}
    : {...shared, b_port_id: port.target_port_id};
}

async function moveCable(fromPortId, toPortId) {
  const port = findPort(fromPortId);
  if (!port || !port.cable_id) return;
  if (!confirmOverwrite(toPortId)) return;
  const payload = cablePayload(port);
  try {
    await api(`/api/ports/${encodeURIComponent(fromPortId)}/cable`, {method: "DELETE"});
    try {
      await api(`/api/ports/${encodeURIComponent(toPortId)}/cable`, {method: "PUT", body: JSON.stringify(payload)});
    } catch (error) {
      // De oude kabel is al weg; zet hem terug zodat een mislukte verplaatsing
      // nooit administratie kost.
      await api(`/api/ports/${encodeURIComponent(fromPortId)}/cable`, {method: "PUT", body: JSON.stringify(payload)});
      throw error;
    }
    await loadData(true);
    toast("Kabel verplaatst");
  } catch (error) { toast(error.message, "error"); await loadData(true); }
}

async function unlinkPort(portId) {
  try {
    await api(`/api/ports/${encodeURIComponent(portId)}/cable`, {method: "DELETE"});
    await loadData(true);
    toast("Kabel losgekoppeld");
  } catch (error) { toast(error.message, "error"); }
}

function findPort(portId) {
  for (const device of state.data.physical_devices) {
    const port = device.ports.find(item => item.id === portId);
    if (port) return port;
  }
  return null;
}

function findPortByEntity(entityId) {
  for (const device of state.data.physical_devices) {
    const port = device.ports.find(item => item.link_kind === "entity" && item.entity_id === entityId);
    if (port) return port;
  }
  return null;
}

function renderTopology() {
  if (!state.data) return;
  const svg = $("#topology-canvas");
  const nodes = state.data.topology?.nodes || [];
  const relations = state.data.topology?.relations || [];
  const visible = nodes.filter(node => !node.hidden);
  const byId = new Map(visible.map(node => [node.id, node]));
  const children = new Map();
  visible.forEach(node => {
    if (node.parent_node_id && byId.has(node.parent_node_id)) {
      if (!children.has(node.parent_node_id)) children.set(node.parent_node_id, []);
      children.get(node.parent_node_id).push(node);
    }
  });
  const positions = autoTopologyLayout(visible, children);
  state.topologyPositions = positions;
  const showPhysical = $('[data-layer="physical"]').checked;
  const showVirtual = $('[data-layer="virtual"]').checked;
  const showServices = $('[data-layer="services"]').checked;
  let groupMarkup = "", nodeMarkup = "", edgeMarkup = "", labelMarkup = "";

  [...visible].sort((a,b) => Number(Boolean(children.get(b.id)?.length)) - Number(Boolean(children.get(a.id)?.length))).forEach(node => {
    const p = positions.get(node.id); if (!p) return;
    const hasChildren = children.has(node.id) && !node.collapsed;
    if (hasChildren || node.node_type === "group") groupMarkup += topoGroup(node, p);
    else nodeMarkup += topoNode(node, p);
  });
  relations.forEach(relation => {
    const from = positions.get(relation.from_node_id), to = positions.get(relation.to_node_id);
    if (!from || !to) return;
    if (relation.source === "patch" && !showPhysical) return;
    const service = relation.relation_type === "service" || relation.relation_type === "dependency";
    if (service && !showServices) return;
    if (relation.source !== "patch" && !service && !showVirtual) return;
    const x1=from.x+from.width/2, y1=from.y+from.height, x2=to.x+to.width/2, y2=to.y;
    const cls = relation.source === "patch" ? "edge-physical" : service ? "edge-service" : "edge-virtual";
    edgeMarkup += `<path class="${cls} relation-hit ${relation.source==='manual'?'manual':''}" data-relation-id="${esc(relation.id)}" d="M${x1} ${y1} C${x1} ${(y1+y2)/2},${x2} ${(y1+y2)/2},${x2} ${y2}"/>`;
    if (relation.label) labelMarkup += `<text class="edge-label" x="${(x1+x2)/2+5}" y="${(y1+y2)/2-4}">${esc(shorten(relation.label,24))}</text>`;
  });
  const extent = [...positions.values()].reduce((acc,p) => ({x:Math.max(acc.x,p.x+p.width+50),y:Math.max(acc.y,p.y+p.height+60)}), {x:1200,y:760});
  view.base = [extent.x, extent.y];
  applyViewBox();
  svg.classList.toggle("editing", state.editingTopology);
  svg.innerHTML = `${edgeMarkup}${labelMarkup}${groupMarkup}${nodeMarkup}`;
  updateSelectionUI();
}

function autoTopologyLayout(nodes, children) {
  const positions = new Map();
  const measured = new Map();
  const measure = node => {
    if (measured.has(node.id)) return measured.get(node.id);
    const kids = children.get(node.id) || [];
    if (!kids.length || node.collapsed) { const size={width:Math.max(176,node.width||0),height:Math.max(58,node.height||0)}; measured.set(node.id,size); return size; }
    const sizes=kids.map(measure), width=Math.max(390,...sizes.map(s=>s.width+36));
    const height=68+sizes.reduce((sum,s)=>sum+s.height+14,0);
    const size={width:Math.max(width,node.width||0),height:Math.max(height,node.height||0)}; measured.set(node.id,size); return size;
  };
  const place = (node,x,y) => {
    const size=measure(node), px=node.manual_position&&node.x!==null?Number(node.x):x, py=node.manual_position&&node.y!==null?Number(node.y):y;
    positions.set(node.id,{x:px,y:py,...size});
    let childY=py+55;
    (children.get(node.id)||[]).forEach(kid=>{ const s=measure(kid); place(kid,px+18,childY); childY+=s.height+14; });
  };
  const roots=nodes.filter(node=>!node.parent_node_id || !nodes.some(n=>n.id===node.parent_node_id));
  const internet=roots.find(n=>n.id==="special:internet"), router=roots.find(n=>n.id==="special:router");
  if (internet) place(internet,512,28);
  if (router) place(router,500,128);
  const others=roots.filter(n=>n!==internet&&n!==router); let x=25,y=255,rowHeight=0;
  others.forEach(node=>{const s=measure(node);if(x+s.width>1175){x=25;y+=rowHeight+24;rowHeight=0;}place(node,x,y);x+=s.width+22;rowHeight=Math.max(rowHeight,s.height);});
  return positions;
}

function topoGroup(node, p) {
  const status=node.status||"unknown";
  return `<g class="topo-node topo-container lifecycle-${esc(node.lifecycle)} ${state.selectedNodes.has(node.id)?"selected":""}" data-node-id="${esc(node.id)}" transform="translate(${p.x} ${p.y})">
    <rect class="topo-group" width="${p.width}" height="${p.height}" rx="16"/>
    <text class="topo-group-title" x="18" y="25">${esc(shorten(node.label,38))}</text>
    <text class="topo-sub" x="18" y="42">${esc(shorten(node.subtitle||node.node_type,48))}</text>
    <circle cx="${p.width-16}" cy="17" r="5" class="status-fill ${esc(status)}"/>
  </g>`;
}

function topoNode(node, p) {
  const colors = {up:"#3ddc97",down:"#ff6262",degraded:"#ffb454",unknown:"#667282"};
  const color = colors[node.status] || colors.unknown, metrics=node.metrics||{};
  const cpu=metrics.cpu_percent!==undefined&&metrics.cpu_percent!==null?Math.max(0,Math.min(100,Number(metrics.cpu_percent))):null;
  const figure = keyFigure(node, metrics);
  return `<g class="topo-node lifecycle-${esc(node.lifecycle)} ${state.selectedNodes.has(node.id)?"selected":""}" data-node-id="${esc(node.id)}" transform="translate(${p.x} ${p.y})">
    <rect class="topo-rect" width="${p.width}" height="${p.height}" rx="10"/>
    <text class="topo-title" x="12" y="22">${esc(shorten(node.label, 23))}</text>
    <text class="topo-sub" x="12" y="41">${esc(shorten(node.subtitle, 27))}</text>
    ${figure ? `<text class="topo-figure" x="${p.width-14}" y="41" text-anchor="end">${esc(figure)}</text>` : ""}
    <circle cx="${p.width-14}" cy="14" r="5" fill="${color}"/>
    ${cpu===null?"":`<rect class="metric-track" x="12" y="${p.height-9}" width="${p.width-24}" height="3" rx="2"/><rect class="metric-fill" x="12" y="${p.height-9}" width="${(p.width-24)*cpu/100}" height="3" rx="2"/>`}
  </g>`;
}

// Eén kerngetal per node: cpu voor machines, respons voor services.
function keyFigure(node, metrics) {
  if (metrics.cpu_percent !== undefined && metrics.cpu_percent !== null) return `${Math.round(Number(metrics.cpu_percent))}%`;
  if (metrics.latency_ms !== undefined && metrics.latency_ms !== null) return `${Math.round(Number(metrics.latency_ms))}ms`;
  return "";
}

function shorten(value, length) { value = String(value || ""); return value.length > length ? `${value.slice(0,length-1)}…` : value; }

function renderAdmin() {
  const assigned = new Set(state.data.physical_devices.flatMap(device => device.ports.filter(port => port.entity_id).map(port => port.entity_id)));
  const unlinked = state.data.entities.filter(entity => entity.origin === "discovered" && !entity.ignored && !entity.archived && !assigned.has(entity.id));
  const inactive = state.data.entities.filter(entity => entity.origin === "discovered" && (entity.ignored || entity.archived));
  $("#providers-grid").innerHTML = state.data.providers.map(provider => {
    const status = provider.last_error ? "error" : provider.last_success_at ? "ok" : "idle";
    return `<article class="provider-card">
      <div class="provider-head"><div class="data-main"><span class="provider-icon">${providerIcon(provider.type)}</span><div><div class="provider-name">${esc(provider.name)}</div><div class="provider-type">${esc(provider.type)}</div></div></div><span class="provider-state ${status}">${provider.enabled ? (status === "error" ? "fout" : status === "ok" ? "actief" : "gereed") : "uit"}</span></div>
      <div class="provider-details"><div><span>Laatste succes</span><b>${formatTime(provider.last_success_at)}</b></div><div><span>Interval</span><b>${provider.poll_interval_seconds}s</b></div></div>
      ${provider.last_error ? `<p class="form-error tiny" title="${esc(provider.last_error)}">${esc(shorten(provider.last_error, 62))}</p>` : ""}
      <div class="provider-actions"><button class="button" data-provider-edit="${provider.id}">Configureren</button><button class="button" data-provider-sync="${provider.id}">Nu ophalen</button></div>
    </article>`;
  }).join("");
  $("#unlinked-count").textContent = `${unlinked.length} gevonden`;
  $("#discoveries-list").innerHTML = unlinked.length ? unlinked.map(entity => `
    <div class="data-row discovery-row"><div class="data-main"><span class="data-icon">${entityIcon(entity.type)}</span><div><strong>${esc(entity.name)}</strong><span>${esc(entity.vendor || entity.type)} · discovery</span></div></div><div><span class="data-cell-label">Adres</span><br>${esc(entity.ip_address || entity.hostname || "—")}</div><div><span class="data-cell-label">Status</span><br>${statusDot(entity.status)} ${esc(entity.status)}</div><div class="row-actions"><button class="button" data-link-entity="${entity.id}">Poort</button><button class="button" data-merge-entity="${entity.id}">Samenvoegen</button><button class="button" data-discovery-state="ignore" data-entity-id="${entity.id}">Negeer</button><button class="button" data-discovery-state="archive" data-entity-id="${entity.id}">Archiveer</button></div></div>`).join("") : `<div class="empty-state">Geen ongekoppelde discoveries.</div>`;
  $("#inactive-discovery-count").textContent=`${inactive.length} items`;
  $("#inactive-discoveries-list").innerHTML=inactive.length?inactive.map(entity=>`<div class="data-row"><div class="data-main"><span class="data-icon">${entityIcon(entity.type)}</span><div><strong>${esc(entity.name)}</strong><span>${entity.archived?"gearchiveerd":"genegeerd"}</span></div></div><div>${esc(entity.ip_address||entity.hostname||"—")}</div><div>${statusDot(entity.status)} ${esc(entity.status)}</div><button class="button" data-discovery-state="restore" data-entity-id="${entity.id}">Herstellen</button></div>`).join(""):`<div class="empty-state">Geen genegeerde of gearchiveerde discoveries.</div>`;
  const mappings=(state.data.provider_records||[]).slice(0,250);$("#mapping-count").textContent=`${state.data.provider_records?.length||0} records`;
  $("#provider-mappings-list").innerHTML=mappings.length?mappings.map(record=>`<div class="data-row mapping-row"><div class="data-main"><span class="data-icon">${providerIcon((state.data.providers.find(p=>p.id===record.provider_id)||{}).type)}</span><div><strong>${esc(record.external_id)}</strong><span>${esc(record.provider_name)} · ${esc(record.kind)}</span></div></div><div>${esc(record.entity_name||"niet gekoppeld")}</div><div>${formatTime(record.last_seen_at)}</div><button class="button" data-mapping-edit="${record.id}">Koppelen</button></div>`).join(""):`<div class="empty-state">Nog geen providerrecords.</div>`;
  const openConflicts = state.data.conflicts.filter(item => item.status === "open");
  $("#conflict-count").textContent = openConflicts.length;
  $("#conflicts-list").innerHTML = openConflicts.length ? openConflicts.map(conflict => `<div class="mini-item"><strong>${esc(conflict.entity_name || "Onbekend device")} · ${esc(conflict.field)}</strong><p>Handmatig: ${esc(conflict.manual_value)}<br>${esc(conflict.provider_name)}: ${esc(conflict.observed_value)}</p><button class="button" data-resolve-conflict="${conflict.id}">Handmatig behouden</button></div>`).join("") : `<div class="empty-state">Geen open conflicten.</div>`;
  $("#backups-list").innerHTML = state.data.backups.length ? state.data.backups.slice(0,8).map(backup => `<div class="mini-item"><strong>${esc(backup.name)}</strong>${backup.portable ? ' <span class="pill">draagbaar</span>' : ""}<p>${formatTime(backup.created_at)} · ${formatBytes(backup.size)}</p><div class="row-actions"><a class="button" href="/api/backups/${encodeURIComponent(backup.name)}/download">Download</a><button class="button danger" data-backup-restore="${esc(backup.name)}">Herstel</button></div></div>`).join("") : `<div class="empty-state">Nog geen back-up gemaakt.</div>`;
  const entityNames=new Map(state.data.entities.map(entity=>[entity.id,entity.name]));
  $("#dns-list").innerHTML = state.data.dns_records.length ? state.data.dns_records.map(record=>`<div class="data-row dns-row"><div class="data-main"><span class="data-icon">DNS</span><div><strong>${esc(record.name)}</strong><span>${esc(record.source)}${record.entity_id?` · ${esc(entityNames.get(record.entity_id)||"device")}`:""}</span></div></div><div><span class="data-cell-label">Type</span><br>${esc(record.record_type)}</div><div><span class="data-cell-label">Waarde</span><br>${esc(record.value)}</div>${record.source==="manual"?`<div class="row-actions"><button class="button" data-dns-edit="${record.id}">Wijzig</button><button class="button danger" data-dns-delete="${record.id}">×</button></div>`:`<span class="pill">read-only</span>`}</div>`).join("") : `<div class="empty-state">Nog geen DNS-records. Voeg er één toe of synchroniseer AdGuard Home.</div>`;
  $("#proxy-count").textContent=`${state.data.proxy_hosts.length} hosts`;
  $("#proxy-list").innerHTML = state.data.proxy_hosts.length ? state.data.proxy_hosts.map(host=>`<div class="data-row proxy-row"><div class="data-main"><span class="data-icon">↗</span><div><strong>${esc((host.domains||[]).join(", ")||"Naamloos")}</strong><span>Nginx Proxy Manager · read-only</span></div></div><div><span class="data-cell-label">Doel</span><br>${esc(host.forward_scheme)}://${esc(host.forward_host)}:${host.forward_port||"—"}</div><div><span class="data-cell-label">Status</span><br>${statusDot(host.enabled?"up":"down")} ${host.enabled?"actief":"uit"}</div><span class="pill">${esc(entityNames.get(host.entity_id)||"niet gekoppeld")}</span></div>`).join("") : `<div class="empty-state">Nog geen proxyhosts. Configureer en synchroniseer Nginx Proxy Manager.</div>`;
  $("#audit-list").innerHTML=(state.data.audit_log||[]).length?state.data.audit_log.map(item=>`<div class="data-row audit-row"><div class="data-main"><span class="data-icon">⌁</span><div><strong>${esc(item.action)}</strong><span>${esc(item.username||"systeem")} · ${esc(item.target_type)}</span></div></div><div>${esc(item.target_id||"—")}</div><div>${formatTime(item.created_at)}</div></div>`).join(""):`<div class="empty-state">Nog geen beheeracties.</div>`;
}

function providerIcon(type) { return ({dhcp_arp:"⌁",uptime_kuma:"◉",glances:"▥",portainer:"⬡",proxmox:"◇",adguard:"DNS",nginx_proxy_manager:"↗"})[type] || "·"; }
function entityIcon(type) { return ({host:"▣",vm:"◇",lxc:"□",container:"⬡",service:"◉",camera:"●",nas:"▤"})[type] || "○"; }

function renderSpeedtest() {
  const speed=state.data.speedtest||{}, latest=speed.latest, settings=speed.settings||{};
  const fmt=value=>value===null||value===undefined?"—":Number(value).toFixed(1);
  $("#speed-down").textContent=latest?`${fmt(latest.download_mbps)} M`:"—";
  $("#speed-up").textContent=latest?`${fmt(latest.upload_mbps)} M`:"—";
  $("#speed-ping").textContent=latest?`${fmt(latest.ping_ms)} ms`:"— ms";
  $("#speed-age").textContent=speed.running?"test loopt…":latest?formatTime(latest.completed_at):settings.last_error?"laatste test mislukt":"nog niet gemeten";
  $("#speed-indicator").classList.toggle("running",Boolean(speed.running));
  [["down","download_mbps"],["up","upload_mbps"],["ping","ping_ms"],["jitter","jitter_ms"]].forEach(([id,key])=>$("#speed-detail-"+id).textContent=latest?fmt(latest[key]):"—");
  const history=(speed.history||[]).slice(0,24).reverse(), max=Math.max(1,...history.map(item=>Number(item.download_mbps)||0));
  $("#speed-chart").innerHTML=history.length?history.map(item=>`<div class="speed-bar-wrap" title="${esc(formatTime(item.completed_at))}: ↓ ${fmt(item.download_mbps)} / ↑ ${fmt(item.upload_mbps)} Mbps"><i class="speed-bar" style="height:${Math.max(3,(Number(item.download_mbps)||0)/max*100)}%"></i></div>`).join(""):`<div class="empty-state">Na de eerste test verschijnt hier de historie.</div>`;
  $("#speed-settings-summary").innerHTML=`<div class="mini-item"><strong>${settings.enabled?"Automatisch actief":"Automatisch uit"}</strong><p>Elke ${Math.round((settings.interval_seconds||21600)/3600)} uur · ${settings.duration_seconds||10}s testduur<br>${settings.last_error?esc(shorten(settings.last_error,90)):"Telemetry uit · lokaal opgeslagen"}</p></div>`;
}

function switchTab(tab) {
  state.activeTab = tab;
  $$(".tab").forEach(item => item.classList.toggle("active", item.dataset.tab === tab));
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `${tab}-view`));
  if (tab === "topology") renderTopology();
}

function openPort(portId, deviceId) {
  const device = state.data.physical_devices.find(item => item.id === deviceId);
  const port = device?.ports.find(item => item.id === portId);
  if (!device || !port) return;
  const form = $("#port-form");
  form.reset();
  form.elements.port_id.value = port.id;
  form.elements.cable_label.value = port.cable_label || "";
  form.elements.cable_color.value = port.cable_color || "";
  form.elements.notes.value = port.cable_notes || "";
  $("#drawer-title").textContent = port.label || `Poort ${port.number}`;
  $("#drawer-device").textContent = `${device.name}${port.side === "rear" ? " · achterzijde" : ""}`;
  $("#drawer-speed").textContent = `${port.speed_mbps || "?"} Mbps`;
  form.elements.entity_id.innerHTML = `<option value="">Niet aangesloten</option>${connectableEntities(port).map(entity => `<option value="${entity.id}">${esc(entity.name)}${entity.ip_address ? ` · ${esc(entity.ip_address)}` : ""}</option>`).join("")}`;
  form.elements.entity_id.value = state.pendingEntityId || (port.link_kind === "entity" ? port.entity_id : "") || "";
  form.elements.b_port_id.innerHTML = `<option value="">Geen doorverbinding</option>${freePorts(port).map(item => `<option value="${item.id}">${esc(item.display)}</option>`).join("")}`;
  form.elements.b_port_id.value = port.link_kind === "port" ? (port.target_port_id || "") : "";
  state.pendingEntityId = null;
  $("#disconnect-port").classList.toggle("hidden", !port.cable_id);
  renderTrace(port);
  $("#drawer-backdrop").classList.remove("hidden");
  $("#port-drawer").classList.add("open");
  $("#port-drawer").setAttribute("aria-hidden", "false");
}

// Devices die nog vrij zijn (of aan déze poort hangen); virtuele objecten
// hangen nooit direct aan een kabel.
function connectableEntities(port) {
  const linked = new Set(state.data.physical_devices.flatMap(device =>
    device.ports.filter(item => item.link_kind === "entity" && item.id !== port.id).map(item => item.entity_id)));
  return state.data.entities.filter(entity =>
    !linked.has(entity.id) && !["service", "container", "vm", "lxc"].includes(entity.type));
}

function freePorts(port) {
  return state.data.physical_devices.flatMap(device => device.ports
    .filter(item => item.id !== port.id && (!item.cable_id || item.id === port.target_port_id))
    .map(item => ({id: item.id, display: `${device.name} · poort ${item.number}${item.side === "rear" ? " (achter)" : ""}`})));
}

async function renderTrace(port) {
  const box = $("#drawer-trace");
  if (!port.cable_id) { box.innerHTML = ""; return; }
  try {
    const trace = await api(`/api/ports/${encodeURIComponent(port.id)}/trace`);
    box.innerHTML = `<span class="data-cell-label">Kabeltrace</span><div class="trace">${trace.steps.map(step =>
      `<span class="trace-step ${esc(step.kind)}">${step.kind === "entity" ? statusDot(step.status) : ""}${esc(step.label)}</span>`
    ).join('<span class="trace-arrow">→</span>')}</div>`;
  } catch { box.innerHTML = ""; }
}

function closeDrawer() {
  $("#drawer-backdrop").classList.add("hidden");
  $("#port-drawer").classList.remove("open");
  $("#port-drawer").setAttribute("aria-hidden", "true");
}

function openProvider(providerId) {
  const provider = state.data.providers.find(item => item.id === providerId);
  if (!provider) return;
  const form = $("#provider-form");
  form.reset();
  form.elements.provider_id.value = provider.id;
  form.elements.enabled.checked = provider.enabled;
  form.elements.poll_interval_seconds.value = provider.poll_interval_seconds;
  form.elements.config.value = JSON.stringify(provider.config, null, 2);
  $("#provider-credentials").innerHTML = (provider.credential_fields || []).length
    ? `<div class="section-bar"><span>Inloggegevens</span><span class="muted">versleuteld</span></div>${provider.credential_fields.map(field => {
        const configured = Boolean(provider.credentials_configured?.[field.key]);
        return `<div class="credential-row"><label>${esc(field.label)}<input data-credential-key="${esc(field.key)}" type="${field.type === "password" ? "password" : "text"}" autocomplete="new-password" placeholder="${configured ? "Opgeslagen · leeg laten om te behouden" : "Nog niet ingesteld"}"></label>${configured ? `<label class="toggle-label compact"><input type="checkbox" data-clear-credential="${esc(field.key)}"><span>Verwijderen</span></label>` : ""}</div>`;
      }).join("")}`
    : `<p class="muted tiny">Deze provider heeft geen inloggegevens nodig.</p>`;
  $("#provider-dialog-title").textContent = provider.name;
  $("#provider-dialog").showModal();
}

function openEntity(entityId=""){
  const form=$("#entity-form"),entity=entityId?state.data.entities.find(item=>item.id===entityId):null;form.reset();form.elements.entity_id.value=entityId;
  $("#entity-dialog-title").textContent=entity?"Device bewerken":"Device toevoegen";
  if(entity){["name","type","hostname","ip_address","mac_address","notes"].forEach(key=>form.elements[key].value=entity[key]||"");}
  $("#entity-dialog").showModal();
}

function openPhysical(deviceId=""){
  const form=$("#physical-form"),device=deviceId?state.data.physical_devices.find(item=>item.id===deviceId):null;form.reset();form.elements.device_id.value=deviceId;
  $("#physical-dialog-title").textContent=device?"Netwerkapparaat bewerken":"Netwerkapparaat toevoegen";
  if(device){["name","type","model","location","notes"].forEach(key=>form.elements[key].value=device[key]||"");form.elements.ports.value=device.ports.length;}
  $("#physical-dialog").showModal();
}

function openMerge(entityId){const source=state.data.entities.find(item=>item.id===entityId),form=$("#merge-form");form.elements.source_entity_id.value=entityId;$("#merge-title").textContent=`${source?.name||"Discovery"} samenvoegen`;form.elements.target_entity_id.innerHTML=state.data.entities.filter(item=>item.id!==entityId&&!item.archived).map(item=>`<option value="${item.id}">${esc(item.name)} · ${esc(item.origin)}</option>`).join("");$("#merge-dialog").showModal();}
function openMapping(recordId){const record=state.data.provider_records.find(item=>item.id===recordId),form=$("#mapping-form");form.elements.record_id.value=recordId;form.elements.entity_id.innerHTML=`<option value="">Niet gekoppeld</option>${state.data.entities.filter(item=>!item.archived).map(item=>`<option value="${item.id}">${esc(item.name)} · ${esc(item.origin)}</option>`).join("")}`;form.elements.entity_id.value=record?.entity_id||"";$("#mapping-dialog").showModal();}

function nodeOptions(selected="", exclude="") {
  return `<option value="">Geen parent</option>${(state.data.topology?.nodes||[]).filter(node=>node.id!==exclude).map(node=>`<option value="${esc(node.id)}" ${node.id===selected?"selected":""}>${esc(node.label)} · ${esc(node.node_type)}</option>`).join("")}`;
}

function openTopologyNode(nodeId) {
  const node=state.data.topology.nodes.find(item=>item.id===nodeId); if(!node)return;
  const form=$("#topology-node-form"); form.elements.node_id.value=node.id; form.elements.label.value=node.label; form.elements.subtitle.value=node.subtitle||"";
  form.elements.parent_node_id.innerHTML=nodeOptions(node.parent_node_id||"",node.id); form.elements.lifecycle.value=node.lifecycle||"active"; form.elements.collapsed.checked=Boolean(node.collapsed);
  $("#delete-topology-group").classList.toggle("hidden",node.reference_type!=="group");
  $("#topology-node-dialog").showModal();
}

function openDns(recordId="") {
  const record=recordId?state.data.dns_records.find(item=>item.id===recordId):null, form=$("#dns-form"); form.reset(); form.dataset.recordId=recordId;
  form.elements.entity_id.innerHTML=`<option value="">Geen</option>${state.data.entities.map(entity=>`<option value="${entity.id}">${esc(entity.name)}</option>`).join("")}`;
  if(record){form.elements.name.value=record.name;form.elements.record_type.value=record.record_type;form.elements.value.value=record.value;form.elements.ttl.value=record.ttl||"";form.elements.entity_id.value=record.entity_id||"";}
  $("#dns-dialog").showModal();
}

function prepareRelationDialog() {
  const options=(state.data.topology?.nodes||[]).map(node=>`<option value="${esc(node.id)}">${esc(node.label)}</option>`).join("");
  const form=$("#topology-relation-form");form.elements.from_node_id.innerHTML=options; form.elements.to_node_id.innerHTML=options;if(form.elements.to_node_id.options.length>1)form.elements.to_node_id.selectedIndex=1; $("#topology-relation-dialog").showModal();
}

let drag=null;
function updateSelectionUI(){const count=state.selectedNodes.size;$("#selection-status").textContent=count?`${count} node(s) geselecteerd · sleep samen of groepeer`:"Bewerkmodus · shift-klik voor multiselectie";$("#group-selection").classList.toggle("hidden",count<2);}
$("#topology-canvas").addEventListener("pointerdown",event=>{
  const group=event.target.closest("[data-node-id]"); if(!group)return;
  if(!state.editingTopology){return;}
  const nodeId=group.dataset.nodeId;
  if(event.shiftKey){state.selectedNodes.has(nodeId)?state.selectedNodes.delete(nodeId):state.selectedNodes.add(nodeId);renderTopology();event.preventDefault();return;}
  if(!state.selectedNodes.has(nodeId)){state.selectedNodes.clear();state.selectedNodes.add(nodeId);}
  const rect=event.currentTarget.getBoundingClientRect(),view=event.currentTarget.viewBox.baseVal;
  const items=[...state.selectedNodes].map(id=>({id,p:state.topologyPositions.get(id),group:[...event.currentTarget.querySelectorAll("[data-node-id]")].find(item=>item.dataset.nodeId===id)})).filter(item=>item.p&&item.group);
  drag={nodeId,items,startX:event.clientX,startY:event.clientY,scaleX:view.width/rect.width,scaleY:view.height/rect.height,moved:false}; group.setPointerCapture(event.pointerId); event.preventDefault();
});
$("#topology-canvas").addEventListener("pointermove",event=>{
  if(drag){const dx=(event.clientX-drag.startX)*drag.scaleX,dy=(event.clientY-drag.startY)*drag.scaleY;drag.moved=drag.moved||Math.abs(dx)+Math.abs(dy)>4;drag.items.forEach(item=>item.group.setAttribute("transform",`translate(${item.p.x+dx} ${item.p.y+dy})`));return;}
  const group=event.target.closest("[data-node-id]"),tooltip=$("#topology-tooltip"); if(!group||state.editingTopology){tooltip.classList.add("hidden");return;}
  const node=state.data.topology.nodes.find(item=>item.id===group.dataset.nodeId);if(!node)return;const m=node.metrics||{};
  tooltip.innerHTML=`<strong>${esc(node.label)}</strong><span>${esc(node.subtitle||node.node_type)}</span><span>status: ${esc(node.status||"unknown")}${node.ip_address?` · ${esc(node.ip_address)}`:""}</span>${m.cpu_percent!==undefined?`<span>cpu: ${esc(m.cpu_percent)}%</span>`:""}${m.latency_ms!==undefined?`<span>response: ${esc(m.latency_ms)} ms</span>`:""}${m.sources?.length?`<span>bron: ${esc(m.sources.join(" + "))}</span>`:""}`;
  tooltip.style.left=`${event.clientX+14}px`;tooltip.style.top=`${event.clientY+14}px`;tooltip.classList.remove("hidden");
});
$("#topology-canvas").addEventListener("pointerleave",()=>$("#topology-tooltip").classList.add("hidden"));
$("#topology-canvas").addEventListener("pointerup",async event=>{
  if(!drag)return;const current=drag;drag=null;
  if(!current.moved){if(state.selectedNodes.size===1)openTopologyNode(current.nodeId);renderTopology();return;}
  const dx=(event.clientX-current.startX)*current.scaleX,dy=(event.clientY-current.startY)*current.scaleY;
  const positions=current.items.map(item=>({id:item.id,x:item.p.x+dx,y:item.p.y+dy}));
  try{await api("/api/topology/positions",{method:"PATCH",body:JSON.stringify({positions})});await loadData(true);}catch(error){toast(error.message,"error");renderTopology();}
});

async function confirmDeletion(kind,id) {
  const base=kind==="entity"?"/api/entities":"/api/physical-devices", label=kind==="entity"?"device":"netwerkapparaat";
  try {
    const impact=await api(`${base}/${encodeURIComponent(id)}/deletion-impact`);
    if(!impact.deletable){toast("Dit geïmporteerde device beheer je in de databron","error");return;}
    const c=impact.counts||{}, lines=[];
    if(c.cables)lines.push(`${c.cables} kabelkoppeling(en) worden verwijderd`);
    if(kind==="physical"&&c.ports)lines.push(`${c.ports} poorten worden verwijderd`);
    if(c.children)lines.push(`${c.children} onderliggende node(s) worden losgekoppeld`);
    if(c.dns_records)lines.push(`${c.dns_records} DNS-record(s) blijven bestaan maar worden losgekoppeld`);
    if(c.proxy_hosts)lines.push(`${c.proxy_hosts} proxyhost-koppeling(en) worden losgekoppeld`);
    if(c.provider_links)lines.push(`${c.provider_links} providerkoppeling(en) worden losgemaakt; het device kan bij een volgende sync terugkeren als discovery`);
    if(c.topology_relations)lines.push(`${c.topology_relations} topologierelatie(s) verdwijnen`);
    const message=`${impact.name} verwijderen?\n\n${lines.length?lines.map(line=>`• ${line}`).join("\n"):"Er zijn geen gekoppelde gegevens."}\n\nDit kan niet ongedaan worden gemaakt, behalve via een back-up.`;
    if(!confirm(message))return;
    await api(`${base}/${encodeURIComponent(id)}?confirm=${encodeURIComponent(impact.name)}`,{method:"DELETE"});
    await loadData(true);toast(`${label[0].toUpperCase()+label.slice(1)} verwijderd`);
  } catch(error){toast(error.message,"error");}
}

document.addEventListener("click", async event => {
  const tab = event.target.closest("[data-tab]"); if (tab) switchTab(tab.dataset.tab);
  const port = event.target.closest("[data-port-id]"); if (port) openPort(port.dataset.portId, port.dataset.deviceId);
  const edit = event.target.closest("[data-provider-edit]"); if (edit) openProvider(edit.dataset.providerEdit);
  const sync = event.target.closest("[data-provider-sync]");
  if (sync) {
    sync.disabled = true; sync.textContent = "Ophalen…";
    try { const result = await api(`/api/providers/${sync.dataset.providerSync}/sync`, {method:"POST"}); toast(`${result.records} records verwerkt`); await loadData(true); }
    catch (error) { toast(error.message, "error"); } finally { sync.disabled = false; sync.textContent = "Nu ophalen"; }
  }
  const link = event.target.closest("[data-link-entity]");
  if (link) { state.pendingEntityId = link.dataset.linkEntity; switchTab("patch"); toast("Kies de fysieke poort voor dit device"); }
  const resolve = event.target.closest("[data-resolve-conflict]");
  if (resolve) { try { await api(`/api/conflicts/${resolve.dataset.resolveConflict}/resolve`, {method:"POST",body:JSON.stringify({resolution:"manual_kept"})}); await loadData(true); toast("Handmatige waarde behouden"); } catch(error){toast(error.message,"error");} }
  const dnsEdit=event.target.closest("[data-dns-edit]");if(dnsEdit)openDns(dnsEdit.dataset.dnsEdit);
  const dnsDelete=event.target.closest("[data-dns-delete]");if(dnsDelete&&confirm("Dit handmatige DNS-record verwijderen?")){try{await api(`/api/dns-records/${dnsDelete.dataset.dnsDelete}`,{method:"DELETE"});await loadData(true);toast("DNS-record verwijderd");}catch(error){toast(error.message,"error");}}
  const relation=event.target.closest(".relation-hit.manual");if(relation&&state.editingTopology&&confirm("Deze handmatige relatie verwijderen?")){try{await api(`/api/topology/relations/${encodeURIComponent(relation.dataset.relationId)}`,{method:"DELETE"});await loadData(true);toast("Relatie verwijderd");}catch(error){toast(error.message,"error");}}
  const entityDelete=event.target.closest("[data-entity-delete]");if(entityDelete)confirmDeletion("entity",entityDelete.dataset.entityDelete);
  const physicalDelete=event.target.closest("[data-physical-delete]");if(physicalDelete)confirmDeletion("physical",physicalDelete.dataset.physicalDelete);
  const entityEdit=event.target.closest("[data-entity-edit]");if(entityEdit)openEntity(entityEdit.dataset.entityEdit);
  const physicalEdit=event.target.closest("[data-physical-edit]");if(physicalEdit)openPhysical(physicalEdit.dataset.physicalEdit);
  const merge=event.target.closest("[data-merge-entity]");if(merge)openMerge(merge.dataset.mergeEntity);
  const mapping=event.target.closest("[data-mapping-edit]");if(mapping)openMapping(mapping.dataset.mappingEdit);
  const discovery=event.target.closest("[data-discovery-state]");
  if(discovery){const mode=discovery.dataset.discoveryState,payload=mode==="ignore"?{ignored:true,archived:false}:mode==="archive"?{ignored:false,archived:true}:{ignored:false,archived:false};try{await api(`/api/entities/${discovery.dataset.entityId}/discovery-state`,{method:"PATCH",body:JSON.stringify(payload)});await loadData(true);toast(mode==="restore"?"Discovery hersteld":mode==="ignore"?"Discovery genegeerd":"Discovery gearchiveerd");}catch(error){toast(error.message,"error");}}
  const restore=event.target.closest("[data-backup-restore]");if(restore){const name=restore.dataset.backupRestore;if(confirm(`${name} terugzetten?\n\nDe huidige database wordt eerst automatisch veiliggesteld. Je sessie kan daarna verlopen.`)){try{await api(`/api/backups/${encodeURIComponent(name)}/restore?confirm=${encodeURIComponent(name)}`,{method:"POST"});toast("Back-up hersteld; app wordt herladen");setTimeout(()=>location.reload(),800);}catch(error){toast(error.message,"error");}}}
});

$("#auth-form").addEventListener("submit", async event => {
  event.preventDefault();
  const element = event.currentTarget, form = new FormData(element);
  $("#auth-error").textContent = "";
  try {
    const result = await api(state.setupRequired ? "/api/auth/setup" : "/api/auth/login", {method:"POST", body:JSON.stringify(Object.fromEntries(form))});
    state.csrf = result.csrf_token;
    $("#username").textContent = result.username;
    $("#avatar").textContent = result.username.slice(0,1).toUpperCase();
    element.reset();
    await showApp();
  } catch (error) { $("#auth-error").textContent = error.message; }
});

$("#logout-button").addEventListener("click", async () => { try { await api("/api/auth/logout", {method:"POST"}); state.csrf = null; showAuth(false); } catch(error){toast(error.message,"error");} });
$("#refresh-button").addEventListener("click", () => loadData());
$("#new-entity-button").addEventListener("click", () => openEntity());
$("#new-physical-button").addEventListener("click", () => openPhysical());
$$('.modal-close').forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$$('.drawer-close').forEach(button => button.addEventListener("click", closeDrawer));
$("#drawer-backdrop").addEventListener("click", closeDrawer);
$$('[data-layer]').forEach(input => input.addEventListener("change", renderTopology));

$("#entity-form").addEventListener("submit", async event => {
  event.preventDefault(); const form=event.currentTarget, payload = Object.fromEntries(new FormData(form)),entityId=payload.entity_id;delete payload.entity_id;
  try { await api(entityId?`/api/entities/${entityId}`:"/api/entities", {method:entityId?"PATCH":"POST", body:JSON.stringify(payload)}); form.reset(); $("#entity-dialog").close(); await loadData(true); toast(entityId?"Device bijgewerkt":"Device toegevoegd"); } catch(error){toast(error.message,"error");}
});

$("#physical-form").addEventListener("submit", async event => {
  event.preventDefault(); const form=event.currentTarget, payload = Object.fromEntries(new FormData(form)),deviceId=payload.device_id;delete payload.device_id;payload.ports = Number(payload.ports);
  try { await api(deviceId?`/api/physical-devices/${deviceId}`:"/api/physical-devices", {method:deviceId?"PATCH":"POST", body:JSON.stringify(payload)}); form.reset(); $("#physical-dialog").close(); await loadData(true); toast(deviceId?"Netwerkapparaat bijgewerkt":"Netwerkapparaat toegevoegd"); } catch(error){toast(error.message,"error");}
});

$("#port-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget, portId = form.elements.port_id.value;
  const payload = {
    b_entity_id: form.elements.entity_id.value || null,
    b_port_id: form.elements.b_port_id.value || null,
    label: form.elements.cable_label.value, color: form.elements.cable_color.value,
    notes: form.elements.notes.value,
  };
  if (payload.b_entity_id && payload.b_port_id) return toast("Kies één ander uiteinde: een device óf een poort", "error");
  try {
    if (payload.b_entity_id || payload.b_port_id) await api(`/api/ports/${encodeURIComponent(portId)}/cable`, {method:"PUT", body:JSON.stringify(payload)});
    else await api(`/api/ports/${encodeURIComponent(portId)}/cable`, {method:"DELETE"});
    closeDrawer(); await loadData(true); toast(payload.b_entity_id || payload.b_port_id ? "Kabel opgeslagen" : "Poort vrijgemaakt");
  } catch(error){toast(error.message,"error");}
});

$("#disconnect-port").addEventListener("click", async () => {
  const portId = $("#port-form").elements.port_id.value;
  try { await api(`/api/ports/${encodeURIComponent(portId)}/cable`, {method:"DELETE"}); closeDrawer(); await loadData(true); toast("Kabel losgekoppeld"); } catch(error){toast(error.message,"error");}
});

$("#provider-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget;
  try {
    const config = JSON.parse(form.elements.config.value);
    const credentials = {};
    $$('[data-credential-key]', form).forEach(input => { if (input.value.trim()) credentials[input.dataset.credentialKey] = input.value; });
    const clear_credentials = $$('[data-clear-credential]:checked', form).map(input => input.dataset.clearCredential);
    await api(`/api/providers/${form.elements.provider_id.value}`, {method:"PATCH", body:JSON.stringify({enabled:form.elements.enabled.checked,poll_interval_seconds:Number(form.elements.poll_interval_seconds.value),config,credentials,clear_credentials})});
    $("#provider-dialog").close(); await loadData(true); toast("Providerconfiguratie opgeslagen");
  } catch(error){toast(error instanceof SyntaxError ? "Configuratie bevat ongeldige JSON" : error.message,"error");}
});

$("#app-settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const result = await api("/api/settings", {method:"PATCH", body:JSON.stringify({title:form.elements.title.value})});
    state.data.site.title = result.title;
    applyBranding(result);
    toast("Titel opgeslagen");
  } catch(error){toast(error.message,"error");}
});

$("#backup-now").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try { const backup = await api("/api/backups", {method:"POST"}); await loadData(true); toast(`Back-up ${backup.name} gemaakt`); } catch(error){toast(error.message,"error");} finally {button.disabled=false;}
});

$("#merge-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget,payload=Object.fromEntries(new FormData(form)),source=payload.source_entity_id;delete payload.source_entity_id;try{await api(`/api/entities/${source}/merge`,{method:"POST",body:JSON.stringify(payload)});form.closest("dialog").close();await loadData(true);toast("Discovery samengevoegd");}catch(error){toast(error.message,"error");}});
$("#mapping-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget,payload=Object.fromEntries(new FormData(form)),recordId=payload.record_id;delete payload.record_id;payload.entity_id=payload.entity_id||null;try{await api(`/api/provider-records/${recordId}/mapping`,{method:"PATCH",body:JSON.stringify(payload)});form.closest("dialog").close();await loadData(true);toast("Bronkoppeling opgeslagen");}catch(error){toast(error.message,"error");}});

$("#topology-edit").addEventListener("click",()=>{state.editingTopology=true;state.selectedNodes.clear();$("#topology-editbar").classList.remove("hidden");$("#topology-edit").classList.add("hidden");renderTopology();});
$("#finish-edit").addEventListener("click",()=>{state.editingTopology=false;state.selectedNodes.clear();$("#topology-editbar").classList.add("hidden");$("#topology-edit").classList.remove("hidden");renderTopology();});
$("#add-group").addEventListener("click",()=>{state.groupSelected=false;$("#topology-group-dialog").showModal();});
$("#group-selection").addEventListener("click",()=>{state.groupSelected=true;$("#topology-group-dialog").showModal();});
$("#add-relation").addEventListener("click",prepareRelationDialog);
$("#reset-layout").addEventListener("click",async()=>{try{await api("/api/topology/layout/reset",{method:"POST"});await loadData(true);toast("Automatische indeling hersteld");}catch(error){toast(error.message,"error");}});
$("#topology-undo").addEventListener("click",async()=>{try{const result=await api("/api/topology/undo",{method:"POST"});state.selectedNodes.clear();await loadData(true);toast(`${result.undone} ongedaan gemaakt`);}catch(error){toast(error.message,"error");}});
$("#topology-node-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget,payload=Object.fromEntries(new FormData(form)),nodeId=payload.node_id;delete payload.node_id;payload.parent_node_id=payload.parent_node_id||null;payload.collapsed=form.elements.collapsed.checked;payload.hidden=false;try{await api(`/api/topology/nodes/${encodeURIComponent(nodeId)}`,{method:"PATCH",body:JSON.stringify(payload)});form.closest("dialog").close();await loadData(true);toast("Node opgeslagen");}catch(error){toast(error.message,"error");}});
$("#topology-group-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget,payload=Object.fromEntries(new FormData(form));payload.node_ids=state.groupSelected?[...state.selectedNodes]:[];try{await api("/api/topology/groups",{method:"POST",body:JSON.stringify(payload)});state.selectedNodes.clear();state.groupSelected=false;form.reset();form.closest("dialog").close();await loadData(true);toast(payload.node_ids.length?"Selectie gegroepeerd":"Groep toegevoegd");}catch(error){toast(error.message,"error");}});
$("#topology-relation-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget;try{await api("/api/topology/relations",{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(form)))});form.reset();form.closest("dialog").close();await loadData(true);toast("Relatie toegevoegd");}catch(error){toast(error.message,"error");}});
$("#delete-topology-group").addEventListener("click",async()=>{const form=$("#topology-node-form"),node=state.data.topology.nodes.find(item=>item.id===form.elements.node_id.value);if(node&&confirm(`${node.label} verwijderen? Kinderen worden uit de groep gehaald.`)){try{await api(`/api/topology/groups/${encodeURIComponent(node.id)}?confirm=${encodeURIComponent(node.label)}`,{method:"DELETE"});form.closest("dialog").close();state.selectedNodes.delete(node.id);await loadData(true);toast("Groep verwijderd");}catch(error){toast(error.message,"error");}}});

$("#new-dns-record").addEventListener("click",()=>openDns());
$("#dns-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget,payload=Object.fromEntries(new FormData(form));payload.ttl=payload.ttl?Number(payload.ttl):null;payload.entity_id=payload.entity_id||null;payload.enabled=true;const path=form.dataset.recordId?`/api/dns-records/${form.dataset.recordId}`:"/api/dns-records";try{await api(path,{method:form.dataset.recordId?"PATCH":"POST",body:JSON.stringify(payload)});form.closest("dialog").close();await loadData(true);toast("DNS-record opgeslagen");}catch(error){toast(error.message,"error");}});

function downloadJson(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"})),link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("#config-export").addEventListener("click",async()=>{try{const config=await api("/api/config/export");downloadJson(config,`plugnet-config-${new Date().toISOString().slice(0,10)}.json`);toast("Configuratie geëxporteerd");}catch(error){toast(error.message,"error");}});
$("#config-import-button").addEventListener("click",()=>$("#config-import-file").click());
$("#config-import-file").addEventListener("change",async event=>{const file=event.target.files[0];if(!file)return;try{const payload=JSON.parse(await file.text());if(!confirm(`${file.name} samenvoegen met de huidige configuratie? Eerst wordt automatisch een back-up gemaakt.`))return;const result=await api("/api/config/import",{method:"POST",body:JSON.stringify(payload)});await loadData(true);toast(`${result.records} configuratierecords geïmporteerd`);}catch(error){toast(error instanceof SyntaxError?"Ongeldig JSON-bestand":error.message,"error");}finally{event.target.value="";}});
$("#backup-import-button").addEventListener("click",()=>$("#backup-import-file").click());
$("#backup-import-file").addEventListener("change",async event=>{const file=event.target.files[0];if(!file)return;const data=new FormData();data.append("file",file);try{const response=await fetch("/api/backups/import",{method:"POST",headers:{"X-CSRF-Token":state.csrf},body:data});const body=await response.json();if(!response.ok)throw new Error(errorMessage(body,response.status));await loadData(true);toast(`Back-up ${body.name} geïmporteerd`);}catch(error){toast(error.message,"error");}finally{event.target.value="";}});

$("#speed-indicator").addEventListener("click",()=>{switchTab("topology");setTimeout(()=>$("#speed-history-card").scrollIntoView({behavior:"smooth",block:"center"}),50);});
$("#run-speedtest").addEventListener("click",async event=>{const button=event.currentTarget;button.disabled=true;button.textContent="Test loopt…";try{const result=await api("/api/speedtest/run",{method:"POST"});await loadData(true);toast(result.status==="success"?"Speedtest voltooid":result.error||"Speedtest is al bezig",result.status==="failed"?"error":"success");}catch(error){toast(error.message,"error");}finally{button.disabled=false;button.textContent="Nu testen";}});
$("#speed-settings-button").addEventListener("click",()=>{const settings=state.data.speedtest.settings,form=$("#speed-form");form.elements.enabled.checked=settings.enabled;form.elements.interval_seconds.value=String(settings.interval_seconds);form.elements.server_id.value=settings.server_id||"";form.elements.interface_name.value=settings.interface_name||"";form.elements.duration_seconds.value=settings.duration_seconds;$("#speed-dialog").showModal();});
$("#speed-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget,payload=Object.fromEntries(new FormData(form));payload.enabled=form.elements.enabled.checked;payload.interval_seconds=Number(payload.interval_seconds);payload.duration_seconds=Number(payload.duration_seconds);payload.server_id=payload.server_id||null;payload.interface_name=payload.interface_name||null;try{await api("/api/speedtest/settings",{method:"PATCH",body:JSON.stringify(payload)});form.closest("dialog").close();await loadData(true);toast("Speedtestinstellingen opgeslagen");}catch(error){toast(error.message,"error");}});

setInterval(()=>{if(state.data&&!document.hidden)pollSummary();},30000);

initialize();

/* ---------------------------------------------------------------- wizard */
// Geen server-side wizardstatus: elke stap doet gewone mutaties, dus
// afbreken laat nooit een halve toestand achter.
const wizard = {step: 1, providers: ["dhcp-arp", "proxmox", "portainer", "glances", "adguard", "nginx-proxy-manager", "uptime-kuma"], created: 0, merged: 0, ignored: 0};

function wizardShow(step) {
  wizard.step = Math.min(4, Math.max(1, step));
  $$(".wizard-panel").forEach(panel => panel.classList.toggle("hidden", Number(panel.dataset.panel) !== wizard.step));
  $$("#wizard-steps li").forEach(item => {
    const index = Number(item.dataset.step);
    item.classList.toggle("active", index === wizard.step);
    item.classList.toggle("done", index < wizard.step);
  });
  $("#wizard-title").textContent = ["Netwerk scannen", "Databronnen koppelen", "Apparaten toewijzen", "Klaar"][wizard.step - 1];
  $("#wizard-back").disabled = wizard.step === 1;
  $("#wizard-skip").classList.toggle("hidden", wizard.step === 4);
  $("#wizard-next").textContent = wizard.step === 4 ? "Sluiten" : "Volgende";
  if (wizard.step === 2) renderWizardProviders();
  if (wizard.step === 3) renderAssignList($("#wizard-assign"));
  if (wizard.step === 4) renderWizardSummary();
}

async function openWizard() {
  const info = await api("/api/wizard/info");
  $("#wizard-subnets").value = info.suggested_subnet || "";
  $("#wizard-subnet-hint").textContent = `Toegestaan volgens PATCH_TRUSTED_SUBNETS: ${info.trusted_subnets.join(", ")}. Maximaal 1024 adressen per subnet.`;
  Object.assign(wizard, {created: 0, merged: 0, ignored: 0});
  $("#wizard-scan-results").innerHTML = "";
  $("#wizard-scan-state").textContent = "";
  wizardShow(1);
  $("#wizard-dialog").showModal();
}

async function closeWizard() {
  $("#wizard-dialog").close();
  try { await api("/api/wizard/info", {method:"PATCH", body:JSON.stringify({dismissed:true})}); } catch { /* niet kritiek */ }
  await loadData(true);
}

async function runScan() {
  const subnets = $("#wizard-subnets").value.split(",").map(value => value.trim()).filter(Boolean);
  const button = $("#wizard-scan");
  button.disabled = true;
  $("#wizard-scan-state").textContent = "bezig met scannen…";
  // Tijdens de scan verschijnen resultaten al: elke vondst wordt los opgeslagen.
  const ticker = setInterval(() => renderScanResults(), 2000);
  try {
    await api("/api/providers/dhcp-arp", {method:"PATCH", body:JSON.stringify({
      enabled: true, poll_interval_seconds: 300, config: {subnets, scan: subnets.length > 0}, credentials: {},
    })});
    const result = await api("/api/providers/dhcp-arp/sync", {method:"POST"});
    $("#wizard-scan-state").textContent = `${result.records} apparaten gevonden`;
  } catch (error) {
    $("#wizard-scan-state").textContent = error.message;
  } finally {
    clearInterval(ticker);
    button.disabled = false;
    await renderScanResults();
  }
}

async function renderScanResults() {
  try {
    const rows = await api("/api/discoveries");
    $("#wizard-scan-results").innerHTML = rows.length ? rows.map(row => `
      <div class="data-row discovery-row">
        <div class="data-main"><span class="data-icon">${entityIcon(row.type)}</span><div><strong>${esc(row.name)}</strong><span>${esc(row.vendor || row.mac_address || row.type)}</span></div></div>
        <div>${esc(row.ip_address || "—")}</div>
        <div>${esc(row.mac_address || "—")}</div>
        <div>${esc(row.hostname || "")}</div>
      </div>`).join("") : `<div class="empty-state">Nog niets gevonden.</div>`;
  } catch { /* stille refresh */ }
}

function renderWizardProviders() {
  $("#wizard-providers").innerHTML = wizard.providers.filter(id => id !== "dhcp-arp").map(id => {
    const provider = state.data.providers.find(item => item.id === id);
    if (!provider) return "";
    const fields = provider.credential_fields || [];
    return `<article class="provider-card" data-wizard-provider="${esc(id)}">
      <div class="provider-head"><div class="data-main"><span class="provider-icon">${providerIcon(provider.type)}</span><div><div class="provider-name">${esc(provider.name)}</div><div class="provider-type">${esc(provider.type)}</div></div></div></div>
      <label class="tiny">Basis-URL<input data-wizard-url placeholder="https://host:poort" value="${esc(provider.config.base_url || (provider.config.endpoints?.[0]?.url) || "")}"></label>
      ${fields.map(field => `<label class="tiny">${esc(field.label)}<input data-wizard-cred="${esc(field.key)}" type="${field.type === "password" ? "password" : "text"}" autocomplete="new-password"></label>`).join("")}
      <div class="provider-actions">
        <button class="button" data-wizard-test="${esc(id)}">Test verbinding</button>
        <button class="button primary" data-wizard-save="${esc(id)}" disabled>Opslaan en ophalen</button>
      </div>
      <p class="tiny wizard-result"></p>
    </article>`;
  }).join("");
}

function wizardProviderPayload(card) {
  const provider = state.data.providers.find(item => item.id === card.dataset.wizardProvider);
  const url = $("[data-wizard-url]", card).value.trim();
  const config = {...provider.config};
  // Glances gebruikt een endpointlijst, de rest één base_url.
  if (provider.type === "glances") config.endpoints = url ? [{name: "host", url}] : [];
  else config.base_url = url;
  const credentials = {};
  $$("[data-wizard-cred]", card).forEach(input => { if (input.value.trim()) credentials[input.dataset.wizardCred] = input.value; });
  return {provider, config, credentials};
}

async function wizardTestProvider(card) {
  const {provider, config, credentials} = wizardProviderPayload(card);
  const result = $(".wizard-result", card), save = $("[data-wizard-save]", card);
  result.textContent = "testen…";
  result.className = "tiny wizard-result";
  try {
    const response = await api(`/api/providers/${provider.id}/test`, {method:"POST", body:JSON.stringify({config, credentials})});
    result.textContent = response.summary;
    result.classList.add(response.ok ? "ok" : "error");
    save.disabled = !response.ok;
  } catch (error) {
    result.textContent = error.message;
    result.classList.add("error");
  }
}

async function wizardSaveProvider(card) {
  const {provider, config, credentials} = wizardProviderPayload(card);
  const result = $(".wizard-result", card);
  try {
    await api(`/api/providers/${provider.id}`, {method:"PATCH", body:JSON.stringify({
      enabled: true, poll_interval_seconds: provider.poll_interval_seconds, config, credentials,
    })});
    const sync = await api(`/api/providers/${provider.id}/sync`, {method:"POST"});
    result.textContent = `opgeslagen · ${sync.records ?? 0} records opgehaald`;
    result.className = "tiny wizard-result ok";
    await loadData(true);
  } catch (error) {
    result.textContent = error.message;
    result.className = "tiny wizard-result error";
  }
}

// Bulk-toewijsscherm: ook los bruikbaar vanuit Admin.
async function renderAssignList(container) {
  const rows = await api("/api/discoveries");
  const ports = state.data.physical_devices.flatMap(device => device.ports
    .filter(port => !port.cable_id)
    .map(port => ({id: port.id, label: `${device.name} · poort ${port.number}${port.side === "rear" ? " (achter)" : ""}`})));
  const targets = state.data.entities.filter(entity => entity.origin === "manual");
  container.innerHTML = rows.length ? rows.map(row => `
    <div class="assign-row" data-assign-id="${esc(row.id)}">
      <div class="data-main"><span class="data-icon">${entityIcon(row.type)}</span>
        <div><strong>${esc(row.name)}</strong><span>${esc(row.vendor || row.type)} · ${esc(row.ip_address || row.mac_address || "")}</span></div></div>
      <select data-assign-action aria-label="Actie voor ${esc(row.name)}">
        <option value="later" selected>Later beslissen</option>
        <option value="promote">Overnemen als device</option>
        <option value="merge">Samenvoegen met…</option>
        <option value="ignore">Negeren</option>
      </select>
      <select data-assign-target class="hidden" aria-label="Doeldevice">${targets.map(entity => `<option value="${esc(entity.id)}">${esc(entity.name)}</option>`).join("")}</select>
      <select data-assign-port class="hidden" aria-label="Poort"><option value="">Nog geen poort</option>${ports.map(port => `<option value="${esc(port.id)}">${esc(port.label)}</option>`).join("")}</select>
    </div>`).join("") : `<div class="empty-state">Geen open discoveries. Scan het netwerk of synchroniseer een databron.</div>`;
}

async function applyAssignments(container, stateLabel) {
  const rows = $$("[data-assign-id]", container);
  let created = 0, merged = 0, ignored = 0, failed = 0;
  for (const row of rows) {
    const id = row.dataset.assignId, action = $("[data-assign-action]", row).value;
    try {
      if (action === "promote") {
        await api(`/api/entities/${encodeURIComponent(id)}/promote`, {method:"POST", body:JSON.stringify({})});
        const portId = $("[data-assign-port]", row).value;
        if (portId) await api(`/api/ports/${encodeURIComponent(portId)}/cable`, {method:"PUT", body:JSON.stringify({b_entity_id:id})});
        created += 1;
      } else if (action === "merge") {
        await api(`/api/entities/${encodeURIComponent(id)}/merge`, {method:"POST", body:JSON.stringify({target_entity_id:$("[data-assign-target]", row).value})});
        merged += 1;
      } else if (action === "ignore") {
        await api(`/api/entities/${encodeURIComponent(id)}/discovery-state`, {method:"PATCH", body:JSON.stringify({ignored:true, archived:false})});
        ignored += 1;
      }
    } catch (error) { failed += 1; toast(`${id}: ${error.message}`, "error"); }
  }
  Object.assign(wizard, {created: wizard.created + created, merged: wizard.merged + merged, ignored: wizard.ignored + ignored});
  await loadData(true);
  await renderAssignList(container);
  if (stateLabel) $(stateLabel).textContent = `${created} overgenomen · ${merged} samengevoegd · ${ignored} genegeerd${failed ? ` · ${failed} mislukt` : ""}`;
  toast(`${created + merged + ignored} keuze(s) toegepast`);
}

async function renderWizardSummary() {
  const open = (await api("/api/discoveries").catch(() => [])).length;
  $("#wizard-summary").innerHTML = `<div class="mini-item">
    <strong>${wizard.created} overgenomen · ${wizard.merged} samengevoegd · ${wizard.ignored} genegeerd</strong>
    <p>${open} discovery(s) staan nog open.<br>Bekijk het resultaat in de <a href="#" data-goto="patch">patchview</a> of de <a href="#" data-goto="topology">topologie</a>.</p></div>`;
}

$("#wizard-scan").addEventListener("click", runScan);
$("#wizard-back").addEventListener("click", () => wizardShow(wizard.step - 1));
$("#wizard-skip").addEventListener("click", () => wizardShow(wizard.step + 1));
$("#wizard-next").addEventListener("click", () => wizard.step === 4 ? closeWizard() : wizardShow(wizard.step + 1));
$("#wizard-close").addEventListener("click", closeWizard);
$("#wizard-apply").addEventListener("click", () => applyAssignments($("#wizard-assign"), "#wizard-apply-state"));
$("#open-wizard").addEventListener("click", openWizard);
$("#open-assign").addEventListener("click", async () => { await renderAssignList($("#assign-standalone")); $("#assign-dialog").showModal(); });
$("#assign-apply").addEventListener("click", () => applyAssignments($("#assign-standalone"), null));

$("#wizard-dialog").addEventListener("click", event => {
  const test = event.target.closest("[data-wizard-test]"); if (test) wizardTestProvider(test.closest("[data-wizard-provider]"));
  const save = event.target.closest("[data-wizard-save]"); if (save) wizardSaveProvider(save.closest("[data-wizard-provider]"));
  const goto = event.target.closest("[data-goto]");
  if (goto) { event.preventDefault(); closeWizard().then(() => switchTab(goto.dataset.goto)); }
});

// Toon/verberg de vervolgkeuzes bij "samenvoegen" en "overnemen".
document.addEventListener("change", event => {
  const select = event.target.closest("[data-assign-action]");
  if (!select) return;
  const row = select.closest("[data-assign-id]");
  $("[data-assign-target]", row).classList.toggle("hidden", select.value !== "merge");
  $("[data-assign-port]", row).classList.toggle("hidden", select.value !== "promote");
});

/* --------------------------------------------------- drag-and-drop koppelen */
// HTML5-drag voor muis; pointer-events voor touch; en een select-dialoog voor
// wie geen van beide gebruikt. Alle drie lopen via dezelfde functies hierboven.
const patchGrid = () => $("#patch-view");

document.addEventListener("dragstart", event => {
  const chip = event.target.closest("[data-drag-entity]");
  const face = event.target.closest(".port-face.occupied");
  if (chip) dragPayload = {kind: "entity", id: chip.dataset.dragEntity};
  else if (face) dragPayload = {kind: "port", id: face.dataset.portId};
  else return;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", dragPayload.id);
  (chip || face).classList.add("dragging");
});

document.addEventListener("dragend", event => {
  $$(".dragging").forEach(item => item.classList.remove("dragging"));
  $$(".drop-target").forEach(item => item.classList.remove("drop-target"));
  dragPayload = null;
});

document.addEventListener("dragover", event => {
  const target = event.target.closest("[data-drop-port]");
  if (!target || !dragPayload) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  target.classList.add("drop-target");
});

document.addEventListener("dragleave", event => {
  const target = event.target.closest("[data-drop-port]");
  if (target) target.classList.remove("drop-target");
});

document.addEventListener("drop", async event => {
  const target = event.target.closest("[data-drop-port]");
  if (!target || !dragPayload) return;
  event.preventDefault();
  target.classList.remove("drop-target");
  const payload = dragPayload;
  dragPayload = null;
  if (payload.kind === "entity") await linkEntityToPort(payload.id, target.dataset.dropPort);
  else if (payload.id !== target.dataset.dropPort) await moveCable(payload.id, target.dataset.dropPort);
});

// Slepen naar de zijlijst = loskoppelen.
$("#unpatched-drop").addEventListener("dragover", event => { if (dragPayload?.kind === "port") { event.preventDefault(); event.currentTarget.classList.add("drop-target"); } });
$("#unpatched-drop").addEventListener("dragleave", event => event.currentTarget.classList.remove("drop-target"));
$("#unpatched-drop").addEventListener("drop", async event => {
  event.preventDefault();
  event.currentTarget.classList.remove("drop-target");
  if (dragPayload?.kind === "port") { const id = dragPayload.id; dragPayload = null; await unlinkPort(id); }
});

// Touch: lang indrukken pakt op, loslaten boven een poort zet neer.
let touchDrag = null, suppressClickUntil = 0;
document.addEventListener("pointerdown", event => {
  if (event.pointerType === "mouse") return;
  const chip = event.target.closest("[data-drag-entity]");
  const face = event.target.closest(".port-face.occupied");
  if (!chip && !face) return;
  const source = chip || face;
  touchDrag = {timer: setTimeout(() => {
    dragPayload = chip ? {kind: "entity", id: chip.dataset.dragEntity} : {kind: "port", id: face.dataset.portId};
    source.classList.add("dragging");
    toast("Sleep naar een poort en laat los");
  }, 350), source};
}, {passive: true});

document.addEventListener("pointermove", event => {
  if (!dragPayload || event.pointerType === "mouse") return;
  $$(".drop-target").forEach(item => item.classList.remove("drop-target"));
  const element = document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-drop-port]");
  if (element) element.classList.add("drop-target");
});

document.addEventListener("pointerup", async event => {
  if (touchDrag) { clearTimeout(touchDrag.timer); touchDrag.source.classList.remove("dragging"); touchDrag = null; }
  if (!dragPayload || event.pointerType === "mouse") return;
  const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-drop-port]");
  $$(".drop-target,.dragging").forEach(item => item.classList.remove("drop-target", "dragging"));
  const payload = dragPayload;
  dragPayload = null;
  // Na een touch-sleep vuurt de browser alsnog een click; die mag de
  // poortkiezer niet openen.
  suppressClickUntil = Date.now() + 600;
  if (!target) return;
  if (payload.kind === "entity") await linkEntityToPort(payload.id, target.dataset.dropPort);
  else if (payload.id !== target.dataset.dropPort) await moveCable(payload.id, target.dataset.dropPort);
});

// Zonder muis: klik een device in de zijlijst en kies een poort uit een lijst.
document.addEventListener("click", event => {
  const chip = event.target.closest("[data-drag-entity]");
  if (!chip || Date.now() < suppressClickUntil) return;
  const entity = state.data.entities.find(item => item.id === chip.dataset.dragEntity);
  const form = $("#quick-link-form");
  form.elements.entity_id.value = chip.dataset.dragEntity;
  $("#quick-link-title").textContent = `${entity?.name || "Device"} koppelen`;
  const options = state.data.physical_devices.flatMap(device => device.ports
    .filter(port => !port.cable_id)
    .map(port => `<option value="${esc(port.id)}">${esc(device.name)} · poort ${port.number}${port.side === "rear" ? " (achter)" : ""}</option>`));
  form.elements.port_id.innerHTML = options.join("") || `<option value="">Geen vrije poort</option>`;
  $("#quick-link-dialog").showModal();
});

$("#quick-link-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget, entityId = form.elements.entity_id.value, portId = form.elements.port_id.value;
  form.closest("dialog").close();
  if (portId) await linkEntityToPort(entityId, portId);
});

/* ------------------------------------------------- entity-drawer met historie */
function sparkline(values, {height = 26, suffix = ""} = {}) {
  const points = values.filter(value => value !== null && value !== undefined).map(Number);
  if (points.length < 2) return `<span class="muted tiny">nog te weinig metingen</span>`;
  const max = Math.max(...points, 1), min = Math.min(...points, 0), span = max - min || 1;
  const step = 100 / (points.length - 1);
  const path = points.map((value, index) => `${index === 0 ? "M" : "L"}${(index * step).toFixed(1)} ${(height - (value - min) / span * height).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 100 ${height}" preserveAspectRatio="none" role="img" aria-label="verloop"><path d="${path}"/></svg>
    <span class="spark-value">${points[points.length - 1].toFixed(1)}${suffix}</span>`;
}

function uptimeBar(days) {
  if (!days.length) return `<span class="muted tiny">nog geen dagen gemeten</span>`;
  return `<div class="uptime-bar">${days.map(day => {
    const ratio = day.samples_total ? day.samples_up / day.samples_total : 0;
    const level = ratio >= 0.999 ? "full" : ratio >= 0.95 ? "high" : ratio > 0 ? "low" : "none";
    return `<i class="uptime-day ${level}" title="${esc(day.day)}: ${(ratio * 100).toFixed(1)}% up${day.flips ? ` · ${day.flips} wissel(s)` : ""}"></i>`;
  }).join("")}</div>`;
}

async function openEntityDrawer(entityId) {
  const entity = state.data.entities.find(item => item.id === entityId);
  if (!entity) return;
  const port = findPortByEntity(entityId);
  $("#entity-drawer-title").textContent = entity.name;
  $("#entity-drawer-sub").innerHTML = `${statusDot(entity.status)} ${esc(entity.status)} · ${esc(entity.type)} · ${esc(entity.origin === "manual" ? "handmatig" : "discovery")}`;
  $("#entity-drawer-facts").innerHTML = [
    ["Adres", entity.ip_address || entity.hostname || "—"], ["MAC", entity.mac_address || "—"],
    ["Vendor", entity.vendor || "—"], ["Laatst gezien", formatTime(entity.last_seen_at)],
  ].map(([label, value]) => `<div><span class="data-cell-label">${label}</span><br>${esc(value)}</div>`).join("");
  $("#entity-drawer-cable").innerHTML = port
    ? `<span class="data-cell-label">Kabel</span><br>${esc(port.cable_label || "zonder label")} · <button class="button micro" data-open-port="${esc(port.id)}" data-open-device="${esc(port.physical_device_id)}">poort openen</button>`
    : `<span class="muted tiny">Niet aan een poort gekoppeld.</span>`;
  $("#entity-drawer-history").innerHTML = `<span class="muted tiny">historie laden…</span>`;
  $("#entity-drawer-backdrop").classList.remove("hidden");
  $("#entity-drawer").classList.add("open");
  $("#entity-drawer").setAttribute("aria-hidden", "false");
  try {
    const history = await api(`/api/entities/${encodeURIComponent(entityId)}/history`);
    const samples = history.samples;
    $("#entity-drawer-history").innerHTML = `
      <div class="history-block"><span class="data-cell-label">Uptime laatste 30 dagen</span>
        <div class="uptime-head"><b>${history.uptime_percent === null ? "—" : `${history.uptime_percent}%`}</b><span class="muted tiny">${history.flips} statuswissel(s)</span></div>
        ${uptimeBar(history.days)}</div>
      <div class="history-block"><span class="data-cell-label">Laatste 48 uur</span>
        <div class="spark-row"><span>cpu</span>${sparkline(samples.map(item => item.cpu_percent), {suffix:"%"})}</div>
        <div class="spark-row"><span>mem</span>${sparkline(samples.map(item => item.memory_percent), {suffix:"%"})}</div>
        <div class="spark-row"><span>ping</span>${sparkline(samples.map(item => item.latency_ms), {suffix:" ms"})}</div>
      </div>`;
  } catch (error) {
    $("#entity-drawer-history").innerHTML = `<span class="muted tiny">${esc(error.message)}</span>`;
  }
}

function closeEntityDrawer() {
  $("#entity-drawer-backdrop").classList.add("hidden");
  $("#entity-drawer").classList.remove("open");
  $("#entity-drawer").setAttribute("aria-hidden", "true");
}

$("#entity-drawer-backdrop").addEventListener("click", closeEntityDrawer);
$$("#entity-drawer .drawer-close").forEach(button => button.addEventListener("click", closeEntityDrawer));
document.addEventListener("click", event => {
  const open = event.target.closest("[data-entity-open]");
  if (open) openEntityDrawer(open.dataset.entityOpen);
  const port = event.target.closest("[data-open-port]");
  if (port) { closeEntityDrawer(); openPort(port.dataset.openPort, port.dataset.openDevice); }
});

/* --------------------------------------------------------- topologie pan/zoom */
// Bewust géén graph-library: alleen de viewBox verschuiven/schalen, zodat
// multiselect, drag→PATCH en de 50-staps undo ongewijzigd blijven werken.
const view = {x: 0, y: 0, scale: 1, base: null};
let panning = null;

function applyViewBox() {
  const svg = $("#topology-canvas");
  if (!view.base) return;
  const [width, height] = view.base;
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${width / view.scale} ${height / view.scale}`);
  $("#zoom-level").textContent = `${Math.round(view.scale * 100)}%`;
}

function resetView() {
  view.x = 0; view.y = 0; view.scale = 1;
  applyViewBox();
}

$("#topology-canvas").addEventListener("wheel", event => {
  if (!view.base) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
  const next = Math.min(4, Math.max(0.4, view.scale * factor));
  const rect = event.currentTarget.getBoundingClientRect();
  const [width, height] = view.base;
  // Zoom naar de cursor: het punt onder de muis blijft staan.
  const px = view.x + (event.clientX - rect.left) / rect.width * (width / view.scale);
  const py = view.y + (event.clientY - rect.top) / rect.height * (height / view.scale);
  view.x = px - (px - view.x) * (view.scale / next);
  view.y = py - (py - view.y) * (view.scale / next);
  view.scale = next;
  applyViewBox();
}, {passive: false});

$("#topology-canvas").addEventListener("pointerdown", event => {
  if (state.editingTopology || event.target.closest("[data-node-id]") || !view.base) return;
  panning = {x: event.clientX, y: event.clientY, startX: view.x, startY: view.y};
  $("#topology-canvas").classList.add("panning");
});

document.addEventListener("pointermove", event => {
  if (!panning || !view.base) return;
  const rect = $("#topology-canvas").getBoundingClientRect(), [width, height] = view.base;
  view.x = panning.startX - (event.clientX - panning.x) / rect.width * (width / view.scale);
  view.y = panning.startY - (event.clientY - panning.y) / rect.height * (height / view.scale);
  applyViewBox();
});

document.addEventListener("pointerup", () => {
  panning = null;
  $("#topology-canvas").classList.remove("panning");
});

$("#zoom-in").addEventListener("click", () => { view.scale = Math.min(4, view.scale * 1.25); applyViewBox(); });
$("#zoom-out").addEventListener("click", () => { view.scale = Math.max(0.4, view.scale / 1.25); applyViewBox(); });
$("#zoom-reset").addEventListener("click", resetView);
