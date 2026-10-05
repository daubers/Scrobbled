import { api } from "./api.js";
import { errorBox, formatDate, formatDay, formatDuration, formatTime, h, num, replace } from "./dom.js";
import { signedInPage } from "./layout.js";
import { spectrum } from "./spectrum.js";

const PER_PAGE = 50;
const panel = document.getElementById("panel");
const meta = document.getElementById("song-meta");
const history = document.getElementById("song-history");
const pager = document.getElementById("pager");

await signedInPage(null); // a permalink, not a top-level nav section

const params = new URLSearchParams(location.search);
const artist = params.get("artist") ?? "";
const track = params.get("track") ?? "";
const page = Math.max(1, Number.parseInt(params.get("page"), 10) || 1);

if (!artist || !track) {
  replace(panel, h("p", { class: "notice" }, "Use the search box on the dashboard to find a song."));
} else {
  try {
    const result = await api(
      `/me/tracks?artist=${encodeURIComponent(artist)}&track=${encodeURIComponent(track)}&page=${page}&limit=${PER_PAGE}`,
    );
    render(result);
  } catch (error) {
    replace(
      panel,
      error.status === 404
        ? h("p", { class: "notice" }, `You've never scrobbled "${track}" by ${artist}.`)
        : errorBox(error.message),
    );
  }
}

function render(result) {
  document.title = `${result.track} · Scrobbler`;
  const m = result.metadata;

  const display = h(
    "div",
    { class: "display" },
    h("p", { class: "display-status" }, "Now viewing"),
    h("p", { class: "display-track" }, result.track),
    h(
      "p",
      { class: "display-meta" },
      h("strong", {}, result.artist),
      m.album ? [" from ", h("strong", {}, m.album)] : null,
    ),
  );
  replace(panel, display, readouts(m), spectrum(result.counts, `Plays of ${result.track}`, "90 days"));

  const row = (label, value) =>
    value === null || value === undefined ? null : h("div", {}, h("dt", {}, label), h("dd", {}, value));
  replace(
    meta,
    row("Album artist", m.album_artist),
    row("Track number", m.track_number),
    row("Duration", m.duration ? formatDuration(m.duration) : null),
    row("MusicBrainz ID", m.mbid),
  );

  if (!result.items.length) {
    replace(history, h("p", { class: "empty" }, "Nothing here yet."));
  } else {
    // Group by local calendar day, same as the full history page.
    const days = new Map();
    for (const scrobble of result.items) {
      const key = new Date(scrobble.played_at).toDateString();
      if (!days.has(key)) days.set(key, []);
      days.get(key).push(scrobble);
    }
    replace(
      history,
      [...days.values()].map((scrobbles) => [
        h("h3", { class: "log-day" }, formatDay(scrobbles[0].played_at)),
        h(
          "ol",
          { class: "log log-clock" },
          scrobbles.map((s) =>
            h(
              "li",
              {},
              h("time", { datetime: s.played_at }, formatTime(s.played_at)),
              s.album ? h("span", { class: "by" }, `from ${s.album}`) : h("span"),
            ),
          ),
        ),
      ]),
    );
  }

  const link = (p) => `?artist=${encodeURIComponent(artist)}&track=${encodeURIComponent(track)}&page=${p}`;
  replace(
    pager,
    result.page > 1 ? h("a", { class: "button button-quiet", href: link(result.page - 1) }, "Newer") : h("span"),
    result.page < result.total_pages
      ? h("a", { class: "button button-quiet", href: link(result.page + 1) }, "Older")
      : h("span"),
  );
}

function readouts(m) {
  const item = (value, label) => h("div", {}, h("dt", {}, label), h("dd", {}, value));
  return h(
    "dl",
    { class: "readouts" },
    item(num(m.playcount), "plays"),
    item(formatDate(m.first_played_at), "first played"),
    item(formatDate(m.last_played_at), "last played"),
  );
}
