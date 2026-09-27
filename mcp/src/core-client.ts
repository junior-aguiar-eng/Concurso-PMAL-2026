import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { CliEnvelopeSchema, type CliEnvelope } from "./schemas.js";

export const MAX_STDOUT_BYTES = 2 * 1024 * 1024;
export interface CoreCaller { call(command: string, input: Record<string, unknown>): Promise<CliEnvelope>; close?(): Promise<void>; }
export type WorkerSpawner = (executable: string, args: string[], options: Parameters<typeof spawn>[2]) => ChildProcessWithoutNullStreams;

export function initializeBundledDatabase(projectRoot: string, dataDirectory = process.env.PMAL_DATA_DIR ?? path.join(os.homedir(), ".codex", "state", "treinador-pmal-oficial")): string | undefined {
  const seed = path.join(projectRoot, "corpus", "pmal-study-seed.db");
  if (!existsSync(seed)) return undefined;
  mkdirSync(dataDirectory, { recursive: true });
  const database = path.join(dataDirectory, "pmal-study.db");
  if (!existsSync(database)) copyFileSync(seed, database);
  return database;
}

export function parsePythonOutput(stdout: string): CliEnvelope {
  if (Buffer.byteLength(stdout, "utf8") > MAX_STDOUT_BYTES) throw new Error("O worker excedeu o limite de stdout de 2 MiB.");
  let payload: unknown;
  try { payload = JSON.parse(stdout.trim()); } catch { throw new Error("O worker violou o contrato JSONL."); }
  const parsed = CliEnvelopeSchema.safeParse(payload);
  if (!parsed.success) throw new Error("O worker devolveu um envelope fora do contrato JSON.");
  return parsed.data;
}

type Pending = { resolve(value: CliEnvelope): void; reject(error: Error): void; timer: NodeJS.Timeout };

export class CoreClient implements CoreCaller {
  readonly projectRoot: string;
  private readonly databasePath?: string;
  private child?: ChildProcessWithoutNullStreams;
  private ready?: Promise<void>;
  private readyResolve?: () => void;
  private readyReject?: (error: Error) => void;
  private buffer = "";
  private pending = new Map<string, Pending>();
  private sequence = 0;
  private queue: Promise<unknown> = Promise.resolve();
  private closed = false;

  constructor(projectRoot: string, private readonly spawnWorker: WorkerSpawner = (executable, args, options) => spawn(executable, args, options) as ChildProcessWithoutNullStreams, databasePath?: string, dataDirectory?: string, private readonly defaultTimeoutMs = 30_000) {
    if (!path.isAbsolute(projectRoot)) throw new Error("projectRoot deve ser absoluto.");
    this.projectRoot = path.resolve(projectRoot);
    this.databasePath = databasePath ?? initializeBundledDatabase(this.projectRoot, dataDirectory);
  }

  private start(): Promise<void> {
    if (this.ready) return this.ready;
    if (this.closed) return Promise.reject(new Error("O worker já foi encerrado."));
    const args = ["run", "--quiet", "--project", this.projectRoot, "python", "-m", "pmal_study.worker", "--project-root", this.projectRoot];
    if (this.databasePath) args.push("--database", this.databasePath);
    this.ready = new Promise<void>((resolve, reject) => { this.readyResolve = resolve; this.readyReject = reject; });
    const child = this.spawnWorker("uv", args, { cwd: this.projectRoot, shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONUTF8: "1" } });
    this.child = child;
    child.stdout.on("data", (chunk: Buffer) => this.consume(chunk.toString("utf8")));
    child.on("error", (error) => { if (this.child === child) this.fail(error); });
    child.on("close", () => { if (this.child === child) this.fail(new Error("O worker Python foi encerrado.")); });
    return this.ready;
  }

  private consume(chunk: string): void {
    this.buffer += chunk;
    if (Buffer.byteLength(this.buffer, "utf8") > MAX_STDOUT_BYTES) { this.child?.kill(); this.fail(new Error("O worker excedeu o limite de stdout de 2 MiB.")); return; }
    for (;;) {
      const newline = this.buffer.indexOf("\n");
      if (newline < 0) return;
      const line = this.buffer.slice(0, newline); this.buffer = this.buffer.slice(newline + 1);
      let value: any;
      try { value = JSON.parse(line); } catch { this.child?.kill(); this.fail(new Error("O worker violou o contrato JSONL.")); return; }
      if (value?.type === "ready") { this.readyResolve?.(); continue; }
      const id = String(value?.id ?? ""); const pending = this.pending.get(id);
      if (!pending) continue;
      clearTimeout(pending.timer); this.pending.delete(id);
      const parsed = CliEnvelopeSchema.safeParse({ ok: value.ok, ...(value.ok ? { data: value.data } : { error: value.error }) });
      if (!parsed.success) pending.reject(new Error("O worker devolveu um envelope fora do contrato JSON.")); else pending.resolve(parsed.data);
    }
  }

  private fail(error: Error): void {
    this.readyReject?.(error); this.ready = undefined; this.readyResolve = undefined; this.readyReject = undefined; this.child = undefined; this.buffer = "";
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
  }

  private async callOnce(command: string, input: Record<string, unknown>): Promise<CliEnvelope> {
    await this.start(); const child = this.child;
    if (!child) throw new Error("O worker Python não iniciou.");
    const id = String(++this.sequence); const timeoutMs = command === "refresh-corpus" ? 600_000 : this.defaultTimeoutMs;
    return new Promise<CliEnvelope>((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); child.kill(); reject(new Error("O worker Python excedeu o tempo limite.")); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      child.stdin.write(JSON.stringify({ id, command, payload: input }) + "\n", (error) => { if (error) { clearTimeout(timer); this.pending.delete(id); reject(error); } });
    });
  }

  async call(command: string, input: Record<string, unknown>): Promise<CliEnvelope> {
    const execute = () => {
      const run = this.queue.then(() => this.callOnce(command, input));
      this.queue = run.catch(() => undefined);
      return run;
    };
    try { return await execute(); }
    catch (error) {
      const readOnly = ["dashboard", "review-queue", "worker-info"].includes(command);
      const idempotentWrite = ["operation_id", "attempt_id", "exchange_id", "job_id", "request_id"].some((key) => typeof input[key] === "string" && input[key]);
      if (!readOnly && !idempotentWrite) throw error;
      this.child?.kill(); this.fail(error instanceof Error ? error : new Error(String(error)));
      return execute();
    }
  }

  async close(): Promise<void> {
    this.closed = true; const child = this.child; if (!child) return;
    child.stdin.end();
    await new Promise<void>((resolve) => { const timer = setTimeout(() => { child.kill(); resolve(); }, 2_000); child.once("close", () => { clearTimeout(timer); resolve(); }); });
  }
}
