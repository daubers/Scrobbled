// A small thumbnail that starts as a placeholder and swaps itself for the real album art
// once (if) it resolves. Shared by every list/page that shows art.

import { fetchImage } from "./api.js";
import { h } from "./dom.js";

const cache = new Map(); // dedupes repeat/concurrent lookups for the same album within a page

const keyFor = (artist, album) => `${artist.toLowerCase()}␟${album.toLowerCase()}`;

// Forget a looked-up album (after the user uploads or corrects its art) so the next
// artThumb() for it asks the server again instead of reusing the stale answer.
export function forgetArt(artist, album) {
  cache.delete(keyFor(artist, album));
}

export function artThumb(artist, album, extraClass) {
  const classes = extraClass ? `art-thumb ${extraClass}` : "art-thumb";
  const el = h("div", { class: `${classes} art-thumb-empty` });
  if (!album) return el; // nothing to look up

  const key = keyFor(artist, album);
  if (!cache.has(key)) {
    cache.set(
      key,
      fetchImage(`/art/album?artist=${encodeURIComponent(artist)}&album=${encodeURIComponent(album)}`),
    );
  }
  cache.get(key).then((url) => {
    if (!url) return; // miss or failed fetch: leave the placeholder
    // Not revoked: the same URL is shared by every thumbnail for this album on the page
    // (see `cache` above), and this is a full-page-reload site, so the browser releases it
    // on navigation anyway.
    const img = h("img", { class: classes, alt: "", loading: "lazy" });
    img.src = url;
    el.replaceWith(img);
  });
  return el;
}
