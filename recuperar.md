primero validar usuario
https://control-financiero-wrm5.onrender.com/api/users?utm_source=chatgpt.com

abrir la app en web y pegar en consola lo siguiente:


(async () => {
  const status = await fetch("/api/status", {
    credentials: "same-origin"
  }).then(r => r.json());

  if (!status.authenticated || status.user?.role !== "admin") {
    throw new Error("Debes entrar con tu cuenta de administrador.");
  }

  const response = await fetch("/api/users", {
    credentials: "same-origin"
  });
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || "No se pudo consultar la lista.");
  }

  const user = data.users.find(
    u => u.username.toLowerCase() === "zadiaz"
  );

  if (!user) {
    alert("No encontré el usuario zadiaz.");
    return;
  }

  if (!user.active) {
    alert("La cuenta zadiaz está inactiva. No se cambió su contraseña.");
    return;
  }

  if (!confirm("¿Restablecer la contraseña de zadiaz?")) return;

  const resultResponse = await fetch(
    `/api/users/${user.id}/reset-password`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": status.csrf_token
      },
      body: "{}"
    }
  );

  const result = await resultResponse.json();

  if (!resultResponse.ok) {
    throw new Error(result.error || "Falló el restablecimiento.");
  }

  alert(
    "Contraseña temporal de zadiaz:\n\n" +
    result.temporary_password +
    "\n\nEntrégasela en privado. Deberá cambiarla al ingresar."
  );
})().catch(error => alert("Error: " + error.message));


darle la contraseña a la persona