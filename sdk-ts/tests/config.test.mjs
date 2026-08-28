import { strictEqual, throws } from "node:assert";
import { describe, it } from "node:test";
import {
  env,
  envInt,
  envFloat,
  envBool,
  requireEnv,
} from "../dist/config.js";

describe("config — env parsing helpers", () => {
  describe("env()", () => {
    it("returns fallback when var is absent", () => {
      strictEqual(env("__NONEXISTENT_CONFIG_XYZ"), "");
    });

    it("returns fallback when var is empty string", () => {
      process.env.__TEST_EMPTY_ENV = "";
      strictEqual(env("__TEST_EMPTY_ENV", "default"), "default");
    });

    it("returns the value when set", () => {
      process.env.__TEST_VAL_ENV = "hello";
      strictEqual(env("__TEST_VAL_ENV", "default"), "hello");
    });
  });

  describe("envInt()", () => {
    it("returns fallback when var is absent", () => {
      strictEqual(envInt("__NONEXISTENT_CONFIG_INT_XYZ", 42), 42);
    });

    it("parses valid integer", () => {
      process.env.__TEST_INT_ENV = "99";
      strictEqual(envInt("__TEST_INT_ENV", 0), 99);
    });

    it("returns fallback for non-numeric string", () => {
      process.env.__TEST_INT_BAD = "abc";
      strictEqual(envInt("__TEST_INT_BAD", 10), 10);
    });
  });

  describe("envFloat()", () => {
    it("returns fallback when var is absent", () => {
      strictEqual(envFloat("__NONEXISTENT_CONFIG_FLOAT_XYZ", 3.14), 3.14);
    });

    it("parses valid float", () => {
      process.env.__TEST_FLOAT_ENV = "2.5";
      strictEqual(envFloat("__TEST_FLOAT_ENV", 0), 2.5);
    });

    it("parses integer-like float", () => {
      process.env.__TEST_FLOAT_INT = "7";
      strictEqual(envFloat("__TEST_FLOAT_INT", 0), 7);
    });
  });

  describe("envBool()", () => {
    it("returns fallback when var is absent", () => {
      strictEqual(envBool("__NONEXISTENT_CONFIG_BOOL_XYZ", false), false);
    });

    it("returns true for truthy values", () => {
      for (const v of ["1", "true", "True", "yes", "YES", "on", "ON"]) {
        process.env.__TEST_BOOL_ENV = v;
        strictEqual(envBool("__TEST_BOOL_ENV", false), true, `truthy: ${v}`);
      }
    });

    it("returns false for falsy values", () => {
      for (const v of ["0", "false", "False", "no", "off", "OFF", "random"]) {
        process.env.__TEST_BOOL_ENV = v;
        strictEqual(envBool("__TEST_BOOL_ENV", true), false, `falsy: ${v}`);
      }
    });
  });

  describe("requireEnv()", () => {
    it("throws when var is absent", () => {
      throws(
        () => requireEnv("__NONEXISTENT_CONFIG_REQ_XYZ"),
        /required env var __NONEXISTENT_CONFIG_REQ_XYZ is not set/,
      );
    });

    it("throws when var is empty string", () => {
      process.env.__TEST_REQ_EMPTY = "";
      throws(
        () => requireEnv("__TEST_REQ_EMPTY"),
        /required env var __TEST_REQ_EMPTY is not set/,
      );
    });

    it("returns value when set", () => {
      process.env.__TEST_REQ_VAL = "secret";
      strictEqual(requireEnv("__TEST_REQ_VAL"), "secret");
    });
  });
});
