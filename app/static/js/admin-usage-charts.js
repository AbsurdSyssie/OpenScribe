(() => {
  "use strict";

  const dataElement = document.getElementById("usage-chart-data");
  if (!dataElement || !window.echarts) return;

  let points;
  try {
    points = JSON.parse(dataElement.textContent || "[]");
  } catch {
    return;
  }
  if (!Array.isArray(points) || points.length === 0) return;

  const metricOptions = {
    input: { label: "Input tokens", color: "#553c9a" },
    output: { label: "Output tokens", color: "#1d4f5e" },
    audio: { label: "Audio hours", color: "#805000" },
    failure: { label: "Failures", color: "#9b2c2c" },
  };
  const rangeLabel = dataElement.dataset.rangeLabel || "Selected period";
  const hasComparison = dataElement.dataset.hasComparison === "true";
  const previousColor = "#3f4854";
  const charts = [];

  const makeControl = (container, labelText, type, id, max, value) => {
    const label = document.createElement("label");
    label.className = "usage-chart-control";
    label.htmlFor = id;
    const text = document.createElement("span");
    text.textContent = labelText;
    const input = document.createElement("input");
    input.type = type;
    input.id = id;
    input.min = "0";
    input.max = String(max);
    input.step = "1";
    input.value = String(value);
    input.setAttribute("aria-valuetext", `${labelText}: ${points[value]?.label || `period ${value + 1}`}`);
    input.addEventListener("input", () => {
      input.setAttribute("aria-valuetext", `${labelText}: ${points[Number(input.value)]?.label || `period ${Number(input.value) + 1}`}`);
    });
    label.append(text, input);
    container.append(label);
    return input;
  };

  document.querySelectorAll("[data-usage-chart]").forEach((element) => {
    const metric = element.dataset.usageChart;
    const metricOption = metricOptions[metric];
    if (!metricOption) return;

    const scope = element.dataset.usageChartScope || "global";
    const currentKey = `current_${metric}`;
    const previousKey = `previous_${metric}`;
    const chart = window.echarts.init(element, null, { renderer: "svg" });
    const currentName = rangeLabel;
    const series = [{
      name: currentName,
      type: "bar",
      data: points.map((point) => point[currentKey]),
      barMaxWidth: 20,
      itemStyle: { color: metricOption.color, borderRadius: [2, 2, 0, 0] },
    }];
    if (hasComparison) {
      series.push({
        name: "Previous equal period",
        type: "bar",
        data: points.map((point) => point[previousKey]),
        barMaxWidth: 20,
        itemStyle: {
          color: previousColor,
          borderType: "dashed",
          borderColor: "#111827",
          borderWidth: 1,
          borderRadius: [2, 2, 0, 0],
        },
      });
    }

    chart.setOption({
      animationDuration: 200,
      aria: {
        enabled: true,
        label: {
          description: `${metricOption.label} chart for ${rangeLabel}. Full values for every period are available in the data table.`,
        },
      },
      color: [metricOption.color, previousColor],
      grid: { top: 46, right: 24, bottom: 72, left: 72 },
      legend: {
        top: 4,
        left: 0,
        itemWidth: 18,
        itemHeight: 9,
        textStyle: { color: "#3f4854", fontSize: 12 },
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
        axisTick: { alignWithLabel: true },
        axisLabel: { color: "#3f4854", interval: "auto", hideOverlap: true, showMinLabel: true, showMaxLabel: true },
        axisLine: { lineStyle: { color: "#767676" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        axisLabel: { color: "#3f4854" },
        splitLine: { lineStyle: { color: "#d0d0d0" } },
      },
      dataZoom: [
        { type: "inside", start: 0, end: 100 },
        { type: "slider", height: 18, bottom: 12, start: 0, end: 100 },
      ],
      series,
    });
    element.setAttribute(
      "aria-label",
      `${metricOption.label} chart for ${rangeLabel}. Full values for every period are available in the data table.`,
    );

    const wrapper = element.closest(".usage-chart-a11y") || element.parentElement;
    if (wrapper) {
      let controls = wrapper.querySelector(".usage-chart-controls");
      if (!controls) {
        controls = document.createElement("details");
        controls.className = "usage-chart-controls";
        const summary = document.createElement("summary");
        summary.textContent = "Keyboard chart controls";
        controls.append(summary);
        const controlGroup = document.createElement("fieldset");
        controlGroup.className = "usage-chart-control-group";
        const groupLabel = document.createElement("legend");
        groupLabel.textContent = `${metricOption.label} chart controls`;
        controlGroup.append(groupLabel);
        const maximum = points.length - 1;
        const start = makeControl(controlGroup, "First period shown", "range", `usage-chart-start-${scope}-${metric}`, maximum, 0);
        const end = makeControl(controlGroup, "Last period shown", "range", `usage-chart-end-${scope}-${metric}`, maximum, maximum);
        const updateZoom = () => {
          let first = Number(start.value);
          let last = Number(end.value);
          if (first > last) {
            if (document.activeElement === start) last = first;
            else first = last;
            start.value = String(first);
            end.value = String(last);
          }
          start.setAttribute("aria-valuetext", `First period shown: ${points[first]?.label || `period ${first + 1}`}`);
          end.setAttribute("aria-valuetext", `Last period shown: ${points[last]?.label || `period ${last + 1}`}`);
          chart.dispatchAction({ type: "dataZoom", startValue: first, endValue: last });
        };
        start.addEventListener("input", updateZoom);
        end.addEventListener("input", updateZoom);
        chart.on("datazoom", (event) => {
          const zoom = event.batch?.[0] || event;
          const option = chart.getOption().dataZoom?.[1] || chart.getOption().dataZoom?.[0] || {};
          const firstValue = Number.isFinite(Number(zoom.startValue))
            ? Number(zoom.startValue)
            : Math.round((Number(zoom.start ?? option.start ?? 0) / 100) * maximum);
          const lastValue = Number.isFinite(Number(zoom.endValue))
            ? Number(zoom.endValue)
            : Math.round((Number(zoom.end ?? option.end ?? 100) / 100) * maximum);
          const first = Math.max(0, Math.min(maximum, firstValue));
          const last = Math.max(first, Math.min(maximum, lastValue));
          start.value = String(first);
          end.value = String(last);
          start.setAttribute("aria-valuetext", `First period shown: ${points[first]?.label || `period ${first + 1}`}`);
          end.setAttribute("aria-valuetext", `Last period shown: ${points[last]?.label || `period ${last + 1}`}`);
        });

        const addSeriesToggle = (name, suffix, selected) => {
          const label = document.createElement("label");
          label.className = "usage-chart-control usage-chart-series-control";
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.id = `usage-chart-series-${scope}-${metric}-${suffix}`;
          checkbox.checked = selected;
          const text = document.createElement("span");
          text.textContent = `Show ${name}`;
          label.append(checkbox, text);
          controlGroup.append(label);
          checkbox.addEventListener("change", () => {
            chart.dispatchAction({ type: checkbox.checked ? "legendSelect" : "legendUnSelect", name });
          });
          chart.on("legendselectchanged", (event) => {
            checkbox.checked = event.selected?.[name] !== false;
          });
        };
        addSeriesToggle(currentName, "current", true);
        if (hasComparison) addSeriesToggle("Previous equal period", "previous", true);
        controls.append(controlGroup);
        wrapper.append(controls);
      }
    }

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
