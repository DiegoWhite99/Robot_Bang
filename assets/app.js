// Chat BANG — frontend. Pantalla PASIVA: el micrófono y el parlante viven en
// la placa (ver python/voice.py); esta página solo muestra los 5 guías,
// quién está activo, y lo que el robot escucha y responde. No hay botones
// de voz ni de texto: todo el turno lo maneja Python.

const chatLog = document.querySelector('#chat-log');
const debugLog = document.querySelector('#debug-log');
const personaCardsEl = document.querySelector('#persona-cards');
const statusBar = document.querySelector('#status-bar');
const hitCounter = document.querySelector('#hit-counter');

let personas = {};
let activePersona = null;

// El panel de diagnóstico no guarda historial largo: solo las últimas
// líneas, para ver en vivo qué está captando el micrófono.
const DEBUG_LOG_MAX_LINES = 40;

// Toque de nostalgia: un "contador de visitas" falso que sube en cada carga.
hitCounter.textContent = `VISITAS: ${String(Math.floor(Math.random() * 90000) + 10000).padStart(7, '0')}`;

const ui = new WebUI();
ui.on_connect(onUIConnected);
ui.on_disconnect(onUIDisconnected);
ui.on_message('personas', onPersonas);
ui.on_message('active_persona', onActivePersona);
ui.on_message('status', (data) => setStatus((data && data.text) || ''));
ui.on_message('heard', onHeard);
ui.on_message('reply', onReply);
ui.on_message('debug', onDebug);
ui.on_message('bang_state', onBangState);
ui.on_message('version', onVersion);

// Version y aviso de actualizaciones: los manda Python al conectarse
// (APP_VERSION en main.py), para que no haya dos numeros distintos entre el
// dashboard y /status. Lo del HTML es solo el respaldo si no llega.
function onVersion(data) {
  if (!data) return;
  if (data.version) {
    const label = `v${data.version}`;
    document.querySelectorAll('#version-chip, #footer-version').forEach((el) => {
      el.textContent = label;
    });
  }
  if (data.notice) {
    document.querySelector('#version-text').textContent = data.notice;
  }
}

function onUIConnected() {
  setStatus('conectado. cargando guías...');
}

function onUIDisconnected() {
  setStatus('⚠ se perdió la conexión con la placa. reconectando...');
}

function setStatus(text) {
  statusBar.textContent = text;
}

function onPersonas(data) {
  personas = {};
  personaCardsEl.innerHTML = '';

  (data.personas || []).forEach((p) => {
    personas[p.key] = p;

    const card = document.createElement('div');
    card.className = `persona-card${p.locked ? ' locked' : ''}`;
    card.style.background = p.color;
    card.dataset.key = p.key;
    card.innerHTML = `${p.name}<span class="tagline">${p.locked ? (p.gender === 'f' ? 'bloqueada' : 'bloqueado') : p.tagline}</span>`;
    personaCardsEl.appendChild(card);
  });

  updateActiveCard();
}

function onActivePersona(data) {
  activePersona = (data && data.key) || null;
  updateActiveCard();

  if (activePersona && personas[activePersona]) {
    document.documentElement.style.setProperty('--accent', personas[activePersona].color);
  }
}

function updateActiveCard() {
  document.querySelectorAll('.persona-card').forEach((card) => {
    card.classList.toggle('active', card.dataset.key === activePersona);
  });
}

function addLine(kind, text, who) {
  const el = document.createElement('div');
  el.className = `line ${kind}`;
  if (who) {
    const label = document.createElement('span');
    label.className = 'who';
    label.textContent = who;
    el.appendChild(label);
  }
  el.appendChild(document.createTextNode(text));
  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function onHeard(data) {
  if (!data || !data.text) return;
  const persona = personas[data.persona];
  addLine('user', data.text, persona ? `TÚ → ${persona.name.toUpperCase()}` : 'TÚ');
}

function onReply(data) {
  if (!data || !data.text) return;
  const persona = personas[data.persona];
  addLine('bot', data.text, persona ? persona.name.toUpperCase() : 'BOT');
}

function onDebug(data) {
  if (!data || !data.text) return;

  const el = document.createElement('div');
  el.className = 'line debug';
  const time = new Date().toLocaleTimeString('es-CO', { hour12: false });
  el.textContent = `[${time}] ${data.text}`;
  debugLog.appendChild(el);
  debugLog.scrollTop = debugLog.scrollHeight;

  while (debugLog.children.length > DEBUG_LOG_MAX_LINES) {
    debugLog.removeChild(debugLog.firstChild);
  }
}

// Estado del reto BANG del guia activo (ver python/bang.py): fase, pregunta
// problema, tarjetas de la ronda (boca abajo hasta que se piden por voz) e
// ideas juntadas en la fase gaseosa.
const bangReto = document.querySelector('#bang-reto');
const bangPregunta = document.querySelector('#bang-pregunta');
const bangCards = document.querySelector('#bang-cards');
const bangIdeas = document.querySelector('#bang-ideas');

function onBangState(data) {
  const phase = data && data.phase;
  document.querySelectorAll('.phase').forEach((el) => {
    el.classList.toggle('active', el.dataset.phase === phase);
  });

  if (!data || !data.reto) {
    bangReto.textContent = 'Todavía no hay reto. Di "Crispi, mi reto es...".';
    bangPregunta.hidden = bangCards.hidden = bangIdeas.hidden = true;
    return;
  }

  bangReto.textContent = `Reto: ${data.reto}`;
  bangPregunta.hidden = !data.pregunta;
  bangPregunta.textContent = data.pregunta ? `Pregunta problema: ${data.pregunta}` : '';

  const cards = data.cards || [];
  bangCards.hidden = cards.length === 0;
  bangCards.innerHTML = '';
  cards.forEach((card) => {
    const el = document.createElement('div');
    el.className = `card ${card.unlocked ? 'unlocked' : ''}`;
    if (card.unlocked) {
      const img = document.createElement('img');
      img.src = card.image;
      img.alt = card.text;
      el.appendChild(img);
    } else {
      el.textContent = '🂠';
    }
    bangCards.appendChild(el);
  });

  const ideas = data.ideas || [];
  bangIdeas.hidden = phase === 'solida' || ideas.length === 0;
  bangIdeas.textContent = `💡 Ideas: ${ideas.length} — ${ideas.join(' · ')}`;
}

// --- Terminal ------------------------------------------------------------
// Los comandos (/unlock_carmel, /status, /bt...) los resuelve Python
// (run_command() en main.py); el servidor contesta por 'terminal_response'.

const terminal = document.querySelector('#terminal');
const terminalOut = document.querySelector('#terminal-out');
const terminalForm = document.querySelector('#terminal-form');
const terminalInput = document.querySelector('#terminal-input');
const COMMANDS = [
  '/help', '/status', '/bt', '/add_bt', '/bt_list', '/bt_connect ', '/bt_disconnect ', '/bt_audio ', '/bt_mode headset', '/bt_mode music',
  '/test_audio', '/reset', '/clear', '/menu', '/bienvenida', '/cara ', '/tarjeta ',
  '/unlock_carmel', '/unlock_cesia', '/unlock_cori', '/unlock_cristal', '/unlock_all',
  '/lock_carmel', '/lock_cesia', '/lock_cori', '/lock_cristal', '/lock_all',
];
const history = [];
let historyPos = 0;
let terminalGreeted = false;

function termPrint(text, kind) {
  const el = document.createElement('div');
  if (kind) el.className = kind;
  el.textContent = text;
  terminalOut.appendChild(el);
  terminalOut.scrollTop = terminalOut.scrollHeight;
}

function openTerminal() {
  terminal.hidden = false;
  if (!terminalGreeted) {
    terminalGreeted = true;
    termPrint('CHAT BANG — terminal. Escribe /help para ver los comandos.', 'ok');
  }
  terminalInput.focus();
}

function closeTerminal() {
  terminal.hidden = true;
}

document.querySelector('#terminal-btn').addEventListener('click', openTerminal);
document.querySelector('#terminal-close').addEventListener('click', closeTerminal);
terminal.addEventListener('click', (e) => {
  if (e.target === terminal) closeTerminal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !terminal.hidden) closeTerminal();
  if (e.key === '`' && terminal.hidden) {
    e.preventDefault();
    openTerminal();
  }
});

terminalForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const line = terminalInput.value.trim();
  terminalInput.value = '';
  if (!line) return;
  history.push(line);
  historyPos = history.length;
  termPrint(`C:\\BANG> ${line}`, 'cmd');
  if (line === '/clear' || line === '/cls') {
    terminalOut.innerHTML = '';
    return;
  }
  if (line.startsWith('/add_bt')) termPrint('🔍 buscando equipos Bluetooth (10 s)...');
  if (line.startsWith('/bt_connect')) termPrint('conectando... (hasta 30 s)');
  ui.send_message('terminal', { line });
});

terminalInput.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowUp' && historyPos > 0) {
    e.preventDefault();
    terminalInput.value = history[--historyPos];
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    historyPos = Math.min(history.length, historyPos + 1);
    terminalInput.value = history[historyPos] || '';
  } else if (e.key === 'Tab') {
    e.preventDefault();
    const typed = terminalInput.value;
    const matches = COMMANDS.filter((c) => c.startsWith(typed));
    if (matches.length === 1) terminalInput.value = matches[0];
    else if (matches.length > 1) termPrint(matches.join('  '));
  }
});

ui.on_message('terminal_response', (data) => {
  const out = (data && data.output) || '';
  if (out) termPrint(out, /^(⚠|Comando desconocido|No conozco)/.test(out) ? 'err' : 'ok');
});
ui.on_message('error', (msg) => {
  if (!terminal.hidden) termPrint(`⚠ ${msg}`, 'err');
});

// --- Bluetooth -------------------------------------------------------------
// El contenedor no ve el Bluetooth: Python le pasa cada acción al ayudante
// del host (tools/bt_helper.py). La respuesta llega por 'bt_response'.

const btWin = document.querySelector('#bt');
const btStatus = document.querySelector('#bt-status');
const btList = document.querySelector('#bt-list');
const btScan = document.querySelector('#bt-scan');
const btRefresh = document.querySelector('#bt-refresh');
let btBusy = false;

function btSend(action, mac, label) {
  if (btBusy) return;
  btBusy = true;
  btScan.disabled = btRefresh.disabled = true;
  btList.querySelectorAll('button').forEach((b) => (b.disabled = true));
  btStatus.classList.remove('err');
  btStatus.textContent = label || 'consultando...';
  ui.send_message('bt', { action, mac });
}

function openBt() {
  btWin.hidden = false;
  btSend('status');
}

document.querySelector('#bt-btn').addEventListener('click', openBt);
document.querySelector('#bt-close').addEventListener('click', () => (btWin.hidden = true));
btWin.addEventListener('click', (e) => {
  if (e.target === btWin) btWin.hidden = true;
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') btWin.hidden = true;
});
btScan.addEventListener('click', () => btSend('scan', null, '🔍 buscando bocinas (10 s)...'));
btRefresh.addEventListener('click', () => btSend('status'));

ui.on_message('bt_response', (data) => {
  btBusy = false;
  btScan.disabled = btRefresh.disabled = false;
  data = data || {};

  if (!data.ok) {
    btStatus.classList.add('err');
    btStatus.textContent = `⚠ ${data.error || 'falló'}`;
  } else {
    const lines = [];
    if (data.message) lines.push(`✅ ${data.message}`);
    lines.push(`Bluetooth: ${data.powered ? 'encendido' : 'apagado'}`);
    lines.push(`La voz sale por: ${data.audio_output || '?'}`);
    btStatus.textContent = lines.join('\n');
  }
  if (data.devices) renderBtDevices(data.devices);
  else btList.querySelectorAll('button').forEach((b) => (b.disabled = false));
});

// --- WiFi --------------------------------------------------------------------
// Igual que el Bluetooth: el contenedor no puede hablar con NetworkManager, así
// que Python se lo pasa al ayudante del host (tools/wifi_helper.py). La
// respuesta llega por 'wifi_response'.
//
// Ojo: esto sirve para CAMBIAR de red. Si el robot se queda sin red, esta misma
// página deja de cargar — es el huevo y la gallina, y no tiene arreglo por aquí.

const wifiWin = document.querySelector('#wifi');
const wifiStatus = document.querySelector('#wifi-status');
const wifiList = document.querySelector('#wifi-list');
const wifiScan = document.querySelector('#wifi-scan');
const wifiRefresh = document.querySelector('#wifi-refresh');
let wifiBusy = false;

function wifiSend(action, extra, label) {
  if (wifiBusy) return;
  wifiBusy = true;
  wifiScan.disabled = wifiRefresh.disabled = true;
  wifiList.querySelectorAll('button').forEach((b) => (b.disabled = true));
  wifiStatus.classList.remove('err');
  wifiStatus.textContent = label || 'consultando...';
  ui.send_message('wifi', Object.assign({ action }, extra || {}));
}

document.querySelector('#wifi-btn').addEventListener('click', () => {
  wifiWin.hidden = false;
  wifiSend('status');
});
document.querySelector('#wifi-close').addEventListener('click', () => (wifiWin.hidden = true));
wifiWin.addEventListener('click', (e) => {
  if (e.target === wifiWin) wifiWin.hidden = true;
});
wifiScan.addEventListener('click', () => wifiSend('scan', null, '🔍 buscando redes...'));
wifiRefresh.addEventListener('click', () => wifiSend('status'));

ui.on_message('wifi_response', (data) => {
  wifiBusy = false;
  wifiScan.disabled = wifiRefresh.disabled = false;
  data = data || {};

  if (!data.ok) {
    wifiStatus.classList.add('err');
    wifiStatus.textContent = `⚠ ${data.error || 'falló'}`;
  } else {
    const lines = [];
    if (data.message) lines.push(`✅ ${data.message}`);
    lines.push(`Red actual: ${data.ssid || 'ninguna'}`);
    lines.push(`Internet: ${data.internet ? 'sí' : 'no'}`);
    if (data.dashboard_url) lines.push(`Esta página: ${data.dashboard_url}`);
    wifiStatus.textContent = lines.join('\n');
  }
  if (data.networks) renderWifiNetworks(data.networks);
  else wifiList.querySelectorAll('button').forEach((b) => (b.disabled = false));
});

function renderWifiNetworks(networks) {
  wifiList.innerHTML = '';
  if (networks.length === 0) {
    wifiList.innerHTML = '<div class="bt-empty">Ninguna red encontrada. Toca BUSCAR REDES.</div>';
    return;
  }
  networks.forEach((n) => {
    const row = document.createElement('div');
    row.className = `bt-device${n.active ? ' connected' : ''}`;

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = n.ssid;
    const meta = document.createElement('span');
    meta.className = 'meta';
    const barras = '▂▄▆█'.slice(0, Math.max(1, Math.ceil(n.signal / 25)));
    meta.textContent = `${barras} ${n.signal}%${n.secure ? ' · con clave' : ' · abierta'}${n.active ? ' · conectada' : ''}`;
    name.appendChild(meta);

    const btn = document.createElement('button');
    btn.className = 'btn95';
    btn.type = 'button';
    btn.textContent = n.active ? 'OLVIDAR' : 'CONECTAR';
    btn.addEventListener('click', () => {
      if (n.active) {
        wifiSend('forget', { ssid: n.ssid }, `olvidando ${n.ssid}...`);
        return;
      }
      // La clave se pide aquí y viaja solo hasta la placa; no se guarda en la web.
      const password = n.secure ? prompt(`Clave de "${n.ssid}":`) : '';
      if (n.secure && password === null) return;
      wifiSend('connect', { ssid: n.ssid, password }, `conectando a ${n.ssid}... (hasta 45 s)`);
    });
    row.append(name, btn);
    wifiList.appendChild(row);
  });
}

function renderBtDevices(devices) {
  btList.innerHTML = '';
  if (devices.length === 0) {
    btList.innerHTML = '<div class="bt-empty">Ningún equipo todavía. Toca BUSCAR BOCINAS.</div>';
    return;
  }
  devices.forEach((d) => {
    const row = document.createElement('div');
    row.className = `bt-device${d.connected ? ' connected' : ''}`;

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = d.name;
    const meta = document.createElement('span');
    meta.className = 'meta';
    meta.textContent = `${d.mac} · ${d.connected ? 'conectada' : d.paired ? 'emparejada' : 'nueva'}${d.audio ? ' · audio' : ''}`;
    name.appendChild(meta);

    const btn = document.createElement('button');
    btn.className = 'btn95';
    btn.type = 'button';
    if (d.connected) {
      btn.textContent = 'DESCONECTAR';
      btn.addEventListener('click', () => btSend('disconnect', d.mac, `desconectando ${d.name}...`));
    } else {
      btn.textContent = d.paired ? 'CONECTAR' : 'EMPAREJAR';
      btn.addEventListener('click', () => btSend('connect', d.mac, `conectando a ${d.name}... (hasta 30 s)`));
    }
    row.append(name, btn);
    btList.appendChild(row);
  });
}
