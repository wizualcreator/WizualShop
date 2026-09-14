// sheet-write-back.gs — Web App Apps Script untuk Wizual Shop bot.
// Deploy: Extensions > Apps Script > tempel ini > Deploy > Web app.
//  - Execute as: Me
//  - Who has access: Anyone
// Set SCRIPT_PROPERTIES (gear icon > Project settings > Script properties):
//   SPREADSHEET_ID = 1nGbHZxthGV-M8qzDjGFnuwuAcH9bAFwh9pQTIEsDAUg
//   WRITE_SECRET  = [buat string rahasia panjang, simpan juga di .env Render]

const SCRIPT_PROP = PropertiesService.getScriptProperties();

function doGet() {
  return HtmlService.createHtmlOutput('<h3>Wizual Shop write-back OK</h3>');
}

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const secret = SCRIPT_PROP.getProperty('WRITE_SECRET');
    if (!secret || body.secret !== secret) {
      return HtmlService.createHtmlOutput('FORBIDDEN');
    }
    const lock = LockService.getScriptLock();
    lock.waitLock(30000);
    try {
      let count = 0;
      if (Array.isArray(body.mark_sold)) {
        const sheet = getSheet('STOCK');
        const data = sheet.getDataRange().getValues();
        const index = mapById(data, 0);
        for (const r of body.mark_sold) {
          const row = index[String(r.stock_id).trim()];
          if (!row) continue;
          sheet.getRange(row, 4).setValue('SOLD');
          sheet.getRange(row, 5).setValue(String(r.sold_to));
          count++;
        }
      }
      if (Array.isArray(body.orders)) {
        const sheet = getSheet('ORDERS');
        const data = sheet.getDataRange().getValues();
        const index = mapById(data, 0);
        for (const o of body.orders) {
          const row = index[String(o.order_id).trim()];
          const vals = [
            o.order_id, o.telegram_id, o.username || '', o.product_id,
            o.qty, o.total, o.status || 'PENDING', o.payment_id || '',
            o.created_at || '', o.paid_at || ''
          ];
          if (row) sheet.getRange(row, 1, 1, vals.length).setValues([vals]);
          else sheet.appendRow(vals);
          count++;
        }
      }
      return HtmlService.createHtmlOutput('OK:' + count);
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    console.error(err);
    return HtmlService.createHtmlOutput('ERROR');
  }
}

function getSheet(name) {
  const id = SCRIPT_PROP.getProperty('SPREADSHEET_ID');
  return SpreadsheetApp.openById(id).getSheetByName(name);
}

function mapById(data, col) {
  const m = {};
  for (let i = 1; i < data.length; i++) {
    const key = String(data[i][col]).trim();
    if (key) m[key] = i + 1;
  }
  return m;
}