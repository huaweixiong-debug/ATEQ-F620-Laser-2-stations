const crypto = require('node:crypto');
const path = require('node:path');
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const { body, validationResult } = require('express-validator');
const { createDb } = require('./db');
const { SIMULATED_ONLY, createSimulatedDevices } = require('./fakeDevices');
const { DomainError, StationService, stationOrThrow } = require('./stateMachine');
const { loadLiveConfig } = require('./liveConfig');
const { DeviceError, RealAteqRtu, RealS7Plc, RealBarTenderPrinter, RealTcpScanner } = require('./realDevices');

function requestId(req) {
  const supplied = req.get('x-request-id');
  return supplied && /^[A-Za-z0-9._:-]{1,100}$/.test(supplied) ? supplied : crypto.randomUUID();
}

function ok(res, req, data, status = 200) {
  return res.status(status).json({ data, requestId: req.requestId });
}

function errorResponse(res, req, error) {
  const status = error instanceof DomainError || error instanceof DeviceError ? error.status : 500;
  const code = error instanceof DomainError || error instanceof DeviceError ? error.code : 'INTERNAL_ERROR';
  const message = error instanceof DomainError || error instanceof DeviceError ? error.message : 'internal server error';
  const details = error instanceof DomainError || error instanceof DeviceError ? error.details : {};
  if (status >= 500) console.error(`[${req.requestId}] ${code}: ${error.stack || error.message}`);
  return res.status(status).json({ error: { code, message, details }, requestId: req.requestId });
}

function validateRequest(req) {
  const result = validationResult(req);
  if (!result.isEmpty()) throw new DomainError('VALIDATION_ERROR', 'request validation failed', 400, { fields: result.array() });
}

function idempotencyKey(req) {
  const key = req.body && req.body.idempotencyKey ? req.body.idempotencyKey : req.get('idempotency-key');
  if (!key || typeof key !== 'string' || !/^[A-Za-z0-9._:-]{8,128}$/.test(key)) {
    throw new DomainError('IDEMPOTENCY_KEY_REQUIRED', 'POST requests require an 8-128 character idempotencyKey or Idempotency-Key header', 400);
  }
  return key;
}

function requestHash(req) {
  const bodyCopy = { ...(req.body || {}) };
  delete bodyCopy.idempotencyKey;
  return crypto.createHash('sha256').update(`${req.method}|${req.path}|${JSON.stringify(bodyCopy)}`).digest('hex');
}

function withIdempotency(db, req, res, work) {
  try {
    const key = idempotencyKey(req);
    const hash = requestHash(req);
    const route = req.path;
    const existing = db.prepare('SELECT * FROM idempotency_keys WHERE route = ? AND idempotency_key = ?').get(route, key);
    if (existing) {
      if (existing.request_hash !== hash) throw new DomainError('IDEMPOTENCY_CONFLICT', 'idempotency key was already used with a different request', 409);
      return res.status(existing.status_code).json(JSON.parse(existing.response_json));
    }
    const data = work();
    const response = { data, requestId: req.requestId };
    db.prepare(`INSERT INTO idempotency_keys (route, idempotency_key, request_hash, status_code, response_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?)`).run(route, key, hash, 200, JSON.stringify(response), new Date().toISOString());
    return res.json(response);
  } catch (error) {
    return errorResponse(res, req, error);
  }
}

async function withAsyncIdempotency(db, req, res, work) {
  try {
    const key = idempotencyKey(req);
    const hash = requestHash(req);
    const route = req.path;
    const existing = db.prepare('SELECT * FROM idempotency_keys WHERE route = ? AND idempotency_key = ?').get(route, key);
    if (existing) {
      if (existing.request_hash !== hash) throw new DomainError('IDEMPOTENCY_CONFLICT', 'idempotency key was already used with a different request', 409);
      return res.status(existing.status_code).json(JSON.parse(existing.response_json));
    }
    const data = await work();
    const response = { data, requestId: req.requestId };
    db.prepare(`INSERT INTO idempotency_keys (route, idempotency_key, request_hash, status_code, response_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?)`).run(route, key, hash, 200, JSON.stringify(response), new Date().toISOString());
    return res.json(response);
  } catch (error) {
    return errorResponse(res, req, error);
  }
}

function createApp(options = {}) {
  if (!SIMULATED_ONLY || (options.deviceMode && !['simulated', 'live'].includes(options.deviceMode))) throw new Error('V2 safety guard: only simulated devices or explicitly configured live adapters are supported');
  const db = options.db || createDb(options.dbPath || path.join(__dirname, 'v2.sqlite'));
  const devices = options.devices || createSimulatedDevices();
  const service = new StationService(db, devices);
  const liveConfig = options.liveConfig || (options.liveConfigPath ? loadLiveConfig(options.liveConfigPath) : null);
  const live = liveConfig ? {
    config: liveConfig,
    ateq: null,
    plc: null,
    printer: null,
    scanner: null,
    getAteq: () => (live.ateq ||= new RealAteqRtu(live.config)),
    getPlc: () => (live.plc ||= new RealS7Plc(live.config)),
    getPrinter: () => (live.printer ||= new RealBarTenderPrinter(live.config)),
    getScanner: () => (live.scanner ||= new RealTcpScanner(live.config))
  } : null;
  const app = express();
  app.locals.db = db;
  app.locals.devices = devices;
  app.locals.service = service;
  app.locals.deviceMode = options.deviceMode || 'simulated';
  app.locals.live = live;
  app.use(helmet());
  app.use(cors({ origin: false }));
  app.use((req, _res, next) => { req.requestId = requestId(req); next(); });
  app.use(express.json({ limit: '64kb' }));

  app.get('/api/stations', (req, res) => {
    try { return ok(res, req, { stations: service.listStations() }); } catch (error) { return errorResponse(res, req, error); }
  });
  app.get('/api/stations/:station', (req, res) => {
    try { stationOrThrow(req.params.station); return ok(res, req, service.getStation(req.params.station)); } catch (error) { return errorResponse(res, req, error); }
  });
  app.get('/api/catalog/products', (req, res) => {
    try { return ok(res, req, { products: db.prepare('SELECT id, code, name, target_program AS targetProgram FROM products ORDER BY id').all() }); } catch (error) { return errorResponse(res, req, error); }
  });
  app.get('/api/catalog/operators', (req, res) => {
    try { return ok(res, req, { operators: db.prepare('SELECT id, code, name FROM operators ORDER BY id').all() }); } catch (error) { return errorResponse(res, req, error); }
  });

  app.post('/api/stations/:station/prepare', [
    body('productId').isInt({ min: 1 }),
    body('operatorId').isInt({ min: 1 }),
    body('mode').isIn(['SINGLE', 'DUAL'])
  ], (req, res) => {
    try { validateRequest(req); } catch (error) { return errorResponse(res, req, error); }
    return withIdempotency(db, req, res, () => service.prepare(req.params.station, req.body));
  });

  app.post('/api/stations/:station/label-scans', [
    body('barcode').isString().trim().isLength({ min: 1, max: 300 })
  ], (req, res) => {
    try { validateRequest(req); } catch (error) { return errorResponse(res, req, error); }
    return withIdempotency(db, req, res, () => service.scanLabel(req.params.station, req.body.barcode));
  });

  app.post('/api/simulator/stations/:station/ateq-observations', [
    body('stepCode').isInt({ min: 0, max: 65535 }),
    body('resultCode').optional({ nullable: true }).isInt({ min: 0, max: 65535 })
  ], (req, res) => {
    try { validateRequest(req); } catch (error) { return errorResponse(res, req, error); }
    return withIdempotency(db, req, res, () => service.observe(req.params.station, req.body));
  });

  app.post('/api/simulator/stations/:station/plc-clear', [], (req, res) => {
    try { validateRequest(req); } catch (error) { return errorResponse(res, req, error); }
    return withIdempotency(db, req, res, () => {
      const station = stationOrThrow(req.params.station);
      const bitAddress = station === 'A' ? 'M0.1' : 'M0.0';
      devices.plc.autoClear(bitAddress);
      return service.confirmPlcClear(station);
    });
  });

  const requireLive = () => {
    if (!live) throw new DomainError('LIVE_CONFIG_REQUIRED', 'live device routes require a validated liveConfig', 503);
    return live;
  };

  app.get('/api/live/ateq/:station/status', async (req, res) => {
    try {
      const station = stationOrThrow(req.params.station);
      const status = await requireLive().getAteq().readRealtime(station);
      return ok(res, req, { station, ...status });
    } catch (error) { return errorResponse(res, req, error); }
  });

  app.post('/api/live/ateq/:station/program', [body('targetProgram').isInt({ min: 1, max: 255 })], async (req, res) => {
    try { validateRequest(req); } catch (error) { return errorResponse(res, req, error); }
    return withAsyncIdempotency(db, req, res, async () => {
      const station = stationOrThrow(req.params.station);
      const targetProgram = Number(req.body.targetProgram);
      const program = await requireLive().getAteq().writeProgram(station, targetProgram);
      return { station, program };
    });
  });

  app.get('/api/live/plc/m0', async (req, res) => {
    try {
      const byte = await requireLive().getPlc().readMByte(0);
      return ok(res, req, { offset: 0, value: byte });
    } catch (error) { return errorResponse(res, req, error); }
  });

  app.post('/api/live/plc/scan-ack', [body('station').isIn(['A', 'B'])], async (req, res) => {
    try { validateRequest(req); } catch (error) { return errorResponse(res, req, error); }
    return withAsyncIdempotency(db, req, res, async () => ({ station: req.body.station, ...(await requireLive().getPlc().writeScanAck(req.body.station)) }));
  });

  app.post('/api/live/printer/print', [body('template').isString().trim().isLength({ min: 1, max: 1000 }), body('copies').optional().isInt({ min: 1, max: 100 })], async (req, res) => {
    try { validateRequest(req); } catch (error) { return errorResponse(res, req, error); }
    return withAsyncIdempotency(db, req, res, async () => {
      const copies = req.body.copies === undefined ? 1 : Number(req.body.copies);
      await requireLive().getPrinter().print({ template: req.body.template, copies });
      return { printed: true, template: req.body.template, copies };
    });
  });

  app.post('/api/live/scanner/connect', [], async (req, res) => {
    return withAsyncIdempotency(db, req, res, async () => {
      const connected = requireLive().getScanner().connect((code) => app.emit('scanner-code', { code, receivedAt: new Date().toISOString() }));
      return { connected: Boolean(connected) };
    });
  });

  app.use((error, req, res, next) => {
    if (error instanceof SyntaxError && error.status === 400 && 'body' in error) return errorResponse(res, req, new DomainError('INVALID_JSON', 'request body is not valid JSON', 400));
    return next(error);
  });
  app.use((error, req, res, _next) => errorResponse(res, req, error));
  return app;
}

if (require.main === module) {
  const deviceMode = process.env.V2_DEVICE_MODE || 'simulated';
  const app = createApp({
    deviceMode,
    dbPath: process.env.V2_DB_PATH || path.join(__dirname, 'v2.sqlite'),
    liveConfigPath: deviceMode === 'live' ? (process.env.V2_LIVE_CONFIG || path.join(__dirname, 'config', 'live.json')) : undefined
  });
  const port = Number(process.env.PORT || 3000);
  app.listen(port, () => console.log(`ATEQ V2 ${deviceMode} service listening on ${port}`));
}

module.exports = { createApp, withIdempotency, withAsyncIdempotency, errorResponse };
