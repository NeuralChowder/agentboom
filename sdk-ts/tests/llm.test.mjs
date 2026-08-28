import { strictEqual, deepStrictEqual } from "node:assert";
import { describe, it } from "node:test";
import http from "node:http";

function createLlmServer(handler) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        const parsed = JSON.parse(body);
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

async function importLlm(port) {
  process.env.PLATFORM_INTERNAL_URL = `http://127.0.0.1:${port}`;
  return import(`../dist/llm.js?v=${port}`);
}

describe("llm — bridge HTTP client", () => {
  it("complete posts prompt and returns text", async () => {
    let captured;
    const { server, port } = await createLlmServer((body) => {
      captured = body;
      return { body: { text: "Hello from LLM" } };
    });
    const mod = await importLlm(port);
    const result = await mod.complete("Summarise this");
    strictEqual(result, "Hello from LLM");
    strictEqual(captured.prompt, "Summarise this");
    server.close();
  });

  it("complete sends optional opts as snake_case wire fields", async () => {
    let captured;
    const { server, port } = await createLlmServer((body) => {
      captured = body;
      return { body: { text: "done" } };
    });
    const mod = await importLlm(port);
    await mod.complete("test", {
      system: "You are a summariser",
      model: "gpt-4",
      temperature: 0.7,
      maxTokens: 100,
      timeoutSec: 30,
    });
    strictEqual(captured.system, "You are a summariser");
    strictEqual(captured.model, "gpt-4");
    strictEqual(captured.temperature, 0.7);
    strictEqual(captured.max_tokens, 100); // camelCase → snake_case
    strictEqual(captured.timeout, 30); // camelCase → snake_case
    strictEqual(captured.json, undefined); // not a json call
    server.close();
  });

  it("complete returns empty string when text is missing", async () => {
    const { server, port } = await createLlmServer(() => ({ body: {} }));
    const mod = await importLlm(port);
    const result = await mod.complete("test");
    strictEqual(result, "");
    server.close();
  });

  it("complete returns empty string when text is null", async () => {
    const { server, port } = await createLlmServer(() => ({ body: { text: null } }));
    const mod = await importLlm(port);
    const result = await mod.complete("test");
    strictEqual(result, "");
    server.close();
  });

  it("completeJson sends json:true and returns parsed object", async () => {
    let captured;
    const { server, port } = await createLlmServer((body) => {
      captured = body;
      return { body: { ok: true, json: { name: "extracted", value: 42 } } };
    });
    const mod = await importLlm(port);
    const result = await mod.completeJson("Extract fields");
    deepStrictEqual(result, { name: "extracted", value: 42 });
    strictEqual(captured.json, true);
    server.close();
  });

  it("completeJson returns null when ok is false", async () => {
    const { server, port } = await createLlmServer(() => ({ body: { ok: false } }));
    const mod = await importLlm(port);
    const result = await mod.completeJson("Extract");
    strictEqual(result, null);
    server.close();
  });

  it("completeJson returns null when ok is true but json missing", async () => {
    const { server, port } = await createLlmServer(() => ({ body: { ok: true } }));
    const mod = await importLlm(port);
    const result = await mod.completeJson("Extract");
    strictEqual(result, null);
    server.close();
  });

  it("completeJson returns null when ok is true but json is null", async () => {
    const { server, port } = await createLlmServer(() => ({ body: { ok: true, json: null } }));
    const mod = await importLlm(port);
    const result = await mod.completeJson("Extract");
    strictEqual(result, null);
    server.close();
  });

  it("non-2xx response throws LLMError with body in message", async () => {
    const { server, port } = await createLlmServer(() => ({
      status: 502,
      body: JSON.stringify({ error: "bad gateway" }),
    }));
    const mod = await importLlm(port);
    try {
      await mod.complete("test");
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.LLMError)) throw new Error(`expected LLMError, got: ${err}`);
      strictEqual(err.message.includes("llm bridge error"), true);
      strictEqual(err.message.includes("bad gateway"), true);
    }
    server.close();
  });

  it("connection refused throws LLMError mentioning unreachable", async () => {
    const mod = await importLlm(1);
    try {
      await mod.complete("test");
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.LLMError)) throw new Error(`expected LLMError, got: ${err}`);
      strictEqual(err.message.includes("llm bridge unreachable"), true);
    }
  });

  it("sends content-type: application/json header", async () => {
    let contentType = null;
    const { server, port } = await new Promise((resolve) => {
      const server = http.createServer((req, res) => {
        contentType = req.headers["content-type"];
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ text: "ok" }));
      });
      server.listen(0, "127.0.0.1", () => {
        resolve({ server, port: server.address().port });
      });
    });
    const mod = await importLlm(port);
    await mod.complete("test");
    strictEqual(contentType, "application/json");
    server.close();
  });

  it("URL construction: posts to /api/llm/complete", async () => {
    let capturedUrl = "";
    const { server, port } = await new Promise((resolve) => {
      const server = http.createServer((req, res) => {
        capturedUrl = req.url;
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ text: "ok" }));
      });
      server.listen(0, "127.0.0.1", () => {
        resolve({ server, port: server.address().port });
      });
    });
    const mod = await importLlm(port);
    await mod.complete("test");
    strictEqual(capturedUrl, "/api/llm/complete");
    server.close();
  });

  it("PLATFORM_INTERNAL_URL constant matches the configured base", async () => {
    const { server, port } = await createLlmServer(() => ({ body: { text: "ok" } }));
    const mod = await importLlm(port);
    strictEqual(mod.PLATFORM_INTERNAL_URL, `http://127.0.0.1:${port}`);
    server.close();
  });
});
