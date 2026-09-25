import { api, describeError, setToken } from "./api.js";
import { errorBox, replace } from "./dom.js";
import { publicPage } from "./layout.js";

publicPage();
const form = document.getElementById("register");
const messages = document.getElementById("messages");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const button = form.querySelector("button");
  button.disabled = true;
  replace(messages);
  try {
    const { token } = await api("/auth/register", {
      method: "POST",
      auth: false,
      body: {
        username: form.elements.username.value.trim(),
        email: form.elements.email.value.trim(),
        password: form.elements.password.value,
      },
    });
    setToken(token);
    location.href = "apps.html";
  } catch (error) {
    replace(messages, errorBox(describeError(error)));
    button.disabled = false;
  }
});
