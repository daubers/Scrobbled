import { api, describeError } from "./api.js";
import { errorBox, h, replace } from "./dom.js";
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

async function save(event) {
  event.preventDefault();
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
    fill(saved);
    status.textContent = saved.enabled ? `Saved. You're sharing as ${saved.handle}.` : "Saved. Sharing is off.";
  } catch (error) {
    status.textContent = "";
    replace(messages, errorBox(describeError(error)));
    messages.scrollIntoView({ behavior: "smooth", block: "start" });
  } finally {
    button.disabled = false;
  }
}
