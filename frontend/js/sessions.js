import { api } from "./api.js";
import { errorBox, formatDate, h, replace, timeAgo } from "./dom.js";
import { signedInPage } from "./layout.js";

const list = document.getElementById("sessions");
const messages = document.getElementById("messages");

await signedInPage("sessions.html");
await refresh();

async function refresh() {
  try {
    const sessions = await api("/sessions");
    if (!sessions.length) {
      replace(list, h("li", { class: "empty" }, "No players are signed in. They appear here once they sign in with an app's key."));
      return;
    }
    replace(list, sessions.map(sessionRow));
  } catch (error) {
    replace(list, h("li", {}, errorBox(error.message)));
  }
}

function sessionRow(session) {
  const actions = h("div", { class: "confirm" });
  const reset = () =>
    replace(actions, h("button", { type: "button", class: "button button-quiet", onclick: ask }, "Revoke"));
  const ask = () =>
    replace(
      actions,
      h("button", { type: "button", class: "button button-danger", onclick: () => revoke(session, actions) }, "Revoke access"),
      h("button", { type: "button", class: "button button-quiet", onclick: reset }, "Cancel"),
    );
  reset();
  return h(
    "li",
    {},
    h(
      "div",
      { class: "row-head" },
      h(
        "span",
        {},
        h("span", { class: "row-title" }, session.app_name),
        h("span", { class: "muted", title: formatDate(session.created_at) }, `  Signed in ${timeAgo(session.created_at)}`),
      ),
      actions,
    ),
  );
}

async function revoke(session, actions) {
  try {
    await api(`/sessions/${session.id}`, { method: "DELETE" });
    await refresh();
    replace(messages, h("p", { class: "notice notice-ok", role: "status" }, `Revoked ${session.app_name}.`));
  } catch (error) {
    replace(actions, errorBox(error.message));
  }
}
