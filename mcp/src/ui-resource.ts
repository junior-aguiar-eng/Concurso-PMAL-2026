import { readFile } from "node:fs/promises";

import {
  registerAppResource,
  RESOURCE_MIME_TYPE,
} from "@modelcontextprotocol/ext-apps/server";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

export const DASHBOARD_URI = "ui://pmal/dashboard/v1.html";
export const STUDY_URI = "ui://pmal/study/v4.html";

function bundleUrl(name: "dashboard" | "study"): URL {
  return new URL(`../../ui/dist/${name}.js`, import.meta.url);
}

export async function appHtml(name: "dashboard" | "study"): Promise<string> {
  const bundle = (await readFile(bundleUrl(name), "utf8"))
    .replaceAll("</script", "<\\/script");
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Painel PMAL Oficial</title></head><body><div id="root"></div><script type="module">${bundle}</script></body></html>`;
}

export function registerDashboardResource(server: McpServer): void {
  const uiMeta = {
    csp: { connectDomains: [], resourceDomains: [] },
    prefersBorder: true,
  };
  registerAppResource(
    server,
    "Painel PMAL Oficial",
    DASHBOARD_URI,
    {
      description: "Painel interativo local de desempenho, edital, revisões e erros.",
      _meta: { ui: uiMeta, "openai/widgetDescription": "Painel PMAL Oficial com desempenho, cobertura do edital e padrões de erro." },
    },
    async () => ({
      contents: [{
        uri: DASHBOARD_URI,
        mimeType: RESOURCE_MIME_TYPE,
        text: await appHtml("dashboard"),
        _meta: { ui: uiMeta, "openai/widgetDescription": "Painel PMAL Oficial com desempenho, cobertura do edital e padrões de erro." },
      }],
    }),
  );
  registerAppResource(
    server,
    "Ambiente de estudo PMAL Oficial",
    STUDY_URI,
    {
      description: "Ambiente interativo completo para sessão, questões, correção, dissecação e métricas.",
      _meta: { ui: uiMeta, "openai/widgetDescription": "Ambiente principal de estudo PMAL Oficial." },
    },
    async () => ({ contents: [{
      uri: STUDY_URI,
      mimeType: RESOURCE_MIME_TYPE,
      text: await appHtml("study"),
      _meta: { ui: uiMeta, "openai/widgetDescription": "Ambiente principal de estudo PMAL Oficial." },
    }] }),
  );
}
