/* Dashboard charts (Chart.js). Data is injected as window.CASHBEE_CHARTS. */
(function () {
  "use strict";
  if (typeof Chart === "undefined" || !window.CASHBEE_CHARTS) return;

  var data = window.CASHBEE_CHARTS;
  var amber = "#f5a623";
  var orange = "#ff7a00";

  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.color = "#94a3b8";
  Chart.defaults.plugins.legend.display = false;

  function gradient(ctx, color) {
    var g = ctx.createLinearGradient(0, 0, 0, 220);
    g.addColorStop(0, color + "55");
    g.addColorStop(1, color + "00");
    return g;
  }

  var baseOpts = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { intersect: false, mode: "index" },
    scales: {
      x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
      y: { beginAtZero: true, grid: { color: "#eef2f7" }, ticks: { precision: 0 } },
    },
    plugins: { tooltip: { backgroundColor: "#0f1729", padding: 10, cornerRadius: 8 } },
  };

  function line(id, series, color) {
    var el = document.getElementById(id);
    if (!el || !series) return;
    var ctx = el.getContext("2d");
    new Chart(ctx, {
      type: "line",
      data: {
        labels: series.labels,
        datasets: [{
          data: series.values,
          borderColor: color,
          backgroundColor: gradient(ctx, color),
          fill: true,
          tension: 0.4,
          borderWidth: 2.5,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: color,
        }],
      },
      options: baseOpts,
    });
  }

  function bar(id, series, color) {
    var el = document.getElementById(id);
    if (!el || !series) return;
    new Chart(el.getContext("2d"), {
      type: "bar",
      data: {
        labels: series.labels,
        datasets: [{ data: series.values, backgroundColor: color, borderRadius: 6, maxBarThickness: 26 }],
      },
      options: baseOpts,
    });
  }

  line("adViewsChart", data.daily_ad_views, orange);
  bar("signupsChart", data.daily_signups, amber);
  bar("withdrawalChart", data.withdrawal_volume, "#16a34a");
})();
