/* =====================================================================
 * Gridiron Leaders — client application
 * Fetches data/stats.json (built by fetch_stats.py) and renders:
 *   - League leaderboards (season-to-date or single week, traditional/advanced)
 *   - Game Matchup Hub with drill-down modal
 *   - Instant search across players & teams
 * ===================================================================== */
(() => {
  'use strict';

  const state = {
    data: null,
    season: null,
    week: 'season',
    view: 'leaders',
    group: 'all',
    mode: 'traditional',
    q: '',
  };

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const app = $('#app');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const GROUP_LABEL = { offense: 'Offense', defense: 'Defense', special: 'Special Teams', team: 'Team Rankings' };
  const CAT_ICON = { passing: '🎯', rushing: '💨', receiving: '🙌', oline: '🧱', dline: '⚡', lb: '🛡️', secondary: '🔒', special: '🦶', team: '🏟️' };
  const POST_WEEK = { 19: 'Wild Card', 20: 'Divisional', 21: 'Conference', 22: 'Super Bowl' };

  /* ---------------------------------------------------------------- helpers */
  const D = () => state.data;
  const seasonBlock = () => D().seasons[String(state.season)] || { weeks: [], leaders: {}, games: [] };

  function team(abbr) {
    return D().teams[abbr] || { name: abbr || 'Free Agent', primary: '#475569', secondary: '#94a3b8', logo: 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png' };
  }

  /** Very dark primaries (e.g. HOU navy) disappear on the dark UI — fall back to secondary. */
  function accent(abbr) {
    const t = team(abbr);
    const lum = (hex) => {
      const n = parseInt(hex.slice(1), 16);
      const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    return lum(t.primary) < 0.12 && lum(t.secondary) > lum(t.primary) ? t.secondary : t.primary;
  }

  function entity(pid, teamAbbr) {
    if (String(pid).startsWith('T:')) {
      const t = team(teamAbbr);
      return { isTeam: true, name: t.name, pos: 'TEAM', team: teamAbbr, status: [] };
    }
    const p = D().players[pid] || { n: 'Unknown', pos: '', s: [] };
    return { isTeam: false, name: p.n, pos: p.pos, team: teamAbbr || p.t, head: p.h, status: p.s || [] };
  }

  function weekLabel(w) {
    if (w === 'season') return 'Season to Date';
    return state.season >= 2021 && POST_WEEK[w] ? POST_WEEK[w] : state.season < 2021 && w > 17 ? (POST_WEEK[w + 1] || `Week ${w}`) : `Week ${w}`;
  }

  function fmt(v, f) {
    if (v === null || v === undefined) return '—';
    switch (f) {
      case 'pct': return `${v.toFixed(1)}%`;
      case 'dec1': return v.toFixed(1);
      case 'dec2': return `${v > 0 ? '+' : ''}${v.toFixed(2)}`;
      case 'signed': return `${v > 0 ? '+' : ''}${v}`;
      case 'time': return `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, '0')}`;
      default: return Number(v).toLocaleString();
    }
  }

  function matches(ent) {
    const q = state.q;
    if (!q) return true;
    const t = team(ent.team);
    return ent.name.toLowerCase().includes(q) || (ent.team || '').toLowerCase() === q || t.name.toLowerCase().includes(q);
  }

  function avatar(ent, size = '') {
    const t = team(ent.team);
    const head = !ent.isTeam && ent.head
      ? `<img class="head" src="${esc(ent.head)}" alt="${esc(ent.name)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`
      : '';
    return `<div class="avatar ${size} ${ent.isTeam ? 'team' : ''}" style="--ring:${accent(ent.team)}">
      <img class="logo" src="${esc(t.logo)}" alt="${ent.isTeam ? esc(t.name) : ''}" loading="lazy" onerror="this.style.display='none'">${head}</div>`;
  }

  function badges(status) {
    return (status || []).filter((s) => s !== 'Active').map((s) => {
      const cls = s.startsWith('IR') ? 'IR' : s;
      return `<span class="badge badge-${cls}" title="${esc(s)}">${esc(s === 'IR / Injured' ? 'IR' : s)}</span>`;
    }).join(' ');
  }

  /* ---------------------------------------------------------------- controls */
  function populateSeasons() {
    const sel = $('#season-select');
    sel.innerHTML = D().meta.seasons.map((s) => `<option value="${s}">${s} Season</option>`).join('');
    sel.value = state.season;
  }

  function populateWeeks() {
    const sel = $('#week-select');
    const weeks = seasonBlock().weeks;
    const opts = state.view === 'leaders' ? [`<option value="season">Season to Date</option>`] : [];
    opts.push(...weeks.slice().reverse().map((w) => `<option value="${w}">${weekLabel(w)}</option>`));
    sel.innerHTML = opts.join('');
    if (state.view === 'games' && (state.week === 'season' || !weeks.includes(+state.week))) state.week = String(weeks[weeks.length - 1] ?? '');
    if (state.view === 'leaders' && state.week !== 'season' && !weeks.includes(+state.week)) state.week = 'season';
    sel.value = state.week;
  }

  function syncChips() {
    $$('[data-view]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === state.view)));
    $$('[data-group]').forEach((b) => b.classList.toggle('active', b.dataset.group === state.group));
    $$('[data-mode]').forEach((b) => b.classList.toggle('active', b.dataset.mode === state.mode));
    $('#leader-subnav').classList.toggle('hidden', state.view !== 'leaders');
  }

  function bind() {
    $('#season-select').addEventListener('change', (e) => { state.season = +e.target.value; populateWeeks(); render(); });
    $('#week-select').addEventListener('change', (e) => { state.week = e.target.value; render(); });
    $$('[data-view]').forEach((b) => b.addEventListener('click', () => { state.view = b.dataset.view; populateWeeks(); render(); }));
    $$('[data-group]').forEach((b) => b.addEventListener('click', () => { state.group = b.dataset.group; render(); }));
    $$('[data-mode]').forEach((b) => b.addEventListener('click', () => { state.mode = b.dataset.mode; render(); }));
    let t;
    $('#search-input').addEventListener('input', (e) => {
      clearTimeout(t);
      t = setTimeout(() => { state.q = e.target.value.trim().toLowerCase(); render(); }, 80);
    });
    $('#modal-backdrop').addEventListener('click', closeModal);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeModal();
      if (e.key === '/' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); $('#search-input').focus(); }
    });
    app.addEventListener('click', (e) => {
      const g = e.target.closest('[data-game]');
      if (g) openModal(g.dataset.game);
    });
    app.addEventListener('keydown', (e) => {
      const g = e.target.closest('[data-game]');
      if (g && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openModal(g.dataset.game); }
    });
  }

  /* ---------------------------------------------------------------- leaderboards */
  function leaderCard(m, entries, idx) {
    const ents = entries.map(([pid, tm, v]) => ({ ...entity(pid, tm), v }));
    const anyHit = state.q && ents.some(matches);
    if (state.q && !anyHit) return '';
    const lead = ents[0];
    const col = accent(lead.team);
    const valCls = m.neg ? 'text-ember' : 'text-white';
    const rowCls = (e) => (state.q ? (matches(e) ? 'hit' : 'dim') : '');

    const rest = ents.slice(1).map((e, i) => `
      <li class="row ${rowCls(e)} flex items-center gap-3 px-4 py-2">
        <span class="w-5 font-display text-xl text-slate-500">${i + 2}</span>
        ${avatar(e, 'sm')}
        <div class="min-w-0 flex-1">
          <p class="flex items-center gap-1 text-sm font-semibold"><span class="truncate">${esc(e.name)}</span>${badges(e.status)}</p>
          <p class="text-[10px] uppercase tracking-widest text-slate-500">${esc(e.team)}${e.isTeam ? '' : ` · ${esc(e.pos)}`}</p>
        </div>
        <span class="font-mono font-bold text-sm tabular-nums ${m.neg ? 'text-ember/90' : 'text-slate-200'}">${fmt(e.v, m.fmt)}</span>
      </li>`).join('');

    return `
      <article class="card glass rounded-xl" style="--team:${col}; animation-delay:${Math.min(idx * 30, 400)}ms" id="card-${m.key.replace('.', '-')}">
        <div class="glow"></div>
        <header class="flex items-center justify-between px-4 pt-4">
          <h3 class="font-display text-xl leading-none uppercase tracking-wider text-slate-300">${esc(m.label)}</h3>
          <div class="flex gap-1">
            ${m.adv ? '<span class="badge bg-violet-500/80 text-white">ADV</span>' : ''}
            ${m.neg ? '<span class="badge bg-ember/80 text-white">NEG</span>' : ''}
          </div>
        </header>
        <div class="row ${rowCls(lead)} relative flex items-center gap-4 px-4 py-4">
          <span class="absolute left-2 top-1 font-display text-6xl leading-none text-white/5 select-none">1</span>
          ${avatar(lead, 'lg')}
          <div class="min-w-0 flex-1">
            <p class="truncate font-black uppercase tracking-wide">${esc(lead.name)}</p>
            <p class="text-[10px] uppercase tracking-[.2em] text-slate-400">${esc(team(lead.team).name)}${lead.isTeam ? '' : ` · ${esc(lead.pos)}`}</p>
            <div class="mt-1 flex gap-1">${badges(lead.status)}</div>
          </div>
          <span class="font-mono text-3xl font-extrabold tabular-nums ${valCls}" style="text-shadow:0 0 22px ${col}">${fmt(lead.v, m.fmt)}</span>
        </div>
        <ol class="border-t border-slate-700/50 py-1">${rest}</ol>
      </article>`;
  }

  function renderLeaders() {
    const meta = D().meta;
    const boards = seasonBlock().leaders[state.week] || {};
    const cats = meta.categories.filter((c) => state.group === 'all' || c.group === state.group);
    let idx = 0;
    let html = '';
    let lastGroup = null;

    for (const cat of cats) {
      const metrics = meta.metrics.filter((m) => m.cat === cat.id && (state.mode === 'advanced' ? m.adv : !m.adv));
      const cards = metrics.map((m) => (boards[m.key]?.length ? leaderCard(m, boards[m.key], idx++) : '')).filter(Boolean);
      if (!cards.length) continue;
      if (state.group === 'all' && cat.group !== lastGroup) {
        html += `<p class="mt-10 first:mt-0 mb-2 font-display text-5xl uppercase tracking-wider text-white/10 leading-none">${GROUP_LABEL[cat.group]}</p>`;
        lastGroup = cat.group;
      }
      html += `
        <section class="mb-10" aria-labelledby="sec-${cat.id}">
          <h2 id="sec-${cat.id}" class="section-title flex items-center font-display text-3xl font-bold uppercase tracking-wider mb-4">
            <span class="mr-2">${CAT_ICON[cat.id] || ''}</span>${esc(cat.label)}
          </h2>
          <div class="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">${cards.join('')}</div>
        </section>`;
    }

    app.innerHTML = `
      <div class="flex flex-wrap items-end justify-between gap-2 mb-6">
        <div>
          <p class="text-xs uppercase tracking-[.3em] text-volt font-bold">${state.season} · ${esc(weekLabel(state.week === 'season' ? 'season' : +state.week))}</p>
          <h2 class="font-display text-5xl uppercase tracking-wider font-bold leading-none">${state.mode === 'advanced' ? 'Advanced Metrics' : 'League Leaders'}</h2>
        </div>
        ${state.q ? `<p class="text-sm text-slate-400">Filtering for “<span class="text-volt">${esc(state.q)}</span>”</p>` : ''}
      </div>
      ${html || empty(state.q ? 'No leaderboard entries match your search.' : 'No leaderboard data for this selection yet.')}`;
  }

  /* ---------------------------------------------------------------- game hub */
  function gameCard(g, i) {
    const [h, a] = [team(g.home), team(g.away)];
    const side = (abbr, t, score, rec, won) => `
      <div class="flex items-center gap-3 ${won ? '' : 'opacity-60'}">
        <img src="${esc(t.logo)}" alt="${esc(t.name)}" class="w-10 h-10 object-contain drop-shadow" loading="lazy">
        <div class="flex-1 min-w-0">
          <p class="font-display text-2xl leading-none uppercase tracking-wide">${esc(abbr)}</p>
          <p class="text-[10px] font-mono text-slate-400">${esc(rec)}</p>
        </div>
        <span class="font-mono text-3xl font-extrabold tabular-nums ${won ? 'text-white' : 'text-slate-400'}">${score}</span>
        ${won ? '<span class="text-volt">◀</span>' : '<span class="w-3"></span>'}
      </div>`;
    return `
      <article data-game="${esc(g.id)}" tabindex="0" role="button" aria-label="${esc(a.name)} at ${esc(h.name)}"
        class="game-card card glass rounded-xl p-4 cursor-pointer focus:outline-none focus:ring-2 focus:ring-volt"
        style="--team:${accent(g.hs >= g.as ? g.home : g.away)}; animation-delay:${Math.min(i * 40, 400)}ms">
        <div class="glow"></div>
        <div class="flex items-center justify-between mb-3">
          <span class="final-badge slant px-3 py-0.5 text-[11px] font-black tracking-widest">FINAL${g.ot ? '/OT' : ''}</span>
          <span class="text-[10px] font-mono text-slate-500">${esc(g.date)}</span>
        </div>
        <div class="space-y-2">
          ${side(g.away, a, g.as, g.arec, g.as > g.hs)}
          ${side(g.home, h, g.hs, g.hrec, g.hs > g.as)}
        </div>
        <p class="mt-3 text-[10px] uppercase tracking-widest text-slate-500 truncate">${esc(g.stadium || '')}</p>
      </article>`;
  }

  function renderGames() {
    const wk = +state.week;
    const games = seasonBlock().games.filter((g) => g.week === wk)
      .filter((g) => !state.q || [g.home, g.away].some((t) => t.toLowerCase() === state.q || team(t).name.toLowerCase().includes(state.q)));
    app.innerHTML = `
      <div class="mb-6">
        <p class="text-xs uppercase tracking-[.3em] text-volt font-bold">${state.season} · ${esc(weekLabel(wk))}</p>
        <h2 class="font-display text-5xl uppercase tracking-wider font-bold leading-none">Game Matchup Hub</h2>
      </div>
      ${games.length ? `<div class="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">${games.map(gameCard).join('')}</div>`
        : empty(state.q ? 'No games match that team.' : 'No completed games for this week.')}`;
  }

  /* ---------------------------------------------------------------- modal */
  function openModal(id) {
    const g = seasonBlock().games.find((x) => x.id === id);
    if (!g) return;
    const [h, a] = [team(g.home), team(g.away)];
    const [hc, ac] = [accent(g.home), accent(g.away)];
    const n = Math.max(4, g.q.home.length);
    const qHead = Array.from({ length: n }, (_, i) => `<th class="px-3 py-2">${i < 4 ? i + 1 : i === 4 ? 'OT' : `OT${i - 3}`}</th>`).join('');
    const qRow = (abbr, arr, total, won) => `
      <tr class="border-t border-slate-700/50">
        <td class="px-3 py-2 text-left font-display text-xl tracking-wide">${esc(abbr)}</td>
        ${Array.from({ length: n }, (_, i) => `<td class="px-3 py-2">${arr[i] ?? '—'}</td>`).join('')}
        <td class="px-3 py-2 font-extrabold ${won ? 'text-volt' : ''}">${total}</td>
      </tr>`;

    const statDefs = [
      ['Total Yards', 'yds'], ['Passing', 'pass'], ['Rushing', 'rush'], ['First Downs', 'fd'],
      ['3rd Down', 'third'], ['Turnovers', 'to'], ['Penalties', 'pen'], ['Possession', 'top'], ['EPA / Play', 'epa'],
    ];
    const sv = (s, k) => (k === 'top' ? fmt(s[k], 'time') : k === 'epa' ? fmt(s[k], 'dec2') : s[k] ?? '—');
    const num = (v) => (typeof v === 'number' ? v : parseFloat(String(v)) || 0);
    const stats = statDefs.map(([label, k]) => {
      const av = g.stats.away?.[k], hv = g.stats.home?.[k];
      const tot = Math.abs(num(av)) + Math.abs(num(hv)) || 1;
      const ap = typeof av === 'number' && k !== 'epa' ? (Math.abs(num(av)) / tot) * 100 : 50;
      return `
        <div class="py-2">
          <div class="flex justify-between font-mono text-sm font-bold"><span>${sv(g.stats.away || {}, k)}</span>
            <span class="text-[10px] font-sans uppercase tracking-widest text-slate-400">${label}</span><span>${sv(g.stats.home || {}, k)}</span></div>
          <div class="mt-1 flex h-1.5 rounded overflow-hidden bg-slate-800">
            <div style="width:${ap}%;background:${ac}"></div><div style="width:${100 - ap}%;background:${hc}"></div></div>
        </div>`;
    }).join('');

    const LEAD = { passing: 'Passing', rushing: 'Rushing', receiving: 'Receiving', tackles: 'Tackles', sacks: 'Sacks', turnovers: 'Takeaways' };
    const leaderCell = (l, abbr, align) => {
      if (!l || (Array.isArray(l) && l.length === 0)) return `<div class="flex-1 text-slate-600 text-sm ${align === 'right' ? 'text-right' : ''}">—</div>`;
      const items = Array.isArray(l[0]) ? l : [l];
      const rows = items.map((item) => {
        const e = entity(item[0], abbr);
        return `
          <div class="flex items-center gap-3 ${align === 'right' ? 'flex-row-reverse text-right' : ''}">
            ${avatar(e)}
            <div class="min-w-0">
              <p class="flex items-center gap-1 font-bold ${align === 'right' ? 'justify-end' : ''}"><span class="truncate">${esc(e.name)}</span>${badges(e.status)}</p>
              <p class="text-[11px] font-mono text-slate-400">${esc(item[1])}</p>
            </div>
          </div>`;
      }).join('');
      return `<div class="flex-1 min-w-0 space-y-2.5">${rows}</div>`;
    };
    const leaders = Object.entries(LEAD).map(([k, label]) => `
      <div class="flex items-center gap-3 py-3 border-t border-slate-700/40">
        ${leaderCell(g.leaders[k]?.away, g.away, 'left')}
        <span class="w-24 shrink-0 text-center font-display text-lg uppercase tracking-wider text-slate-400">${label}</span>
        ${leaderCell(g.leaders[k]?.home, g.home, 'right')}
      </div>`).join('');

    const big = (t, abbr, score, rec, won, align) => `
      <div class="flex-1 flex items-center gap-4 ${align === 'right' ? 'flex-row-reverse text-right' : ''}">
        <img src="${esc(t.logo)}" alt="${esc(t.name)}" class="w-16 h-16 sm:w-24 sm:h-24 object-contain drop-shadow-2xl">
        <div><p class="font-display text-2xl sm:text-4xl uppercase leading-none tracking-wide">${esc(t.name)}</p>
          <p class="font-mono text-xs text-slate-300">${esc(rec)}</p></div>
        <span class="mx-auto sm:mx-4 font-mono text-5xl sm:text-7xl font-extrabold tabular-nums ${won ? 'text-white' : 'text-white/50'}">${score}</span>
      </div>`;

    $('#modal-body').innerHTML = `
      <div class="relative p-5 sm:p-8" style="background:linear-gradient(100deg, ${ac}cc, #0b1622 45%, #0b1622 55%, ${hc}cc)">
        <button id="modal-close" class="absolute right-3 top-3 w-9 h-9 rounded-full bg-black/40 hover:bg-black/70 text-xl" aria-label="Close">×</button>
        <div class="text-center mb-4">
          <span class="final-badge slant px-4 py-1 text-xs font-black tracking-[.3em]">FINAL${g.ot ? ' / OT' : ''}</span>
          <p id="modal-title" class="mt-2 text-[11px] uppercase tracking-[.3em] text-slate-300">${esc(weekLabel(g.week))} · ${esc(g.date)} ${g.stadium ? '· ' + esc(g.stadium) : ''}</p>
        </div>
        <div class="flex flex-col sm:flex-row items-center gap-4">
          ${big(a, g.away, g.as, g.arec, g.as > g.hs, 'left')}
          <span class="font-display text-3xl text-white/40">@</span>
          ${big(h, g.home, g.hs, g.hrec, g.hs > g.as, 'right')}
        </div>
      </div>
      <div class="p-5 sm:p-8 grid gap-8 lg:grid-cols-5">
        <div class="lg:col-span-2 space-y-6">
          <div>
            <h3 class="font-display text-2xl uppercase tracking-wider mb-2">Scoring</h3>
            <div class="overflow-x-auto"><table class="w-full text-center font-mono text-sm">
              <thead class="text-[10px] uppercase tracking-widest text-slate-400"><tr><th class="px-3 py-2 text-left">Team</th>${qHead}<th class="px-3 py-2">T</th></tr></thead>
              <tbody>${qRow(g.away, g.q.away, g.as, g.as > g.hs)}${qRow(g.home, g.q.home, g.hs, g.hs > g.as)}</tbody>
            </table></div>
          </div>
          <div>
            <h3 class="font-display text-2xl uppercase tracking-wider mb-1">Team Stats</h3>
            <div class="flex justify-between text-xs font-black tracking-widest"><span style="color:${ac}">${esc(g.away)}</span><span style="color:${hc}">${esc(g.home)}</span></div>
            ${stats}
          </div>
        </div>
        <div class="lg:col-span-3">
          <h3 class="font-display text-2xl uppercase tracking-wider mb-1">Head-to-Head Leaders</h3>
          ${leaders}
        </div>
      </div>`;
    $('#modal-close').addEventListener('click', closeModal);
    $('#game-modal').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    $('#modal-close').focus();
  }

  function closeModal() {
    $('#game-modal').classList.add('hidden');
    document.body.style.overflow = '';
  }

  /* ---------------------------------------------------------------- shell */
  const empty = (msg) => `
    <div class="glass rounded-xl p-12 text-center">
      <p class="font-display text-4xl uppercase tracking-wider text-slate-500">No Data</p>
      <p class="text-slate-400 mt-1">${esc(msg)}</p>
    </div>`;

  function render() {
    syncChips();
    $('#week-select').value = state.week;
    state.view === 'leaders' ? renderLeaders() : renderGames();
  }

  async function init() {
    bind();
    try {
      const res = await fetch(`data/stats.json?t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
    } catch (err) {
      app.innerHTML = empty(`Could not load data/stats.json (${err.message}). Run "python fetch_stats.py" first.`);
      $('#meta-line').textContent = 'Data unavailable';
      return;
    }
    const meta = D().meta;
    state.season = meta.current_season;
    const updated = new Date(meta.generated_at);
    $('#meta-line').textContent = `Updated ${updated.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })} · Through ${weekLabel(meta.current_week)}`;
    populateSeasons();
    populateWeeks();
    render();
  }

  init();
})();
