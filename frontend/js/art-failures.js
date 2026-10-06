import { api, describeError, upload } from "./api.js";
import { artThumb, forgetArt } from "./art.js";
import { errorBox, h, num, replace } from "./dom.js";
import { signedInPage } from "./layout.js";

const PER_PAGE = 25;
const list = document.getElementById("albums");
const messages = document.getElementById("messages");
const pager = document.getElementById("pager");
const page = Math.max(1, Number.parseInt(new URLSearchParams(location.search).get("page"), 10) || 1);

await signedInPage("art.html");
await refresh();

async function refresh() {
  try {
    render(await api(`/art/failures?page=${page}&limit=${PER_PAGE}`));
  } catch (error) {
    replace(list, h("li", {}, errorBox(error.message)));
  }
}

function render(result) {
  document.getElementById("art-summary").textContent = result.total
    ? `${num(result.total)} of your albums have no cover art, most-played first. Upload a picture, retry the lookup, or correct what it searches for.`
    : "Every album you've scrobbled has cover art, or is still being looked up.";
  if (!result.items.length) {
    replace(list);
  } else {
    replace(list, result.items.map(albumRow));
  }
  replace(
    pager,
    result.page > 1 ? h("a", { class: "button button-quiet", href: `?page=${result.page - 1}` }, "Previous") : h("span"),
    result.page < result.total_pages
      ? h("a", { class: "button button-quiet", href: `?page=${result.page + 1}` }, "Next")
      : h("span"),
  );
}

function statusText(item) {
  const own = item.override;
  if (own?.status === "pending") return "Your correction is waiting to be looked up.";
  if (own?.status === "error") {
    return own.error_code === "NotFound"
      ? "Your correction didn't find any art."
      : `Your correction failed (${own.error_code ?? "unknown error"}).`;
  }
  if (item.status === "error") return `The lookup failed (${item.error_code ?? "unknown error"}). It will be tried again.`;
  if (item.status === "not_found") return "No cover art was found for this album.";
  return "Waiting to be looked up.";
}

function say(notice) {
  replace(messages, notice);
}

function albumRow(item) {
  const { artist, album } = item;
  const query = `artist=${encodeURIComponent(artist)}&album=${encodeURIComponent(album)}`;
  const fileInput = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", hidden: true });
  const correction = correctionForm(item, query);
  const buttons = h("div", { class: "button-row" });
  const busy = (on) => buttons.querySelectorAll("button").forEach((b) => (b.disabled = on));

  async function act(work, done) {
    busy(true);
    replace(messages);
    try {
      await work();
      say(h("p", { class: "notice notice-ok", role: "status" }, done));
      forgetArt(artist, album);
      await refresh();
    } catch (error) {
      say(errorBox(describeError(error)));
    } finally {
      busy(false);
    }
  }

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    const data = new FormData();
    data.append("file", file);
    act(() => upload(`/art/album?${query}`, data), `Uploaded art for ${album}.`);
    fileInput.value = "";
  });

  replace(
    buttons,
    h("button", { type: "button", class: "button", onclick: () => fileInput.click() }, "Upload art"),
    h(
      "button",
      {
        type: "button",
        class: "button button-quiet",
        onclick: () => act(() => api(`/art/album/retry?${query}`, { method: "POST" }), `Looking up ${album} again.`),
      },
      "Retry lookup",
    ),
    h(
      "button",
      { type: "button", class: "button button-quiet", onclick: () => (correction.hidden = !correction.hidden) },
      "Correct search",
    ),
    item.override
      ? h(
          "button",
          {
            type: "button",
            class: "button button-quiet",
            onclick: () => act(() => api(`/art/album?${query}`, { method: "DELETE" }), `Removed your correction for ${album}.`),
          },
          "Remove mine",
        )
      : null,
  );
  correction.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = { artist, album };
    const { search_artist, search_album, release_group_mbid, release_mbid } = correction.elements;
    if (search_artist.value.trim()) body.search_artist = search_artist.value.trim();
    if (search_album.value.trim()) body.search_album = search_album.value.trim();
    if (release_group_mbid.value.trim()) body.release_group_mbid = release_group_mbid.value.trim();
    if (release_mbid.value.trim()) body.release_mbid = release_mbid.value.trim();
    act(() => api("/art/album/correction", { method: "PUT", body }), `Queued a corrected lookup for ${album}.`);
  });

  return h(
    "li",
    { class: "art-row" },
    artThumb(artist, album, "art-thumb-lg"),
    h(
      "div",
      { class: "art-row-body" },
      h("span", { class: "row-title" }, album),
      h("span", { class: "by" }, ` by ${artist}`),
      h("p", { class: "muted" }, `${num(item.scrobbles)} ${item.scrobbles === 1 ? "scrobble" : "scrobbles"} · ${statusText(item)}`),
      buttons,
      fileInput,
      correction,
    ),
  );
}

function correctionForm(item, query) {
  const own = item.override ?? {};
  const field = (name, label, value, hint, extra = {}) =>
    h(
      "div",
      { class: "field" },
      h("label", { for: `${name}-${query}` }, label),
      h("input", { id: `${name}-${query}`, name, type: "text", value: value ?? "", autocomplete: "off", ...extra }),
      hint ? h("p", { class: "hint" }, hint) : null,
    );
  return h(
    "form",
    { class: "stacked-form art-correction", hidden: true },
    field("search_artist", "Search for artist", own.search_artist ?? item.artist),
    field("search_album", "Search for album", own.search_album ?? item.album),
    field(
      "release_group_mbid",
      "MusicBrainz release-group ID (optional)",
      own.release_group_mbid,
      "If you know it, paste the ID from the album's MusicBrainz page. It skips the search.",
      { placeholder: "00000000-0000-0000-0000-000000000000", pattern: "[0-9a-fA-F-]{36}" },
    ),
    field(
      "release_mbid",
      "MusicBrainz release ID (optional)",
      own.release_mbid,
      "Or a specific release's ID, if that's the one you have. Use either this or the release-group ID.",
      { placeholder: "00000000-0000-0000-0000-000000000000", pattern: "[0-9a-fA-F-]{36}" },
    ),
    h("button", { type: "submit", class: "button" }, "Look it up"),
  );
}
