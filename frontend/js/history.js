import { api } from "./api.js";
import { errorBox, formatDay, formatTime, h, num, replace } from "./dom.js";
import { signedInPage } from "./layout.js";

const PER_PAGE = 50;
const container = document.getElementById("history");
const pager = document.getElementById("pager");

await signedInPage("history.html");
const page = Math.max(1, Number.parseInt(new URLSearchParams(location.search).get("page"), 10) || 1);

try {
  const result = await api(`/me/recent?page=${page}&limit=${PER_PAGE}`);
  render(result);
} catch (error) {
  replace(container, errorBox(error.message));
}

function render(result) {
  document.getElementById("history-summary").textContent =
    `${num(result.total)} scrobbles, newest first. Page ${result.page} of ${result.total_pages}.`;

  if (!result.items.length) {
    replace(container, h("p", { class: "empty" }, "Nothing here yet. Tracks appear once a connected player scrobbles them."));
    return;
  }

  // Group by local calendar day.
  const days = new Map();
  for (const scrobble of result.items) {
    const key = new Date(scrobble.played_at).toDateString();
    if (!days.has(key)) days.set(key, []);
    days.get(key).push(scrobble);
  }
  replace(
    container,
    [...days.values()].map((scrobbles) => [
      h("h2", { class: "log-day" }, formatDay(scrobbles[0].played_at)),
      h(
        "ol",
        { class: "log log-clock" },
        scrobbles.map((s) =>
          h(
            "li",
            {},
            h("time", { datetime: s.played_at }, formatTime(s.played_at)),
            h(
              "span",
              {},
              h("span", { class: "track" }, s.track),
              h("span", { class: "by" }, ` by ${s.artist}`),
              s.album ? h("span", { class: "by" }, `, from ${s.album}`) : null,
            ),
          ),
        ),
      ),
    ]),
  );

  replace(
    pager,
    result.page > 1 ? h("a", { class: "button button-quiet", href: `?page=${result.page - 1}` }, "Newer") : h("span"),
    result.page < result.total_pages
      ? h("a", { class: "button button-quiet", href: `?page=${result.page + 1}` }, "Older")
      : h("span"),
  );
}
