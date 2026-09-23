import { once } from "node:events";
import path from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { afterEach, describe, expect, it } from "vitest";
import { startHttpServer } from "../src/index.js";
describe("servidor MCP local", () => {
    const resources = [];
    afterEach(async () => {
        for (const resource of resources.reverse())
            await resource.close();
        resources.length = 0;
    });
    it("expõe /mcp, lista ferramentas e consulta o núcleo Python", async () => {
        const projectRoot = path.resolve("../../..");
        const httpServer = startHttpServer(projectRoot, 0);
        resources.push(httpServer);
        await once(httpServer, "listening");
        const address = httpServer.address();
        if (!address || typeof address === "string")
            throw new Error("porta indisponível");
        const client = new Client({ name: "pmal-test", version: "0.1.0" });
        resources.push(client);
        const transport = new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${address.port}/mcp`));
        await client.connect(transport);
        const listed = await client.listTools();
        const dashboard = await client.callTool({
            name: "pmal_get_dashboard",
            arguments: {},
        });
        expect(listed.tools.map((tool) => tool.name)).toContain("pmal_submit_answer");
        expect(dashboard.isError).not.toBe(true);
        expect(dashboard.structuredContent).toMatchObject({ study_bank: 19 });
    }, 20_000);
});
