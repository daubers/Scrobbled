// A small thumbnail that starts as a placeholder and swaps itself for the real album art
// once (if) it resolves. Shared by every list/page that shows art.

import { fetchImage } from "./api.js";
import { h } from "./dom.js";

const cache = new Map(); // dedupes repeat/concurrent lookups for the same album within a page

export function artThumb(artist, album, extraClass) {
  const classes = extraClass ? `art-thumb ${extraClass}` : "art-thumb";
  const el = h("div", { class: `${classes} art-thumb-empty` });
  if (!album) return el; // nothing to look up

  const key = `${artist.toLowerCase()}␟${album.toLowerCase()}`;
  if (!cache.has(key)) {
    cache.set(
      key,
      fetchImage(`/art/album?artist=${encodeURIComponent(artist)}&album=${encodeURIComponent(album)}`),
    );
  }
  cache.get(key).then((url) => {
    if (!url) return; // miss or failed fetch: leave the placeholder
    const img = h("img", { class: classes, alt: "", loading: "lazy" });
    img.addEventListener("load", () => URL.revokeObjectURL(url), { once: true });
    img.src = url;
    el.replaceWith(img);
  });
  return el;
}
