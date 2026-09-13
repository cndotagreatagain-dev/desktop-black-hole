// Native DSH plugin. Only aggregate metadata leaves this process.
import { mkdir, writeFile, rename, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { randomUUID } from 'node:crypto';

export const name = 'black-hole-status';
export const inject = ['agents'];

export function apply(ctx) {
  // Mount at the profile root, never inside a single agent's scoped context.
  const directory = join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'),
                         'DesktopBlackHole', 'status', 'dsh-v1');
  return attach(ctx, directory);
}

export function attach(ctx, directory) {
  const started = Math.round(Date.now() - process.uptime() * 1000);
  const file = join(directory, `${process.pid}-${randomUUID()}.json`);
  const temporary = file + '.tmp';
  let closed = false, active = false, pending = false, warned = false;
  let drain = Promise.resolve();
  const flush = async () => {
    try {
      while (pending && !closed) {
        pending = false;
        const agents = ctx.agents.list();
        const value = { version: 1, source: 'dsh', pid: process.pid,
          started_ms: started, updated_ms: Date.now(), agents: agents.length,
          running: agents.filter(a => a.status === 'running').length,
          unknown: agents.filter(a => a.status !== 'running' && a.status !== 'idle').length };
        await mkdir(directory, { recursive: true });
        await writeFile(temporary, JSON.stringify(value), { encoding: 'utf8', mode: 0o600 });
        if (!closed) {
          for (let attempt=0; ; attempt++) {
            try { await rename(temporary, file); break; }
            catch (error) {
              if (attempt >= 4 || !['EPERM','EACCES','EBUSY'].includes(error.code)) throw error;
              await new Promise(r => setTimeout(r, 15));
            }
          }
        }
      }
    } catch (error) {
      if (!warned) ctx.logger?.warn?.('Black-hole status file unavailable (' +
        (error.code || 'unknown') + '); agent execution is unaffected.');
      warned = true;
    } finally {
      active = false;
    }
  };
  const request = () => {
    if (closed) return;
    pending = true;
    if (!active) {
      active = true;
      // Coalesce same-tick events; never hold up DSH's event dispatch.
      drain = Promise.resolve().then(flush);
    }
  };
  ctx.on('agent/created', request);
  ctx.on('agent/status', request);
  ctx.on('agent/disposed', request);
  ctx.effect(() => {
    const timer = setInterval(request, 10000);
    timer.unref?.();
    request();
    return async () => {
      closed = true;
      clearInterval(timer);
      await drain;
      await Promise.all([file, temporary].map(p => unlink(p).catch(() => {})));
    };
  });
}
