const Database = require('better-sqlite3');

const STATES = [
  'IDLE', 'PREPARING_PROGRAM', 'ARMED', 'TEST_1_RUNNING', 'WAIT_TEST_2',
  'TEST_2_RUNNING', 'FINALIZING', 'COMPLETED_NG', 'ALLOCATING_LABEL',
  'PRINT_PENDING', 'PRINTED_AWAIT_LABEL_SCAN', 'PLC_CLEAR_PENDING',
  'RELEASED_OK', 'RECOVERY_REQUIRED', 'FAULT'
];

const now = () => new Date().toISOString();

function createDb(filename) {
  const db = new Database(filename);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.pragma('busy_timeout = 5000');
  db.exec(`
    CREATE TABLE IF NOT EXISTS products (
      id INTEGER PRIMARY KEY,
      code TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      target_program INTEGER NOT NULL CHECK (target_program BETWEEN 1 AND 9999)
    );
    CREATE TABLE IF NOT EXISTS operators (
      id INTEGER PRIMARY KEY,
      code TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS station_runtime (
      station_id TEXT PRIMARY KEY CHECK (station_id IN ('A', 'B')),
      state TEXT NOT NULL CHECK (state IN (${STATES.map((s) => `'${s}'`).join(',')})),
      current_cycle_id INTEGER UNIQUE,
      last_step_code INTEGER NOT NULL DEFAULT 65525,
      baseline_terminal INTEGER NOT NULL DEFAULT 1 CHECK (baseline_terminal IN (0, 1)),
      tester_active INTEGER NOT NULL DEFAULT 0 CHECK (tester_active IN (0, 1)),
      plc_byte INTEGER NOT NULL DEFAULT 0 CHECK (plc_byte BETWEEN 0 AND 255),
      result_code INTEGER,
      error_code TEXT,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cycles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      station_id TEXT NOT NULL CHECK (station_id IN ('A', 'B')),
      product_id INTEGER NOT NULL REFERENCES products(id),
      operator_id INTEGER NOT NULL REFERENCES operators(id),
      mode TEXT NOT NULL CHECK (mode IN ('SINGLE', 'DUAL')),
      target_program INTEGER NOT NULL CHECK (target_program BETWEEN 1 AND 9999),
      barcode TEXT,
      label_serial TEXT,
      state TEXT NOT NULL CHECK (state IN (${STATES.map((s) => `'${s}'`).join(',')})),
      final_result TEXT CHECK (final_result IN ('OK', 'NG') OR final_result IS NULL),
      created_at TEXT NOT NULL,
      completed_at TEXT,
      CHECK ((barcode IS NULL AND label_serial IS NULL) OR
        (final_result = 'OK' AND barcode IS NOT NULL AND label_serial IS NOT NULL))
    );
    CREATE TABLE IF NOT EXISTS test_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      cycle_id INTEGER NOT NULL REFERENCES cycles(id),
      attempt_no INTEGER NOT NULL CHECK (attempt_no IN (1, 2)),
      started_at TEXT NOT NULL,
      completed_at TEXT,
      result_code INTEGER,
      outcome TEXT CHECK (outcome IN ('OK', 'NG') OR outcome IS NULL),
      UNIQUE (cycle_id, attempt_no)
    );
    CREATE TABLE IF NOT EXISTS program_preparations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      cycle_id INTEGER NOT NULL UNIQUE REFERENCES cycles(id),
      station_id TEXT NOT NULL CHECK (station_id IN ('A', 'B')),
      target_program INTEGER NOT NULL CHECK (target_program BETWEEN 1 AND 9999),
      read_before INTEGER,
      write_performed INTEGER NOT NULL DEFAULT 0 CHECK (write_performed IN (0, 1)),
      read_back INTEGER,
      verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
      created_at TEXT NOT NULL,
      error_code TEXT
    );
    CREATE TABLE IF NOT EXISTS ateq_observations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      station_id TEXT NOT NULL CHECK (station_id IN ('A', 'B')),
      cycle_id INTEGER REFERENCES cycles(id),
      observation_index INTEGER NOT NULL,
      step_code INTEGER NOT NULL,
      result_code INTEGER,
      observed_at TEXT NOT NULL,
      UNIQUE (station_id, observation_index)
    );
    CREATE TABLE IF NOT EXISTS labels (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      cycle_id INTEGER NOT NULL UNIQUE REFERENCES cycles(id),
      station_id TEXT NOT NULL CHECK (station_id IN ('A', 'B')),
      label_serial TEXT NOT NULL UNIQUE,
      qr_payload TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('PRINT_PENDING', 'PRINTED', 'SCANNED')),
      printed_at TEXT,
      scanned_at TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS plc_handshakes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      cycle_id INTEGER NOT NULL UNIQUE REFERENCES cycles(id),
      station_id TEXT NOT NULL CHECK (station_id IN ('A', 'B')),
      bit_address TEXT NOT NULL CHECK ((station_id = 'A' AND bit_address = 'M0.1') OR
        (station_id = 'B' AND bit_address = 'M0.0')),
      state TEXT NOT NULL CHECK (state IN ('INTENT', 'PULSE_SENT', 'CLEARED')),
      pulse_sent INTEGER NOT NULL DEFAULT 0 CHECK (pulse_sent IN (0, 1)),
      created_at TEXT NOT NULL,
      cleared_at TEXT
    );
    CREATE TABLE IF NOT EXISTS station_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      station_id TEXT NOT NULL CHECK (station_id IN ('A', 'B')),
      cycle_id INTEGER REFERENCES cycles(id),
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS idempotency_keys (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      route TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      status_code INTEGER NOT NULL,
      response_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE (route, idempotency_key)
    );
    CREATE TABLE IF NOT EXISTS label_sequences (
      name TEXT PRIMARY KEY,
      next_value INTEGER NOT NULL CHECK (next_value >= 1)
    );
    CREATE TRIGGER IF NOT EXISTS labels_require_final_ok
      BEFORE INSERT ON labels
      BEGIN
        SELECT CASE WHEN (SELECT final_result FROM cycles WHERE id = NEW.cycle_id) <> 'OK'
          THEN RAISE(ABORT, 'label requires final OK') END;
      END;
    CREATE TRIGGER IF NOT EXISTS station_events_immutable_update
      BEFORE UPDATE ON station_events BEGIN SELECT RAISE(ABORT, 'station_events are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS station_events_immutable_delete
      BEFORE DELETE ON station_events BEGIN SELECT RAISE(ABORT, 'station_events are immutable'); END;
  `);
  db.prepare("INSERT OR IGNORE INTO station_runtime (station_id, state, updated_at) VALUES ('A', 'IDLE', ?), ('B', 'IDLE', ?)").run(now(), now());
  db.prepare("INSERT OR IGNORE INTO label_sequences (name, next_value) VALUES ('global', 1)").run();
  db.prepare("INSERT OR IGNORE INTO products (id, code, name, target_program) VALUES (1, 'P-100', '模拟产品 100', 100), (2, 'P-200', '模拟产品 200', 200)").run();
  db.prepare("INSERT OR IGNORE INTO operators (id, code, name) VALUES (1, 'OP-01', '模拟操作员 01'), (2, 'OP-02', '模拟操作员 02')").run();
  return db;
}

function addEvent(db, stationId, cycleId, eventType, payload = {}) {
  db.prepare('INSERT INTO station_events (station_id, cycle_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)')
    .run(stationId, cycleId || null, eventType, JSON.stringify(payload), now());
}

module.exports = { STATES, createDb, addEvent, now };
