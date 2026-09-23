import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { CoreClient, MAX_STDOUT_BYTES, parsePythonOutput, } from "../src/core-client.js";
describe("CoreClient", () => {
    it("invoca a CLI por argumentos, com raiz absoluta e sem shell", async () => {
        const executor = vi.fn().mockResolvedValue({
            exitCode: 0,
            stdout: '{"ok":true,"data":{"attempts_total":0}}\n',
            stderr: "",
        });
        const root = path.resolve("C:/workspace/pmal");
        const client = new CoreClient(root, executor);
        await client.call("dashboard", {});
        expect(executor).toHaveBeenCalledWith("uv", [
            "run",
            "--project",
            root,
            "python",
            "-m",
            "pmal_study.cli",
            "dashboard",
            "--input-json",
            "{}",
        ], { cwd: root, timeoutMs: 15_000, maxStdoutBytes: MAX_STDOUT_BYTES });
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
