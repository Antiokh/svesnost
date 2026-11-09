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
// Every stage must be considered when deciding whether the existing report
// still represents this PR HEAD. A failure must replace the old report.
for (const fragment of [
  "id: selection",
  "id: editors",
  "id: finalize",
  "SNAPSHOT_OUTCOME: ${{ steps.snapshot.outcome }}",
  "SELECTION_OUTCOME: ${{ steps.selection.outcome }}",
  "EDITORS_OUTCOME: ${{ steps.editors.outcome }}",
  "GENERATE_OUTCOME: ${{ steps.generate.outcome }}",
  '[ "$SNAPSHOT_OUTCOME" != success ]',
  '[ "$SELECTION_OUTCOME" != success ]',
  '[ "$EDITORS_OUTCOME" != success ]',
  '[ "$GENERATE_OUTCOME" != success ]',
  '[ ! -s "$REPORT_PATH" ]',
  "steps.finalize.outcome == 'success'",
  "Проверка недоступна: один из этапов проверки завершился ошибкой.",
]) {
  assert.ok(workflow.includes(fragment), "Missing failure-path guard: " + fragment);
}
assert.ok(workflow.includes("always() &&"));

// Execute the exact finalizer shell body with an isolated report path.
// This checks both normal preservation and invalidation for each failed stage.
const os = require("node:os");
const { spawnSync } = require("node:child_process");
const lines = workflow.split("\n");
const finalize = lines.findIndex((line) =>
  line.includes("- name: Finalize report, including failures in any review stage")
);
assert.ok(finalize >= 0);
const runStart = lines.findIndex((line, i) => i > finalize && line === "        run: |");
const runEnd = lines.findIndex((line, i) => i > runStart && line.startsWith("      - name: "));
assert.ok(runStart > finalize && runEnd > runStart);
const finalizer = lines.slice(runStart + 1, runEnd)
  .map((line) => line.startsWith("          ") ? line.slice(10) : line)
  .join("\n");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "humanizer-finalize-"));
const reportPath = path.join(dir, "report.md");
const initialReport = marker + "\nPrevious successful review\n";
const successfulStages = {
  SNAPSHOT_OUTCOME: "success",
  SELECTION_OUTCOME: "success",
  EDITORS_OUTCOME: "success",
  GENERATE_OUTCOME: "success",
};
function runFinalizer(outcomes) {
  const output = spawnSync("bash", ["-e", "-c", finalizer], {
    env: { ...process.env, ...successfulStages, ...outcomes, REPORT_PATH: reportPath },
    encoding: "utf8",
  });
  assert.equal(output.status, 0, output.stderr);
  return fs.readFileSync(reportPath, "utf8");
}
try {
  fs.writeFileSync(reportPath, initialReport);
  assert.equal(runFinalizer({}), initialReport, "A successful report must stay unchanged");
  for (const stage of Object.keys(successfulStages)) {
    fs.writeFileSync(reportPath, initialReport);
    const unavailable = runFinalizer({ [stage]: "failure" });
    assert.ok(unavailable.startsWith(marker));
    assert.ok(unavailable.includes("Проверка недоступна"), stage);
    assert.ok(!unavailable.includes("Previous successful review"), stage);
  }
  // A missing output file with otherwise successful stages is also invalid.
  fs.unlinkSync(reportPath);
  assert.ok(runFinalizer({}).includes("Проверка недоступна"));
} finally {
  fs.rmSync(dir, { recursive: true, force: true });
}

console.log("Humanizer ownership, synchronization and failed-stage reports: OK");
