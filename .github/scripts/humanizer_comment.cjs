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

module.exports = { findOwnedReviewComment, isCurrentReviewHead };
