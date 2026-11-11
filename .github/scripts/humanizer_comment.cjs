"use strict";

/** Only our workflow bot may own the report which the workflow updates. */
function findOwnedReviewComment(comments, marker) {
  return comments.find(
    (comment) =>
      comment?.user?.login === "github-actions[bot]" &&
      typeof comment.body === "string" &&
      comment.body.includes(marker)
  );
}

/** Refuse an obsolete synchronize run that outlived its cancellation. */
function isCurrentReviewHead(eventHead, currentHead) {
  return (
    typeof eventHead === "string" &&
    eventHead.length > 0 &&
    eventHead === currentHead
  );
}

/** The review belongs to both a particular PR head and a particular base. */
function isCurrentReviewTarget(eventPr, currentPr) {
  return (
    isCurrentReviewHead(eventPr?.head?.sha, currentPr?.head?.sha) &&
    typeof eventPr?.base?.ref === "string" &&
    eventPr.base.ref.length > 0 &&
    eventPr.base.ref === currentPr?.base?.ref &&
    typeof eventPr?.base?.sha === "string" &&
    eventPr.base.sha.length > 0 &&
    eventPr.base.sha === currentPr?.base?.sha
  );
}

module.exports = { findOwnedReviewComment, isCurrentReviewHead, isCurrentReviewTarget };
