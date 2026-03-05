/* Performance Dashboard — fetches /api/performance, renders charts + tables */
/* global LightweightCharts */

var perfChart = null;
var perfLineSeries = null;
var _perfPeriodData = {};
var _perfCurrentPeriod = 'daily';

async function refreshPerformance() {
  try {
    var r = await fetch('/api/performance');
    var data = await r.json();
    renderPerformanceKPIs(data.metrics || {});
    renderEquityCurve(data.equity_curve || []);
    _perfPeriodData = {
      daily: data.daily_pnl || [],
      weekly: data.weekly_pnl || [],
      monthly: data.monthly_pnl || [],
    };
    renderPeriodTable(_perfPeriodData[_perfCurrentPeriod]);
    renderStrategyTable(data.per_strategy || []);
  } catch (e) {
    console.error('Performance fetch error:', e);
  }

  // Also load auto-trade status
  refreshAutoTradeStatus();
}

function renderPerformanceKPIs(m) {
  var el = function(id) { return document.getElementById(id); };
  var pnlCls = m.total_pnl > 0 ? 'c-green' : m.total_pnl < 0 ? 'c-red' : 'c-muted';
  el('perf-total-pnl').innerHTML = '<span class="' + pnlCls + '">$' + (m.total_pnl || 0).toLocaleString('en-US', { minimumFractionDigits: 2 }) + '</span>';
  el('perf-sharpe').textContent = (m.sharpe_ratio || 0).toFixed(2);
  el('perf-max-dd').innerHTML = '<span class="c-red">$' + (m.max_drawdown || 0).toFixed(2) + '</span>';
  el('perf-win-rate').textContent = ((m.win_rate || 0) * 100).toFixed(1) + '%';
  el('perf-profit-factor').textContent = (m.profit_factor || 0).toFixed(2);
  el('perf-expectancy').textContent = '$' + (m.expectancy || 0).toFixed(2);
  el('perf-total-trades').textContent = m.total_trades || 0;
}

function renderEquityCurve(curveData) {
  var container = document.getElementById('perf-equity-chart');
  if (!container) return;

  if (!curveData || curveData.length === 0) {
    container.innerHTML = '<span class="c-muted" style="padding:20px;display:block;text-align:center;">No trade data yet</span>';
    return;
  }

  // Initialize or recreate chart
  if (perfChart) {
    perfChart.remove();
    perfChart = null;
  }

  perfChart = LightweightCharts.createChart(container, {
    layout: { textColor: '#cccccc', background: { type: 'solid', color: '#0c0c0c' },
      fontFamily: 'JetBrains Mono, monospace', fontSize: 10 },
    grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
    rightPriceScale: { borderColor: '#767676' },
    timeScale: { borderColor: '#767676', timeVisible: true },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  perfLineSeries = perfChart.addLineSeries({
    color: '#16c60c',
    lineWidth: 2,
    priceFormat: { type: 'custom', formatter: function(v) { return '$' + v.toFixed(2); } },
  });

  // Add baseline at zero
  perfChart.addLineSeries({
    color: '#767676',
    lineWidth: 1,
    lineStyle: 2, // dashed
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  }).setData(curveData.length > 0 ? [
    { time: curveData[0].time, value: 0 },
    { time: curveData[curveData.length - 1].time, value: 0 },
  ] : []);

  perfLineSeries.setData(curveData);
  perfChart.timeScale().fitContent();
}

function switchPerfPeriod(period, el) {
  _perfCurrentPeriod = period;
  if (el) {
    el.parentElement.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
    el.classList.add('active');
  }
  renderPeriodTable(_perfPeriodData[period] || []);
}

function renderPeriodTable(data) {
  var container = document.getElementById('perf-period-table');
  if (!container) return;

  if (!data || data.length === 0) {
    container.innerHTML = '<span class="c-muted">No data for this period</span>';
    return;
  }

  var html = '<table><tr><th>Period</th><th class="num">P&L</th><th class="num">Trades</th><th class="num">Wins</th><th class="num">Win Rate</th></tr>';
  for (var i = data.length - 1; i >= 0 && i >= data.length - 30; i--) {
    var row = data[i];
    var cls = row.pnl > 0 ? 'c-green' : row.pnl < 0 ? 'c-red' : 'c-muted';
    html += '<tr>' +
      '<td>' + row.period + '</td>' +
      '<td class="num ' + cls + '">$' + row.pnl.toFixed(2) + '</td>' +
      '<td class="num">' + row.trades + '</td>' +
      '<td class="num">' + row.wins + '</td>' +
      '<td class="num">' + (row.win_rate * 100).toFixed(1) + '%</td></tr>';
  }
  html += '</table>';
  container.innerHTML = html;
}

function renderStrategyTable(data) {
  var container = document.getElementById('perf-strategy-table');
  if (!container) return;

  if (!data || data.length === 0) {
    container.innerHTML = '<span class="c-muted">No strategy data</span>';
    return;
  }

  var html = '<table><tr><th>Strategy</th><th class="num">P&L</th><th class="num">Trades</th><th class="num">Win Rate</th><th class="num">Avg P&L</th></tr>';
  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var cls = row.pnl > 0 ? 'c-green' : row.pnl < 0 ? 'c-red' : 'c-muted';
    html += '<tr>' +
      '<td>' + row.strategy + '</td>' +
      '<td class="num ' + cls + '">$' + row.pnl.toFixed(2) + '</td>' +
      '<td class="num">' + row.trades + '</td>' +
      '<td class="num">' + (row.win_rate * 100).toFixed(1) + '%</td>' +
      '<td class="num">$' + row.avg_pnl.toFixed(2) + '</td></tr>';
  }
  html += '</table>';
  container.innerHTML = html;
}

async function refreshAutoTradeStatus() {
  var panel = document.getElementById('auto-trade-panel');
  if (!panel) return;

  try {
    var r = await fetch('/api/auto-trade/status');
    var data = await r.json();

    if (!data.enabled) {
      panel.innerHTML = '<span class="c-muted">Auto-trade: </span><span class="c-red">DISABLED</span>' +
        ' <button class="lab-btn" onclick="toggleAutoTrade()" style="font-size:var(--text-xs);padding:2px 8px;">Enable</button>';
      return;
    }

    if (data.error) {
      panel.innerHTML = '<span class="c-red">Error: ' + data.error + '</span>';
      return;
    }

    var dailyCls = data.daily_pnl >= 0 ? 'c-green' : 'c-red';
    var html = '<span class="c-muted">Auto-trade: </span><span class="c-green">ENABLED</span>' +
      ' <button class="lab-btn" onclick="toggleAutoTrade()" style="font-size:var(--text-xs);padding:2px 8px;">Disable</button><br>' +
      '<span class="c-muted">Equity:</span> $' + data.equity.toLocaleString('en-US', { minimumFractionDigits: 2 }) +
      ' <span class="c-muted">| Buying Power:</span> $' + data.buying_power.toLocaleString('en-US', { minimumFractionDigits: 2 }) +
      ' <span class="c-muted">| Daily P&L:</span> <span class="' + dailyCls + '">$' + data.daily_pnl.toFixed(2) + '</span>';

    // Positions
    if (data.positions && data.positions.length > 0) {
      html += '<br><span class="c-muted">Positions (' + data.positions.length + '/' + data.max_positions + '):</span>';
      for (var i = 0; i < data.positions.length; i++) {
        var p = data.positions[i];
        var pCls = parseFloat(p.pnl) >= 0 ? 'c-green' : 'c-red';
        html += ' <span class="c-cyan">' + p.symbol + '</span>(' + p.qty + ' ' + p.side + ' <span class="' + pCls + '">$' + parseFloat(p.pnl).toFixed(2) + '</span>)';
      }
    } else {
      html += '<br><span class="c-muted">Positions: 0/' + data.max_positions + '</span>';
    }

    panel.innerHTML = html;
  } catch (e) {
    panel.innerHTML = '<span class="c-muted">Auto-trade status unavailable</span>';
  }
}

async function toggleAutoTrade() {
  try {
    await fetch('/api/auto-trade/toggle', { method: 'POST' });
    refreshAutoTradeStatus();
  } catch (e) {
    console.error('Toggle auto-trade error:', e);
  }
}
