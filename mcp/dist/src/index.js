import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CoreClient } from "./core-client.js";
import { createToolCatalog } from "./tools.js";
export function parseProjectRoot(argv) {
    const index = argv.indexOf("--project-root");
    const value = index >= 0 ? argv[index + 1] : undefined;
    if (!value || !path.isAbsolute(value)) {
        throw new Error("Informe --project-root com caminho absoluto.");
    }
    return path.resolve(value);
}
export function createPmalServer(projectRoot) {
    const server = new McpServer({ name: "treinador-pmal-oficial", version: "0.1.0" }, {
        instructions: "Use apenas as quatro disciplinas do recorte PMAL Oficial. Obtenha a próxima questão antes de apresentá-la e só envie a resposta após receber C/E e confiança de 0 a 3.",
    });
    const core = new CoreClient(projectRoot);
    for (const entry of createToolCatalog(core)) {
        server.registerTool(entry.name, entry.definition, async (input) => entry.handler(input));
    }
    return server;
}
export function startHttpServer(projectRoot, port) {
    const httpServer = createServer(async (request, response) => {
        const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
        if (request.method === "GET" && url.pathname === "/") {
            response.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
            response.end("Treinador PMAL Oficial MCP");
            return;
        }
        if (request.method === "OPTIONS" && url.pathname === "/mcp") {
            response.writeHead(204, {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "content-type, mcp-session-id",
                "Access-Control-Expose-Headers": "Mcp-Session-Id",
            });
            response.end();
            return;
        }
        if (url.pathname === "/mcp" && ["POST", "GET", "DELETE"].includes(request.method ?? "")) {
            response.setHeader("Access-Control-Allow-Origin", "*");
            response.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
            const server = createPmalServer(projectRoot);
            const transport = new StreamableHTTPServerTransport({
                sessionIdGenerator: undefined,
                enableJsonResponse: true,
            });
            response.on("close", () => {
                void transport.close();
                void server.close();
            });
            try {
                await server.connect(transport);
                await transport.handleRequest(request, response);
            }
            catch {
                if (!response.headersSent)
                    response.writeHead(500).end("Internal server error");
            }
            return;
        }
        response.writeHead(404).end("Not Found");
    });
    httpServer.listen(port);
    return httpServer;
}
const isEntryPoint = process.argv[1]
    ? path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
    : false;
if (isEntryPoint) {
    const projectRoot = parseProjectRoot(process.argv.slice(2));
    const port = Number(process.env.PORT ?? 8787);
    startHttpServer(projectRoot, port);
}
