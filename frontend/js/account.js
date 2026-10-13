import { api, describeError, upload } from "./api.js";
import { avatarImage } from "./avatar.js";
import { errorBox, h, replace } from "./dom.js";
import { signedInPage } from "./layout.js";

const root = document.getElementById("avatar-panel");
const messages = document.getElementById("messages");

let user = await signedInPage("account.html");
render();

const SOURCES = {
  upload: "An image you uploaded.",
  gravatar: "Your Gravatar, copied to this server.",
  none: "You haven't set a profile image yet.",
};

function say(notice) {
  replace(messages, notice);
}

// Runs a change, adopts the user it returns (or refetches), and redraws everything that
// shows the image - this panel and the one in the page header.
async function change(action, done) {
  const buttons = root.querySelectorAll("button");
  buttons.forEach((button) => (button.disabled = true));
  try {
    user = (await action()) ?? (await api("/auth/me"));
    say(h("p", { class: "notice notice-ok" }, done));
  } catch (error) {
    say(errorBox(describeError(error)));
  }
  render();
  const slot = document.querySelector(".account-avatar");
  if (slot) replace(slot, avatarImage(user, "avatar-sm"));
}

function render() {
  const file = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", hidden: true });
  file.addEventListener("change", () => {
    if (!file.files[0]) return;
    const data = new FormData();
    data.append("file", file.files[0]);
    change(() => upload("/me/avatar", data, undefined, "PUT"), "Profile image updated.");
  });

  const buttons = [
    h("button", { type: "button", class: "button", onclick: () => file.click() }, "Upload image"),
    user.gravatar_enabled
      ? h(
          "button",
          {
            type: "button",
            class: "button button-quiet",
            onclick: () => change(() => api("/me/avatar/gravatar", { method: "POST" }), "Now using your Gravatar."),
          },
          user.avatar_source === "gravatar" ? "Refresh Gravatar" : "Use my Gravatar",
        )
      : null,
    user.has_avatar
      ? h(
          "button",
          {
            type: "button",
            class: "button button-quiet",
            onclick: () => change(() => api("/me/avatar", { method: "DELETE" }), "Profile image removed."),
          },
          "Remove",
        )
      : null,
  ];

  replace(
    root,
    h(
      "div",
      { class: "avatar-row" },
      avatarImage(user, "avatar-lg"),
      h(
        "div",
        { class: "avatar-body" },
        h("p", {}, SOURCES[user.avatar_source] ?? SOURCES.none),
        h("p", { class: "muted" }, "JPEG, PNG or WebP. Only you see it, unless you choose to share it on your fediverse profile."),
        h("div", { class: "button-row" }, buttons, file),
        user.gravatar_enabled
          ? h(
              "p",
              { class: "muted" },
              "Using Gravatar makes this server ask Gravatar for the picture linked to your email address, sending it a hash of that address (not the address itself). Your browser never contacts Gravatar.",
            )
          : null,
      ),
    ),
  );
}
