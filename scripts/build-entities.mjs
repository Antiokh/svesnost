// Mirrors the Starlight docsLoader glob options and Astro content ID generation.
// Keep the file-set, slug and metadata validation contracts in sync with upstream.
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';
import { z } from 'astro/zod';
import { slug as githubSlug } from 'github-slugger';
import { entityFields } from '../src/lib/entity-schema.mjs';
import { checkMediaFile } from '../src/lib/entity-files.mjs';
import { buildEntityProjections, localeFor } from '../src/lib/entity-projections.mjs';

// https://github.com/withastro/starlight/blob/main/packages/starlight/src/loaders.ts
const docsExtension = /\.(?:markdown|mdown|mkdn|mkd|mdwn|md|mdx)$/i;
const stripExtension = /\.(?:markdown|mdown|mkdn|mkd|mdwn|md|mdx)$/i;
// Astro image() resolves image assets during content parsing. The raw YAML
// preflight uses an equivalent *path* shape and checkMediaFile() checks disk.
const rawImage = () => z.string().regex(
  /^\.\/(?!.*(?:\/\.\.?\/|\\\\))[a-z0-9_./-]+\.(?:jpe?g|png|gif|webp|avif|svg)$/i,
  'Image path must be a local file',
);
const rawEntitySchema = entityFields(rawImage);

export async function discoverDocs(root) {
  async function walk(dir) {
    const found = [];
    for (const item of await readdir(dir, { withFileTypes: true })) {
      const file = path.join(dir, item.name);
      if (item.isDirectory()) found.push(...await walk(file));
      // docsLoader: **/[^_]*.{markdown,mdown,mkdn,mkd,mdwn,md,mdx}
      else if (item.isFile() && !item.name.startsWith('_') && docsExtension.test(item.name)) {
        found.push(file);
      }
    }
    return found;
  }
  return (await walk(root)).sort();
}

export function validatedEntityFrontmatter(raw, filename) {
  if (typeof raw.title !== 'string' || !raw.title.trim()) {
    throw new Error(filename + ': expected non-empty frontmatter title');
  }
  if (raw.description !== undefined && typeof raw.description !== 'string') {
    throw new Error(filename + ': description must be a string');
  }
  const result = rawEntitySchema.safeParse(raw);
  if (!result.success) {
    throw new Error(filename + ': invalid structured entity metadata:\n' +
      result.error.issues.map((issue) => issue.path.join('.') + ': ' + issue.message).join('\n'));
  }
  // Project only known, validated fields. Never carry unknown raw YAML (or
  // reserved id/url) into public JSON. Schema strips unknown top-level keys.
  return { title: raw.title, ...(raw.description !== undefined ? { description: raw.description } : {}), ...result.data };
}

function frontmatter(content, filename) {
  const match = /^---\s*\r?\n([\s\S]*?)\r?\n---(?:\s*\r?\n|$)/.exec(content);
  if (!match) throw new Error('Missing YAML frontmatter: ' + filename);
  const raw = YAML.parse(match[1]) ?? {};
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('Invalid YAML frontmatter: ' + filename);
  }
  return raw;
}

export async function buildEntityData({ docsRoot = path.resolve('src/content/docs'),
  outputRoot = path.resolve('public/generated') } = {}) {
  const entries = [];
  for (const filename of await discoverDocs(docsRoot)) {
    const relative = path.relative(docsRoot, filename).split(path.sep).join('/');
    const raw = frontmatter(await readFile(filename, 'utf8'), filename);
    if (raw.draft) continue;
    // Astro glob loader reads 'slug' BEFORE schema validation.
    const fileSlug = relative.replace(stripExtension, '').split('/').map((part) => githubSlug(part))
      .join('/').replace(/\/index$/, '');
    if (raw.slug !== undefined && (typeof raw.slug !== 'string' ||
      !/^[\p{L}\p{N}_./-]+$/u.test(raw.slug) || raw.slug.includes('..') ||
      raw.slug.startsWith('/') || raw.slug.endsWith('/') || raw.slug.includes('//'))) {
      throw new Error(filename + ': invalid custom slug');
    }
    const id = raw.slug || fileSlug;
    if (localeFor(id) !== localeFor(relative)) {
      throw new Error(filename + ': custom slug crosses the source language boundary (' + id + ')');
    }
    if (entries.some((entry) => entry.id === id)) {
      throw new Error(filename + ': duplicate Starlight route slug ' + id);
    }
    if (raw.entity === 'media') checkMediaFile(raw.file, filename, docsRoot);
    // Ordinary articles are not projected; structural articles require schema.
    const data = raw.entity ? validatedEntityFrontmatter(raw, filename) : raw;
    entries.push({ id, data });
  }
  const output = buildEntityProjections(entries);
  await mkdir(outputRoot, { recursive: true });
  const writeJSON = (name, data) => writeFile(path.join(outputRoot, name),
    JSON.stringify(data, null, 2) + '\n');
  await Promise.all([
    writeJSON('entity-index.json', {schema:output.schema,locales:output.locales}),
    writeJSON('events.json', {schema:'svesnost-events-1',locales:Object.fromEntries(
      Object.entries(output.locales).map(([locale, edition])=>[locale,edition.records.filter((r)=>r.entity==='event')]))}),
    writeJSON('places.json', {schema:'svesnost-places-1',locales:Object.fromEntries(
      Object.entries(output.locales).map(([locale, edition])=>[locale,edition.records.filter((r)=>r.entity==='place')]))}),
    writeJSON('person-event-index.json', {schema:'svesnost-person-event-1',locales:Object.fromEntries(
      Object.entries(output.locales).map(([locale, edition])=>[locale,edition.indexes.byPersonEvents]))}),
    writeJSON('places.geojson', output.geojson),
  ]);
  console.log('Entity projections:',Object.entries(output.locales).map(([l,v])=>l+'='+v.records.length)
    .join(', '),'mapped events:',output.geojson.features.length);
  return output;
}

// Executing this file directly is the CLI entry point. Importing the builder
// in Astro's development watcher must not trigger an uncontrolled rebuild.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildEntityData();
}
