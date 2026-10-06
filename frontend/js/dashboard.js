import { api } from "./api.js";
import { artThumb } from "./art.js";
import { errorBox, formatDuration, h, num, replace, timeAgo } from "./dom.js";
import { signedInPage } from "./layout.js";
import { spectrum } from "./spectrum.js";

const PERIODS = [
  ["7day", "7 days"],
  ["1month", "30 days"],
  ["12month", "12 months"],
  ["overall", "All time"],
];
const PERIOD_KEY = "scrobbler.period";
const NOW_PLAYING_POLL_MS = 30_000;

const panel = document.getElementById("panel");
const main = document.getElementById("main");

let progressTimer;

await signedInPage("index.html");
renderPeriodSelector();
initSongSearch();
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
  renderRanking(document.getElementById("top-albums"), albums.items, true, false, true);
  renderRanking(document.getElementById("top-tracks"), tracks.items, true, true);
}

function renderRanking(list, items, withArtist, linkToSong = false, withArt = false) {
  if (!items.length) {
    replace(list, h("li", { class: "empty" }, "No plays in this period."));
    return;
  }
  const max = items[0].playcount;
  const meterOffset = withArt ? "5.75rem" : "2.75rem";
  replace(
    list,
    items.map((item) => {
      const by = withArtist ? h("span", { class: "by" }, item.artist) : null;
      const name = linkToSong
        ? h("a", { class: "name", href: songHref(item.artist, item.name) }, item.name, by)
        : h("span", { class: "name" }, item.name, by);
      return h(
        "li",
        {},
        withArt ? artThumb(item.artist, item.name) : null,
        h("span", { class: "rank" }, item.rank),
        name,
        h("span", { class: "plays" }, num(item.playcount)),
        h("span", {
          class: "meter",
          "aria-hidden": "true",
          style: `width: calc((100% - ${meterOffset}) * ${item.playcount / max})`,
        }),
      );
    }),
  );
}

function songHref(artist, track) {
  return `song.html?artist=${encodeURIComponent(artist)}&track=${encodeURIComponent(track)}`;
}

// ---- Song search --------------------------------------------------------------

function initSongSearch() {
  const input = document.getElementById("song-search");
  const results = document.getElementById("song-search-results");
  let debounce;
  let hideTimer;

  input.addEventListener("input", () => {
    clearTimeout(debounce);
    const q = input.value.trim();
    if (q.length < 2) {
      results.hidden = true;
      return;
    }
    debounce = setTimeout(() => runSearch(q), 250);
  });
  input.addEventListener("focus", () => {
    if (results.childElementCount) results.hidden = false;
  });
  input.addEventListener("blur", () => {
    hideTimer = setTimeout(() => (results.hidden = true), 150);
  });
  // Keep focus on mousedown so the subsequent click still lands on the result.
  results.addEventListener("mousedown", (event) => event.preventDefault());

  async function runSearch(q) {
    let matches;
    try {
      matches = await api(`/me/tracks/search?q=${encodeURIComponent(q)}&limit=10`);
    } catch {
      return; // a flaky search box isn't worth surfacing an error for
    }
    clearTimeout(hideTimer);
    if (!matches.length) {
      replace(results, h("li", { class: "empty" }, "No matches."));
    } else {
      replace(
        results,
        matches.map((m) =>
          h(
            "li",
            {},
            h("a", { href: songHref(m.artist, m.track) }, m.track, h("span", { class: "by" }, m.artist)),
          ),
        ),
      );
    }
    results.hidden = false;
  }
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
        artThumb(s.album_artist || s.artist, s.album),
        h("time", { datetime: s.played_at, title: new Date(s.played_at).toLocaleString() }, timeAgo(s.played_at)),
        h(
          "a",
          { href: songHref(s.artist, s.track) },
          h("span", { class: "track" }, s.track),
          h("span", { class: "by" }, s.artist),
        ),
      ),
    ),
  );
}
