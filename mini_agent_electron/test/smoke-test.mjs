#!/usr/bin/env node
/**
 * UI Smoke Test — Runs the Electron app with --smoke-test flag.
 * Catches React runtime errors after UI changes.
 *
 * Usage:  npm run test:smoke
 *         node test/smoke-test.mjs   (must build first)
 */

import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const electronPath = path.join(__dirname, '..', 'node_modules', '.bin', 'electron');

const child = spawn(electronPath, ['.', '--smoke-test'], {
  cwd: path.join(__dirname, '..'),
  stdio: 'inherit',
  env: { ...process.env, ELECTRON_RUN_AS_NODE: undefined },
});

child.on('close', (code) => {
  process.exit(code ?? 1);
});
