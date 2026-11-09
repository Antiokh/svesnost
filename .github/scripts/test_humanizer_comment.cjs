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

// Content review runs are serialized by PR number. Metadata-only edits
// get isolated groups, so they cannot cancel a content review.
const workflow = fs.readFileSync(
  path.join(__dirname, "../workflows/humanizer-review.yml"),
  "utf8"
);
// Metadata-only edits get an independent concurrency group and skip the job.
// Edits to the PR base must still run the review and supersede older results.
assert.ok(workflow.includes("format('humanizer-review-{0}', github.event.pull_request.number)"));
assert.ok(workflow.includes("humanizer-review-metadata-{0}-{1}"));
assert.ok(workflow.includes("github.event.action == 'edited' && !github.event.changes.base"));
assert.ok(workflow.includes("github.event.action != 'edited' || github.event.changes.base != null"));
assert.ok(workflow.includes("cancel-in-progress: true"));
assert.ok(workflow.includes("isCurrentReviewHead("));
assert.ok(workflow.includes("file.previous_filename"));
assert.ok(workflow.includes("JSON.stringify({"));
// Transient snapshot-fetch failures are retried; terminal failures publish
// an unavailable report instead of silently retaining a stale PR comment.
assert.ok(workflow.includes("id: snapshot"));
assert.ok(workflow.includes("for attempt in 1 2 3"));
assert.ok(workflow.includes("trap report_unavailable ERR"));
assert.ok(workflow.includes("steps.snapshot.outcome == 'failure'"));
assert.ok(workflow.includes("steps.generate.outcome == 'success'"));
assert.ok(workflow.includes("always() &&"));

console.log("Humanizer bot ownership and sequential synchronize safety: OK");
