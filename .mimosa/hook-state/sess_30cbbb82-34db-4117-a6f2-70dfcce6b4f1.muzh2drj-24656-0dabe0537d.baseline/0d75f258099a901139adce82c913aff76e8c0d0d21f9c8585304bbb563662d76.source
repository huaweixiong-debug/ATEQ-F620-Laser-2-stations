const path = require('node:path');
const { loadLiveConfig } = require('../liveConfig');
const { RealAteqRtu, RealS7Plc } = require('../realDevices');

async function main() {
  const filename = process.argv[2] || process.env.V2_LIVE_CONFIG;
  if (!filename) throw new Error('usage: npm run preflight:live -- <config.json>');
  const config = loadLiveConfig(path.resolve(filename));
  const ateq = new RealAteqRtu(config);
  const plc = new RealS7Plc(config);
  try {
    const report = { mode: 'read-only-preflight', plcM0: await plc.readMByte(0), ateq: {} };
    for (const station of ['A', 'B']) report.ateq[station] = await ateq.readRealtime(station);
    console.log(JSON.stringify(report, null, 2));
  } finally { await Promise.allSettled([ateq.close(), plc.close()]); }
}
main().catch((error) => { console.error(`LIVE_PREFLIGHT_FAILED: ${error.message}`); process.exitCode = 1; });
