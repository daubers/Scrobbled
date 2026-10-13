// Shared page shell: site header, navigation, sign-in guard.

import { api, getToken, redirectToLogin, setToken } from "./api.js";
import { avatarImage } from "./avatar.js";
import { h, replace } from "./dom.js";

const NAV = [
  ["index.html", "Overview"],
  ["history.html", "History"],
  ["art.html", "Album art"],
];

// Account pages live in the menu under the user's name. "Sharing" joins them (after
// Profile) when the server federates.
const MENU = [
  ["account.html", "Profile"],
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
  const menu = accountMenu(user, active);
  header.append(menu.element);
  addSharingLink(menu.list, active);
  return user;
}

export function publicPage() {
  replace(document.getElementById("site-header"), brand());
}

function brand() {
  return h(
    "a",
    { class: "brand", href: "index.html" },
    h("span", { class: "brand-mark", "aria-hidden": "true" }),
    h("span", { class: "brand-word", "aria-hidden": "true" }),
    h("span", { class: "visually-hidden" }, "Scrobbled"),
  );
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

// The account menu: a button showing the user's picture and name that opens a list of
// account pages and "Sign out". Returns the element and its <ul> (for late additions).
function accountMenu(user, active) {
  const list = h("ul", { class: "account-menu", id: "account-menu", hidden: true });
  for (const [href, label] of MENU) list.append(menuLink(href, label, active));
  list.append(
    h(
      "li",
      { class: "account-menu-end" },
      h("button", { type: "button", class: "account-menu-item", onclick: signOut }, "Sign out"),
    ),
  );

  const trigger = h(
    "button",
    {
      type: "button",
      class: "account-trigger",
      "aria-haspopup": "true",
      "aria-expanded": "false",
      "aria-controls": "account-menu",
      "aria-label": `Account menu for ${user.username}`,
    },
    h("span", { class: "account-avatar" }, avatarImage(user, "avatar-sm")),
    h("span", { class: "account-name" }, user.username),
    h("span", { class: "account-icon", "aria-hidden": "true" }),
  );
  const element = h("div", { class: "account" }, trigger, list);

  const items = () => [...list.querySelectorAll("a, button")];
  const setOpen = (open, focus) => {
    list.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
    if (open && focus === "first") items()[0]?.focus();
    if (open && focus === "last") items().at(-1)?.focus();
    if (!open && focus) trigger.focus();
  };

  trigger.addEventListener("click", () => setOpen(list.hidden));
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true, "first");
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true, "last");
    }
  });
  list.addEventListener("keydown", (event) => {
    const all = items();
    const at = all.indexOf(document.activeElement);
    if (event.key === "Escape") {
      setOpen(false, true);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      all[(at + 1) % all.length].focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      all[(at - 1 + all.length) % all.length].focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      all[0].focus();
    } else if (event.key === "End") {
      event.preventDefault();
      all.at(-1).focus();
    }
  });
  // Close when focus or a click lands outside the menu.
  document.addEventListener("click", (event) => {
    if (!element.contains(event.target)) setOpen(false);
  });
  element.addEventListener("focusout", (event) => {
    if (event.relatedTarget && !element.contains(event.relatedTarget)) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !list.hidden) setOpen(false, true);
  });

  // Mark the trigger when the page being viewed is one of the menu's.
  if (MENU.some(([href]) => href === active) || active === SHARING) {
    trigger.setAttribute("data-current", "true");
  }
  return { element, list };
}

function menuLink(href, label, active) {
  return h("li", {}, h("a", { class: "account-menu-item", href, "aria-current": href === active ? "page" : false }, label));
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

const SHARING = "sharing.html";

async function addSharingLink(list, active) {
  if (!(await federationAvailable())) return;
  list.children[0].after(menuLink(SHARING, "Sharing", active)); // right after Profile
}

