import { api } from "./api.js";
import { errorBox, formatDate, h, replace, timeAgo } from "./dom.js";
import { signedInPage } from "./layout.js";

const messages = document.getElementById("messages");
const KIND_LABEL = { weekly: "Weekly summary", milestone: "Milestone" };
const VISIBILITY_LABEL = { followers: "Followers only", unlisted: "Unlisted", public: "Public" };

await signedInPage("sharing.html");
await refresh();

async function refresh() {
  let posts;
  try {
    posts = await api("/federation/posts");
  } catch (error) {
    replace(
      messages,
      error.status === 404
        ? h("p", { class: "notice" }, "This server doesn't share to the fediverse.")
        : errorBox(error.message),
    );
    return;
  }
  replace(
    document.getElementById("posts"),
    posts.length
      ? posts.map(postRow)
      : h(
          "li",
          { class: "empty" },
          "Nothing posted yet. Weekly summaries and milestones appear here once you're sharing.",
        ),
  );
}

function postRow(post) {
  const actions = h("div", { class: "confirm" });
  const reset = () =>
    replace(
      actions,
      post.deleted_at
        ? null
        : h("button", { type: "button", class: "button button-quiet", onclick: askDelete }, "Delete"),
    );
  const askDelete = () =>
    replace(
      actions,
      h("span", { class: "muted" }, "Delivered followers keep a copy; this only removes it from their timeline."),
      h("button", { type: "button", class: "button button-danger", onclick: doDelete }, "Delete post"),
      h("button", { type: "button", class: "button button-quiet", onclick: reset }, "Cancel"),
    );
  const doDelete = async () => {
    try {
      await api(`/federation/posts/${post.id}`, { method: "DELETE" });
      replace(messages, h("p", { class: "notice notice-ok", role: "status" }, "Post deleted."));
    } catch (error) {
      replace(messages, errorBox(error.message));
    }
    await refresh();
  };
  reset();
  return h(
    "li",
    {},
    h(
      "div",
      { class: "row-head" },
      h("span", { class: "row-title" }, KIND_LABEL[post.kind] ?? post.kind),
      actions,
    ),
    h("p", { style: "white-space: pre-line" }, post.text),
    h(
      "p",
      { class: "muted", title: formatDate(post.created_at) },
      `${VISIBILITY_LABEL[post.visibility] ?? post.visibility} · Posted ${timeAgo(post.created_at)}`,
      post.deleted_at ? ` · Deleted ${timeAgo(post.deleted_at)}` : null,
    ),
  );
}
