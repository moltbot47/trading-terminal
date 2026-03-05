/* Strategy Lab -- Client-side logic for strategy import, scanner, and simulator */

// ---- HELPERS ----
var _labLoaded = false;
var _expandedStrategyId = null;

function refreshLab() {
  loadStrategies();
  loadLabStats();
  loadLabAnalytics();
  loadScannerFeed();
  loadActiveSims();
  _labLoaded = true;
}

// ---- IMPORT: YouTube ----
var _ytPollTimer = null;

async function importYouTube() {
  var url = $('yt-url').value.trim();
  if (!url) { $('yt-status').innerHTML = '<span class="c-red">Enter a YouTube URL</span>'; return; }

  $('yt-status').innerHTML = '<span class="c-yellow">Starting import...</span>';
  try {
    var r = await fetch('/api/lab/import/youtube', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    });
    var d = await r.json();
    if (d.error) {
      $('yt-status').innerHTML = '<span class="c-red">' + d.error + '</span>';
      return;
    }
    $('yt-status').innerHTML = '<span class="c-yellow">Downloading & transcribing... (this takes 1-3 min)</span>';
    _ytPollTimer = setInterval(pollYTStatus, 3000);
  } catch (e) {
    $('yt-status').innerHTML = '<span class="c-red">Error: ' + e.message + '</span>';
  }
}

async function pollYTStatus() {
  try {
    var r = await fetch('/api/lab/import/status');
    var d = await r.json();
    if (d.busy) {
      $('yt-status').innerHTML = '<span class="c-yellow">Stage: ' + d.stage + '...</span>';
    } else {
      clearInterval(_ytPollTimer);
      if (d.stage === 'complete') {
        $('yt-status').innerHTML = '<span class="c-green">\u2713 Strategy imported! (ID: ' + d.strategy_id + ')</span>';
        $('yt-url').value = '';
        loadStrategies();
        loadLabStats();
      } else if (d.error) {
        $('yt-status').innerHTML = '<span class="c-red">' + d.error + '</span>';
      }
    }
  } catch (e) { /* keep polling */ }
}

// ---- IMPORT: Transcript ----
async function importTranscript() {
  var transcript = $('transcript-input').value.trim();
  if (!transcript) { $('transcript-status').innerHTML = '<span class="c-red">Paste a transcript</span>'; return; }

  $('transcript-status').innerHTML = '<span class="c-yellow">Extracting strategy rules...</span>';
  try {
    var r = await fetch('/api/lab/import/transcript', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transcript: transcript,
        source_url: $('transcript-url').value.trim()
      })
    });
    var d = await r.json();
    if (d.error) {
      $('transcript-status').innerHTML = '<span class="c-red">' + d.error + '</span>';
      return;
    }
    $('transcript-status').innerHTML = '<span class="c-green">\u2713 Strategy "' + (d.strategy ? d.strategy.name : 'imported') + '" created!</span>';
    $('transcript-input').value = '';
    loadStrategies();
    loadLabStats();
  } catch (e) {
    $('transcript-status').innerHTML = '<span class="c-red">Error: ' + e.message + '</span>';
  }
}

// ---- IMPORT: Manual JSON ----
async function importManual() {
  var raw = $('manual-json').value.trim();
  if (!raw) { $('manual-status').innerHTML = '<span class="c-red">Enter strategy JSON</span>'; return; }

  var data;
  try { data = JSON.parse(raw); } catch (e) {
    $('manual-status').innerHTML = '<span class="c-red">Invalid JSON: ' + e.message + '</span>';
    return;
  }

  try {
    var r = await fetch('/api/lab/strategies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    var d = await r.json();
    if (d.error) {
      $('manual-status').innerHTML = '<span class="c-red">' + d.error + '</span>';
      return;
    }
    $('manual-status').innerHTML = '<span class="c-green">\u2713 Strategy created (ID: ' + d.id + ')</span>';
    loadStrategies();
    loadLabStats();
  } catch (e) {
    $('manual-status').innerHTML = '<span class="c-red">Error: ' + e.message + '</span>';
  }
}

// ---- STRATEGIES LIST ----
async function loadStrategies() {
  try {
    var r = await fetch('/api/lab/strategies');
    var strategies = await r.json();
    if (!strategies.length) {
      $('strategies-panel').innerHTML = '<span class="c-muted">No strategies imported yet. Use the import panel above.</span>';
      return;
    }
    var html = '<table><thead><tr><th>Name</th><th>TF</th><th>Inst</th><th class="num">Scans</th><th class="num">Hits</th><th>Active</th><th></th></tr></thead><tbody>';
    strategies.forEach(function(s) {
      var insts = Array.isArray(s.instruments) ? s.instruments.join(',') : s.instruments;
      var activeCls = s.active ? 'c-green' : 'c-muted';
      var activeIcon = s.active ? '\u25CF ON' : '\u25CB OFF';
      var srcIcon = s.source_type === 'youtube' ? '\u25B6' : s.source_type === 'transcript' ? '\u2630' : '\u270E';
      var nameClick = 'onclick="viewStrategy(' + s.id + ')" style="cursor:pointer"';
      html += '<tr>' +
        '<td class="c-cyan" ' + nameClick + '>' + srcIcon + ' ' + s.name + '</td>' +
        '<td>' + (s.timeframe || '5m') + '</td>' +
        '<td class="c-muted" style="font-size:var(--text-xs)">' + insts + '</td>' +
        '<td class="num">' + fmtK(s.total_scans || 0) + '</td>' +
        '<td class="num c-yellow">' + (s.total_hits || 0) + '</td>' +
        '<td class="' + activeCls + '" style="cursor:pointer" onclick="toggleStrategy(' + s.id + ')">' + activeIcon + '</td>' +
        '<td><span class="c-red" style="cursor:pointer" onclick="deleteStrategy(' + s.id + ',\'' + s.name.replace(/'/g, "\\'") + '\')">\u2717</span></td>' +
        '</tr>';
      // Expanded detail row
      if (_expandedStrategyId === s.id) {
        html += '<tr><td colspan="7" class="strategy-detail-cell">' + renderStrategyDetail(s) + '</td></tr>';
      }
    });
    html += '</tbody></table>';
    $('strategies-panel').innerHTML = html;
  } catch (e) {
    $('strategies-panel').innerHTML = '<span class="c-red">[ERROR] ' + e.message + '</span>';
  }
}

// ---- STRATEGY DETAIL RENDERER ----
function viewStrategy(id) {
  _expandedStrategyId = (_expandedStrategyId === id) ? null : id;
  loadStrategies();
}

function renderStrategyDetail(s) {
  var html = '<div class="strategy-detail">';

  // Header
  html += '<div class="detail-header">';
  html += '<span class="c-bold">' + s.name + '</span>';
  if (s.source_url) {
    html += ' <span class="c-muted" style="font-size:var(--text-xs)">[' + s.source_type + ']</span>';
  }
  if (s.video_duration) {
    var mins = Math.floor(s.video_duration / 60);
    html += ' <span class="c-muted" style="font-size:var(--text-xs)">(' + mins + 'min)</span>';
  }
  html += '</div>';

  // Description + Edge
  if (s.description) {
    html += '<div class="detail-desc">' + s.description + '</div>';
  }
  var edge = s.edge_summary;
  if (typeof edge === 'string' && edge) {
    html += '<div class="detail-edge"><span class="c-yellow">\u26A1 Edge:</span> ' + edge + '</div>';
  }

  // Highlights
  var highlights = s.highlights;
  if (typeof highlights === 'string') { try { highlights = JSON.parse(highlights); } catch(e) { highlights = []; } }
  if (Array.isArray(highlights) && highlights.length) {
    html += '<div class="detail-section"><span class="c-cyan">KEY TAKEAWAYS</span></div>';
    html += '<ul class="detail-list">';
    highlights.forEach(function(h) {
      html += '<li>\u2022 ' + h + '</li>';
    });
    html += '</ul>';
  }

  // Entry Rules
  var entryRules = s.entry_rules;
  if (typeof entryRules === 'string') { try { entryRules = JSON.parse(entryRules); } catch(e) { entryRules = []; } }
  if (Array.isArray(entryRules) && entryRules.length) {
    html += '<div class="detail-section"><span class="c-green">ENTRY CONDITIONS</span> <span class="c-muted">(all must be true)</span></div>';
    html += '<div class="detail-rules">';
    entryRules.forEach(function(r, i) {
      var label = r.label || formatRule(r);
      html += '<div class="detail-rule"><span class="c-green">' + (i + 1) + '.</span> ' + label + '</div>';
    });
    html += '</div>';
  }

  // Direction Rules
  var dirRules = s.direction_rules;
  if (typeof dirRules === 'string') { try { dirRules = JSON.parse(dirRules); } catch(e) { dirRules = []; } }
  if (Array.isArray(dirRules) && dirRules.length) {
    html += '<div class="detail-section"><span class="c-blue">DIRECTION LOGIC</span></div>';
    html += '<div class="detail-rules">';
    dirRules.forEach(function(r) {
      var label = r.label || (r.direction.toUpperCase() + ' when ' + formatRule(r));
      var cls = r.direction === 'long' ? 'c-green' : 'c-red';
      html += '<div class="detail-rule"><span class="' + cls + '">\u2192 ' + r.direction.toUpperCase() + '</span> ' + label + '</div>';
    });
    html += '</div>';
  }

  // Exit Rules
  var exitRules = s.exit_rules;
  if (typeof exitRules === 'string') { try { exitRules = JSON.parse(exitRules); } catch(e) { exitRules = {}; } }
  if (exitRules && (exitRules.stop_loss || exitRules.take_profit)) {
    html += '<div class="detail-section"><span class="c-red">EXIT RULES</span></div>';
    html += '<div class="detail-rules">';
    if (exitRules.stop_loss) {
      var sl = exitRules.stop_loss;
      var slLabel = sl.label || ('Stop: ' + sl.method + (sl.multiplier ? ' ' + sl.multiplier + 'x' : '') + (sl.value ? ' ' + sl.value : ''));
      html += '<div class="detail-rule"><span class="c-red">SL</span> ' + slLabel + '</div>';
    }
    if (exitRules.take_profit) {
      var tp = exitRules.take_profit;
      var tpLabel = tp.label || ('Target: ' + tp.method + (tp.ratio ? ' ' + tp.ratio + ':1' : '') + (tp.value ? ' ' + tp.value : ''));
      html += '<div class="detail-rule"><span class="c-green">TP</span> ' + tpLabel + '</div>';
    }
    html += '</div>';
  }

  // Indicators
  var indicators = s.indicators_config;
  if (typeof indicators === 'string') { try { indicators = JSON.parse(indicators); } catch(e) { indicators = []; } }
  if (Array.isArray(indicators) && indicators.length) {
    html += '<div class="detail-section"><span class="c-magenta">INDICATORS</span></div>';
    html += '<div class="detail-indicators">';
    indicators.forEach(function(ind) {
      var p = ind.params || {};
      var label = ind.indicator;
      if (p.period) label += '(' + p.period + ')';
      else if (p.fast) label += '(' + p.fast + ',' + p.slow + ',' + (p.signal || 9) + ')';
      html += '<span class="detail-indicator-badge">' + label + '</span>';
    });
    html += '</div>';
  }

  // Transcript (collapsible)
  if (s.transcript && s.transcript.length > 10) {
    html += '<div class="detail-section detail-transcript-toggle" onclick="toggleTranscript(' + s.id + ')">';
    html += '<span class="c-muted">TRANSCRIPT</span> <span class="collapse-arrow" id="transcript-arrow-' + s.id + '">\u25B6</span>';
    html += '</div>';
    html += '<div class="detail-transcript" id="transcript-body-' + s.id + '" style="display:none">';
    html += '<div class="detail-transcript-text">' + s.transcript.substring(0, 3000) + (s.transcript.length > 3000 ? '...' : '') + '</div>';
    html += '</div>';
  }

  html += '</div>';
  return html;
}

function formatRule(r) {
  var ind = r.indicator || '?';
  var params = r.params || {};
  var cond = r.condition || '';
  var left = ind;
  if (params.period) left += '(' + params.period + ')';

  if (r.value !== undefined) {
    return left + ' ' + cond + ' ' + r.value;
  }
  if (r.reference) {
    var ref = r.reference;
    var right = ref.indicator || '?';
    if (ref.params && ref.params.period) right += '(' + ref.params.period + ')';
    return left + ' ' + cond.replace('_', ' ') + ' ' + right;
  }
  return left + ' ' + cond;
}

function toggleTranscript(id) {
  var body = document.getElementById('transcript-body-' + id);
  var arrow = document.getElementById('transcript-arrow-' + id);
  if (body.style.display === 'none') {
    body.style.display = 'block';
    if (arrow) arrow.innerHTML = '\u25BC';
  } else {
    body.style.display = 'none';
    if (arrow) arrow.innerHTML = '\u25B6';
  }
}

async function toggleStrategy(id) {
  await fetch('/api/lab/strategies/' + id + '/toggle', { method: 'POST' });
  loadStrategies();
}

async function deleteStrategy(id, name) {
  if (!confirm('Delete strategy "' + name + '" and all its scanner hits?')) return;
  await fetch('/api/lab/strategies/' + id, { method: 'DELETE' });
  _expandedStrategyId = null;
  loadStrategies();
  loadLabStats();
  loadLabAnalytics();
}

// ---- LAB STATS ----
async function loadLabStats() {
  try {
    var r = await fetch('/api/lab/stats');
    var d = await r.json();
    if (!d.total_hits && !d.active) {
      $('lab-stats').innerHTML = '<span class="c-muted">No data yet — import a strategy and let the scanner run.</span>';
      return;
    }
    $('lab-stats').innerHTML =
      '<div class="kpi"><div class="kpi-val">' + d.total_hits + '</div><div class="kpi-label">Total Hits</div></div>' +
      '<div class="kpi"><div class="kpi-val c-yellow">' + (d.active || 0) + '</div><div class="kpi-label">Active</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.closed + '</div><div class="kpi-label">Closed</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.win_rate + '%</div><div class="kpi-label">Win Rate</div></div>' +
      '<div class="kpi"><div class="kpi-val ' + pnlColor(d.total_pnl_points) + '">' + fmt(d.total_pnl_points) + '</div><div class="kpi-label">P&L (pts)</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.profit_factor + '</div><div class="kpi-label">PF</div></div>';
  } catch (e) { /* keep last */ }
}

// ---- ANALYTICS ----
async function loadLabAnalytics() {
  try {
    var r = await fetch('/api/lab/analytics');
    var data = await r.json();
    if (!data.length) {
      $('lab-analytics').innerHTML = '<span class="c-muted">No strategies to analyze</span>';
      return;
    }
    var html = '<table><thead><tr><th>Strategy</th><th class="num">Hits</th><th class="num">Win%</th><th class="num">PF</th><th class="num">P&L</th><th class="num">Avg W</th><th class="num">Avg L</th></tr></thead><tbody>';
    data.forEach(function(s) {
      var nameCls = s.active ? 'c-cyan' : 'c-muted';
      html += '<tr>' +
        '<td class="' + nameCls + '">' + s.name + '</td>' +
        '<td class="num">' + s.total_hits + '</td>' +
        '<td class="num">' + s.win_rate + '%</td>' +
        '<td class="num">' + s.profit_factor + '</td>' +
        '<td class="num ' + pnlColor(s.total_pnl_points) + '">' + fmt(s.total_pnl_points) + '</td>' +
        '<td class="num c-green">' + fmt(s.avg_win) + '</td>' +
        '<td class="num c-red">' + fmt(s.avg_loss) + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    $('lab-analytics').innerHTML = html;
  } catch (e) { /* keep last */ }
}

// ---- SCANNER FEED ----
async function loadScannerFeed() {
  try {
    var r = await fetch('/api/lab/scanner/hits?limit=50');
    var hits = await r.json();
    if (!hits.length) {
      $('scanner-feed').innerHTML = '<span class="c-muted">No scanner hits yet. Import a strategy and wait for conditions to match.</span>';
      updateSimTabs([]);
      return;
    }
    var html = '<table><thead><tr><th>Time</th><th>Strategy</th><th>Inst</th><th>Dir</th><th class="num">Entry</th><th class="num">SL</th><th class="num">TP</th><th>Status</th><th class="num">P&L</th></tr></thead><tbody>';
    hits.forEach(function(h) {
      var ts = h.timestamp ? h.timestamp.substring(11, 19) : '--';
      var dirCls = h.direction === 'long' ? 'c-green' : 'c-red';
      var statusCls = h.status === 'won' ? 'c-green' : h.status === 'lost' ? 'c-red' : h.status === 'simulating' ? 'c-yellow' : 'c-muted';
      var statusIcon = h.status === 'won' ? '\u2713' : h.status === 'lost' ? '\u2717' : h.status === 'simulating' ? '\u25CF' : '\u2014';
      html += '<tr>' +
        '<td class="c-muted">' + ts + '</td>' +
        '<td style="font-size:var(--text-xs)">' + (h.strategy_name || 'ID:' + h.strategy_id) + '</td>' +
        '<td class="c-cyan">' + h.instrument + '</td>' +
        '<td class="' + dirCls + '">' + h.direction.toUpperCase() + '</td>' +
        '<td class="num">' + (h.entry_price ? h.entry_price.toFixed(2) : '--') + '</td>' +
        '<td class="num c-red">' + (h.stop_loss ? h.stop_loss.toFixed(2) : '--') + '</td>' +
        '<td class="num c-green">' + (h.take_profit ? h.take_profit.toFixed(2) : '--') + '</td>' +
        '<td class="' + statusCls + '">' + statusIcon + ' ' + h.status + '</td>' +
        '<td class="num ' + pnlColor(h.pnl_points) + '">' + (h.pnl_points != null ? fmt(h.pnl_points) : '--') + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    $('scanner-feed').innerHTML = html;
    updateSimTabs(hits);
  } catch (e) {
    $('scanner-feed').innerHTML = '<span class="c-red">[ERROR] ' + e.message + '</span>';
  }
}

function updateSimTabs(hits) {
  var won = hits.filter(function(h) { return h.status === 'won'; });
  var lost = hits.filter(function(h) { return h.status === 'lost'; });
  var closed = hits.filter(function(h) { return h.status !== 'simulating' && h.status !== 'detected'; });

  var makeTable = function(data) {
    if (!data.length) return '<span class="c-muted">None</span>';
    var h = '<table><thead><tr><th>Strategy</th><th>Inst</th><th>Dir</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">P&L</th><th class="num">MAE</th><th class="num">MFE</th><th class="num">Bars</th></tr></thead><tbody>';
    data.forEach(function(s) {
      var dirCls = s.direction === 'long' ? 'c-green' : 'c-red';
      h += '<tr>' +
        '<td style="font-size:var(--text-xs)">' + (s.strategy_name || '--') + '</td>' +
        '<td class="c-cyan">' + s.instrument + '</td>' +
        '<td class="' + dirCls + '">' + s.direction.toUpperCase() + '</td>' +
        '<td class="num">' + (s.entry_price ? s.entry_price.toFixed(2) : '--') + '</td>' +
        '<td class="num">' + (s.exit_price ? s.exit_price.toFixed(2) : '--') + '</td>' +
        '<td class="num ' + pnlColor(s.pnl_points) + '">' + (s.pnl_points != null ? fmt(s.pnl_points) : '--') + '</td>' +
        '<td class="num c-red">' + (s.mae_points ? s.mae_points.toFixed(2) : '0') + '</td>' +
        '<td class="num c-green">' + (s.mfe_points ? s.mfe_points.toFixed(2) : '0') + '</td>' +
        '<td class="num">' + (s.bars_held || 0) + '</td>' +
        '</tr>';
    });
    return h + '</tbody></table>';
  };

  $('sim-all').innerHTML = makeTable(closed);
  $('sim-won').innerHTML = makeTable(won);
  $('sim-lost').innerHTML = makeTable(lost);
}

// ---- ACTIVE SIMULATIONS ----
async function loadActiveSims() {
  try {
    var r = await fetch('/api/lab/scanner/active');
    var data = await r.json();
    if (!data.length) {
      $('active-sims').innerHTML = '<span class="c-muted">No active simulations</span>';
      return;
    }
    var html = '<table><thead><tr><th>Inst</th><th>Dir</th><th class="num">Entry</th><th class="num">SL</th><th class="num">TP</th><th class="num">MAE</th><th class="num">MFE</th><th class="num">Bars</th></tr></thead><tbody>';
    data.forEach(function(s) {
      var dirCls = s.direction === 'long' ? 'c-green' : 'c-red';
      html += '<tr>' +
        '<td class="c-cyan">' + s.instrument + '</td>' +
        '<td class="' + dirCls + '">' + s.direction.toUpperCase() + '</td>' +
        '<td class="num">' + s.entry_price.toFixed(2) + '</td>' +
        '<td class="num c-red">' + (s.stop_loss ? s.stop_loss.toFixed(2) : '--') + '</td>' +
        '<td class="num c-green">' + (s.take_profit ? s.take_profit.toFixed(2) : '--') + '</td>' +
        '<td class="num c-red">' + (s.mae_points ? s.mae_points.toFixed(2) : '0') + '</td>' +
        '<td class="num c-green">' + (s.mfe_points ? s.mfe_points.toFixed(2) : '0') + '</td>' +
        '<td class="num">' + (s.bars_held || 0) + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    $('active-sims').innerHTML = html;
  } catch (e) { /* keep last */ }
}

// ---- PERIODIC REFRESH ----
setInterval(function() {
  if (document.getElementById('view-lab').classList.contains('active')) {
    loadScannerFeed();
    loadActiveSims();
    loadLabStats();
  }
}, 30000);
