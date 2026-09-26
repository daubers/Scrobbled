import { api, describeError } from "./api.js";
import { errorBox, h, num, replace } from "./dom.js";
import { signedInPage } from "./layout.js";

const form = document.getElementById("sharing");
const messages = document.getElementById("messages");
const handlePanel = document.getElementById("handle-panel");
const FIELDS = [
  "enabled",
  "visibility",
  "manually_approves_followers",
  "discoverable",
  "indexable",
  "post_weekly_summary",
  "post_milestones",
  "timezone",
  "display_name",
  "bio",
];

fillTimezoneChoices();
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
  document.getElementById("preview-weekly").addEventListener("click", previewWeekly);
}

// Free text (validated server-side), with autocomplete from the browser's own IANA list
// when it can supply one; a picker where that's available, a plain field otherwise.
function fillTimezoneChoices() {
  if (typeof Intl.supportedValuesOf !== "function") return;
  const datalist = document.getElementById("timezones");
  replace(datalist, Intl.supportedValuesOf("timeZone").map((zone) => h("option", { value: zone })));
}

async function previewWeekly() {
  const box = document.getElementById("weekly-preview");
  const button = document.getElementById("preview-weekly");
  button.disabled = true;
  try {
    const preview = await api("/federation/preview/weekly");
    replace(
      box,
      h("p", { class: "muted" }, "What this week's summary would say so far:"),
      h("p", { style: "white-space: pre-line" }, preview.text),
    );
    box.hidden = false;
  } catch (error) {
    replace(messages, errorBox(error.message));
  } finally {
    button.disabled = false;
  }
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
    post_weekly_summary: form.elements.post_weekly_summary.checked,
    post_milestones: form.elements.post_milestones.checked,
    timezone: form.elements.timezone.value.trim() || "UTC",
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
