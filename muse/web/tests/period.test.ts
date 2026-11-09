// Run with: node --test (after `npm run demo` installs esbuild) or via tests/run.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { addMonths, mondayOf, resolvePeriod, switchKey } from "../lib/period";

const bounds = { first: "2023-01-01", last: "2026-10-31" };

test("month view", () => {
  const p = resolvePeriod("month", "2024-10", bounds);
  assert.equal(p.start, "2024-10-01");
  assert.equal(p.end, "2024-11-01");
  assert.equal(p.label, "October 2024");
  assert.equal(p.prev, "2024-09");
  assert.equal(p.next, "2024-11");
  assert.equal(p.bucket, "day");
});

test("no next beyond the history, no prev before it", () => {
  assert.equal(resolvePeriod("month", "2026-10", bounds).next, null);
  assert.equal(resolvePeriod("month", "2023-01", bounds).prev, null);
  assert.equal(resolvePeriod("year", "2026", bounds).next, null);
});

test("weeks start on Monday, whatever day is given", () => {
  assert.equal(mondayOf("2024-10-10"), "2024-10-07");
  const p = resolvePeriod("week", "2024-10-10", bounds);
  assert.equal(p.key, "2024-10-07");
  assert.equal(p.end, "2024-10-14");
  assert.equal(p.label, "Week of 7 October 2024");
});

test("day view across a month end and a leap day", () => {
  assert.equal(resolvePeriod("day", "2024-02-29", bounds).next, "2024-03-01");
  assert.equal(resolvePeriod("day", "2024-03-01", bounds).prev, "2024-02-29");
  assert.equal(resolvePeriod("day", "2024-02-29", bounds).label, "Thursday 29 February 2024");
});

test("bad or missing input falls back to the latest month", () => {
  for (const [v, k] of [[undefined, undefined], ["month", "2024-13"], ["day", "2023-02-30"], ["nonsense", "x"]]) {
    const p = resolvePeriod(v, k, bounds);
    assert.equal(p.view === "day" ? p.key.slice(0, 7) : p.key, p.view === "day" ? "2026-10" : "2026-10", `${v} ${k}`);
  }
});

test("switching view keeps you at the same time", () => {
  const month = resolvePeriod("month", "2024-10", bounds);
  assert.equal(switchKey(month, "year", bounds), "2024");
  assert.equal(switchKey(month, "week", bounds), "2024-09-30");
  assert.equal(switchKey(month, "day", bounds), "2024-10-01");
  assert.equal(switchKey(resolvePeriod("all", undefined, bounds), "month", bounds), "2026-10");
});

test("December rolls into January", () => {
  assert.equal(addMonths("2024-12-01", 1), "2025-01-01");
  assert.equal(resolvePeriod("month", "2024-12", bounds).next, "2025-01");
});
