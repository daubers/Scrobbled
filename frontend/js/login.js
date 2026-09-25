import { api, describeError, getToken, safeNext, setToken } from "./api.js";
import { errorBox, replace } from "./dom.js";
import { publicPage } from "./layout.js";

publicPage();
const next = safeNext(new URLSearchParams(location.search).get("next"));
if (getToken()) location.replace(next);

const form = document.getElementById("login");
const messages = document.getElementById("messages");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  replace(messages);
  try {
    const { token } = await api("/auth/login", {
      method: "POST",
      auth: false,
      body: { username: form.elements.username.value.trim(), password: form.elements.password.value },
    });
    setToken(token);
    location.href = next;
  } catch (error) {
    replace(messages, errorBox(describeError(error)));
    button.disabled = false;
  }
});
