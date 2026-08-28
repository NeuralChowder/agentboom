import { strictEqual } from "node:assert";
import { describe, it } from "node:test";

describe("index — exports and surface API", () => {
  it("exports db namespace with all functions", async () => {
    const sdk = await import("../dist/index.js?v=e1");
    strictEqual(typeof sdk.db.execute, "function");
    strictEqual(typeof sdk.db.fetchOne, "function");
    strictEqual(typeof sdk.db.fetchRow, "function");
    strictEqual(typeof sdk.db.fetchAll, "function");
    strictEqual(typeof sdk.db.fetchVal, "function");
    strictEqual(typeof sdk.db.batch, "function");
  });

  it("exports llm namespace with all functions", async () => {
    const sdk = await import("../dist/index.js?v=e2");
    strictEqual(typeof sdk.llm.complete, "function");
    strictEqual(typeof sdk.llm.completeJson, "function");
  });

  it("exports agent namespace with all functions", async () => {
    const sdk = await import("../dist/index.js?v=e3");
    strictEqual(typeof sdk.agent.ask, "function");
    strictEqual(typeof sdk.agent.askJson, "function");
  });

  it("exports capabilities namespace with all functions", async () => {
    const sdk = await import("../dist/index.js?v=e4");
    strictEqual(typeof sdk.capabilities.call, "function");
    strictEqual(typeof sdk.capabilities.resolve, "function");
    strictEqual(typeof sdk.capabilities.registry, "function");
    strictEqual(typeof sdk.capabilities.invalidateCache, "function");
  });

  it("exports config helpers", async () => {
    const sdk = await import("../dist/index.js?v=e5");
    strictEqual(typeof sdk.env, "function");
    strictEqual(typeof sdk.envInt, "function");
    strictEqual(typeof sdk.envFloat, "function");
    strictEqual(typeof sdk.envBool, "function");
    strictEqual(typeof sdk.requireEnv, "function");
  });

  it("exports error classes", async () => {
    const sdk = await import("../dist/index.js?v=e6");
    strictEqual(typeof sdk.db.DbError, "function");
    strictEqual(typeof sdk.llm.LLMError, "function");
    strictEqual(typeof sdk.capabilities.CapabilityError, "function");
  });

  it("each namespace exposes PLATFORM_INTERNAL_URL constant", async () => {
    const sdk = await import("../dist/index.js?v=e7");
    // These will be the default URL since env var isn't set
    strictEqual(sdk.db.PLATFORM_INTERNAL_URL, "http://127.0.0.1:8000");
    strictEqual(sdk.llm.PLATFORM_INTERNAL_URL, "http://127.0.0.1:8000");
    strictEqual(sdk.agent.PLATFORM_INTERNAL_URL, "http://127.0.0.1:8000");
    strictEqual(sdk.capabilities.PLATFORM_INTERNAL_URL, "http://127.0.0.1:8000");
  });
});
