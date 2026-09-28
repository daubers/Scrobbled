import { api } from "./api.js";
import { errorBox, formatDate, formatDuration, h, num, replace, timeAgo } from "./dom.js";
import { signedInPage } from "./layout.js";

const PERIODS = [
  ["7day", "7 days"],
  ["1month", "30 days"],
  ["12month", "12 months"],
  ["overall", "All time"],
];
const PERIOD_KEY = "scrobbler.period";
const SEGMENTS = 10; // lit segments per spectrum column at the busiest day
const NOW_PLAYING_POLL_MS = 30_000;

const panel = document.getElementById("panel");
const main = document.getElementById("main");

let progressTimer;

await signedInPage("index.html");
renderPeriodSelector();
await Promise.all([loadPanel(), loadTop(currentPeriod()), loadRecent()]).catch(showError);
setInterval(() => refreshDisplay().catch(() => {}), NOW_PLAYING_POLL_MS);

function showError(error) {
  main.prepend(errorBox(error.message));
}

// ---- Front panel ------------------------------------------------------------

async function loadPanel() {
  const [nowPlaying, summary, counts, recent] = await Promise.all([
    api("/me/now-playing"),
    api("/me/summary"),
    api("/me/counts?period=1month&bucket=day"),
    api("/me/recent?limit=1"),
  ]);
  const display = h("div", { class: "display" });
  replace(panel, display, readouts(summary), spectrum(counts));
  renderDisplay(display, nowPlaying.now_playing, recent.items[0]);
}

async function refreshDisplay() {
  const display = panel.querySelector(".display");
  if (!display) return;
  const [nowPlaying, recent] = await Promise.all([api("/me/now-playing"), api("/me/recent?limit=1")]);
  renderDisplay(display, nowPlaying.now_playing, recent.items[0]);
}

function renderDisplay(display, playing, last) {
  clearInterval(progressTimer);
  display.classList.toggle("display-idle", !playing);

  if (!playing) {
    replace(
      display,
      h("p", { class: "display-status" }, "Idle"),
      h("p", { class: "display-track" }, "Nothing playing"),
      last
        ? h(
            "p",
            { class: "display-meta" },
            "Last played ",
            h("strong", {}, last.track),
            " by ",
            h("strong", {}, last.artist),
            `, ${timeAgo(last.played_at)}`,
          )
        : h("p", { class: "display-meta" }, "Connect a player on the Apps page to start scrobbling."),
    );
    return;
  }

  const fill = h("div", { class: "progress-fill" });
  const elapsed = h("span", {});
  replace(
    display,
    h("p", { class: "display-status" }, "Now playing"),
    h("p", { class: "display-track" }, playing.track),
    h(
      "p",
      { class: "display-meta" },
      h("strong", {}, playing.artist),
      playing.album ? [" from ", h("strong", {}, playing.album)] : null,
    ),
    playing.duration
      ? h(
          "div",
          { class: "progress" },
          elapsed,
          h("div", { class: "progress-track", "aria-hidden": "true" }, fill),
          h("span", {}, formatDuration(playing.duration)),
        )
      : null,
  );

  if (playing.duration) {
    const started = new Date(playing.started_at).getTime();
    const tick = () => {
      const seconds = Math.min(playing.duration, (Date.now() - started) / 1000);
      elapsed.textContent = formatDuration(seconds);
      fill.style.width = `${(seconds / playing.duration) * 100}%`;
    };
    tick();
    progressTimer = setInterval(tick, 1000);
  }
}

function readouts(summary) {
  const item = (value, label) => h("div", {}, h("dt", {}, label), h("dd", {}, num(value)));
  return h(
    "dl",
    { class: "readouts" },
    item(summary.scrobbles, "scrobbles"),
    item(summary.artists, "artists"),
    item(summary.tracks, "tracks"),
  );
}

// Daily plays for the last 30 days, drawn as a segmented level meter.
function spectrum(counts) {
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
  svg.setAttribute("aria-label", `Plays per day over the last ${counts.length} days: ${num(total)} in total`);

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
      h("span", {}, `${num(total)} plays in 30 days`),
      h("span", {}, "Today"),
    ),
  );
}

// ---- Most played ------------------------------------------------------------

function currentPeriod() {
  try {
    const saved = localStorage.getItem(PERIOD_KEY);
    if (PERIODS.some(([value]) => value === saved)) return saved;
  } catch {
    // Storage unavailable: fall back to the default.
  }
  return "7day";
}

function renderPeriodSelector() {
  const selector = document.getElementById("period");
  const active = currentPeriod();
  for (const [value, label] of PERIODS) {
    const id = `period-${value}`;
    selector.append(
      h("input", { type: "radio", name: "period", id, value, checked: value === active }),
      h("label", { for: id }, label),
    );
  }
  selector.addEventListener("change", (event) => {
    try {
      localStorage.setItem(PERIOD_KEY, event.target.value);
    } catch {
      // Not remembered; that's fine.
    }
    loadTop(event.target.value).catch(showError);
  });
}

async function loadTop(period) {
  const [artists, albums, tracks] = await Promise.all([
    api(`/me/top/artists?period=${period}&limit=10`),
    api(`/me/top/albums?period=${period}&limit=10`),
    api(`/me/top/tracks?period=${period}&limit=10`),
  ]);
  renderRanking(document.getElementById("top-artists"), artists.items, false);
  renderRanking(document.getElementById("top-albums"), albums.items, true);
  renderRanking(document.getElementById("top-tracks"), tracks.items, true);
}

function renderRanking(list, items, withArtist) {
  if (!items.length) {
    replace(list, h("li", { class: "empty" }, "No plays in this period."));
    return;
  }
  const max = items[0].playcount;
  replace(
    list,
    items.map((item) =>
      h(
        "li",
        {},
        h("span", { class: "rank" }, item.rank),
        h("span", { class: "name" }, item.name, withArtist ? h("span", { class: "by" }, item.artist) : null),
        h("span", { class: "plays" }, num(item.playcount)),
        h("span", {
          class: "meter",
          "aria-hidden": "true",
          style: `width: calc((100% - 2.75rem) * ${item.playcount / max})`,
        }),
      ),
    ),
  );
}

// ---- Recently played ----------------------------------------------------------

async function loadRecent() {
  const recent = await api("/me/recent?limit=10");
  const list = document.getElementById("recent");
  if (!recent.items.length) {
    replace(list, h("li", { class: "empty" }, "No scrobbles yet. Connect a player on the Apps page."));
    return;
  }
  replace(
    list,
    recent.items.map((s) =>
      h(
        "li",
        {},
        h("time", { datetime: s.played_at, title: new Date(s.played_at).toLocaleString() }, timeAgo(s.played_at)),
        h("span", {}, h("span", { class: "track" }, s.track), h("span", { class: "by" }, ` by ${s.artist}`)),
      ),
    ),
  );
}
