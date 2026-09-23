import { spawn } from "node:child_process";
import path from "node:path";
import { CliEnvelopeSchema } from "./schemas.js";
export const MAX_STDOUT_BYTES = 2 * 1024 * 1024;
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
        env: { ...process.env, PYTHONUTF8: "1" },
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
    const timer = setTimeout(() => finishError(new Error("A CLI Python excedeu 15 segundos.")), options.timeoutMs);
    child.stdout.on("data", (chunk) => {
        stdoutBytes += chunk.length;
        if (stdoutBytes > options.maxStdoutBytes) {
            finishError(new Error("A CLI excedeu o limite de stdout de 2 MiB."));
            return;
        }
        stdout.push(chunk);
    });
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", finishError);
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
    projectRoot;
    constructor(projectRoot, executor = executeProcess) {
        this.executor = executor;
        if (!path.isAbsolute(projectRoot)) {
            throw new Error("projectRoot deve ser absoluto.");
        }
        this.projectRoot = path.resolve(projectRoot);
    }
    async call(command, input) {
        const args = [
            "run",
            "--project",
            this.projectRoot,
            "python",
            "-m",
            "pmal_study.cli",
            command,
            "--input-json",
            JSON.stringify(input),
        ];
        const output = await this.executor("uv", args, {
            cwd: this.projectRoot,
            timeoutMs: 15_000,
            maxStdoutBytes: MAX_STDOUT_BYTES,
        });
        const envelope = parsePythonOutput(output.stdout);
        if (output.exitCode !== 0 && envelope.ok) {
            throw new Error("A CLI encerrou com falha apesar de declarar sucesso.");
        }
        return envelope;
    }
}
