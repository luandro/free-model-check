/* ==========================================================================
   Free Model Check — Client Application (site/app.js)
   Vanilla ES6+, zero external dependencies, strict escaping, accessible UI
   ========================================================================== */

(function () {
  'use strict';

  const DATA_PATHS = {
    aa: [
      'data/free_models.json',
      './free_models.json',
      '../free_models.json'
    ],
    stealthmark: [
      'data/free_models_stealthmark.json',
      './free_models_stealthmark.json',
      '../free_models_stealthmark.json'
    ],
    unscored: [
      'data/free_models_unbenchmarked.json',
      './free_models_unbenchmarked.json',
      '../free_models_unbenchmarked.json'
    ]
  };

  const state = {
    aaData: null,
    stealthData: null,
    unscoredData: null,
    activeTab: 'all',
    searchQuery: '',
    providerFilter: 'all',
    sortBy: 'score',
    expandedRoutes: new Set()
  };

  /* ------------------------------------------------------------------------
     Utility Helpers
     ------------------------------------------------------------------------ */

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function cleanText(value) {
    if (value === null || value === undefined) return '';
    return String(value).trim();
  }

  function formatNumber(num, decimals = 1) {
    if (typeof num !== 'number' || !Number.isFinite(num)) return null;
    return num.toFixed(decimals);
  }

  function formatInteger(num) {
    if (typeof num !== 'number' || !Number.isFinite(num)) return null;
    return num.toLocaleString('en-US');
  }

  function formatContextShort(tokens) {
    if (typeof tokens !== 'number' || !Number.isFinite(tokens) || tokens <= 0) {
      return '—';
    }
    if (tokens >= 1000000) {
      const m = tokens / 1000000;
      const rounded = Math.abs(m - Math.round(m)) < 0.06 ? Math.round(m) : m.toFixed(1);
      return `${rounded}M`;
    }
    if (tokens >= 1000) {
      return `${Math.round(tokens / 1024)}K`;
    }
    return String(tokens);
  }

  function formatUnixTimestamp(unixSeconds) {
    if (typeof unixSeconds !== 'number' || !Number.isFinite(unixSeconds)) {
      return { formatted: 'Unknown', iso: '', relative: '' };
    }
    const date = new Date(unixSeconds * 1000);
    if (Number.isNaN(date.getTime())) {
      return { formatted: 'Unknown', iso: '', relative: '' };
    }
    const iso = date.toISOString();
    const yyyy = date.getUTCFullYear();
    const mm = String(date.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(date.getUTCDate()).padStart(2, '0');
    const hh = String(date.getUTCHours()).padStart(2, '0');
    const min = String(date.getUTCMinutes()).padStart(2, '0');
    const formatted = `${yyyy}-${mm}-${dd} ${hh}:${min} UTC`;

    const diffSec = Math.round((Date.now() - date.getTime()) / 1000);
    let relative = '';
    if (diffSec >= 0 && diffSec < 60) {
      relative = '(just now)';
    } else if (diffSec >= 60 && diffSec < 3600) {
      relative = `(${Math.floor(diffSec / 60)}m ago)`;
    } else if (diffSec >= 3600 && diffSec < 86400 * 3) {
      relative = `(${Math.floor(diffSec / 3600)}h ago)`;
    } else if (diffSec >= 86400 * 3 && diffSec < 86400 * 90) {
      relative = `(${Math.floor(diffSec / 86400)}d ago)`;
    }
    return { formatted, iso, relative };
  }

  function routeProviderBaseLabel(route) {
    if (!route || typeof route !== 'object') return 'unknown';
    const base = cleanText(route.api_base);
    if (base) {
      try {
        const url = new URL(base.includes('//') ? base : `https://${base}`);
        let host = (url.hostname || '').replace(/^api\./, '').replace(/^www\./, '');
        if (host.endsWith('googleapis.com')) return 'google';
        if (host) {
          let parts = host.split('.');
          if (parts[0].endsWith('-api') && parts.length > 1) {
            parts = parts.slice(1);
          }
          if (parts[0]) return parts[0];
        }
      } catch (_err) {
        // Fallback to provider field below
      }
    }
    return cleanText(route.provider) || 'unknown';
  }

  function routeProviderDisplayLabel(route) {
    const base = routeProviderBaseLabel(route);
    if (!base) return 'unknown';
    if (route && route.requires_provider_key) return `${base}*`;
    if (route && route.stale_mirror) return `${base}\u2020`;
    return base;
  }

  function getProviderLabelsForRoutes(routes) {
    if (!Array.isArray(routes)) return [];
    const seen = new Set();
    const out = [];
    for (const r of routes) {
      const label = routeProviderDisplayLabel(r);
      if (label && !seen.has(label)) {
        seen.add(label);
        out.push(label);
      }
    }
    return out;
  }

  function getBaseProvidersForRoutes(routes) {
    if (!Array.isArray(routes)) return [];
    const seen = new Set();
    for (const r of routes) {
      const base = routeProviderBaseLabel(r).toLowerCase();
      if (base) seen.add(base);
    }
    return Array.from(seen);
  }

  function routeSourceLabel(source) {
    const s = cleanText(source).toLowerCase();
    if (s === 'opencode') return 'OpenCode Zen catalogue';
    if (s === 'openrouter') return 'OpenRouter public API';
    if (s === 'f24') return 'F24 SALES LiteLLM feed';
    return cleanText(source) || 'unknown';
  }

  /* ------------------------------------------------------------------------
     Theme Management
     ------------------------------------------------------------------------ */

  function initTheme() {
    const toggleBtn = document.getElementById('theme-toggle');
    const toggleLabel = document.getElementById('theme-toggle-label');
    if (!toggleBtn) return;

    function applyTheme(theme) {
      const normalized = theme === 'light' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', normalized);
      const isLight = normalized === 'light';
      toggleBtn.setAttribute('aria-pressed', isLight ? 'true' : 'false');
      toggleBtn.setAttribute(
        'aria-label',
        isLight ? 'Switch to dark theme' : 'Switch to light theme'
      );
      if (toggleLabel) {
        toggleLabel.textContent = isLight ? 'Light' : 'Dark';
      }
    }

    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current);

    toggleBtn.addEventListener('click', function () {
      const active = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = active === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try {
        localStorage.setItem('fmc-theme', next);
      } catch (_err) {
        // Ignore storage errors in private browsing
      }
    });
  }

  /* ------------------------------------------------------------------------
     Data Fetching with Graceful Fallback
     ------------------------------------------------------------------------ */

  async function fetchFirstAvailableJson(paths) {
    for (const path of paths) {
      try {
        const response = await fetch(path, {
          headers: { Accept: 'application/json' },
          cache: 'no-cache'
        });
        if (!response.ok) continue;
        const data = await response.json();
        if (data && typeof data === 'object') {
          return data;
        }
      } catch (_err) {
        // Try next candidate path
      }
    }
    return null;
  }

  /* ------------------------------------------------------------------------
     Summary Strip & Header Rendering
     ------------------------------------------------------------------------ */

  function renderSummary(summary, aaModels, stealthModels, unscoredModels) {
    if (!summary || typeof summary !== 'object') return;

    // AA Index Version badge
    const versionBadge = document.getElementById('aa-version-badge');
    if (versionBadge && summary.artificial_analysis_index_version) {
      versionBadge.textContent = `AA Index v${Number(summary.artificial_analysis_index_version).toFixed(1)}`;
    }

    // Last updated timestamp
    const timeEl = document.getElementById('last-updated-time');
    const relEl = document.getElementById('last-updated-relative');
    if (timeEl) {
      const ts = formatUnixTimestamp(summary.generated_unix);
      timeEl.textContent = ts.formatted;
      if (ts.iso) timeEl.setAttribute('datetime', ts.iso);
      if (relEl) relEl.textContent = ts.relative;
    }

    // 1. Selected models
    const statModels = document.getElementById('stat-selected-models');
    const statModelsMeta = document.getElementById('stat-selected-models-meta');
    const selectedCount =
      typeof summary.selected_underlying_models === 'number'
        ? summary.selected_underlying_models
        : aaModels.length;
    if (statModels) {
      statModels.textContent = String(selectedCount);
    }
    if (statModelsMeta) {
      const matched = summary.free_underlying_models_matched ?? aaModels.length;
      const considered = summary.aa_models_considered
        ? formatInteger(summary.aa_models_considered)
        : '—';
      const extraParts = [];
      if (stealthModels.length > 0) extraParts.push(`${stealthModels.length} StealthMark`);
      if (unscoredModels.length > 0) extraParts.push(`${unscoredModels.length} unscored`);
      const extraSuffix = extraParts.length ? ` (+${extraParts.join(', ')})` : '';
      statModelsMeta.textContent = `${selectedCount} selected from ${matched} free matched of ${considered} AA models${extraSuffix}`;
    }

    // 2. Free routes
    const statRoutes = document.getElementById('stat-free-routes');
    const statRoutesMeta = document.getElementById('stat-free-routes-meta');
    const verif = summary.routes_by_verification || {};
    const zeroPriced = verif['zero-priced'] ?? summary.free_routes_seen ?? 0;
    const freeRoutesSeen = summary.free_routes_seen ?? zeroPriced;
    if (statRoutes) {
      statRoutes.textContent = String(freeRoutesSeen);
    }
    if (statRoutesMeta) {
      const skippedFreeTier = summary.free_tier_routes_skipped || 0;
      const skippedMirror = summary.mirror_routes_skipped || 0;
      const parts = [`${zeroPriced} zero-priced`];
      if (skippedFreeTier > 0) parts.push(`${skippedFreeTier} free-tier* skipped`);
      if (skippedMirror > 0) parts.push(`${skippedMirror} mirror\u2020 skipped`);
      statRoutesMeta.textContent = parts.join(' · ');
    }

    // 3. Intelligence floor
    const statFloor = document.getElementById('stat-intel-floor');
    const statFloorMeta = document.getElementById('stat-intel-floor-meta');
    const floorTag = document.getElementById('summary-floor-tag');
    const floorVal =
      typeof summary.intelligence_floor === 'number'
        ? summary.intelligence_floor.toFixed(1)
        : '25.0';
    if (statFloor) {
      statFloor.textContent = `>= ${floorVal}`;
    }
    if (floorTag && summary.intelligence_floor_source) {
      floorTag.textContent = summary.intelligence_floor_source;
    }
    if (statFloorMeta) {
      const minCtx =
        summary.thresholds &&
        summary.thresholds.agent_ready &&
        summary.thresholds.agent_ready.min_context
          ? formatContextShort(summary.thresholds.agent_ready.min_context)
          : '64K';
      statFloorMeta.textContent = `AA Intelligence Index >= ${floorVal} · Ready >= ${minCtx} ctx`;
    }

    // 4. Sources
    const statSources = document.getElementById('stat-sources');
    const statSourcesMeta = document.getElementById('stat-sources-meta');
    const bySource = summary.routes_by_source || {};
    if (statSources) {
      const activeSourceNames = ['OpenRouter', 'OpenCode', 'F24'];
      statSources.textContent = activeSourceNames.join(' · ');
    }
    if (statSourcesMeta) {
      const orCount = bySource.openrouter ?? 0;
      const ocCount = bySource.opencode ?? 0;
      const f24Count = bySource.f24 ?? 0;
      const mdCount = summary.models_dev_entries_seen
        ? `${formatInteger(summary.models_dev_entries_seen)} models.dev`
        : 'models.dev';
      statSourcesMeta.textContent = `OpenRouter (${orCount}) · OpenCode (${ocCount}) · F24 (${f24Count}) · ${mdCount}`;
    }

    // Coverage note under controls
    const coverageNote = document.getElementById('aa-coverage-note');
    if (coverageNote && summary.benchmark_coverage) {
      const cov = summary.benchmark_coverage;
      if (cov.coding_agentic_available === false) {
        coverageNote.textContent =
          'Note: AA free tier provides Intelligence Index scores only; Coding and Agentic sub-indices require AA Pro and are shown as —.';
      } else {
        coverageNote.textContent = `AA benchmark pool: ${cov.free_candidates_scored || 0} scored free candidates (${cov.free_candidates_scored_own || 0} own, ${cov.free_candidates_scored_inherited || 0} inherited).`;
      }
    }
  }

  /* ------------------------------------------------------------------------
     Filtering & Sorting Logic
     ------------------------------------------------------------------------ */

  function collectAllProviders(aaModels, stealthModels, unscoredModels) {
    const providerCounts = new Map();
    const allLists = [aaModels, stealthModels, unscoredModels];
    for (const list of allLists) {
      if (!Array.isArray(list)) continue;
      for (const item of list) {
        const baseProviders = getBaseProvidersForRoutes(item.free_routes || []);
        for (const p of baseProviders) {
          providerCounts.set(p, (providerCounts.get(p) || 0) + 1);
        }
      }
    }
    return Array.from(providerCounts.entries()).sort((a, b) => {
      if (b[1] !== a[1]) return b[1] - a[1];
      return a[0].localeCompare(b[0]);
    });
  }

  function populateProviderFilter(aaModels, stealthModels, unscoredModels) {
    const select = document.getElementById('provider-filter');
    if (!select) return;
    const providers = collectAllProviders(aaModels, stealthModels, unscoredModels);
    const currentVal = state.providerFilter;

    select.innerHTML = '<option value="all">All providers</option>';
    for (const [prov, count] of providers) {
      const opt = document.createElement('option');
      opt.value = prov;
      opt.textContent = `${prov} (${count})`;
      select.appendChild(opt);
    }

    if (providers.some(([p]) => p === currentVal)) {
      select.value = currentVal;
    } else {
      state.providerFilter = 'all';
      select.value = 'all';
    }
  }

  function recordMatchesFilters(record, sectionType) {
    const routes = Array.isArray(record.free_routes) ? record.free_routes : [];

    // 1. Provider filter
    if (state.providerFilter !== 'all') {
      const baseProviders = getBaseProvidersForRoutes(routes);
      if (!baseProviders.includes(state.providerFilter)) {
        return false;
      }
    }

    // 2. Search query filter
    const q = state.searchQuery.trim().toLowerCase();
    if (!q) return true;

    const model = record.model || {};
    const quality = record.quality || {};
    const providerLabels = getProviderLabelsForRoutes(routes);
    const routeStrings = routes.map(
      (r) => `${r.provider || ''} ${r.route_model || ''} ${r.model_name || ''} ${r.source || ''} ${r.verification || ''}`
    );

    const haystack = [
      model.name,
      model.creator,
      model.aa_slug,
      model.stealthmark_id,
      model.revealed_name,
      model.group,
      model.models_dev_id,
      model.description,
      quality.score_source,
      quality.score_inherited_from,
      quality.tier,
      sectionType,
      ...providerLabels,
      ...routeStrings
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();

    return haystack.includes(q);
  }

  function getRecordContext(record) {
    if (
      record.agent_readiness &&
      record.agent_readiness.models_dev &&
      typeof record.agent_readiness.models_dev.context === 'number'
    ) {
      return record.agent_readiness.models_dev.context;
    }
    if (record.capabilities && typeof record.capabilities.context === 'number') {
      return record.capabilities.context;
    }
    return -1;
  }

  function getRecordReleaseDate(record) {
    if (record.model && record.model.release_date) {
      return String(record.model.release_date);
    }
    if (
      record.agent_readiness &&
      record.agent_readiness.models_dev &&
      record.agent_readiness.models_dev.release_date
    ) {
      return String(record.agent_readiness.models_dev.release_date);
    }
    if (record.capabilities && record.capabilities.release_date) {
      return String(record.capabilities.release_date);
    }
    return '';
  }

  function sortRecords(records, sectionType) {
    const copy = records.slice();
    const sortBy = state.sortBy;

    copy.sort((a, b) => {
      const nameA = cleanText(a.model && a.model.name);
      const nameB = cleanText(b.model && b.model.name);

      if (sortBy === 'name') {
        return nameA.localeCompare(nameB);
      }

      if (sortBy === 'context') {
        const ctxDiff = getRecordContext(b) - getRecordContext(a);
        if (ctxDiff !== 0) return ctxDiff;
      }

      if (sortBy === 'routes') {
        const rA = a.route_count || (a.free_routes ? a.free_routes.length : 0);
        const rB = b.route_count || (b.free_routes ? b.free_routes.length : 0);
        if (rB !== rA) return rB - rA;
      }

      if (sortBy === 'release') {
        const relA = getRecordReleaseDate(a);
        const relB = getRecordReleaseDate(b);
        if (relB !== relA) return relB.localeCompare(relA);
      }

      if (sectionType === 'unscored') {
        const stealthDiff = (b.stealth ? 1 : 0) - (a.stealth ? 1 : 0);
        if (stealthDiff !== 0) return stealthDiff;
        const ctxDiff = getRecordContext(b) - getRecordContext(a);
        if (ctxDiff !== 0) return ctxDiff;
        return nameA.localeCompare(nameB);
      }

      const qA = a.quality || {};
      const qB = b.quality || {};
      const scoreA = typeof qA.capability_score === 'number' ? qA.capability_score : -1;
      const scoreB = typeof qB.capability_score === 'number' ? qB.capability_score : -1;
      const intelA = typeof qA.intelligence === 'number' ? qA.intelligence : -1;
      const intelB = typeof qB.intelligence === 'number' ? qB.intelligence : -1;

      if (sortBy === 'intelligence') {
        if (intelB !== intelA) return intelB - intelA;
        if (scoreB !== scoreA) return scoreB - scoreA;
      } else {
        // Default: score
        if (scoreB !== scoreA) return scoreB - scoreA;
        if (intelB !== intelA) return intelB - intelA;
      }

      const rA = a.route_count || (a.free_routes ? a.free_routes.length : 0);
      const rB = b.route_count || (b.free_routes ? b.free_routes.length : 0);
      if (rB !== rA) return rB - rA;
      return nameA.localeCompare(nameB);
    });

    return copy;
  }

  /* ------------------------------------------------------------------------
     Card / Hybrid Table Rendering
     ------------------------------------------------------------------------ */

  function renderVerificationBadge(route) {
    const v = cleanText(route.verification) || 'zero-priced';
    if (route.requires_provider_key || v === 'free-tier') {
      return '<span class="verif-badge verif-freetier" title="Uses your own provider key; usage may bill at list price">free-tier (*)</span>';
    }
    if (route.stale_mirror || v === 'mirror-listed') {
      return '<span class="verif-badge verif-mirror" title="Third-party mirror claim; unconfirmed by live provider">mirror-listed (&dagger;)</span>';
    }
    return '<span class="verif-badge verif-zero" title="Provider publishes explicit 0 across all billable fields">zero-priced</span>';
  }

  function renderRoutesDrawer(cardKey, routes) {
    if (!Array.isArray(routes) || routes.length === 0) return '';
    const isExpanded = state.expandedRoutes.has(cardKey);
    const itemsHtml = routes
      .map((route) => {
        const provLabel = routeProviderDisplayLabel(route);
        const routeModel = cleanText(route.route_model);
        const srcLabel = routeSourceLabel(route.source);
        return `
          <div class="route-item">
            <div class="route-item-left">
              <div class="route-id-line">
                <span class="route-provider-tag">${escapeHtml(provLabel)}</span>
                <code class="route-model-code" title="${escapeHtml(routeModel)}">${escapeHtml(routeModel)}</code>
              </div>
              <span class="route-source-caption">${escapeHtml(srcLabel)}</span>
            </div>
            <div class="route-item-right">
              ${renderVerificationBadge(route)}
              <button type="button" class="copy-route-btn" data-copy-route="${escapeHtml(routeModel)}" aria-label="Copy route ID ${escapeHtml(routeModel)}">
                Copy ID
              </button>
            </div>
          </div>
        `;
      })
      .join('');

    return `
      <div class="model-routes-drawer" id="drawer-${escapeHtml(cardKey)}" ${isExpanded ? '' : 'hidden'}>
        <div class="routes-drawer-list">
          ${itemsHtml}
        </div>
      </div>
    `;
  }

  function renderProviderColumn(cardKey, routes, routeCount) {
    const labels = getProviderLabelsForRoutes(routes);
    const pillsHtml = labels
      .map((label) => {
        const isWarn = label.includes('*') || label.includes('\u2020');
        return `<span class="provider-pill ${isWarn ? 'provider-pill-warn' : ''}">${escapeHtml(label)}</span>`;
      })
      .join('');

    const firstRoute = routes && routes[0] ? cleanText(routes[0].route_model) : '';
    const isExpanded = state.expandedRoutes.has(cardKey);
    const countNum = typeof routeCount === 'number' ? routeCount : routes.length;
    const countLabel = countNum === 1 ? '1 free route' : `${countNum} free routes`;

    return `
      <div class="providers-wrap">
        <span class="cell-label">Providers &amp; Routes</span>
        <div class="provider-pills">${pillsHtml || '<span class="provider-pill">unknown</span>'}</div>
        <div class="route-summary-line">
          <span class="route-count-badge">${escapeHtml(countLabel)}</span>
          <button
            type="button"
            class="routes-toggle-btn"
            data-toggle-drawer="${escapeHtml(cardKey)}"
            aria-expanded="${isExpanded ? 'true' : 'false'}"
            aria-controls="drawer-${escapeHtml(cardKey)}"
            title="${escapeHtml(firstRoute)}"
          >
            <span>${isExpanded ? 'Hide routes' : 'View route IDs'}</span>
          </button>
        </div>
      </div>
    `;
  }

  function renderSpecsColumn(readyStatus, mdMeta) {
    const meta = mdMeta || {};
    const ctxTokens = typeof meta.context === 'number' ? meta.context : null;
    const ctxShort = formatContextShort(ctxTokens);
    const ctxExact = ctxTokens ? `${formatInteger(ctxTokens)} tokens` : 'Unknown context';

    let readyBadge = '';
    if (readyStatus === true) {
      readyBadge = '<span class="mini-badge mini-badge-ready" title="Confirmed tool calling and >= 65,536 context">Agent Ready</span>';
    } else if (readyStatus === false) {
      readyBadge = '<span class="mini-badge mini-badge-warn" title="Failed agent-readiness threshold">Not Agent Ready</span>';
    } else {
      readyBadge = '<span class="mini-badge" title="Capability metadata incomplete">Readiness ?</span>';
    }

    const toolCall = meta.tool_call;
    const toolText = toolCall === true ? 'Tool Call: Yes' : toolCall === false ? 'Tool Call: No' : 'Tool Call: ?';
    const toolClass = toolCall === true ? 'mini-badge-ready' : '';

    const reasoningBadge =
      meta.reasoning === true ? '<span class="mini-badge">Reasoning</span>' : '';

    return `
      <div class="specs-wrap">
        <span class="cell-label">Context &amp; Readiness</span>
        <div class="context-line" title="${escapeHtml(ctxExact)}">
          <span>${escapeHtml(ctxShort)} ctx</span>
          ${ctxTokens ? `<span class="context-raw">(${escapeHtml(formatInteger(ctxTokens))})</span>` : ''}
        </div>
        <div class="readiness-badges">
          ${readyBadge}
          <span class="mini-badge ${toolClass}">${escapeHtml(toolText)}</span>
          ${reasoningBadge}
        </div>
      </div>
    `;
  }

  function renderAAModelCard(record, index) {
    const rank = index + 1;
    const model = record.model || {};
    const quality = record.quality || {};
    const perf = record.performance || {};
    const agent = record.agent_readiness || {};
    const md = agent.models_dev || {};
    const routes = Array.isArray(record.free_routes) ? record.free_routes : [];
    const cardKey = `aa-${model.aa_slug || index}`;

    const scoreStr = formatNumber(quality.capability_score, 1);
    const intelStr = formatNumber(quality.intelligence, 1);
    const codeStr = formatNumber(quality.coding, 1);
    const agentStr = formatNumber(quality.agentic, 1);

    const barWidth =
      typeof quality.capability_score === 'number'
        ? Math.min(100, Math.max(8, Math.round((quality.capability_score / 60) * 100)))
        : 0;

    const releaseDate = cleanText(model.release_date || md.release_date) || '—';
    const scoreSource = cleanText(quality.score_source) || 'own';
    const inheritedFrom = cleanText(quality.score_inherited_from);
    const tier = cleanText(quality.tier) || 'strong';

    const sourcePillClass = scoreSource === 'inherited' ? 'source-pill-inherited' : 'source-pill-own';
    const sourceTitle =
      scoreSource === 'inherited' && inheritedFrom
        ? `Inherited from sibling AA snapshot: ${inheritedFrom}`
        : 'Direct Artificial Analysis benchmark score';
    const sourceLabel =
      scoreSource === 'inherited' && inheritedFrom
        ? `score_source: inherited (${inheritedFrom})`
        : `score_source: ${scoreSource}`;

    const perfParts = [];
    if (typeof perf.tokens_per_second === 'number') {
      perfParts.push(`${perf.tokens_per_second.toFixed(0)} tok/s`);
    }
    if (typeof perf.time_to_first_token_seconds === 'number') {
      perfParts.push(`${perf.time_to_first_token_seconds.toFixed(2)}s TTFT`);
    }

    return `
      <article class="model-card" role="listitem" data-rank="${rank}">
        <div class="model-card-main">
          <div class="cell-rank">
            <span class="rank-badge" aria-label="Rank ${rank}">#${rank}</span>
            <span class="cell-label">Tier: ${escapeHtml(tier)}</span>
          </div>

          <div class="cell-model">
            <span class="cell-label">Model &amp; Creator</span>
            <div class="model-name-row">
              <h3 class="model-name">${escapeHtml(model.name || 'Unknown Model')}</h3>
            </div>
            <div class="creator-line">
              <span class="creator-name">${escapeHtml(model.creator || 'Unknown Creator')}</span>
              ${model.aa_slug ? `<span aria-hidden="true">·</span><code class="slug-code">${escapeHtml(model.aa_slug)}</code>` : ''}
            </div>
          </div>

          <div class="cell-scores">
            <span class="cell-label">Benchmarks (Score / Intel / Code / Agent)</span>
            <div class="scores-grid">
              <div class="score-box score-box-primary" title="Weighted capability score">
                <span class="score-box-label">Score</span>
                <span class="score-box-val ${scoreStr ? '' : 'is-null'}">${escapeHtml(scoreStr || '—')}</span>
                <div class="score-bar-track" aria-hidden="true">
                  <div class="score-bar-fill" style="width: ${barWidth}%"></div>
                </div>
              </div>
              <div class="score-box" title="Artificial Analysis Intelligence Index">
                <span class="score-box-label">Intel</span>
                <span class="score-box-val ${intelStr ? '' : 'is-null'}">${escapeHtml(intelStr || '—')}</span>
              </div>
              <div class="score-box" title="Artificial Analysis Coding Index (Pro tier)">
                <span class="score-box-label">Coding</span>
                <span class="score-box-val ${codeStr ? '' : 'is-null'}">${escapeHtml(codeStr || '—')}</span>
              </div>
              <div class="score-box" title="Artificial Analysis Agentic Index (Pro tier)">
                <span class="score-box-label">Agentic</span>
                <span class="score-box-val ${agentStr ? '' : 'is-null'}">${escapeHtml(agentStr || '—')}</span>
              </div>
            </div>
          </div>

          ${renderProviderColumn(cardKey, routes, record.route_count)}
          ${renderSpecsColumn(agent.status, md)}

          <div class="meta-wrap">
            <span class="cell-label">Released &amp; Source</span>
            <div class="release-date" title="Model release date">${escapeHtml(releaseDate)}</div>
            <div class="source-badges">
              <span class="source-pill ${sourcePillClass}" title="${escapeHtml(sourceTitle)}">
                ${escapeHtml(sourceLabel)}
              </span>
              <span class="source-pill">tier: ${escapeHtml(tier)}</span>
            </div>
            ${perfParts.length ? `<div class="perf-line">${escapeHtml(perfParts.join(' · '))}</div>` : ''}
          </div>
        </div>
        ${renderRoutesDrawer(cardKey, routes)}
      </article>
    `;
  }

  function renderStealthMarkCard(record, index) {
    const rank = index + 1;
    const model = record.model || {};
    const quality = record.quality || {};
    const sm = quality.stealthmark || {};
    const agent = record.agent_readiness || {};
    const md = agent.models_dev || {};
    const routes = Array.isArray(record.free_routes) ? record.free_routes : [];
    const cardKey = `sm-${model.stealthmark_id || index}`;

    const generalStr = formatNumber(sm.general ?? quality.intelligence, 1);
    const visualStr = formatNumber(sm.visual, 1);
    const overallStr = formatNumber(sm.overall, 1);

    const identityParts = [];
    if (model.revealed_name) identityParts.push(`Revealed: ${model.revealed_name}`);
    if (model.group) identityParts.push(model.group);
    if (model.identity_status) identityParts.push(model.identity_status);

    const nameHtml = model.page_url
      ? `<a href="${escapeHtml(model.page_url)}" class="model-name-link" target="_blank" rel="noopener noreferrer">${escapeHtml(model.name || 'Stealth Model')}</a>`
      : escapeHtml(model.name || 'Stealth Model');

    const editionText = sm.edition || (md.release_date ? md.release_date : 'Default edition');
    const scenesText =
      typeof sm.results_counted === 'number' && typeof sm.prompts === 'number'
        ? `${sm.results_counted}/${sm.prompts} scenes`
        : '';

    return `
      <article class="model-card" role="listitem" data-rank="${rank}">
        <div class="model-card-main">
          <div class="cell-rank">
            <span class="rank-badge" aria-label="Rank ${rank}">#${rank}</span>
          </div>

          <div class="cell-model">
            <span class="cell-label">Model &amp; Identity</span>
            <div class="model-name-row">
              <h3 class="model-name">${nameHtml}</h3>
            </div>
            <div class="creator-line">
              <span class="creator-name">${escapeHtml(identityParts.join(' · ') || 'StealthMark Evaluated')}</span>
              ${model.stealthmark_id ? `<code class="slug-code">${escapeHtml(model.stealthmark_id)}</code>` : ''}
            </div>
          </div>

          <div class="cell-scores">
            <span class="cell-label">StealthMark (General / Visual / Overall)</span>
            <div class="scores-grid scores-grid-3">
              <div class="score-box score-box-primary" title="StealthMark General (0-100 intelligence proxy)">
                <span class="score-box-label">General</span>
                <span class="score-box-val ${generalStr ? '' : 'is-null'}">${escapeHtml(generalStr || '—')}</span>
              </div>
              <div class="score-box" title="StealthMark Visual SVG score">
                <span class="score-box-label">Visual</span>
                <span class="score-box-val ${visualStr ? '' : 'is-null'}">${escapeHtml(visualStr || '—')}</span>
              </div>
              <div class="score-box" title="StealthMark Overall score">
                <span class="score-box-label">Overall</span>
                <span class="score-box-val ${overallStr ? '' : 'is-null'}">${escapeHtml(overallStr || '—')}</span>
              </div>
            </div>
          </div>

          ${renderProviderColumn(cardKey, routes, record.route_count)}
          ${renderSpecsColumn(agent.status, md)}

          <div class="meta-wrap">
            <span class="cell-label">Edition &amp; Source</span>
            <div class="release-date">${escapeHtml(editionText)}</div>
            <div class="source-badges">
              <span class="source-pill source-pill-inherited">
                score_source: ${escapeHtml(quality.score_source || 'stealthmark-proxy')}
              </span>
              ${scenesText ? `<span class="source-pill">${escapeHtml(scenesText)}</span>` : ''}
            </div>
          </div>
        </div>
        ${renderRoutesDrawer(cardKey, routes)}
      </article>
    `;
  }

  function renderUnscoredCard(record, index) {
    const rank = index + 1;
    const model = record.model || {};
    const bench = record.benchmark || {};
    const caps = record.capabilities || {};
    const routes = Array.isArray(record.free_routes) ? record.free_routes : [];
    const cardKey = `unscored-${model.models_dev_id || index}`;

    const benchStatus =
      bench.status === 'aa-unscored' ? 'AA entry unscored' : 'No AA or StealthMark entry';
    const releaseDate = cleanText(caps.release_date) || '—';

    return `
      <article class="model-card" role="listitem" data-rank="${rank}">
        <div class="model-card-main">
          <div class="cell-rank">
            <span class="rank-badge" aria-label="Rank ${rank}">#${rank}</span>
          </div>

          <div class="cell-model">
            <span class="cell-label">Model &amp; Classification</span>
            <div class="model-name-row">
              <h3 class="model-name">${escapeHtml(model.name || 'Unscored Model')}</h3>
              ${record.stealth ? '<span class="mini-badge mini-badge-warn">Stealth</span>' : ''}
              ${record.legacy ? '<span class="mini-badge">Legacy</span>' : ''}
            </div>
            <div class="creator-line">
              ${model.models_dev_id ? `<code class="slug-code">${escapeHtml(model.models_dev_id)}</code>` : '<span class="creator-name">Feed metadata verified</span>'}
            </div>
          </div>

          <div class="cell-scores">
            <span class="cell-label">Benchmark Status</span>
            <div class="source-badges">
              <span class="source-pill">${escapeHtml(benchStatus)}</span>
              ${bench.aa_slug ? `<code class="slug-code">${escapeHtml(bench.aa_slug)}</code>` : ''}
            </div>
          </div>

          ${renderProviderColumn(cardKey, routes, record.route_count)}
          ${renderSpecsColumn(caps.agent_ready, caps)}

          <div class="meta-wrap">
            <span class="cell-label">Released</span>
            <div class="release-date">${escapeHtml(releaseDate)}</div>
            <div class="source-badges">
              <span class="source-pill">score_source: unscored</span>
            </div>
          </div>
        </div>
        ${renderRoutesDrawer(cardKey, routes)}
      </article>
    `;
  }

  function renderEmptyState(message) {
    return `
      <div class="empty-state">
        <h3 class="empty-state-title">No models match the active filters</h3>
        <p class="empty-state-desc">${escapeHtml(message)}</p>
      </div>
    `;
  }

  /* ------------------------------------------------------------------------
     Main View Orchestration
     ------------------------------------------------------------------------ */

  function updateView() {
    const rawAA = state.aaData && Array.isArray(state.aaData.models) ? state.aaData.models : [];
    const rawStealth =
      state.stealthData && Array.isArray(state.stealthData.models) ? state.stealthData.models : [];
    const rawUnscored =
      state.unscoredData && Array.isArray(state.unscoredData.models)
        ? state.unscoredData.models
        : [];

    const hasStealth = rawStealth.length > 0;
    const hasUnscored = rawUnscored.length > 0;
    const hasSecondary = hasStealth || hasUnscored;

    const filteredAA = sortRecords(
      rawAA.filter((r) => recordMatchesFilters(r, 'aa')),
      'aa'
    );
    const filteredStealth = sortRecords(
      rawStealth.filter((r) => recordMatchesFilters(r, 'stealthmark')),
      'stealthmark'
    );
    const filteredUnscored = sortRecords(
      rawUnscored.filter((r) => recordMatchesFilters(r, 'unscored')),
      'unscored'
    );

    // Update tab counts & visibility
    const tabAll = document.getElementById('tab-all');
    const tabAA = document.getElementById('tab-aa');
    const tabStealth = document.getElementById('tab-stealthmark');
    const tabUnscored = document.getElementById('tab-unscored');

    const countAllEl = document.getElementById('count-all');
    const countAAEl = document.getElementById('count-aa');
    const countStealthEl = document.getElementById('count-stealthmark');
    const countUnscoredEl = document.getElementById('count-unscored');

    if (countAAEl) countAAEl.textContent = String(filteredAA.length);
    if (countStealthEl) countStealthEl.textContent = String(filteredStealth.length);
    if (countUnscoredEl) countUnscoredEl.textContent = String(filteredUnscored.length);
    if (countAllEl) {
      countAllEl.textContent = String(
        filteredAA.length + filteredStealth.length + filteredUnscored.length
      );
    }

    if (tabStealth) tabStealth.hidden = !hasStealth;
    if (tabUnscored) tabUnscored.hidden = !hasUnscored;
    if (tabAll) tabAll.hidden = !hasSecondary;

    // Ensure activeTab is valid
    if (state.activeTab === 'stealthmark' && !hasStealth) state.activeTab = 'aa';
    if (state.activeTab === 'unscored' && !hasUnscored) state.activeTab = 'aa';
    if (state.activeTab === 'all' && !hasSecondary) state.activeTab = 'aa';

    const tabs = [tabAll, tabAA, tabStealth, tabUnscored].filter(Boolean);
    for (const t of tabs) {
      const isCurrent = t.getAttribute('data-section') === state.activeTab;
      t.classList.toggle('is-active', isCurrent);
      t.setAttribute('aria-selected', isCurrent ? 'true' : 'false');
    }

    // Section visibility: if a file is empty or missing, hide that section gracefully
    const secAA = document.getElementById('section-aa');
    const secStealth = document.getElementById('section-stealthmark');
    const secUnscored = document.getElementById('section-unscored');

    if (secAA) {
      secAA.hidden = !(state.activeTab === 'all' || state.activeTab === 'aa');
    }
    if (secStealth) {
      secStealth.hidden =
        !hasStealth || !(state.activeTab === 'all' || state.activeTab === 'stealthmark');
    }
    if (secUnscored) {
      secUnscored.hidden =
        !hasUnscored || !(state.activeTab === 'all' || state.activeTab === 'unscored');
    }

    // Render AA list
    const aaListEl = document.getElementById('aa-models-list');
    if (aaListEl) {
      if (filteredAA.length > 0) {
        aaListEl.innerHTML = filteredAA.map((rec, idx) => renderAAModelCard(rec, idx)).join('');
      } else {
        aaListEl.innerHTML = renderEmptyState(
          rawAA.length === 0
            ? 'No AA-scored models were present in free_models.json.'
            : 'Try clearing the search query or selecting "All providers".'
        );
      }
    }

    // Render StealthMark list (only when populated)
    const stealthListEl = document.getElementById('stealthmark-models-list');
    if (stealthListEl && hasStealth) {
      if (filteredStealth.length > 0) {
        stealthListEl.innerHTML = filteredStealth
          .map((rec, idx) => renderStealthMarkCard(rec, idx))
          .join('');
      } else {
        stealthListEl.innerHTML = renderEmptyState(
          'No StealthMark proxy models match the current search or provider filter.'
        );
      }
    }

    // Render Unscored list (only when populated)
    const unscoredListEl = document.getElementById('unscored-models-list');
    if (unscoredListEl && hasUnscored) {
      if (filteredUnscored.length > 0) {
        unscoredListEl.innerHTML = filteredUnscored
          .map((rec, idx) => renderUnscoredCard(rec, idx))
          .join('');
      } else {
        unscoredListEl.innerHTML = renderEmptyState(
          'No unscored models match the current search or provider filter.'
        );
      }
    }

    // Update results status bar
    const countTextEl = document.getElementById('results-count-text');
    if (countTextEl) {
      const totalShown =
        (secAA && !secAA.hidden ? filteredAA.length : 0) +
        (secStealth && !secStealth.hidden ? filteredStealth.length : 0) +
        (secUnscored && !secUnscored.hidden ? filteredUnscored.length : 0);
      const totalAvailable =
        (secAA && !secAA.hidden ? rawAA.length : 0) +
        (secStealth && !secStealth.hidden ? rawStealth.length : 0) +
        (secUnscored && !secUnscored.hidden ? rawUnscored.length : 0);

      const filterNotes = [];
      if (state.providerFilter !== 'all') filterNotes.push(`provider: ${state.providerFilter}`);
      if (state.searchQuery.trim()) filterNotes.push(`query: "${state.searchQuery.trim()}"`);
      const suffix = filterNotes.length ? ` (filtered by ${filterNotes.join(', ')})` : '';
      countTextEl.textContent = `Showing ${totalShown} of ${totalAvailable} verified model${totalAvailable === 1 ? '' : 's'}${suffix}`;
    }
  }

  /* ------------------------------------------------------------------------
     Event Listeners
     ------------------------------------------------------------------------ */

  function bindEvents() {
    const searchInput = document.getElementById('search-input');
    const clearBtn = document.getElementById('clear-search');
    const providerSelect = document.getElementById('provider-filter');
    const sortSelect = document.getElementById('sort-select');
    const tablist = document.getElementById('section-tablist');
    const panelContainer = document.getElementById('panel-container');

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        state.searchQuery = searchInput.value || '';
        if (clearBtn) {
          clearBtn.hidden = !state.searchQuery.length;
        }
        updateView();
      });
    }

    if (clearBtn && searchInput) {
      clearBtn.addEventListener('click', function () {
        searchInput.value = '';
        state.searchQuery = '';
        clearBtn.hidden = true;
        searchInput.focus();
        updateView();
      });
    }

    if (providerSelect) {
      providerSelect.addEventListener('change', function () {
        state.providerFilter = providerSelect.value || 'all';
        updateView();
      });
    }

    if (sortSelect) {
      sortSelect.addEventListener('change', function () {
        state.sortBy = sortSelect.value || 'score';
        updateView();
      });
    }

    if (tablist) {
      tablist.addEventListener('click', function (e) {
        const btn = e.target.closest('.section-tab');
        if (!btn || btn.hidden) return;
        const sec = btn.getAttribute('data-section');
        if (sec) {
          state.activeTab = sec;
          updateView();
        }
      });
    }

    if (panelContainer) {
      panelContainer.addEventListener('click', function (e) {
        const toggleBtn = e.target.closest('[data-toggle-drawer]');
        if (toggleBtn) {
          const key = toggleBtn.getAttribute('data-toggle-drawer');
          const drawer = document.getElementById(`drawer-${key}`);
          if (key && drawer) {
            const willExpand = drawer.hidden;
            drawer.hidden = !willExpand;
            toggleBtn.setAttribute('aria-expanded', willExpand ? 'true' : 'false');
            toggleBtn.innerHTML = `<span>${willExpand ? 'Hide routes' : 'View route IDs'}</span>`;
            if (willExpand) {
              state.expandedRoutes.add(key);
            } else {
              state.expandedRoutes.delete(key);
            }
          }
          return;
        }

        const copyBtn = e.target.closest('[data-copy-route]');
        if (copyBtn) {
          const routeId = copyBtn.getAttribute('data-copy-route');
          if (routeId && navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(routeId).then(
              function () {
                const orig = copyBtn.textContent;
                copyBtn.textContent = 'Copied';
                setTimeout(function () {
                  copyBtn.textContent = orig;
                }, 1400);
              },
              function () {
                // Ignore clipboard errors
              }
            );
          }
        }
      });
    }
  }

  /* ------------------------------------------------------------------------
     Bootstrap
     ------------------------------------------------------------------------ */

  async function init() {
    initTheme();
    bindEvents();

    const [aaData, stealthData, unscoredData] = await Promise.all([
      fetchFirstAvailableJson(DATA_PATHS.aa),
      fetchFirstAvailableJson(DATA_PATHS.stealthmark),
      fetchFirstAvailableJson(DATA_PATHS.unscored)
    ]);

    state.aaData = aaData;
    state.stealthData = stealthData;
    state.unscoredData = unscoredData;

    const errorBanner = document.getElementById('error-banner');

    if (!aaData) {
      if (errorBanner) {
        errorBanner.hidden = false;
        errorBanner.textContent =
          'Could not load data/free_models.json. Ensure the report JSON has been generated and copied to site/data/free_models.json.';
      }
      const aaListEl = document.getElementById('aa-models-list');
      if (aaListEl) {
        aaListEl.innerHTML = renderEmptyState(
          'Waiting for data/free_models.json to be published.'
        );
      }
      const countTextEl = document.getElementById('results-count-text');
      if (countTextEl) {
        countTextEl.textContent = 'Dataset unavailable';
      }
      return;
    }

    if (errorBanner) {
      errorBanner.hidden = true;
    }

    const aaModels = Array.isArray(aaData.models) ? aaData.models : [];
    const stealthModels =
      stealthData && Array.isArray(stealthData.models) ? stealthData.models : [];
    const unscoredModels =
      unscoredData && Array.isArray(unscoredData.models) ? unscoredData.models : [];

    // Default active tab: 'all' if secondary sections have data, otherwise 'aa'
    state.activeTab = stealthModels.length > 0 || unscoredModels.length > 0 ? 'all' : 'aa';

    renderSummary(aaData.summary, aaModels, stealthModels, unscoredModels);
    populateProviderFilter(aaModels, stealthModels, unscoredModels);
    updateView();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
