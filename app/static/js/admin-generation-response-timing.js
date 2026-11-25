(() => {
  "use strict";
  if (!window.echarts) return;

  const stageNames = {
    queue: "Queue", preparation: "Preparation", generation: "Generation",
    checking: "Checking", finalising: "Finalising", unknown: "Unknown stage",
  };
  const stageColors = {
    queue: "#718096", preparation: "#3d7a8c", generation: "#1d4f5e",
    checking: "#80520a", finalising: "#246b46", unknown: "#56616f",
  };
  const fmtSeconds = (ms) => Number.isFinite(ms) ? `${(ms / 1000).toFixed(1)} s` : "Unknown";
  const outcomeLabels = {
    ready: "Draft ready", retry: "Recovered by retry", backup: "Recovered by backup",
    warning: "Draft ready with a failure", failed: "Failed", unknown: "Unknown outcome",
  };
  const outcomeMarkers = {
    retry: { color: "#80520a", symbol: "diamond", label: "Retry" },
    backup: { color: "#3d7a8c", symbol: "triangle", label: "Backup" },
    failed: { color: "#c53030", symbol: "circle", label: "Failed" },
    warning: { color: "#80520a", symbol: "rect", label: "Warning" },
  };
  const dateLabel = (value) => {
    const date = new Date(value ?? NaN);
    return Number.isNaN(date.getTime()) ? "Unknown" : `${date.toLocaleString("en-GB", { timeZone: "UTC" })} UTC`;
  };
  const dayLabel = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const completionLabel = new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  });
  const scaleMaximum = (values) => {
    const largest = Math.max(1, ...values);
    const step = 10 ** Math.floor(Math.log10(largest / 4));
    return Math.ceil(largest * 1.1 / step) * step;
  };
  const payloads = [...document.querySelectorAll("script[data-generation-timing-data]")].flatMap((dataEl) => {
    try { return [{ source: dataEl.dataset.generationTimingData, data: JSON.parse(dataEl.textContent || "{}") }]; } catch { return []; }
  });
  const allJobs = payloads.flatMap(({ data }) => Array.isArray(data.jobs) ? data.jobs : []);
  const allAverages = payloads.flatMap(({ data }) => Array.isArray(data.averages) ? data.averages : []);
  const stageMax = scaleMaximum(allJobs.map((job) => job.total_ms / 1000));
  const averageMax = scaleMaximum(allAverages.map((point) => point.average_ms / 1000));
  const averageTimes = allAverages.map((point) => Date.parse(point.at)).filter(Number.isFinite);
  const firstAverage = Math.min(...averageTimes);
  const lastAverage = Math.max(...averageTimes);
  const averageRange = firstAverage < lastAverage ? { min: firstAverage, max: lastAverage } : {};

  payloads.forEach(({ source, data }) => {
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    if (!jobs.length) return;
    const sorted = [...jobs].sort((a, b) => new Date(a.at) - new Date(b.at));
    const labels = sorted.map((job) => completionLabel.format(new Date(job.at)));
    const stageValues = sorted.map((job) => {
      const values = Object.fromEntries((Array.isArray(job.stages) ? job.stages : []).map((stage) => [stage.key, Number(stage.duration_ms || 0)]));
      const total = Number(job.total_ms || 0);
      const stageTotal = Object.values(values).reduce((sum, value) => sum + value, 0);
      if (stageTotal < total) values.unknown = (values.unknown || 0) + total - stageTotal;
      return values;
    });
    const stageKeys = [...new Set(stageValues.flatMap((values) => Object.keys(values)))];
    const knownKeys = Object.keys(stageNames);
    stageKeys.sort((a, b) => (knownKeys.indexOf(a) < 0 ? knownKeys.length : knownKeys.indexOf(a)) - (knownKeys.indexOf(b) < 0 ? knownKeys.length : knownKeys.indexOf(b)));
    const common = {
      animation: false,
      aria: { enabled: true },
      grid: { top: 30, right: 18, bottom: labels.length > 7 ? 56 : 40, left: 62 },
      tooltip: { show: false },
      xAxis: {
        type: "category", data: labels, axisLabel: { color: "#3f4854", rotate: labels.length > 7 ? 35 : 0, hideOverlap: true },
        axisLine: { lineStyle: { color: "#767676" } },
      },
      yAxis: { type: "value", min: 0, max: stageMax, axisLabel: { color: "#3f4854", formatter: (value) => `${Number(value.toFixed(1))} s` }, splitLine: { lineStyle: { color: "#d0d0d0" } } },
    };

    const stageEl = document.querySelector(`[data-generation-stage-chart="${source}"]`);
    let stageChart;
    if (stageEl) {
      const stages = stageKeys.map((key) => ({
        name: stageNames[key] || key,
        type: "bar", stack: "duration", barMaxWidth: 28,
        itemStyle: { color: stageColors[key] || stageColors.unknown },
        data: stageValues.map((values) => Number(values[key] || 0) / 1000),
      }));
      const markData = sorted.flatMap((job, index) => {
        const outcome = String(job.outcome || "unknown").toLowerCase();
        const marker = outcomeMarkers[outcome];
        return !marker ? [] : [{
          name: outcomeLabels[outcome], jobIndex: index, coord: [index, job.total_ms / 1000],
          symbol: marker.symbol, itemStyle: { color: marker.color },
          label: { show: sorted.length <= 16, formatter: marker.label, color: marker.color, fontWeight: 700, position: "top" },
        }];
      });
      if (stages.length) stages[stages.length - 1].markPoint = {
        symbol: "diamond", symbolSize: 10, data: markData, tooltip: { show: false },
      };
      stageChart = window.echarts.init(stageEl, null, { renderer: "svg" });
      stageChart.setOption({ ...common, legend: { top: 0, left: 0, textStyle: { color: "#3f4854", fontSize: 11 } }, series: stages });
      stageEl.setAttribute("aria-label", "Stacked stage durations in completion order. Select a job for the outcome and exact timing values.");
      window.addEventListener("resize", () => stageChart.resize());
      const key = document.querySelector(`[data-generation-markers="${source}"]`);
      if (key && markData.length) {
        for (const [outcome, marker] of Object.entries(outcomeMarkers)) {
          if (!sorted.some((job) => job.outcome === outcome)) continue;
          const item = document.createElement("li");
          const shape = document.createElement("span");
          shape.className = `generation-timing__marker generation-timing__marker--${outcome}`;
          shape.setAttribute("aria-hidden", "true");
          item.append(shape, outcomeLabels[outcome] || marker.label);
          key.append(item);
        }
        key.hidden = false;
      }
    }

    const averageEl = document.querySelector(`[data-generation-average-chart="${source}"]`);
    if (averageEl) {
      const averages = Array.isArray(data.averages) ? [...data.averages].sort((a, b) => new Date(a.at) - new Date(b.at)) : [];
      const plottedAverages = averages.filter((point) => Number.isFinite(Date.parse(point.at)));
      const avgChart = window.echarts.init(averageEl, null, { renderer: "svg" });
      avgChart.setOption({
        ...common,
        xAxis: {
          type: "time", splitNumber: 4, ...averageRange, axisLine: { lineStyle: { color: "#767676" } },
          axisLabel: { color: "#3f4854", hideOverlap: true, formatter: (value) => dayLabel.format(value) },
        },
        yAxis: { ...common.yAxis, max: averageMax },
        legend: { top: 0, left: 0, data: ["Successful average"], textStyle: { color: "#3f4854", fontSize: 11 } },
        series: [{ name: "Successful average", type: "line", data: plottedAverages.map((point) => [Date.parse(point.at), Number(point.average_ms || 0) / 1000]), showSymbol: true, symbolSize: 7, connectNulls: false, lineStyle: { color: "#1d4f5e", width: 2 }, itemStyle: { color: "#1d4f5e" } }],
      });
      averageEl.setAttribute("aria-label", "Average end-to-end duration of successful jobs over time, in seconds. Exact values are available in the successful average duration table.");
      window.addEventListener("resize", () => avgChart.resize());
    }

    const histogramEl = document.querySelector(`[data-generation-histogram="${source}"]`);
    if (histogramEl) {
      const bins = Array.isArray(data.histogram) ? data.histogram : [];
      const histogram = window.echarts.init(histogramEl, null, { renderer: "svg" });
      histogram.setOption({
        animation: false,
        aria: { enabled: true },
        grid: { top: 16, right: 16, bottom: 62, left: 48 },
        tooltip: { show: false },
        xAxis: { type: "category", data: bins.map((bin) => String(bin.label || "Unknown")), axisLabel: { color: "#3f4854", rotate: bins.length > 6 ? 30 : 0 } },
        yAxis: { type: "value", min: 0, minInterval: 1, axisLabel: { color: "#3f4854" }, splitLine: { lineStyle: { color: "#d0d0d0" } } },
        series: [{ name: "Successful jobs", type: "bar", data: bins.map((bin) => Number(bin.count || 0)), barMaxWidth: 34, itemStyle: { color: "#1d4f5e", borderRadius: [2, 2, 0, 0] }, label: { show: true, position: "top", color: "#2d3748" } }],
      });
      histogramEl.setAttribute("aria-label", "Histogram of successful job duration counts by duration range.");
      window.addEventListener("resize", () => histogram.resize());
    }

    const tbody = document.querySelector(`[data-generation-jobs="${source}"]`);
    const picker = document.querySelector(`[data-generation-job-picker="${source}"]`);
    const selected = document.querySelector(`[data-generation-selected="${source}"]`);
    const rows = sorted.map((job) => {
      const tr = document.createElement("tr");
      const stages = Array.isArray(job.stages) ? job.stages : [];
      const failures = Array.isArray(job.failures) ? job.failures.map((cause) => String(cause)) : [];
      const cells = [document.createElement("td"), document.createElement("td"), document.createElement("td"), document.createElement("td"), document.createElement("td")];
      cells[0].textContent = dateLabel(job.at);
      cells[1].textContent = fmtSeconds(job.total_ms);
      const outcome = String(job.outcome || "unknown").toLowerCase();
      cells[2].textContent = outcomeLabels[outcome] || outcomeLabels.unknown;
      cells[2].className = `generation-timing__outcome generation-timing__outcome--${Object.hasOwn(outcomeLabels, outcome) ? outcome : "unknown"}`;
      cells[3].className = "generation-timing__stage-list";
      cells[3].textContent = stages.map((stage) => `${stageNames[stage.key] || stage.key}: ${fmtSeconds(stage.duration_ms)}`).join("; ") || "No stage data";
      if (failures.length) {
        failures.forEach((cause) => {
          const span = document.createElement("span");
          span.className = "generation-timing__cause";
          span.textContent = cause;
          cells[4].append(span);
        });
      } else cells[4].textContent = "—";
      cells.forEach((cell) => tr.append(cell));
      return tr;
    });
    if (tbody) rows.forEach((row) => tbody.append(row));
    const showJob = (index) => {
      const job = sorted[index];
      if (!job || !selected) return;
      selected.classList.add("generation-timing__selected--active");
      selected.replaceChildren();
      const heading = document.createElement("h4");
      heading.textContent = `Job finished at ${dateLabel(job.at)}`;
      selected.append(heading);
      const outcome = String(job.outcome || "unknown").toLowerCase();
      const duration = document.createElement("p");
      duration.textContent = `${outcomeLabels[outcome] || outcomeLabels.unknown} · ${fmtSeconds(job.total_ms)} elapsed`;
      selected.append(duration);
      const stage = document.createElement("p");
      stage.textContent = `Stages: ${(Array.isArray(job.stages) ? job.stages : []).map((item) => `${stageNames[item.key] || item.key}: ${fmtSeconds(item.duration_ms)}`).join("; ") || "Unknown"}`;
      selected.append(stage);
      const failures = document.createElement("p");
      failures.textContent = job.failures?.length ? job.failures.join(" · ") : "No failures recorded";
      selected.append(failures);
      if (picker) picker.value = String(index);
    };
    if (picker) {
      sorted.forEach((job, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = `${dateLabel(job.at)} · ${fmtSeconds(job.total_ms)} · ${outcomeLabels[String(job.outcome || "unknown").toLowerCase()] || outcomeLabels.unknown}`;
        picker.append(option);
      });
      picker.addEventListener("change", () => {
        if (picker.value !== "") showJob(Number(picker.value));
      });
    }
    stageChart?.on("click", (params) => {
      const index = Number(params.data?.jobIndex ?? params.dataIndex);
      if (Number.isInteger(index)) showJob(index);
    });

    const countsEl = document.querySelector(`[data-generation-timing-counts="${source}"]`);
    if (countsEl) {
      const summary = document.createElement("p");
      const duration = data.successful_average_ms == null ? "No successful durations" : `${fmtSeconds(data.successful_average_ms)} average to draft`;
      summary.textContent = `${duration} · ${data.successful_count ?? 0} successful · ${data.failed_count ?? 0} failed in this period`;
      const recent = document.createElement("p");
      const recovery = ["retry", "backup", "warning"].filter((key) => data.outcomes?.[key]).map((key) =>
        `${outcomeLabels[key]} ${data.outcomes[key]}`
      );
      const causes = (data.failure_counts || []).map(({ label, count }) => `${label} × ${count}`);
      recent.textContent = [...recovery, ...causes].join(" · ") || "No attempt failures recorded";
      if (data.detail_capped) recent.textContent += ` (latest ${sorted.length} jobs)`;
      countsEl.replaceChildren(summary, recent);
    }
    const averageValues = document.querySelector(`[data-generation-average-values="${source}"]`);
    if (averageValues) {
      (Array.isArray(data.averages) ? data.averages : []).forEach((point) => {
        const row = averageValues.insertRow();
        row.insertCell().textContent = dateLabel(point.at);
        row.insertCell().textContent = fmtSeconds(point.average_ms);
        row.insertCell().textContent = String(point.count ?? "Unknown");
      });
    }
    const histogramValues = document.querySelector(`[data-generation-histogram-values="${source}"]`);
    if (histogramValues) {
      (Array.isArray(data.histogram) ? data.histogram : []).forEach((bin) => {
        const row = histogramValues.insertRow();
        row.insertCell().textContent = String(bin.label || "Unknown");
        row.insertCell().textContent = String(bin.count ?? 0);
      });
    }
  });
})();
