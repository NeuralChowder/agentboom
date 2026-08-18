/**
 * @agentboom/sdk — the Node/TypeScript bridge SDK.
 *
 * One rule: shared logic lives ONCE, in the platform gateway (Python).
 * This package is a thin loopback-HTTP client onto it, so TypeScript
 * mini-apps get full platform support without duplicating any logic.
 *
 *   import { db, llm, agent, capabilities } from "@agentboom/sdk";
 *
 *   const rows = await db.fetchAll("SELECT * FROM things WHERE x = ?", 1);
 *   const text = await llm.complete("Summarise this...");
 *   const answer = await agent.ask("What failed last night?");
 *   const result = await capabilities.call("contacts.lookup", { text: "Maria" });
 *
 * Scheduling (cron) is owned by the gateway's scheduler for ALL mini-apps:
 * declare jobs in your .miniapp.json, serve the target endpoint — no cron
 * parsing needed in Node.
 */
export * as db from "./db.js";
export * as llm from "./llm.js";
export * as agent from "./agent.js";
export * as capabilities from "./capabilities.js";
export {
  env,
  envInt,
  envFloat,
  envBool,
  requireEnv,
} from "./config.js";
