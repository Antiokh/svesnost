import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve('dist');
const read = (url) => {
  const file = path.join(root, url.replace(/^\//, ''), 'index.html');
  assert.ok(existsSync(file), 'Missing page ' + url + ': ' + file);
  return readFileSync(file, 'utf8');
};

for (const [url, lang] of [['/', 'sr'], ['/ru/', 'ru'], ['/en/', 'en']]) {
  const html = read(url);
  assert.match(html, new RegExp('<html[^>]+lang="' + lang + '"'), url + ' wrong html language');
  assert.ok(html.includes('data-pagefind-body'), url + ' should be indexed');
  assert.ok(html.includes('data-sves-mobile-toolbar'), url + ' splash must provide mobile controls');
  assert.match(html, /<details[^>]*data-sves-language-mobile/, url + ' must use no-JS mobile language links');
  assert.ok(html.includes('data-sves-theme-mobile'), url + ' lacks mobile theme switch');
}
for (const lang of ['sr', 'ru', 'en']) {
  const prefix = lang === 'sr' ? '' : '/' + lang;
  const html = read(prefix + '/materials/');
  assert.match(html, new RegExp('<html[^>]+lang="' + lang + '"'));
  assert.match(html, /<details[^>]*data-sves-language-mobile/, prefix + '/materials/ must use no-JS mobile language links');
  assert.ok(html.includes('data-sves-theme-mobile'), prefix + '/materials/ lacks menu theme control');
}

const original = read('/materials/izvori/');
assert.ok(original.includes('data-pagefind-body'), 'Serbian-only article must be indexed');
assert.ok(original.includes('href="/ru/"'), 'Untranslated Serbian article must switch to RU home');
assert.ok(original.includes('href="/en/"'), 'Untranslated Serbian article must switch to EN home');

for (const lang of ['ru', 'en']) {
  const url = '/' + lang + '/materials/izvori/';
  const fallback = read(url);
  assert.ok(fallback.includes('noindex'), url + ' must have noindex');
  assert.ok(!fallback.includes('data-pagefind-body'), url + ' must be excluded from Pagefind');
  const listing = read('/' + lang + '/materials/');
  assert.ok(!listing.includes('href="' + url + '"'), url + ' leaked into localized menu');
}
const policy = read('/editorial-policy/');
assert.ok(policy.includes('data-panel-toggle="sidebar"'), 'Article lacks desktop sidebar control');
assert.ok(policy.includes('data-panel-toggle="toc"'), 'Article lacks desktop ToC control');
assert.ok(existsSync(path.join(root, 'pagefind')), 'Missing Pagefind index');
console.log('Language routing, navigation and search exclusions: OK');
