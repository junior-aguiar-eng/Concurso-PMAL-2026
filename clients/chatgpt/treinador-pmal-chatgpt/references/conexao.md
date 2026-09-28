# Conectar o servidor MCP ao ChatGPT

O ChatGPT só aceita conectores MCP remotos por HTTPS; o modo `stdio` usado pelo Claude/Codex não serve.

1. Inicie o servidor em HTTP (porta padrão 8787, rota `/mcp`):
   ```bash
   cd mcp && npm ci && npm run build
   PORT=8787 node dist/index.js --transport http --project-root ../runtime
   ```
2. Exponha por HTTPS com um túnel temporário (ex.: `cloudflared tunnel --url http://localhost:8787` ou `ngrok http 8787`).
3. No ChatGPT: Configurações → Aplicativos/Conectores → modo desenvolvedor → criar conector com URL `https://<túnel>/mcp`, sem autenticação. Em workspace corporativo, o administrador pode precisar liberar conectores personalizados.
4. Na conversa, ative o conector e a skill `treinador-pmal-chatgpt`.

**Segurança:** o servidor não tem autenticação. Qualquer pessoa com a URL do túnel acessa e altera seu histórico. Use túnel temporário, não divulgue a URL e encerre-o ao final do estudo.
