// Shared Chart.js defaults and color palette for the work site.
// Pages should load Chart.js v4 from CDN and then this file.

(function () {
  if (typeof Chart === "undefined") {
    console.error("charts.js: Chart.js must be loaded before this file");
    return;
  }

  window.MC_CHART_COLORS = {
    blue: "#1576d8",
    purple: "#7F77DD",
    green: "#2f9e44",
    orange: "#f08c00",
    red: "#E05050",
    teal: "#1098AD",
    pink: "#D6336C",
    gray: "#748492",
    grayLight: "#E2E2E2",
    grayMid: "#676F7E",
    text: "#21242C",
    bg: "#FAFAFA",
    cardBg: "#FFFFFF",
    border: "#E8E8E8",
    grid: "#F0F0F0",
  };

  const c = window.MC_CHART_COLORS;

  // Series palette for multi-series charts — pick from these in order.
  window.MC_SERIES_PALETTE = [c.blue, c.purple, c.green, c.orange, c.teal, c.pink];

  // Convert a hex color to rgba for fills.
  window.mcAlpha = function (hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  };

  // Global defaults
  Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = c.grayMid;
  Chart.defaults.borderColor = c.border;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.responsive = true;

  // Scales
  Chart.defaults.scale.grid.color = c.grid;
  Chart.defaults.scale.grid.drawTicks = false;
  Chart.defaults.scale.border = Chart.defaults.scale.border || {};
  Chart.defaults.scale.border.display = false;
  Chart.defaults.scale.ticks.padding = 8;

  // Legend
  Chart.defaults.plugins.legend.position = "bottom";
  Chart.defaults.plugins.legend.align = "start";
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.boxHeight = 8;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.padding = 16;

  // Tooltip
  Chart.defaults.plugins.tooltip.backgroundColor = c.text;
  Chart.defaults.plugins.tooltip.titleFont = { weight: "600", size: 12 };
  Chart.defaults.plugins.tooltip.bodyFont = { size: 12 };
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 6;
  Chart.defaults.plugins.tooltip.displayColors = true;
  Chart.defaults.plugins.tooltip.boxPadding = 6;
  Chart.defaults.plugins.tooltip.boxWidth = 8;
  Chart.defaults.plugins.tooltip.boxHeight = 8;
  Chart.defaults.plugins.tooltip.usePointStyle = true;

  // Tooltip swatch: solid filled dot, no stroke, matching the legend.
  // Stacked-area datasets use white borderColor for layer separators, so fall back
  // to backgroundColor in that case to get the actual series color.
  Chart.defaults.plugins.tooltip.callbacks.labelColor = function (context) {
    const ds = context.dataset;
    let color = ds.borderColor;
    if (color === "#fff" || color === "#FFFFFF" || color === "rgb(255, 255, 255)") {
      color = ds.backgroundColor;
    }
    return { backgroundColor: color, borderColor: color, borderWidth: 0 };
  };

  // Line/Bar element defaults
  Chart.defaults.elements.line.tension = 0.32;
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 5;
  Chart.defaults.elements.point.hitRadius = 10;
  Chart.defaults.elements.bar.borderRadius = 4;
  Chart.defaults.elements.bar.borderSkipped = false;
})();
