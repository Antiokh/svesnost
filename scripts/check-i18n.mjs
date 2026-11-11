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

for (const [prefix,lang] of [['','sr'],['/ru','ru'],['/en','en']]) {
  const url = prefix + '/events/map/';
  const html = read(url);
  assert.match(html,new RegExp('<html[^>]+lang="' + lang + '"'));
  assert.ok(html.includes('data-event-map'), url + ' lacks event map component');
  assert.ok(html.includes('data-locale="' + lang + '"'), url + ' wrong map edition');
}
for (const file of ['entity-index.json','events.json','places.json','person-event-index.json','places.geojson']) {
  assert.ok(existsSync(path.join(root,'generated',file)), 'Missing generated ' + file);
}
const index = JSON.parse(readFileSync(path.join(root,'generated','entity-index.json'),'utf8'));
assert.deepEqual(Object.keys(index.locales), ['sr','ru','en']);
const geojson = JSON.parse(readFileSync(path.join(root,'generated','places.geojson'),'utf8'));
assert.equal(geojson.type,'FeatureCollection');
for(const feature of geojson.features) {
  assert.ok(index.locales[feature.properties.locale]?.records.some((record) => record.url === feature.properties.eventUrl));
}
console.log('Entity map routes and generated projections: OK');


// Every published Lukić record has three independently authored editions.
// Test the actual HTML switcher links, not just their source filenames.
const translatedLukicPaths = [
  '/persons/',
  '/persons/aleksandar-lukic/',
  '/events/brics-intro-belgrade-2024/',
  '/events/lukic-adviser-appointment-2025/',
  '/evidence/lukic-appointment-2025/',
  '/sources/ips-lukic-profile/',
  '/sources/matica-lukic-biography-2019/',
  '/sources/mit-lukic-appointment-2025/',
  '/sources/brics-lukic-2024/',
  '/sources/rts-lukic-2026/',
];
const languagePrefixes = { root: '', ru: '/ru', en: '/en' };
for (const route of translatedLukicPaths) {
  for (const [locale, prefix] of Object.entries(languagePrefixes)) {
    const pageUrl = prefix + route;
    const html = read(pageUrl);
    const htmlLang = locale === 'root' ? 'sr' : locale;
    assert.match(html, new RegExp('<html[^>]+lang="' + htmlLang + '"'), pageUrl + ' wrong language');
    assert.ok(html.includes('data-pagefind-body'), pageUrl + ' should be searchable');

    for (const [targetLocale, targetPrefix] of Object.entries(languagePrefixes)) {
      const target = targetPrefix + route;
      // These route values contain only /, hyphens and ASCII alphanumerics.
      const link = new RegExp(
        '<a\\b(?=[^>]*\\bhref="' + target + '")' +
        '(?=[^>]*\\bdata-language-target="' + targetLocale + '")[^>]*>',
        'g'
      );
      const matches = html.match(link) ?? [];
      assert.equal(matches.length, 2,
        pageUrl + ' needs desktop and mobile switch links to ' + target +
        ' (found ' + matches.length + ')');
    }
  }
}
console.log('Lukić SR/RU/EN page equivalents and switch links: OK');
