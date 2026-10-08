const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test, beforeEach, afterEach } = require('node:test');
const { createApp } = require('../server');
const { createDb } = require('../db');
const { createSimulatedDevices, SIMULATED_ONLY } = require('../fakeDevices');

let contexts = [];

async function startContext() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ateq-v2-'));
  const dbPath = path.join(dir, 'test.sqlite');
  const devices = createSimulatedDevices();
  const app = createApp({ dbPath, devices });
  const server = await new Promise((resolve) => {
    const value = app.listen(0, '127.0.0.1', () => resolve(value));
  });
  const address = server.address();
  const context = { app, devices, dbPath, dir, server, base: `http://127.0.0.1:${address.port}` };
  contexts.push(context);
  return context;
}

async function stopContext(context) {
  await new Promise((resolve, reject) => context.server.close((error) => error ? reject(error) : resolve()));
  context.app.locals.db.close();
  fs.rmSync(context.dir, { recursive: true, force: true });
}

beforeEach(() => { contexts = []; });
afterEach(async () => { for (const context of contexts.reverse()) await stopContext(context); });

async function call(context, method, route, body, extraHeaders = {}) {
  const response = await fetch(`${context.base}${route}`, {
    method,
    headers: { 'content-type': 'application/json', ...extraHeaders },
    body: method === 'GET' ? undefined : JSON.stringify(body || {})
  });
  return { status: response.status, body: await response.json() };
}

async function prepare(context, station, mode = 'SINGLE', key = `prepare-${station}-${Date.now()}-${Math.random()}`, productId = 2) {
  return call(context, 'POST', `/api/stations/${station}/prepare`, { productId, operatorId: 1, mode, idempotencyKey: key });
}

async function observe(context, station, stepCode, resultCode, key = `obs-${station}-${stepCode}-${Date.now()}-${Math.random()}`) {
  const body = { stepCode, idempotencyKey: key };
  if (resultCode !== undefined) body.resultCode = resultCode;
  return call(context, 'POST', `/api/simulator/stations/${station}/ateq-observations`, body);
}

test('fake-only safety guard and required catalog/station endpoints', async () => {
  assert.equal(SIMULATED_ONLY, true);
  assert.throws(() => createApp({ deviceMode: 'hardware' }), /only simulated devices/);
  const context = await startContext();
  const stations = await call(context, 'GET', '/api/stations');
  assert.equal(stations.status, 200);
  assert.deepEqual(stations.body.data.stations.map((item) => item.stationId), ['A', 'B']);
  assert.equal((await call(context, 'GET', '/api/catalog/products')).body.data.products.length, 2);
  assert.equal((await call(context, 'GET', '/api/catalog/operators')).body.data.operators.length, 2);
});

test('preparation reads, conditionally writes, reads back, and creates no pretest QR', async () => {
  const context = await startContext();
  const prepared = await prepare(context, 'A', 'SINGLE', 'same-prepare-key');
  assert.equal(prepared.status, 200);
  assert.deepEqual(prepared.body.data.program, { readBefore: 100, writePerformed: 1, readBack: 200, verified: true });
  const station = (await call(context, 'GET', '/api/stations/A')).body.data;
  assert.equal(station.state, 'ARMED');
  assert.equal(station.cycle.barcode, null);
  assert.equal(station.cycle.labelSerial, null);
  assert.equal(station.label, null);
  assert.equal(context.devices.ateq.writeHistory.length, 1);
  assert.equal(context.app.locals.db.prepare('SELECT COUNT(*) AS n FROM labels').get().n, 0);
  const repeat = await prepare(context, 'A', 'SINGLE', 'same-prepare-key');
  const repeatAgain = await prepare(context, 'A', 'SINGLE', 'same-prepare-key');
  assert.deepEqual(repeatAgain.body, repeat.body);
});

test('program selection is frozen by the selected product, not a client override', async () => {
  const context = await startContext();
  const response = await call(context, 'POST', '/api/stations/A/prepare', {
    productId: 2, operatorId: 1, mode: 'SINGLE', targetProgram: 9999, idempotencyKey: 'product-program-only'
  });
  assert.equal(response.status, 200);
  assert.equal(response.body.data.program.readBack, 200);
  assert.equal((await call(context, 'GET', '/api/stations/A')).body.data.cycle.targetProgram, 200);
});

test('active tester refuses program write and never arms', async () => {
  const context = await startContext();
  context.devices.ateq.setActive('B', true);
  const result = await prepare(context, 'B');
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'ATEQ_ACTIVE');
  assert.equal(context.devices.ateq.writeHistory.length, 0);
  assert.equal((await call(context, 'GET', '/api/stations/B')).body.data.state, 'IDLE');
});

test('program read-back mismatch leaves station unarmed', async () => {
  const context = await startContext();
  context.devices.ateq.setReadBackOverride('B', 999);
  const result = await prepare(context, 'B');
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'PROGRAM_VERIFY_FAILED');
  assert.equal(context.devices.ateq.writeHistory.length, 1);
  assert.equal((await call(context, 'GET', '/api/stations/B')).body.data.state, 'IDLE');
});

test('step 5 is accepted as the first valid start and terminal completes one single attempt', async () => {
  const context = await startContext();
  await prepare(context, 'A');
  const start = await observe(context, 'A', 5);
  assert.equal(start.body.data.attemptStarted, 1);
  assert.equal(start.body.data.state, 'TEST_1_RUNNING');
  assert.equal((await call(context, 'GET', '/api/stations/A')).body.data.cycle.barcode, null);
  const done = await observe(context, 'A', 65525, 0);
  assert.equal(done.body.data.state, 'PRINTED_AWAIT_LABEL_SCAN');
  const station = (await call(context, 'GET', '/api/stations/A')).body.data;
  assert.equal(station.cycle.attempts.length, 1);
  assert.equal(station.cycle.finalResult, 'OK');
  assert.match(station.cycle.barcode, /^ATEQ\|A\|/);
  assert.equal(station.label.status, 'PRINTED');
});

test('single NG persists trace and never allocates or prints a label', async () => {
  const context = await startContext();
  await prepare(context, 'B');
  await observe(context, 'B', 4);
  const done = await observe(context, 'B', 65535, 12);
  assert.equal(done.body.data.state, 'COMPLETED_NG');
  const station = (await call(context, 'GET', '/api/stations/B')).body.data;
  assert.equal(station.cycle.finalResult, 'NG');
  assert.equal(station.cycle.barcode, null);
  assert.equal(station.label, null);
  assert.equal(context.devices.printer.printed.length, 0);
  assert.equal(context.app.locals.db.prepare('SELECT COUNT(*) AS n FROM ateq_observations').get().n, 2);
});

test('dual mode always permits a second attempt after first NG and final NG has no label', async () => {
  const context = await startContext();
  await prepare(context, 'A', 'DUAL');
  await observe(context, 'A', 4);
  const first = await observe(context, 'A', 65525, 3);
  assert.equal(first.body.data.state, 'WAIT_TEST_2');
  assert.equal((await call(context, 'GET', '/api/stations/A')).body.data.cycle.attempts[0].outcome, 'NG');
  assert.equal((await observe(context, 'A', 6)).body.data.attemptStarted, 2);
  const final = await observe(context, 'A', 65535, 0);
  assert.equal(final.body.data.finalResult, 'NG');
  const station = (await call(context, 'GET', '/api/stations/A')).body.data;
  assert.equal(station.state, 'COMPLETED_NG');
  assert.equal(station.cycle.attempts.length, 2);
  assert.equal(station.label, null);
});

test('dual OK allocates QR only at final completion and label scan then PLC clear releases', async () => {
  const context = await startContext();
  await prepare(context, 'A', 'DUAL');
  await observe(context, 'A', 5);
  await observe(context, 'A', 65525, 0);
  assert.equal((await call(context, 'GET', '/api/stations/A')).body.data.state, 'WAIT_TEST_2');
  await observe(context, 'A', 4);
  const final = await observe(context, 'A', 65525, 0);
  assert.equal(final.body.data.state, 'PRINTED_AWAIT_LABEL_SCAN');
  const station = (await call(context, 'GET', '/api/stations/A')).body.data;
  const barcode = station.cycle.barcode;
  const wrong = await call(context, 'POST', '/api/stations/B/label-scans', { barcode, idempotencyKey: 'wrong-station-scan' });
  assert.equal(wrong.status, 409);
  const scan = await call(context, 'POST', '/api/stations/A/label-scans', { barcode, idempotencyKey: 'valid-a-scan' });
  assert.equal(scan.body.data.bitAddress, 'M0.1');
  assert.equal(scan.body.data.state, 'PLC_CLEAR_PENDING');
  assert.equal(context.devices.plc.readByte(), 2);
  assert.equal((await call(context, 'GET', '/api/stations/A')).body.data.label.status, 'PRINTED');
  const clear = await call(context, 'POST', '/api/simulator/stations/A/plc-clear', { idempotencyKey: 'clear-a-1' });
  assert.equal(clear.body.data.state, 'RELEASED_OK');
  assert.equal((await call(context, 'GET', '/api/stations/A')).body.data.label.status, 'SCANNED');
});

test('B label scan uses only M0.0 and concurrent A/B pulses preserve shared byte bits', async () => {
  const context = await startContext();
  await Promise.all([prepare(context, 'A'), prepare(context, 'B')]);
  for (const station of ['A', 'B']) {
    await observe(context, station, 4);
    await observe(context, station, 65525, 0);
  }
  const labels = {};
  for (const station of ['A', 'B']) labels[station] = (await call(context, 'GET', `/api/stations/${station}`)).body.data.cycle.barcode;
  const scans = await Promise.all([
    call(context, 'POST', '/api/stations/A/label-scans', { barcode: labels.A, idempotencyKey: 'scan-concurrent-a' }),
    call(context, 'POST', '/api/stations/B/label-scans', { barcode: labels.B, idempotencyKey: 'scan-concurrent-b' })
  ]);
  assert.deepEqual(scans.map((item) => item.body.data.bitAddress).sort(), ['M0.0', 'M0.1']);
  assert.equal(context.devices.plc.readByte(), 3);
  assert.equal(context.devices.plc.writeHistory.at(-1).after, 3);
  await call(context, 'POST', '/api/simulator/stations/B/plc-clear', { idempotencyKey: 'clear-concurrent-b' });
  assert.equal(context.devices.plc.readByte(), 2);
  await call(context, 'POST', '/api/simulator/stations/A/plc-clear', { idempotencyKey: 'clear-concurrent-a' });
  assert.equal(context.devices.plc.readByte(), 0);
});

test('idempotent observation does not create a duplicate attempt', async () => {
  const context = await startContext();
  await prepare(context, 'A');
  const first = await observe(context, 'A', 4, undefined, 'observation-repeat-key');
  const second = await observe(context, 'A', 4, undefined, 'observation-repeat-key');
  assert.deepEqual(second.body, first.body);
  assert.equal((await call(context, 'GET', '/api/stations/A')).body.data.cycle.attempts.length, 1);
  const conflict = await observe(context, 'A', 5, undefined, 'observation-repeat-key');
  assert.equal(conflict.status, 409);
  assert.equal(conflict.body.error.code, 'IDEMPOTENCY_CONFLICT');
});

test('restart recovery preserves printed-await-scan and PLC-clear-pending without replaying print or pulse', async () => {
  const context = await startContext();
  await prepare(context, 'A');
  await observe(context, 'A', 6);
  await observe(context, 'A', 65525, 0);
  const before = (await call(context, 'GET', '/api/stations/A')).body.data;
  const barcode = before.cycle.barcode;
  await call(context, 'POST', '/api/stations/A/label-scans', { barcode, idempotencyKey: 'recovery-scan' });
  const prints = context.devices.printer.printed.length;
  const pulses = context.devices.plc.writeHistory.length;
  await new Promise((resolve, reject) => context.server.close((error) => error ? reject(error) : resolve()));
  context.app.locals.db.close();
  contexts = [];
  const restarted = await startContextWithPath(context.dbPath, context.devices);
  const recovered = (await call(restarted, 'GET', '/api/stations/A')).body.data;
  assert.equal(recovered.state, 'PLC_CLEAR_PENDING');
  assert.equal(recovered.label.status, 'PRINTED');
  assert.equal(restarted.devices.printer.printed.length, prints);
  assert.equal(restarted.devices.plc.writeHistory.length, pulses);
  const cleared = await call(restarted, 'POST', '/api/simulator/stations/A/plc-clear', { idempotencyKey: 'recovery-clear' });
  assert.equal(cleared.body.data.state, 'RELEASED_OK');
});

async function startContextWithPath(dbPath, devices) {
  const app = createApp({ dbPath, devices });
  const server = await new Promise((resolve) => {
    const value = app.listen(0, '127.0.0.1', () => resolve(value));
  });
  const address = server.address();
  const context = { app, devices, dbPath, dir: path.dirname(dbPath), server, base: `http://127.0.0.1:${address.port}` };
  contexts.push(context);
  return context;
}

test('missing idempotency key is rejected for every POST', async () => {
  const context = await startContext();
  const result = await call(context, 'POST', '/api/simulator/stations/A/plc-clear', {});
  assert.equal(result.status, 400);
  assert.equal(result.body.error.code, 'IDEMPOTENCY_KEY_REQUIRED');
});

test('station event log is append-only', async () => {
  const db = createDb(':memory:');
  db.prepare('INSERT INTO station_events (station_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)').run('A', 'TEST', '{}', new Date().toISOString());
  assert.throws(() => db.prepare('UPDATE station_events SET event_type = ? WHERE id = 1').run('x'), /immutable/);
  assert.throws(() => db.prepare('DELETE FROM station_events WHERE id = 1').run(), /immutable/);
  db.close();
});
