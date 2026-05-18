# Grabador de Audio → Google Drive + Sheets

## Archivos

| Archivo | Función |
|---------|---------|
| `index.html` | Formulario web (graba audio, envía al script) |
| `Code.gs` | Google Apps Script (recibe POST, guarda en Drive y Sheet) |

---

## Setup del Apps Script

1. Abrí [script.google.com](https://script.google.com) → Nuevo proyecto
2. Pegá el contenido de `Code.gs`
3. **Deployar como Web App:**
   - Ejecutar como: **Yo** (la cuenta con acceso al Drive y Sheet)
   - Quién puede acceder: **Cualquier persona** ← CRÍTICO
4. Copiá la URL del deploy y pegala en `index.html` como `SCRIPT_URL`
5. Corré `testTodo()` desde el editor para confirmar permisos

---

## Diagnóstico de CORS (el problema central)

### Por qué falla el envío desde el navegador

Google Apps Script **no permite agregar headers CORS** en `doPost`. El navegador bloquea la respuesta si el servidor no devuelve `Access-Control-Allow-Origin`. 

### Solución implementada

- El cliente usa **XMLHttpRequest con `Content-Type: text/plain`**
- Esto convierte la petición en "simple" (no dispara preflight OPTIONS)
- El script parsea el body igual — el Content-Type no importa server-side
- Cuando el navegador bloquea la *respuesta*, el XHR dispara `onerror`
- **`onerror` ≠ que la petición no llegó** — la petición sí llega al script
- El form muestra un warning amarillo indicando que verifiquen en el Sheet

### Checklist si sigue sin llegar

1. **¿El deploy es "Cualquier persona"?** → El error más común
2. **¿Re-deployaste después del último cambio de código?** → Cambios en el código no se aplican al deploy viejo; hay que crear un nuevo deploy o hacer "Manage deployments → New version"
3. **¿La red bloquea script.google.com?** → Probá desde celular con datos móviles o red diferente
4. **Corré `testDoPost()` desde el editor** → Simula un POST sin abrir el navegador; si esto funciona y escribe en el Sheet, el script está bien y el problema es el envío desde el cliente
5. **Revisá "Ejecuciones" en Apps Script** → Si no aparece nada, la petición nunca llegó al servidor

---

## Columnas del Sheet

| A | B | C | D | E |
|---|---|---|---|---|
| Fecha/hora | Nombre persona | Nº pregunta | Link audio Drive | Transcripción |
