import { api, describeError, upload } from "./api.js";
import { errorBox, formatDate, h, num, replace, timeAgo } from "./dom.js";
import { signedInPage } from "./layout.js";

const POLL_MS = 2000;
// Declared before the top-level awaits below, which render jobs straight away.
const STATUS_TEXT = {
  pending: "Waiting to start",
  running: "Importing",
  completed: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};
const messages = document.getElementById("messages");
const jobsList = document.getElementById("jobs");
const fileForm = document.getElementById("file-form");
const lastfmForm = document.getElementById("lastfm-form");
const progress = document.getElementById("upload-progress");

let pollTimer;

await signedInPage("import.html");
try {
  const options = await api("/imports/options");
  document.getElementById("file-limit").textContent = `Up to ${formatBytes(options.max_upload_bytes)}.`;
  document.getElementById("lastfm-section").hidden = !options.sources.includes("lastfm");
} catch (error) {
  replace(messages, errorBox(error.message));
}
await refresh();

fileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileForm.elements.file.files[0];
  if (!file) return;
  const button = fileForm.querySelector("button");
  const fill = progress.querySelector(".bar-fill");
  const status = progress.querySelector("[role=status]");
  button.disabled = true;
  progress.hidden = false;
  replace(messages);
  try {
    const data = new FormData();
    data.append("file", file);
    await upload("/imports/file", data, (fraction) => {
      fill.style.width = `${Math.round(fraction * 100)}%`;
      status.textContent = fraction < 1 ? `Uploading ${Math.round(fraction * 100)}%` : "Starting the import";
    });
    fileForm.reset();
    await refresh();
  } catch (error) {
    replace(messages, errorBox(describeError(error)));
  } finally {
    button.disabled = false;
    progress.hidden = true;
    fill.style.width = "0";
  }
});

lastfmForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = lastfmForm.querySelector("button");
  button.disabled = true;
  replace(messages);
  try {
    await api("/imports/lastfm", { method: "POST", body: { username: lastfmForm.elements.username.value.trim() } });
    lastfmForm.reset();
    await refresh();
  } catch (error) {
    replace(messages, errorBox(describeError(error)));
  } finally {
    button.disabled = false;
  }
});

async function refresh() {
  clearTimeout(pollTimer);
  let jobs;
  try {
    jobs = await api("/imports");
  } catch (error) {
    replace(jobsList, h("li", {}, errorBox(error.message)));
    return;
  }
  if (!jobs.length) {
    replace(jobsList, h("li", { class: "empty" }, "Nothing imported yet."));
    return;
  }
  replace(jobsList, jobs.map(jobRow));
  if (jobs.some((job) => job.status === "pending" || job.status === "running")) {
    pollTimer = setTimeout(refresh, POLL_MS);
  }
}

function jobRow(job) {
  const title = job.source === "lastfm" ? `Last.fm: ${job.lastfm_username}` : job.filename;
  const active = job.status === "pending" || job.status === "running";
  const fraction = job.total ? Math.min(1, job.processed / job.total) : 0;
  const when = job.finished_at ?? job.started_at ?? job.created_at;

  const counts = [
    `${num(job.imported)} added`,
    job.duplicates ? `${num(job.duplicates)} already in your history` : null,
    job.skipped ? `${num(job.skipped)} skipped` : null,
  ].filter(Boolean);

  return h(
    "li",
    { class: `import-job import-${job.status}` },
    h(
      "div",
      { class: "row-head" },
      h("span", { class: "row-title" }, title),
      active
        ? h(
            "button",
            { type: "button", class: "button button-quiet", onclick: () => cancel(job) },
            "Cancel import",
          )
        : null,
    ),
    h(
      "p",
      { class: "import-status" },
      h("strong", {}, STATUS_TEXT[job.status] ?? job.status),
      ` ${timeAgo(when)}`,
      job.status === "running" && job.total ? `, ${num(job.processed)} of ${num(job.total)}` : "",
    ),
    active
      ? h(
          "div",
          {
            class: "bar",
            role: "progressbar",
            "aria-valuemin": 0,
            "aria-valuemax": 100,
            "aria-valuenow": Math.round(fraction * 100),
            "aria-label": `Progress of ${title}`,
          },
          h("div", { class: "bar-fill", style: `width: ${fraction * 100}%` }),
        )
      : null,
    job.status === "pending" && Date.now() - new Date(job.created_at) > 60_000
      ? h("p", { class: "muted" }, "Still waiting? The import worker may not be running on the server.")
      : null,
    job.processed || job.status === "completed" ? h("p", { class: "muted" }, counts.join(", ")) : null,
    job.error ? h("p", { class: "notice notice-error" }, job.error) : null,
    h("p", { class: "muted import-date" }, `Started ${formatDate(job.created_at)}`),
  );
}

async function cancel(job) {
  try {
    await api(`/imports/${job.id}/cancel`, { method: "POST" });
  } catch (error) {
    replace(messages, errorBox(error.message));
  }
  await refresh();
}

function formatBytes(bytes) {
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${num(Math.round(mb))} MB` : `${num(Math.round(bytes / 1024))} KB`;
}
