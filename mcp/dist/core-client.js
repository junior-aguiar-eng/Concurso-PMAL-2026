import { spawn } from "node:child_process";
import path from "node:path";
import { CliEnvelopeSchema } from "./schemas.js";
export const MAX_STDOUT_BYTES = 2 * 1024 * 1024;
export const DEFAULT_TIMEOUT_MS = 30_000;
/**
 * Resolve como a CLI Python será executada.
 * PMAL_PYTHON (interpretador >= 3.12) dispensa o uv: o núcleo não tem dependências externas.
 * Sem ele, usa o uv (PMAL_UV permite informar o caminho absoluto, útil no Claude Desktop,
 * que não herda o PATH do terminal).
 */
function configured(value) {
    const trimmed = value?.trim();
    // Campo opcional não preenchido no Claude Desktop pode chegar como "${user_config.x}".
    return trimmed && !trimmed.startsWith("${") ? trimmed : undefined;
}
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
export function parsePythonOutput(stdout) {
    if (Buffer.byteLength(stdout, "utf8") > MAX_STDOUT_BYTES) {
        throw new Error("A CLI excedeu o limite de stdout de 2 MiB.");
    }
    const trimmed = stdout.trim();
    let payload;
    try {
        payload = JSON.parse(trimmed);
    }
    catch {
        throw new Error("A CLI violou o contrato JSON de linha única.");
    }
    const parsed = CliEnvelopeSchema.safeParse(payload);
    if (!parsed.success) {
        throw new Error("A CLI devolveu um envelope fora do contrato JSON.");
    }
    return parsed.data;
}
export const executeProcess = (executable, args, options) => new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
        cwd: options.cwd,
        shell: false,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, ...options.env, PYTHONUTF8: "1" },
    });
    const stdout = [];
    const stderr = [];
    let stdoutBytes = 0;
    let settled = false;
    const finishError = (error) => {
        if (!settled) {
            settled = true;
            clearTimeout(timer);
            child.kill();
            reject(error);
        }
    };
    const timer = setTimeout(() => finishError(new Error(`A CLI Python excedeu ${Math.round(options.timeoutMs / 1000)} segundos.`)), options.timeoutMs);
    child.stdout.on("data", (chunk) => {
        stdoutBytes += chunk.length;
        if (stdoutBytes > options.maxStdoutBytes) {
            finishError(new Error("A CLI excedeu o limite de stdout de 2 MiB."));
            return;
        }
        stdout.push(chunk);
    });
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", (error) => finishError(error.code === "ENOENT"
        ? new Error(`Executável "${executable}" não encontrado. Configure PMAL_PYTHON (Python >= 3.12) ou PMAL_UV com caminho absoluto.`)
        : error));
    child.on("close", (code) => {
        if (settled)
            return;
        settled = true;
        clearTimeout(timer);
        resolve({
            exitCode: code ?? -1,
            stdout: Buffer.concat(stdout).toString("utf8"),
            stderr: Buffer.concat(stderr).toString("utf8"),
        });
    });
});
export class CoreClient {
    executor;
    databasePath;
    projectRoot;
    runner;
    timeoutMs;
    constructor(projectRoot, executor = executeProcess, databasePath, options = {}) {
        this.executor = executor;
        this.databasePath = databasePath;
        this.runner = options.runner ?? resolveRunner();
        this.timeoutMs = options.timeoutMs ?? resolveTimeoutMs();
        if (!path.isAbsolute(projectRoot)) {
            throw new Error("projectRoot deve ser absoluto.");
        }
        this.projectRoot = path.resolve(projectRoot);
    }
    async call(command, input) {
        const cliArgs = [
            "-m",
            "pmal_study.cli",
            command,
            "--project-root",
            this.projectRoot,
            "--input-json",
            JSON.stringify(input),
        ];
        if (this.databasePath)
            cliArgs.push("--database", this.databasePath);
        const args = this.runner.kind === "python"
            ? cliArgs
            : ["run", "--quiet", "--project", this.projectRoot, "python", ...cliArgs];
        const env = this.runner.kind === "python"
            ? { PYTHONPATH: path.join(this.projectRoot, "src") }
            : undefined;
        const output = await this.executor(this.runner.executable, args, {
            cwd: this.projectRoot,
            timeoutMs: this.timeoutMs,
            maxStdoutBytes: MAX_STDOUT_BYTES,
            ...(env ? { env } : {}),
        });
        let envelope;
        try {
            envelope = parsePythonOutput(output.stdout);
        }
        catch (error) {
            const detail = output.stderr.trim().split(/\r?\n/).slice(-3).join(" | ");
            throw detail ? new Error(`${error.message} stderr: ${detail}`) : error;
        }
        if (output.exitCode !== 0 && envelope.ok) {
            throw new Error("A CLI encerrou com falha apesar de declarar sucesso.");
        }
        return envelope;
    }
}
