import { api, apiBase, describeError } from "./api.js";
import { errorBox, formatDate, h, replace } from "./dom.js";
import { signedInPage } from "./layout.js";

const list = document.getElementById("apps");
const messages = document.getElementById("messages");
const form = document.getElementById("create-app");

const user = await signedInPage("apps.html");
renderSetup();
await refresh();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  replace(messages);
  try {
    const created = await api("/apps", { method: "POST", body: { name: form.elements.name.value.trim() } });
    form.reset();
    await refresh(created.api_key);
    replace(messages, h("p", { class: "notice notice-ok", role: "status" }, `Created ${created.name}. Copy its key and secret into the player.`));
  } catch (error) {
    replace(messages, errorBox(describeError(error)));
  } finally {
    button.disabled = false;
  }
});

async function refresh(revealKey) {
  try {
    const apps = await api("/apps");
    if (!apps.length) {
      replace(list, h("li", { class: "empty" }, "No apps yet. Create one above for each player you want to connect."));
      return;
    }
    replace(list, apps.map((app) => appRow(app, app.api_key === revealKey)));
  } catch (error) {
    replace(list, h("li", {}, errorBox(error.message)));
  }
}

function appRow(app, reveal) {
  const row = h("li");
  const secret = h("code", {}, reveal ? app.shared_secret : "••••••••••••••••");
  const toggle = h("button", { type: "button", class: "link-button" }, reveal ? "Hide" : "Show");
  let shown = reveal;
  toggle.addEventListener("click", () => {
    shown = !shown;
    secret.textContent = shown ? app.shared_secret : "••••••••••••••••";
    toggle.textContent = shown ? "Hide" : "Show";
  });

  const actions = h("div", { class: "confirm" });
  const askDelete = () =>
    replace(
      actions,
      h("span", { class: "muted" }, "Players using this app stop scrobbling."),
      h("button", { type: "button", class: "button button-danger", onclick: () => remove(app, actions) }, "Delete app"),
      h("button", { type: "button", class: "button button-quiet", onclick: resetActions }, "Keep it"),
    );
  const resetActions = () =>
    replace(actions, h("button", { type: "button", class: "button button-quiet", onclick: askDelete }, "Delete"));
  resetActions();

  return replace(
    row,
    h("div", { class: "row-head" }, h("span", { class: "row-title" }, app.name), actions),
    h(
      "dl",
      { class: "keys" },
      h("dt", {}, "API key"),
      h("dd", {}, h("code", {}, app.api_key), copyButton(app.api_key)),
      h("dt", {}, "Shared secret"),
      h("dd", {}, secret, toggle, copyButton(app.shared_secret)),
      h("dt", {}, "Created"),
      h("dd", {}, formatDate(app.created_at)),
    ),
  );
}

async function remove(app, actions) {
  try {
    await api(`/apps/${encodeURIComponent(app.api_key)}`, { method: "DELETE" });
    await refresh();
    replace(messages, h("p", { class: "notice notice-ok", role: "status" }, `Deleted ${app.name}.`));
  } catch (error) {
    replace(actions, errorBox(error.message));
  }
}

function copyButton(value) {
  const button = h("button", { type: "button", class: "link-button" }, "Copy");
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy manually";
    }
    setTimeout(() => {
      button.textContent = "Copy";
    }, 2000);
  });
  return button;
}

function renderSetup() {
  replace(
    document.getElementById("setup"),
    h("dt", {}, "API URL"),
    h("dd", {}, h("code", {}, `${apiBase}/2.0/`)),
    h("dt", {}, "Username"),
    h("dd", {}, h("code", {}, user.username)),
    h("dt", {}, "Password"),
    h("dd", {}, "Your Scrobbler password"),
    h("dt", {}, "Key and secret"),
    h("dd", {}, "From one of the apps above"),
    h("dt", {}, "API reference"),
    h("dd", {}, h("a", { href: `${apiBase}/api/docs` }, "Interactive API docs")),
  );
}
