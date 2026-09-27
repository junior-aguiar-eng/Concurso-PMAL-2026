import { EventEmitter } from "node:events";
import path from "node:path";
import { PassThrough, Writable } from "node:stream";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it } from "vitest";

import { CoreClient, DEFAULT_TIMEOUT_MS, resolveRunner, resolveTimeoutMs, workerCommand } from "../src/core-client.js";
import { BUNDLED_PROJECT_ROOT, createPmalServer, parseDatabasePath, parseProjectRoot } from "../src/index.js";
import { createToolCatalog } from "../src/tools.js";

describe("execução do worker no Claude Desktop", () => {
  it("resolve Python direto, uv com caminho e placeholders vazios", () => {
    expect(resolveRunner({ PMAL_PYTHON: " C:/Python312/python.exe " })).toEqual({ kind: "python", executable: "C:/Python312/python.exe" });
    expect(resolveRunner({ PMAL_UV: "/opt/uv" })).toEqual({ kind: "uv", executable: "/opt/uv" });
    expect(resolveRunner({ PMAL_PYTHON: "${user_config.python_path}", PMAL_UV: "" })).toEqual({ kind: "uv", executable: "uv" });
    expect(resolveTimeoutMs({ PMAL_TIMEOUT_MS: "45000" })).toBe(45_000);
    expect(resolveTimeoutMs({ PMAL_TIMEOUT_MS: "x" })).toBe(DEFAULT_TIMEOUT_MS);
  });

  it("monta o comando do worker com e sem uv", () => {
    const root = path.resolve("/pmal/runtime");
    const database = path.resolve("/dados/pmal-study.db");
    expect(workerCommand({ kind: "python", executable: "python3" }, root, database)).toEqual({
      args: ["-m", "pmal_study.worker", "--project-root", root, "--database", database],
      env: { PYTHONPATH: path.join(root, "src") },
    });
    expect(workerCommand({ kind: "uv", executable: "uv" }, root).args).toEqual([
      "run", "--quiet", "--project", root, "python", "-m", "pmal_study.worker", "--project-root", root,
    ]);
  });

  it("explica executável ausente em vez de erro genérico", async () => {
    const spawner = () => {
      const child = new EventEmitter() as ChildProcessWithoutNullStreams;
      Object.assign(child, {
        stdin: new Writable({ write(_chunk, _encoding, callback) { callback(); } }),
        stdout: new PassThrough(), stderr: new PassThrough(), kill: () => true,
      });
      queueMicrotask(() => child.emit("error", Object.assign(new Error("spawn"), { code: "ENOENT" })));
      return child;
    };
    const client = new CoreClient(path.resolve("/pmal"), spawner, path.resolve("/tmp/x.db"), undefined, 5_000, { kind: "python", executable: "python-inexistente" });
    await expect(client.call("start-session", { mode: "timed" })).rejects.toThrow(/PMAL_PYTHON/);
  });

  it("usa o runtime empacotado e ignora banco não configurado", () => {
    expect(parseProjectRoot([], "/qualquer", {})).toBe(BUNDLED_PROJECT_ROOT);
    expect(parseDatabasePath([], "/", { PMAL_DATABASE: "${user_config.data_dir}/pmal-study.db" })).toBeUndefined();
    expect(parseDatabasePath(["--database", "/dados/p.db"], "/", {})).toBe(path.resolve("/dados/p.db"));
  });
});

describe("conteúdo legível pelo modelo", () => {
  const core = { call: async () => ({ ok: true as const, data: { request_id: "r1", kind: "dissection", status: "failed" } }) };

  it("inclui JSON no content das ferramentas do modelo, não nas exclusivas do painel", async () => {
    const catalog = createToolCatalog(core);
    const modelTool = catalog.find((entry) => entry.name === "pmal_fail_host_job");
    const appTool = catalog.find((entry) => entry.name === "pmal_get_host_job_status");
    const modelResult = await modelTool?.handler({ request_id: "r1", code: "x", message: "y" });
    const appResult = await appTool?.handler({ request_id: "r1" });
    expect(modelResult?.content).toHaveLength(2);
    expect(JSON.parse(modelResult?.content[1]?.text ?? "{}")).toEqual(modelResult?.structuredContent);
    expect(appResult?.content).toHaveLength(1);
  });
});

describe("prompts e instruções", () => {
  it("publica prompts de estudo e o protocolo nas instruções", async () => {
    const server = createPmalServer(BUNDLED_PROJECT_ROOT, { databasePath: path.resolve("/tmp/nao-usado.db") });
    const client = new Client({ name: "teste", version: "1.0.0" });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
    try {
      const prompts = await client.listPrompts();
      expect(prompts.prompts.map((prompt) => prompt.name)).toEqual(expect.arrayContaining([
        "pmal_estudar", "pmal_revisar", "pmal_diagnostico", "pmal_simulado", "pmal_estudar_no_chat", "pmal_painel",
      ]));
      const study = await client.getPrompt({ name: "pmal_estudar", arguments: { minutos: "20", disciplinas: "legislacao_pmal" } });
      expect(study.messages[0]?.content).toMatchObject({ type: "text", text: expect.stringContaining('"discipline"') });
      expect(client.getInstructions()).toContain("pmal_claim_host_job");
    } finally {
      await client.close();
      await server.close();
    }
  });
});
