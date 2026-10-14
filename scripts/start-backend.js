import { spawn, spawnSync } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const reqPath = fs.existsSync(path.join(rootDir, 'backend', 'requirements.txt'))
  ? path.join(rootDir, 'backend', 'requirements.txt')
  : path.join(rootDir, 'requirements.txt');

const isWindows = process.platform === 'win32';

/**
 * Safely get and harmonize PATH across Windows and Unix platforms.
 */
function getSanitizedEnv(pythonExe) {
  const currentPath = process.env.PATH || process.env.Path || '';
  const pathParts = currentPath.split(path.delimiter).filter(Boolean);

  if (pythonExe && fs.existsSync(pythonExe)) {
    const pyDir = path.dirname(pythonExe);
    const pyParent = path.dirname(pyDir);
    const pyScripts = path.join(pyDir, 'Scripts');
    const pyBin = path.join(pyDir, 'bin');

    const toPrepend = [pyDir, pyScripts, pyBin, pyParent].filter((p) => fs.existsSync(p));
    for (const p of toPrepend.reverse()) {
      if (!pathParts.includes(p)) {
        pathParts.unshift(p);
      }
    }
  }

  // On Windows, also ensure common System32 and Git/FFmpeg directories exist in PATH
  if (isWindows) {
    const sys32 = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32');
    if (fs.existsSync(sys32) && !pathParts.includes(sys32)) {
      pathParts.push(sys32);
    }
  }

  const unifiedPath = pathParts.join(path.delimiter);
  return {
    ...process.env,
    PATH: unifiedPath,
    Path: unifiedPath,
    PYTHONUNBUFFERED: '1',
    PYTHONPATH: rootDir
  };
}

/**
 * Reclaim port 8000 from a stale/zombie backend before launching.
 * If an old uvicorn is still bound to :8000 it would keep serving OUTDATED
 * code, so new requests can silently hit the wrong process (e.g. render
 * failing with "Failed to download video clip segment" even after a fix).
 * We kill the holder so only ONE backend, running current code, ever listens.
 */
function reclaimPort8000() {
  try {
    const find = isWindows
      ? spawnSync('netstat', ['-ano'], { encoding: 'utf8' })
      : spawnSync('sh', ['-c', "ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null"], { encoding: 'utf8' });
    const out = (find.stdout || '') + (find.stderr || '');
    const pids = new Set();
    const lines = out.split(RegExp('[' + String.fromCharCode(10, 13) + ']'));
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line.includes(':8000') || !/LISTEN/i.test(line)) continue;
      if (isWindows) {
        const m = line.trim().split(/\s+/);
        const pid = m[m.length - 1];
        if (/^\d+$/.test(pid)) pids.add(pid);
      } else {
        const m = line.match(/pid=(\d+)/);
        if (m) pids.add(m[1]);
      }
    }
    if (pids.size === 0) return;
    console.log('\x1b[33m%s\x1b[0m', `♻️  Port 8000 is busy (stale backend: ${[...pids].join(', ')}). Reclaiming...`);
    for (const pid of pids) {
      if (isWindows) {
        spawnSync('taskkill', ['/F', '/T', '/PID', pid], { encoding: 'utf8' });
      } else {
        try { process.kill(Number(pid), 'SIGKILL'); } catch (_) { /* ignore */ }
      }
    }
    // Give the OS a moment to release the socket.
    const until = Date.now() + 3000;
    while (Date.now() < until) { /* brief spin to let the port free up */ }
  } catch (_) {
    // Best-effort: never block startup on the preflight itself.
  }
}

function findPythonCandidates() {
  const candidates = [];

  // 1. Local project virtual environments (highest priority)
  const venvNames = ['venv', '.venv', 'env', '.env'];
  for (const v of venvNames) {
    const winPath = path.join(rootDir, v, 'Scripts', 'python.exe');
    const unixPath = path.join(rootDir, v, 'bin', 'python');
    if (isWindows && fs.existsSync(winPath)) {
      candidates.push({ exe: winPath, args: [], name: `${v}/Scripts/python.exe` });
    } else if (!isWindows && fs.existsSync(unixPath)) {
      candidates.push({ exe: unixPath, args: [], name: `${v}/bin/python` });
    }
  }

  // 2. Environment variable override
  if (process.env.PYTHON_PATH && fs.existsSync(process.env.PYTHON_PATH)) {
    candidates.push({ exe: process.env.PYTHON_PATH, args: [], name: 'PYTHON_PATH env' });
  }

  // 3. System commands
  candidates.push({ exe: 'python', args: [], name: 'System python' });
  if (isWindows) {
    candidates.push({ exe: 'py', args: ['-3'], name: 'Windows Python Launcher (py -3)' });
  }
  candidates.push({ exe: 'python3', args: [], name: 'System python3' });

  // 4. Windows standard installation locations fallback
  if (isWindows) {
    const localAppData = process.env.LOCALAPPDATA;
    if (localAppData) {
      const progPath = path.join(localAppData, 'Programs', 'Python');
      if (fs.existsSync(progPath)) {
        try {
          const dirs = fs.readdirSync(progPath);
          for (const d of dirs) {
            const exe = path.join(progPath, d, 'python.exe');
            if (fs.existsSync(exe)) {
              candidates.push({ exe, args: [], name: `LocalAppData ${d}` });
            }
          }
        } catch {}
      }
    }

    const progFiles = [
      process.env['ProgramFiles'],
      process.env['ProgramFiles(x86)'],
      'C:\\Program Files',
      'C:\\Program Files (x86)'
    ].filter(Boolean);

    for (const pf of progFiles) {
      const pyBase = path.join(pf, 'Python');
      if (fs.existsSync(pyBase)) {
        try {
          const dirs = fs.readdirSync(pyBase);
          for (const d of dirs) {
            const exe = path.join(pyBase, d, 'python.exe');
            if (fs.existsSync(exe)) {
              candidates.push({ exe, args: [], name: `ProgramFiles ${d}` });
            }
          }
        } catch {}
      }
    }

    const rootDrives = [
      'C:\\Python314\\python.exe',
      'C:\\Python313\\python.exe',
      'C:\\Python312\\python.exe',
      'C:\\Python311\\python.exe',
      'C:\\Python310\\python.exe'
    ];
    for (const p of rootDrives) {
      if (fs.existsSync(p)) {
        candidates.push({ exe: p, args: [], name: p });
      }
    }
  }

  return candidates;
}

function testPython(candidate) {
  try {
    const testEnv = getSanitizedEnv(candidate.exe);
    // Thorough test: verify uvicorn, fastapi, and full backend config import
    const res = spawnSync(
      candidate.exe,
      [...candidate.args, '-c', 'import sys, uvicorn, fastapi; import backend.config; print("OK")'],
      {
        cwd: rootDir,
        timeout: 10000,
        encoding: 'utf-8',
        shell: false,
        env: testEnv
      }
    );

    if (res.status === 0 && res.stdout && res.stdout.includes('OK')) {
      return { works: true, hasDependencies: true };
    }

    // Basic test: check if Python runs at all
    const basicRes = spawnSync(
      candidate.exe,
      [...candidate.args, '--version'],
      {
        cwd: rootDir,
        timeout: 5000,
        encoding: 'utf-8',
        shell: false,
        env: testEnv
      }
    );

    const versionOutput = (basicRes.stdout || '') + (basicRes.stderr || '');
    if (basicRes.status === 0 || versionOutput.toLowerCase().includes('python')) {
      return { works: true, hasDependencies: false };
    }
  } catch (err) {
    // Ignore execution errors
  }
  return { works: false, hasDependencies: false };
}

function installDependencies(candidate) {
  console.log('\x1b[33m%s\x1b[0m', `📦 Installing Python dependencies from ${path.relative(rootDir, reqPath)}...`);
  console.log('\x1b[90m%s\x1b[0m', `   Running: ${candidate.exe} -m pip install -r "${reqPath}"`);

  const pipEnv = getSanitizedEnv(candidate.exe);
  const pipRes = spawnSync(
    candidate.exe,
    [...candidate.args, '-m', 'pip', 'install', '-r', reqPath],
    {
      cwd: rootDir,
      stdio: 'inherit',
      shell: false,
      env: pipEnv
    }
  );

  return pipRes.status === 0;
}

async function start() {
  console.log('\x1b[36m%s\x1b[0m', '⚡ [Cheat Clip Pro] Initializing Fast & Resilient Python Backend Server...');

  // Free :8000 from any stale backend first, so the server we launch is the
  // ONLY one serving requests (prevents silently hitting outdated code).
  reclaimPort8000();

  const candidates = findPythonCandidates();
  let selectedCandidate = null;

  for (const c of candidates) {
    const test = testPython(c);
    if (test.works && test.hasDependencies) {
      selectedCandidate = c;
      console.log('\x1b[32m%s\x1b[0m', `✓ Using Python: ${c.name} (${c.exe})`);
      break;
    }
  }

  // If no Python candidate has dependencies ready, find the best working Python and install them
  if (!selectedCandidate) {
    for (const c of candidates) {
      const test = testPython(c);
      if (test.works) {
        console.log('\x1b[33m%s\x1b[0m', `⚠️ Python found (${c.name}), but required backend packages are missing or incomplete.`);
        const installed = installDependencies(c);
        if (installed) {
          selectedCandidate = c;
          console.log('\x1b[32m%s\x1b[0m', `✓ Dependencies installed successfully on ${c.name}`);
        } else {
          console.warn('\x1b[31m%s\x1b[0m', `⚠️ Failed to auto-install dependencies on ${c.name}.`);
        }
        break;
      }
    }
  }

  if (!selectedCandidate) {
    console.error('\x1b[31m%s\x1b[0m', '❌ No working Python installation found!');
    console.error('\x1b[33m%s\x1b[0m', '💡 Please install Python 3.10+ from https://www.python.org/downloads/');
    console.error('\x1b[33m%s\x1b[0m', '   Make sure to check "Add Python to PATH" during installation.');
    process.exit(1);
  }

  const pythonExe = selectedCandidate.exe;
  const baseArgs = selectedCandidate.args;
  const backendEnv = getSanitizedEnv(pythonExe);

  const uvicornArgs = [
    ...baseArgs,
    '-m',
    'uvicorn',
    'backend.main:app',
    '--host',
    '0.0.0.0',
    '--port',
    '8000',
    '--reload',
    '--reload-dir',
    path.join(rootDir, 'backend')
  ];

  console.log('\x1b[34m%s\x1b[0m', `🚀 Launching FastAPI server on http://127.0.0.1:8000 ...`);

  const backendProc = spawn(pythonExe, uvicornArgs, {
    cwd: rootDir,
    shell: false,
    stdio: 'inherit',
    env: backendEnv
  });

  backendProc.on('error', (err) => {
    console.error('\x1b[31m%s\x1b[0m', `❌ Failed to start Python backend: ${err.message}`);
    console.error('\x1b[33m%s\x1b[0m', `💡 Ensure Python is installed and run: pip install -r backend/requirements.txt`);
  });

  backendProc.on('exit', (code, signal) => {
    if (code !== 0 && code !== null) {
      console.error('\x1b[31m%s\x1b[0m', `⚠️ Backend server process exited with code ${code}.`);
      console.error('\x1b[33m%s\x1b[0m', `💡 Common reasons:`);
      console.error('\x1b[33m%s\x1b[0m', `   1. Port 8000 is already in use by another app or zombie process.`);
      console.error('\x1b[33m%s\x1b[0m', `   2. Missing dependencies. Run: pip install -r backend/requirements.txt`);
      console.error('\x1b[33m%s\x1b[0m', `   3. If space in username path on Windows, ensure venv was created properly.`);
    }
  });

  process.on('SIGINT', () => {
    backendProc.kill('SIGINT');
    process.exit(0);
  });
  process.on('SIGTERM', () => {
    backendProc.kill('SIGTERM');
    process.exit(0);
  });
}

start();

