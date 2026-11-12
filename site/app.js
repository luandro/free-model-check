/* Free Model Check, the register. Reads data/free_models.json and prints the rows. */

(function () {
  'use strict';

  var state = {
    aa: [],
    stealth: [],
    unscored: [],
    summary: {},
    section: 'aa',
    query: '',
    provider: 'all',
    sort: 'score',
    open: {}
  };

  var DATA = {
    aa: 'data/free_models.json',
    stealth: 'data/free_models_stealthmark.json',
    unscored: 'data/free_models_unbenchmarked.json'
  };

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function text(value) {
    if (value === null || value === undefined) return '';
    return String(value).trim();
  }

  function num(value) {
    return typeof value === 'number' && isFinite(value) ? value : null;
  }

  function oneDecimal(value) {
    var n = num(value);
    return n === null ? 'n/a' : n.toFixed(1);
  }

  function whole(value) {
    var n = num(value);
    return n === null ? 'n/a' : n.toLocaleString('en-GB');
  }

  function shortContext(value) {
    var n = num(value);
    if (n === null || n <= 0) return 'n/a';
    if (n >= 1000000) {
      var m = n / 1000000;
      return (Math.abs(m - Math.round(m)) < 0.05 ? String(Math.round(m)) : m.toFixed(1)) + 'M';
    }
    if (n >= 1000) return Math.round(n / 1000) + 'K';
    return String(n);
  }

  function stamp(unix) {
    var n = num(unix);
    if (n === null) return { full: 'undated', iso: '' };
    var d = new Date(n * 1000);
    if (isNaN(d.getTime())) return { full: 'undated', iso: '' };
    var pad = function (v) { return String(v).padStart(2, '0'); };
    return {
      full: d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) +
        ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ' UTC',
      iso: d.toISOString()
    };
  }

  function providerLabel(route) {
    var base = text(route && route.api_base);
    if (base) {
      var host = base;
      try {
        host = new URL(base.indexOf('//') === -1 ? 'https://' + base : base).hostname;
      } catch (err) {
        host = base;
      }
      host = host.replace(/^api\./, '').replace(/^www\./, '');
      if (host.slice(-14) === 'googleapis.com') return 'google';
      var parts = host.split('.');
      if (parts[0].slice(-4) === '-api' && parts.length > 1) parts = parts.slice(1);
      if (parts[0]) return parts[0];
    }
    return text(route && route.provider) || 'unknown';
  }

  function providerMark(route) {
    if (route && route.requires_provider_key) return '*';
    if (route && route.stale_mirror) return '†';
    return '';
  }

  function providerList(routes) {
    var seen = [];
    var out = [];
    (routes || []).forEach(function (route) {
      var label = providerLabel(route);
      if (seen.indexOf(label) === -1) {
        seen.push(label);
        out.push({ label: label, mark: providerMark(route) });
      } else if (providerMark(route)) {
        var existing = out.filter(function (p) { return p.label === label; })[0];
        if (existing && existing.mark === '') existing.mark = providerMark(route);
      }
    });
    return out;
  }

  function contextOf(record) {
    var md = record.agent_readiness && record.agent_readiness.models_dev;
    if (md && num(md.context) !== null) return num(md.context);
    if (record.capabilities && num(record.capabilities.context) !== null) {
      return num(record.capabilities.context);
    }
    return null;
  }

  function releaseOf(record) {
    var model = record.model || {};
    if (text(model.release_date)) return text(model.release_date);
    var md = record.agent_readiness && record.agent_readiness.models_dev;
    if (md && text(md.release_date)) return text(md.release_date);
    if (record.capabilities && text(record.capabilities.release_date)) {
      return text(record.capabilities.release_date);
    }
    return '';
  }

  function haystack(record) {
    var model = record.model || {};
    var quality = record.quality || {};
    var parts = [
      model.name, model.creator, model.aa_slug, model.stealthmark_id,
      model.revealed_name, model.models_dev_id, quality.tier, quality.score_source
    ];
    (record.free_routes || []).forEach(function (route) {
      parts.push(route.provider, route.route_model, route.model_name, route.source);
    });
    return parts.filter(Boolean).join(' ').toLowerCase();
  }

  function visible(record) {
    if (state.provider !== 'all') {
      var names = providerList(record.free_routes).map(function (p) { return p.label; });
      if (names.indexOf(state.provider) === -1) return false;
    }
    if (!state.query) return true;
    return haystack(record).indexOf(state.query) !== -1;
  }

  function sorter(field) {
    return function (a, b) {
      var av;
      var bv;
      if (field === 'name') {
        av = text(a.model && a.model.name).toLowerCase();
        bv = text(b.model && b.model.name).toLowerCase();
        return av < bv ? -1 : av > bv ? 1 : 0;
      }
      if (field === 'context') {
        av = contextOf(a);
        bv = contextOf(b);
        av = av === null ? -1 : av;
        bv = bv === null ? -1 : bv;
        return bv - av;
      }
      if (field === 'routes') {
        return (b.route_count || 0) - (a.route_count || 0);
      }
      if (field === 'release') {
        av = releaseOf(a);
        bv = releaseOf(b);
        return bv < av ? -1 : bv > av ? 1 : 0;
      }
      if (field === 'intelligence') {
        av = num(a.quality && a.quality.intelligence);
        bv = num(b.quality && b.quality.intelligence);
        av = av === null ? -1 : av;
        bv = bv === null ? -1 : bv;
        return bv - av;
      }
      av = num(a.quality && a.quality.capability_score);
      bv = num(b.quality && b.quality.capability_score);
      av = av === null ? -1 : av;
      bv = bv === null ? -1 : bv;
      return bv - av;
    };
  }

  function modelCell(record, href) {
    var model = record.model || {};
    var name = text(model.name) || 'Untitled model';
    var maker = text(model.creator) || text(model.stealthmark_id) || text(model.models_dev_id);
    var inner = '<span class="model-cell">' + esc(name) + '</span>';
    if (maker) inner += '<span class="model-maker">' + esc(maker) + '</span>';
    if (href) {
      return '<a class="model-link" href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + inner + '</a>';
    }
    return inner;
  }

  function providerCell(routes) {
    var list = providerList(routes);
    if (!list.length) return '<span class="model-maker">unknown</span>';
    return '<ul class="provider-set">' + list.map(function (p) {
      var mark = p.mark ? '<span class="mark" aria-hidden="true">' + esc(p.mark) + '</span>' : '';
      return '<li>' + esc(p.label) + mark + '</li>';
    }).join('') + '</ul>';
  }

  function readyCell(status) {
    if (status === true) return '<span class="ready-yes">yes</span>';
    if (status === false) return '<span class="ready-no">no</span>';
    return '<span class="ready-no">unknown</span>';
  }

  function routeRows(record) {
    var routes = record.free_routes || [];
    if (!routes.length) return '';
    return routes.map(function (route) {
      var note = [];
      if (route.verification) note.push(route.verification);
      if (route.source) note.push(route.source);
      return '<li><span class="route-id">' + esc(route.route_model) + '</span>' +
        '<span class="route-note">' + esc(note.join(', ')) + '</span></li>';
    }).join('');
  }

  function detailRow(record, key, heading) {
    var routes = record.free_routes || [];
    var open = !!state.open[key];
    var detail = '<tr class="route-row" id="detail-' + esc(key) + '"' + (open ? '' : ' hidden') + '>' +
      '<td colspan="9"><div class="route-detail">' +
      '<h4>' + esc(heading) + '</h4>' +
      '<ul class="route-list">' + routeRows(record) + '</ul>' +
      '</div></td></tr>';
    var toggle = '<button class="row-toggle" type="button" data-toggle="' + esc(key) + '" aria-expanded="' +
      (open ? 'true' : 'false') + '" aria-controls="detail-' + esc(key) + '">' +
      (open ? 'Hide routes ' : 'Show routes ') + '(' + routes.length + ')</button>';
    return { toggle: toggle, detail: detail };
  }

  function aaRow(record, index) {
    var quality = record.quality || {};
    var key = 'aa-' + (record.model && (record.model.aa_slug || record.model.name) || index);
    var extra = detailRow(record, key, 'Route ids and price proof');
    var href = record.model && record.model.aa_slug
      ? 'https://artificialanalysis.ai/models/' + encodeURIComponent(record.model.aa_slug)
      : null;
    return '<tr>' +
      '<td class="col-rank">' + (index + 1) + '</td>' +
      '<td class="col-model" data-label="Model">' + modelCell(record, href) + extra.toggle + '</td>' +
      '<td class="col-score" data-label="Capability">' + oneDecimal(quality.capability_score) + '</td>' +
      '<td class="col-score" data-label="Intel">' + oneDecimal(quality.intelligence) + '</td>' +
      '<td class="col-score" data-label="Code">' + oneDecimal(quality.coding) + '</td>' +
      '<td class="col-score" data-label="Agent">' + oneDecimal(quality.agentic) + '</td>' +
      '<td class="col-routes" data-label="Routes">' + (record.route_count || 0) + '</td>' +
      '<td class="col-ready" data-label="Agent ready">' + readyCell(record.agent_readiness && record.agent_readiness.status) + '</td>' +
      '<td class="col-provider" data-label="Served by">' + providerCell(record.free_routes) + '</td>' +
      '</tr>' + extra.detail;
  }

  function stealthRow(record, index) {
    var model = record.model || {};
    var sm = (record.quality && record.quality.stealthmark) || {};
    var key = 'sm-' + (model.stealthmark_id || index);
    var extra = detailRow(record, key, 'Route ids and price proof');
    var scenes = num(sm.results_counted) !== null && num(sm.prompts) !== null
      ? sm.results_counted + ' of ' + sm.prompts
      : 'n/a';
    return '<tr>' +
      '<td class="col-rank">' + (index + 1) + '</td>' +
      '<td class="col-model" data-label="Model">' + modelCell(record, model.page_url) + extra.toggle + '</td>' +
      '<td class="col-score" data-label="General">' + oneDecimal(sm.general) + '</td>' +
      '<td class="col-score" data-label="Visual">' + oneDecimal(sm.visual) + '</td>' +
      '<td class="col-score" data-label="Overall">' + oneDecimal(sm.overall) + '</td>' +
      '<td class="col-routes" data-label="Scenes">' + esc(scenes) + '</td>' +
      '<td class="col-provider" data-label="Served by">' + providerCell(record.free_routes) + '</td>' +
      '</tr>' + extra.detail;
  }

  function unscoredRow(record, index) {
    var model = record.model || {};
    var caps = record.capabilities || {};
    var key = 'un-' + (model.models_dev_id || index);
    var extra = detailRow(record, key, 'Route ids and price proof');
    var status = record.benchmark && record.benchmark.status === 'aa-unscored'
      ? 'AA entry, unscored'
      : 'No AA entry';
    return '<tr>' +
      '<td class="col-rank">' + (index + 1) + '</td>' +
      '<td class="col-model" data-label="Model">' + modelCell(record) + extra.toggle + '</td>' +
      '<td class="col-ready" data-label="Context">' + shortContext(caps.context) + '</td>' +
      '<td class="col-ready" data-label="Agent ready">' + readyCell(caps.agent_ready) + '</td>' +
      '<td class="col-provider" data-label="Served by">' + providerCell(record.free_routes) + '</td>' +
      '<td class="col-routes" data-label="Status">' + esc(status) + '</td>' +
      '</tr>' + extra.detail;
  }

  function paint() {
    var aa = state.aa.filter(visible).sort(sorter(state.sort));
    var sm = state.stealth.filter(visible).sort(sorter('intelligence'));
    var un = state.unscored.filter(visible).sort(sorter('context'));

    document.getElementById('rows-aa').innerHTML = aa.length
      ? aa.map(aaRow).join('')
      : '<tr><td colspan="9">' + esc(emptyLine(state.aa.length)) + '</td></tr>';
    document.getElementById('rows-stealthmark').innerHTML = sm.length
      ? sm.map(stealthRow).join('')
      : '<tr><td colspan="7">' + esc(emptyLine(state.stealth.length)) + '</td></tr>';
    document.getElementById('rows-unscored').innerHTML = un.length
      ? un.map(unscoredRow).join('')
      : '<tr><td colspan="6">' + esc(emptyLine(state.unscored.length)) + '</td></tr>';

    document.getElementById('count-aa').textContent = aa.length;
    document.getElementById('count-stealthmark').textContent = sm.length;
    document.getElementById('count-unscored').textContent = un.length;

    var shown = (state.section === 'aa' ? aa.length : 0) +
      (state.section === 'stealthmark' ? sm.length : 0) +
      (state.section === 'unscored' ? un.length : 0);
    var total = (state.section === 'aa' ? state.aa.length : 0) +
      (state.section === 'stealthmark' ? state.stealth.length : 0) +
      (state.section === 'unscored' ? state.unscored.length : 0);
    var notes = [];
    if (state.provider !== 'all') notes.push('served by ' + state.provider);
    if (state.query) notes.push('matching “' + state.query + '”');
    document.getElementById('results-status').textContent =
      shown + ' of ' + total + ' entries' + (notes.length ? ', ' + notes.join(', ') : '') + '.';
  }

  function emptyLine(count) {
    if (count === 0) return 'Nothing in this part of the register for this edition.';
    return 'No entry here matches the current filter.';
  }

  function showSection(name) {
    state.section = name;
    ['aa', 'stealthmark', 'unscored'].forEach(function (part) {
      var section = document.getElementById('section-' + part);
      var tab = document.getElementById('tab-' + part);
      var on = part === name;
      if (section) section.hidden = !on;
      if (tab) tab.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    paint();
  }

  function fillProviders() {
    var select = document.getElementById('provider-filter');
    var seen = {};
    var names = [];
    state.aa.concat(state.stealth, state.unscored).forEach(function (record) {
      providerList(record.free_routes).forEach(function (p) {
        if (!seen[p.label]) {
          seen[p.label] = true;
          names.push(p.label);
        }
      });
    });
    names.sort();
    select.innerHTML = '<option value="all">Every provider</option>' + names.map(function (n) {
      return '<option value="' + esc(n) + '">' + esc(n) + '</option>';
    }).join('');
  }

  function fillSummary(summary) {
    state.summary = summary || {};
    var generated = stamp(summary.generated_unix);
    var edition = 'Edition of ' + generated.full;
    var index = num(summary.artificial_analysis_index_version);

    document.getElementById('folio-updated').textContent = generated.full;
    document.getElementById('folio-edition').textContent = index === null
      ? 'Artificial Analysis'
      : 'AA index ' + index.toFixed(1);
    document.getElementById('foot-edition').textContent = generated.full;
    document.getElementById('foot-index').textContent = index === null ? 'n/a' : index.toFixed(1);
    document.getElementById('foot-generated').textContent = generated.full;
    document.title = 'Free Model Check, ' + edition.toLowerCase();

    document.getElementById('fig-models').textContent = whole(summary.selected_underlying_models);
    document.getElementById('fig-routes').textContent = whole(summary.free_routes_seen);
    document.getElementById('fig-floor').textContent = num(summary.intelligence_floor) === null
      ? 'n/a'
      : String(summary.intelligence_floor);
    document.getElementById('fig-catalogue').textContent = whole(summary.aa_models_considered);
  }

  function read(key, url) {
    return fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .then(function (data) {
        if (!data || !Array.isArray(data.models)) throw new Error('no models array');
        state[key] = data.models;
        return data;
      });
  }

  function load() {
    var settled = false;

    function finishOnce() {
      if (settled) return;
      settled = true;
      finish();
    }

    Promise.all([
      read('aa', DATA.aa).catch(function () { return null; }),
      read('stealth', DATA.stealth).catch(function () { return null; }),
      read('unscored', DATA.unscored).catch(function () { return null; })
    ]).then(function (parts) {
      if (parts[0] && parts[0].summary) fillSummary(parts[0].summary);
      finishOnce();
    });

    setTimeout(finishOnce, 9000);
  }

  function finish() {
    var hasStealth = state.stealth.length > 0;
    var hasUnscored = state.unscored.length > 0;
    document.getElementById('tab-stealthmark').hidden = !hasStealth;
    document.getElementById('tab-unscored').hidden = !hasUnscored;
    document.getElementById('tab-aa').hidden = !state.aa.length && !hasStealth && !hasUnscored;
    fillProviders();
    paint();
    if (!state.summary.generated_unix) {
      document.getElementById('folio-updated').textContent = 'edition unavailable';
    }
    if (!state.aa.length && !state.stealth.length && !state.unscored.length) {
      document.getElementById('results-status').textContent =
        'The register could not be read. The JSON files are the source of record.';
    }
  }

  function bind() {
    var search = document.getElementById('search-input');
    var clear = document.getElementById('clear-search');

    search.addEventListener('input', function () {
      state.query = search.value.trim().toLowerCase();
      clear.hidden = !search.value.length;
      paint();
    });

    clear.addEventListener('click', function () {
      search.value = '';
      state.query = '';
      clear.hidden = true;
      search.focus();
      paint();
    });

    document.getElementById('provider-filter').addEventListener('change', function (event) {
      state.provider = event.target.value;
      paint();
    });

    document.getElementById('sort-select').addEventListener('change', function (event) {
      state.sort = event.target.value;
      paint();
    });

    document.querySelectorAll('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        showSection(tab.getAttribute('data-section'));
      });
    });

    document.addEventListener('click', function (event) {
      var toggle = event.target.closest('[data-toggle]');
      if (!toggle) return;
      var key = toggle.getAttribute('data-toggle');
      var row = document.getElementById('detail-' + key);
      state.open[key] = !state.open[key];
      if (row) row.hidden = !state.open[key];
      toggle.setAttribute('aria-expanded', state.open[key] ? 'true' : 'false');
      toggle.textContent = (state.open[key] ? 'Hide routes ' : 'Show routes ') +
        '(' + (row ? row.querySelectorAll('.route-list li').length : 0) + ')';
    });

    var root = document.documentElement;
    var button = document.getElementById('theme-toggle');
    var label = document.getElementById('theme-label');

    function apply(mode) {
      var dark = mode === 'dark';
      root.setAttribute('data-theme', dark ? 'dark' : 'light');
      document.body.setAttribute('data-ink', dark ? 'dark' : 'light');
      button.setAttribute('aria-pressed', dark ? 'true' : 'false');
      label.textContent = dark ? 'Paper on ink' : 'Ink on paper';
    }

    var stored = null;
    try {
      stored = window.localStorage.getItem('fmc-ink');
    } catch (err) {
      stored = null;
    }
    apply(stored === 'dark' ? 'dark' : 'light');

    button.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      apply(next);
      try {
        window.localStorage.setItem('fmc-ink', next);
      } catch (err) {
        /* storage is optional */
      }
    });
  }

  bind();
  load();
}());