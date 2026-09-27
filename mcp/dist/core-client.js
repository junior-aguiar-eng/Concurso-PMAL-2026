import { spawn } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { CliEnvelopeSchema } from "./schemas.js";
export const MAX_STDOUT_BYTES = 2 * 1024 * 1024;
export const DEFAULT_TIMEOUT_MS = 30_000;
function configured(value) {
    const trimmed = value?.trim();
    // Campo opcional não preenchido no Claude Desktop pode chegar como "${user_config.x}".
    return trimmed && !trimmed.startsWith("${") ? trimmed : undefined;
}
/**
 * PMAL_PYTHON (interpretador >= 3.11) dispensa o uv: o núcleo não tem dependências externas.
 * Sem ele, usa o uv; PMAL_UV aceita caminho absoluto, pois o Claude Desktop não herda o PATH do terminal.
 */
export function resolveRunner(env = process.env) {
    const python = configured(env.PMAL_PYTHON);
    if (python)
        return { kind: "python", executable: python };
    return { kind: "uv", executable: configured(env.PMAL_UV) ?? "uv" };
}
export function resolveTimeoutMs(env = process.env) {
    const value = Number(env.PMAL_TIMEOUT_MS);
    return Number.isInteger(value) && value > 0 ? value : DEFAULT_TIMEOUT_MS;
}
export function workerCommand(runner, projectRoot, databasePath) {
    const workerArgs = ["-m", "pmal_study.worker", "--project-root", projectRoot];
    if (databasePath)
        workerArgs.push("--database", databasePath);
    return runner.kind === "python"
        ? { args: workerArgs, env: { PYTHONPATH: path.join(projectRoot, "src") } }
        : { args: ["run", "--quiet", "--project", projectRoot, "python", ...workerArgs], env: {} };
}
export function initializeBundledDatabase(projectRoot, dataDirectory = configured(process.env.PMAL_DATA_DIR) ?? path.join(os.homedir(), ".codex", "state", "treinador-pmal-oficial")) {
    const seed = path.join(projectRoot, "corpus", "pmal-study-seed.db");
    if (!existsSync(seed))
        return undefined;
    mkdirSync(dataDirectory, { recursive: true });
    const database = path.join(dataDirectory, "pmal-study.db");
    if (!existsSync(database))
        copyFileSync(seed, database);
    return database;
}
export function parsePythonOutput(stdout) {
    if (Buffer.byteLength(stdout, "utf8") > MAX_STDOUT_BYTES)
        throw new Error("O worker excedeu o limite de stdout de 2 MiB.");
    let payload;
    try {
        payload = JSON.parse(stdout.trim());
    }
    catch {
        throw new Error("O worker violou o contrato JSONL.");
    }
    const parsed = CliEnvelopeSchema.safeParse(payload);
    if (!parsed.success)
        throw new Error("O worker devolveu um envelope fora do contrato JSON.");
    return parsed.data;
}
export class CoreClient {
    spawnWorker;
    defaultTimeoutMs;
    projectRoot;
    databasePath;
    child;
    ready;
    readyResolve;
    readyReject;
    buffer = "";
    pending = new Map();
    sequence = 0;
    queue = Promise.resolve();
    closed = false;
    stderrTail = "";
    runner;
    constructor(projectRoot, spawnWorker = (executable, args, options) => spawn(executable, args, options), databasePath, dataDirectory, defaultTimeoutMs = resolveTimeoutMs(), runner = resolveRunner()) {
        this.spawnWorker = spawnWorker;
        this.defaultTimeoutMs = defaultTimeoutMs;
        this.runner = runner;
        if (!path.isAbsolute(projectRoot))
            throw new Error("projectRoot deve ser absoluto.");
        this.projectRoot = path.resolve(projectRoot);
        this.databasePath = databasePath ?? initializeBundledDatabase(this.projectRoot, dataDirectory);
    }
    start() {
        if (this.ready)
            return this.ready;
        if (this.closed)
            return Promise.reject(new Error("O worker já foi encerrado."));
        const { args, env } = workerCommand(this.runner, this.projectRoot, this.databasePath);
        const executable = this.runner.executable;
        this.ready = new Promise((resolve, reject) => { this.readyResolve = resolve; this.readyReject = reject; });
        this.stderrTail = "";
        const child = this.spawnWorker(executable, args, { cwd: this.projectRoot, shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, ...env, PYTHONUTF8: "1" } });
        this.child = child;
        child.stdout.on("data", (chunk) => this.consume(chunk.toString("utf8")));
        child.stderr?.on("data", (chunk) => { this.stderrTail = (this.stderrTail + chunk.toString("utf8")).slice(-2_000); });
        child.on("error", (error) => {
            if (this.child !== child)
                return;
            this.fail(error.code === "ENOENT"
                ? new Error(`Executável "${executable}" não encontrado. Configure PMAL_PYTHON (Python >= 3.11) ou PMAL_UV com caminho absoluto.`)
                : error);
        });
        child.on("close", () => {
            if (this.child !== child)
                return;
            const detail = this.stderrTail.trim().split(/\r?\n/).slice(-3).join(" | ");
            this.fail(new Error(detail ? `O worker Python foi encerrado. stderr: ${detail}` : "O worker Python foi encerrado."));
        });
        return this.ready;
    }
    consume(chunk) {
        this.buffer += chunk;
        if (Buffer.byteLength(this.buffer, "utf8") > MAX_STDOUT_BYTES) {
            this.child?.kill();
            this.fail(new Error("O worker excedeu o limite de stdout de 2 MiB."));
            return;
        }
        for (;;) {
            const newline = this.buffer.indexOf("\n");
            if (newline < 0)
                return;
            const line = this.buffer.slice(0, newline);
            this.buffer = this.buffer.slice(newline + 1);
            let value;
            try {
                value = JSON.parse(line);
            }
            catch {
                this.child?.kill();
                this.fail(new Error("O worker violou o contrato JSONL."));
                return;
            }
            if (value?.type === "ready") {
                this.readyResolve?.();
                continue;
            }
            const id = String(value?.id ?? "");
            const pending = this.pending.get(id);
            if (!pending)
                continue;
            clearTimeout(pending.timer);
            this.pending.delete(id);
            const parsed = CliEnvelopeSchema.safeParse({ ok: value.ok, ...(value.ok ? { data: value.data } : { error: value.error }) });
            if (!parsed.success)
                pending.reject(new Error("O worker devolveu um envelope fora do contrato JSON."));
            else
                pending.resolve(parsed.data);
        }
    }
    fail(error) {
        this.readyReject?.(error);
        this.ready = undefined;
        this.readyResolve = undefined;
        this.readyReject = undefined;
        this.child = undefined;
        this.buffer = "";
        for (const pending of this.pending.values()) {
            clearTimeout(pending.timer);
            pending.reject(error);
        }
        this.pending.clear();
    }
    async callOnce(command, input) {
        await this.start();
        const child = this.child;
        if (!child)
            throw new Error("O worker Python não iniciou.");
        const id = String(++this.sequence);
        const timeoutMs = command === "refresh-corpus" ? 600_000 : this.defaultTimeoutMs;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { this.pending.delete(id); child.kill(); reject(new Error("O worker Python excedeu o tempo limite.")); }, timeoutMs);
            this.pending.set(id, { resolve, reject, timer });
            child.stdin.write(JSON.stringify({ id, command, payload: input }) + "\n", (error) => { if (error) {
                clearTimeout(timer);
                this.pending.delete(id);
                reject(error);
            } });
        });
    }
    async call(command, input) {
        const execute = () => {
            const run = this.queue.then(() => this.callOnce(command, input));
            this.queue = run.catch(() => undefined);
            return run;
        };
        try {
            return await execute();
        }
        catch (error) {
            const readOnly = ["dashboard", "review-queue", "worker-info"].includes(command);
            const idempotentWrite = ["operation_id", "attempt_id", "exchange_id", "job_id", "request_id"].some((key) => typeof input[key] === "string" && input[key]);
            if (!readOnly && !idempotentWrite)
                throw error;
            this.child?.kill();
            this.fail(error instanceof Error ? error : new Error(String(error)));
            return execute();
        }
    }
    async close() {
        this.closed = true;
        const child = this.child;
        if (!child)
            return;
        child.stdin.end();
        await new Promise((resolve) => { const timer = setTimeout(() => { child.kill(); resolve(); }, 2_000); child.once("close", () => { clearTimeout(timer); resolve(); }); });
    }
}
