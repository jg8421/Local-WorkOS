/* Real save/export lifecycle helpers with synthetic transport and DOM only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const begin = source.indexOf('  function renderIOStatus()');
const end = source.indexOf('  const AI_BUSY =', begin);
assert.ok(begin >= 0 && end > begin);
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function harness() {
  let current = { ioOperations: new Map() }, buttons = [], sequence = 0;
  const app = { workspace: 'personal', epoch: 1 }, region = { innerHTML: '', hidden: true };
  const sandbox = { app, view: () => current, crypto: { randomUUID: () => 'synthetic-operation-' + ++sequence },
    $: selector => selector === '#io-status-region' ? region : null, $$: () => buttons,
    esc: value => String(value).replace(/[<>&"]/g, x => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[x])),
    actionButton: (label, action) => `<button data-action="${action}">${label}</button>`,
    StaleRequestError: class extends Error { constructor() { super('Workspace changed'); this.name = 'StaleRequestError'; } }
  };
  const helpers = vm.runInNewContext(source.slice(begin, end) + '\n({runIO,updateIOStatus});', sandbox);
  return { ...helpers, app, region, get state() { return current; }, buttons: next => { buttons = next; }, switchView: () => { current = { ioOperations: new Map() }; } };
}
const tests = [];
function test(name, run) { tests.push({ name, run }); }
test('repeated export while editor buttons are rebuilt issues only one operation', async () => {
  const h = harness(), gate = deferred(); let exports = 0;
  const first = h.runIO('export:record:docx', 'Word 导出', () => { exports++; return gate.promise; });
  const replacement = { dataset: { action: 'export', id: 'record', format: 'docx' }, disabled: false };
  const unrelated = { dataset: { action: 'export', id: 'other', format: 'docx' }, disabled: true };
  h.buttons([replacement, unrelated]); h.updateIOStatus();
  assert.equal(replacement.disabled, true);
  const second = h.runIO('export:record:docx', 'Word 导出', () => { exports++; });
  await Promise.resolve(); assert.equal(exports, 1); gate.resolve('saved-file');
  assert.deepEqual(await Promise.all([first, second]), ['saved-file', 'saved-file']);
  assert.equal(replacement.disabled, false); assert.equal(unrelated.disabled, true);
  assert.equal(h.region.hidden, true);
});
test('failed export stays visible and explicit retry reruns only the file operation', async () => {
  const h = harness(); let attempts = 0;
  const execute = async () => { if (++attempts === 1) throw Error('<synthetic> exporter offline'); return 'downloaded'; };
  const retry = () => h.runIO('export:record:html', 'HTML 导出', execute, { retry });
  await assert.rejects(retry(), error => error.ioHandled === true);
  const failed = [...h.state.ioOperations.values()][0];
  assert.equal(failed.status, 'failed'); assert.match(h.region.innerHTML, /重试/);
  assert.match(h.region.innerHTML, /&lt;synthetic&gt;/); assert.doesNotMatch(h.region.innerHTML, /<synthetic>/);
  assert.equal(await failed.retry(), 'downloaded'); assert.equal(attempts, 2);
  assert.equal(h.state.ioOperations.size, 0);
});
test('late file response after a workspace switch cannot mark the new workspace saved', async () => {
  const h = harness(), original = h.state, gate = deferred();
  const request = h.runIO('save:record', '保存修改', () => gate.promise);
  h.app.workspace = 'demo'; h.app.epoch++; h.switchView(); gate.resolve({ saved: true });
  await assert.rejects(request, error => error.name === 'StaleRequestError');
  assert.equal(original.ioOperations.size, 0); assert.equal(h.state.ioOperations.size, 0);
});
test('aborted downloads do not leave a misleading failure/retry banner', async () => {
  const h = harness(); const error = Error('cancelled'); error.name = 'AbortError';
  await assert.rejects(h.runIO('download:original', '原文件下载', async () => { throw error; }));
  assert.equal(h.state.ioOperations.size, 0); assert.equal(h.region.hidden, true);
});
(async () => { for (const [index, item] of tests.entries()) { await item.run(); console.log(`PASS ${index + 1} ${item.name}`); } console.log(`RESULT ${tests.length}/${tests.length}`); })().catch(error => { console.error(error); process.exitCode = 1; });
