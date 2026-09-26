import { api } from "./api.js";
import { errorBox, h, replace } from "./dom.js";
import { publicPage } from "./layout.js";

const root = document.getElementById("profile");
const username = new URLSearchParams(location.search).get("u") ?? "";

// A public page: never sends visitors to sign in.
publicPage();

try {
  const profile = await api(`/federation/profiles/${encodeURIComponent(username)}`, { auth: false });
  document.title = `${profile.display_name} · Scrobbler`;
  const copy = h("button", { type: "button", class: "button button-quiet" }, "Copy handle");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(profile.handle);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select and copy it above";
    }
  });
  replace(
    root,
    h("h1", {}, profile.display_name),
    h("div", { class: "approve-app handle" }, profile.handle),
    profile.bio ? h("p", { class: "lede profile-bio" }, profile.bio) : null,
    profile.now_playing
      ? h("p", { class: "now-playing" }, "Now playing: ", h("strong", {}, profile.now_playing))
      : null,
    h("h2", { class: "profile-follow" }, "Follow from the fediverse"),
    h(
      "p",
      { class: "muted" },
      "Search for the handle above in Mastodon or any other fediverse app, then follow.",
    ),
    h("div", { class: "button-row" }, copy),
  );
} catch (error) {
  replace(
    root,
    error.status === 404
      ? [h("h1", {}, "No profile here"), h("p", { class: "lede" }, "No one on this server shares their listening under that name.")]
      : errorBox(error.message),
  );
}
