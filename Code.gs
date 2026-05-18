// Google Apps Script — Receptor de audio
// Sheet: "Respuestas"  |  Drive folder: 1rZmvh5WC9KEPSQ99AtC3ZvJkJRKJp6L3
// Columnas: A=Fecha/hora  B=Nombre  C=Nº pregunta  D=Link audio  E=Transcripción

var SHEET_ID    = '1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM';
var SHEET_NAME  = 'Respuestas';
var FOLDER_ID   = '1rZmvh5WC9KEPSQ99AtC3ZvJkJRKJp6L3';

// ── CORS helpers ──────────────────────────────────────────────────────────────
// Apps Script no soporta headers CORS personalizados en doPost.
// El cliente usa XHR con Content-Type: text/plain para evitar preflight.
// Respondemos con ContentService para que al menos haya una respuesta legible.

function buildResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── doGet — útil para diagnóstico desde el navegador ─────────────────────────
function doGet(e) {
  var params = e ? e.parameter : {};
  if (params.test === 'diag') {
    return buildResponse({ ok: true, msg: 'Script activo', ts: new Date().toISOString() });
  }
  return buildResponse({ ok: true, msg: 'Grabador de audio — usa POST para enviar datos.' });
}

// ── doPost — receptor principal ───────────────────────────────────────────────
function doPost(e) {
  try {
    // El body llega como text/plain → e.postData.contents
    var raw = e.postData ? e.postData.contents : '{}';
    var data = JSON.parse(raw);

    // Ping de diagnóstico
    if (data.test === true) {
      Logger.log('Ping de diagnóstico recibido: ' + JSON.stringify(data));
      return buildResponse({ ok: true, msg: 'ping recibido', ts: new Date().toISOString() });
    }

    var persona  = data.persona   || 'Sin nombre';
    var pregunta = data.pregunta  || '?';
    var audioB64 = data.audio     || '';
    var mime     = data.mimeType  || 'audio/webm';

    if (!audioB64) {
      Logger.log('Error: no se recibió audio.');
      return buildResponse({ ok: false, error: 'No se recibió audio' });
    }

    // 1. Decodificar base64 y guardar en Drive
    var ext      = mime.includes('ogg') ? 'ogg' : 'webm';
    var fileName = persona.replace(/[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ ]/g, '_')
                   + '_P' + pregunta
                   + '_' + Utilities.formatDate(new Date(), 'America/Argentina/Buenos_Aires', 'yyyyMMdd_HHmmss')
                   + '.' + ext;

    var decoded = Utilities.base64Decode(audioB64);
    var blob    = Utilities.newBlob(decoded, mime, fileName);

    var folder  = DriveApp.getFolderById(FOLDER_ID);
    var file    = folder.createFile(blob);
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
    var fileUrl = file.getUrl();

    Logger.log('Archivo creado: ' + fileUrl);

    // 2. Anotar en el Sheet
    var ss    = SpreadsheetApp.openById(SHEET_ID);
    var sheet = ss.getSheetByName(SHEET_NAME);

    if (!sheet) {
      // Crear la hoja si no existe
      sheet = ss.insertSheet(SHEET_NAME);
      sheet.appendRow(['Fecha/hora', 'Nombre persona', 'Nº pregunta', 'Link audio Drive', 'Transcripción']);
    }

    var ts = Utilities.formatDate(new Date(), 'America/Argentina/Buenos_Aires', 'dd/MM/yyyy HH:mm:ss');
    sheet.appendRow([ts, persona, pregunta, fileUrl, '']);

    Logger.log('Fila agregada. Persona: ' + persona + ' | Pregunta: ' + pregunta);

    return buildResponse({ ok: true, fileUrl: fileUrl, ts: ts });

  } catch (err) {
    Logger.log('ERROR en doPost: ' + err.toString() + '\n' + err.stack);
    return buildResponse({ ok: false, error: err.toString() });
  }
}

// ── Test manual desde el editor ───────────────────────────────────────────────
// Corré esta función desde el editor para verificar acceso a Drive y Sheets.
function testTodo() {
  // Crear un archivo de prueba en Drive
  var folder  = DriveApp.getFolderById(FOLDER_ID);
  var content = 'test-' + new Date().toISOString();
  var file    = folder.createFile('test_script.txt', content, 'text/plain');
  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  Logger.log('Drive OK: ' + file.getUrl());

  // Agregar fila al Sheet
  var ss    = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(SHEET_NAME);
  sheet.appendRow([new Date(), 'TEST manual', 'testTodo()', file.getUrl(), '']);
  Logger.log('Sheet OK');
}

// ── Simular un doPost desde el editor ────────────────────────────────────────
// Para testear sin abrir el navegador: crea un audio real en b64 o usa un dummy.
function testDoPost() {
  var fakeAudio = Utilities.base64Encode('fake-audio-bytes-for-testing');
  var fakeEvent = {
    postData: {
      contents: JSON.stringify({
        persona:  'Test Editor',
        pregunta: '99',
        audio:    fakeAudio,
        mimeType: 'audio/webm'
      })
    }
  };
  var result = doPost(fakeEvent);
  Logger.log('testDoPost result: ' + result.getContent());
}
