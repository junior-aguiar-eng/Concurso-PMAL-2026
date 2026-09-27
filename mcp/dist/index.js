import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CoreClient } from "./core-client.js";
import { createToolCatalog } from "./tools.js";
import { registerDashboardResource } from "./ui-resource.js";
import { registerPrompts, SERVER_INSTRUCTIONS } from "./prompts.js";
/** runtime/ que acompanha o servidor, usado quando --project-root é omitido. */
export const BUNDLED_PROJECT_ROOT = fileURLToPath(new URL("../../runtime", import.meta.url));
function optionValue(argv, flag) {
    const index = argv.indexOf(flag);
    return index >= 0 ? argv[index + 1] : undefined;
}
export function parseProjectRoot(argv, cwd = process.cwd(), env = process.env) {
    const value = optionValue(argv, "--project-root") ?? env.PMAL_PROJECT_ROOT ?? BUNDLED_PROJECT_ROOT;
    return path.resolve(cwd, value);
}
/** Banco explícito; sem ele, o CoreClient copia o banco-semente para PMAL_DATA_DIR. */
export function parseDatabasePath(argv, cwd = process.cwd(), env = process.env) {
    const value = optionValue(argv, "--database") ?? env.PMAL_DATABASE?.trim();
    if (!value || value.includes("${"))
        return undefined;
    return path.resolve(cwd, value);
}
export function parseTransport(argv) {
    const index = argv.indexOf("--transport");
    const value = index >= 0 ? argv[index + 1] : "http";
    if (value !== "http" && value !== "stdio") {
        throw new Error("Transporte deve ser http ou stdio.");
    }
    return value;
}
export function createPmalServer(projectRoot, options = {}) {
    const server = new McpServer({ name: "treinador-pmal-oficial", version: "0.3.1" }, {
        instructions: SERVER_INSTRUCTIONS,
    });
    const core = new CoreClient(projectRoot, undefined, options.databasePath);
    if (options.includeUi !== false)
        registerDashboardResource(server);
    registerPrompts(server);
    for (const entry of createToolCatalog(core, { includeRender: options.includeUi !== false })) {
        server.registerTool(entry.name, entry.definition, async (input) => entry.handler(input));
    }
    const close = server.close.bind(server);
    server.close = async () => {
        await core.close();
        await close();
    };
    return server;
}
export function startHttpServer(projectRoot, port, options = {}) {
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
            const server = createPmalServer(projectRoot, options);
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
    const arguments_ = process.argv.slice(2);
    const projectRoot = parseProjectRoot(arguments_);
    const transport = parseTransport(arguments_);
    const databasePath = parseDatabasePath(arguments_);
    if (transport === "stdio") {
        const server = createPmalServer(projectRoot, { databasePath });
        await server.connect(new StdioServerTransport());
    }
    else {
        const port = Number(process.env.PORT ?? 8787);
        startHttpServer(projectRoot, port, { databasePath });
    }
}
