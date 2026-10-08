const SIMULATED_ONLY = true;

class FakeAteq {
  constructor() {
    this.writeHistory = [];
    this.stations = new Map([
      ['A', { program: 100, active: false, readCount: 0, readBackOverride: null }],
      ['B', { program: 100, active: false, readCount: 0, readBackOverride: null }]
    ]);
  }

  _station(stationId) {
    const station = this.stations.get(stationId);
    if (!station) throw new Error(`unknown simulated station ${stationId}`);
    return station;
  }

  readProgram(stationId) {
    const station = this._station(stationId);
    station.readCount += 1;
    return station.readCount > 1 && station.readBackOverride !== null ? station.readBackOverride : station.program;
  }

  setReadBackOverride(stationId, program) {
    this._station(stationId).readBackOverride = program;
  }

  writeProgram(stationId, program) {
    const station = this._station(stationId);
    if (station.active) throw new Error('ATEQ program write refused while tester is active');
    this.writeHistory.push({ stationId, program, at: new Date().toISOString() });
    station.program = program;
  }

  isActive(stationId) {
    return this._station(stationId).active;
  }

  setActive(stationId, active) {
    this._station(stationId).active = Boolean(active);
  }
}

class FakeBytePlc {
  constructor() {
    this.byte = 0;
    this.writeHistory = [];
  }

  _bit(bitAddress) {
    if (bitAddress === 'M0.0') return 0;
    if (bitAddress === 'M0.1') return 1;
    throw new Error(`unsupported simulated PLC bit ${bitAddress}`);
  }

  readByte() {
    return this.byte;
  }

  pulse(bitAddress) {
    const bit = this._bit(bitAddress);
    const before = this.byte;
    const after = before | (1 << bit);
    this.byte = after;
    this.writeHistory.push({ bitAddress, before, after, at: new Date().toISOString() });
    return after;
  }

  autoClear(bitAddress) {
    const bit = this._bit(bitAddress);
    this.byte &= ~(1 << bit);
    return this.byte;
  }
}

class FakePrinter {
  constructor() {
    this.printed = [];
  }

  print(label) {
    this.printed.push({ ...label, printedAt: new Date().toISOString() });
    return true;
  }
}

function createSimulatedDevices() {
  if (!SIMULATED_ONLY) throw new Error('hardware mode is forbidden in V2');
  return { ateq: new FakeAteq(), plc: new FakeBytePlc(), printer: new FakePrinter() };
}

module.exports = { SIMULATED_ONLY, FakeAteq, FakeBytePlc, FakePrinter, createSimulatedDevices };
