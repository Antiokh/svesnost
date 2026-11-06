/*
 * After the Astro/Starlight build, prove each projected entity URL is an
 * actual generated page containing the same stable entity ID.
 * This catches disagreements with docsLoader slug normalization and overrides.
 */
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';

const data = JSON.parse(readFileSync('public/generated/entity-index.json', 'utf8'));
let checked = 0;
const urls = new Set();
for (const [locale, edition] of Object.entries(data.locales)) {
  for (const record of edition.records) {
    const url = record.url;
    assert.match(url, /^\/[\p{L}\p{N}_.\/-]*\/$/u, record.id + ' has an invalid route: ' + url);
    assert.ok(!url.includes('..') && !url.includes('//'), record.id + ' has unsafe route');
    assert.ok(!urls.has(url), 'Duplicate projected route: ' + url);
    urls.add(url);
    const expectedPrefix = locale === 'sr' ? '/' : '/' + locale + '/';
    assert.ok(url.startsWith(expectedPrefix), record.id + ' crosses locale: ' + url);
    const htmlPath = path.resolve('dist', url.slice(1), 'index.html');
    assert.ok(existsSync(htmlPath), record.id + ' route missing in Astro output: ' + url);
    const html = readFileSync(htmlPath, 'utf8');
    assert.ok(html.includes('data-entity-id="' + record.id + '"'),
      record.id + ' route points at a different page (or fallback): ' + url);
    checked++;
  }
}
console.log('Canonical Starlight entity routes verified:', checked);
