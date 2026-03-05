/* Backtester -- Client-side logic for historical strategy backtesting */

// ---- STATE ----
var _btChart = null;
var _btLoaded = false;

function refreshBacktester() {
  // Set default dates: 1 year ago -> today
  var today = new Date();
  var yearAgo = new Date();
  yearAgo.setFullYear(today.getFullYear() - 1);
  var endInput = $('bt-end-date');
  var startInput = $('bt-start-date');
  if (endInput && !endInput.value) endInput.value = formatDateInput(today);
  if (startInput && !startInput.value) startInput.value = formatDateInput(yearAgo);

  loadBtStrategies();
  loadBtHistory();
  _btLoaded = true;
}

function formatDateInput(d) {
  var mm = String(d.getMonth() + 1).padStart(2, '0');
  var dd = String(d.getDate()).padStart(2, '0');
  return d.getFullYear() + '-' + mm + '-' + dd;
}

// ---- STRATEGIES DROPDOWN ----
async function loadBtStrategies() {
  try {
    var r = await fetch('/api/lab/strategies');
    var strategies = await r.json();
    var sel = $('bt-strategy');
    if (!sel) return;
    var current = sel.value;
    sel.innerHTML = '<option value="">-- Select Strategy --</option>';
    strategies.forEach(function(s) {
      var opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name + ' (' + (s.timeframe || '5m') + ')';
      sel.appendChild(opt);
    });
    if (current) sel.value = current;
  } catch (e) {
    console.error('Failed to load strategies for backtester:', e);
  }
}

// ---- RUN BACKTEST ----
async function runBacktest() {
  var strategyId = $('bt-strategy').value;
  var symbol = $('bt-symbol').value;
  var startDate = $('bt-start-date').value;
  var endDate = $('bt-end-date').value;

  if (!strategyId) {
    $('bt-status').innerHTML = '<span class="c-red">Select a strategy</span>';
    return;
  }
  if (!symbol) {
    $('bt-status').innerHTML = '<span class="c-red">Select a symbol</span>';
    return;
  }

  var btn = $('bt-run-btn');
  btn.disabled = true;
  btn.textContent = 'Running...';
  $('bt-status').innerHTML = '<span class="c-yellow">Running backtest...</span>';

  try {
    var r = await fetch('/api/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy_id: parseInt(strategyId),
        symbol: symbol,
        start_date: startDate,
        end_date: endDate
      })
    });
    var data = await r.json();
    if (data.error) {
      $('bt-status').innerHTML = '<span class="c-red">' + data.error + '</span>';
    } else {
      $('bt-status').innerHTML = '<span class="c-green">\u2713 Backtest complete</span>';
      displayBtResults(data);
      loadBtHistory();
    }
  } catch (e) {
    $('bt-status').innerHTML = '<span class="c-red">Error: ' + e.message + '</span>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
  }
}

// ---- DISPLAY RESULTS ----
function displayBtResults(data) {
  renderBtSummary(data);
  renderEquityCurve(data.equity_curve || []);
  renderFrequency(data.trades || []);
  renderTradeLog(data.trades || []);
}

function renderBtSummary(data) {
  var el = $('bt-summary');
  if (!el) return;

  var totalTrades = data.total_trades || 0;
  var winRate = data.win_rate != null ? data.win_rate : 0;
  var totalPnl = data.total_pnl != null ? data.total_pnl : 0;
  var pf = data.profit_factor != null ? data.profit_factor : 0;
  var maxDD = data.max_drawdown != null ? data.max_drawdown : 0;
  var tradesPerDay = data.trades_per_day != null ? data.trades_per_day : 0;

  el.innerHTML =
    '<div class="kpi"><div class="kpi-val">' + totalTrades + '</div><div class="kpi-label">Total Trades</div></div>' +
    '<div class="kpi"><div class="kpi-val">' + winRate.toFixed(1) + '%</div><div class="kpi-label">Win Rate</div></div>' +
    '<div class="kpi"><div class="kpi-val ' + pnlColor(totalPnl) + '">' + fmt(totalPnl) + '</div><div class="kpi-label">Total P&L</div></div>' +
    '<div class="kpi"><div class="kpi-val">' + pf.toFixed(2) + '</div><div class="kpi-label">Profit Factor</div></div>' +
    '<div class="kpi"><div class="kpi-val c-red">' + fmt(-Math.abs(maxDD)) + '</div><div class="kpi-label">Max Drawdown</div></div>' +
    '<div class="kpi"><div class="kpi-val">' + tradesPerDay.toFixed(1) + '</div><div class="kpi-label">Trades/Day</div></div>';
}

// ---- EQUITY CURVE (TradingView Lightweight Charts) ----
function renderEquityCurve(curve) {
  var container = $('bt-equity-chart');
  if (!container) return;

  // Remove existing chart
  if (_btChart) {
    _btChart.remove();
    _btChart = null;
  }

  if (!curve.length) {
    container.innerHTML = '<span class="c-muted">No equity data</span>';
    return;
  }

  container.innerHTML = '';
  _btChart = LightweightCharts.createChart(container, {
    layout: {
      textColor: '#cccccc',
      background: { type: 'solid', color: '#0c0c0c' },
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: 10
    },
    grid: {
      vertLines: { color: '#1a1a1a' },
      horzLines: { color: '#1a1a1a' }
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#767676' },
    timeScale: { borderColor: '#767676', timeVisible: true, secondsVisible: false }
  });

  var lineSeries = _btChart.addLineSeries({
    color: '#16c60c',
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true
  });

  var chartData = curve.map(function(pt) {
    return { time: pt.time, value: pt.value };
  });
  lineSeries.setData(chartData);
  _btChart.timeScale().fitContent();
}

// ---- TRADE FREQUENCY (ASCII BAR CHART) ----
function renderFrequency(trades) {
  var el = $('bt-frequency');
  if (!el) return;

  if (!trades.length) {
    el.innerHTML = '<span class="c-muted">No trades</span>';
    return;
  }

  // Group trades by date
  var byDate = {};
  trades.forEach(function(t) {
    var ts = t.entry_time || t.timestamp || '';
    var date = ts.substring(0, 10); // YYYY-MM-DD
    if (!date) return;
    byDate[date] = (byDate[date] || 0) + 1;
  });

  var dates = Object.keys(byDate).sort();
  if (!dates.length) {
    el.innerHTML = '<span class="c-muted">No date data</span>';
    return;
  }

  var maxCount = 0;
  dates.forEach(function(d) { if (byDate[d] > maxCount) maxCount = byDate[d]; });

  var maxBarWidth = 30;
  var html = '<div style="font-size:var(--text-xs);line-height:1.5;">';
  dates.forEach(function(d) {
    var count = byDate[d];
    var barLen = maxCount > 0 ? Math.max(1, Math.round((count / maxCount) * maxBarWidth)) : 1;
    var label = d.substring(5); // MM-DD
    html += '<span class="c-muted">' + label + '</span> <span class="c-cyan">' +
      '\u2588'.repeat(barLen) + '</span> <span class="c-bold">' + count + '</span><br>';
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---- TRADE LOG TABLE ----
function renderTradeLog(trades) {
  var el = $('bt-trades');
  if (!el) return;

  if (!trades.length) {
    el.innerHTML = '<span class="c-muted">No trades</span>';
    return;
  }

  var html = '<table><thead><tr>' +
    '<th>Entry</th><th>Exit</th><th>Dir</th><th class="num">Entry$</th><th class="num">Exit$</th>' +
    '<th class="num">P&L</th><th>Reason</th><th class="num">MAE</th><th class="num">MFE</th><th class="num">Bars</th>' +
    '</tr></thead><tbody>';

  trades.forEach(function(t) {
    var entryTs = compactTs(t.entry_time);
    var exitTs = compactTs(t.exit_time);
    var dir = (t.direction || '').toUpperCase();
    var dirCls = t.direction === 'long' ? 'c-green' : 'c-red';
    var pnl = t.pnl != null ? t.pnl : 0;

    html += '<tr>' +
      '<td class="c-muted">' + entryTs + '</td>' +
      '<td class="c-muted">' + exitTs + '</td>' +
      '<td class="' + dirCls + '">' + dir + '</td>' +
      '<td class="num">' + (t.entry_price != null ? t.entry_price.toFixed(2) : '--') + '</td>' +
      '<td class="num">' + (t.exit_price != null ? t.exit_price.toFixed(2) : '--') + '</td>' +
      '<td class="num ' + pnlColor(pnl) + '">' + fmt(pnl) + '</td>' +
      '<td class="c-muted" style="font-size:var(--text-xs)">' + (t.exit_reason || '--') + '</td>' +
      '<td class="num c-red">' + (t.mae != null ? t.mae.toFixed(2) : '--') + '</td>' +
      '<td class="num c-green">' + (t.mfe != null ? t.mfe.toFixed(2) : '--') + '</td>' +
      '<td class="num">' + (t.bars_held != null ? t.bars_held : '--') + '</td>' +
      '</tr>';
  });

  html += '</tbody></table>';
  el.innerHTML = html;
}

function compactTs(ts) {
  if (!ts) return '--';
  // Expect "YYYY-MM-DD HH:MM:SS" or ISO format
  var d = ts.substring(5, 16); // "MM-DD HH:MM"
  return d.replace('T', ' ');
}

// ---- RUN HISTORY ----
async function loadBtHistory() {
  try {
    var r = await fetch('/api/backtest/runs');
    var runs = await r.json();
    var el = $('bt-history');
    if (!el) return;

    if (!runs.length) {
      el.innerHTML = '<span class="c-muted">No backtest runs yet</span>';
      return;
    }

    var html = '<table><thead><tr>' +
      '<th>Symbol</th><th>Date Range</th><th class="num">Trades</th><th class="num">Win%</th>' +
      '<th class="num">P&L</th><th class="num">PF</th><th></th>' +
      '</tr></thead><tbody>';

    runs.forEach(function(run) {
      var startShort = (run.start_date || '').substring(5); // MM-DD
      var endShort = (run.end_date || '').substring(5);
      var pnl = run.total_pnl != null ? run.total_pnl : 0;

      html += '<tr style="cursor:pointer" onclick="loadBtRun(' + run.id + ')">' +
        '<td class="c-cyan">' + (run.symbol || '--') + '</td>' +
        '<td class="c-muted" style="font-size:var(--text-xs)">' + startShort + ' \u2192 ' + endShort + '</td>' +
        '<td class="num">' + (run.total_trades || 0) + '</td>' +
        '<td class="num">' + (run.win_rate != null ? run.win_rate.toFixed(1) : '--') + '%</td>' +
        '<td class="num ' + pnlColor(pnl) + '">' + fmt(pnl) + '</td>' +
        '<td class="num">' + (run.profit_factor != null ? run.profit_factor.toFixed(2) : '--') + '</td>' +
        '<td><span class="c-red" style="cursor:pointer" onclick="event.stopPropagation();deleteBtRun(' + run.id + ')">\u2717</span></td>' +
        '</tr>';
    });

    html += '</tbody></table>';
    el.innerHTML = html;
  } catch (e) {
    var el2 = $('bt-history');
    if (el2) el2.innerHTML = '<span class="c-red">[ERROR] ' + e.message + '</span>';
  }
}

// ---- LOAD SINGLE RUN ----
async function loadBtRun(runId) {
  try {
    $('bt-status').innerHTML = '<span class="c-yellow">Loading run #' + runId + '...</span>';
    var r = await fetch('/api/backtest/runs/' + runId);
    var data = await r.json();
    if (data.error) {
      $('bt-status').innerHTML = '<span class="c-red">' + data.error + '</span>';
      return;
    }
    $('bt-status').innerHTML = '<span class="c-green">Loaded run #' + runId + '</span>';
    displayBtResults(data);
  } catch (e) {
    $('bt-status').innerHTML = '<span class="c-red">Error: ' + e.message + '</span>';
  }
}

// ---- DELETE RUN ----
async function deleteBtRun(runId) {
  if (!confirm('Delete backtest run #' + runId + '?')) return;
  try {
    await fetch('/api/backtest/runs/' + runId, { method: 'DELETE' });
    loadBtHistory();
  } catch (e) {
    console.error('Failed to delete run:', e);
  }
}
