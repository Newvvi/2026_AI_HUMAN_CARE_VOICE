/* 독거노인 안심 모니터 — 화면 동작
   서버(/api/state, /api/stream)에서 받은 것만 보여 준다. 판단 등급은 만들지 않는다. */
'use strict';

const S = { events: new Map(), devices: {}, broker: { connected: false }, info: {}, history: [], range: '600', recentLabeled: new Set() };
const SRC = { audio: { name: '음성', dev: 'R1', icon: '🔊' }, vision: { name: '영상', dev: 'R2', icon: '📹' } };
const STATUS_TEXT = { online: '연결됨', offline: '끊김', unknown: '확인 불가', connected: '연결됨', disconnected: '끊김' };
const LABEL_TEXT = { confirmed: '확인됨', false_alarm: '오경보', none: '확인 전' };
const maxPendingCards = () => (window.innerWidth < 560 ? 2 : 3);   // 휴대폰은 2장

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const srcOf = (s) => SRC[s] || { name: s || '알 수 없음', dev: '', icon: '❔' };
const srcClass = (s) => (SRC[s] ? s : 'other');

/* ── 시간 ── */
function toDate(v) { if (!v) return null; const d = new Date(v); return isNaN(d) ? null : d; }
function evTime(ev) { return toDate(ev.occurred_at) || toDate(ev.received_at); }
// 노트북 시간대 설정과 상관없이 항상 한국 시각으로 표시
const KST_TIME = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const KST_DATE = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' });
function hms(d) { return d ? KST_TIME.format(d) : '--:--:--'; }
function ago(d) {
  if (!d) return '';
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 10) return '방금';
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return KST_DATE.format(d).slice(5).replace('-', '/');
}
function isToday(d) { return !!d && KST_DATE.format(d) === KST_DATE.format(new Date()); }

/* ── 상태 계산 ── */
function deviceValue(name) {
  if (S.info.mode === 'live' && !S.broker.connected) return 'unknown';   // 문서 규칙: 브로커와 끊기면 둘 다 확인 불가
  return (S.devices[name] && S.devices[name].value) || 'unknown';
}
const isBroken = (ev) => ev.contract && ev.contract.level === 'error';
const needsCheck = (ev) => ev.label === 'none' && !isBroken(ev);
function sortedEvents() { return [...S.events.values()].sort((a, b) => (evTime(b) || 0) - (evTime(a) || 0)); }

/* ── 상태 띠 ── */
function renderStatus() {
  const set = (id, v, title) => {
    const el = document.getElementById(id);
    el.dataset.v = v;
    $('em', el).textContent = STATUS_TEXT[v] || v;
    el.title = title || '';
  };
  set('st-broker', S.broker.connected ? 'connected' : 'disconnected', S.broker.since ? `바뀐 시각 ${hms(toDate(S.broker.since))}` : '');
  for (const dev of ['audio', 'vision']) {
    const d = S.devices[dev] || {};
    const v = deviceValue(dev);
    set(`st-${dev}`, v, d.since ? `받은 시각 ${hms(toDate(d.since))}${d.retained ? ' (접속할 때 브로커가 보관하던 마지막 상태)' : ''}` : '아직 상태 메시지 없음');
  }
  const err = $('#broker-error');
  const msg = S.serverLost ? '모니터 서버와 연결이 끊겼습니다. 노트북의 실행 창이 켜져 있는지 확인하세요.' : (S.broker.error || '');
  err.hidden = !msg;
  err.textContent = msg;
}

/* ── 오늘 요약 ── */
function renderToday() {
  const today = [...S.events.values()].filter((e) => isToday(evTime(e)) && !isBroken(e));
  const c = (f) => today.filter(f).length;
  $('#today').innerHTML =
    `<span>오늘 음성 감지<b>${c((e) => e.source === 'audio')}</b></span>` +
    `<span>영상 감지<b>${c((e) => e.source === 'vision')}</b></span>` +
    `<span>확인 대기<b>${c(needsCheck)}</b></span>` +
    `<span>확인됨<b>${c((e) => e.label === 'confirmed')}</b></span>` +
    `<span>오경보<b>${c((e) => e.label === 'false_alarm')}</b></span>`;
}

/* ── 카드 (확인 대기 · 상세 공용) ── */
function chipsHTML(ev) {
  const out = [];
  if (ev.delay_sec != null) {
    out.push(ev.delay_sec >= 60
      ? `<li class="chip warn">늦게 도착 ${Math.round(ev.delay_sec)}초</li>`
      : `<li class="chip" title="occurred_at(발생) → 대시보드가 받은 시각">발생→도착 ${ev.delay_sec}초</li>`);
  }
  if (ev.same_second) out.push('<li class="chip info">같은 초 연속 감지</li>');
  if (ev.dup_count) out.push(`<li class="chip">중복 수신 ${ev.dup_count}회</li>`);
  if (ev.dup_conflict) out.push('<li class="chip error">같은 번호, 다른 내용</li>');
  if (ev.contract && ev.contract.level === 'ok') out.push('<li class="chip">규약 준수</li>');
  for (const [lv, text] of (ev.contract && ev.contract.problems) || []) out.push(`<li class="chip ${lv}">${esc(text)}</li>`);
  return out.join('');
}
function actionsHTML(ev) {
  if (isBroken(ev)) return '<div class="labeled">규약 오류 메시지는 확인 대상이 아닙니다</div>';
  if (ev.label === 'none') {
    return `<div class="actions">
      <button class="btn confirm" type="button" data-act="label" data-label="confirmed" data-key="${esc(ev.key)}">확인됨</button>
      <button class="btn false" type="button" data-act="label" data-label="false_alarm" data-key="${esc(ev.key)}">오경보</button>
    </div>`;
  }
  const rt = ev.response_sec != null ? `알림 후 ${ev.response_sec}초 만에 표시` : '';
  return `<div class="labeled ${ev.label}"><span>${LABEL_TEXT[ev.label]}</span><small>${rt}</small>
    <button class="btn sub" type="button" data-act="label" data-label="none" data-key="${esc(ev.key)}">되돌리기</button></div>`;
}
function cardHTML(ev) {
  const s = srcOf(ev.source);
  const t = evTime(ev);
  const pct = ev.confidence != null ? Math.round(ev.confidence * 100) : null;
  return `
    <div class="card-head"><span class="src">${s.icon} ${esc(s.name)} ${s.dev}</span><span class="ago" data-t="${t ? t.toISOString() : ''}">${ago(t)}</span></div>
    <div class="card-time">${hms(t)}${toDate(ev.occurred_at) ? '' : '<small class="t-note">받은 시각</small>'}</div>
    <div class="iid">incident_id ${esc(ev.incident_id || '(없음)')}</div>
    <div class="meter">
      <div class="meter-row"><span>신뢰도 (confidence)</span><b>${pct != null ? pct + '%' : '값 없음'}</b></div>
      <div class="bar"><i style="width:${pct != null ? Math.max(0, Math.min(100, pct)) : 0}%"></i></div>
    </div>
    <ul class="chips">${chipsHTML(ev)}</ul>
    <div class="act-wrap">${actionsHTML(ev)}</div>
    <div class="more-row">
      <button class="btn sub" type="button" data-act="clip" data-key="${esc(ev.key)}" ${ev.clip_url ? '' : 'disabled'}>${ev.source === 'vision' ? '클립 보기' : '클립 듣기'}</button>
      <button class="btn sub" type="button" data-open="${esc(ev.key)}">자세히</button>
    </div>
    <div class="clip" hidden></div>`;
}
function prettyRaw(raw) { try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; } }
function makeCard(ev, isNew) {
  const el = document.createElement('article');
  el.className = `card ${srcClass(ev.source)}${isNew ? ' new' : ''}`;
  el.dataset.key = ev.key;
  el.innerHTML = cardHTML(ev);
  return el;
}
function updateCard(el, ev) {   // 재생 중인 클립이 끊기지 않게 바뀌는 부분만 갱신
  $('.chips', el).innerHTML = chipsHTML(ev);
  $('.act-wrap', el).innerHTML = actionsHTML(ev);
}

/* ── 확인 대기 목록 ── */
function renderPending(newKey) {
  const box = $('#pending-list');
  const all = sortedEvents().filter((e) => needsCheck(e) || S.recentLabeled.has(e.key));
  const show = all.slice(0, maxPendingCards());
  const keys = new Set(show.map((e) => e.key));
  for (const el of [...box.querySelectorAll('.card')]) if (!keys.has(el.dataset.key)) el.remove();
  for (const n of [...box.querySelectorAll('.empty, .pending-more')]) n.remove();
  show.forEach((ev, i) => {
    let el = box.querySelector(`.card[data-key="${CSS.escape(ev.key)}"]`);
    if (el) { updateCard(el, ev); return; }
    el = makeCard(ev, ev.key === newKey);
    const cards = [...box.querySelectorAll('.card')];
    const next = cards.find((c) => show.findIndex((x) => x.key === c.dataset.key) > i);
    box.insertBefore(el, next || null);   // 기존 카드는 움직이지 않음
  });
  const waiting = all.filter(needsCheck).length;
  if (!show.length) {
    box.insertAdjacentHTML('beforeend', `<div class="empty"><b>확인할 알림이 없습니다</b>${
      deviceValue('audio') === 'online' || deviceValue('vision') === 'online'
        ? '새 감지가 오면 여기에 바로 나타납니다.' : '음성 R1·영상 R2 연결 상태를 위에서 확인하세요.'}</div>`);
  } else if (waiting > show.length) {
    box.insertAdjacentHTML('beforeend', `<p class="pending-more">이 밖에 ${waiting - show.length}건이 더 기다리고 있습니다. 최근 감지에서 열어 확인하세요.</p>`);
  }
}

/* ── 최근 감지 목록 ── */
function renderFeed() {
  const list = sortedEvents().slice(0, 60);
  const ol = $('#feed-list');
  if (!list.length) { ol.innerHTML = '<li class="feed-empty">아직 받은 감지가 없습니다.</li>'; return; }
  ol.innerHTML = list.map((ev) => {
    const s = srcOf(ev.source), t = evTime(ev);
    const pct = ev.confidence != null ? Math.round(ev.confidence * 100) : 0;
    const st = isBroken(ev) ? ['bad', '규약 오류'] : [ev.label, LABEL_TEXT[ev.label]];
    return `<li><button class="row ${srcClass(ev.source)}" type="button" data-open="${esc(ev.key)}">
      <span class="band"></span>
      <span class="t">${hms(t).slice(0, 5)}<small class="ago" data-t="${t ? t.toISOString() : ''}">${ago(t)}</small></span>
      <span class="mid"><span class="who">${s.icon} ${esc(s.name)} · ${ev.confidence != null ? pct + '%' : '값 없음'}</span>
        <span class="id">${esc(ev.incident_id || ev.key)}${ev.dup_count ? ` · 중복 ${ev.dup_count}` : ''}</span>
        <span class="mini"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></span></span>
      <span class="state ${st[0]}">${st[1]}</span>
    </button></li>`;
  }).join('');
}

/* ── 상세 시트 ── */
function openSheet(key) {
  const ev = S.events.get(key); if (!ev) return;
  const body = $('#sheet-body');
  destroyClips(body);
  body.innerHTML = '';
  const card = makeCard(ev, false);
  $('.more-row', card).remove();
  $('.clip', card).remove();
  card.insertAdjacentHTML('beforeend', detailHTML(ev));
  body.appendChild(card);
  const dlg = $('#sheet');
  if (!dlg.open) dlg.showModal();
  body.scrollTop = 0;
  loadClip($('.d-clip', card), ev, false);
}
$('#sheet').addEventListener('close', () => destroyClips($('#sheet-body')));
function refreshSheet(key) {
  const el = $(`#sheet-body .card[data-key="${CSS.escape(key)}"]`);
  if (el && S.events.has(key)) updateCard(el, S.events.get(key));
}

/* ── 전체 갱신 ── */
function renderAll(newKey) {
  renderStatus(); renderToday(); renderPending(newKey); renderFeed(); renderBadge(); renderTimeline(); renderStats(); renderSystem();
}
function renderBadge() {
  const n = [...S.events.values()].filter(needsCheck).length;
  const b = $('#pending-badge');
  b.hidden = !n; b.textContent = n;
  document.title = n ? `(${n}) 확인 필요 - 독거노인 안심 모니터` : '독거노인 안심 모니터';
}

/* ── 알림 (소리·진동) ── */
let audioCtx = null;
function beep(source) {
  if (!audioCtx || localStorage.getItem('care.sound') !== 'on') return;
  const base = source === 'vision' ? 660 : 880;
  [0, 0.22].forEach((delay, i) => {
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = base + i * 110;
    g.gain.setValueAtTime(0.0001, audioCtx.currentTime + delay);
    g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + delay + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + delay + 0.18);
    o.connect(g).connect(audioCtx.destination);
    o.start(audioCtx.currentTime + delay); o.stop(audioCtx.currentTime + delay + 0.2);
  });
}
function notify(ev) {
  if (isBroken(ev)) return;
  beep(ev.source);
  if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
}

/* ── 서버 메시지 처리 ── */
function handle(msg) {
  const d = msg.data;
  switch (msg.kind) {
    case 'alert': S.events.set(d.key, d); renderAll(d.key); notify(d); break;
    case 'dup': { const ev = S.events.get(d.key); if (ev) Object.assign(ev, d); renderAll(); refreshSheet(d.key); break; }
    case 'status': S.devices[d.device] = { value: d.value, since: d.at, retained: d.retained }; S.history.push(d); renderStatus(); renderPending(); renderTimeline(); renderSystem(); break;
    case 'broker': S.broker = d; S.history.push({ device: 'broker', value: d.connected ? 'connected' : 'disconnected', at: d.since }); renderStatus(); renderPending(); renderTimeline(); renderSystem(); break;
    case 'label': applyLabel(d); break;
    case 'demo': S.info.demo_running = d.running; break;
    case 'reset': loadState(); break;
  }
}
function applyLabel(d) {
  const ev = S.events.get(d.key); if (!ev) return;
  Object.assign(ev, d);
  if (d.label !== 'none') {   // 표시 결과를 잠깐 보여 준 뒤 확인 대기에서 내림
    S.recentLabeled.add(d.key);
    setTimeout(() => { S.recentLabeled.delete(d.key); renderPending(); }, 1500);
  }
  renderAll(); refreshSheet(d.key);
}

async function loadState() {
  const s = await (await fetch('/api/state')).json();
  S.events = new Map(s.events.map((e) => [e.key, e]));
  S.devices = s.devices; S.broker = s.broker; S.info = s.info; S.history = s.history || [];
  $('#pending-list').innerHTML = '';
  setupMode();
  renderAll();
}
function connectStream() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  es.onerror = () => { S.serverLost = true; renderStatus(); };
  es.onopen = () => { if (S.serverLost) { S.serverLost = false; loadState(); } };
}

/* ── 버튼 동작 ── */
async function postLabel(key, label) {
  const r = await fetch('/api/label', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, label }) });
  if (r.ok) applyLabel(await r.json());
}
async function toggleClip(btn) {
  const card = btn.closest('.card'); const box = $('.clip', card);
  if (!box.hidden) { destroyClips(box); box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  loadClip(box, S.events.get(btn.dataset.key), true);
}

/* ── 클립 (5단계) ── */
const AUDIO_EXT = ['.wav', '.mp3', '.ogg', '.m4a', '.flac'];
const VIDEO_EXT = ['.mp4', '.webm', '.mov', '.m4v'];
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmtBytes = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + 'MB' : Math.max(1, Math.round(b / 1024)) + 'KB');
const fmtSec = (x) => (x == null || !isFinite(x) ? '-' : `${Math.floor(x / 60)}:${pad2(Math.floor(x % 60))}.${Math.floor((x % 1) * 10)}`);
const pad2 = (n) => String(n).padStart(2, '0');
function destroyClips(root) {
  root.querySelectorAll('.ws').forEach((el) => { if (el._ws) { el._ws.destroy(); el._ws = null; } });
  root.querySelectorAll('audio, video').forEach((m) => { m.pause(); m.removeAttribute('src'); m.load(); });
}
async function loadClip(box, ev, autoplay) {
  if (!box || !ev) return;
  if (!ev.clip_url) { box.innerHTML = '<p class="clip-msg">이 감지에는 clip_url이 없습니다.</p>'; return; }
  box.innerHTML = '<p class="clip-wait">클립을 가져오는 중…</p>';
  let info;
  try { info = await (await fetch('/api/clipcheck/' + encodeURIComponent(ev.key))).json(); }
  catch { box.innerHTML = '<p class="clip-msg">모니터 서버에 연결할 수 없습니다.</p>'; return; }
  S.clipResults = S.clipResults || {}; S.clipResults[ev.key] = !!info.ok;
  const meta = box.closest('.card') && box.closest('.card').querySelector('.clip-meta');
  if (meta) meta.innerHTML = clipMetaHTML(ev, info);
  if (!info.ok) { box.innerHTML = `<p class="clip-msg">${esc(info.error || '클립을 열 수 없습니다')}</p>`; return; }
  const src = '/api/clip/' + encodeURIComponent(ev.key);
  const isVideo = VIDEO_EXT.includes(info.ext) || (!AUDIO_EXT.includes(info.ext) && ev.source === 'vision');
  if (isVideo) {
    box.innerHTML = `<video controls playsinline preload="metadata" src="${src}"></video><p class="clip-sub"></p>`;
    const v = $('video', box), sub = $('.clip-sub', box);
    v.addEventListener('loadedmetadata', () => { sub.textContent = `${v.videoWidth}×${v.videoHeight} · ${fmtSec(v.duration)}`; });
    v.addEventListener('error', () => { sub.className = 'clip-msg'; sub.textContent = '브라우저가 이 영상 형식을 재생하지 못합니다. H.264 mp4인지 영상 팀에 확인이 필요합니다.'; });
    if (autoplay) v.play().catch(() => {});
    return;
  }
  // 음성: 파형으로 보여 주기 (wavesurfer.js가 없으면 기본 플레이어)
  if (!window.WaveSurfer) {
    box.innerHTML = `<audio controls preload="metadata" src="${src}"></audio>`;
    if (autoplay) $('audio', box).play().catch(() => {});
    return;
  }
  box.innerHTML = `<div class="ws"></div>
    <div class="ws-bar"><button class="btn sub ws-play" type="button">재생</button><span class="ws-time">0:00.0 / -</span></div>`;
  const holder = $('.ws', box), playBtn = $('.ws-play', box), time = $('.ws-time', box);
  const ws = WaveSurfer.create({
    container: holder, url: src, height: 72, normalize: true, barWidth: 2, barGap: 1, barRadius: 2,
    waveColor: cssVar('--ink-2') || '#789', progressColor: cssVar('--audio') || '#c60', cursorColor: cssVar('--ink') || '#222',
  });
  holder._ws = ws;
  const tick = () => { time.textContent = `${fmtSec(ws.getCurrentTime())} / ${fmtSec(ws.getDuration())}`; };
  ws.on('ready', () => { tick(); if (autoplay) ws.play().catch(() => {}); });
  ws.on('timeupdate', tick);
  ws.on('play', () => { playBtn.textContent = '일시정지'; });
  ws.on('pause', () => { playBtn.textContent = '재생'; });
  ws.on('finish', () => { playBtn.textContent = '다시 재생'; });
  ws.on('error', () => { box.insertAdjacentHTML('beforeend', '<p class="clip-msg">파형을 그리지 못했습니다. 브라우저가 이 형식을 읽지 못합니다.</p>'); });
  playBtn.addEventListener('click', () => ws.playPause());
}
function clipMetaHTML(ev, info) {
  const rows = [['원본 주소', `<span class="url">${esc(ev.clip_url)}</span>`]];
  if (!info.ok) { rows.push(['접근 결과', `<span class="bad">실패</span> ${esc(info.error || '')}`]); }
  else {
    rows.push(['접근 결과', `<span class="good">성공</span>${info.fetch_ms != null ? ` (${info.fetch_ms}ms)` : ''}`]);
    rows.push(['파일', `${esc((info.ext || '').replace('.', '').toUpperCase())} · ${fmtBytes(info.bytes)}`]);
    if (info.duration_sec != null) rows.push(['길이', `${info.duration_sec}초`]);
    if (info.sample_rate) rows.push(['음성 형식', `${info.sample_rate}Hz · ${info.channels === 1 ? '모노' : info.channels + '채널'}`]);
    rows.push(['보관', `노트북에 보관됨 <a class="dl" href="/api/clip/${encodeURIComponent(ev.key)}" download="${esc(ev.incident_id || ev.key)}${esc(info.ext || '')}">내려받기</a>`]);
  }
  return rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
}

/* ── 상세 패널 (5단계): JSON 4필드를 하나씩 풀어서 보여 준다 ── */
const FIELD_INFO = {
  incident_id: '감지 하나의 고유 번호. 음성은 A_ + 발생 시각, 같은 초 두 번째부터 _2',
  confidence: '모델이 위험 신호라고 본 정도 (0~1)',
  occurred_at: '감지가 일어난 시각 (+09:00 한국 시간)',
  clip_url: '확인용 클립의 HTTP 주소. MQTT는 주소만 보내고 파일은 따로 가져옴',
};
function fieldRows(ev) {
  let data = null; try { data = JSON.parse(ev.raw); } catch { /* 규약 오류 */ }
  const probs = (ev.contract && ev.contract.problems) || [];
  return Object.keys(FIELD_INFO).map((f) => {
    const mine = probs.filter(([, text]) => text.includes(f));
    const lv = mine.some(([l]) => l === 'error') ? 'error' : mine.length ? 'warn' : 'ok';
    const has = data && Object.prototype.hasOwnProperty.call(data, f);
    const val = has ? (typeof data[f] === 'string' ? data[f] : JSON.stringify(data[f])) : '(없음)';
    const note = lv === 'ok' ? '규약 준수' : mine.map(([, t]) => t).join(', ');
    return `<tr class="${lv}"><th scope="row">${f}</th><td class="v">${esc(val)}</td><td>${FIELD_INFO[f]}</td><td class="chk">${lv === 'ok' ? '✓ ' : '! '}${esc(note)}</td></tr>`;
  }).join('');
}
function partnersHTML(ev) {
  if (isBroken(ev)) return '<p class="d-none">규약 오류 메시지라 다른 감지와 비교하지 않습니다.</p>';
  const ps = computePairs().filter((p) => p.a.key === ev.key || p.v.key === ev.key);
  if (!ps.length) return `<p class="d-none">10초 안에 함께 감지된 ${ev.source === 'audio' ? '영상' : '음성'} 감지가 없습니다.</p>`;
  return ps.map((p) => {
    const o = p.a.key === ev.key ? p.v : p.a, s = srcOf(o.source);
    return `<button type="button" class="partner ${srcClass(o.source)}" data-open="${esc(o.key)}">
      <b>${s.icon} ${s.name} ${s.dev} ${hms(evTime(o))}</b><span>${p.diff}초 차 · 신뢰도 ${o.confidence != null ? Math.round(o.confidence * 100) + '%' : '값 없음'} · ${LABEL_TEXT[o.label]}</span></button>`;
  }).join('');
}
function detailHTML(ev) {
  const rows = [
    ['토픽', esc(ev.topic)],
    ['발생 시각', toDate(ev.occurred_at) ? `${hms(toDate(ev.occurred_at))} (${esc(ev.occurred_at)})` : `읽을 수 없음${ev.occurred_at != null ? ` (받은 값: ${esc(ev.occurred_at)})` : ''}`],
    ['받은 시각', `${hms(toDate(ev.received_at))}`],
    ['발생→도착', ev.delay_sec != null ? `${ev.delay_sec}초${ev.delay_sec >= 60 ? ' (늦게 도착)' : ''}` : '-'],
    ['중복 수신', ev.dup_count ? `${ev.dup_count}회${ev.dup_conflict ? ' (내용이 다른 중복 있음)' : ''}` : '없음'],
    ['같은 초 감지', ev.same_second ? '예 (번호 끝 _2 등)' : '아니요'],
  ];
  return `
  <section class="d-sec"><h3>클립</h3><div class="d-clip clip"></div><dl class="clip-meta kv"></dl></section>
  <section class="d-sec"><h3>함께 감지 (10초 안)</h3><div class="partners">${partnersHTML(ev)}</div></section>
  <section class="d-sec"><h3>JSON 4필드</h3>
    <div class="tbl"><table class="fields"><thead><tr><th>필드</th><th>받은 값</th><th>뜻</th><th>규약 검사</th></tr></thead><tbody>${fieldRows(ev)}</tbody></table></div></section>
  <section class="d-sec"><h3>전달 정보</h3><dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl></section>
  <details class="d-sec raw-d"><summary>원문 JSON (받은 그대로)</summary><pre>${esc(prettyRaw(ev.raw))}</pre></details>`;
}
document.addEventListener('click', (e) => {
  const o = e.target.closest('[data-open]'); if (o) { openSheet(o.dataset.open); return; }
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.act === 'label') postLabel(b.dataset.key, b.dataset.label);
  else if (b.dataset.act === 'clip') toggleClip(b);
  else if (b.dataset.act === 'raw') { const r = $('.raw', b.closest('.card')); r.hidden = !r.hidden; }
  else if (b.dataset.range) setRange(b.dataset.range);
  else if (b.dataset.srange) setStatsRange(b.dataset.srange);
  else if (b.dataset.act === 'copysum') copySummary();
  else if (b.dataset.act === 'copy') copyText(document.querySelector(b.dataset.copy).textContent, b);
  else if (b.dataset.act === 'newsession') newSession();
  else if (b.dataset.tab) switchTab(b.dataset.tab);
  else if (b.dataset.demo) fetch('/api/demo/' + b.dataset.demo, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ speed: Number(b.dataset.speed || 1) }) });
});
function switchTab(name) {
  document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('on', x.dataset.tab === name));
  document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('on', x.id === 'tab-' + name));
  if (name === 'timeline') renderTimeline();
  if (name === 'stats') renderStats();
  if (name === 'system') renderSystem();
}
function setupMode() {
  const badge = $('#mode-badge');
  if (S.info.mode === 'live') { badge.textContent = `실시간 · 브로커 ${S.info.broker}`; badge.className = 'mode-badge live'; }
  else { badge.textContent = '데모'; badge.className = 'mode-badge'; }
  $('#demo-bar').hidden = S.info.mode !== 'demo';
}
function setupPrefs() {
  const sound = $('#btn-sound'), big = $('#btn-big');
  const paint = () => {
    const on = localStorage.getItem('care.sound') === 'on';
    sound.setAttribute('aria-pressed', on); sound.textContent = on ? '알림음 켜짐' : '알림음 켜기';
    const isBig = localStorage.getItem('care.big') === 'on';
    big.setAttribute('aria-pressed', isBig); document.documentElement.classList.toggle('big', isBig);
  };
  sound.addEventListener('click', () => {
    const on = localStorage.getItem('care.sound') !== 'on';
    localStorage.setItem('care.sound', on ? 'on' : 'off');
    if (on) { audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)(); audioCtx.resume(); beep('audio'); }
    paint();
  });
  big.addEventListener('click', () => { localStorage.setItem('care.big', localStorage.getItem('care.big') === 'on' ? 'off' : 'on'); paint(); });
  // 브라우저 정책상 소리는 화면을 한 번 누른 뒤부터 가능
  document.addEventListener('pointerdown', () => {
    if (localStorage.getItem('care.sound') === 'on' && !audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }, { once: true });
  paint();
}


/* ── 타임라인 (4단계) ──
   음성 줄과 영상 줄에 감지를 점으로 찍고, 시각이 10초 안인 음성·영상 쌍을 선으로 잇는다 (표시만, 판단 아님) */
const PAIR_SEC = 10;
const KST_OFFSET = 9 * 3600e3;
function validTimed(ev) { return !isBroken(ev) && evTime(ev) && SRC[ev.source]; }
function computePairs() {
  const evs = [...S.events.values()].filter(validTimed);
  const a = evs.filter((e) => e.source === 'audio'), v = evs.filter((e) => e.source === 'vision');
  // 음성 감지마다 10초 안에서 가장 가까운 영상 감지 하나와만 잇는다 (한 음성이 여러 번 세지지 않게)
  const pairs = [];
  for (const x of a) {
    let best = null;
    for (const y of v) {
      const diff = Math.abs(evTime(x) - evTime(y)) / 1000;
      if (diff <= PAIR_SEC && (!best || diff < best.diff)) best = { a: x, v: y, diff };
    }
    if (best) pairs.push({ ...best, diff: Math.round(best.diff * 10) / 10 });
  }
  return pairs.sort((p, q) => Math.max(evTime(q.a), evTime(q.v)) - Math.max(evTime(p.a), evTime(p.v)));
}
function setRange(r) {
  S.range = r;
  document.querySelectorAll('[data-range]').forEach((x) => x.classList.toggle('on', x.dataset.range === r));
  renderTimeline();
}
function timeWindow() {
  const now = Date.now();
  if (S.range === 'today') {
    const start = Math.floor((now + KST_OFFSET) / 86400e3) * 86400e3 - KST_OFFSET;
    return [start, now + (now - start) * 0.02];
  }
  if (S.range === 'all') {
    const ts = [...S.events.values()].filter(validTimed).map((e) => evTime(e).getTime());
    if (!ts.length) return [now - 600e3, now + 20e3];
    const lo = Math.min(...ts), hi = Math.max(now, ...ts), pad = Math.max((hi - lo) * 0.05, 15e3);
    return [lo - pad, hi + pad];
  }
  const r = Number(S.range) * 1000;
  return [now - r * 0.97, now + r * 0.03];
}
function offIntervals(device, end) {   // 끊김 구간: offline(장치) 또는 disconnected(브로커)
  const hs = S.history.filter((h) => h.device === device || h.device === 'broker')
    .map((h) => ({ ...h, t: toDate(h.at) })).filter((h) => h.t).sort((x, y) => x.t - y.t);
  const out = []; let devOff = null, brokerOff = null;
  const open = () => devOff !== null || brokerOff !== null;
  let since = null;
  for (const h of hs) {
    const was = open();
    if (h.device === 'broker') brokerOff = h.value === 'disconnected' ? (brokerOff ?? h.t) : null;
    else devOff = h.value === 'offline' ? (devOff ?? h.t) : null;
    const is = open();
    if (!was && is) since = h.t;
    if (was && !is && since) { out.push([since.getTime(), h.t.getTime()]); since = null; }
  }
  if (open() && since) out.push([since.getTime(), end]);
  return out;
}
function renderTimeline() {
  const wrap = document.getElementById('tl-wrap');
  if (!wrap || !document.getElementById('tab-timeline').classList.contains('on')) { renderPairs(); return; }
  const W = Math.max(300, wrap.clientWidth || 800), narrow = W < 480, H = 232, L = narrow ? 62 : 84, R = narrow ? 10 : 18;
  const lanes = { audio: { y: 62, label: narrow ? '🔊 음성' : '🔊 음성 R1' }, vision: { y: 146, label: narrow ? '📹 영상' : '📹 영상 R2' } };
  const laneH = 64, axisY = 196;
  const [start, end] = timeWindow();
  const x = (t) => L + ((t - start) / (end - start)) * (W - L - R);
  const inWin = (t) => t >= start && t <= end;

  // 눈금 간격: 화면 폭에 맞춰 자동
  const steps = [10e3, 30e3, 60e3, 120e3, 300e3, 600e3, 900e3, 1800e3, 3600e3, 7200e3, 10800e3, 21600e3];
  const maxTicks = Math.max(3, Math.floor((W - L - R) / 86));
  const step = steps.find((st) => (end - start) / st <= maxTicks) || steps[steps.length - 1];
  const ticks = [];
  for (let t = Math.ceil((start + KST_OFFSET) / step) * step - KST_OFFSET; t <= end; t += step) ticks.push(t);
  const fmtTick = (t) => (step < 60e3 ? hms(new Date(t)) : hms(new Date(t)).slice(0, 5));

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="음성·영상 감지 타임라인">
    <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="6" style="stroke: var(--unk); stroke-width: 2; opacity: .55"></line></pattern></defs>`;
  for (const [k, ln] of Object.entries(lanes)) {
    svg += `<rect class="lane-bg" x="${L}" y="${ln.y - laneH / 2}" width="${W - L - R}" height="${laneH}" rx="8"></rect>`;
    svg += `<text class="lane-label" x="10" y="${ln.y + 5}">${ln.label}</text>`;
    for (const [a, b] of offIntervals(k, Date.now())) {
      const s0 = Math.max(a, start), e0 = Math.min(b, end);
      if (e0 > s0) svg += `<rect class="off" x="${x(s0)}" y="${ln.y - laneH / 2}" width="${Math.max(2, x(e0) - x(s0))}" height="${laneH}"><title>${k === 'audio' ? '음성 R1' : '영상 R2'} 연결 끊김 ${hms(new Date(a))}~${b >= Date.now() - 1000 ? '지금' : hms(new Date(b))}</title></rect>`;
    }
  }
  for (const t of ticks) svg += `<g class="tick"><line x1="${x(t)}" x2="${x(t)}" y1="${lanes.audio.y - laneH / 2}" y2="${axisY}"></line><text x="${x(t)}" y="${axisY + 16}" text-anchor="middle">${fmtTick(t)}</text></g>`;
  const nowX = x(Date.now());
  if (nowX <= W - R) svg += `<g class="now"><line x1="${nowX}" x2="${nowX}" y1="${lanes.audio.y - laneH / 2 - 6}" y2="${axisY}"></line><text x="${nowX}" y="${lanes.audio.y - laneH / 2 - 10}" text-anchor="middle">지금</text></g>`;

  // 점 위치 (겹치면 위아래로 비켜 놓기)
  const pos = new Map(), buckets = new Map(), shifts = [0, -14, 14, -26, 26];
  const evs = [...S.events.values()].filter(validTimed).sort((p, q) => evTime(p) - evTime(q));
  for (const ev of evs) {
    const t = evTime(ev).getTime(); if (!inWin(t)) continue;
    const px = x(t), bk = `${ev.source}:${Math.round(px / 12)}`;
    const n = buckets.get(bk) || 0; buckets.set(bk, n + 1);
    pos.set(ev.key, { x: px, y: lanes[ev.source].y + shifts[n % shifts.length] });
  }
  // 10초 연결선 (글자는 자리가 있을 때만, 없으면 선에 마우스를 올리면 보임)
  const pairs = computePairs(), placed = [];
  for (const p of pairs) {
    const pa = pos.get(p.a.key), pv = pos.get(p.v.key); if (!pa || !pv) continue;
    const mx = (pa.x + pv.x) / 2 + 6, my = (pa.y + pv.y) / 2 + 4;
    const room = Math.hypot(pa.x - pv.x, pa.y - pv.y) > 40 && !placed.some(([qx, qy]) => Math.abs(qx - mx) < 46 && Math.abs(qy - my) < 14);
    if (room) placed.push([mx, my]);
    svg += `<g class="link"><title>음성·영상 ${p.diff}초 차</title><line x1="${pa.x}" y1="${pa.y}" x2="${pv.x}" y2="${pv.y}"></line>
      ${room ? `<text x="${mx}" y="${my}">${p.diff}초 차</text>` : ''}</g>`;
  }
  // 점
  for (const ev of evs) {
    const ps = pos.get(ev.key); if (!ps) continue;
    const r = 5 + (ev.confidence != null ? Math.max(0, Math.min(1, ev.confidence)) : 0.5) * 7;
    const tip = `${srcOf(ev.source).name} ${hms(evTime(ev))} · 신뢰도 ${ev.confidence != null ? Math.round(ev.confidence * 100) + '%' : '값 없음'} · ${LABEL_TEXT[ev.label]} · ${ev.incident_id}`;
    svg += `<g class="pt ${ev.label}" data-open="${esc(ev.key)}" tabindex="0" role="button" aria-label="${esc(tip)}">
      <title>${esc(tip)}</title>
      <circle class="dot-${srcClass(ev.source)}" cx="${ps.x}" cy="${ps.y}" r="${r}"></circle>
      <circle class="ring" cx="${ps.x}" cy="${ps.y}" r="${r + 3.5}"></circle></g>`;
  }
  if (!pos.size) svg += `<text class="tl-empty" x="${(L + W - R) / 2}" y="${(lanes.audio.y + lanes.vision.y) / 2 + 4}" text-anchor="middle">이 범위에 감지가 없습니다. 범위를 넓혀 보세요.</text>`;
  svg += '</svg>';
  wrap.innerHTML = svg;

  const shown = evs.filter((e) => pos.has(e.key));
  const broken = [...S.events.values()].filter(isBroken).length;
  const pairsIn = pairs.filter((p) => pos.has(p.a.key) && pos.has(p.v.key)).length;
  document.getElementById('tl-sum').innerHTML =
    `${hms(new Date(start)).slice(0, 5)}~${hms(new Date(Math.min(end, Date.now()))).slice(0, 5)} 사이 ` +
    `음성 <b>${shown.filter((e) => e.source === 'audio').length}</b>건, 영상 <b>${shown.filter((e) => e.source === 'vision').length}</b>건, ` +
    `10초 안 함께 감지 <b>${pairsIn}</b>쌍` + (broken ? ` (규약 오류 ${broken}건은 시각을 읽을 수 없어 제외)` : '');
  renderPairs(pairs);
}
function renderPairs(pairs = computePairs()) {
  const ol = document.getElementById('pair-list'); if (!ol) return;
  if (!pairs.length) { ol.innerHTML = '<li class="pair-empty">아직 10초 안에 함께 감지된 음성·영상 쌍이 없습니다.</li>'; return; }
  const side = (ev, cls) => `<button type="button" class="${cls}" data-open="${esc(ev.key)}">
    <b>${srcOf(ev.source).icon} ${srcOf(ev.source).name} ${hms(evTime(ev))}</b>
    <small>${esc(ev.incident_id)} · ${ev.confidence != null ? Math.round(ev.confidence * 100) + '%' : '값 없음'} · ${LABEL_TEXT[ev.label]}</small></button>`;
  ol.innerHTML = pairs.slice(0, 30).map((p) =>
    `<li class="pair">${side(p.a, 'p-a')}<span class="diff">${p.diff}초 차</span>${side(p.v, 'p-v')}</li>`).join('');
}
document.addEventListener('keydown', (e) => {
  const o = e.target.closest && e.target.closest('g[data-open]');
  if (o && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openSheet(o.dataset.open); }
});
window.addEventListener('resize', () => renderTimeline());
setInterval(() => { if (S.range !== 'all') renderTimeline(); }, 5000);   // 시간이 흐르면 창을 따라 이동


/* ── 기록·통계 (6단계) ──
   모두 받은 JSON 4필드 + 받은 시각 + 보호자 표시에서만 계산한다 */
S.srange = 'today';
const charts = {};
function setStatsRange(r) {
  S.srange = r;
  document.querySelectorAll('[data-srange]').forEach((x) => x.classList.toggle('on', x.dataset.srange === r));
  renderStats();
}
function statsEvents() {
  const all = [...S.events.values()];
  if (S.srange === 'all') return all;
  return all.filter((e) => isToday(evTime(e)));
}
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
function sourceStats(evs, src) {
  const raw = evs.filter((e) => e.source === src);
  const ok = raw.filter((e) => !isBroken(e));
  const conf = (f) => avg(ok.filter(f).map((e) => e.confidence).filter((c) => c != null));
  const confirmed = ok.filter((e) => e.label === 'confirmed').length, falseA = ok.filter((e) => e.label === 'false_alarm').length;
  const delays = ok.map((e) => e.delay_sec).filter((d) => d != null);
  return {
    n: ok.length, confirmed, falseA, pending: ok.filter((e) => e.label === 'none').length,
    precision: confirmed + falseA ? confirmed / (confirmed + falseA) : null,
    confAll: conf(() => true), confOk: conf((e) => e.label === 'confirmed'), confFalse: conf((e) => e.label === 'false_alarm'),
    delayAvg: avg(delays), delayMax: delays.length ? Math.max(...delays) : null,
    late: ok.filter((e) => e.delay_sec != null && e.delay_sec >= 60).length,
    dups: raw.reduce((a, e) => a + (e.dup_count || 0), 0),
    sameSec: ok.filter((e) => e.same_second).length,
    resp: avg(ok.map((e) => e.response_sec).filter((x) => x != null)),
    cOk: raw.filter((e) => e.contract && e.contract.level === 'ok').length,
    cWarn: raw.filter((e) => e.contract && e.contract.level === 'warn').length,
    cErr: raw.filter(isBroken).length,
  };
}
const pctTxt = (x) => (x == null ? '-' : Math.round(x * 100) + '%');
const secTxt = (x) => (x == null ? '-' : (Math.round(x * 10) / 10) + '초');
function renderStats() {
  const tab = document.getElementById('tab-stats');
  if (!tab || !tab.classList.contains('on')) return;
  const evs = statsEvents();
  const A = sourceStats(evs, 'audio'), V = sourceStats(evs, 'vision');
  const keys = new Set(evs.map((e) => e.key));
  const pairs = computePairs().filter((p) => keys.has(p.a.key) && keys.has(p.v.key));
  const row = (label, f, cls = '') => `<tr class="${cls}"><th scope="row">${label}</th><td>${f(A)}</td><td>${f(V)}</td></tr>`;
  const group = (label) => `<tr class="group"><th colspan="3">${label}</th></tr>`;
  document.getElementById('cmp-table').innerHTML = `
    <thead><tr><th></th><th class="audio">🔊 음성 R1</th><th class="vision">📹 영상 R2</th></tr></thead>
    <tbody>
      ${group('감지와 보호자 표시')}
      ${row('감지 수 (규약 오류 제외)', (s) => `<b>${s.n}</b>`, 'key')}
      ${row('확인됨 / 오경보 / 확인 전', (s) => `${s.confirmed} / ${s.falseA} / ${s.pending}`)}
      ${row('실측 정밀도', (s) => `<b>${pctTxt(s.precision)}</b><small>${s.confirmed + s.falseA ? `확인됨 ${s.confirmed} / 표시 ${s.confirmed + s.falseA}건` : '표시한 감지 없음'}</small>`, 'key')}
      ${row('보호자 평균 반응 시간', (s) => secTxt(s.resp))}
      ${group('신뢰도 (confidence)')}
      ${row('평균 신뢰도', (s) => pctTxt(s.confAll))}
      ${row('확인됨의 평균 신뢰도', (s) => pctTxt(s.confOk))}
      ${row('오경보의 평균 신뢰도', (s) => pctTxt(s.confFalse))}
      ${group('전달 (occurred_at → 받은 시각)')}
      ${row('평균 / 최대 발생→도착 시간', (s) => `${secTxt(s.delayAvg)} / ${secTxt(s.delayMax)}`)}
      ${row('늦게 도착 (60초 이상)', (s) => `${s.late}건`)}
      ${row('중복 수신 (QoS 1)', (s) => `${s.dups}회`)}
      ${row('같은 초 연속 감지 (_2 등)', (s) => `${s.sameSec}건`)}
      ${group('팀 JSON 규약')}
      ${row('준수 / 경고 / 오류', (s) => `${s.cOk} / ${s.cWarn} / ${s.cErr}`)}
      <tr class="group"><th colspan="3">10초 안 함께 감지: ${pairs.length}쌍</th></tr>
    </tbody>`;
  const times = evs.filter(validTimed).map((e) => evTime(e).getTime());
  document.getElementById('st-sum').innerHTML = times.length
    ? `${S.srange === 'today' ? '오늘' : '전체 기록'} ${hms(new Date(Math.min(...times))).slice(0, 5)}~${hms(new Date(Math.max(...times))).slice(0, 5)} 사이 감지 <b>${A.n + V.n}</b>건 · 모드: ${S.info.mode === 'live' ? '실시간' : '데모'}`
    : '이 기간에 받은 감지가 없습니다.';
  drawCharts(evs);
}
function drawCharts(evs) {
  if (!window.Chart) return;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = cssVar('--ink-2');
  Chart.defaults.borderColor = cssVar('--line');
  const col = { audio: cssVar('--audio'), vision: cssVar('--vision'), ok: cssVar('--ok'), fa: cssVar('--false'), none: cssVar('--unk') };
  // 시간대별: 기간 폭에 맞춰 1분/10분/1시간 묶음
  const ok = evs.filter(validTimed);
  const ts = ok.map((e) => evTime(e).getTime());
  let labels = [], aData = [], vData = [];
  if (ts.length) {
    const span = Math.max(...ts) - Math.min(...ts);
    const bucket = span <= 15 * 60e3 ? 60e3 : span <= 3 * 3600e3 ? 600e3 : 3600e3;
    const b0 = Math.floor((Math.min(...ts) + KST_OFFSET) / bucket) * bucket - KST_OFFSET;
    const nB = Math.floor((Math.max(...ts) - b0) / bucket) + 1;
    labels = Array.from({ length: nB }, (_, i) => hms(new Date(b0 + i * bucket)).slice(0, 5));
    aData = new Array(nB).fill(0); vData = new Array(nB).fill(0);
    for (const e of ok) { const i = Math.floor((evTime(e).getTime() - b0) / bucket); (e.source === 'audio' ? aData : vData)[i]++; }
  }
  upsertChart('ch-time', {
    type: 'bar',
    data: { labels, datasets: [
      { label: '음성 R1', data: aData, backgroundColor: col.audio },
      { label: '영상 R2', data: vData, backgroundColor: col.vision }] },
    options: { maintainAspectRatio: false, animation: false, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } } },
  });
  const bins = ['50% 미만', '50~60%', '60~70%', '70~80%', '80~90%', '90% 이상'];
  const binOf = (c) => (c < 0.5 ? 0 : Math.min(5, Math.floor((c - 0.5) / 0.1) + 1));
  for (const src of ['audio', 'vision']) {
    const sets = { confirmed: new Array(6).fill(0), false_alarm: new Array(6).fill(0), none: new Array(6).fill(0) };
    for (const e of ok) if (e.source === src && e.confidence != null) sets[e.label][binOf(e.confidence)]++;
    upsertChart('ch-conf-' + src, {
      type: 'bar',
      data: { labels: bins, datasets: [
        { label: '확인됨', data: sets.confirmed, backgroundColor: col.ok },
        { label: '오경보', data: sets.false_alarm, backgroundColor: col.fa },
        { label: '확인 전', data: sets.none, backgroundColor: col.none }] },
      options: { maintainAspectRatio: false, animation: false, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } } },
    });
  }
}
function upsertChart(id, cfg) {
  const el = document.getElementById(id); if (!el) return;
  if (charts[id]) { charts[id].data = cfg.data; charts[id].update('none'); return; }
  charts[id] = new Chart(el, cfg);
}
function summaryText() {
  const evs = statsEvents();
  const A = sourceStats(evs, 'audio'), V = sourceStats(evs, 'vision');
  const keys = new Set(evs.map((e) => e.key));
  const pairs = computePairs().filter((p) => keys.has(p.a.key) && keys.has(p.v.key)).length;
  const line = (name, s) => `${name}: 감지 ${s.n}건 (확인됨 ${s.confirmed}, 오경보 ${s.falseA}, 확인 전 ${s.pending}), 실측 정밀도 ${pctTxt(s.precision)}` +
    `${s.confirmed + s.falseA ? ` (${s.confirmed}/${s.confirmed + s.falseA})` : ''}, 평균 신뢰도 ${pctTxt(s.confAll)}, 평균 발생→도착 ${secTxt(s.delayAvg)}, 중복 수신 ${s.dups}회, 규약 오류 ${s.cErr}건`;
  return [`[독거노인 안심 모니터 기록 요약] ${KST_DATE.format(new Date())} · ${S.srange === 'today' ? '오늘' : '전체 기록'} · ${S.info.mode === 'live' ? '실시간' : '데모'} 모드`,
    line('음성 R1', A), line('영상 R2', V), `10초 안 함께 감지: ${pairs}쌍`].join('\n');
}
async function copySummary() {
  const text = summaryText(), msg = document.getElementById('copy-msg');
  try { await navigator.clipboard.writeText(text); }
  catch {   // 휴대폰(http 주소)에서는 clipboard API가 막혀 있어 예전 방식 사용
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  msg.textContent = '복사했습니다';
  setTimeout(() => { msg.textContent = ''; }, 2500);
}


/* ── 시스템 탭 (7단계): 접속 정보 + 기기 연결 기록 + 통신 로그 ──
   김우석 답변 8·9·10번(발행 횟수, 시계, 성능)을 받은 기록만으로 확인한다. 파이에는 아무것도 보내지 않는다. */
const GROUP_SEC = 10;   // 같은 기기에서 10초 안에 이어진 감지는 '한 상황'으로 묶어 본다 (표시용)
function uptime(device) {
  const startT = toDate(S.history[0] && S.history[0].at) || new Date();
  const hs = S.history.filter((h) => h.device === device).map((h) => ({ v: h.value, t: toDate(h.at) })).filter((h) => h.t);
  let on = 0, last = null, offs = 0;
  for (const h of hs) {
    if (last && (last.v === 'online' || last.v === 'connected')) on += h.t - last.t;
    if (h.v === 'offline' || h.v === 'disconnected') offs++;
    last = h;
  }
  if (last && (last.v === 'online' || last.v === 'connected')) on += Date.now() - last.t;
  const total = Date.now() - startT;
  return { pct: total > 0 ? on / total : null, offs, last: hs.length ? hs[hs.length - 1] : null };
}
function bursts(evs) {
  const out = []; let cur = [];
  for (const e of evs) {
    if (cur.length && (evTime(e) - evTime(cur[cur.length - 1])) / 1000 > GROUP_SEC) { out.push(cur); cur = []; }
    cur.push(e);
  }
  if (cur.length) out.push(cur);
  return out;
}
const median = (xs) => { if (!xs.length) return null; const a = [...xs].sort((x, y) => x - y); const m = Math.floor(a.length / 2); return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
function logStats(src) {
  const raw = [...S.events.values()].filter((e) => e.source === src);
  const ok = raw.filter(validTimed).sort((a, b) => evTime(a) - evTime(b));
  const bs = bursts(ok);
  const recv = raw.map((e) => toDate(e.received_at)).filter(Boolean).sort((a, b) => a - b);
  const gaps = recv.slice(1).map((t, i) => (t - recv[i]) / 1000);
  const delays = ok.map((e) => e.delay_sec).filter((d) => d != null);
  const clipKeys = raw.map((e) => e.key).filter((k) => S.clipResults && k in S.clipResults);
  return {
    n: raw.length, bursts: bs.length, maxBurst: bs.reduce((m, b) => Math.max(m, b.length), 0),
    perBurst: bs.length ? ok.length / bs.length : null,
    lastRecv: recv.length ? recv[recv.length - 1] : null, gapMed: median(gaps),
    dMin: delays.length ? Math.min(...delays) : null, dMed: median(delays), dMax: delays.length ? Math.max(...delays) : null,
    contractOk: raw.filter((e) => e.contract && e.contract.level === 'ok').length,
    dups: raw.reduce((a, e) => a + (e.dup_count || 0), 0),
    clipTried: clipKeys.length, clipOk: clipKeys.filter((k) => S.clipResults[k]).length,
  };
}
function renderSystem() {
  const tab = document.getElementById('tab-system');
  if (!tab || !tab.classList.contains('on')) return;
  const host = (S.info.broker || '').split(':')[0];
  $('#sys-r1').textContent = S.info.mode === 'live' ? host : '데모 모드 (R1 없음)';
  $('#sys-r2cmd').textContent = S.info.mode === 'live' ? `MQTT_HOST=${host}\nMQTT_USERNAME=vision` : '실시간 모드에서 표시됩니다';
  $('#sys-url').textContent = S.info.lan_url || '-';
  const qr = $('#sys-qr'); if (!qr.getAttribute('src')) qr.setAttribute('src', '/api/qr.svg');

  const name = { broker: '브로커 (R1 Mosquitto)', audio: '🔊 음성 R1', vision: '📹 영상 R2' };
  $('#sys-dev').innerHTML = `<thead><tr><th></th><th>지금</th><th>가동률</th><th>끊김 횟수</th><th>마지막 변화</th></tr></thead><tbody>` +
    ['broker', 'audio', 'vision'].map((d) => {
      const u = uptime(d);
      const now = d === 'broker' ? (S.broker.connected ? 'connected' : 'disconnected') : deviceValue(d);
      return `<tr><th scope="row">${name[d]}</th><td><b>${STATUS_TEXT[now] || now}</b></td><td>${pctTxt(u.pct)}</td><td>${u.offs}회</td>` +
        `<td>${u.last ? `${STATUS_TEXT[u.last.v] || u.last.v} ${hms(u.last.t)}` : '-'}</td></tr>`;
    }).join('') + '</tbody>';
  $('#sys-hist').innerHTML = [...S.history].reverse().slice(0, 80).map((h) =>
    `<li>${hms(toDate(h.at))} ${name[h.device] || esc(h.device)} → <b class="${esc(h.value)}">${STATUS_TEXT[h.value] || esc(h.value)}</b>${h.retained ? ' (접속 시 받은 마지막 상태)' : ''}</li>`).join('') || '<li>기록 없음</li>';

  const A = logStats('audio'), V = logStats('vision');
  const row = (label, f) => `<tr><th scope="row">${label}</th><td>${f(A)}</td><td>${f(V)}</td></tr>`;
  const n1 = (x) => (x == null ? '-' : Math.round(x * 10) / 10);
  $('#sys-log').innerHTML = `<thead><tr><th></th><th class="audio">🔊 음성 R1</th><th class="vision">📹 영상 R2</th></tr></thead><tbody>
    ${row('받은 메시지', (s) => `<b>${s.n}</b>건`)}
    ${row('마지막 수신', (s) => (s.lastRecv ? `${hms(s.lastRecv)} (${ago(s.lastRecv)})` : '-'))}
    ${row('수신 간격 (중앙값)', (s) => (s.gapMed == null ? '-' : n1(s.gapMed) + '초'))}
    <tr class="group"><th colspan="3">8. 한 상황에 JSON 몇 건 (10초 안에 이어진 감지를 한 묶음으로)</th></tr>
    ${row('묶음 수 / 묶음당 평균 / 최대', (s) => (s.bursts ? `${s.bursts} / ${n1(s.perBurst)} / ${s.maxBurst}건` : '-'))}
    <tr class="group"><th colspan="3">9. 시계 확인 (발생→도착 시간: 최소 / 중앙 / 최대)</th></tr>
    ${row('발생→도착', (s) => (s.dMin == null ? '-' : `${n1(s.dMin)} / ${n1(s.dMed)} / ${n1(s.dMax)}초`))}
    <tr class="group"><th colspan="3">10. 통신 품질</th></tr>
    ${row('규약 준수', (s) => (s.n ? `${s.contractOk} / ${s.n}` : '-'))}
    ${row('중복 수신 (QoS 1)', (s) => s.dups + '회')}
    ${row('클립 접근 (열어 본 것 기준)', (s) => (s.clipTried ? `${s.clipOk} / ${s.clipTried} 성공` : '아직 열어 보지 않음'))}
  </tbody>`;

  // 자동 점검 문장
  const checks = [];
  const add = (lv, text) => checks.push(`<li class="${lv}">${text}</li>`);
  if (S.info.mode === 'live') add(S.broker.connected ? 'ok' : 'bad', S.broker.connected ? `R1 브로커(${esc(host)})에 연결되어 있습니다.` : 'R1 브로커와 연결이 끊겼습니다. R1 전원·핫스팟을 확인하세요.');
  for (const [src, s, label] of [['audio', A, '음성 R1'], ['vision', V, '영상 R2']]) {
    const v = deviceValue(src);
    if (v !== 'online') add(v === 'offline' ? 'bad' : 'warn', `${label} 상태가 ${STATUS_TEXT[v] || v}입니다.`);
    if (s.dMin != null && s.dMin < -1) add('warn', `${label}: 발생 시각이 받은 시각보다 ${n1(-s.dMin)}초 늦습니다. 이 파이의 시계가 노트북보다 빠를 수 있습니다 (timedatectl 확인).`);
    else if (src === 'vision' && s.dMed != null && s.dMed > 5) add('warn', `${label}: 발생→도착 중앙값이 ${n1(s.dMed)}초입니다. 파이 시계가 느리거나 전달이 늦을 수 있습니다.`);
    if (s.n && s.contractOk < s.n) add('warn', `${label}: 규약 검사를 통과하지 못한 메시지가 ${s.n - s.contractOk}건 있습니다 (기록·상세에서 확인).`);
    if (s.clipTried && s.clipOk < s.clipTried) add('warn', `${label}: 열지 못한 클립이 ${s.clipTried - s.clipOk}건 있습니다 (clip_url 서버·주소 확인).`);
  }
  if (A.n && A.dMin != null && A.dMin >= 0) add('ok', '음성 R1: 발생→도착 시간이 모두 0초 이상입니다. 음성은 말하는 시간 + 말 끝 1초 + 클립 저장이 포함되어 10초 안팎이 정상입니다.');
  $('#sys-checks').innerHTML = checks.join('') || '<li>아직 점검할 기록이 없습니다.</li>';
}
async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch { const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
  const old = btn.textContent; btn.textContent = '복사했습니다'; setTimeout(() => { btn.textContent = old; }, 2000);
}
async function newSession() {
  if (!confirm('지금까지의 기록을 보관 폴더로 옮기고 화면을 비울까요?\n(기록은 지워지지 않고 data/archive에 남습니다)')) return;
  const r = await (await fetch('/api/session/new', { method: 'POST' })).json();
  S.clipResults = {};
  $('#sess-msg').textContent = '보관했습니다: ' + r.archived_to;
  setTimeout(() => { $('#sess-msg').textContent = ''; }, 6000);
}
setInterval(() => renderSystem(), 10000);

/* 상대 시각("3분 전") 주기 갱신 */
setInterval(() => document.querySelectorAll('.ago[data-t]').forEach((el) => { el.textContent = ago(toDate(el.dataset.t)); }), 15000);

setupPrefs();
loadState().then(connectStream);
