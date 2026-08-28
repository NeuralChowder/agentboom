import { strictEqual, deepStrictEqual } from "node:assert";
import { describe, it } from "node:test";
import http from "node:http";

function createAgentServer(handler) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        const parsed = body ? JSON.parse(body) : undefined;
        try {
          const result = handler(parsed);
          res.writeHead(result.status ?? 200, { "content-type": "application/json" });
          res.end(typeof result.body === "string" ? result.body : JSON.stringify(result.body));
        } catch (err) {
          res.writeHead(500, { "content-type": "application/json" });
          res.end(JSON.stringify({ error: String(err) }));
        }
      });
    });
    server.listen(0, "127.0.0.1", () => {
      resolve({ server, port: server.address().port });
    });
  });
}

async function importAgent(port) {
  process.env.PLATFORM_INTERNAL_URL = `http://127.0.0.1:${port}`;
  return import(`../dist/agent.js?v=${port}`);
}

describe("agent — bridge HTTP client", () => {
  it("ask posts prompt and returns text on ok response", async () => {
    let captured;
    const { server, port } = await createAgentServer((body) => {
      captured = body;
      return { body: { ok: true, text: "The answer is 42" } };
    });
    const mod = await importAgent(port);
    const result = await mod.ask("What is 6 times 7?");
    strictEqual(result, "The answer is 42");
    strictEqual(captured.prompt, "What is 6 times 7?");
    server.close();
  });

  it("ask sends conversation and timeout opts", async () => {
    let captured;
    const { server, port } = await createAgentServer((body) => {
      captured = body;
      return { body: { ok: true, text: "continuation" } };
    });
    const mod = await importAgent(port);
    await mod.ask("follow-up", { conversation: "conv-id-123", timeoutSec: 60 });
    strictEqual(captured.conversation, "conv-id-123");
    strictEqual(captured.timeout, 60); // camelCase → snake_case
    server.close();
  });

  it("ask returns null when gateway responds ok:false", async () => {
    const { server, port } = await createAgentServer(() => ({
      body: { ok: false, text: "" },
    }));
    const mod = await importAgent(port);
    const result = await mod.ask("test");
    strictEqual(result, null);
    server.close();
  });

  it("ask returns null when ok:true but text missing", async () => {
    const { server, port } = await createAgentServer(() => ({ body: { ok: true } }));
    const mod = await importAgent(port);
    const result = await mod.ask("test");
    strictEqual(result, null);
    server.close();
  });

  it("ask returns null on non-2xx (silently, unlike db/llm)", async () => {
    const { server, port } = await createAgentServer(() => ({
      status: 500,
      body: JSON.stringify({ error: "internal error" }),
    }));
    const mod = await importAgent(port);
    const result = await mod.ask("test");
    strictEqual(result, null); // silent failure by design
    server.close();
  });

  it("ask returns null on connection refused (mirrors Python SDK)", async () => {
    const mod = await importAgent(1);
    const result = await mod.ask("test");
    strictEqual(result, null); // silent failure by design
  });

  it("askJson posts json:true and returns parsed object", async () => {
    let captured;
    const { server, port } = await createAgentServer((body) => {
      captured = body;
      return { body: { ok: true, json: { answer: 42, confidence: 0.99 } } };
    });
    const mod = await importAgent(port);
    const result = await mod.askJson("What is 6 times 7?");
    deepStrictEqual(result, { answer: 42, confidence: 0.99 });
    strictEqual(captured.json, true);
    server.close();
  });

  it("askJson returns null when ok is false", async () => {
    const { server, port } = await createAgentServer(() => ({ body: { ok: false } }));
    const mod = await importAgent(port);
    const result = await mod.askJson("test");
    strictEqual(result, null);
    server.close();
  });

  it("askJson returns null when ok:true but json missing", async () => {
    const { server, port } = await createAgentServer(() => ({ body: { ok: true } }));
    const mod = await importAgent(port);
    const result = await mod.askJson("test");
    strictEqual(result, null);
    server.close();
  });

  it("askJson returns null on connection failure", async () => {
    const mod = await importAgent(1);
    const result = await mod.askJson("test");
    strictEqual(result, null);
  });

  it("URL construction: posts to /api/agent/ask", async () => {
    let capturedUrl = "";
    const { server, port } = await new Promise((resolve) => {
      const server = http.createServer((req, res) => {
        capturedUrl = req.url;
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: true, text: "ok" }));
      });
      server.listen(0, "127.0.0.1", () => {
        resolve({ server, port: server.address().port });
      });
    });
    const mod = await importAgent(port);
    await mod.ask("test");
    strictEqual(capturedUrl, "/api/agent/ask");
    server.close();
  });

  it("sends content-type: application/json header", async () => {
    let contentType = null;
    const { server, port } = await new Promise((resolve) => {
      const server = http.createServer((req, res) => {
        contentType = req.headers["content-type"];
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: true, text: "ok" }));
      });
      server.listen(0, "127.0.0.1", () => {
        resolve({ server, port: server.address().port });
      });
    });
    const mod = await importAgent(port);
    await mod.ask("test");
    strictEqual(contentType, "application/json");
    server.close();
  });

  it("conversation defaults to undefined (not null) in body", async () => {
    let captured;
    const { server, port } = await createAgentServer((body) => {
      captured = body;
      return { body: { ok: true, text: "ok" } };
    });
    const mod = await importAgent(port);
    await mod.ask("test");
    // The key should be present with undefined value (JSON.stringify drops it)
    strictEqual(captured.conversation, undefined);
    server.close();
  });

  it("PLATFORM_INTERNAL_URL constant matches configured base", async () => {
    const { server, port } = await createAgentServer(() => ({ body: { ok: true, text: "ok" } }));
    const mod = await importAgent(port);
    strictEqual(mod.PLATFORM_INTERNAL_URL, `http://127.0.0.1:${port}`);
    server.close();
  });
});
