import { api } from "./api.js";
import { errorBox, formatDate, h, replace } from "./dom.js";
import { publicPage } from "./layout.js";

const root = document.getElementById("post");
const params = new URLSearchParams(location.search);
const username = params.get("u") ?? "";
const postId = params.get("id") ?? "";

// A public page: never sends visitors to sign in.
publicPage();

try {
  const post = await api(
    `/federation/profiles/${encodeURIComponent(username)}/posts/${encodeURIComponent(postId)}`,
    { auth: false },
  );
  document.title = `${post.display_name} · Scrobbler`;
  replace(
    root,
    h(
      "p",
      { class: "muted" },
      h("a", { href: post.profile_url }, post.display_name),
      ` · ${post.handle}`,
    ),
    h("p", { style: "white-space: pre-line" }, post.text),
    h("p", { class: "muted", title: formatDate(post.created_at) }, `Posted ${formatDate(post.created_at)}`),
  );
} catch (error) {
  replace(
    root,
    error.status === 404
      ? [
          h("h1", {}, "No post here"),
          h("p", { class: "lede" }, "This post doesn't exist, isn't public, or has been deleted."),
        ]
      : errorBox(error.message),
  );
}
