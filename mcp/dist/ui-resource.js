import { readFile } from "node:fs/promises";
import { registerAppResource, RESOURCE_MIME_TYPE, } from "@modelcontextprotocol/ext-apps/server";
export const DASHBOARD_URI = "ui://pmal/dashboard/v1.html";
function dashboardBundleUrl() {
    return new URL("../../ui/dist/dashboard.js", import.meta.url);
}
export async function dashboardHtml() {
    const bundle = (await readFile(dashboardBundleUrl(), "utf8"))
        .replaceAll("</script", "<\\/script");
    return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Painel PMAL Oficial</title></head><body><div id="root"></div><script type="module">${bundle}</script></body></html>`;
}
export function registerDashboardResource(server) {
    const uiMeta = {
        csp: { connectDomains: [], resourceDomains: [] },
        prefersBorder: true,
    };
    registerAppResource(server, "Painel PMAL Oficial", DASHBOARD_URI, {
        description: "Painel interativo local de desempenho, edital, revisões e erros.",
        _meta: { ui: uiMeta, "openai/widgetDescription": "Painel PMAL Oficial com desempenho, cobertura do edital e padrões de erro." },
    }, async () => ({
        contents: [{
                uri: DASHBOARD_URI,
                mimeType: RESOURCE_MIME_TYPE,
                text: await dashboardHtml(),
                _meta: { ui: uiMeta, "openai/widgetDescription": "Painel PMAL Oficial com desempenho, cobertura do edital e padrões de erro." },
            }],
    }));
}
