import path from "node:path";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import { describe, expect, it } from "vitest";
import { CoreClient, MAX_STDOUT_BYTES, initializeBundledDatabase, parsePythonOutput } from "../src/core-client.js";
import type { ChildProcessWithoutNullStreams } from "node:child_process";

function fakeWorker(onRequest: (text: string, stdout: PassThrough) => void): ChildProcessWithoutNullStreams {
  const process = new EventEmitter() as ChildProcessWithoutNullStreams;
  const stdout = new PassThrough(); const stderr = new PassThrough();
  const stdin = new Writable({ write(chunk, _encoding, callback) { onRequest(chunk.toString(), stdout); callback(); } });
  Object.assign(process, { stdin, stdout, stderr, pid: 1234, kill: () => { queueMicrotask(() => process.emit("close", 1)); return true; } });
  queueMicrotask(() => stdout.write('{"type":"ready","protocol":1,"pid":1234,"schema":10}\n'));
  return process;
}

describe("CoreClient persistente", () => {
  it("reutiliza o mesmo worker Python entre chamadas aquecidas", async () => {
    const temporary = mkdtempSync(path.join(os.tmpdir(), "pmal-worker-"));
    const packagedRoot = path.resolve(import.meta.dirname, "../../runtime");
    const projectRoot = existsSync(path.join(packagedRoot, "pyproject.toml"))
      ? packagedRoot : path.resolve(import.meta.dirname, "../../../..");
    const client = new CoreClient(projectRoot, undefined, path.join(temporary, "state.db"));
    try {
      const first = await client.call("worker-info", {});
      const second = await client.call("worker-info", {});
      expect(first.ok && second.ok && first.data).toMatchObject(second.ok ? second.data as object : {});
    } finally { await client.close(); rmSync(temporary, { recursive: true, force: true }); }
  }, 30_000);

  it("reinicia após queda e só repete automaticamente operação segura", async () => {
    const temporary = mkdtempSync(path.join(os.tmpdir(), "pmal-restart-"));
    const packagedRoot = path.resolve(import.meta.dirname, "../../runtime");
    const projectRoot = existsSync(path.join(packagedRoot, "pyproject.toml"))
      ? packagedRoot : path.resolve(import.meta.dirname, "../../../..");
    const client = new CoreClient(projectRoot, undefined, path.join(temporary, "state.db"));
    try {
      const first = await client.call("worker-info", {});
      if (!first.ok) throw new Error("worker-info falhou");
      const firstPid = (first.data as { pid: number }).pid;
      process.kill(firstPid);
      const restarted = await client.call("worker-info", {});
      expect(restarted.ok && (restarted.data as { pid: number }).pid).not.toBe(firstPid);
    } finally { await client.close(); rmSync(temporary, { recursive: true, force: true }); }
  }, 30_000);

  it("rejeita linha truncada ou acima de 2 MiB", () => {
    expect(() => parsePythonOutput('{"ok":true')).toThrow(/JSONL/);
    expect(() => parsePythonOutput("x".repeat(MAX_STDOUT_BYTES + 1))).toThrow(/2 MiB/);
  });

  it("rejeita resposta truncada sem repetir mutação ambígua", async () => {
    let starts = 0;
    const client = new CoreClient(path.resolve("C:/workspace/pmal"), () => {
      starts += 1; return fakeWorker((_request, stdout) => stdout.write('{"id":"1","ok":true\n'));
    });
    await expect(client.call("start-session", {})).rejects.toThrow(/JSONL/);
    expect(starts).toBe(1);
  });

  it("interrompe worker que excede o timeout", async () => {
    const client = new CoreClient(path.resolve("C:/workspace/pmal"), () => fakeWorker(() => undefined), undefined, undefined, 20);
    await expect(client.call("start-session", {})).rejects.toThrow(/tempo limite/);
  });

  it("copia o índice empacotado uma única vez", () => {
    const temporary = mkdtempSync(path.join(os.tmpdir(), "pmal-state-"));
    try {
      const root = path.join(temporary, "runtime"); const data = path.join(temporary, "state");
      mkdirSync(path.join(root, "corpus"), { recursive: true });
      writeFileSync(path.join(root, "corpus", "pmal-study-seed.db"), "seed-v1");
      initializeBundledDatabase(root, data);
      writeFileSync(path.join(data, "pmal-study.db"), "user-state");
      initializeBundledDatabase(root, data);
      expect(readFileSync(path.join(data, "pmal-study.db"), "utf8")).toBe("user-state");
    } finally { rmSync(temporary, { recursive: true, force: true }); }
  });
});
