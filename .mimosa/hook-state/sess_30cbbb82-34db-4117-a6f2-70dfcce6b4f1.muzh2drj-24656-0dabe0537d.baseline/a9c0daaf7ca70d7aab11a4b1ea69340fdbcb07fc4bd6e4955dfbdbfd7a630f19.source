const fs = require('node:fs');

const LIVE_WRITE_TOKEN = 'I_UNDERSTAND_THIS_WRITES_REAL_EQUIPMENT';

function requireString(value, name) {
  if (typeof value !== 'string' || !value.trim() || value === 'REQUIRED') throw new Error(`live configuration requires ${name}`);
  return value.trim();
}

function loadLiveConfig(filename) {
  const config = JSON.parse(fs.readFileSync(filename, 'utf8'));
  if (config.mode !== 'live') throw new Error('live configuration mode must be live');
  requireString(config.plc?.host, 'plc.host');
  for (const station of ['A', 'B']) {
    requireString(config.ateq?.[station]?.port, `ateq.${station}.port`);
    if (!Number.isInteger(config.ateq?.[station]?.slaveId) || config.ateq[station].slaveId < 1 || config.ateq[station].slaveId > 255) throw new Error(`live configuration requires ateq.${station}.slaveId (1..255)`);
  }
  requireString(config.scanner?.host, 'scanner.host');
  if (!Number.isInteger(config.scanner?.port) || config.scanner.port < 1 || config.scanner.port > 65535) throw new Error('live configuration requires scanner.port');
  requireString(config.printer?.executable, 'printer.executable');
  requireString(config.printer?.templateRoot, 'printer.templateRoot');
  requireString(config.printer?.printerName, 'printer.printerName');
  if (config.scanner.terminator === '\\r') config.scanner.terminator = '\r';
  if (config.scanner.terminator === '\\n') config.scanner.terminator = '\n';
  return config;
}

function writesEnabled(environment = process.env) {
  return environment.V2_MODE === 'live' && environment.V2_LIVE_WRITE_ENABLE === LIVE_WRITE_TOKEN;
}

module.exports = { LIVE_WRITE_TOKEN, loadLiveConfig, writesEnabled };
