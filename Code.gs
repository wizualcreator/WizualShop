// LEGACY Apps Script - TIDAK DIPAKAI oleh bot.py.
// Ini versi lama bot. Rahasia TIDAK BOLEH disimpan di sini.
// Isi nilai di bawah ini lewat PropertiesService / env saat deploy.
const BOT_TOKEN = 'YOUR_BOT_TOKEN';
const TELEGRAM_API = 'https://api.telegram.org/bot' + BOT_TOKEN;
const SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID';
const MIDTRANS_SERVER_KEY = 'YOUR_MIDTRANS_SERVER_KEY';
const MIDTRANS_IS_PRODUCTION = false;
const MIDTRANS_SNAP_URL = 'https://app.midtrans.com/snap/v1/transactions';
const MIDTRANS_STATUS_URL = 'https://api.midtrans.com/v2';
const WEBHOOK_URL = 'YOUR_WEBHOOK_URL';
const ADMIN_CHAT_ID = 'YOUR_ADMIN_CHAT_ID';
const WRITE_SECRET = 'YOUR_WRITE_SECRET';

const SHEET_PRODUCTS = 'PRODUCTS';
const SHEET_STOCK = 'STOCK';
const SHEET_ORDERS = 'ORDERS';
const SHEET_SETTINGS = 'SETTINGS';

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.secret && body.secret === WRITE_SECRET && Array.isArray(body.mark_sold)) {
      markRowsSold(body.mark_sold);
      return HtmlService.createHtmlOutput('OK');
    }
    if (body.update_id !== undefined) {
      handleTelegram(body);
    } else if (body.transaction_status && body.order_id) {
      handleMidtransNotification(body);
    }
    return HtmlService.createHtmlOutput('OK');
  } catch (err) {
    console.error(err);
    try {
      const msg = String(err && err.stack ? err.stack : err);
      tg('sendMessage', {
        chat_id: ADMIN_CHAT_ID,
        text: '❌ *ERROR BOT:*\n' + msg.substring(0, 1500),
        parse_mode: 'Markdown'
      });
    } catch (e2) {}
    return HtmlService.createHtmlOutput('ERROR');
  }
}

function markRowsSold(rows) {
  const sheet = getSheet(SHEET_STOCK);
  const data = sheet.getDataRange().getValues();
  const index = {};
  for (let i = 1; i < data.length; i++) {
    index[String(data[i][0]).trim()] = i + 1;
  }
  for (const r of rows) {
    const row = index[String(r.stock_id).trim()];
    if (!row) continue;
    sheet.getRange(row, 4).setValue('SOLD');
    sheet.getRange(row, 5).setValue(String(r.sold_to));
  }
}

function doGet() {
  return HtmlService.createHtmlOutput('Digitalin Store Bot OK');
}

function tg(method, payload) {
  const options = {
    method: 'post',
    payload: payload,
    muteHttpExceptions: true
  };
  const resp = UrlFetchApp.fetch(TELEGRAM_API + '/' + method, options);
  return JSON.parse(resp.getContentText());
}

function getSheet(name) {
  return SpreadsheetApp.openById(SPREADSHEET_ID).getSheetByName(name);
}

function getSettings() {
  const sheet = getSheet(SHEET_SETTINGS);
  const data = sheet.getDataRange().getValues();
  const settings = {};
  for (let i = 1; i < data.length; i++) {
    settings[String(data[i][0]).trim()] = data[i][1];
  }
  return settings;
}

function getProducts() {
  const sheet = getSheet(SHEET_PRODUCTS);
  const data = sheet.getDataRange().getValues();
  const products = [];
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][4]).trim().toUpperCase() !== 'ACTIVE') continue;
    if (!data[i][0]) continue;
    products.push({
      id: String(data[i][0]).trim(),
      name: String(data[i][1]),
      emoji: String(data[i][2]),
      price: parseInt(String(data[i][3]).replace(/[^0-9]/g, ''), 10) || 0,
      status: String(data[i][4]).trim(),
      description: String(data[i][5])
    });
  }
  return products;
}

function getAvailableStock(productId) {
  const sheet = getSheet(SHEET_STOCK);
  const data = sheet.getDataRange().getValues();
  let count = 0;
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][1]).trim() === productId &&
        String(data[i][3]).trim().toUpperCase() === 'AVAILABLE') {
      count++;
    }
  }
  return count;
}

function stockSummary() {
  const products = getProducts();
  const lines = [];
  for (const p of products) {
    const avail = getAvailableStock(p.id);
    lines.push(`${p.emoji} ${p.name} : ${avail} stok`);
  }
  return lines.join('\n');
}

function handleTelegram(update) {
  if (update.message) {
    const msg = update.message;
    const chatId = msg.chat.id;
    const text = msg.text || '';

    if (text.startsWith('/start')) {
      sendStartMessage(chatId);
    } else if (text.startsWith('/menu')) {
      showBuyProducts(chatId);
    } else {
      handlePendingQuantity(chatId, text);
    }
  } else if (update.callback_query) {
    handleCallback(update.callback_query);
  }
}

function sendStartMessage(chatId) {
  const settings = getSettings();
  const storeName = settings['STORE_NAME'] || 'Digitalin Store';
  const stock = stockSummary();

  const message =
`✨ *${storeName}* ✨

📦 *Live Stock Summary*
────────────────────
${stock || 'Tidak ada produk aktif.'}

Silakan pilih menu di bawah ini:`;

  const keyboard = {
    inline_keyboard: [
      [{ text: '🛍️ Buy Products', callback_data: 'buy' },
       { text: '📦 Check Stock', callback_data: 'stock' }],
      [{ text: '📋 My Orders', callback_data: 'orders' },
       { text: '👤 Contact Admin', callback_data: 'contact' }]
    ]
  };

  tg('sendMessage', {
    chat_id: chatId,
    text: message,
    parse_mode: 'Markdown',
    reply_markup: JSON.stringify(keyboard)
  });
}

function handleCallback(cb) {
  const query = cb;
  const chatId = query.message.chat.id;
  const messageId = query.message.message_id;
  const data = query.data || '';
  const userId = query.from.id;

  tg('answerCallbackQuery', { callback_query_id: query.id });

  if (data === 'buy') {
    showBuyProducts(chatId, messageId);
  } else if (data === 'stock') {
    showStock(chatId, messageId);
  } else if (data === 'orders') {
    showMyOrders(chatId, messageId, userId);
  } else if (data === 'contact') {
    contactAdmin(chatId, messageId);
  } else if (data.startsWith('buy:')) {
    const productId = data.split(':')[1];
    startBuyFlow(chatId, messageId, productId);
  } else if (data.startsWith('confirm')) {
    confirmCheckout(chatId, messageId, userId);
  } else if (data.startsWith('cancel')) {
    clearState(chatId);
    editMessage(chatId, messageId, 'Pesanan dibatalkan. 🙁');
  }
}

function editMessage(chatId, messageId, text, keyboard) {
  const payload = { chat_id: chatId, message_id: messageId, text: text, parse_mode: 'Markdown' };
  if (keyboard) payload.reply_markup = JSON.stringify(keyboard);
  tg('editMessageText', payload);
}

function showBuyProducts(chatId, messageId) {
  const products = getProducts();
  if (products.length === 0) {
    const text = 'Tidak ada produk yang tersedia saat ini.';
    if (messageId) editMessage(chatId, messageId, text);
    else tg('sendMessage', { chat_id: chatId, text: text });
    return;
  }

  const lines = ['🛍️ *Daftar Produk*\n'];
  const keyboard = [];
  for (const p of products) {
    const avail = getAvailableStock(p.id);
    lines.push(`${p.emoji} *${p.name}*\n   Rp${p.price.toLocaleString('id-ID')} | Stok: ${avail}`);
    keyboard.push([{ text: `${p.emoji} ${p.name} - Rp${p.price.toLocaleString('id-ID')}`, callback_data: `buy:${p.id}` }]);
  }
  keyboard.push([{ text: '🔙 Kembali', callback_data: 'back' }]);

  const reply = { inline_keyboard: keyboard };
  if (messageId) editMessage(chatId, messageId, lines.join('\n'), reply);
  else tg('sendMessage', { chat_id: chatId, text: lines.join('\n'), parse_mode: 'Markdown', reply_markup: JSON.stringify(reply) });
}

function showStock(chatId, messageId) {
  const products = getProducts();
  const lines = ['📦 *Check Stock*\n'];
  for (const p of products) {
    const avail = getAvailableStock(p.id);
    const mark = avail > 0 ? '✅' : '❌';
    lines.push(`${mark} ${p.emoji} ${p.name} : ${avail} tersedia`);
  }
  lines.push('\n🔄 Stok diperbarui otomatis dari database.');
  const keyboard = { inline_keyboard: [[{ text: '🛍️ Beli Sekarang', callback_data: 'buy' }]] };
  if (messageId) editMessage(chatId, messageId, lines.join('\n'), keyboard);
  else tg('sendMessage', { chat_id: chatId, text: lines.join('\n'), parse_mode: 'Markdown', reply_markup: JSON.stringify(keyboard) });
}

function showMyOrders(chatId, messageId, userId) {
  const sheet = getSheet(SHEET_ORDERS);
  const data = sheet.getDataRange().getValues();
  const lines = ['📋 *My Orders*\n'];
  let found = false;
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][1]).trim() !== String(userId)) continue;
    found = true;
    const orderId = data[i][0];
    const productId = data[i][3];
    const qty = data[i][4];
    const total = data[i][5];
    const status = String(data[i][6]).trim();
    lines.push(`• ${orderId} | ${productId} x${qty}\n  Rp${total} | ${status}`);
  }
  if (!found) lines.push('Belum ada pesanan. Ayo belanja! 🛍️');
  const keyboard = { inline_keyboard: [[{ text: '🛍️ Buy Products', callback_data: 'buy' }]] };
  if (messageId) editMessage(chatId, messageId, lines.join('\n'), keyboard);
  else tg('sendMessage', { chat_id: chatId, text: lines.join('\n'), parse_mode: 'Markdown', reply_markup: JSON.stringify(keyboard) });
}

function contactAdmin(chatId, messageId) {
  const settings = getSettings();
  const admin = settings['ADMIN_USERNAME'] || 'admin';
  const text = `👤 *Contact Admin*\n\nSilakan hubungi admin kami jika membutuhkan bantuan:\n\n📩 Telegram: @${admin}`;
  const keyboard = { inline_keyboard: [[{ text: '📩 Chat Admin', url: `https://t.me/${admin}` }]] };
  if (messageId) editMessage(chatId, messageId, text, keyboard);
  else tg('sendMessage', { chat_id: chatId, text: text, parse_mode: 'Markdown', reply_markup: JSON.stringify(keyboard) });
}

function getState(chatId) {
  const store = PropertiesService.getScriptProperties();
  const raw = store.getProperty('state_' + chatId);
  return raw ? JSON.parse(raw) : null;
}

function setState(chatId, obj) {
  PropertiesService.getScriptProperties().setProperty('state_' + chatId, JSON.stringify(obj));
}

function clearState(chatId) {
  PropertiesService.getScriptProperties().deleteProperty('state_' + chatId);
}

function startBuyFlow(chatId, messageId, productId) {
  const products = getProducts();
  const product = products.find(p => p.id === productId);
  if (!product) {
    editMessage(chatId, messageId, 'Produk tidak ditemukan.');
    return;
  }
  const avail = getAvailableStock(productId);
  setState(chatId, { product: product, qty: 1 });
  const text =
`${product.emoji} *${product.name}*
Harga: Rp${product.price.toLocaleString('id-ID')}
Stok tersedia: ${avail}

Jumlah yang mau dibeli? (kirim angka 1-${avail})`;
  tg('sendMessage', { chat_id: chatId, text: text, parse_mode: 'Markdown' });
}

function handlePendingQuantity(chatId, text) {
  const state = getState(chatId);
  if (!state || !state.product) return;
  const qty = parseInt(text.replace(/[^0-9]/g, ''), 10);
  if (!qty || qty < 1) {
    tg('sendMessage', { chat_id: chatId, text: 'Jumlah tidak valid. Masukkan angka misal: 2' });
    return;
  }
  const avail = getAvailableStock(state.product.id);
  if (qty > avail) {
    tg('sendMessage', { chat_id: chatId, text: `Stok tidak mencukupi. Tersedia hanya ${avail}.` });
    return;
  }
  state.qty = qty;
  setState(chatId, state);
  const total = state.product.price * qty;
  const keyboard = {
    inline_keyboard: [
      [{ text: '✅ Checkout & Bayar', callback_data: 'confirm' }],
      [{ text: '❌ Batal', callback_data: 'cancel' }]
    ]
  };
  tg('sendMessage', {
    chat_id: chatId,
    text: `Ringkasan Pesanan:\n\n${state.product.emoji} ${state.product.name}\nJumlah: ${qty}\nTotal: Rp${total.toLocaleString('id-ID')}\n\nLanjutkan ke pembayaran?`,
    parse_mode: 'Markdown',
    reply_markup: JSON.stringify(keyboard)
  });
}

function confirmCheckout(chatId, messageId, userId) {
  const state = getState(chatId);
  if (!state || !state.product) return;
  const product = state.product;
  const qty = state.qty;
  const total = product.price * qty;

  if (getAvailableStock(product.id) < qty) {
    editMessage(chatId, messageId, 'Maaf, stok sudah habis. Coba lagi.');
    clearState(chatId);
    return;
  }

  const orderId = 'ORD-' + Utilities.getUuid().replace(/-/g, '').substring(0, 10).toUpperCase();
  const settings = getSettings();
  const username = ''; // diisi lewat getMe tidak perlu; username diambil dari update asli

  const sheet = getSheet(SHEET_ORDERS);
  sheet.appendRow([
    orderId,
    String(userId),
    '',
    product.id,
    qty,
    total,
    'PENDING',
    '',
    new Date().toISOString(),
    ''
  ]);

  editMessage(chatId, messageId, `⏳ Membuat link pembayaran...`);

  const paymentUrl = createMidtransPayment(orderId, total, product, qty, chatId);

  if (paymentUrl) {
    const keyboard = {
      inline_keyboard: [
        [{ text: '💳 Bayar Sekarang', url: paymentUrl }],
        [{ text: '🔄 Cek Status Pembayaran', callback_data: `paid:${orderId}` }]
      ]
    };
    tg('sendMessage', {
      chat_id: chatId,
      text:
`🧾 *Pesanan Dibuat!*
Order ID: \`${orderId}\`

${product.emoji} ${product.name}
Jumlah: ${qty}
Total: *Rp${total.toLocaleString('id-ID')}*

Klik tombol di bawah untuk membayar. Setelah pembayaran berhasil, produk digital akan dikirim otomatis ke chat ini.`,
      parse_mode: 'Markdown',
      reply_markup: JSON.stringify(keyboard)
    });
    notifyAdmin(`🔔 *PESANAN BARU (PENDING)*\n\nOrder: \`${orderId}\`\n${product.name} x${qty}\nTotal: Rp${total.toLocaleString('id-ID')}\nUser ID: ${userId}`);
  } else {
    tg('sendMessage', {
      chat_id: chatId,
      text: 'Terjadi kendala membuat pembayaran. Admin akan menghubungi kamu. 🙏'
    });
    notifyAdmin(`⚠️ *GAGAL buat pembayaran Midtrans*\nOrder: \`${orderId}\`\n${product.name} x${qty}\nTotal: Rp${total}\nUser: ${userId}`);
  }

  clearState(chatId);
}

function createMidtransPayment(orderId, total, product, qty, chatId) {
  const auth = Utilities.base64Encode(MIDTRANS_SERVER_KEY + ':');
  const payload = {
    transaction_details: {
      order_id: orderId,
      gross_amount: total
    },
    item_details: [
      {
        id: product.id,
        price: product.price,
        quantity: qty,
        name: product.name
      }
    ],
    customer_details: {
      first_name: 'Telegram',
      phone: String(chatId)
    }
  };
  const options = {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Basic ' + auth, 'Accept': 'application/json' },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };
  try {
    const resp = UrlFetchApp.fetch(MIDTRANS_SNAP_URL, options);
    const json = JSON.parse(resp.getContentText());
    if (json.redirect_url) return json.redirect_url;
    console.error('Midtrans create error: ' + resp.getContentText());
    return null;
  } catch (err) {
    console.error(err);
    return null;
  }
}

function handleMidtransNotification(body) {
  const orderId = body.order_id;
  const statusCode = body.status_code;
  const grossAmount = body.gross_amount;
  const signatureKey = body.signature_key;
  const transactionStatus = body.transaction_status;

  const expectedSig = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_512,
    orderId + statusCode + grossAmount + MIDTRANS_SERVER_KEY
  ).map(function (b) { return (b < 0 ? b + 256 : b).toString(16).padStart(2, '0'); }).join('');

  if (expectedSig !== signatureKey) {
    console.warn('Invalid Midtrans signature for ' + orderId);
    return;
  }

  if (transactionStatus === 'settlement' || transactionStatus === 'capture') {
    processPaidOrder(orderId, body.transaction_id || body.payment_id || '');
  } else if (transactionStatus === 'expire' || transactionStatus === 'deny' || transactionStatus === 'cancel') {
    updateOrderStatus(orderId, 'FAILED');
  }
}

function processPaidOrder(orderId, paymentId) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const sheet = getSheet(SHEET_ORDERS);
    const data = sheet.getDataRange().getValues();
    for (let i = 1; i < data.length; i++) {
      if (String(data[i][0]).trim() !== orderId) continue;
      const status = String(data[i][6]).trim();
      if (status !== 'PENDING') return;
      const userId = String(data[i][1]).trim();
      const productId = String(data[i][3]).trim();
      const qty = parseInt(data[i][4], 10);
      const total = data[i][5];
      const productName = productNameById(productId);
      const emoji = emojiById(productId);

      sheet.getRange(i + 1, 7).setValue('PAID');
      sheet.getRange(i + 1, 8).setValue(paymentId);
      sheet.getRange(i + 1, 10).setValue(new Date().toISOString());

      const contents = allocateStock(productId, qty, userId);
      if (contents.length === 0) {
        sheet.getRange(i + 1, 7).setValue('NO_STOCK');
        notifyAdmin(`⚠️ *ORDER NO STOCK*\nOrder \`${orderId}\` dibayar tapi stok habis! Refund diperlukan.\nUser ID: ${userId}`);
        return;
      }

      deliverProducts(chatIdFromUserId(userId), orderId, emoji, productName, contents);
      sheet.getRange(i + 1, 7).setValue('COMPLETED');
      notifyAdmin(`✅ *PEMBAYARAN BERHASIL*\n\nOrder \`${orderId}\`\n${emoji} ${productName} x${qty}\nTotal: Rp${total}\nUser: ${userId}\nStatus: COMPLETED`);
      return;
    }
  } finally {
    lock.releaseLock();
  }
}

function productNameById(productId) {
  const p = getProducts().find(x => x.id === productId);
  return p ? p.name : productId;
}

function emojiById(productId) {
  const p = getProducts().find(x => x.id === productId);
  return p ? p.emoji : '';
}

function allocateStock(productId, qty, userId) {
  const sheet = getSheet(SHEET_STOCK);
  const data = sheet.getDataRange().getValues();
  const contents = [];
  let remaining = qty;
  for (let i = 1; i < data.length && remaining > 0; i++) {
    if (String(data[i][1]).trim() === productId &&
        String(data[i][3]).trim().toUpperCase() === 'AVAILABLE') {
      contents.push(String(data[i][2]));
      sheet.getRange(i + 1, 4).setValue('SOLD');
      sheet.getRange(i + 1, 5).setValue(userId);
      remaining--;
    }
  }
  return contents;
}

function deliverProducts(chatId, orderId, emoji, productName, contents) {
  if (!chatId) return;
  let text = `✅ *Pembayaran Berhasil!*\n\n${emoji} ${productName}\nOrder: \`${orderId}\`\n\nBerikut produk digital kamu:\n\n`;
  for (let i = 0; i < contents.length; i++) {
    text += `🔑 *Produk ${i + 1}:*\n${contents[i]}\n\n`;
  }
  text += 'Terima kasih sudah berbelanja! 🙏';
  tg('sendMessage', { chat_id: chatId, text: text, parse_mode: 'Markdown' });
}

function chatIdFromUserId(userId) {
  return userId;
}

function updateOrderStatus(orderId, status) {
  const sheet = getSheet(SHEET_ORDERS);
  const data = sheet.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][0]).trim() === orderId) {
      sheet.getRange(i + 1, 7).setValue(status);
      return;
    }
  }
}

function notifyAdmin(text) {
  const settings = getSettings();
  const adminId = settings['ADMIN_CHAT_ID'] || ADMIN_CHAT_ID;
  if (!adminId) return;
  tg('sendMessage', { chat_id: adminId, text: text, parse_mode: 'Markdown' });
}

function checkPendingPayments() {
  const sheet = getSheet(SHEET_ORDERS);
  const data = sheet.getDataRange().getValues();
  const auth = Utilities.base64Encode(MIDTRANS_SERVER_KEY + ':');
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][6]).trim() !== 'PENDING') continue;
    const orderId = String(data[i][0]).trim();
    const options = {
      method: 'get',
      headers: { 'Authorization': 'Basic ' + auth, 'Accept': 'application/json' },
      muteHttpExceptions: true
    };
    try {
      const resp = UrlFetchApp.fetch(MIDTRANS_STATUS_URL + '/' + orderId + '/status', options);
      const json = JSON.parse(resp.getContentText());
      const ts = json.transaction_status;
      if (ts === 'settlement' || ts === 'capture') {
        processPaidOrder(orderId, json.transaction_id || '');
      } else if (ts === 'expire' || ts === 'deny' || ts === 'cancel') {
        updateOrderStatus(orderId, 'FAILED');
      }
    } catch (err) {
      console.error('Status check error ' + orderId + ': ' + err);
    }
  }
}

function setupWebhook() {
  const resp = tg('setWebhook', { url: WEBHOOK_URL });
  console.log(resp);
}

function setupTrigger() {
  const existing = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'checkPendingPayments');
  if (existing.length === 0) {
    ScriptApp.newTrigger('checkPendingPayments').timeBased().everyMinutes(1).create();
    console.log('Trigger dibuat: cek pembayaran tiap 1 menit');
  }
}
