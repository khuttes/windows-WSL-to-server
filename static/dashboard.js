const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const statusMessage = document.querySelector("#status-message");
const policyMessage = document.querySelector("#policy-message");

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      "X-CSRF-Token": csrfToken,
      ...options.headers,
    },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error || `Request failed (${response.status}).`);
  }
  return body;
}

async function refreshStatus() {
  try {
    const status = await requestJson("/api/status");
    document.querySelector("#uptime").textContent = `${status.uptime_hours} hours`;
    document.querySelector("#cpu").textContent = `${status.cpu_percent}%`;
    document.querySelector("#memory").textContent = `${status.memory_percent}%`;
    document.querySelector("#disk").textContent = `${status.disk_percent}%`;
    const services = document.querySelector("#services");
    services.replaceChildren();
    for (const [name, state] of Object.entries(status.services)) {
      const item = document.createElement("li");
      item.textContent = `${name}: ${state}`;
      services.append(item);
    }
    statusMessage.textContent = "Status updated.";
    statusMessage.className = "notice success";
  } catch (error) {
    statusMessage.textContent = error.message;
    statusMessage.className = "notice error";
  }
}

document.querySelector("#save-policy").addEventListener("click", async () => {
  try {
    const policy = JSON.parse(document.querySelector("#policy").value);
    await requestJson("/api/policy", { method: "POST", body: JSON.stringify(policy) });
    policyMessage.textContent = "Policy saved. Apply it to update system settings.";
    policyMessage.className = "notice success";
  } catch (error) {
    policyMessage.textContent = error.message;
    policyMessage.className = "notice error";
  }
});

document.querySelector("#apply-policy").addEventListener("click", async () => {
  if (!window.confirm("Apply this policy to system services, resource limits, and scheduled root tasks?")) {
    return;
  }
  try {
    const result = await requestJson("/api/apply", { method: "POST", body: "{}" });
    policyMessage.textContent = result.output || "Policy applied.";
    policyMessage.className = "notice success";
  } catch (error) {
    policyMessage.textContent = error.message;
    policyMessage.className = "notice error";
  }
});

refreshStatus();
window.setInterval(refreshStatus, 5000);
