// A segmented level meter: daily play counts drawn as columns of lit/unlit segments,
// like a hi-fi spectrum analyser. Shared by the dashboard (30 days, all plays) and the
// song page (90 days, one track).

import { formatDate, h, num } from "./dom.js";

const SEGMENTS = 10; // lit segments per column at the busiest day

export function spectrum(counts, subject = "Plays", windowLabel = "30 days") {
  const NS = "http://www.w3.org/2000/svg";
  const width = 1000;
  const height = 96;
  const gap = 4;
  const max = Math.max(1, ...counts.map((c) => c.count));
  const columnWidth = width / counts.length;
  const segmentHeight = height / SEGMENTS;
  const total = counts.reduce((sum, c) => sum + c.count, 0);

  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${subject} per day over the last ${counts.length} days: ${num(total)} in total`);

  counts.forEach((day, i) => {
    const lit = day.count ? Math.max(1, Math.round((day.count / max) * SEGMENTS)) : 0;
    const group = document.createElementNS(NS, "g");
    const title = document.createElementNS(NS, "title");
    title.textContent = `${formatDate(day.start)}: ${num(day.count)} ${day.count === 1 ? "play" : "plays"}`;
    group.append(title);
    for (let s = 0; s < SEGMENTS; s++) {
      const rect = document.createElementNS(NS, "rect");
      rect.setAttribute("x", i * columnWidth + gap / 2);
      rect.setAttribute("y", height - (s + 1) * segmentHeight + 1.5);
      rect.setAttribute("width", columnWidth - gap);
      rect.setAttribute("height", segmentHeight - 3);
      rect.setAttribute("class", s < lit ? "seg-on" : "seg-off");
      group.append(rect);
    }
    svg.append(group);
  });

  return h(
    "div",
    { class: "spectrum" },
    svg,
    h(
      "div",
      { class: "spectrum-axis" },
      h("span", {}, formatDate(counts[0].start)),
      h("span", {}, `${num(total)} ${subject.toLowerCase()} in ${windowLabel}`),
      h("span", {}, "Today"),
    ),
  );
}
