"use strict";

const assert = require("node:assert/strict");
const { findOwnedReviewComment, isCurrentReviewHead, isCurrentReviewTarget } = require("./humanizer_comment.cjs");
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

// A review is tied to both refs. Retargeting or advancing the base without a
// new head commit must never make the previous comparison look current.
const expectedPr = {
  state: "open",
  head: { sha: newHead },
  base: { ref: "main", sha: "c".repeat(40) },
};
const changedRef = { head: expectedPr.head, base: { ...expectedPr.base, ref: "release" } };
const changedSha = { head: expectedPr.head, base: { ...expectedPr.base, sha: "d".repeat(40) } };
const changedHead = { head: { sha: oldHead }, base: expectedPr.base };
assert.equal(isCurrentReviewTarget(expectedPr, expectedPr), true);
assert.equal(isCurrentReviewTarget(expectedPr, changedRef), false);
assert.equal(isCurrentReviewTarget(expectedPr, changedSha), false);
assert.equal(isCurrentReviewTarget(expectedPr, changedHead), false);
assert.equal(isCurrentReviewTarget(expectedPr, { head: expectedPr.head }), false);
assert.equal(isCurrentReviewTarget({ head: expectedPr.head, base: { ref: "main", sha: "" } }, expectedPr), false);


// Content review runs are serialized by PR number. Metadata-only edits
// get isolated groups, so they cannot cancel a content review.
const workflow = fs.readFileSync(
  path.join(__dirname, "../workflows/humanizer-review.yml"),
  "utf8"
);
// Metadata-only edits get an independent concurrency group and skip the job.
// Edits to the PR base must still run the review and supersede older results.
assert.ok(workflow.includes("format('humanizer-review-{0}', github.event_name == 'workflow_dispatch' && inputs.pr_number || github.event.pull_request.number)"));
assert.ok(workflow.includes("humanizer-review-metadata-{0}-{1}"));
assert.ok(workflow.includes("github.event.action == 'edited' && !github.event.changes.base"));
assert.ok(workflow.includes("github.event.action != 'edited' || github.event.changes.base != null"));
assert.ok(workflow.includes("cancel-in-progress: true"));
assert.ok(workflow.includes("isCurrentReviewTarget("));
assert.ok(workflow.includes("TRUSTED_READY: ${{ steps.trusted.outputs.ready }}"));
assert.ok(workflow.includes("steps.trusted.outputs.ready == 'false'"));
assert.ok(workflow.includes("file.previous_filename"));
assert.ok(workflow.includes("JSON.stringify({"));
// Transient snapshot-fetch failures are retried; terminal failures publish
// an unavailable report instead of silently retaining a stale PR comment.
assert.ok(workflow.includes("id: snapshot"));
assert.ok(workflow.includes("for attempt in 1 2 3"));
assert.ok(workflow.includes("trap report_unavailable ERR"));
// Every stage, including both checkouts, must invalidate a stale report.
// The first integration PR intentionally has no trusted adapter, but only
// successful checkouts may use that first-PR exception.
assert.ok(workflow.includes("id: checkout_base"));
assert.ok(workflow.includes("id: checkout_head"));
assert.ok(workflow.includes("steps.checkout_base.outcome != 'success'"));
assert.ok(workflow.includes("steps.checkout_head.outcome != 'success'"));
assert.ok(workflow.includes("fs.existsSync(helperPath)"));
assert.ok(workflow.includes("item?.user?.login === 'github-actions[bot]'"));
// Every stage must be considered when deciding whether the existing report
// still represents this PR HEAD. A failure must replace the old report.
for (const fragment of [
  "id: selection",
  "id: editors",
  "id: finalize",
  "ADAPTER_READY: ${{ steps.trusted.outputs.ready }}",
  "BASE_CHECKOUT_OUTCOME: ${{ steps.checkout_base.outcome }}",
  "HEAD_CHECKOUT_OUTCOME: ${{ steps.checkout_head.outcome }}",
  "SNAPSHOT_OUTCOME: ${{ steps.snapshot.outcome }}",
  "SELECTION_OUTCOME: ${{ steps.selection.outcome }}",
  "EDITORS_OUTCOME: ${{ steps.editors.outcome }}",
  "GENERATE_OUTCOME: ${{ steps.generate.outcome }}",
  '[ "$ADAPTER_READY" = false ]',
  '[ "$BASE_CHECKOUT_OUTCOME" != success ]',
  '[ "$HEAD_CHECKOUT_OUTCOME" != success ]',
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
assert.ok(workflow.includes("workflow_dispatch:"));
assert.ok(workflow.includes("steps.pr.outputs.base_sha"));
assert.ok(workflow.includes("steps.pr.outputs.head_sha"));
assert.ok(workflow.includes("steps.pr.outputs.active == 'true'"));
assert.ok(workflow.includes("steps.pr.outputs.target"));
assert.ok(workflow.includes("steps.pr.outputs.author != 'dependabot[bot]'"));

// The base checkout can fail before the helper exists. The workflow must
// contain a trusted, standalone fallback rather than relying on base files.
assert.ok(workflow.includes("minimal trusted inline recovery code"));
assert.ok(workflow.includes("isCurrentReviewTarget = (expected, actual)"));
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
  BASE_CHECKOUT_OUTCOME: "success",
  HEAD_CHECKOUT_OUTCOME: "success",
  SNAPSHOT_OUTCOME: "success",
  SELECTION_OUTCOME: "success",
  EDITORS_OUTCOME: "success",
  GENERATE_OUTCOME: "success",
};
function runFinalizer(outcomes) {
  const output = spawnSync("bash", ["-e", "-c", finalizer], {
    env: { ...process.env, ...successfulStages, ADAPTER_READY: "true", ...outcomes, REPORT_PATH: reportPath },
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
  // A retarget to a base without an adapter invalidates the old report even
  // though the skipped language-editor steps are not failures.
  fs.writeFileSync(reportPath, initialReport);
  const withoutAdapter = runFinalizer({
    ADAPTER_READY: "false",
    SNAPSHOT_OUTCOME: "skipped",
    SELECTION_OUTCOME: "skipped",
    EDITORS_OUTCOME: "skipped",
    GENERATE_OUTCOME: "skipped",
  });
  assert.ok(withoutAdapter.includes("в текущей базовой ветке нет доверенного адаптера"));
  assert.ok(!withoutAdapter.includes("Previous successful review"));

  // A missing output file with otherwise successful stages is also invalid.
  fs.unlinkSync(reportPath);
  assert.ok(runFinalizer({}).includes("Проверка недоступна"));
} finally {
  fs.rmSync(dir, { recursive: true, force: true });
}

// Exercise the actual GitHub Script publisher, not just string fragments.
// This covers first integration, a retarget with an existing bot comment,
// and ref changes between pagination and the final write.
const AsyncFunction = (async function () {}).constructor;
const publishIndex = lines.findIndex(line => line.includes("- name: Update PR review comment"));
const publishScriptIndex = lines.findIndex((line, index) =>
  index > publishIndex && line === "          script: |"
);
assert.ok(publishIndex >= 0 && publishScriptIndex > publishIndex);
const publisherSource = lines.slice(publishScriptIndex + 1)
  .map(line => line.startsWith("            ") ? line.slice(12) : line)
  .join("\n");
const runPublisher = new AsyncFunction("github", "context", "core", "require", "process", publisherSource);

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "humanizer-publisher-"));
  try {
    const report = path.join(temp, "comment.md");
    const workspace = path.join(temp, "workspace");
    const helperFile = path.join(workspace, "base/.github/scripts/humanizer_comment.cjs");
    fs.mkdirSync(path.dirname(helperFile), { recursive: true });
    fs.copyFileSync(path.join(__dirname, "humanizer_comment.cjs"), helperFile);
    const untrustedWorkspace = path.join(temp, "no-helper");
    fs.mkdirSync(untrustedWorkspace);
    fs.writeFileSync(report, marker + "\nПроверка недоступна: в текущей базовой ветке нет доверенного адаптера Humanizer.\n");

    async function publish({ first = expectedPr, second = expectedPr,
                             comments = [], ready = "true", helper = true } = {}) {
      const writes = [];
      let gets = 0;
      const github = {
        rest: {
          pulls: { get: async () => ({ data: (gets++ === 0 ? first : second) }) },
          issues: {
            listComments: async () => comments,
            createComment: async args => { writes.push({ kind: "create", args }); },
            updateComment: async args => { writes.push({ kind: "update", args }); },
          },
        },
        paginate: async () => comments,
      };
      const context = {
        repo: { owner: "Antiokh", repo: "svesnost" },
        issue: { number: 4 },
        payload: { pull_request: expectedPr },
      };
      const core = { info: () => {} };
      const processMock = {
        env: {
          REPORT_PATH: report,
          GITHUB_WORKSPACE: helper ? workspace : untrustedWorkspace,
          TRUSTED_READY: ready,
          REVIEW_PR_NUMBER: "4",
          REVIEW_TARGET: JSON.stringify(expectedPr),
        },
      };
      await runPublisher(github, context, core, require, processMock);
      return writes;
    }

    // Normal review uses the trusted helper and creates its first report.
    let writes = await publish();
    assert.deepEqual(writes.map(w => w.kind), ["create"]);
    // Changed base ref prevents publication even though HEAD is unchanged.
    writes = await publish({ first: changedRef });
    assert.equal(writes.length, 0);
    // A base SHA update during pagination also prevents a stale write;
    // this path exercises the standalone trusted workflow fallback.
    writes = await publish({ second: changedSha, helper: false });
    assert.equal(writes.length, 0);
    writes = await publish({ second: { ...expectedPr, state: "closed" } });
    assert.equal(writes.length, 0);
    // Missing base adapter only updates an existing owned report; it must not
    // create a comment on a first-integration PR.
    writes = await publish({ comments: [ours], ready: "false", helper: false });
    assert.deepEqual(writes.map(w => w.kind), ["update"]);
    assert.equal(writes[0].args.comment_id, ours.id);
    assert.ok(writes[0].args.body.includes("Проверка недоступна"));
    writes = await publish({ ready: "false", helper: false });
    assert.equal(writes.length, 0);
    // A spoofed marker cannot be mistaken for an owned report.
    writes = await publish({ comments: [spoofed], ready: "false", helper: false });
    assert.equal(writes.length, 0);
    // Failed checkouts have no trusted.ready=false output, so they retain
    // the existing error-notification behavior even without prior comments.
    writes = await publish({ ready: "", helper: false });
    assert.deepEqual(writes.map(w => w.kind), ["create"]);


    // Exercise the actual live-ref resolver used by both PR and dispatch runs.
    const resolverStart = lines.findIndex(line => line.includes("- name: Resolve current PR snapshot"));
    const resolverScript = lines.findIndex((line, index) =>
      index > resolverStart && line === "          script: |"
    );
    const resolverEnd = lines.findIndex((line, index) =>
      index > resolverScript && line.startsWith("      - name: ")
    );
    assert.ok(resolverStart >= 0 && resolverScript > resolverStart && resolverEnd > resolverScript);
    const runResolver = new AsyncFunction("github", "context", "core", "process",
      lines.slice(resolverScript + 1, resolverEnd)
        .map(line => line.startsWith("            ") ? line.slice(12) : line)
        .join("\n"));
    const livePr = {
      number: 4, state: "open",
      head: { sha: newHead, repo: { full_name: "Antiokh/svesnost" } },
      base: expectedPr.base, user: { login: "contributor" },
    };
    async function resolve(eventName, pr) {
      const outputs = {};
      await runResolver(
        { rest: { pulls: { get: async args => {
          assert.equal(args.pull_number, 4);
          return { data: pr };
        } } } },
        { eventName, repo: { owner: "Antiokh", repo: "svesnost" } },
        { info: () => {}, setOutput: (key, value) => { outputs[key] = value; } },
        { env: { REVIEW_PR_NUMBER: "4" } },
      );
      return outputs;
    }
    const resolved = await resolve("workflow_dispatch", livePr);
    assert.equal(resolved.active, "true");
    assert.equal(resolved.head_sha, newHead);
    assert.equal(resolved.base_sha, expectedPr.base.sha);
    assert.deepEqual(JSON.parse(resolved.target), {
      head: { sha: newHead }, base: expectedPr.base,
    });
    assert.equal((await resolve("pull_request", livePr)).active, "true");
    for (const pr of [
      { ...livePr, state: "closed" },
      { ...livePr, base: { ...livePr.base, ref: "release" } },
      { ...livePr, head: { ...livePr.head, repo: { full_name: "other/fork" } } },
      { ...livePr, user: { login: "dependabot[bot]" } },
    ]) {
      assert.equal((await resolve("workflow_dispatch", pr)).active, "false");
    }

    // Exercise the new main-push dispatcher with filtered open PRs.
    const refreshWorkflow = fs.readFileSync(
      path.join(__dirname, "../workflows/humanizer-base-refresh.yml"), "utf8"
    );
    assert.ok(refreshWorkflow.includes("branches: [main]"));
    assert.ok(refreshWorkflow.includes("actions: write"));
    assert.ok(refreshWorkflow.includes("cancel-in-progress: true"));
    const refreshLines = refreshWorkflow.split("\n");
    const refreshStart = refreshLines.findIndex(line =>
      line.includes("- name: Recheck open same-repository PRs targeting main")
    );
    const refreshScript = refreshLines.findIndex((line, index) =>
      index > refreshStart && line === "          script: |"
    );
    assert.ok(refreshScript > refreshStart);
    const dispatch = new AsyncFunction("github", "context", "core",
      refreshLines.slice(refreshScript + 1)
        .map(line => line.startsWith("            ") ? line.slice(12) : line)
        .join("\n"));
    const dispatched = [];
    await dispatch({
      paginate: async (_, args) => {
        assert.equal(args.state, "open");
        assert.equal(args.base, "main");
        return [
          livePr,
          { ...livePr, number: 5, base: { ...livePr.base, ref: "release" } },
          { ...livePr, number: 6, head: { ...livePr.head, repo: { full_name: "other/fork" } } },
          { ...livePr, number: 7, user: { login: "dependabot[bot]" } },
          { ...livePr, number: 8, state: "closed" },
          { ...livePr, number: 9 },
        ];
      },
      rest: { pulls: { list: () => {} }, actions: {
        createWorkflowDispatch: async args => { dispatched.push(args); },
      } },
    }, { repo: { owner: "Antiokh", repo: "svesnost" } }, { info: () => {} });
    assert.deepEqual(dispatched.map(item => item.inputs.pr_number), ["4", "9"]);
    for (const args of dispatched) {
      assert.equal(args.ref, "main");
      assert.equal(args.workflow_id, "humanizer-review.yml");
    }
    console.log("Main update dispatch and current PR ref regression: OK");

    console.log("Humanizer retarget and PR publication regressions: OK");
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

