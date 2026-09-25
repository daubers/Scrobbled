import { ApiError, api } from "./api.js";
import { errorBox, h, replace } from "./dom.js";
import { signedInPage } from "./layout.js";

const root = document.getElementById("approve");
const token = new URLSearchParams(location.search).get("token");

await signedInPage("");

if (!token) {
  replace(root, h("h1", {}, "Nothing to approve"), h("p", { class: "lede" }, "This link is missing its token. Start signing in from your player again."));
} else {
  try {
    const info = await api(`/tokens/${encodeURIComponent(token)}`);
    info.approved ? showApproved(info.app_name) : showRequest(info.app_name);
  } catch (error) {
    showProblem(error);
  }
}

function showRequest(appName) {
  const allow = h("button", { type: "button", class: "button" }, "Allow access");
  allow.addEventListener("click", async () => {
    allow.disabled = true;
    try {
      await api("/tokens/approve", { method: "POST", body: { token } });
      showApproved(appName);
    } catch (error) {
      allow.disabled = false;
      root.append(errorBox(error.message));
    }
  });
  replace(
    root,
    h("h1", {}, "Allow access?"),
    h("div", { class: "approve-app" }, appName),
    h("p", { class: "lede" }, "It wants to scrobble to your account and read your listening history. You can revoke this at any time from Sessions."),
    h("div", { class: "button-row" }, allow, h("a", { class: "button button-quiet", href: "index.html" }, "Don't allow")),
  );
}

function showApproved(appName) {
  replace(
    root,
    h("h1", {}, "Access allowed"),
    h("div", { class: "approve-app" }, appName),
    h("p", { class: "lede" }, "It can now scrobble to your account. Go back to it to finish signing in."),
    h("a", { class: "button button-quiet", href: "sessions.html" }, "View sessions"),
  );
}

function showProblem(error) {
  const expired = error instanceof ApiError && error.status === 404;
  replace(
    root,
    h("h1", {}, expired ? "This link has expired" : "Something went wrong"),
    expired
      ? h("p", { class: "lede" }, "Sign-in links last an hour and work once. Start signing in from your player again to get a new one.")
      : errorBox(error.message),
  );
}
