#!/usr/bin/env node
// Obsidian MCP bridge — stdio ↔ Obsidian Local REST API, one port per vault.
// Requires: Obsidian running with the Local REST API plugin in each vault you want reachable.
// A vault is only reachable while its own Obsidian window is open: obsidian://open?vault=<Name>

const http = require("http");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const HOST = "127.0.0.1";
const VAULT_ROOT = process.env.DAVID_VAULTS || path.join(__dirname, "vaults");   // the folder that holds one sub-folder per vault
const DEFAULT_VAULT = "System";

// Ports match each vault's .obsidian/plugins/obsidian-local-rest-api/data.json (insecurePort).
const VAULTS = { System: 27123, Business: 27125, Music: 27127, Lore: 27129 };
// Research is firewalled from cloud tiers in the DLP policy (config/vaults.toml) — it stays out of this bridge
// (and so out of any model's context) unless explicitly opted in.
if (process.env.OBSIDIAN_MCP_ALLOW_RESEARCH === "1") VAULTS.Research = 27131;

const keyCache = {};
function apiKeyFor(vault) {
  if (!keyCache[vault]) {
    const cfg = path.join(VAULT_ROOT, vault, ".obsidian", "plugins", "obsidian-local-rest-api", "data.json");
    keyCache[vault] = JSON.parse(fs.readFileSync(cfg, "utf8")).apiKey;
  }
  return keyCache[vault];
}

function request(vault, method, urlPath, body, contentType) {
  return new Promise((resolve, reject) => {
    const buf = body !== undefined ? Buffer.from(body, "utf8") : null;
    const opts = {
      hostname: HOST, port: VAULTS[vault], path: urlPath, method,
      headers: {
        "Authorization": `Bearer ${apiKeyFor(vault)}`,
        ...(buf ? { "Content-Type": contentType, "Content-Length": buf.length } : {})
      }
    };
    const req = http.request(opts, res => {
      let data = "";
      res.on("data", c => data += c);
      res.on("end", () => {
        try { resolve({ status: res.statusCode, body: data ? JSON.parse(data) : null }); }
        catch { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on("error", reject);
    if (buf) req.write(buf);
    req.end();
  });
}

const vaultProp = {
  type: "string",
  enum: Object.keys(VAULTS),
  description: `Which vault to use (default ${DEFAULT_VAULT}). Its Obsidian window must be open.`
};

const TOOLS = [
  {
    name: "list_vault_files",
    description: "List all files and folders in an Obsidian vault. Returns paths relative to the vault root.",
    inputSchema: {
      type: "object",
      properties: {
        vault: vaultProp,
        path: { type: "string", description: "Sub-folder to list (optional, defaults to vault root)" }
      }
    }
  },
  {
    name: "read_note",
    description: "Read the full markdown content of a note by its vault path (e.g. 'System/OKR-2026.md').",
    inputSchema: {
      type: "object",
      properties: {
        vault: vaultProp,
        path: { type: "string", description: "Vault-relative path to the note, e.g. 'Task Ledger.md' or 'Briefings/2026-06-14.md'" }
      },
      required: ["path"]
    }
  },
  {
    name: "create_or_update_note",
    description: "Create a new note or fully replace an existing note's content. Use for session logs, work summaries, David updates, and structured knowledge entries.",
    inputSchema: {
      type: "object",
      properties: {
        vault: vaultProp,
        path: { type: "string", description: "Vault-relative path, e.g. 'System/SessionLog/2026-06-14.md'" },
        content: { type: "string", description: "Full markdown content for the note" }
      },
      required: ["path", "content"]
    }
  },
  {
    name: "append_to_note",
    description: "Append text to the end of an existing note without overwriting it. Use for adding entries to logs, ledgers, or running notes.",
    inputSchema: {
      type: "object",
      properties: {
        vault: vaultProp,
        path: { type: "string", description: "Vault-relative path to the note" },
        content: { type: "string", description: "Markdown text to append" }
      },
      required: ["path", "content"]
    }
  },
  {
    name: "search_vault",
    description: "Full-text search across all notes in a vault. Returns matching file paths and context snippets.",
    inputSchema: {
      type: "object",
      properties: {
        vault: vaultProp,
        query: { type: "string", description: "Search query (plain text or regex)" }
      },
      required: ["query"]
    }
  },
  {
    name: "delete_note",
    description: "Delete a note from a vault by its path.",
    inputSchema: {
      type: "object",
      properties: {
        vault: vaultProp,
        path: { type: "string", description: "Vault-relative path to the note to delete" }
      },
      required: ["path"]
    }
  }
];

async function callTool(name, args) {
  const vault = args.vault || DEFAULT_VAULT;
  if (!(vault in VAULTS)) {
    return `Unknown or unavailable vault "${vault}". Available: ${Object.keys(VAULTS).join(", ")}`;
  }
  try {
    if (name === "list_vault_files") {
      const sub = args.path ? `/${encodeURIComponent(args.path)}/` : "/";
      const res = await request(vault, "GET", `/vault${sub}`);
      if (res.status !== 200) return `Error ${res.status}: ${JSON.stringify(res.body)}`;
      const files = res.body.files || [];
      return files.join("\n") || "(empty)";
    }

    if (name === "read_note") {
      const res = await request(vault, "GET", `/vault/${encodeURIComponent(args.path)}`);
      if (res.status === 404) return `Note not found: ${args.path}`;
      if (res.status !== 200) return `Error ${res.status}: ${JSON.stringify(res.body)}`;
      return typeof res.body === "string" ? res.body : JSON.stringify(res.body);
    }

    if (name === "create_or_update_note") {
      const res = await request(vault, "PUT", `/vault/${encodeURIComponent(args.path)}`, args.content, "text/markdown");
      if (res.status === 200 || res.status === 204) return `Created/updated: ${args.path}`;
      return `Error ${res.status}: ${res.body}`;
    }

    if (name === "append_to_note") {
      const res = await request(vault, "POST", `/vault/${encodeURIComponent(args.path)}`, `\n${args.content}`, "text/markdown");
      if (res.status === 200 || res.status === 204) return `Appended to: ${args.path}`;
      return `Error ${res.status}: ${res.body}`;
    }

    if (name === "search_vault") {
      const res = await request(vault, "POST", `/search/simple/?query=${encodeURIComponent(args.query)}`);
      if (res.status !== 200) return `Error ${res.status}: ${JSON.stringify(res.body)}`;
      const results = Array.isArray(res.body) ? res.body : [];
      if (!results.length) return "No results found.";
      return results.map(r => `${r.filename}\n  ${r.context || ""}`).join("\n\n");
    }

    if (name === "delete_note") {
      const res = await request(vault, "DELETE", `/vault/${encodeURIComponent(args.path)}`);
      if (res.status === 200 || res.status === 204) return `Deleted: ${args.path}`;
      return `Error ${res.status}: ${JSON.stringify(res.body)}`;
    }

    return `Unknown tool: ${name}`;
  } catch (err) {
    if (err.code === "ECONNREFUSED") {
      return `Vault "${vault}" is not open in Obsidian (port ${VAULTS[vault]} refused). Open it with: obsidian://open?vault=${vault}`;
    }
    return `Error: ${err.message}`;
  }
}

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on("line", async line => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }

  if (msg.method === "initialize") {
    send({ jsonrpc: "2.0", id: msg.id, result: {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "obsidian-david", version: "1.1.0" }
    }});
    return;
  }

  if (msg.method === "notifications/initialized") return;

  if (msg.method === "tools/list") {
    send({ jsonrpc: "2.0", id: msg.id, result: { tools: TOOLS } });
    return;
  }

  if (msg.method === "tools/call") {
    const result = await callTool(msg.params.name, msg.params.arguments || {});
    send({ jsonrpc: "2.0", id: msg.id, result: {
      content: [{ type: "text", text: result }]
    }});
    return;
  }

  if (msg.id !== undefined) {
    send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: "Method not found" } });
  }
});
