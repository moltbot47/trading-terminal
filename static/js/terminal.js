/* Trading Terminal v3 -- Client-side logic */
/* global LightweightCharts */

const $ = id => document.getElementById(id);
const pnlColor = v => v > 0 ? 'c-green' : v < 0 ? 'c-red' : 'c-muted';
const fmt = v => v != null ? (v > 0 ? '+' : '') + v.toFixed(2) : '--';
const fmtK = v => Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'K' : v.toFixed(0);

// Top-level view switching (Terminal / Strategy Lab)
function switchMainView(view, el) {
  document.querySelectorAll('.main-view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.top-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('view-' + view).classList.add('active');
  el.classList.add('active');
  // Trigger lab data load when switching to lab
  if (view === 'lab' && typeof refreshLab === 'function') refreshLab();
  if (view === 'backtester' && typeof refreshBacktester === 'function') refreshBacktester();
}

function adxBar(val) {
  const pct = Math.min(val / 50 * 100, 100);
  const cls = val < 20 ? 'adx-low' : val < 30 ? 'adx-mid' : 'adx-high';
  return '<span class="adx-bar"><span class="adx-fill ' + cls + '" style="width:' + pct + '%"></span></span> ' + val.toFixed(1);
}

function blockBar(v, max, w) {
  if (typeof w === 'undefined') w = 15;
  var ratio = max > 0 ? Math.max(0, Math.min(1, v / max)) : 0;
  var f = Math.round(ratio * w), e = w - f;
  var c = ratio > 0.8 ? 'c-green' : ratio > 0.5 ? 'c-yellow' : 'c-red';
  return '<span class="' + c + '">' + '\u2588'.repeat(f) + '</span><span class="c-muted">' + '\u2591'.repeat(e) + '</span>';
}

function togglePanel(titleEl) {
  var body = titleEl.nextElementSibling;
  var arrow = titleEl.querySelector('.collapse-arrow');
  body.classList.toggle('collapsed');
  if (arrow) arrow.classList.toggle('open');
}

function switchTab(group, el, showId) {
  document.querySelectorAll('.' + group).forEach(function(t) { t.classList.remove('active'); });
  el.parentElement.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
  el.classList.add('active');
  document.getElementById(showId).classList.add('active');
}

// ---- LIVE PRICES --------------------------------------------------------
var prevPrices = {};
var regimeBadgeCache = {};  // BUG-005 fix: persist regime badges across ticker rebuilds

// ---- REGIME / ADX -------------------------------------------------------
async function loadRegime() {
  try {
    var r = await fetch('/api/regime');
    var data = await r.json();
    var order = ['MNQ', 'MYM', 'MES', 'MBT'];
    var html = '<table><thead><tr><th>Instrument</th><th>Regime</th><th>ADX</th><th>Vol %ile</th><th>VIX</th><th>Size Mult</th></tr></thead><tbody>';
    for (var i = 0; i < order.length; i++) {
      var sym = order[i];
      var d = data[sym];
      if (!d) { html += '<tr><td class="c-cyan">' + sym + '</td><td colspan="5" class="c-muted">No data</td></tr>'; continue; }
      var regime = d.regime || 'unknown';
      var adx = d.adx || 0;
      var volPct = d.realized_vol_pctile != null ? d.realized_vol_pctile : 0;
      var sizeMult = d.size_multiplier != null ? d.size_multiplier : 1;
      var regimeCls = regime === 'trending' ? 'regime-trending' : regime === 'volatile' ? 'regime-volatile' : 'regime-ranging';
      var regimeIcon = regime === 'trending' ? '\u2197' : regime === 'volatile' ? '\u26A1' : '\u2194';
      var volCls = volPct > 80 ? 'c-red' : volPct > 50 ? 'c-yellow' : 'c-green';
      var sizeCls = sizeMult >= 0.8 ? 'c-green' : sizeMult >= 0.6 ? 'c-yellow' : 'c-red';
      html += '<tr>' +
        '<td class="c-cyan c-bold">' + sym + '</td>' +
        '<td class="' + regimeCls + '">' + regimeIcon + ' ' + regime.toUpperCase() + '</td>' +
        '<td class="num">' + adxBar(adx) + '</td>' +
        '<td class="num ' + volCls + '">' + volPct.toFixed(0) + '%</td>' +
        '<td class="num">' + (d.vix_level != null ? d.vix_level.toFixed(1) : '--') + '</td>' +
        '<td class="num ' + sizeCls + '">' + sizeMult.toFixed(2) + 'x</td>' +
        '</tr>';

      // Update ticker badge + cache it (BUG-005 fix)
      var badgeHtml = '<span class="' + regimeCls + '">' + regimeIcon + regime + '</span>';
      regimeBadgeCache[sym] = badgeHtml;
      var badge = document.getElementById('regime-badge-' + sym);
      if (badge) badge.innerHTML = badgeHtml;
    }
    html += '</tbody></table>';
    $('regime-panel').innerHTML = html;
  } catch (e) { $('regime-panel').innerHTML = '<span class="c-red">[ERROR] ' + e.message + '</span>'; }
}

// ---- NEWS CALENDAR (BUG-011 fix: operator precedence) --------------------
async function loadNews() {
  try {
    var r = await fetch('/api/news');
    var events = await r.json();
    if (!events.length) { $('news-panel').innerHTML = '<span class="c-green">\u2713 No upcoming High/Medium events</span>'; return; }
    var html = '<table><thead><tr><th>Impact</th><th>Event</th><th>When</th><th>Fcst</th><th>Prev</th></tr></thead><tbody>';
    events.slice(0, 12).forEach(function(ev) {
      var impCls = ev.impact === 'High' ? 'news-high' : 'news-medium';
      var mins = ev.minutes_away;
      var isPast = mins < 0;
      // BUG-011 fix: added parentheses for correct operator precedence
      var isNear = (Math.abs(mins) <= 15 && ev.impact === 'High') || (Math.abs(mins) <= 10 && ev.impact === 'Medium');
      var rowCls = isNear ? 'news-upcoming' : isPast ? 'news-past' : '';
      var when;
      if (Math.abs(mins) < 60) when = (mins > 0 ? 'in ' : '') + Math.abs(mins).toFixed(0) + 'm' + (mins < 0 ? ' ago' : '');
      else if (Math.abs(mins) < 1440) when = (mins / 60).toFixed(1) + 'h';
      else when = (mins / 1440).toFixed(0) + 'd';
      var blockIcon = isNear ? ' <span class="c-red">[BLOCKED]</span>' : '';
      html += '<tr class="' + rowCls + '"><td class="' + impCls + '">' + (ev.impact === 'High' ? '\u25CF\u25CF\u25CF' : '\u25CF\u25CF\u25CB') + '</td>' +
        '<td>' + ev.title + blockIcon + '</td><td class="num">' + when + '</td>' +
        '<td class="num c-muted">' + (ev.forecast || '--') + '</td><td class="num c-muted">' + (ev.previous || '--') + '</td></tr>';
    });
    html += '</tbody></table>';
    $('news-panel').innerHTML = html;
  } catch (e) { $('news-panel').innerHTML = '<span class="c-red">[ERROR] ' + e.message + '</span>'; }
}

// ---- SYSTEM HEALTH -------------------------------------------------------
async function loadHealth() {
  try {
    var r = await fetch('/api/health');
    var d = await r.json();
    var hb = d.heartbeat || {};
    var dd = d.drawdown || {};
    var sys = d.system || {};

    var lastCycle = hb.timestamp ? new Date(hb.timestamp) : null;
    var ageSec = lastCycle ? (Date.now() - lastCycle.getTime()) / 1000 : 99999;
    var alive = ageSec < 600;
    var statusIcon = alive ? '<span class="c-green">\u25CF ALIVE</span>' : '<span class="c-red">\u25CF OFFLINE</span>';

    var cushion = (dd.highest_balance || 50000) - (dd.drawdown_floor || 47500);
    var maxDD = 2000;
    var cushionPct = (cushion / maxDD * 100).toFixed(0);

    var html = '<table>' +
      '<tr><td class="c-muted">System</td><td>' + statusIcon + ' <span class="c-muted">(cycle ' + (hb.cycle || '--') + ')</span></td>' +
          '<td class="c-muted">Last Beat</td><td>' + (hb.timestamp ? hb.timestamp.substring(5, 19) : '--') + '</td></tr>' +
      '<tr><td class="c-muted">Drawdown</td><td>' + blockBar(cushion, maxDD) + ' <span class="c-muted">' + cushionPct + '%</span></td>' +
          '<td class="c-muted">Floor</td><td>$' + (dd.drawdown_floor || 0).toLocaleString() + '</td></tr>' +
      '<tr><td class="c-muted">Peak Bal</td><td>$' + (dd.highest_balance || 0).toLocaleString() + '</td>' +
          '<td class="c-muted">Daily P&L</td><td class="' + pnlColor(sys.daily_pnl || 0) + '">' + fmt(sys.daily_pnl || 0) + '</td></tr>' +
      '</table>';
    $('health-panel').innerHTML = html;
  } catch (e) { $('health-panel').innerHTML = '<span class="c-red">[ERROR] ' + e.message + '</span>'; }
}

// ---- OPEN POSITIONS ------------------------------------------------------
async function loadPositions() {
  try {
    var r = await fetch('/api/positions');
    var d = await r.json();
    var all = [].concat(d.latpfn || [], d.trend_follower || []);
    if (!all.length) { $('positions-panel').innerHTML = '<span class="c-muted">No open positions</span>'; return; }
    var html = '<table><thead><tr><th>Inst</th><th>Dir</th><th class="num">Size</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">Target</th></tr></thead><tbody>';
    all.forEach(function(p) {
      html += '<tr>' +
        '<td class="c-cyan">' + (p.instrument || '--') + '</td>' +
        '<td class="' + (p.direction === 'long' ? 'c-green' : 'c-red') + '">' + (p.direction || '').toUpperCase() + '</td>' +
        '<td class="num">' + (p.size || p.position_size || '--') + '</td>' +
        '<td class="num">' + (p.entry_price || '--') + '</td>' +
        '<td class="num c-red">' + (p.stop_loss || p.current_stop || p.initial_stop || '--') + '</td>' +
        '<td class="num c-green">' + (p.take_profit || '--') + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    $('positions-panel').innerHTML = html;
  } catch (e) { $('positions-panel').innerHTML = '<span class="c-red">[ERROR] ' + e.message + '</span>'; }
}

// ---- BROKER STATS & TRADES -----------------------------------------------
async function loadBrokerStats() {
  try {
    var r = await fetch('/api/broker-stats');
    var d = await r.json();
    if (!d.total_trades) { $('broker-kpis').innerHTML = '<span class="c-muted">No broker data</span>'; return; }
    $('broker-kpis').innerHTML =
      '<div class="kpi"><div class="kpi-val ' + pnlColor(d.total_pnl) + '">' + fmt(d.total_pnl) + '</div><div class="kpi-label">Total P&L</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.total_trades + '</div><div class="kpi-label">Trades</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.win_rate + '%</div><div class="kpi-label">Win Rate</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.profit_factor + '</div><div class="kpi-label">PF</div></div>' +
      '<div class="kpi"><div class="kpi-val c-green">' + fmt(d.avg_win) + '</div><div class="kpi-label">Avg Win</div></div>' +
      '<div class="kpi"><div class="kpi-val c-red">' + fmt(-d.avg_loss) + '</div><div class="kpi-label">Avg Loss</div></div>';
  } catch (e) { $('broker-kpis').innerHTML = '<span class="c-red">[ERROR]</span>'; }
}

async function loadBrokerTrades() {
  try {
    var r = await fetch('/api/broker-trades');
    var rows = await r.json();
    if (!rows.length) { $('broker-trades').innerHTML = '<span class="c-muted">No trades</span>'; return; }
    var html = '<table><thead><tr><th>Time</th><th>Sym</th><th>Side</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">P&L</th></tr></thead><tbody>';
    rows.forEach(function(t) {
      var ts = t.timestamp ? t.timestamp.split(' ').pop().substring(0, 8) : '--';
      html += '<tr><td class="c-muted">' + ts + '</td><td class="c-cyan">' + (t.raw_symbol || t.instrument) + '</td>' +
        '<td class="' + (t.direction === 'long' ? 'c-green' : 'c-red') + '">' + (t.direction || '').toUpperCase() + '</td>' +
        '<td class="num">' + t.quantity + '</td><td class="num">' + t.entry_price + '</td><td class="num">' + t.exit_price + '</td>' +
        '<td class="num ' + pnlColor(t.pnl) + '">' + fmt(t.pnl) + '</td></tr>';
    });
    html += '</tbody></table>';
    $('broker-trades').innerHTML = html;
  } catch (e) { /* keep last */ }
}

// ---- PREDICTIONS ---------------------------------------------------------
async function loadPredictions() {
  try {
    var r = await fetch('/api/predictions-recent');
    var rows = await r.json();
    if (!rows.length) { $('predictions').innerHTML = '<span class="c-muted">No predictions</span>'; return; }
    var html = '<table><thead><tr><th>Time</th><th>Inst</th><th>Dir</th><th class="num">Conf</th><th>Regime</th><th>Tier</th><th class="num">Price</th><th class="num">Target</th><th>Sig</th></tr></thead><tbody>';
    rows.forEach(function(p) {
      var ts = p.timestamp ? p.timestamp.substring(11, 19) : '--';
      var sig = p.signal_generated ? '<span class="c-green">\u2713</span>' : '<span class="c-muted">\u00B7</span>';
      var confCls = p.composite_confidence > 0.5 ? 'c-green' : p.composite_confidence > 0.3 ? 'c-yellow' : 'c-muted';
      html += '<tr><td class="c-muted">' + ts + '</td><td class="c-cyan">' + p.instrument + '</td>' +
        '<td class="' + (p.direction === 'long' ? 'c-green' : 'c-red') + '">' + p.direction + '</td>' +
        '<td class="num ' + confCls + '">' + (p.composite_confidence * 100).toFixed(1) + '%</td>' +
        '<td class="' + (p.regime === 'volatile' ? 'c-yellow' : p.regime === 'trending' ? 'c-green' : 'c-muted') + '">' + (p.regime || '--') + '</td>' +
        '<td>' + (p.shot_tier === 'no_trade' ? '<span class="c-muted">--</span>' : p.shot_tier) + '</td>' +
        '<td class="num">' + (p.current_price ? p.current_price.toFixed(2) : '--') + '</td>' +
        '<td class="num">' + (p.forecast_end_price ? p.forecast_end_price.toFixed(2) : '--') + '</td>' +
        '<td>' + sig + '</td></tr>';
    });
    html += '</tbody></table>';
    $('predictions').innerHTML = html;
  } catch (e) { /* keep last */ }
}

// ---- TURBO ---------------------------------------------------------------
async function loadTurboStats() {
  try {
    var r = await fetch('/api/turbo-stats');
    var d = await r.json();
    if (!d.total_signals) { $('turbo-kpis').innerHTML = '<span class="c-muted">No turbo data</span>'; return; }
    var assetHtml = '';
    (d.assets || []).forEach(function(a) {
      assetHtml += '<div class="kpi"><div class="kpi-val" style="font-size:0.8rem">' + a.asset.toUpperCase() + '</div><div class="kpi-label">' + a.trades + 't / ' + fmt(a.pnl) + '</div></div>';
    });
    $('turbo-kpis').innerHTML =
      '<div class="kpi"><div class="kpi-val">' + fmtK(d.total_signals) + '</div><div class="kpi-label">Signals</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.traded + '</div><div class="kpi-label">Traded</div></div>' +
      '<div class="kpi"><div class="kpi-val">' + d.win_rate + '%</div><div class="kpi-label">Win Rate</div></div>' +
      '<div class="kpi"><div class="kpi-val ' + pnlColor(d.total_pnl) + '">' + fmt(d.total_pnl) + '</div><div class="kpi-label">P&L</div></div>' +
      assetHtml;
  } catch (e) { /* keep last */ }
}

async function loadTurboSignals() {
  try {
    var r = await fetch('/api/turbo-signals');
    var rows = await r.json();
    var makeTable = function(data) {
      var h = '<table><thead><tr><th>Time</th><th>Asset</th><th>TF</th><th class="num">Price</th><th class="num">Mom</th><th>Dir</th><th>Signal</th><th class="num">P&L</th></tr></thead><tbody>';
      data.forEach(function(s) {
        var ts = s.timestamp ? s.timestamp.substring(11, 19) : '--';
        var dir = s.momentum_direction || '--';
        var sig = s.signal_generated ? '<span class="' + (s.signal_direction === 'up' ? 'c-green' : 'c-red') + '">' + s.signal_direction + '</span>' : '<span class="c-muted">' + (s.skip_reason || '--').substring(0, 12) + '</span>';
        h += '<tr><td class="c-muted">' + ts + '</td><td class="c-cyan">' + s.asset.toUpperCase() + '</td>' +
          '<td>' + s.timeframe + '</td><td class="num">' + (s.crypto_price ? s.crypto_price.toFixed(2) : '--') + '</td>' +
          '<td class="num">' + (s.momentum_strength ? s.momentum_strength.toFixed(3) : '0') + '</td>' +
          '<td class="' + (dir === 'up' ? 'c-green' : dir === 'down' ? 'c-red' : 'c-muted') + '">' + (dir || '--') + '</td>' +
          '<td>' + sig + '</td><td class="num ' + pnlColor(s.pnl) + '">' + (s.traded ? fmt(s.pnl) : '<span class="c-muted">--</span>') + '</td></tr>';
      });
      return h + '</tbody></table>';
    };
    $('turbo-all').innerHTML = makeTable(rows);
    $('turbo-traded').innerHTML = makeTable(rows.filter(function(s) { return s.traded; }));
  } catch (e) { /* keep last */ }
}

// ---- POLYMARKET ----------------------------------------------------------
async function loadPolymarket() {
  try {
    var r = await fetch('/api/polymarket-forecasts');
    var rows = await r.json();
    if (!rows.length) { $('polymarket').innerHTML = '<span class="c-muted">No forecasts</span>'; return; }
    var html = '<table><thead><tr><th style="max-width:280px">Question</th><th class="num">LLM%</th><th class="num">Mkt%</th><th class="num">Edge</th></tr></thead><tbody>';
    rows.slice(0, 10).forEach(function(f) {
      var q = f.question.length > 45 ? f.question.substring(0, 45) + '\u2026' : f.question;
      var llm = (f.llm_probability * 100).toFixed(0);
      var mkt = (f.market_price * 100).toFixed(1);
      var edge = ((f.llm_probability - f.market_price) * 100).toFixed(1);
      var edgeColor = Math.abs(edge) > 10 ? 'c-yellow' : 'c-muted';
      html += '<tr><td style="max-width:280px;overflow:hidden;text-overflow:ellipsis">' + q + '</td>' +
        '<td class="num c-cyan">' + llm + '%</td><td class="num">' + mkt + '%</td>' +
        '<td class="num ' + edgeColor + '">' + (edge > 0 ? '+' : '') + edge + '%</td></tr>';
    });
    html += '</tbody></table>';
    $('polymarket').innerHTML = html;
  } catch (e) { /* keep last */ }
}

// ---- CLOCK & REFRESH -----------------------------------------------------
function updateClock() {
  $('clock').textContent = new Date().toLocaleTimeString('en-US', { hour12: false });
}

// Fast data (5 sec): prices
async function refreshFast() {
  await loadPrices();
}

// Medium data (30 sec): regime, positions, predictions
async function refreshMedium() {
  await Promise.all([loadRegime(), loadPositions(), loadPredictions()]);
}

// Slow data (60 sec): news, health, broker, turbo, polymarket
async function refreshSlow() {
  $('status-msg').textContent = 'Refreshing...';
  $('status-msg').className = 'c-yellow';
  await Promise.all([loadNews(), loadHealth(), loadBrokerStats(), loadBrokerTrades(), loadTurboStats(), loadTurboSignals(), loadPolymarket()]);
  $('status-msg').textContent = 'Connected';
  $('status-msg').className = 'c-green';
}

// Initial load
updateClock();
setInterval(updateClock, 1000);

// Staggered refresh
refreshFast();
refreshMedium();
refreshSlow();

setInterval(refreshFast, 5000);     // prices every 5s
setInterval(refreshMedium, 30000);  // regime every 30s
setInterval(refreshSlow, 60000);    // everything else every 60s

// ---- CANDLESTICK CHART (TradingView Lightweight Charts) ------------------
var tvChart = null;
var candleSeries = null;
var currentChartSymbol = 'MNQ';
var chartGeneration = 0;  // BUG-003 fix: discard stale fetch responses
var lastCandleData = {};  // BUG-015 fix: track last candle data per symbol for update()

function initChart() {
  var container = $('chart-container');
  if (tvChart) { tvChart.remove(); }
  tvChart = LightweightCharts.createChart(container, {
    layout: { textColor: '#cccccc', background: { type: 'solid', color: '#0c0c0c' }, fontFamily: 'JetBrains Mono, monospace', fontSize: 10 },
    grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#767676' },
    timeScale: { borderColor: '#767676', timeVisible: true, secondsVisible: false },
  });
  candleSeries = tvChart.addCandlestickSeries({
    upColor: '#16c60c', downColor: '#e74856', borderUpColor: '#16c60c', borderDownColor: '#e74856',
    wickUpColor: '#16c60c', wickDownColor: '#e74856',
  });
}

async function loadChart(symbol) {
  if (!tvChart) initChart();
  var gen = ++chartGeneration;
  try {
    var r = await fetch('/api/candles/' + symbol);
    var data = await r.json();
    if (gen !== chartGeneration) return;  // stale response -- discard
    if (Array.isArray(data) && data.length) {
      var prevData = lastCandleData[symbol];
      // BUG-015 fix: if we have previous data for this symbol, try update() for last candle
      if (prevData && prevData.length > 0 && data.length > 0) {
        var prevLast = prevData[prevData.length - 1];
        var newLast = data[data.length - 1];
        // If the data up to the last candle is identical, just update the last candle
        if (data.length === prevData.length && prevLast.time === newLast.time) {
          candleSeries.update(newLast);
          lastCandleData[symbol] = data;
          return;
        }
      }
      // Full refresh needed (new candles arrived or first load)
      candleSeries.setData(data);
      tvChart.timeScale().fitContent();
      lastCandleData[symbol] = data;
    }
  } catch (e) { console.error('Chart load error:', e); }
}

function switchChart(symbol, el) {
  currentChartSymbol = symbol;
  if (el) {
    el.parentElement.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
    el.classList.add('active');
  }
  // Clear previous data so next load does full setData for new symbol
  lastCandleData[symbol] = null;
  loadChart(symbol);
}

// Init chart
initChart();
loadChart('MNQ');
// Refresh chart every 60s
setInterval(function() { loadChart(currentChartSymbol); }, 60000);

// ---- LIVE PRICE CONSOLE (BUG-016 fix: append only) ----------------------
var priceLog = [];
var lastPriceData = null;
var _consoleLineCount = 0;  // Track how many lines are in the DOM

async function loadPrices() {
  try {
    var r = await fetch('/api/prices');
    var data = await r.json();
    lastPriceData = data;

    var order = ['MNQ', 'MYM', 'MES', 'MBT'];
    var labels = { MNQ: 'Micro Nasdaq', MYM: 'Micro Dow', MES: 'Micro S&P', MBT: 'Micro Bitcoin' };
    var html = '';
    // BUG-002 fix: compute changes from saved prev prices BEFORE overwriting
    var changes = {};
    for (var i = 0; i < order.length; i++) {
      var sym = order[i];
      var d = data[sym];
      if (!d || isNaN(d.price)) { changes[sym] = { chg: 0, cls: 'c-muted', arrow: '\u2500' }; continue; }
      var prev = prevPrices[sym] || d.price;
      var chg = d.price - prev;
      changes[sym] = {
        chg: chg, prev: prev,
        arrow: chg > 0 ? '\u25B2' : chg < 0 ? '\u25BC' : '\u2500',
        cls: chg > 0 ? 'c-green' : chg < 0 ? 'c-red' : 'c-muted',
        consoleCls: d.price > prev ? 'c-green' : d.price < prev ? 'c-red' : 'c-cyan'
      };
      prevPrices[sym] = d.price;
    }

    // Build ticker strip
    for (var j = 0; j < order.length; j++) {
      var sym2 = order[j];
      var d2 = data[sym2];
      if (!d2 || isNaN(d2.price)) {
        html += '<div class="ticker"><span class="ticker-sym">' + sym2 + '</span><br><span class="c-muted">--</span></div>';
        continue;
      }
      var c = changes[sym2];
      // Stale detection: if price timestamp is older than 120s, show warning
      var priceAge = d2.timestamp ? (Date.now() / 1000 - d2.timestamp) : 0;
      var staleTag = priceAge > 120 ? ' <span class="c-yellow" style="font-size:var(--text-xs)">[STALE]</span>' : '';
      html += '<div class="ticker">' +
        '<span class="ticker-sym">' + sym2 + '</span> <span class="c-muted" style="font-size:var(--text-xs)">' + (labels[sym2] || '') + '</span>' + staleTag + '<br>' +
        '<span class="ticker-price ' + c.cls + '">' + d2.price.toLocaleString('en-US', { minimumFractionDigits: 2 }) + '</span>' +
        ' <span class="' + c.cls + '" style="font-size:var(--text-xs)">' + c.arrow + ' ' + (c.chg >= 0 ? '+' : '') + c.chg.toFixed(2) + '</span><br>' +
        '<span class="ticker-hl">H:' + d2.high.toFixed(2) + ' L:' + d2.low.toFixed(2) + '</span>' +
        '<span id="regime-badge-' + sym2 + '" class="ticker-regime"></span></div>';
    }
    $('ticker-strip').innerHTML = html;

    // BUG-005 fix: re-apply cached regime badges after innerHTML rebuild
    for (var k = 0; k < order.length; k++) {
      var sym3 = order[k];
      if (regimeBadgeCache[sym3]) {
        var badge = document.getElementById('regime-badge-' + sym3);
        if (badge) badge.innerHTML = regimeBadgeCache[sym3];
      }
    }

    // Update tab title with lead instrument price
    var lead = data['MNQ'] || data['MYM'] || data['MES'];
    if (lead) document.title = 'MNQ ' + lead.price.toLocaleString('en-US', { minimumFractionDigits: 2 }) + ' \u2014 Trading Terminal';

    // BUG-016 fix: Append new line instead of rebuilding all innerHTML
    var now = new Date().toLocaleTimeString('en-US', { hour12: false });
    var line = '<span class="c-muted">[' + now + ']</span> ';
    var hasAny = false;
    for (var m = 0; m < order.length; m++) {
      var sym4 = order[m];
      var d4 = data[sym4];
      if (!d4 || isNaN(d4.price)) { line += '<span class="c-muted">' + sym4 + ':--</span>  '; continue; }
      hasAny = true;
      var c4 = changes[sym4];
      line += '<span class="c-cyan">' + sym4 + '</span>:<span class="' + c4.consoleCls + '">' + d4.price.toLocaleString('en-US', { minimumFractionDigits: 2 }) + '</span>  ';
    }
    if (hasAny || priceLog.length === 0) {
      priceLog.push(line);
      var consoleEl = $('price-console');
      // Append instead of full rebuild
      if (_consoleLineCount > 0) {
        var br = document.createElement('br');
        consoleEl.appendChild(br);
      }
      var span = document.createElement('span');
      span.innerHTML = line;
      consoleEl.appendChild(span);
      _consoleLineCount++;

      // Trim old entries if over 60
      if (priceLog.length > 60) {
        priceLog.shift();
        // Remove first child (line) and its <br> if present
        if (consoleEl.firstChild) consoleEl.removeChild(consoleEl.firstChild);
        if (consoleEl.firstChild && consoleEl.firstChild.tagName === 'BR') consoleEl.removeChild(consoleEl.firstChild);
        _consoleLineCount--;
      }
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  } catch (e) { /* keep last */ }
}
