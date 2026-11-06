/*
 * Validate local media references from raw frontmatter before Astro imports them.
 * A plain string must never bypass the image() contract or escape docs/.
 */
import { existsSync, statSync } from 'node:fs';
import path from 'node:path';

const localPathPattern = /^\.\/(?!.*(?:\/\.\.?\/|\\\\))[a-z0-9_./-]+\.(?:jpe?g|png|gif|webp|avif|svg|pdf|txt|csv|json|mp3|ogg|wav|mp4|webm|mov|zip)$/i;

export function checkMediaFile(file, documentPath, docsRoot, fileExists = existsSync) {
  if (file === undefined || file === null) return;
  if (typeof file !== 'string' || !localPathPattern.test(file)) {
    throw new Error(`${documentPath}: media.file must be a safe local ./path with a supported extension`);
  }
  const root = path.resolve(docsRoot);
  const target = path.resolve(path.dirname(documentPath), file);
  if (!target.startsWith(root + path.sep)) {
    throw new Error(`${documentPath}: media.file escapes the content directory`);
  }
  if (!fileExists(target)) throw new Error(`${documentPath}: media.file does not exist: ${file}`);
  if (fileExists === existsSync && !statSync(target).isFile()) {
    throw new Error(`${documentPath}: media.file must point to a regular file: ${file}`);
  }
}
