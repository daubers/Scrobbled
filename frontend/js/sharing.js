import { api, describeError } from "./api.js";
import { errorBox, h, num, replace } from "./dom.js";
import { signedInPage } from "./layout.js";

const form = document.getElementById("sharing");
const messages = document.getElementById("messages");
const handlePanel = document.getElementById("handle-panel");
const FIELDS = ["enabled", "visibility", "manually_approves_followers", "discoverable", "indexable", "display_name", "bio"];

await signedInPage("sharing.html");

let settings;
try {
  settings = await api("/federation/settings");
} catch (error) {
  replace(
    messages,
    error.status === 404
      ? h("p", { class: "notice" }, "This server doesn't share to the fediverse. Ask whoever runs it to turn it on.")
      : errorBox(error.message),
  );
}

if (settings) {
  fill(settings);
  form.hidden = false;
  form.elements.enabled.addEventListener("change", () => showHandle(form.elements.enabled.checked));
  form.addEventListener("submit", save);
}

function fill(values) {
  for (const name of FIELDS) {
    const field = form.elements[name];
    if (name === "visibility") {
      form.querySelector(`input[name=visibility][value="${values.visibility}"]`).checked = true;
    } else if (field.type === "checkbox") {
      field.checked = Boolean(values[name]);
    } else {
      field.value = values[name] ?? "";
    }
  }
  document.getElementById("handle").textContent = values.handle;
  const summary = document.getElementById("follower-summary");
  const parts = [`${num(values.followers)} ${values.followers === 1 ? "follower" : "followers"}`];
  if (values.pending_followers) parts.push(`${num(values.pending_followers)} waiting for approval`);
  replace(summary, parts.join(", "), ". ", h("a", { href: "followers.html" }, "See followers"));
  replace(
    document.getElementById("profile-link"),
    "Your public profile page: ",
    h("a", { href: values.profile_url }, values.profile_url.replace(/^https?:\/\//, "")),
  );
  showHandle(values.enabled);
}

function showHandle(on) {
  handlePanel.hidden = !on;
}

// Turning sharing off removes every follower, so it's confirmed first.
function confirmTurningOff(followerCount) {
  return new Promise((resolve) => {
    const box = document.getElementById("confirm-off");
    const done = (answer) => {
      box.hidden = true;
      resolve(answer);
    };
    replace(
      box,
      h(
        "p",
        {},
        `Turning sharing off removes your ${num(followerCount)} ${followerCount === 1 ? "follower" : "followers"}. `,
        "Each is told, and they'd have to follow you again if you turn it back on.",
      ),
      h(
        "div",
        { class: "button-row" },
        h("button", { type: "button", class: "button button-danger", onclick: () => done(true) }, "Turn off sharing"),
        h(
          "button",
          {
            type: "button",
            class: "button button-quiet",
            onclick: () => {
              form.elements.enabled.checked = true;
              showHandle(true);
              done(false);
            },
          },
          "Keep sharing",
        ),
      ),
    );
    box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

async function save(event) {
  event.preventDefault();
  const turningOff = settings.enabled && !form.elements.enabled.checked;
  if (turningOff && settings.followers > 0 && !(await confirmTurningOff(settings.followers))) return;
  const button = form.querySelector("button[type=submit]");
  const changes = {
    enabled: form.elements.enabled.checked,
    visibility: form.querySelector("input[name=visibility]:checked").value,
    manually_approves_followers: form.elements.manually_approves_followers.checked,
    discoverable: form.elements.discoverable.checked,
    indexable: form.elements.indexable.checked,
    display_name: form.elements.display_name.value.trim() || null,
    bio: form.elements.bio.value.trim() || null,
  };
  const status = document.getElementById("save-status");
  button.disabled = true;
  status.textContent = "Saving";
  replace(messages);
  try {
    const saved = await api("/federation/settings", { method: "PATCH", body: changes });
    settings = saved;
    fill(saved);
    status.textContent = saved.enabled
      ? `Saved. You're sharing as ${saved.handle}.`
      : "Saved. Sharing is off, and your followers have been told.";
  } catch (error) {
    status.textContent = "";
    replace(messages, errorBox(describeError(error)));
    messages.scrollIntoView({ behavior: "smooth", block: "start" });
  } finally {
    button.disabled = false;
  }
}
