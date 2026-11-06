import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import YAML from 'yaml';
import { slug as githubSlug } from 'github-slugger';
import { localeFor } from '../src/lib/entity-projections.mjs';
import { checkMediaFile } from '../src/lib/entity-files.mjs';
import { buildEntityProjections } from '../src/lib/entity-projections.mjs';

const root = path.resolve('src/content/docs');
const out = path.resolve('public/generated');

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const results = [];
  for (const item of entries) {
    const full = path.join(dir, item.name);
    if (item.isDirectory()) results.push(...await walk(full));
    else if (item.isFile() && /\.mdx?$/i.test(item.name)) results.push(full);
  }
  return results;
}
function frontmatter(content, filename) {
  const raw = /^---\s*\r?\n([\s\S]*?)\r?\n---(?:\s*\r?\n|$)/.exec(content);
  if (!raw) throw new Error('Missing YAML frontmatter: ' + filename);
  const parsed = YAML.parse(raw[1]) ?? {};
  if (!parsed || typeof parsed !== 'object') throw new Error('Invalid YAML frontmatter: ' + filename);
  return parsed;
}
const entries = [];
for (const filename of await walk(root)) {
  const relative = path.relative(root, filename).split(path.sep).join('/');
  const data = frontmatter(await readFile(filename, 'utf8'), filename);
  if (data.draft) continue;
  // Mirrors Astro's glob loader generateIdDefault: explicit slug wins,
  // otherwise GitHub-slugger normalizes EACH file path segment and strips /index.
  const fileSlug = relative.replace(/\.mdx?$/i, '').split('/').map((part) => githubSlug(part)).join('/')
    .replace(/\/index$/, '');
  if (data.slug !== undefined && (typeof data.slug !== 'string' ||
    !/^[\p{L}\p{N}_./-]+$/u.test(data.slug) || data.slug.includes('..') ||
    data.slug.startsWith('/') || data.slug.endsWith('/') || data.slug.includes('//'))) {
    throw new Error(filename + ': invalid custom slug');
  }
  const id = data.slug || fileSlug;
  if (localeFor(id) !== localeFor(relative)) {
    throw new Error(filename + ': custom slug crosses the source language boundary (' + id + ')');
  }
  if (entries.some((entry) => entry.id === id)) {
    throw new Error(filename + ': duplicate Starlight route slug ' + id);
  }
  if (data.entity === 'media') checkMediaFile(data.file, filename, root);
  entries.push({ id, data });
}
const output = buildEntityProjections(entries);
await mkdir(out, { recursive: true });
const writeJSON = async (name, data) => writeFile(path.join(out, name), JSON.stringify(data,null,2) + '\n');
await Promise.all([
  writeJSON('entity-index.json',{schema:output.schema,locales:output.locales}),
  writeJSON('events.json',{schema:'svesnost-events-1',locales:Object.fromEntries(Object.entries(output.locales).map(([l,v])=>[l,v.records.filter((x)=>x.entity==='event')]))}),
  writeJSON('places.json',{schema:'svesnost-places-1',locales:Object.fromEntries(Object.entries(output.locales).map(([l,v])=>[l,v.records.filter((x)=>x.entity==='place')]))}),
  writeJSON('person-event-index.json',{schema:'svesnost-person-event-1',locales:Object.fromEntries(Object.entries(output.locales).map(([l,v])=>[l,v.indexes.byPersonEvents]))}),
  writeJSON('places.geojson',output.geojson),
]);
console.log('Entity projections:',Object.entries(output.locales).map(([l,v])=>l+'='+v.records.length).join(', '),'mapped events:',output.geojson.features.length);
