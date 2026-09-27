import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { CoreClient, DEFAULT_TIMEOUT_MS, executeProcess, MAX_STDOUT_BYTES, parsePythonOutput, resolveRunner, resolveTimeoutMs, } from "../core-client.js";
describe("CoreClient", () => {
    it("invoca a CLI por argumentos, com raiz absoluta e sem shell", async () => {
        const executor = vi.fn().mockResolvedValue({
            exitCode: 0,
            stdout: '{"ok":true,"data":{"attempts_total":0}}\n',
            stderr: "",
        });
        const root = path.resolve("C:/workspace/pmal");
        const client = new CoreClient(root, executor, undefined, { runner: resolveRunner({}) });
        await client.call("dashboard", {});
        expect(executor).toHaveBeenCalledWith("uv", [
            "run",
            "--quiet",
            "--project",
            root,
            "python",
            "-m",
            "pmal_study.cli",
            "dashboard",
            "--project-root",
            root,
            "--input-json",
            "{}",
        ], { cwd: root, timeoutMs: DEFAULT_TIMEOUT_MS, maxStdoutBytes: MAX_STDOUT_BYTES });
    });
    it("dispensa o uv quando PMAL_PYTHON é informado", async () => {
        const executor = vi.fn().mockResolvedValue({
            exitCode: 0,
            stdout: '{"ok":true,"data":{}}\n',
            stderr: "",
        });
        const root = path.resolve("C:/workspace/pmal");
        const database = path.resolve("C:/dados/pmal-study.db");
        const client = new CoreClient(root, executor, database, {
            runner: resolveRunner({ PMAL_PYTHON: "/usr/bin/python3" }),
            timeoutMs: 5_000,
        });
        await client.call("dashboard", {});
        expect(executor).toHaveBeenCalledWith("/usr/bin/python3", [
            "-m",
            "pmal_study.cli",
            "dashboard",
            "--project-root",
            root,
            "--input-json",
            "{}",
            "--database",
            database,
        ], {
            cwd: root,
            timeoutMs: 5_000,
            maxStdoutBytes: MAX_STDOUT_BYTES,
            env: { PYTHONPATH: path.join(root, "src") },
        });
    });
    it("resolve executor e timeout pelo ambiente", () => {
        expect(resolveRunner({ PMAL_UV: " /opt/uv " })).toEqual({ kind: "uv", executable: "/opt/uv" });
        expect(resolveRunner({ PMAL_PYTHON: "", PMAL_UV: "" })).toEqual({ kind: "uv", executable: "uv" });
        expect(resolveRunner({ PMAL_PYTHON: "${user_config.python_path}" })).toEqual({ kind: "uv", executable: "uv" });
        expect(resolveTimeoutMs({ PMAL_TIMEOUT_MS: "45000" })).toBe(45_000);
        expect(resolveTimeoutMs({ PMAL_TIMEOUT_MS: "abc" })).toBe(DEFAULT_TIMEOUT_MS);
    });
    it("explica executável ausente", async () => {
        await expect(executeProcess("pmal-inexistente-xyz", [], {
            cwd: process.cwd(),
            timeoutMs: 5_000,
            maxStdoutBytes: MAX_STDOUT_BYTES,
        })).rejects.toThrow(/PMAL_PYTHON/);
    });
    it("anexa o stderr quando a CLI não devolve JSON", async () => {
        const executor = vi.fn().mockResolvedValue({
            exitCode: 1,
            stdout: "",
            stderr: "Traceback\nModuleNotFoundError: No module named 'pmal_study'\n",
        });
        const client = new CoreClient(path.resolve("/pmal"), executor, undefined, { runner: resolveRunner({}) });
        await expect(client.call("dashboard", {})).rejects.toThrow(/No module named/);
    });
    it("rejeita stdout que não seja um único objeto JSON", () => {
        expect(() => parsePythonOutput("log\n{\"ok\":true,\"data\":{}}\n"))
            .toThrow(/contrato JSON/);
    });
    it("rejeita saída acima de 2 MiB", () => {
        expect(() => parsePythonOutput("x".repeat(MAX_STDOUT_BYTES + 1)))
            .toThrow(/2 MiB/);
    });
});
