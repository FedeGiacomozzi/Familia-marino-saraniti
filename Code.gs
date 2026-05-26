// Google Apps Script — Receptor de audio y fotos
// Sheet: "Respuestas"  |  Drive folder: 1rZmvh5WC9KEPSQ99AtC3ZvJkJRKJp6L3
// Columnas: A=Fecha/hora  B=Nombre  C=FechaNac  D=Nº pregunta  E=Link Audio  F=Transcripción  G=Fotografía
//
// Configuración requerida (correr una sola vez desde el editor):
//   configurar("https://TU_CLOUD_RUN_URL", "marino-saraniti")

var SHEET_ID         = '1A1M79ITLeRVWkwct7pqjUTmLu9NWXn9uDLpKWMMomgM';
var SHEET_NAME       = 'Respuestas';
var FOLDER_ID        = '1rZmvh5WC9KEPSQ99AtC3ZvJkJRKJp6L3';
var PHOTOS_FOLDER_ID = '1MGjJCL3gE1ljT9r8qbdR-f_c1BWUC_JC';

function buildResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  var params = e ? e.parameter : {};
  if (params.test === 'diag') {
    return buildResponse({ ok: true, msg: 'Script activo', ts: new Date().toISOString() });
  }
  return buildResponse({ ok: true, msg: 'Grabador familiar — usa POST para enviar datos.' });
}

// ── doPost — receptor principal ───────────────────────────────────────────────
function doPost(e) {
  try {
    var raw  = e.postData ? e.postData.contents : '{}';
    var data = JSON.parse(raw);

    if (data.test === true) {
      Logger.log('Ping recibido: ' + JSON.stringify(data));
      return buildResponse({ ok: true, msg: 'ping recibido', ts: new Date().toISOString() });
    }

    if (data.tipo === 'foto') {
      return _guardarFoto(data);
    }

    return _guardarAudio(data);

  } catch (err) {
    Logger.log('ERROR en doPost: ' + err.toString() + '\n' + err.stack);
    return buildResponse({ ok: false, error: err.toString() });
  }
}

function _guardarAudio(data) {
  var persona  = data.persona   || 'Sin nombre';
  var fechaNac = data.fechaNac  || '';
  var pregunta = data.pregunta  || '?';
  var audioB64 = data.audio     || '';
  var mime     = data.mimeType  || 'audio/webm';

  if (!audioB64) {
    return buildResponse({ ok: false, error: 'No se recibió audio' });
  }

  var ext      = mime.includes('ogg') ? 'ogg' : 'webm';
  var fileName = _sanitize(persona)
                 + '_P' + pregunta
                 + '_' + Utilities.formatDate(new Date(), 'America/Argentina/Buenos_Aires', 'yyyyMMdd_HHmmss')
                 + '.' + ext;

  var decoded = Utilities.base64Decode(audioB64);
  var blob    = Utilities.newBlob(decoded, mime, fileName);

  var folder = DriveApp.getFolderById(FOLDER_ID);
  var file   = folder.createFile(blob);
  try { file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW); } catch(ex) {}
  var fileUrl = file.getUrl();

  var sheet = _getOrCreateSheet();
  var ts    = _now();
  sheet.appendRow([ts, persona, fechaNac, pregunta, fileUrl, '', '']);

  // ── Notificar a Cloud Run para que guarde en GCS + Firestore ─────────────
  _notificarCloudRun('/ingest-audio', {
    nombre:      persona,
    fecha_nac:   fechaNac,
    pregunta_id: String(pregunta),
    drive_url:   fileUrl,
    mime_type:   mime
  });

  Logger.log('Audio guardado: ' + fileUrl);
  return buildResponse({ ok: true, fileUrl: fileUrl, ts: ts });
}

function _guardarFoto(data) {
  var persona  = data.persona  || 'Sin nombre';
  var fotoB64  = data.foto     || '';
  var mime     = data.mimeType || 'image/jpeg';

  if (!fotoB64) {
    return buildResponse({ ok: false, error: 'No se recibió foto' });
  }

  var ext      = mime.includes('png') ? 'png' : mime.includes('webp') ? 'webp' : 'jpg';
  var fileName = _sanitize(persona)
                 + '_foto_'
                 + Utilities.formatDate(new Date(), 'America/Argentina/Buenos_Aires', 'yyyyMMdd_HHmmss')
                 + '.' + ext;

  var decoded = Utilities.base64Decode(fotoB64);
  var blob    = Utilities.newBlob(decoded, mime, fileName);

  var folder = DriveApp.getFolderById(PHOTOS_FOLDER_ID);
  var file   = folder.createFile(blob);
  try { file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW); } catch(ex) {}
  var fileUrl = file.getUrl();

  var sheet   = _getOrCreateSheet();
  var data_   = sheet.getDataRange().getValues();
  var updated = false;
  for (var i = 1; i < data_.length; i++) {
    if ((data_[i][1] || '').toString().trim().toLowerCase() === persona.trim().toLowerCase()) {
      sheet.getRange(i + 1, 7).setValue(fileUrl);
      updated = true;
      break;
    }
  }
  if (!updated) {
    sheet.appendRow([_now(), persona, '', '', '', '', fileUrl]);
  }

  // ── Notificar a Cloud Run ─────────────────────────────────────────────────
  _notificarCloudRun('/ingest-foto', {
    nombre:    persona,
    drive_url: fileUrl,
    mime_type: mime
  });

  Logger.log('Foto guardada: ' + fileUrl);
  return buildResponse({ ok: true, fileUrl: fileUrl });
}

// ── Cloud Run notify (fire-and-forget, no bloquea si falla) ──────────────────

function _notificarCloudRun(path, payload) {
  try {
    var props      = PropertiesService.getScriptProperties();
    var baseUrl    = props.getProperty('CLOUD_RUN_URL');
    var familiaId  = props.getProperty('FAMILIA_ID') || 'marino-saraniti';

    if (!baseUrl) {
      Logger.log('CLOUD_RUN_URL no configurado — skip Firestore/GCS sync');
      return;
    }

    payload.familia_id = familiaId;

    UrlFetchApp.fetch(baseUrl + path, {
      method:             'post',
      contentType:        'application/json',
      payload:            JSON.stringify(payload),
      muteHttpExceptions: true,
      followRedirects:    true
    });
  } catch (ex) {
    Logger.log('Cloud Run notify error (no bloquea): ' + ex);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _getOrCreateSheet() {
  var ss    = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(['Fecha/hora', 'Nombre', 'FechaNac', 'Nº pregunta', 'Link Audio', 'Transcripción', 'Fotografía']);
  }
  return sheet;
}

function _sanitize(name) {
  return name.replace(/[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ ]/g, '_');
}

function _now() {
  return Utilities.formatDate(new Date(), 'America/Argentina/Buenos_Aires', 'dd/MM/yyyy HH:mm:ss');
}

// ── Configuración (correr UNA vez desde el editor tras el deploy) ─────────────
// Ejemplo: configurar("https://familia-pipeline-xxxx-uc.a.run.app", "marino-saraniti")

function configurar(cloudRunUrl, familiaId) {
  var props = PropertiesService.getScriptProperties();
  props.setProperty('CLOUD_RUN_URL', cloudRunUrl);
  props.setProperty('FAMILIA_ID',    familiaId || 'marino-saraniti');
  Logger.log('Configurado: CLOUD_RUN_URL=' + cloudRunUrl + '  FAMILIA_ID=' + (familiaId || 'marino-saraniti'));
}

// ── Tests manuales ────────────────────────────────────────────────────────────

function testTodo() {
  var folder  = DriveApp.getFolderById(FOLDER_ID);
  var content = 'test-' + new Date().toISOString();
  var file    = folder.createFile('test_script.txt', content, 'text/plain');
  try { file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW); } catch(e) { Logger.log('setSharing: ' + e); }
  Logger.log('Drive OK: ' + file.getUrl());

  var sheet = _getOrCreateSheet();
  sheet.appendRow([new Date(), 'TEST manual', '01-01-1970', 'testTodo()', file.getUrl(), '', '']);
  Logger.log('Sheet OK');
}

function testDoPost() {
  var fakeAudio = Utilities.base64Encode('fake-audio-bytes-for-testing');
  var fakeEvent = {
    postData: {
      contents: JSON.stringify({
        persona:  'Test Editor',
        fechaNac: '01-01-1990',
        pregunta: '99',
        audio:    fakeAudio,
        mimeType: 'audio/webm'
      })
    }
  };
  var result = doPost(fakeEvent);
  Logger.log('testDoPost result: ' + result.getContent());
}

function testFoto() {
  var fakeImg   = Utilities.base64Encode('fake-image-bytes');
  var fakeEvent = {
    postData: {
      contents: JSON.stringify({
        tipo:     'foto',
        persona:  'Test Editor',
        foto:     fakeImg,
        mimeType: 'image/jpeg'
      })
    }
  };
  var result = doPost(fakeEvent);
  Logger.log('testFoto result: ' + result.getContent());
}

function testCloudRun() {
  var props   = PropertiesService.getScriptProperties();
  var baseUrl = props.getProperty('CLOUD_RUN_URL');
  if (!baseUrl) { Logger.log('CLOUD_RUN_URL no configurado'); return; }
  var resp = UrlFetchApp.fetch(baseUrl + '/health', { muteHttpExceptions: true });
  Logger.log('Cloud Run health: ' + resp.getContentText());
}

function configurarNow() {
  configurar('https://familia-pipeline-776445604502.us-central1.run.app', 'marino-saraniti');
}
