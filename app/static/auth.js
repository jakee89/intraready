const $ = selector => document.querySelector(selector);
let authStatus = null;

async function send(path, body) {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-IntraReady-Request": "1" },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Request failed");
  return result;
}

async function initialise() {
  try {
    const response = await fetch("/api/auth/status");
    const status = await response.json(); authStatus = status;
    if (status.authenticated && status.user?.must_change_password) { $("#loading").classList.add("hidden"); $("#changePasswordForm").classList.remove("hidden"); return; }
    if (status.authenticated) { location.replace("/"); return; }
    $("#loading").classList.add("hidden");
    $(status.setup_required ? "#setupForm" : "#loginForm").classList.remove("hidden");
    if (["approval_required", "open"].includes(status.registration_mode)) $("#showRegister").classList.remove("hidden");
  } catch (_) { $("#loading").textContent = "The server is unavailable. Refresh to try again."; }
}

$("#loginForm").addEventListener("submit", async event => {
  event.preventDefault(); const button=event.currentTarget.querySelector("button"); button.disabled=true; $("#loginError").textContent="";
  try { await send("/api/auth/login", Object.fromEntries(new FormData(event.currentTarget))); location.replace("/"); }
  catch(error) { $("#loginError").textContent=error.message; button.disabled=false; }
});

$("#showRegister").addEventListener("click", () => { $("#loginForm").classList.add("hidden"); $("#registerForm").classList.remove("hidden"); });
$("#backToLogin").addEventListener("click", () => { $("#registerForm").classList.add("hidden"); $("#loginForm").classList.remove("hidden"); });
$("#completeToLogin").addEventListener("click", () => { $("#registerComplete").classList.add("hidden"); $("#loginForm").classList.remove("hidden"); });
$("#registerForm").addEventListener("submit", async event => {
  event.preventDefault(); const button=event.currentTarget.querySelector('button[type="submit"]'); button.disabled=true; $("#registerError").textContent="";
  try { await send("/api/auth/register", Object.fromEntries(new FormData(event.currentTarget))); event.currentTarget.classList.add("hidden"); $("#registerComplete").classList.remove("hidden"); }
  catch(error) { $("#registerError").textContent=error.message; button.disabled=false; }
});

$("#setupForm").addEventListener("submit", async event => {
  event.preventDefault(); const data=Object.fromEntries(new FormData(event.currentTarget)); const button=event.currentTarget.querySelector("button");
  $("#setupError").textContent="";
  if(data.password!==data.password_confirm){ $("#setupError").textContent="The passwords do not match."; return; }
  button.disabled=true;
  try { await send("/api/auth/setup-owner", data); location.replace("/"); }
  catch(error) { $("#setupError").textContent=error.message; button.disabled=false; }
});

$("#changePasswordForm").addEventListener("submit", async event => {
  event.preventDefault(); const data=Object.fromEntries(new FormData(event.currentTarget)); const button=event.currentTarget.querySelector("button"); $("#changePasswordError").textContent="";
  if(data.new_password!==data.password_confirm){ $("#changePasswordError").textContent="The new passwords do not match."; return; }
  button.disabled=true;
  try {
    const response=await fetch("/api/auth/change-password", {method:"POST",headers:{"Content-Type":"application/json","X-IntraReady-Request":"1","X-CSRF-Token":authStatus.user.csrf_token},body:JSON.stringify(data)});
    const result=await response.json(); if(!response.ok) throw new Error(result.detail||"Request failed"); location.replace("/");
  } catch(error) { $("#changePasswordError").textContent=error.message; button.disabled=false; }
});

initialise();
