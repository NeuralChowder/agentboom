import { strictEqual, deepStrictEqual, ok } from "node:assert";
import { describe, it } from "node:test";
import http from "node:http";

function createCapsServer(handler) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        const parsed = body ? JSON.parse(body) : undefined;
        try {
          const result = handler(req, parsed);
          res.writeHead(result.status ?? 200, result.headers || { "content-type": "application/json" });
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

async function importCaps(port) {
  process.env.PLATFORM_INTERNAL_URL = `http://127.0.0.1:${port}`;
  return import(`../dist/capabilities.js?v=${port}`);
}

describe("capabilities — bridge HTTP client", () => {
  it("registry fetches from /api/capabilities and returns records", async () => {
    let capturedUrl = "";
    const { server, port } = await createCapsServer((req) => {
      capturedUrl = req.url;
      return {
        body: {
          capabilities: {
            "contacts.lookup": {
              app: "contacts",
              method: "POST",
              path: "/lookup",
              description: "Look up a contact",
            },
          },
        },
      };
    });
    const mod = await importCaps(port);
    const caps = await mod.registry();
    ok("contacts.lookup" in caps);
    strictEqual(caps["contacts.lookup"].app, "contacts");
    strictEqual(caps["contacts.lookup"].method, "POST");
    strictEqual(caps["contacts.lookup"].path, "/lookup");
    strictEqual(capturedUrl, "/api/capabilities");
    server.close();
  });

  it("registry returns cached result within TTL", async () => {
    let requestCount = 0;
    const { server, port } = await createCapsServer(() => {
      requestCount++;
      return {
        body: {
          capabilities: {
            "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
          },
        },
      };
    });
    const mod = await importCaps(port);
    const caps1 = await mod.registry();
    const caps2 = await mod.registry(); // should be cached
    strictEqual(requestCount, 1);
    deepStrictEqual(caps1, caps2);
    server.close();
  });

  it("registry with refresh=true bypasses cache", async () => {
    let requestCount = 0;
    const { server, port } = await createCapsServer(() => {
      requestCount++;
      return {
        body: {
          capabilities: {
            "contacts.lookup": { app: "contacts", method: "POST", path: `/v${requestCount}`, description: "d" },
          },
        },
      };
    });
    const mod = await importCaps(port);
    const r1 = await mod.resolve("contacts.lookup");
    strictEqual(r1.path, "/v1");
    const r2 = await mod.resolve("contacts.lookup"); // cached
    strictEqual(r2.path, "/v1");
    const r3 = await mod.resolve("contacts.lookup", true); // refreshed
    strictEqual(r3.path, "/v2");
    strictEqual(requestCount, 2);
    server.close();
  });

  it("registry throws CapabilityError when unreachable and no cache", async () => {
    const mod = await importCaps(1);
    try {
      await mod.registry();
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.CapabilityError)) throw new Error(`expected CapabilityError, got: ${err}`);
      strictEqual(err.message.includes("capability registry unreachable"), true);
    }
  });

  it("registry throws CapabilityError on non-2xx from gateway", async () => {
    const { server, port } = await createCapsServer(() => ({
      status: 403,
      body: JSON.stringify({ error: "forbidden" }),
    }));
    const mod = await importCaps(port);
    try {
      await mod.registry();
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.CapabilityError)) throw new Error(`expected CapabilityError, got: ${err}`);
      strictEqual(err.message.includes("capability registry returned HTTP 403"), true);
    }
    server.close();
  });

  it("registry handles missing capabilities key (returns empty)", async () => {
    const { server, port } = await createCapsServer(() => ({ body: {} }));
    const mod = await importCaps(port);
    const caps = await mod.registry();
    deepStrictEqual(caps, {});
    server.close();
  });

  it("resolve returns record for known name", async () => {
    const { server, port } = await createCapsServer(() => ({
      body: {
        capabilities: {
          "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
        },
      },
    }));
    const mod = await importCaps(port);
    const record = await mod.resolve("contacts.lookup");
    strictEqual(record.app, "contacts");
    strictEqual(record.path, "/lookup");
    server.close();
  });

  it("resolve throws CapabilityError for unknown name with available list", async () => {
    const { server, port } = await createCapsServer(() => ({
      body: {
        capabilities: {
          "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
        },
      },
    }));
    const mod = await importCaps(port);
    try {
      await mod.resolve("nonexistent.lookup");
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.CapabilityError)) throw new Error(`expected CapabilityError, got: ${err}`);
      strictEqual(err.message.includes("capability 'nonexistent.lookup' is not provided"), true);
      strictEqual(err.message.includes("contacts.lookup"), true); // lists available
    }
    server.close();
  });

  it("resolve lists '(none loaded)' when registry is empty", async () => {
    const { server, port } = await createCapsServer(() => ({ body: { capabilities: {} } }));
    const mod = await importCaps(port);
    try {
      await mod.resolve("anything");
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.CapabilityError)) throw new Error(`expected CapabilityError, got: ${err}`);
      strictEqual(err.message.includes("(none loaded)"), true);
    }
    server.close();
  });

  it("call POST builds /api/<app><path> URL and sends JSON body", async () => {
    let callUrl = "";
    let callMethod = "";
    let callBody;
    const { server, port } = await createCapsServer((req, body) => {
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
            },
          },
        };
      }
      callUrl = req.url;
      callMethod = req.method;
      callBody = body;
      return { body: { result: "ok" } };
    });
    const mod = await importCaps(port);
    const result = await mod.call("contacts.lookup", { text: "Maria" });
    strictEqual(callMethod, "POST");
    strictEqual(callUrl, "/api/contacts/lookup");
    deepStrictEqual(callBody, { text: "Maria" });
    deepStrictEqual(result, { result: "ok" });
    server.close();
  });

  it("call GET builds URL with query params", async () => {
    let callUrl = "";
    const { server, port } = await createCapsServer((req, body) => {
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "GET", path: "/lookup", description: "d" },
            },
          },
        };
      }
      callUrl = req.url;
      return { body: { result: "ok" } };
    });
    const mod = await importCaps(port);
    const result = await mod.call("contacts.lookup", { text: "Maria", limit: 5 });
    ok(callUrl.includes("text=Maria"), `URL should have text param: ${callUrl}`);
    ok(callUrl.includes("limit=5"), `URL should have limit param: ${callUrl}`);
    strictEqual(callUrl.startsWith("/api/contacts/lookup?"), true);
    deepStrictEqual(result, { result: "ok" });
    server.close();
  });

  it("call throws CapabilityError on non-2xx from target app", async () => {
    const { server, port } = await createCapsServer((req) => {
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
            },
          },
        };
      }
      return { status: 500, body: JSON.stringify({ error: "boom" }) };
    });
    const mod = await importCaps(port);
    try {
      await mod.call("contacts.lookup", {});
      throw new Error("should have thrown");
    } catch (err) {
      if (!(err instanceof mod.CapabilityError)) throw new Error(`expected CapabilityError, got: ${err}`);
      strictEqual(err.message.includes("failed"), true);
      strictEqual(err.message.includes("HTTP 500"), true);
      strictEqual(err.message.includes("boom"), true);
    }
    server.close();
  });

  it("call returns raw text when response is not valid JSON", async () => {
    const { server, port } = await createCapsServer((req, body) => {
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
            },
          },
        };
      }
      return {
        status: 200,
        headers: { "content-type": "text/plain" },
        body: "plain text response",
      };
    });
    const mod = await importCaps(port);
    const result = await mod.call("contacts.lookup", {});
    strictEqual(result, "plain text response");
    server.close();
  });

  it("call returns parsed JSON when response is JSON", async () => {
    const { server, port } = await createCapsServer((req, body) => {
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
            },
          },
        };
      }
      return { body: { phone: "+123", name: "Maria" } };
    });
    const mod = await importCaps(port);
    const result = await mod.call("contacts.lookup", { text: "Maria" });
    deepStrictEqual(result, { phone: "+123", name: "Maria" });
    server.close();
  });

  it("invalidateCache forces a new registry fetch", async () => {
    let requestCount = 0;
    const { server, port } = await createCapsServer(() => {
      requestCount++;
      return {
        body: {
          capabilities: {
            "contacts.lookup": { app: "contacts", method: "POST", path: `/v${requestCount}`, description: "d" },
          },
        },
      };
    });
    const mod = await importCaps(port);
    const r1 = await mod.resolve("contacts.lookup");
    strictEqual(r1.path, "/v1");
    strictEqual(requestCount, 1);

    mod.invalidateCache();
    const r2 = await mod.resolve("contacts.lookup");
    strictEqual(r2.path, "/v2");
    strictEqual(requestCount, 2);
    server.close();
  });

  it("call with default method POST when method is empty string", async () => {
    let callMethod = "";
    const { server, port } = await createCapsServer((req, body) => {
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "", path: "/lookup", description: "d" },
            },
          },
        };
      }
      callMethod = req.method;
      return { body: { ok: true } };
    });
    const mod = await importCaps(port);
    await mod.call("contacts.lookup", {});
    strictEqual(callMethod, "POST");
    server.close();
  });

  it("no Authorization header is sent (loopback trust by design)", async () => {
    let authHeader = null;
    const { server, port } = await createCapsServer((req) => {
      authHeader = req.headers["authorization"] ?? null;
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
            },
          },
        };
      }
      return { body: { ok: true } };
    });
    const mod = await importCaps(port);
    await mod.call("contacts.lookup", {});
    strictEqual(authHeader, null, "SDK should not send Authorization header on loopback");
    server.close();
  });

  it("URL construction: registry at /api/capabilities, call at /api/<app><path>", async () => {
    let urls = [];
    const { server, port } = await createCapsServer((req) => {
      urls.push(req.url);
      if (req.url === "/api/capabilities") {
        return {
          body: {
            capabilities: {
              "contacts.lookup": { app: "contacts", method: "POST", path: "/lookup", description: "d" },
            },
          },
        };
      }
      return { body: { ok: true } };
    });
    const mod = await importCaps(port);
    await mod.call("contacts.lookup", {});
    strictEqual(urls[0], "/api/capabilities");
    strictEqual(urls[1], "/api/contacts/lookup");
    server.close();
  });
});
