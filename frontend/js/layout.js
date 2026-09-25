// Shared page shell: site header, navigation, sign-in guard.

import { api, getToken, redirectToLogin, setToken } from "./api.js";
import { h, replace } from "./dom.js";

const NAV = [
  ["index.html", "Overview"],
  ["history.html", "History"],
  ["apps.html", "Apps"],
  ["sessions.html", "Sessions"],
  ["import.html", "Import"],
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
  addSharingLink(header.querySelector("nav ul"), active);
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

// "Sharing" only appears when the server federates. Remembered per tab.
const FEDERATION_KEY = "scrobbler.federation";

async function federationAvailable() {
  try {
    const cached = sessionStorage.getItem(FEDERATION_KEY);
    if (cached !== null) return cached === "1";
  } catch {
    // Storage unavailable: just ask.
  }
  let available = false;
  try {
    await api("/federation/settings");
    available = true;
  } catch {
    available = false;
  }
  try {
    sessionStorage.setItem(FEDERATION_KEY, available ? "1" : "0");
  } catch {
    // Not remembered; asked again next page.
  }
  return available;
}

async function addSharingLink(list, active) {
  if (!list || !(await federationAvailable())) return;
  const href = "sharing.html";
  list.append(h("li", {}, h("a", { href, "aria-current": href === active ? "page" : false }, "Sharing")));
}

