const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { writesEnabled } = require('./liveConfig');

const ACTIVE_STEPS = new Set([4, 5, 6]);
const swap16 = (value) => ((value & 0xff) << 8) | ((value >>> 8) & 0xff);
function withTimeout(promise, label, timeoutMs = 5000) {
  let timer;
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs); });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

class DeviceError extends Error {
  constructor(device, cause) {
    super(`${device} communication failed: ${cause.message || cause}`);
    this.name = 'DeviceError'; this.code = 'DEVICE_COMMUNICATION_ERROR'; this.status = 503;
    this.details = { device, cause: cause.message || String(cause) };
  }
}

function callbackResult(invoke) {
  return new Promise((resolve, reject) => invoke((error, result) => error ? reject(error) : resolve(result)));
}

class RealAteqRtu {
  constructor(config, { createClient, environment = process.env } = {}) {
    this.config = config;
    this.environment = environment;
    this.createClient = createClient || (() => new (require('modbus-serial'))());
    this.clients = new Map();
  }

  async _client(station) {
    if (!['A', 'B'].includes(station)) throw new Error('station must be A or B');
    if (this.clients.has(station)) return this.clients.get(station);
    const settings = this.config.ateq[station];
    const client = this.createClient();
    try { await withTimeout(client.connectRTUBuffered(settings.port, { baudRate: settings.baudRate, parity: settings.parity, dataBits: settings.dataBits, stopBits: settings.stopBits }), `ATEQ ${station} connection`); }
    catch (error) { throw new DeviceError(`ATEQ-${station}`, error); }
    if (typeof client.setTimeout === 'function') client.setTimeout(5000);
    client.setID(settings.slaveId);
    this.clients.set(station, client);
    return client;
  }

  async readRealtime(station) {
    let result;
    try { result = await (await this._client(station)).readHoldingRegisters(0x0030, 13); }
    catch (error) { throw new DeviceError(`ATEQ-${station}`, error); }
    if (!result || !Array.isArray(result.data) || result.data.length !== 13) throw new Error('ATEQ realtime register block is incomplete');
    const words = result.data.map((value) => Number(value));
    const stepCode = swap16(words[4]);
    return { stepCode, testerActive: ACTIVE_STEPS.has(stepCode), resultCode: (swap16(words[3]) & 1) === 1 ? 0 : 1, rawWords: words };
  }

  async readProgram(station) {
    return swap16((await this.readRealtime(station)).rawWords[0]) + 1;
  }

  async writeProgram(station, targetProgram) {
    if (!writesEnabled(this.environment)) throw new Error('REAL_WRITE_DISABLED: set V2_MODE=live and the explicit write token');
    if (!Number.isInteger(targetProgram) || targetProgram < 1 || targetProgram > 255) throw new Error('ATEQ program must be 1..255');
    const realtime = await this.readRealtime(station);
    if (realtime.testerActive) throw new Error('ATEQ is active; program write is refused');
    try { await (await this._client(station)).writeRegister(0x0200, swap16(targetProgram - 1)); }
    catch (error) { throw new DeviceError(`ATEQ-${station}`, error); }
    const readBack = await this.readProgram(station);
    if (readBack !== targetProgram) throw new Error(`ATEQ program readback mismatch: expected ${targetProgram}, got ${readBack}`);
    return readBack;
  }

  async close() {
    for (const client of this.clients.values()) await client.close();
    this.clients.clear();
  }
}

class RealS7Plc {
  constructor(config, { client, environment = process.env } = {}) {
    this.config = config;
    this.environment = environment;
    this.client = client || new (require('node-snap7').S7Client)();
    this.connected = false;
  }

  async connect() {
    if (this.connected) return;
    try { await withTimeout(callbackResult((done) => this.client.ConnectTo(this.config.plc.host, this.config.plc.rack, this.config.plc.slot, done)), 'PLC connection'); }
    catch (error) { throw new DeviceError('PLC', error); }
    this.connected = true;
  }

  async readMByte(offset = 0) {
    await this.connect();
    let result;
    try { result = await callbackResult((done) => this.client.MBRead(offset, 1, done)); }
    catch (error) { throw new DeviceError('PLC', error); }
    if (!Buffer.isBuffer(result) || result.length !== 1) throw new Error('PLC M-byte read returned no byte');
    return result[0];
  }

  async writeScanAck(station) {
    if (!writesEnabled(this.environment)) throw new Error('REAL_WRITE_DISABLED: set V2_MODE=live and the explicit write token');
    const address = this.config.plc.scanAckBits?.[station];
    if (!['M0.0', 'M0.1'].includes(address)) throw new Error(`PLC scan acknowledgement for ${station} is not an approved M0 bit`);
    const bit = address === 'M0.1' ? 1 : 0;
    const current = await this.readMByte(0);
    const updated = current | (1 << bit);
    try { await callbackResult((done) => this.client.MBWrite(0, 1, Buffer.from([updated]), done)); }
    catch (error) { throw new DeviceError('PLC', error); }
    return { address, before: current, after: updated };
  }

  async close() { if (this.connected) this.client.Disconnect(); this.connected = false; }
}

class RealBarTenderPrinter {
  constructor(config, { environment = process.env, spawnProcess = spawn } = {}) { this.config = config; this.environment = environment; this.spawnProcess = spawnProcess; }
  async print({ template, copies = 1 }) {
    if (!writesEnabled(this.environment)) throw new Error('REAL_WRITE_DISABLED: set V2_MODE=live and the explicit write token');
    const root = path.resolve(this.config.printer.templateRoot);
    const selected = path.resolve(template);
    const relative = path.relative(root, selected);
    if (path.extname(selected).toLowerCase() !== '.btw' || !relative || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('print template must be a .btw under printer.templateRoot');
    await new Promise((resolve, reject) => {
      const child = this.spawnProcess(this.config.printer.executable, ['/F', selected, '/C', String(copies), '/P', this.config.printer.printerName, '/X'], { shell: false, windowsHide: true, stdio: 'ignore' });
      const timer = setTimeout(() => { child.kill(); reject(new Error('BarTender print timed out after 5000ms')); }, 5000);
      child.once('error', (error) => { clearTimeout(timer); reject(error); });
      child.once('exit', (code) => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`BarTender exited ${code}`)); });
    });
  }
}

class RealTcpScanner {
  constructor(config) { this.config = config; this.socket = null; this.buffer = ''; }
  connect(onCode) {
    if (this.socket && !this.socket.destroyed) return this.socket;
    this.socket = net.createConnection({ host: this.config.scanner.host, port: this.config.scanner.port });
    this.socket.setEncoding('utf8');
    this.socket.setTimeout(5000);
    this.socket.on('timeout', () => this.socket?.destroy(new Error('scanner connection timed out after 5000ms')));
    this.socket.on('error', (error) => { this.lastError = error; });
    this.socket.on('data', (chunk) => {
      this.buffer += chunk;
      let index;
      while ((index = this.buffer.indexOf(this.config.scanner.terminator || '\r')) >= 0) {
        const code = this.buffer.slice(0, index).trim(); this.buffer = this.buffer.slice(index + (this.config.scanner.terminator || '\r').length);
        if (code) onCode(code);
      }
    });
    return this.socket;
  }
  close() { this.socket?.destroy(); this.socket = null; }
}

module.exports = { DeviceError, RealAteqRtu, RealS7Plc, RealBarTenderPrinter, RealTcpScanner, swap16 };
