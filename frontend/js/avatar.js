// A round profile image that starts as a placeholder ring and swaps itself for the real
// picture once (if) it loads. `source` is either a signed-in user (their own image,
// fetched with their token) or { path } for another public image URL.

import { fetchImage } from "./api.js";
import { h } from "./dom.js";

export function avatarImage(user, extraClass, path = "/me/avatar") {
  const classes = extraClass ? `avatar ${extraClass}` : "avatar";
  const el = h("span", { class: `${classes} avatar-empty`, "aria-hidden": "true" });
  if (!user?.has_avatar) return el;
  fetchImage(path).then((url) => {
    if (!url) return; // miss or failed fetch: leave the placeholder
    const img = h("img", { class: classes, alt: "" });
    img.src = url;
    el.replaceWith(img);
  });
  return el;
}
