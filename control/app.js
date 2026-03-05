// ─────────────────────────────────────────────
// Stato locale
// ─────────────────────────────────────────────
const P = {
  thr:-18, ratio:4, knee:6, atk:5, rel:150, mu:0,
  txfreq:100, txgain:-17, gain:0, gain_l:0, gain_r:0,
  pilot:0.09, stereo:0.44, rds:0.03,
  preemph:0, deemph:0, mono_mode:0,
  mute_l:false, mute_r:false, phase_inv_r:false, phase_offset:0,
  test_mode:0, test_tone_hz:1000, test_tone_amp:0.5,
};
let compInputDb = -40, compGrDb = 0, compOutPeak = -60;
let startTime = Date.now();

// ─────────────────────────────────────────────
// Tab navigation
// ─────────────────────────────────────────────
function showTab(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  event.target.classList.add('active');
  if (id === 'history') refreshHistory();
}

// ─────────────────────────────────────────────
// API calls
// ─────────────────────────────────────────────
async function sendCmd(cmd) {
  try {
    await fetch('/api/cmd', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})});
  } catch(e) {}
}

async function sendRawCmd() {
  const cmd = document.getElementById('raw-cmd').value.trim();
  if (!cmd) return;
  try {
    const r = await fetch('/api/cmd', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})});
    const d = await r.json();
    document.getElementById('raw-resp').textContent = d.response || '(no risposta)';
  } catch(e) { document.getElementById('raw-resp').textContent = 'Errore: '+e; }
}

async function eepromSave() {
  const el = document.getElementById('eeprom-status');
  try {
    // Prima flusha i valori del form RDS al server, poi salva su EEPROM
    await saveRdsCfg();
    const r = await fetch('/api/eeprom/save', {method:'POST'});
    const d = await r.json();
    el.style.color = d.ok ? 'var(--green)' : 'var(--yellow)';
    el.textContent = d.ok
      ? '✓ Salvato [' + (d.backend||'') + '] — ' + new Date().toLocaleTimeString()
      : '⚠ Salvataggio parziale: ' + JSON.stringify(d.groups);
  } catch(e) { el.style.color='var(--red)'; el.textContent='Errore: '+e; }
}

async function eepromLoad() {
  const el = document.getElementById('eeprom-status');
  try {
    const r = await fetch('/api/eeprom/load', {method:'POST'});
    const d = await r.json();
    if (d.ok) { el.style.color='var(--blue)'; el.textContent='✓ Caricato da EEPROM'; }
    else { el.style.color='var(--yellow)'; el.textContent='⚠ '+d.error; }
  } catch(e) { el.style.color='var(--red)'; el.textContent='Errore: '+e; }
}

async function doSoftstart() {
  const target = parseInt(document.getElementById('sl-dac-target').value);
  try {
    await fetch('/api/softstart', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dac_target:target})});
    document.getElementById('softstart-lbl').textContent = 'Soft-start → DAC '+target;
  } catch(e) {}
}

async function sendPid() {
  const kp = parseFloat(document.getElementById('pid-kp').value);
  const ki = parseFloat(document.getElementById('pid-ki').value);
  const kd = parseFloat(document.getElementById('pid-kd').value);
  try {
    await fetch('/api/pid', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kp,ki,kd})});
  } catch(e) {}
}

function onPidTarget(val) {
  document.getElementById('lbl-pid-target').textContent = val.toFixed(1)+' W';
  try {
    fetch('/api/pid', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_w:val})});
  } catch(e) {}
}

// ─────────────────────────────────────────────
// Param handlers
// ─────────────────────────────────────────────
function setPowerMode(mode) {
  P._power_mode = mode;
  const btnM = document.getElementById('btn-pwr-manual');
  const btnA = document.getElementById('btn-pwr-auto');
  const row  = document.getElementById('pwr-setpoint-row');
  const sl   = document.getElementById('sl-pwr-setpoint');
  if (mode === 'manual') {
    btnM.classList.add('primary');    btnA.classList.remove('primary');
    if (row) row.style.opacity = '1';
    if (sl)  sl.disabled = false;
  } else {
    btnA.classList.add('primary');    btnM.classList.remove('primary');
    if (row) row.style.opacity = '0.45';
    if (sl)  sl.disabled = true;
  }
  sendCmd('PWR_MODE=' + mode.toUpperCase());
}

function onPwrSetpoint(val) {
  const nom = P.tx_nom ?? 50;
  document.getElementById('lbl-pwr-setpoint').textContent = val + ' W';
  // Converti W → attenuazione relativa rispetto alla potenza nominale
  // TX_GAIN_SET = TX_GAIN_NOM + 20*log10(val/nom)  [dB]
  const txGainNom = P.tx_gain ?? -17;
  const att = val > 0 ? txGainNom + 20 * Math.log10(val / nom) : -89;
  sendCmd('TX_GAIN=' + att.toFixed(1));
  P.pwr_setpoint = val;
}

function onTxNom(val) {
  document.getElementById('lbl-tx-nom').textContent = val + ' W';
  // Aggiorna il max dello slider setpoint
  const sl = document.getElementById('sl-pwr-setpoint');
  const mx = document.getElementById('pwr-setpoint-max');
  if (sl) { sl.max = val; if (+sl.value > val) sl.value = val; }
  if (mx) mx.textContent = val + ' W';
  P.tx_nom = val;
  sendCmd('TX_NOM=' + val);
}

function onVolMpx(comp, val) {
  const khz = (val * 75).toFixed(1);
  const map = {
    mono:   ['lbl-vol-mono',   'VOL_MONO=',   'sl-vol-mono',   'sl-mono'],
    stereo: ['lbl-vol-stereo', 'VOL_STEREO=', 'sl-vol-stereo', 'sl-stereo'],
    pilot:  ['lbl-vol-pilot',  'VOL_PILOT=',  'sl-vol-pilot',  'sl-pilot'],
    rds:    ['lbl-vol-rds',    'VOL_RDS=',    'sl-vol-rds',    'sl-rds'],
  };
  const [lbl, cmd, sl, slTx] = map[comp];
  const e = document.getElementById(lbl); if(e) e.textContent = khz + ' kHz';
  // Sincronizza slider TX se esiste
  const sTx = document.getElementById(slTx); if(sTx) { sTx.value = val; }
  sendCmd(cmd + val);
  P['vol_'+comp] = val;
}

function onTxParam(name, val) {
  P[name] = val;
  const map = {
    txfreq: ['lbl-txfreq', v=>v.toFixed(1)+' MHz', v=>'TX_FREQ='+v],
    txgain: ['lbl-txgain', v=>(v>=0?'+':'')+v.toFixed(0)+' dB', v=>'TX_GAIN='+v],
    gain:   ['lbl-gain',   v=>(v>=0?'+':'')+v.toFixed(1)+' dB', v=>'GAIN='+v],
    pilot:  ['lbl-pilot',  v=>(v*75).toFixed(1)+' kHz', v=>'VOL_PILOT='+v],
    stereo: ['lbl-stereo', v=>(v*75).toFixed(1)+' kHz', v=>'VOL_STEREO='+v],
    rds:    ['lbl-rds',    v=>(v*75).toFixed(1)+' kHz', v=>'VOL_RDS='+v],
    preemph:['lbl-preemph',v=>v<=0?'LINEAR':v+' µs', v=>'PREEMPH='+v],
    deemph: ['lbl-deemph', v=>v<=0?'LINEAR':v+' µs', v=>'DEEMPH='+v],
  };
  if (map[name]) {
    const [lblId, fmt, cmd] = map[name];
    document.getElementById(lblId).textContent = fmt(val);
    sendCmd(cmd(val));
  }
  if (name === 'txfreq') document.getElementById('top-freq').innerHTML = val.toFixed(1)+'<span>MHz</span>';
}

function onComp(name, val) {
  P[name] = val;
  const lbls = {
    thr:   ['lbl-thr',   v=>v.toFixed(1)+' dBFS',    'COMP_THR'],
    ratio: ['lbl-ratio', v=>v.toFixed(1)+' : 1',      'COMP_RATIO'],
    knee:  ['lbl-knee',  v=>v===0?'Hard':v.toFixed(1)+' dB', 'COMP_KNEE'],
    atk:   ['lbl-atk',  v=>v.toFixed(1)+' ms',        'COMP_ATK'],
    rel:   ['lbl-rel',  v=>v.toFixed(0)+' ms',         'COMP_REL'],
    mu:    ['lbl-mu',   v=>(v>=0?'+':'')+v.toFixed(1)+' dB', 'COMP_MU'],
  };
  if (lbls[name]) {
    const [id, fmt, cmd] = lbls[name];
    document.getElementById(id).textContent = fmt(val);
    sendCmd(`${cmd}=${val}`);
  }
  drawCurve();
}

// ─────────────────────────────────────────────
// Gain computer (replica C++)
// ─────────────────────────────────────────────
function gainComputer(in_db, thr, ratio, knee) {
  // Formula Giannoulis (JAES 2012) — soft knee standard
  // GR = 0                                       se over < -half  (sotto soglia)
  // GR = (1/R-1) * (over+half)² / (2*knee)       se -half <= over <= half (zona knee)
  // GR = over * (1/R - 1)                         se over > half  (sopra soglia)
  const half = knee * 0.5;
  const over = in_db - thr;
  if (over < -half) return 0;
  if (over <= half) {
    const x = over + half;
    return (1 / ratio - 1) * (x * x) / (2 * knee);
  }
  return over * (1 / ratio - 1);
}

// ─────────────────────────────────────────────
// Curva di trasferimento
// ─────────────────────────────────────────────
function toggleCompParams() {
  const body     = document.getElementById('comp-params-body');
  const arrow    = document.getElementById('comp-params-arrow');
  const metering = document.getElementById('comp-metering-panel');
  const open     = body.style.display === 'none';

  // Apri parametri → nascondi metering; chiudi → mostra metering
  body.style.display    = open ? 'block' : 'none';
  arrow.style.transform = open ? 'rotate(180deg)' : 'rotate(0deg)';
  metering.style.display = open ? 'none' : 'flex';

  setTimeout(drawCurve, 50);
}

// Storia punto operativo (ultimi N campioni per il trail)
const _opHistory = [];
const _OP_HISTORY_MAX = 24;

function drawCurve() {
  const canvas = document.getElementById('curve-canvas');
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  canvas.width = W * devicePixelRatio;
  canvas.height = H * devicePixelRatio;
  const ctx = canvas.getContext('2d');
  ctx.scale(devicePixelRatio, devicePixelRatio);
  const pad = 28, RANGE = 60;
  const xOf = db => pad + (db+RANGE)/(RANGE)*(W-pad*2);
  const yOf = db => (H-pad) - (db+RANGE)/(RANGE)*(H-pad*2);

  // Sfondo
  ctx.clearRect(0, 0, W, H);

  // Griglia — più visibile
  ctx.lineWidth = 1;
  for (let db=-60;db<=0;db+=12) {
    ctx.strokeStyle = (db===0||db===-60) ? 'rgba(0,200,240,.12)' : 'rgba(255,255,255,.06)';
    ctx.beginPath();ctx.moveTo(xOf(db),pad);ctx.lineTo(xOf(db),H-pad);ctx.stroke();
    ctx.beginPath();ctx.moveTo(pad,yOf(db));ctx.lineTo(W-pad,yOf(db));ctx.stroke();
  }

  // Assi
  ctx.strokeStyle='rgba(0,200,240,.25)';ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(pad,pad);ctx.lineTo(pad,H-pad);ctx.lineTo(W-pad,H-pad);ctx.stroke();

  // Labels — più leggibili
  ctx.fillStyle='#6a9898';ctx.font=`10px 'Share Tech Mono',monospace`;
  ctx.textAlign='center';ctx.textBaseline='top';
  [-60,-48,-36,-24,-12,0].forEach(db=>ctx.fillText(db,xOf(db),H-pad+3));
  ctx.textAlign='right';ctx.textBaseline='middle';
  [-60,-48,-36,-24,-12,0].forEach(db=>ctx.fillText(db,pad-3,yOf(db)));

  // Label assi
  ctx.fillStyle='#4a7070';ctx.font=`9px 'Barlow Condensed',sans-serif`;
  ctx.textAlign='center';
  ctx.fillText('INPUT dBFS', W/2, H-4);
  ctx.save();ctx.translate(8,H/2);ctx.rotate(-Math.PI/2);
  ctx.fillText('OUTPUT dBFS',0,0);ctx.restore();

  // 1:1 reference
  ctx.strokeStyle='rgba(255,255,255,.10)';ctx.setLineDash([4,6]);ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(xOf(-RANGE),yOf(-RANGE));ctx.lineTo(xOf(0),yOf(0));ctx.stroke();
  ctx.setLineDash([]);

  // Knee zone
  const half=P.knee*.5;
  ctx.fillStyle='rgba(245,196,0,.07)';
  ctx.fillRect(xOf(P.thr-half),pad,xOf(P.thr+half)-xOf(P.thr-half),H-pad*2);
  ctx.strokeStyle='rgba(245,196,0,.35)';ctx.setLineDash([3,4]);ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(xOf(P.thr),pad);ctx.lineTo(xOf(P.thr),H-pad);ctx.stroke();
  ctx.setLineDash([]);

  // Curva di trasferimento — gradiente colore
  const grad = ctx.createLinearGradient(xOf(-RANGE),0,xOf(0),0);
  grad.addColorStop(0,'rgba(0,200,240,.5)');
  grad.addColorStop(1,'rgba(0,229,170,.9)');
  ctx.strokeStyle=grad;ctx.lineWidth=2;
  ctx.shadowColor='rgba(0,200,240,.4)';ctx.shadowBlur=6;
  ctx.beginPath();let first=true;
  for (let in_db=-RANGE;in_db<=0;in_db+=0.5) {
    const out=in_db+gainComputer(in_db,P.thr,P.ratio,P.knee)+P.mu;
    const x=xOf(in_db),y=yOf(Math.max(-RANGE,out));
    first?ctx.moveTo(x,y):ctx.lineTo(x,y);first=false;
  }
  ctx.stroke();ctx.shadowBlur=0;

  // Punto operativo + trail
  const gr  = gainComputer(compInputDb,P.thr,P.ratio,P.knee);
  const out = compInputDb+gr+P.mu;
  const ox  = xOf(compInputDb), oy = yOf(Math.max(-RANGE,out));

  // Aggiorna storia
  _opHistory.push({x:ox,y:oy});
  if (_opHistory.length > _OP_HISTORY_MAX) _opHistory.shift();

  // Trail (sfuma)
  for (let i=0;i<_opHistory.length-1;i++) {
    const alpha = (i/_OP_HISTORY_MAX)*0.35;
    ctx.strokeStyle=`rgba(255,90,90,${alpha})`;
    ctx.lineWidth=2;
    ctx.beginPath();
    ctx.moveTo(_opHistory[i].x,_opHistory[i].y);
    ctx.lineTo(_opHistory[i+1].x,_opHistory[i+1].y);
    ctx.stroke();
  }

  // Crosshair
  ctx.strokeStyle='rgba(255,80,80,.20)';ctx.lineWidth=1;ctx.setLineDash([2,4]);
  ctx.beginPath();ctx.moveTo(ox,oy);ctx.lineTo(ox,H-pad);
  ctx.moveTo(ox,oy);ctx.lineTo(pad,oy);ctx.stroke();ctx.setLineDash([]);

  // Punto
  const active = compInputDb > P.thr - P.knee;  // sta comprimendo?
  ctx.fillStyle = active ? '#ff5a5a' : '#00e5aa';
  ctx.shadowColor = active ? 'rgba(255,80,80,.7)' : 'rgba(0,229,170,.5)';
  ctx.shadowBlur = active ? 12 : 6;
  ctx.beginPath();ctx.arc(ox,oy,4,0,Math.PI*2);ctx.fill();
  ctx.shadowBlur=0;

  // Info testo
  const infoEl = document.getElementById('curve-info');
  if (infoEl) {
    const grDisp = gr < -0.1 ? gr.toFixed(1) : '0.0';
    infoEl.textContent = `in ${compInputDb.toFixed(1)}  out ${Math.max(-RANGE,out).toFixed(1)}  GR ${grDisp} dB`;
    infoEl.style.color = active ? '#ff5a5a' : '#6a9898';
  }
}

// ─────────────────────────────────────────────
// History canvas
// ─────────────────────────────────────────────
function drawHistory(canvasId, data, color, min, max, unit='') {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !data || data.length < 2) return;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * devicePixelRatio;
  canvas.height = H * devicePixelRatio;
  const ctx = canvas.getContext('2d');
  ctx.scale(devicePixelRatio, devicePixelRatio);
  const pad = 4;
  const range = max - min || 1;
  const xOf = i => pad + i/(data.length-1)*(W-pad*2);
  const yOf = v => (H-pad) - (v-min)/range*(H-pad*2);

  // Griglia
  ctx.strokeStyle='rgba(255,255,255,.03)';ctx.lineWidth=1;
  for (let i=0;i<4;i++) {
    const y = pad + i/(3)*(H-pad*2);
    ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();
  }

  // Area fill
  ctx.fillStyle = color.replace(')',', 0.08)').replace('rgb','rgba');
  ctx.beginPath();ctx.moveTo(xOf(0),H-pad);
  data.forEach((v,i)=>ctx.lineTo(xOf(i),yOf(v)));
  ctx.lineTo(xOf(data.length-1),H-pad);ctx.closePath();ctx.fill();

  // Line
  ctx.strokeStyle=color;ctx.lineWidth=1.5;
  ctx.shadowColor=color;ctx.shadowBlur=4;
  ctx.beginPath();
  data.forEach((v,i)=>i===0?ctx.moveTo(xOf(0),yOf(v)):ctx.lineTo(xOf(i),yOf(v)));
  ctx.stroke();ctx.shadowBlur=0;

  // Min/max labels
  const cur = data[data.length-1];
  const dmax = Math.max(...data), dmin = Math.min(...data);
  ctx.fillStyle='rgba(184,204,204,.6)';ctx.font=`8px 'Share Tech Mono',monospace`;
  ctx.textAlign='right';ctx.textBaseline='top';
  ctx.fillText(dmax.toFixed(1)+unit, W-pad, pad);
  ctx.textBaseline='bottom';
  ctx.fillText(dmin.toFixed(1)+unit, W-pad, H-pad);
  ctx.textAlign='left';ctx.textBaseline='middle';
  ctx.fillStyle='rgba(0,229,170,.9)';
  ctx.fillText('▶ '+cur.toFixed(1)+unit, pad, H/2);
}

async function refreshHistory() {
  try {
    const r = await fetch('/api/history');
    const d = await r.json();
    drawHistory('hist-temp',  d.temp,    '#ff7d2a', 0,   80,  '°C');
    drawHistory('hist-fwd',   d.fwd,     '#00e5aa', 0,   15,  'W');
    drawHistory('hist-swr',   d.swr,     '#00c8f0', 1.0, 3.5, '');
    drawHistory('hist-gr',    d.comp_gr, '#b060ff', -20, 0,   'dB');
  } catch(e) {}
}

function downloadCSV() {
  window.open('/api/csv');
}

// ─────────────────────────────────────────────
// Poll status
// ─────────────────────────────────────────────
async function pollStatus() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) throw new Error();
    const s = await r.json();

    // Connessione OK
    document.getElementById('chip-conn').querySelector('.dot').style.background='var(--green)';
    document.getElementById('sys-lastpoll').textContent = new Date().toLocaleTimeString();
    if (s.storage_backend) {
      const el = document.getElementById('sys-storage');
      el.textContent = s.storage_backend;
      el.style.color = s.storage_backend === 'EEPROM' ? 'var(--green)' : 'var(--blue)';
    }
    if (s.serial_number) {
      document.getElementById('sys-sn').textContent = s.serial_number;
      document.getElementById('sys-sn').style.color = 'var(--green)';
    } else {
      document.getElementById('sys-sn').textContent = 'NON SERIALIZZATO';
      document.getElementById('sys-sn').style.color = 'var(--yellow)';
    }

    // Aggiorna uptime
    const up = Math.floor((Date.now()-startTime)/1000);
    document.getElementById('sys-uptime').textContent = `${Math.floor(up/3600)}h ${Math.floor(up%3600/60)}m ${up%60}s`;

    // ── Params ──
    const p = s.params || {};
    // ── Sync sorgente audio ──────────────────────────────────────
    if (s.audio_source) updateSourceUI(s.audio_source);
    if (s.audio_source_cfg) {
      const cfg = s.audio_source_cfg;
      const u = document.getElementById('cfg-url-webradio');
      const a = document.getElementById('cfg-dev-audioin');
      const m = document.getElementById('cfg-dev-mpx');
      if (u && cfg.webradio?.url)   u.value = cfg.webradio.url;
      if (a && cfg.audioin?.device) a.value = cfg.audioin.device;
      if (m && cfg.mpx?.device)     m.value = cfg.mpx.device;
    }
    syncSlider('sl-txfreq','lbl-txfreq', p.tx_freq, v=>v?.toFixed(1)+' MHz');
    syncSlider('sl-txgain','lbl-txgain', p.tx_gain, v=>(v>=0?'+':'')+v?.toFixed(0)+' dB');
    syncSlider('sl-gain',  'lbl-gain',   p.gain,    v=>(v>=0?'+':'')+v?.toFixed(1)+' dB');
    syncSlider('sl-pilot', 'lbl-pilot',  p.vol_pilot, v=>(v*75).toFixed(1)+' kHz');
    syncSlider('sl-stereo','lbl-stereo', p.vol_stereo, v=>(v*75).toFixed(1)+' kHz');
    syncSlider('sl-rds',   'lbl-rds',    p.vol_rds,  v=>(v*75).toFixed(1)+' kHz');
    // Configurazione Impianto
    if (p.tx_nom != null) {
      P.tx_nom = p.tx_nom;
      const sl=document.getElementById('sl-tx-nom'); if(sl) sl.value=p.tx_nom;
      const lb=document.getElementById('lbl-tx-nom'); if(lb) lb.textContent=p.tx_nom+' W';
      const slS=document.getElementById('sl-pwr-setpoint'); if(slS) slS.max=p.tx_nom;
      const mx=document.getElementById('pwr-setpoint-max'); if(mx) mx.textContent=p.tx_nom+' W';
    }
    syncSlider('sl-txgain',    'lbl-txgain',    p.tx_gain,   v=>(v>=0?'+':'')+v.toFixed(0)+' dB');
    syncSlider('sl-vol-mono',  'lbl-vol-mono',  p.vol_mono,  v=>(v*75).toFixed(1)+' kHz');
    syncSlider('sl-vol-stereo','lbl-vol-stereo',p.vol_stereo,v=>(v*75).toFixed(1)+' kHz');
    syncSlider('sl-vol-pilot', 'lbl-vol-pilot', p.vol_pilot, v=>(v*75).toFixed(1)+' kHz');
    syncSlider('sl-vol-rds',   'lbl-vol-rds',   p.vol_rds,   v=>(v*75).toFixed(1)+' kHz');
    // Gain L/R
    if (p.gain_l != null) { syncSlider('sl-gain-l','lbl-gain-l', p.gain_l, v=>(v>=0?'+':'')+v.toFixed(1)+' dB'); }
    if (p.gain_r != null) { syncSlider('sl-gain-r','lbl-gain-r', p.gain_r, v=>(v>=0?'+':'')+v.toFixed(1)+' dB'); }
    if (p.gains_linked != null && p.gains_linked !== _gainsLinked) {
      _gainsLinked = p.gains_linked;
      const btn  = document.getElementById('btn-link');
      const row  = document.getElementById('gain-r-row');
      const name = document.getElementById('lbl-gain-l-name');
      if (btn)  { btn.textContent = _gainsLinked ? '🔗' : '🔓'; btn.classList.toggle('primary',_gainsLinked); btn.classList.toggle('danger',!_gainsLinked); }
      if (row)  { row.style.display = _gainsLinked ? 'none' : ''; }
      if (name) { name.textContent = _gainsLinked ? 'Master' : 'Gain L'; }
    }
    if (p.mono_mode != null) _updateMonoModeUI(p.mono_mode);
    // Mute L/R
    if (p.mute_l != null) { P.mute_l = p.mute_l; const b=document.getElementById('btn-mute-l'); if(b) b.classList.toggle('danger',p.mute_l); }
    if (p.mute_r != null) { P.mute_r = p.mute_r; const b=document.getElementById('btn-mute-r'); if(b) b.classList.toggle('danger',p.mute_r); }
    // Fase R
    if (p.phase_inv_r != null) { P.phase_inv_r = p.phase_inv_r; const b=document.getElementById('btn-phase-inv'); if(b) b.classList.toggle('danger',p.phase_inv_r); }
    if (p.phase_offset != null) {
      P.phase_offset = p.phase_offset;
      const sl = document.getElementById('sl-phase-offset');
      const lb = document.getElementById('lbl-phase-offset');
      if (sl) sl.value = p.phase_offset;
      if (lb) lb.textContent = Math.round(p.phase_offset) + '°';
    }
    if (p.test_mode != null) _updateTestModeUI(p.test_mode);
    if (p.test_tone_hz  != null) { document.getElementById('sl-test-hz').value  = p.test_tone_hz;  document.getElementById('lbl-test-hz').textContent  = p.test_tone_hz + ' Hz'; }
    if (p.test_tone_amp != null) { const db=(20*Math.log10(p.test_tone_amp)).toFixed(0); document.getElementById('sl-test-amp').value = p.test_tone_amp; document.getElementById('lbl-test-amp').textContent = (db>=0?'+':'')+db+' dBFS'; }
    // Preemph buttons
    if (p.preemph != null) {
      document.getElementById('lbl-preemph').textContent = p.preemph <= 0 ? 'LINEAR' : p.preemph + ' µs';
      ['btn-pre-off','btn-pre-50','btn-pre-75'].forEach(id => { const el=document.getElementById(id); if(el){el.classList.remove('primary');} });
      const preMap = {0:'btn-pre-off', 50:'btn-pre-50', 75:'btn-pre-75'};
      const preBtn = preMap[p.preemph];
      if (preBtn) { const el=document.getElementById(preBtn); if(el) el.classList.add('primary'); }
    }
    if (p.deemph != null) { document.getElementById('lbl-deemph').textContent = p.deemph <= 0 ? 'LINEAR' : p.deemph + ' µs'; syncSlider('sl-deemph','lbl-deemph', p.deemph, v=>v<=0?'LINEAR':v+' µs'); }

    if (p.tx_freq != null) {
      P.txfreq = p.tx_freq;
      document.getElementById('top-freq').innerHTML = p.tx_freq.toFixed(1)+'<span>MHz</span>';
    }
    if (p.mute != null) {
      syncCheck('tog-mute',  p.mute);
      document.getElementById('chip-mute').style.display = p.mute ? 'flex' : 'none';
    }
    if (p.debug != null) syncCheck('tog-debug', p.debug);
    if (p.ta != null)    syncCheck('tog-ta',    p.ta);
    if (p.tp != null)    syncCheck('tog-tp',    p.tp);
    if (p.ms != null)    syncCheck('tog-ms',    p.ms);
    syncText('rds-ps',  p.ps);
    syncText('rds-rt',  p.rt);
    syncText('rds-pi',  p.pi);
    syncText('rds-pty', p.pty != null ? String(p.pty) : null);
    /* AF1/AF2: il modulatore le restituisce in MHz×10; mostrare in MHz con 1 decimale */
    if (p.af1 != null) syncText('rds-af1', p.af1 > 0 ? (p.af1 / 10).toFixed(1) : '0');
    if (p.af2 != null) syncText('rds-af2', p.af2 > 0 ? (p.af2 / 10).toFixed(1) : '0');

    // Comp params
    if (p.comp_thr   != null) { P.thr=p.comp_thr;   document.getElementById('sl-thr').value=p.comp_thr;   document.getElementById('lbl-thr').textContent=p.comp_thr.toFixed(1)+' dBFS'; }
    if (p.comp_ratio != null) { P.ratio=p.comp_ratio;document.getElementById('sl-ratio').value=p.comp_ratio; document.getElementById('lbl-ratio').textContent=p.comp_ratio.toFixed(1)+' : 1'; }
    if (p.comp_en != null) syncCheck('tog-comp', p.comp_en);

    // ── Metering ──
    const m = s.metering || {};
    compInputDb  = m.comp_input_db  ?? -40;
    compGrDb     = m.comp_gr_db     ?? 0;
    compOutPeak  = m.comp_output_peak ?? -60;

    const grAbs = Math.abs(compGrDb);
    const grEl = document.getElementById('gr-big');
    grEl.textContent = grAbs < 0.1 ? '0.0' : grAbs.toFixed(1);
    grEl.className = 'big-val ' + (grAbs<0.5?'green':grAbs<6?'yellow':'red');

    const pctIn  = Math.max(0, Math.min(100, (compInputDb+60)/60*100));
    const pctGR  = Math.min(100, grAbs/20*100);
    const pctOut = Math.max(0, Math.min(100, (compOutPeak+60)/60*100));
    document.getElementById('vbar-in').style.height = pctIn+'%';
    document.getElementById('vbar-gr').style.height = pctGR+'%';
    document.getElementById('vbar-out').style.height = pctOut+'%';
    document.getElementById('vbar-in-val').textContent  = compInputDb>-60?compInputDb.toFixed(1)+' dB':'−∞';
    document.getElementById('vbar-gr-val').textContent  = grAbs<0.1?'—':'−'+grAbs.toFixed(1)+' dB';
    document.getElementById('vbar-out-val').textContent = compOutPeak>-60?compOutPeak.toFixed(1)+' dB':'−∞';

    // Output FM — peak totale in kHz
    const mpxPeak = m.mpx_peak ?? 0;
    const peakKhz = mpxPeak * 75;
    const _pc='big-val '+(mpxPeak<0.8?'green':mpxPeak<0.95?'yellow':'red');
    document.getElementById('mpx-peak-val').textContent=peakKhz.toFixed(1);
    document.getElementById('mpx-peak-val').className=_pc;
    document.getElementById('mpx-peak-num').textContent=peakKhz.toFixed(1)+' kHz';
    document.getElementById('bar-mpx-peak').style.width=Math.min(100,mpxPeak*100)+'%';
    {const e=document.getElementById('mpx-peak-val-t');if(e){e.textContent=peakKhz.toFixed(1);e.className=_pc;}}
    {const e=document.getElementById('mpx-peak-num-t');if(e)e.textContent=peakKhz.toFixed(1)+' kHz';}
    {const e=document.getElementById('bar-mpx-peak-t');if(e)e.style.width=Math.min(100,mpxPeak*100)+'%';}
    // Componenti: mono e stereo dinamici dal modulatore, pilot e RDS statici dai vol_*
    const vMono   = p.vol_mono   ?? 0;
    const vPilot  = p.vol_pilot  ?? 0;
    const vStereo = p.vol_stereo ?? 0;
    const vRds    = p.vol_rds    ?? 0;
    const monoPeak   = m.mono_peak   ?? 0;
    const stereoPeak = m.stereo_peak ?? 0;
    // Mono e stereo: peak audio × vol, espresso in kHz di deviazione
    const monoKhz   = monoPeak   * vMono   * 75;
    const stereoKhz = stereoPeak * vStereo * 75;
    const fmt = v => v.toFixed(1)+' kHz';
    document.getElementById('out-mono-num').textContent   = fmt(monoKhz);
    document.getElementById('out-pilot-num').textContent  = fmt(vPilot  * 75);
    document.getElementById('out-stereo-num').textContent = fmt(stereoKhz);
    document.getElementById('out-rds-num').textContent    = fmt(vRds    * 75);
    document.getElementById('bar-out-mono').style.width   = Math.min(100, monoPeak   * vMono   * 100)+'%';
    document.getElementById('bar-out-pilot').style.width  = Math.min(100, vPilot  * 100)+'%';
    document.getElementById('bar-out-stereo').style.width = Math.min(100, stereoPeak * vStereo * 100)+'%';
    document.getElementById('bar-out-rds').style.width    = Math.min(100, vRds    * 100)+'%';
    {const e=document.getElementById('out-mono-num-t');if(e)e.textContent=fmt(monoKhz);}
    {const e=document.getElementById('out-pilot-num-t');if(e)e.textContent=fmt(vPilot*75);}
    {const e=document.getElementById('out-stereo-num-t');if(e)e.textContent=fmt(stereoKhz);}
    {const e=document.getElementById('out-rds-num-t');if(e)e.textContent=fmt(vRds*75);}
    {const e=document.getElementById('bar-out-mono-t');if(e)e.style.width=Math.min(100,monoPeak*vMono*100)+'%';}
    {const e=document.getElementById('bar-out-pilot-t');if(e)e.style.width=Math.min(100,vPilot*100)+'%';}
    {const e=document.getElementById('bar-out-stereo-t');if(e)e.style.width=Math.min(100,stereoPeak*vStereo*100)+'%';}
    {const e=document.getElementById('bar-out-rds-t');if(e)e.style.width=Math.min(100,vRds*100)+'%';}

    // ── Sensori ──
    const sens = s.sensors || {};
    const temp = sens.temp_c ?? 0;
    const fwd  = sens.fwd_w  ?? 0;
    const ref  = sens.ref_w  ?? 0;
    const swr  = sens.swr    ?? 1;
    const dac  = sens.dac_value ?? 0;
    const pidO = sens.pid_output ?? 0;

    // Temp
    document.getElementById('temp-big').textContent = temp.toFixed(1);
    document.getElementById('temp-big').className = 'big-val '+(temp<45?'green':temp<60?'yellow':'red');
    document.getElementById('bar-temp').style.width = Math.min(100, temp/80*100)+'%';

    // FWD
    document.getElementById('fwd-big').textContent = fwd.toFixed(2);
    document.getElementById('fwd-big').className = 'big-val '+(fwd>0.5?'green':'yellow');
    {const e=document.getElementById('fwd-big-tx');if(e){e.textContent=fwd.toFixed(2);e.className='big-val '+(fwd>0.5?'green':'yellow');}}
    {const e=document.getElementById('ref-big-tx');if(e){const rv=sens.ref_w??0;e.textContent=rv.toFixed(2);e.className='big-val '+(rv>fwd*0.1?'red':rv>fwd*0.05?'yellow':'');e.style.color=rv>fwd*0.1?'':'var(--text-mid)';}}

    document.getElementById('bar-fwd').style.width = Math.min(100, fwd/15*100)+'%';

    // SWR
    document.getElementById('swr-big').textContent = swr.toFixed(2);
    document.getElementById('swr-big').className = 'big-val '+(swr<1.5?'green':swr<2.5?'yellow':'red');
    document.getElementById('bar-swr').style.width = Math.min(100, (swr-1)/2*100)+'%';
    document.getElementById('ref-w-val').textContent = ref.toFixed(2)+' W';

    // DAC
    document.getElementById('dac-val').textContent = dac;
    document.getElementById('bar-dac').style.width = (dac/4095*100)+'%';
    document.getElementById('pid-out-val').textContent = pidO.toFixed(1);

    // Soft-start
    const ss = s.softstart;
    document.getElementById('softstart-lbl').textContent = ss ? 'Soft-start in corso...' : '';
    if (ss) {
      document.getElementById('softstart-fill').style.width = (dac/4095*100)+'%';
    }

    // ── Allarmi ──
    const alm = s.alarms || {};
    setAlarm('alm-temp',    alm.temp_high);
    setAlarm('alm-swr',     alm.swr_high);
    setAlarm('alm-fwdlow',  alm.fwd_low);
    setAlarm('alm-fwdhigh', alm.fwd_high);
    const anyAlarm = Object.values(alm).some(Boolean);
    document.getElementById('chip-alarm').style.display = anyAlarm?'flex':'none';

  } catch(e) {
    document.getElementById('chip-conn').querySelector('.dot').style.background='var(--red)';
  }
  setTimeout(pollStatus, 150);
}

function setAlarm(id, active) {
  const el = document.getElementById(id);
  if (active) el.classList.add('active');
  else el.classList.remove('active');
}

function syncSlider(slId, lblId, val, fmt) {
  if (val == null) return;
  const sl = document.getElementById(slId);
  if (sl && document.activeElement !== sl && Math.abs(+sl.value - val) > 0.001)
    sl.value = val;
  const lb = document.getElementById(lblId);
  if (lb) lb.textContent = fmt(val);
}

function syncText(id, val) {
  if (val == null) return;
  const el = document.getElementById(id);
  if (!el || document.activeElement === el) return;
  if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
    el.value = val;
  } else {
    el.textContent = val;
  }
}

// Mappa dei toggle con update pendente: id → timestamp dell'ultimo click
const _pendingToggle = {};

function toBool(val) {
  // Gestisce sia booleani che stringhe "1"/"0"/"true"/"false" dal server
  if (typeof val === 'boolean') return val;
  if (typeof val === 'number')  return val !== 0;
  if (typeof val === 'string')  return val === '1' || val === 'true' || val === 'on';
  return false;
}

function syncCheck(id, val) {
  if (val == null) return;
  const el = document.getElementById(id);
  if (!el) return;
  const bval = toBool(val);
  if (_pendingToggle[id] && Date.now() - _pendingToggle[id] < 2000) {
    if (el.checked === bval) delete _pendingToggle[id];
    return;
  }
  el.checked = bval;
}

function toggleAndSend(id, cmd) {
  const el = document.getElementById(id);
  if (!el) return;
  // Marca come pendente prima di inviare
  _pendingToggle[id] = Date.now();
  sendCmd(cmd + (el.checked ? '1' : '0'));
}

// ─────────────────────────────────────────────
// RDS avanzato
// ─────────────────────────────────────────────
function updatePsPreview(val) {
  const s = val.padEnd(16, ' ');
  document.getElementById('ps-prev1').textContent = s.slice(0,8);
  document.getElementById('ps-prev2').textContent = s.slice(8,16) || '        ';
}

function onRtModeChange() {
  const mode = document.querySelector('input[name="rt-mode"]:checked').value;
  const isSong = mode === 'song';
  document.getElementById('rds-icecast-row').style.opacity          = isSong ? '1' : '0.4';
  document.getElementById('rds-icecast-row').style.pointerEvents    = isSong ? '' : 'none';
  document.getElementById('rds-icecast-interval-row').style.opacity = isSong ? '1' : '0.4';
  document.getElementById('rds-icecast-interval-row').style.pointerEvents = isSong ? '' : 'none';
  document.getElementById('rds-rt-fixed-row').style.opacity         = isSong ? '0.4' : '1';
  document.getElementById('rds-rt-fixed-row').style.pointerEvents   = isSong ? 'none' : '';
  saveRdsCfg();
}

async function saveRdsCfg() {
  const mode = document.querySelector('input[name="rt-mode"]:checked')?.value || 'fixed';
  const body = {
    ps_long:              document.getElementById('rds-ps-long').value,
    ps_cycle_sec:         parseFloat(document.getElementById('rds-ps-cycle').value) || 5,
    rt_mode:              mode,
    rt_fixed:             document.getElementById('rds-rt-fixed').value,
    rt_alt:               document.getElementById('rds-rt-alt').value,
    rt_alt_sec:           parseFloat(document.getElementById('rds-rt-alt-sec').value) || 15,
    icecast_url:          document.getElementById('rds-icecast-url').value,
    icecast_interval_sec: parseFloat(document.getElementById('rds-icecast-interval').value) || 15,
  };
  try {
    await fetch('/api/rds/config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
  } catch(e) {}
}

async function pollRdsStatus() {
  try {
    const r = await fetch('/api/rds/status');
    if (!r.ok) return;
    const d = await r.json();
    const cfg = d.cfg || {};
    const rs  = d.state || {};

    // Aggiorna campi solo se non in focus
    const syncCfgText = (id, val) => {
      const el = document.getElementById(id);
      if (el && document.activeElement !== el) el.value = val ?? el.value;
    };

    syncCfgText('rds-ps-long',          cfg.ps_long);
    syncCfgText('rds-ps-cycle',         cfg.ps_cycle_sec);
    syncCfgText('rds-rt-fixed',         cfg.rt_fixed);
    syncCfgText('rds-rt-alt',           cfg.rt_alt);
    syncCfgText('rds-rt-alt-sec',       cfg.rt_alt_sec);
    syncCfgText('rds-icecast-url',      cfg.icecast_url);
    syncCfgText('rds-icecast-interval', cfg.icecast_interval_sec);

    if (cfg.rt_mode) {
      const radio = document.getElementById('rt-mode-' + cfg.rt_mode);
      if (radio && !radio.checked) { radio.checked = true; onRtModeChange(); }
    }
    if (cfg.ps_long) updatePsPreview(cfg.ps_long);

    // Etichetta RT principale: varia con la modalità
    const lbl = document.getElementById('rds-rt-fixed-lbl');
    if (lbl) lbl.textContent = cfg.rt_mode === 'song'
      ? 'RT alternativo al titolo (64 chr — es. slogan/URL)'
      : 'RT principale (64 chr — inviato completo)';

    // Titolo corrente (Icecast)
    const titleEl = document.getElementById('rds-current-title');
    if (titleEl) titleEl.textContent = rs.current_title || '—';

    // RT in onda ora
    const rtEl = document.getElementById('rds-rt');
    if (rtEl) rtEl.textContent = rs.current_rt || '—';

    // Slot attivo RT (Principale / Alternativo)
    const slotEl = document.getElementById('rds-rt-slot');
    if (slotEl) {
      const hasAlt = cfg.rt_alt && cfg.rt_alt.trim();
      slotEl.textContent = !hasAlt ? '—' : (rs.rt_slot === 1 ? 'ALT' : 'MAIN');
      slotEl.style.color = rs.rt_slot === 1 ? 'var(--orange)' : 'var(--blue)';
    }

    // Indicatore on-air nella testata pannello
    const oaEl = document.getElementById('rds-on-air-lbl');
    if (oaEl) {
      const rtSlot = rs.rt_slot === 1 ? 'ALT' : 'MAIN';
      const psH    = rs.ps_half  === 1 ? '2/2' : '1/2';
      oaEl.textContent = `RT:${rtSlot} · PS:${psH}`;
    }

  } catch(e) {}
  setTimeout(pollRdsStatus, 2000);
}

// ─────────────────────────────────────────────
// Animation loop
// ─────────────────────────────────────────────
let lastCurveDraw = 0;
function frame(ts) {
  if (ts - lastCurveDraw > 100) {
    if (document.getElementById('tab-comp').classList.contains('active')) drawCurve();
    lastCurveDraw = ts;
  }
  requestAnimationFrame(frame);
}

// ─────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────
drawCurve();
requestAnimationFrame(frame);
pollStatus();
pollRdsStatus();
onRtModeChange();
updatePsPreview(document.getElementById('rds-ps-long').value);

// ─────────────────────────────────────────────
// Catena Webradio
// ─────────────────────────────────────────────

const CHAIN_STATUS_COLOR = {
  stopped:  'var(--text-dim)',
  starting: 'var(--yellow)',
  running:  'var(--green)',
  error:    'var(--red)',
  stopping: 'var(--yellow)',
};
const CHAIN_STATUS_LABEL = {
  stopped:  '● STOP',
  starting: '◌ AVVIO…',
  running:  '● IN ARIA',
  error:    '✖ ERRORE',
  stopping: '◌ STOP…',
};

let _chainLogLines = [];

function chainUpdateSourceUI(src) {
  src = src || (document.getElementById('chain-source') || {}).value || 'webradio';
  const show = id => { const el = document.getElementById(id); if (el) el.style.display = 'grid'; };
  const hide = id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; };
  hide('chain-row-url'); hide('chain-row-dev1'); hide('chain-row-dev2');
  hide('chain-row-rate'); hide('chain-row-tone');
  if (src === 'webradio') {
    show('chain-row-url');
  } else if (src === 'alsa1') {
    show('chain-row-dev1');
    const rl = document.getElementById('chain-rate-lbl');
    if (rl) rl.textContent = 'Sample rate';
    show('chain-row-rate');
  } else if (src === 'alsa2') {
    const dl = document.getElementById('chain-dev2-lbl');
    if (dl) dl.textContent = 'Dispositivo 2';
    show('chain-row-dev2');
    const rl = document.getElementById('chain-rate-lbl');
    if (rl) rl.textContent = 'Sample rate';
    show('chain-row-rate');
  } else if (src === 'tone') {
    const te = document.getElementById('chain-row-tone');
    if (te) te.style.display = 'flex';
  } else if (src === 'mpx_in') {
    const dl = document.getElementById('chain-dev2-lbl');
    if (dl) dl.textContent = 'Dispositivo MPX';
    show('chain-row-dev2');
    const rl = document.getElementById('chain-rate-lbl');
    if (rl) rl.textContent = 'Sample rate MPX';
    show('chain-row-rate');
  }
}
function chainSourceChanged() {
  chainUpdateSourceUI();
  chainSaveCfg();
}

function pollChainStatus() {
  fetch('/api/chain/status')
    .then(r => r.json())
    .then(d => {
      const st = d.status || 'stopped';
      const badge = document.getElementById('chain-status-badge');
      if (badge) {
        badge.textContent = CHAIN_STATUS_LABEL[st] || st;
        badge.style.color = CHAIN_STATUS_COLOR[st] || 'var(--text-mid)';
        badge.style.background = st === 'running' ? 'rgba(0,200,80,0.12)' :
                                 st === 'error'   ? 'rgba(255,60,60,0.12)' : 'var(--border)';
      }
      const up = document.getElementById('chain-uptime');
      if (up) {
        if (st === 'running' && d.uptime_sec != null) {
          const h = Math.floor(d.uptime_sec/3600);
          const m = Math.floor((d.uptime_sec%3600)/60);
          const s = d.uptime_sec%60;
          up.textContent = `uptime ${h}h${String(m).padStart(2,'0')}m${String(s).padStart(2,'0')}s`;
        } else {
          up.textContent = d.error ? `⚠ ${d.error}` : '';
        }
      }
      if (d.cfg) {
        const active = el => document.activeElement !== el;
        const sv = (id, v) => { const el = document.getElementById(id); if (el && active(el) && v != null) el.value = v; };
        const sc = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
        // selettore sorgente
        const srcEl = document.getElementById('chain-source');
        if (srcEl && active(srcEl) && d.cfg.audio_source) {
          srcEl.value = d.cfg.audio_source;
          chainUpdateSourceUI(d.cfg.audio_source);
        }
        sv('chain-url',       d.cfg.stream_url);
        sv('chain-dev1',      d.cfg.alsa_dev1);
        sv('chain-dev2',      d.cfg.alsa_dev2);
        sv('chain-tone-freq', d.cfg.tone_freq);
        { const amp = parseFloat(d.cfg.tone_amplitude ?? 0.5);
          const el = document.getElementById('chain-tone-amp');
          if (el) { el.value = amp;
            const lbl = document.getElementById('chain-tone-amp-val');
            if (lbl) lbl.textContent = (20*Math.log10(Math.max(amp,1e-6))).toFixed(1)+' dBFS'; } }
        sv('chain-flowgraph', d.cfg.flowgraph);
        sc('tog-chain-restart', d.cfg.auto_restart);
        // rate dipende dalla sorgente
        const src = d.cfg.audio_source;
        const rEl = document.getElementById('chain-rate');
        if (rEl && active(rEl)) {
          if (src === 'mpx_in') rEl.value = d.cfg.mpx_rate ?? 192000;
          else rEl.value = d.cfg.alsa_rate ?? 48000;
        }
      }
      if (d.log && d.log.length) {
        const logEl = document.getElementById('chain-log');
        if (logEl) {
          const newLines = d.log.filter(l => !_chainLogLines.includes(l));
          if (newLines.length) {
            _chainLogLines = d.log;
            logEl.textContent = d.log.join('\n');
            logEl.scrollTop = logEl.scrollHeight;
          }
        }
      }
    })
    .catch(() => {});
  setTimeout(pollChainStatus, 2000);
}

function chainCmd(cmd) {
  fetch(`/api/chain/${cmd}`, { method: 'POST' })
    .then(r => r.json())
    .then(d => { if (!d.ok) alert('Errore: ' + (d.error || '?')); })
    .catch(e => alert('Errore connessione: ' + e));
}

function chainSaveCfg() {
  const src     = (document.getElementById('chain-source')     || {}).value   || 'webradio';
  const flowEl  = document.getElementById('chain-flowgraph');
  const arEl    = document.getElementById('tog-chain-restart');
  const rateEl  = document.getElementById('chain-rate');
  const rate    = rateEl ? parseInt(rateEl.value) || 48000 : 48000;
  const body = {
    audio_source: src,
    stream_url:   (document.getElementById('chain-url')       || {}).value || undefined,
    alsa_dev1:    (document.getElementById('chain-dev1')      || {}).value || undefined,
    alsa_dev2:    (document.getElementById('chain-dev2')      || {}).value || undefined,
    tone_freq:      parseInt((document.getElementById('chain-tone-freq') || {}).value) || 1000,
    tone_amplitude: parseFloat((document.getElementById('chain-tone-amp') || {}).value ?? 0.5),
    flowgraph:    flowEl ? flowEl.value.trim() : undefined,
    auto_restart: arEl   ? arEl.checked        : false,
  };
  if (src === 'mpx_in') body.mpx_rate   = rate;
  else                   body.alsa_rate  = rate;
  fetch('/api/chain/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}


pollChainStatus();
// ── Mute per canale e fase ────────────────────────────────────────────────────
function toggleMuteCh(ch) {
  const key = ch === 'L' ? 'mute_l' : 'mute_r';
  const btnId = ch === 'L' ? 'btn-mute-l' : 'btn-mute-r';
  P[key] = !P[key];
  sendCmd((ch === 'L' ? 'MUTE_L=' : 'MUTE_R=') + (P[key] ? '1' : '0'));
  const btn = document.getElementById(btnId);
  if (btn) { btn.classList.toggle('danger', P[key]); btn.classList.toggle('primary', false); }
}

function togglePhaseInv() {
  P.phase_inv_r = !P.phase_inv_r;
  sendCmd('PHASE_INV_R=' + (P.phase_inv_r ? '1' : '0'));
  const btn = document.getElementById('btn-phase-inv');
  if (btn) btn.classList.toggle('danger', P.phase_inv_r);
}

function onPhaseOffset(deg) {
  P.phase_offset = deg;
  document.getElementById('lbl-phase-offset').textContent = deg + '°';
  sendCmd('PHASE_OFFSET=' + deg);
}


const _testModeLabels = {
  0: 'Funzionamento normale',
  1: 'Tono sinusoidale continuo su L e R',
  2: 'CW — portante non modulata (MPX = 0)'
};
function setTestMode(m) {
  const wasInTest = P.test_mode > 0;
  const goingIntoTest = m > 0;
  if (goingIntoTest && !wasInTest) {
    P._comp_before_test = document.getElementById('tog-comp')?.checked ?? true;
    sendCmd('COMP_EN=0');
    const tog = document.getElementById('tog-comp'); if (tog) tog.checked = false;
    P._gain_l_before_test = P.gain_l ?? 0;
    P._gain_r_before_test = P.gain_r ?? 0;
    sendCmd('GAIN_L=0'); sendCmd('GAIN_R=0'); sendCmd('GAIN=0');
    const slL=document.getElementById('sl-gain-l'); if(slL) slL.value=0;
    const slR=document.getElementById('sl-gain-r'); if(slR) slR.value=0;
    const lbL=document.getElementById('lbl-gain-l'); if(lbL) lbL.textContent='0.0 dB';
    const lbR=document.getElementById('lbl-gain-r'); if(lbR) lbR.textContent='0.0 dB';
    P.gain_l=0; P.gain_r=0;
  } else if (!goingIntoTest && wasInTest) {
    const rc = P._comp_before_test ?? true;
    sendCmd('COMP_EN='+(rc?'1':'0'));
    const tog=document.getElementById('tog-comp'); if(tog) tog.checked=rc;
    const gl=P._gain_l_before_test??0, gr=P._gain_r_before_test??0;
    sendCmd('GAIN_L='+gl); sendCmd('GAIN_R='+gr); sendCmd('GAIN='+gl);
    const slL=document.getElementById('sl-gain-l'); if(slL) slL.value=gl;
    const slR=document.getElementById('sl-gain-r'); if(slR) slR.value=gr;
    const fd=v=>(v>=0?'+':'')+v.toFixed(1)+' dB';
    const lbL=document.getElementById('lbl-gain-l'); if(lbL) lbL.textContent=fd(gl);
    const lbR=document.getElementById('lbl-gain-r'); if(lbR) lbR.textContent=fd(gr);
    P.gain_l=gl; P.gain_r=gr;
  }
  sendCmd('TEST_MODE=' + m);
  P.test_mode = m;
  _updateTestModeUI(m);
}
function _updateTestModeUI(m) {
  ['btn-test-off','btn-test-tone','btn-test-cw'].forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('primary', i === m);
    el.classList.toggle('danger',  m > 0 && i === m);
    if (m > 0 && i === m) el.classList.remove('primary');
  });
  const lbl = document.getElementById('lbl-test-mode');
  if (lbl) lbl.textContent = _testModeLabels[m] ?? '';
  const params = document.getElementById('test-tone-params');
  if (params) params.style.display = (m === 1) ? 'flex' : 'none';
  // Abilita/disabilita controlli Canali
  const ctrl    = document.getElementById('canali-controls');
  const notice  = document.getElementById('canali-test-notice');
  const inTest  = m > 0;
  if (ctrl)   { ctrl.style.opacity = inTest ? '1' : '0.35'; ctrl.style.pointerEvents = inTest ? '' : 'none'; }
  if (notice) { notice.style.display = inTest ? 'none' : ''; }
}
function onTestTone() {
  const hz  = +document.getElementById('sl-test-hz').value;
  const amp = +document.getElementById('sl-test-amp').value;
  const dbfs = (20 * Math.log10(amp)).toFixed(0);
  document.getElementById('lbl-test-hz').textContent  = hz + ' Hz';
  document.getElementById('lbl-test-amp').textContent = (dbfs >= 0 ? '+' : '') + dbfs + ' dBFS';
  sendCmd('TEST_TONE_HZ=' + hz);
  sendCmd('TEST_TONE_AMP=' + amp);
  P.test_tone_hz  = hz;
  P.test_tone_amp = amp;
}


function setPreemph(us) {
  P.preemph = us;
  document.getElementById('lbl-preemph').textContent = us <= 0 ? 'LINEAR' : us + ' µs';
  ['btn-pre-off','btn-pre-50','btn-pre-75'].forEach(id => {
    document.getElementById(id).classList.remove('primary');
  });
  const map = {0:'btn-pre-off', 50:'btn-pre-50', 75:'btn-pre-75'};
  if (map[us] !== undefined) { document.getElementById(map[us]).classList.add('primary'); }
  sendCmd('PREEMPH=' + us);
}

// ── Mono mode ─────────────────────────────────────────────────────────────────
const _monoModeMap = { stereo:0, mono:3, L:1, R:2 };
const _monoModeLabels = { 0:'Stereo normale', 1:'Mono da L (R = L)', 2:'Mono da R (L = R)', 3:'Mono L+R (mix)' };
function toggleMono() {
  const isMono = (P.mono_mode !== 0);
  setMonoMode(isMono ? 'stereo' : 'mono');
}
function setMonoMode(mode) {
  const val = _monoModeMap[mode] ?? 0;
  sendCmd('MONO_MODE=' + val);
  P.mono_mode = val;
  _updateMonoModeUI(val);
}
function _updateMonoModeUI(val) {
  const ids = ['btn-mode-stereo','btn-mode-mono','btn-mode-l','btn-mode-r'];
  const vals = [0, 3, 1, 2];
  ids.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('primary', vals[i] === val);
  });
  const lbl = document.getElementById('lbl-mono-mode');
  if (lbl) lbl.textContent = _monoModeLabels[val] ?? '';
  // Bottone MONO nella tab TX: primary se in qualsiasi modalità mono, neutro se stereo
  const btnMono = document.getElementById('btn-mono');
  if (btnMono) {
    btnMono.classList.toggle('primary', val !== 0);
  }
}

// ── Gain L/R ─────────────────────────────────────────────────────────────────
let _gainsLinked = true;
function toggleGainLink() {
  _gainsLinked = !_gainsLinked;
  const btn  = document.getElementById('btn-link');
  const row  = document.getElementById('gain-r-row');
  const name = document.getElementById('lbl-gain-l-name');
  if (_gainsLinked) {
    btn.textContent = '🔗'; btn.classList.add('primary'); btn.classList.remove('danger');
    if (row)  row.style.display = 'none';
    if (name) name.textContent = 'Master';
  } else {
    btn.textContent = '🔓'; btn.classList.remove('primary'); btn.classList.add('danger');
    if (row)  row.style.display = '';
    if (name) name.textContent = 'Gain L';
  }
  sendCmd('GAINS_LINKED=' + (_gainsLinked ? '1' : '0'));
}
function onGainLR(ch, val) {
  const fmtDb = v => (v >= 0 ? '+' : '') + v.toFixed(1) + ' dB';
  if (ch === 'L') {
    document.getElementById('lbl-gain-l').textContent = fmtDb(val);
    sendCmd('GAIN_L=' + val);
    if (_gainsLinked) {
      document.getElementById('sl-gain-r').value = val;
      document.getElementById('lbl-gain-r').textContent = fmtDb(val);
      sendCmd('GAIN_R=' + val);
      // sincronizza anche lo slider legacy nella tab TX
      document.getElementById('sl-gain').value = val;
      document.getElementById('lbl-gain').textContent = fmtDb(val);
      sendCmd('GAIN=' + val);
      P.gain = val;
    }
    P.gain_l = val;
  } else {
    document.getElementById('lbl-gain-r').textContent = fmtDb(val);
    sendCmd('GAIN_R=' + val);
    P.gain_r = val;
  }
}


const _mpxSaved  = { pilot:0.09, stereo:0.44, rds:0.03 };
const _mpxActive = { pilot:true, stereo:true,  rds:true  };

function toggleMpx(name) {
  const nowOn = !_mpxActive[name];
  _mpxActive[name] = nowOn;
  // aggiorna tutti i bottoni associati a questo componente
  const btnIds = name === 'stereo'
    ? ['btn-stereo', 'btn-stereo-mpx', 'btn-stereo-t']
    : ['btn-' + name, 'btn-' + name + '-t'];
  const sl = document.getElementById('sl-' + name);
  btnIds.forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    if (nowOn) btn.classList.replace('danger','primary');
    else       btn.classList.replace('primary','danger');
  });
  if (nowOn) {
    sl.disabled = false; sl.style.opacity = '';
    const v = _mpxSaved[name];
    sl.value = v;
    onTxParam(name, v);
  } else {
    _mpxSaved[name] = parseFloat(sl.value) || _mpxSaved[name];
    sl.disabled = true; sl.style.opacity = '0.3';
    document.getElementById('lbl-' + name).textContent = '0.0 kHz';
    sendCmd({ pilot:'VOL_PILOT=0', stereo:'VOL_STEREO=0', rds:'VOL_RDS=0' }[name]);
  }
}


// ─────────────────────────────────────────────
// Gestione tema
// ─────────────────────────────────────────────
const THEMES = [
  { id: 'dark',  file: 'theme-dark.css',  icon: '🌙', label: 'Dark'  },
  { id: 'light', file: 'theme-light.css', icon: '☀',  label: 'Light' },
  { id: 'neon',  file: 'theme-neon.css',  icon: '⚡', label: 'Neon'  },
];

function applyTheme(id) {
  const t = THEMES.find(x => x.id === id) ?? THEMES[0];
  document.getElementById('theme-css').href = t.file;
  document.getElementById('btn-theme').textContent = t.icon;
  document.getElementById('btn-theme').title = 'Tema: ' + t.label;
  localStorage.setItem('fm_theme', id);
}

function cycleTheme() {
  const cur = localStorage.getItem('fm_theme') ?? 'dark';
  const idx = THEMES.findIndex(x => x.id === cur);
  applyTheme(THEMES[(idx + 1) % THEMES.length].id);
}

// Applica tema salvato all'avvio
(function() {
  const saved = localStorage.getItem('fm_theme');
  if (saved && saved !== 'dark') applyTheme(saved);
})();

// ─────────────────────────────────────────────
// Sorgente Audio
// ─────────────────────────────────────────────
const SRC_LABELS = {
  webradio: '🌐 WEBRADIO',
  audioin:  '🎙 AUDIO IN',
  mpx:      '📡 MPX',
};

function updateSourceUI(source) {
  ['webradio','audioin','mpx'].forEach(s => {
    const btn = document.getElementById('btn-src-' + s);
    if (!btn) return;
    btn.classList.toggle('primary', s === source);
  });
  const badge = document.getElementById('src-status-badge');
  if (badge) {
    badge.textContent = SRC_LABELS[source] ?? source;
    badge.style.background = 'var(--green-dim)';
    badge.style.color = 'var(--green)';
    badge.style.borderColor = 'var(--green)';
  }
}

function srcLog(msg, isErr) {
  const el = document.getElementById('src-log');
  if (!el) return;
  el.textContent = msg;
  el.style.color = isErr ? 'var(--red)' : 'var(--text-mid)';
}

async function selectSource(src) {
  if (!SRC_LABELS[src]) return;
  srcLog('Cambio in corso…');
  // Disabilita bottoni durante il cambio
  ['webradio','audioin','mpx'].forEach(s => {
    const b = document.getElementById('btn-src-' + s);
    if (b) b.disabled = true;
  });
  try {
    const r = await fetch('/api/source/select', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({source: src})
    });
    const d = await r.json();
    if (d.ok) {
      updateSourceUI(src);
      srcLog(d.msg ?? 'Sorgente attiva: ' + SRC_LABELS[src]);
    } else {
      srcLog('Errore: ' + (d.error ?? 'sconosciuto'), true);
    }
  } catch(e) {
    srcLog('Errore di rete: ' + e.message, true);
  } finally {
    ['webradio','audioin','mpx'].forEach(s => {
      const b = document.getElementById('btn-src-' + s);
      if (b) b.disabled = false;
    });
  }
}

async function srcCfgSave() {
  const payload = {};
  const u = document.getElementById('cfg-url-webradio');
  const a = document.getElementById('cfg-dev-audioin');
  const m = document.getElementById('cfg-dev-mpx');
  if (u) payload.url_webradio = u.value;
  if (a) payload.dev_audioin  = a.value;
  if (m) payload.dev_mpx      = m.value;
  const r = await fetch('/api/source/config', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const d = await r.json();
  srcLog(d.ok ? '✓ Configurazione sorgenti salvata' : 'Errore salvataggio', !d.ok);
}

// Funzioni chain esistenti (collegate ai controlli in tab-Service)
async function chainSourceChanged() {
  const sel = document.getElementById('chain-source');
  if (!sel) return;
  chainSourceShowRows(sel.value);
}

function chainSourceShowRows(src) {
  const rows = {
    'chain-row-url':   ['webradio'],
    'chain-row-dev1':  ['alsa1'],
    'chain-row-dev2':  ['alsa2','mpx_in'],
    'chain-row-rate':  ['alsa1','alsa2','mpx_in'],
    'chain-row-tone':  ['tone'],
  };
  for (const [id, srcs] of Object.entries(rows)) {
    const el = document.getElementById(id);
    if (el) el.style.display = srcs.includes(src) ? 'grid' : 'none';
  }
}

async function chainSaveCfg() {
  const sel   = document.getElementById('chain-source');
  const url   = document.getElementById('chain-url');
  const dev1  = document.getElementById('chain-dev1');
  const dev2  = document.getElementById('chain-dev2');
  const rate  = document.getElementById('chain-rate');
  const fg    = document.getElementById('chain-flowgraph');
  const ar    = document.getElementById('tog-chain-restart');
  const tf    = document.getElementById('chain-tone-freq');
  const ta    = document.getElementById('chain-tone-amp');
  const payload = {};
  if (sel)  payload.source_type   = sel.value;
  if (url)  payload.url           = url.value;
  if (dev1) payload.device        = dev1.value;
  if (dev2) payload.device2       = dev2.value;
  if (rate) payload.sample_rate   = +rate.value;
  if (fg)   payload.flowgraph     = fg.value;
  if (ar)   payload.auto_restart  = ar.checked;
  if (tf)   payload.tone_freq     = +tf.value;
  if (ta)   payload.tone_amp      = +ta.value;
  const r = await fetch('/api/chain/config', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const d = await r.json();
  const log = document.getElementById('chain-log');
  if (log) log.textContent += (d.ok !== false ? '✓ Config salvata\n' : '✗ Errore\n');
}
