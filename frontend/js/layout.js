// Shared page shell: site header, navigation, sign-in guard.

import { api, getToken, redirectToLogin, setToken } from "./api.js";
import { h, replace } from "./dom.js";

const NAV = [
  ["index.html", "Overview"],
  ["history.html", "History"],
  ["apps.html", "Apps"],
  ["sessions.html", "Sessions"],
];

// Renders the header and returns the signed-in user (redirecting if there isn't one).
export async function signedInPage(active) {
  if (!getToken()) {
    redirectToLogin();
    return new Promise(() => {}); // navigation is under way
  }
  const header = document.getElementById("site-header");
  replace(header, brand(), nav(active));
  const user = await api("/auth/me");
  header.append(
    h(
      "div",
      { class: "account" },
      h("span", { class: "account-name" }, user.username),
      h("button", { type: "button", class: "link-button", onclick: signOut }, "Sign out"),
    ),
  );
  return user;
}

export function publicPage() {
  replace(document.getElementById("site-header"), brand());
}

function brand() {
  return h("a", { class: "brand", href: "index.html" }, h("span", { class: "brand-mark", "aria-hidden": "true" }), "Scrobbler");
}

function nav(active) {
  return h(
    "nav",
    { "aria-label": "Main" },
    h(
      "ul",
      {},
      NAV.map(([href, label]) =>
        h("li", {}, h("a", { href, "aria-current": href === active ? "page" : false }, label)),
      ),
    ),
  );
}

async function signOut() {
  try {
    await api("/auth/logout", { method: "POST" });
  } finally {
    setToken(null);
    location.href = "login.html";
  }
}
