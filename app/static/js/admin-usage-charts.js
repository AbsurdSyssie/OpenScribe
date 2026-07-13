(() => {
  "use strict";

  document.querySelectorAll("[data-auto-submit]").forEach((control) => {
    control.addEventListener("change", () => control.form?.requestSubmit());
  });

  const dataElement = document.getElementById("usage-chart-data");
  if (!dataElement || !window.echarts) return;

  let points;
  try {
    points = JSON.parse(dataElement.textContent || "[]");
  } catch {
    return;
  }

  const metricOptions = {
    input: { label: "Input tokens", color: "#6b46c1" },
    output: { label: "Output tokens", color: "#1d4f5e" },
    audio: { label: "Audio hours", color: "#b7791f" },
    failure: { label: "Failures", color: "#c53030" },
  };
  const rangeLabel = dataElement.dataset.rangeLabel || "Selected period";
  const hasComparison = dataElement.dataset.hasComparison === "true";

  const charts = [];
  document.querySelectorAll("[data-usage-chart]").forEach((element) => {
    const metric = element.dataset.usageChart;
    const metricOption = metricOptions[metric];
    if (!metricOption) return;

    const currentKey = `current_${metric}`;
    const previousKey = `previous_${metric}`;
    const chart = window.echarts.init(element, null, { renderer: "svg" });
    chart.setOption({
      animationDuration: 350,
      aria: { enabled: true },
      color: [metricOption.color, metricOption.color],
      grid: { top: 52, right: 24, bottom: 72, left: 72 },
      legend: {
        top: 4,
        left: 0,
        itemWidth: 18,
        itemHeight: 9,
        textStyle: { color: "#718096", fontSize: 12 },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        valueFormatter: (value) =>
          metric === "audio" ? `${Number(value).toFixed(2)} h` : Number(value).toLocaleString(),
      },
      xAxis: {
        type: "category",
        data: points.map((point) => point.short_label),
        name: "Day in period",
        nameLocation: "middle",
        nameGap: 38,
        axisTick: { alignWithLabel: true },
        axisLabel: { color: "#718096", interval: 1 },
        axisLine: { lineStyle: { color: "#d8d2c8" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        name: metricOption.label,
        nameTextStyle: { color: "#718096", padding: [0, 0, 8, 0] },
        axisLabel: { color: "#718096" },
        splitLine: { lineStyle: { color: "#e9e5de" } },
      },
      dataZoom: [
        { type: "inside", start: 0, end: 100 },
        { type: "slider", height: 18, bottom: 12, start: 0, end: 100 },
      ],
      series: [
        {
          name: rangeLabel,
          type: "bar",
          data: points.map((point) => point[currentKey]),
          barMaxWidth: 20,
          itemStyle: { color: metricOption.color, borderRadius: [2, 2, 0, 0] },
        },
        ...(hasComparison ? [{
          name: "Previous equal period",
          type: "bar",
          data: points.map((point) => point[previousKey]),
          barMaxWidth: 20,
          itemStyle: { color: metricOption.color, opacity: 0.28, borderRadius: [2, 2, 0, 0] },
        }] : []),
      ],
    });
    charts.push(chart);
  });

  const resizeCharts = () => charts.forEach((chart) => chart.resize());
  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(resizeCharts);
    document.querySelectorAll("[data-usage-chart]").forEach((element) => observer.observe(element));
  } else {
    window.addEventListener("resize", resizeCharts);
  }
})();
