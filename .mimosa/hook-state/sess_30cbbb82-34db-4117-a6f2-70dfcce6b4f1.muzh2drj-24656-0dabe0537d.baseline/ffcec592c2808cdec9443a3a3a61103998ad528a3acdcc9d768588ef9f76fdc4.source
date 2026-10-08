const assert = require('node:assert/strict');
const { test } = require('node:test');
const { RealAteqRtu, RealS7Plc, RealBarTenderPrinter, swap16 } = require('../realDevices');
const { LIVE_WRITE_TOKEN, loadLiveConfig } = require('../liveConfig');

const config = {
  mode: 'live',
  plc: { host: '192.0.2.10', rack: 0, slot: 1, scanAckBits: { A: 'M0.1', B: 'M0.0' } },
  ateq: {
    A: { port: 'COM11', slaveId: 1, baudRate: 9600, parity: 'even', dataBits: 8, stopBits: 1 },
    B: { port: 'COM12', slaveId: 2, baudRate: 9600, parity: 'even', dataBits: 8, stopBits: 1 }
  },
  scanner: { host: '192.0.2.20', port: 9004, terminator: '\r' },
  printer: { executable: 'C:\\BarTender\\bartend.exe', templateRoot: 'C:\\templates', printerName: 'Unit Test Printer' }
};

test('real ATEQ adapter decodes program and refuses writes without the explicit live token', async () => {
  const writes = [];
  const client = {
    async connectRTUBuffered() {}, setID() {}, async close() {},
    async readHoldingRegisters() { return { data: [swap16(99), 0, 0, 0, 0xffff, 0, 0, 0, 0, 0, 0, 0, 0] }; },
    async writeRegister(address, value) { writes.push([address, value]); }
  };
  const blocked = new RealAteqRtu(config, { createClient: () => client, environment: { V2_MODE: 'live' } });
  assert.equal(await blocked.readProgram('A'), 100);
  await assert.rejects(() => blocked.writeProgram('A', 101), /REAL_WRITE_DISABLED/);
  assert.equal(writes.length, 0);
  const enabled = new RealAteqRtu(config, { createClient: () => client, environment: { V2_MODE: 'live', V2_LIVE_WRITE_ENABLE: LIVE_WRITE_TOKEN } });
  await enabled.writeProgram('A', 100);
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0], [0x0200, swap16(99)]);
});

test('real PLC adapter permits only the configured label bit and preserves the shared M0 byte', async () => {
  const writes = [];
  const client = {
    ConnectTo(_host, _rack, _slot, done) { done(null); },
    MBRead(_offset, _size, done) { done(null, Buffer.from([1])); },
    MBWrite(offset, size, buffer, done) { writes.push([offset, size, Buffer.from(buffer)]); done(null); },
    Disconnect() {}
  };
  const plc = new RealS7Plc(config, { client, environment: { V2_MODE: 'live', V2_LIVE_WRITE_ENABLE: LIVE_WRITE_TOKEN } });
  assert.deepEqual(await plc.writeScanAck('A'), { address: 'M0.1', before: 1, after: 3 });
  assert.deepEqual(writes[0], [0, 1, Buffer.from([3])]);
  await plc.close();
});

test('real print adapter is token-gated and never invokes a shell', async () => {
  const calls = [];
  const printer = new RealBarTenderPrinter(config, {
    environment: { V2_MODE: 'live', V2_LIVE_WRITE_ENABLE: LIVE_WRITE_TOKEN },
    spawnProcess(command, args, options) {
      calls.push([command, args, options]);
      return { once(event, callback) { if (event === 'exit') queueMicrotask(() => callback(0)); return this; } };
    }
  });
  await printer.print({ template: 'C:\\templates\\P-100-A.btw' });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][2].shell, false);
});

test('live configuration rejects unconfirmed ATEQ placeholders', () => {
  const fs = require('node:fs'); const os = require('node:os'); const path = require('node:path');
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'ateq-live-')), 'live.json');
  fs.writeFileSync(file, JSON.stringify({ ...config, ateq: { ...config.ateq, A: { ...config.ateq.A, port: 'REQUIRED' } } }));
  assert.throws(() => loadLiveConfig(file), /ateq.A.port/);
  fs.rmSync(path.dirname(file), { recursive: true, force: true });
});
