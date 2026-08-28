import { strictEqual, deepStrictEqual } from "node:assert";
import { describe, it } from "node:test";
import http from "node:http";

/** Create a tiny HTTP server that mimics /api/bridge/db. */
function createDbServer(handler) {
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
      const addr = server.address();
      resolve({ server, port: addr.port });
    });
  });
}

/** Import a fresh module instance pointed at the given port. */
async function importDb(port) {
  process.env.PLATFORM_INTERNAL_URL = `http://127.0.0.1:${port}`;
  return import(`../dist/db.js?v=${port}`);
}

describe("db — bridge HTTP client", () => {
  it("execute posts {op:'execute'} and returns rowCount", async () => {
    let captured;
    const { server, port } = await createDbServer((body) => {
      captured = body;
      return { body: { rowcount: 5 } };
    });
    const mod = await importDb(port);
    const result = await mod.execute("INSERT INTO t VALUES (?)", "a");
    strictEqual(result.rowCount, 5);
    strictEqual(captured.op, "execute");
    strictEqual(captured.sql, "INSERT INTO t VALUES (?)");
    deepStrictEqual(captured.params, ["a"]);
    server.close();
  });

  it("execute defaults rowCount to 0 when rowcount missing", async () => {
    const { server, port } = await createDbServer(() => ({ body: {} }));
    const mod = await importDb(port);
    const result = await mod.execute("INSERT INTO t VALUES (1)");
    strictEqual(result.rowCount, 0);
    server.close();
  });

  it("fetchOne returns the row from gateway response", async () => {
    const { server, port } = await createDbServer(() => ({
      body: { row: { id: 1, name: "test", active: true } },
    }));
    const mod = await importDb(port);
    const result = await mod.fetchOne("SELECT * FROM t WHERE id = ?", 1);
    deepStrictEqual(result, { id: 1, name: "test", active: true });
    server.close();
  });

  it("fetchOne returns null when row is null", async () => {
    const { server, port } = await createDbServer(() => ({ body: { row: null } }));
    const mod = await importDb(port);
    const result = await mod.fetchOne("SELECT * FROM t WHERE id = ?", 999);
    strictEqual(result, null);
    server.close();
  });

  it("fetchOne returns null when row key is missing", async () => {
    const { server, port } = await createDbServer(() => ({ body: {} }));
    const mod = await importDb(port);
    const result = await mod.fetchOne("SELECT 1");
    strictEqual(result, null);
    server.close();
  });

  it("fetchRow is an alias of fetchOne", async () => {
    const { server, port } = await createDbServer(() => ({ body: { row: { x: 42 } } }));
    const mod = await importDb(port);
    const result = await mod.fetchRow("SELECT 1");
    deepStrictEqual(result, { x: 42 });
    server.close();
  });

  it("fetchAll returns the rows array", async () => {
    const { server, port } = await createDbServer(() => ({
      body: { rows: [{ id: 1 }, { id: 2 }] },
    }));
    const mod = await importDb(port);
    const result = await mod.fetchAll("SELECT * FROM t");
    strictEqual(result.length, 2);
    deepStrictEqual(result[0], { id: 1 });
    deepStrictEqual(result[1], { id: 2 });
    server.close();
  });

  it("fetchAll returns empty array when rows is missing", async () => {
    const { server, port } = await createDbServer(() => ({ body: {} }));
    const mod = await importDb(port);
    const result = await mod.fetchAll("SELECT * FROM empty");
    strictEqual(result.length, 0);
    server.close();
  });

  it("fetchVal returns the value field", async () => {
    const { server, port } = await createDbServer(() => ({ body: { value: "hello" } }));
    const mod = await importDb(port);
    const result = await mod.fetchVal("SELECT 'hello'");
    strictEqual(result, "hello");
    server.close();
  });

  it("fetchVal returns null when value is missing", async () => {
    const { server, port } = await createDbServer(() => ({ body: {} }));
    const mod = await importDb(port);
    const result = await mod.fetchVal("SELECT NULL");
    strictEqual(result, null);
    server.close();
  });

  it("fetchVal returns null when value is null", async () => {
    const { server, port } = await createDbServer(() => ({ body: { value: null } }));
    const mod = await importDb(port);
    const result = await mod.fetchVal("SELECT NULL");
    strictEqual(result, null);
    server.close();
  });

  it("batch posts {op:'batch', statements:[...]} and returns void", async () => {
    let captured;
    const { server, port } = await createDbServer((body) => {
      captured = body;
      return { body: {} };
    });
    const mod = await importDb(port);
    await mod.batch([
      { sql: "INSERT INTO a VALUES (1)", params: [1] },
      { sql: "UPDATE b SET n = n + 1" },
    ]);
    strictEqual(captured.op, "batch");
    strictEqual(captured.statements.length, 2);
    deepStrictEqual(captured.statements[0], { sql: "INSERT INTO a VALUES (1)", params: [1] });
    deepStrictEqual(captured.statements[1], { sql: "UPDATE b SET n = n + 1" });
    server.close();
  });

  it("non-2xx response throws DbError", async () => {
    const { server, port } = await createDbServer(() => ({
      status: 500,
      body: JSON.stringify({ error: "internal server error" }),
    }));
    const mod = await importDb(port);
    try {
      await mod.execute("SELECT 1");
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.DbError)) throw new Error(`expected DbError, got: ${err}`);
      strictEqual(err.message.includes("db bridge error"), true);
      strictEqual(err.message.includes("internal server error"), true);
    }
    server.close();
  });

  it("non-2xx response throws DbError with truncated body", async () => {
    const { server, port } = await createDbServer(() => ({
      status: 500,
      body: "x".repeat(500),
    }));
    const mod = await importDb(port);
    try {
      await mod.fetchAll("SELECT 1");
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.DbError)) throw new Error(`expected DbError, got: ${err}`);
      // Message should be truncated to 300 chars from body
      strictEqual(err.message.length < 500, true);
    }
    server.close();
  });

  it("connection refused throws DbError mentioning unreachable", async () => {
    // Port 1 is reserved and almost never in use
    const mod = await importDb(1);
    try {
      await mod.execute("SELECT 1");
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.DbError)) throw new Error(`expected DbError, got: ${err}`);
      strictEqual(err.message.includes("db bridge unreachable"), true);
      strictEqual(err.message.includes("127.0.0.1:1"), true);
    }
  });

  it("sends content-type: application/json header", async () => {
    let contentType = null;
    const { server, port } = await new Promise((resolve) => {
      const server = http.createServer((req, res) => {
        contentType = req.headers["content-type"];
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ rowcount: 0 }));
      });
      server.listen(0, "127.0.0.1", () => {
        resolve({ server, port: server.address().port });
      });
    });
    const mod = await importDb(port);
    await mod.execute("SELECT 1");
    strictEqual(contentType, "application/json");
    server.close();
  });

  it("JSON encode/decode round-trip preserves unicode and booleans", async () => {
    let captured;
    const { server, port } = await createDbServer((body) => {
      captured = body;
      return { body: { rows: [{ name: "José émojis 🎉", active: true, count: 3.14 }] } };
    });
    const mod = await importDb(port);
    const result = await mod.fetchAll("SELECT * FROM t WHERE n = ?", "José 🎉");
    strictEqual(captured.params[0], "José 🎉");
    strictEqual(result[0].name, "José émojis 🎉");
    strictEqual(result[0].active, true);
    strictEqual(result[0].count, 3.14);
    server.close();
  });
});
