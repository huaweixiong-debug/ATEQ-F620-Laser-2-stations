const { addEvent, now } = require('./db');

const ACTIVE_STEPS = new Set([4, 5, 6]);
const TERMINAL_STEPS = new Set([65525, 65535]);

class DomainError extends Error {
  constructor(code, message, status = 409, details = {}) {
    super(message);
    this.name = 'DomainError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

function stationOrThrow(stationId) {
  const value = String(stationId || '').toUpperCase();
  if (!['A', 'B'].includes(value)) throw new DomainError('INVALID_STATION', 'station must be A or B', 400);
  return value;
}

function labelSerial(productCode, station, date, value) {
  return `${productCode}-${date.replaceAll('-', '')}-${station}-${String(value).padStart(6, '0')}`;
}

class StationService {
  constructor(db, devices) {
    this.db = db;
    this.devices = devices;
  }

  listStations() {
    return ['A', 'B'].map((stationId) => this.getStation(stationId));
  }

  getStation(stationId) {
    const station = stationOrThrow(stationId);
    const runtime = this.db.prepare('SELECT * FROM station_runtime WHERE station_id = ?').get(station);
    const cycle = runtime.current_cycle_id
      ? this.db.prepare(`SELECT c.*, p.code AS product_code, p.name AS product_name, o.code AS operator_code, o.name AS operator_name
          FROM cycles c JOIN products p ON p.id = c.product_id JOIN operators o ON o.id = c.operator_id WHERE c.id = ?`).get(runtime.current_cycle_id)
      : null;
    const attempts = cycle ? this.db.prepare('SELECT * FROM test_attempts WHERE cycle_id = ? ORDER BY attempt_no').all(cycle.id) : [];
    const preparation = cycle ? this.db.prepare('SELECT * FROM program_preparations WHERE cycle_id = ?').get(cycle.id) : null;
    const label = cycle ? this.db.prepare('SELECT * FROM labels WHERE cycle_id = ?').get(cycle.id) : null;
    const handshake = cycle ? this.db.prepare('SELECT * FROM plc_handshakes WHERE cycle_id = ?').get(cycle.id) : null;
    const observations = this.db.prepare('SELECT * FROM ateq_observations WHERE station_id = ? ORDER BY id DESC LIMIT 20').all(station).reverse();
    return {
      stationId: station,
      state: runtime.state,
      enabled: true,
      connected: true,
      deviceMode: 'simulated',
      testerActive: Boolean(runtime.tester_active),
      currentStep: runtime.last_step_code,
      resultCode: runtime.result_code,
      errorCode: runtime.error_code,
      plcByte: runtime.plc_byte,
      cycle: cycle ? {
        id: cycle.id,
        product: { id: cycle.product_id, code: cycle.product_code, name: cycle.product_name },
        operator: { id: cycle.operator_id, code: cycle.operator_code, name: cycle.operator_name },
        mode: cycle.mode,
        targetProgram: cycle.target_program,
        barcode: cycle.barcode,
        labelSerial: cycle.label_serial,
        state: cycle.state,
        finalResult: cycle.final_result,
        attempts
      } : null,
      preparation: preparation || null,
      label: label || null,
      handshake: handshake || null,
      observations
    };
  }

  prepare(stationId, input) {
    const station = stationOrThrow(stationId);
    const productId = Number(input.productId);
    const operatorId = Number(input.operatorId);
    const mode = input.mode;
    if (!Number.isInteger(productId) || !Number.isInteger(operatorId) || !['SINGLE', 'DUAL'].includes(mode)) throw new DomainError('INVALID_PREPARATION', 'productId, operatorId and mode are invalid', 400);
    const result = this.db.transaction(() => {
      const runtime = this.db.prepare('SELECT * FROM station_runtime WHERE station_id = ?').get(station);
      if (!['IDLE', 'COMPLETED_NG', 'RELEASED_OK'].includes(runtime.state)) throw new DomainError('STATION_NOT_READY', `station ${station} is ${runtime.state}`);
      const product = this.db.prepare('SELECT * FROM products WHERE id = ?').get(productId);
      const operator = this.db.prepare('SELECT * FROM operators WHERE id = ?').get(operatorId);
      if (!product) throw new DomainError('PRODUCT_NOT_FOUND', 'product was not found', 404);
      if (!operator) throw new DomainError('OPERATOR_NOT_FOUND', 'operator was not found', 404);
      const targetProgram = product.target_program;
      const createdAt = now();
      const cycleInfo = this.db.prepare(`INSERT INTO cycles (station_id, product_id, operator_id, mode, target_program, state, created_at)
        VALUES (?, ?, ?, ?, ?, 'PREPARING_PROGRAM', ?)`).run(station, productId, operatorId, mode, targetProgram, createdAt);
      const cycleId = cycleInfo.lastInsertRowid;
      this.db.prepare('UPDATE station_runtime SET state = ?, current_cycle_id = ?, error_code = NULL, result_code = NULL, updated_at = ? WHERE station_id = ?')
        .run('PREPARING_PROGRAM', cycleId, createdAt, station);
      addEvent(this.db, station, cycleId, 'CYCLE_PREPARING', { productId, operatorId, mode, targetProgram });

      let active;
      let readBefore;
      try {
        active = this.devices.ateq.isActive(station);
        readBefore = this.devices.ateq.readProgram(station);
      } catch (error) {
        throw new DomainError('ATEQ_READ_FAILED', 'ATEQ program read failed; station cannot arm', 503, { cause: error.message });
      }
      let writePerformed = 0;
      if (active) throw new DomainError('ATEQ_ACTIVE', 'ATEQ tester is active; program write is refused and station cannot arm');
      if (readBefore !== targetProgram) {
        try {
          this.devices.ateq.writeProgram(station, targetProgram);
        } catch (error) {
          throw new DomainError('ATEQ_WRITE_FAILED', 'ATEQ program write failed; station cannot arm', 503, { cause: error.message });
        }
        writePerformed = 1;
      }
      let readBack;
      try {
        readBack = this.devices.ateq.readProgram(station);
      } catch (error) {
        throw new DomainError('ATEQ_READBACK_FAILED', 'ATEQ program read-back failed; station cannot arm', 503, { cause: error.message });
      }
      const verified = readBack === targetProgram;
      this.db.prepare(`INSERT INTO program_preparations (cycle_id, station_id, target_program, read_before, write_performed, read_back, verified, created_at, error_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(cycleId, station, targetProgram, readBefore, writePerformed, readBack, verified ? 1 : 0, now(), verified ? null : 'PROGRAM_VERIFY_FAILED');
      if (!verified) throw new DomainError('PROGRAM_VERIFY_FAILED', 'ATEQ target program could not be verified');
      this.db.prepare("UPDATE cycles SET state = 'ARMED' WHERE id = ?").run(cycleId);
      this.db.prepare("UPDATE station_runtime SET state = 'ARMED', baseline_terminal = 1, tester_active = 0, last_step_code = 65525, updated_at = ? WHERE station_id = ?").run(now(), station);
      addEvent(this.db, station, cycleId, 'CYCLE_ARMED', { readBefore, writePerformed, readBack });
      return { cycleId, stationId: station, state: 'ARMED', program: { readBefore, writePerformed, readBack, verified: true } };
    })();
    return result;
  }

  observe(stationId, input) {
    const station = stationOrThrow(stationId);
    const stepCode = Number(input.stepCode);
    const resultCode = input.resultCode === undefined || input.resultCode === null ? null : Number(input.resultCode);
    if (!Number.isInteger(stepCode) || stepCode < 0 || stepCode > 65535) throw new DomainError('INVALID_OBSERVATION', 'stepCode must be an integer from 0 to 65535', 400);
    if (resultCode !== null && (!Number.isInteger(resultCode) || resultCode < 0 || resultCode > 65535)) throw new DomainError('INVALID_RESULT_CODE', 'resultCode must be an integer from 0 to 65535', 400);
    return this.db.transaction(() => {
      const runtime = this.db.prepare('SELECT * FROM station_runtime WHERE station_id = ?').get(station);
      if (!runtime.current_cycle_id) throw new DomainError('NO_CYCLE', 'station has no prepared cycle');
      if (!['ARMED', 'TEST_1_RUNNING', 'WAIT_TEST_2', 'TEST_2_RUNNING'].includes(runtime.state)) throw new DomainError('OBSERVATION_NOT_ALLOWED', `observations are not accepted in ${runtime.state}`);
      const cycle = this.db.prepare('SELECT * FROM cycles WHERE id = ?').get(runtime.current_cycle_id);
      const maxIndex = this.db.prepare('SELECT COALESCE(MAX(observation_index), 0) AS n FROM ateq_observations WHERE station_id = ?').get(station).n;
      this.db.prepare(`INSERT INTO ateq_observations (station_id, cycle_id, observation_index, step_code, result_code, observed_at)
        VALUES (?, ?, ?, ?, ?, ?)`).run(station, cycle.id, maxIndex + 1, stepCode, resultCode, now());
      this.db.prepare('UPDATE station_runtime SET last_step_code = ?, tester_active = ?, updated_at = ? WHERE station_id = ?')
        .run(stepCode, ACTIVE_STEPS.has(stepCode) ? 1 : 0, now(), station);

      if ((runtime.state === 'ARMED' || runtime.state === 'WAIT_TEST_2') && ACTIVE_STEPS.has(stepCode)) {
        const attemptNo = runtime.state === 'ARMED' ? 1 : 2;
        this.db.prepare(`INSERT INTO test_attempts (cycle_id, attempt_no, started_at) VALUES (?, ?, ?)`).run(cycle.id, attemptNo, now());
        const runningState = attemptNo === 1 ? 'TEST_1_RUNNING' : 'TEST_2_RUNNING';
        this.db.prepare('UPDATE cycles SET state = ? WHERE id = ?').run(runningState, cycle.id);
        this.db.prepare('UPDATE station_runtime SET state = ?, updated_at = ? WHERE station_id = ?').run(runningState, now(), station);
        addEvent(this.db, station, cycle.id, 'ATTEMPT_STARTED', { attemptNo, stepCode });
        return { stationId: station, cycleId: cycle.id, state: runningState, attemptStarted: attemptNo };
      }

      if ((runtime.state === 'TEST_1_RUNNING' || runtime.state === 'TEST_2_RUNNING') && TERMINAL_STEPS.has(stepCode)) {
        if (resultCode === null) throw new DomainError('RESULT_REQUIRED', 'resultCode is required for a terminal observation', 400);
        const attempt = this.db.prepare('SELECT * FROM test_attempts WHERE cycle_id = ? AND completed_at IS NULL ORDER BY attempt_no DESC LIMIT 1').get(cycle.id);
        if (!attempt) return { stationId: station, cycleId: cycle.id, state: runtime.state, attemptCompleted: false, ignored: true };
        const outcome = resultCode === 0 ? 'OK' : 'NG';
        this.db.prepare('UPDATE test_attempts SET completed_at = ?, result_code = ?, outcome = ? WHERE id = ?').run(now(), resultCode, outcome, attempt.id);
        addEvent(this.db, station, cycle.id, 'ATTEMPT_COMPLETED', { attemptNo: attempt.attempt_no, stepCode, resultCode, outcome });
        if (cycle.mode === 'DUAL' && attempt.attempt_no === 1) {
          this.db.prepare("UPDATE cycles SET state = 'WAIT_TEST_2' WHERE id = ?").run(cycle.id);
          this.db.prepare("UPDATE station_runtime SET state = 'WAIT_TEST_2', updated_at = ? WHERE station_id = ?").run(now(), station);
          return { stationId: station, cycleId: cycle.id, state: 'WAIT_TEST_2', attemptCompleted: 1, outcome };
        }
        const attempts = this.db.prepare('SELECT outcome FROM test_attempts WHERE cycle_id = ? ORDER BY attempt_no').all(cycle.id);
        const finalResult = attempts.length === (cycle.mode === 'DUAL' ? 2 : 1) && attempts.every((item) => item.outcome === 'OK') ? 'OK' : 'NG';
        this.db.prepare("UPDATE cycles SET state = 'FINALIZING' WHERE id = ?").run(cycle.id);
        this.db.prepare("UPDATE station_runtime SET state = 'FINALIZING', result_code = ?, updated_at = ? WHERE station_id = ?").run(resultCode, now(), station);
        addEvent(this.db, station, cycle.id, 'FINALIZING', { finalResult });
        if (finalResult === 'NG') {
          this.db.prepare("UPDATE cycles SET state = 'COMPLETED_NG', final_result = 'NG', completed_at = ? WHERE id = ?").run(now(), cycle.id);
          this.db.prepare("UPDATE station_runtime SET state = 'COMPLETED_NG', updated_at = ? WHERE station_id = ?").run(now(), station);
          addEvent(this.db, station, cycle.id, 'COMPLETED_NG', { finalResult });
          return { stationId: station, cycleId: cycle.id, state: 'COMPLETED_NG', finalResult: 'NG', label: null };
        }
        return this._allocateAndPrint(station, cycle.id);
      }
      return { stationId: station, cycleId: cycle.id, state: runtime.state, observed: true, attemptStarted: false };
    })();
  }

  _allocateAndPrint(station, cycleId) {
    this.db.prepare("UPDATE cycles SET state = 'ALLOCATING_LABEL', final_result = 'OK', completed_at = ? WHERE id = ?").run(now(), cycleId);
    this.db.prepare("UPDATE station_runtime SET state = 'ALLOCATING_LABEL', updated_at = ? WHERE station_id = ?").run(now(), station);
    const product = this.db.prepare('SELECT p.code FROM cycles c JOIN products p ON p.id = c.product_id WHERE c.id = ?').get(cycleId);
    const date = new Date().toISOString().slice(0, 10);
    const sequenceName = `${product.code}:${date}:${station}`;
    this.db.prepare('INSERT OR IGNORE INTO label_sequences (name, next_value) VALUES (?, 1)').run(sequenceName);
    const sequence = this.db.prepare('SELECT next_value FROM label_sequences WHERE name = ?').get(sequenceName).next_value;
    this.db.prepare('UPDATE label_sequences SET next_value = ? WHERE name = ?').run(sequence + 1, sequenceName);
    const serial = labelSerial(product.code, station, date, sequence);
    const qrPayload = `ATEQ|${station}|${cycleId}|${serial}`;
    this.db.prepare("UPDATE cycles SET state = 'PRINT_PENDING', barcode = ?, label_serial = ? WHERE id = ?").run(qrPayload, serial, cycleId);
    this.db.prepare(`INSERT INTO labels (cycle_id, station_id, label_serial, qr_payload, status, created_at)
      VALUES (?, ?, ?, ?, 'PRINT_PENDING', ?)`).run(cycleId, station, serial, qrPayload, now());
    addEvent(this.db, station, cycleId, 'LABEL_ALLOCATED', { serial, qrPayload });
    const printed = this.devices.printer.print({ cycleId, stationId: station, labelSerial: serial, qrPayload });
    if (!printed) throw new DomainError('PRINT_FAILED', 'simulated printer did not accept the label', 503);
    this.db.prepare("UPDATE labels SET status = 'PRINTED', printed_at = ? WHERE cycle_id = ?").run(now(), cycleId);
    this.db.prepare("UPDATE cycles SET state = 'PRINTED_AWAIT_LABEL_SCAN' WHERE id = ?").run(cycleId);
    this.db.prepare("UPDATE station_runtime SET state = 'PRINTED_AWAIT_LABEL_SCAN', updated_at = ? WHERE station_id = ?").run(now(), station);
    addEvent(this.db, station, cycleId, 'LABEL_PRINTED_AWAIT_SCAN', { serial });
    return { stationId: station, cycleId, state: 'PRINTED_AWAIT_LABEL_SCAN', finalResult: 'OK', labelSerial: serial, barcode: qrPayload };
  }

  scanLabel(stationId, scannedValue) {
    const station = stationOrThrow(stationId);
    if (!scannedValue || typeof scannedValue !== 'string') throw new DomainError('INVALID_SCAN', 'barcode is required', 400);
    const result = this.db.transaction(() => {
      const runtime = this.db.prepare('SELECT * FROM station_runtime WHERE station_id = ?').get(station);
      if (runtime.state !== 'PRINTED_AWAIT_LABEL_SCAN' || !runtime.current_cycle_id) throw new DomainError('LABEL_SCAN_NOT_ALLOWED', `station ${station} is not awaiting a final OK label`);
      const label = this.db.prepare(`SELECT l.*, c.state AS cycle_state, c.final_result FROM labels l JOIN cycles c ON c.id = l.cycle_id
        WHERE l.station_id = ? AND l.status = 'PRINTED' AND (l.label_serial = ? OR l.qr_payload = ?)`)
        .get(station, scannedValue, scannedValue);
      if (!label || label.cycle_state !== 'PRINTED_AWAIT_LABEL_SCAN' || label.final_result !== 'OK') throw new DomainError('LABEL_NOT_ELIGIBLE', 'scanned label is not a persisted printed final-OK label at this station');
      const bitAddress = station === 'A' ? 'M0.1' : 'M0.0';
      this.db.prepare(`INSERT INTO plc_handshakes (cycle_id, station_id, bit_address, state, pulse_sent, created_at)
        VALUES (?, ?, ?, 'INTENT', 0, ?)`).run(label.cycle_id, station, bitAddress, now());
      this.db.prepare("UPDATE cycles SET state = 'PLC_CLEAR_PENDING' WHERE id = ?").run(label.cycle_id);
      this.db.prepare("UPDATE station_runtime SET state = 'PLC_CLEAR_PENDING', updated_at = ? WHERE station_id = ?").run(now(), station);
      addEvent(this.db, station, label.cycle_id, 'PLC_HANDSHAKE_INTENT', { bitAddress });
      return { label, bitAddress };
    })();
    try {
      this.devices.plc.pulse(result.bitAddress);
      this.db.transaction(() => {
        this.db.prepare("UPDATE plc_handshakes SET state = 'PULSE_SENT', pulse_sent = 1 WHERE cycle_id = ? AND state = 'INTENT'").run(result.label.cycle_id);
        this.db.prepare('UPDATE station_runtime SET plc_byte = ?, updated_at = ? WHERE station_id = ?').run(this.devices.plc.readByte(), now(), station);
        addEvent(this.db, station, result.label.cycle_id, 'PLC_PULSE_SENT', { bitAddress: result.bitAddress, plcByte: this.devices.plc.readByte() });
      })();
    } catch (error) {
      this.db.prepare("UPDATE station_runtime SET state = 'RECOVERY_REQUIRED', error_code = 'PLC_PULSE_FAILED', updated_at = ? WHERE station_id = ?").run(now(), station);
      throw new DomainError('PLC_PULSE_FAILED', 'PLC pulse failed; no automatic retry will be attempted', 503, { cause: error.message });
    }
    return { stationId: station, cycleId: result.label.cycle_id, state: 'PLC_CLEAR_PENDING', bitAddress: result.bitAddress, plcByte: this.devices.plc.readByte() };
  }

  confirmPlcClear(stationId) {
    const station = stationOrThrow(stationId);
    const runtime = this.db.prepare('SELECT * FROM station_runtime WHERE station_id = ?').get(station);
    if (runtime.state === 'RELEASED_OK') return { stationId: station, state: 'RELEASED_OK', alreadyCleared: true };
    if (runtime.state !== 'PLC_CLEAR_PENDING' || !runtime.current_cycle_id) throw new DomainError('PLC_CLEAR_NOT_PENDING', `station ${station} has no pending PLC clear`);
    const handshake = this.db.prepare("SELECT * FROM plc_handshakes WHERE cycle_id = ? AND state = 'PULSE_SENT'").get(runtime.current_cycle_id);
    if (!handshake) throw new DomainError('PLC_HANDSHAKE_UNCERTAIN', 'handshake is pending without a confirmed pulse; manual recovery is required');
    if ((this.devices.plc.readByte() & (handshake.bit_address === 'M0.1' ? 2 : 1)) !== 0) throw new DomainError('PLC_CLEAR_FAILED', 'PLC bit did not clear', 503);
    this.db.transaction(() => {
      this.db.prepare("UPDATE plc_handshakes SET state = 'CLEARED', cleared_at = ? WHERE id = ?").run(now(), handshake.id);
      this.db.prepare("UPDATE labels SET status = 'SCANNED', scanned_at = ? WHERE cycle_id = ?").run(now(), runtime.current_cycle_id);
      this.db.prepare("UPDATE cycles SET state = 'RELEASED_OK' WHERE id = ?").run(runtime.current_cycle_id);
      this.db.prepare("UPDATE station_runtime SET state = 'RELEASED_OK', plc_byte = ?, updated_at = ? WHERE station_id = ?").run(this.devices.plc.readByte(), now(), station);
      addEvent(this.db, station, runtime.current_cycle_id, 'PLC_CLEARED_RELEASED', { bitAddress: handshake.bit_address });
    })();
    return { stationId: station, cycleId: runtime.current_cycle_id, state: 'RELEASED_OK', bitAddress: handshake.bit_address, plcByte: this.devices.plc.readByte() };
  }
}

module.exports = { ACTIVE_STEPS, TERMINAL_STEPS, DomainError, StationService, stationOrThrow };
