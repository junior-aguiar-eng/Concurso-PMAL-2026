import { once } from "node:events";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { afterEach, describe, expect, it } from "vitest";

import { parseProjectRoot, parseTransport, startHttpServer } from "../src/index.js";

describe("servidor MCP local", () => {
  const resources: Array<{ close: () => unknown }> = [];

  afterEach(async () => {
    for (const resource of resources.reverse()) await resource.close();
    resources.length = 0;
  });

  it("aceita transporte stdio ou http e rejeita valores desconhecidos", () => {
    expect(parseTransport(["--transport", "stdio"])).toBe("stdio");
    expect(parseTransport([])).toBe("http");
    expect(() => parseTransport(["--transport", "socket"])).toThrow(/transporte/i);
  });

  it("resolve o diretório do runtime relativamente ao cwd do plugin", () => {
    const pluginRoot = path.resolve("..");

    expect(parseProjectRoot(["--project-root", "./runtime"], pluginRoot))
      .toBe(path.join(pluginRoot, "runtime"));
  });

  it("inicializa por stdio usando exatamente o launcher empacotado", async () => {
    const pluginRoot = path.resolve("..");
    const manifest = JSON.parse(await readFile(path.join(pluginRoot, ".mcp.json"), "utf8"));
    const declaration = manifest.mcpServers["treinador-pmal-oficial"];
    const client = new Client({ name: "pmal-package-test", version: "0.1.0" });
    resources.push(client);
    const transport = new StdioClientTransport({
      command: declaration.command,
      args: declaration.args,
      cwd: path.resolve(pluginRoot, declaration.cwd),
      env: { ...process.env, ...declaration.env },
      stderr: "pipe",
    });

    await client.connect(transport);
    const listed = await client.listTools();

    expect(listed.tools.map((tool) => tool.name)).toContain("pmal_open_study_panel");
  }, 20_000);

  it("expõe /mcp, lista ferramentas e consulta o núcleo Python", async () => {
    const packagedRoot = path.resolve(import.meta.dirname, "../../runtime");
    const projectRoot = existsSync(path.join(packagedRoot, "pyproject.toml"))
      ? packagedRoot : path.resolve(import.meta.dirname, "../../../..");
    const httpServer = startHttpServer(projectRoot, 0);
    resources.push(httpServer);
    await once(httpServer, "listening");
    const address = httpServer.address();
    if (!address || typeof address === "string") throw new Error("porta indisponível");

    const client = new Client({ name: "pmal-test", version: "0.1.0" });
    resources.push(client);
    const transport = new StreamableHTTPClientTransport(
      new URL(`http://127.0.0.1:${address.port}/mcp`),
    );
    await client.connect(transport);

    const listed = await client.listTools();
    const listedResources = await client.listResources();
    const dashboardResource = await client.readResource({ uri: "ui://pmal/dashboard/v1.html" });
    const studyResource = await client.readResource({ uri: "ui://pmal/study/v4.html" });
    const dashboardContent = dashboardResource.contents[0];
    const dashboard = await client.callTool({
      name: "pmal_get_dashboard",
      arguments: {},
    });

    expect(listed.tools.map((tool) => tool.name)).toContain("pmal_submit_answer");
    expect(listed.tools.find((tool) => tool.name === "pmal_render_dashboard")?._meta)
      .toMatchObject({ ui: { resourceUri: "ui://pmal/dashboard/v1.html" } });
    expect(listedResources.resources.map((resource) => resource.uri))
      .toContain("ui://pmal/dashboard/v1.html");
    expect(dashboardContent?.mimeType).toBe("text/html;profile=mcp-app");
    expect(dashboardContent).toMatchObject({ uri: "ui://pmal/dashboard/v1.html" });
    const dashboardHtml = dashboardContent && "text" in dashboardContent ? dashboardContent.text : "";
    const studyContent = studyResource.contents[0];
    const studyHtml = studyContent && "text" in studyContent ? studyContent.text : "";
    expect(dashboardHtml).toContain('id="root"');
    expect(dashboardHtml).toContain("Nenhuma resposta registrada ainda.");
    expect(dashboardHtml).toContain("pmal_get_dashboard");
    expect(dashboardHtml).not.toContain("pmal_start_study_session");
    expect(studyHtml).toContain("pmal_start_study_session");
    expect(dashboardHtml.length).toBeGreaterThan(1_000);
    expect(dashboardHtml).not.toMatch(/\.pdf\b/i);
    expect(dashboard.isError).not.toBe(true);
    expect(dashboard.structuredContent).toMatchObject({ study_bank: expect.any(Number) });
  }, 20_000);
});
