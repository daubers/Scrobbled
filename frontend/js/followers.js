import { api } from "./api.js";
import { errorBox, formatDate, h, replace, timeAgo } from "./dom.js";
import { signedInPage } from "./layout.js";

const messages = document.getElementById("messages");

await signedInPage("sharing.html");
await refresh();

async function refresh() {
  let followers, blocks;
  try {
    [followers, blocks] = await Promise.all([api("/federation/followers"), api("/federation/blocks")]);
  } catch (error) {
    replace(
      messages,
      error.status === 404
        ? h("p", { class: "notice" }, "This server doesn't share to the fediverse.")
        : errorBox(error.message),
    );
    return;
  }
  const requests = followers.filter((f) => f.state === "pending");
  const accepted = followers.filter((f) => f.state === "accepted");

  document.getElementById("requests-section").hidden = requests.length === 0;
  replace(document.getElementById("requests"), requests.map(requestRow));
  replace(
    document.getElementById("followers"),
    accepted.length
      ? accepted.map(followerRow)
      : h("li", { class: "empty" }, "No followers yet. People can follow you by searching for your handle."),
  );
  document.getElementById("blocks-section").hidden = blocks.length === 0;
  replace(document.getElementById("blocks"), blocks.map(blockRow));
}

// Remote names and handles are shown as plain text only.
function who(person) {
  return h(
    "span",
    {},
    h("span", { class: "row-title" }, person.display_name || person.handle),
    person.display_name ? h("span", { class: "muted" }, ` ${person.handle}`) : null,
  );
}

async function act(path, done) {
  try {
    await api(path, { method: "POST" });
    replace(messages, h("p", { class: "notice notice-ok", role: "status" }, done));
  } catch (error) {
    replace(messages, errorBox(error.message));
  }
  await refresh();
}

function requestRow(follower) {
  return h(
    "li",
    {},
    h(
      "div",
      { class: "row-head" },
      who(follower),
      h(
        "div",
        { class: "confirm" },
        h(
          "button",
          { type: "button", class: "button", onclick: () => act(`/federation/followers/${follower.id}/approve`, `${follower.handle} now follows you.`) },
          "Approve",
        ),
        h(
          "button",
          { type: "button", class: "button button-quiet", onclick: () => act(`/federation/followers/${follower.id}/decline`, `Declined ${follower.handle}.`) },
          "Decline",
        ),
      ),
    ),
    h("p", { class: "muted", title: formatDate(follower.since) }, `Asked ${timeAgo(follower.since)}`),
  );
}

function followerRow(follower) {
  const actions = h("div", { class: "confirm" });
  const reset = () =>
    replace(
      actions,
      h("button", { type: "button", class: "button button-quiet", onclick: askRemove }, "Remove"),
      h("button", { type: "button", class: "button button-quiet", onclick: askBlock }, "Block"),
    );
  const confirm = (question, label, path, done) =>
    replace(
      actions,
      h("span", { class: "muted" }, question),
      h("button", { type: "button", class: "button button-danger", onclick: () => act(path, done) }, label),
      h("button", { type: "button", class: "button button-quiet", onclick: reset }, "Cancel"),
    );
  const askRemove = () =>
    confirm(
      "They can follow you again later.",
      "Remove follower",
      `/federation/followers/${follower.id}/remove`,
      `Removed ${follower.handle}.`,
    );
  const askBlock = () =>
    confirm(
      "They're removed and can't follow you again.",
      "Block",
      `/federation/followers/${follower.id}/block`,
      `Blocked ${follower.handle}.`,
    );
  reset();
  return h(
    "li",
    {},
    h("div", { class: "row-head" }, who(follower), actions),
    h("p", { class: "muted", title: formatDate(follower.since) }, `Following since ${timeAgo(follower.since)}`),
  );
}

function blockRow(block) {
  const unblock = async () => {
    try {
      await api(`/federation/blocks/${block.id}`, { method: "DELETE" });
      replace(messages, h("p", { class: "notice notice-ok", role: "status" }, `Unblocked ${block.handle}.`));
    } catch (error) {
      replace(messages, errorBox(error.message));
    }
    await refresh();
  };
  return h(
    "li",
    {},
    h(
      "div",
      { class: "row-head" },
      who(block),
      h("button", { type: "button", class: "button button-quiet", onclick: unblock }, "Unblock"),
    ),
  );
}
