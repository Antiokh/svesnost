"use strict";

const assert = require("node:assert/strict");
const { findOwnedReviewComment, isCurrentReviewHead } = require("./humanizer_comment.cjs");
const fs = require("node:fs");
const path = require("node:path");

const marker = "<!-- svesnost-humanizer-review -->";
const spoofed = {
  id: 12,
  user: { login: "contributor" },
  body: marker + "\nFake report",
};
const anotherBot = {
  id: 13,
  user: { login: "other-bot[bot]" },
  body: marker + "\nUnrelated report",
};
const ours = {
  id: 14,
  user: { login: "github-actions[bot]" },
  body: marker + "\nVerified report",
};
const otherOwn = {
  id: 15,
  user: { login: "github-actions[bot]" },
  body: "Other workflow output",
};

assert.equal(findOwnedReviewComment([spoofed, anotherBot, ours], marker), ours);
assert.equal(findOwnedReviewComment([spoofed, anotherBot], marker), undefined);
assert.equal(findOwnedReviewComment([otherOwn, spoofed], marker), undefined);
assert.equal(findOwnedReviewComment([otherOwn, ours], marker), ours);
assert.equal(findOwnedReviewComment([{ id: 16, body: marker }], marker), undefined);

// Two quick synchronize events: only the most recent head may publish.
const oldHead = "a".repeat(40);
const newHead = "b".repeat(40);
assert.equal(isCurrentReviewHead(oldHead, newHead), false);
assert.equal(isCurrentReviewHead(newHead, newHead), true);
assert.equal(isCurrentReviewHead("", newHead), false);
assert.equal(isCurrentReviewHead(undefined, newHead), false);

// Concurrency is keyed by PR number, not by SHA or branch. Different pushes
// on the same PR therefore cancel the previous run, without blocking others.
const workflow = fs.readFileSync(
  path.join(__dirname, "../workflows/humanizer-review.yml"),
  "utf8"
);
assert.ok(workflow.includes("group: humanizer-review-" + "$" + "{{ github.event.pull_request.number }}"));
assert.ok(workflow.includes("cancel-in-progress: true"));
assert.ok(workflow.includes("isCurrentReviewHead("));
assert.ok(workflow.includes("file.previous_filename"));
assert.ok(workflow.includes("JSON.stringify({"));

console.log("Humanizer bot ownership and sequential synchronize safety: OK");
