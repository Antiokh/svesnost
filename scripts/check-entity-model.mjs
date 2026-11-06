import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { checkMediaFile } from '../src/lib/entity-files.mjs';
import { buildEntityProjections } from '../src/lib/entity-projections.mjs';
import { buildEntityData, discoverDocs } from './build-entities.mjs';
import { z } from 'astro/zod';
import { entityFields, ENTITY_KINDS, SOURCE_TYPES, MEDIA_TYPES } from '../src/lib/entity-schema.mjs';
import { validateEntityCatalog } from '../src/lib/entity-catalog.mjs';

const schema = entityFields(() => z.object({ src: z.string(), width: z.number(), height: z.number() }));
const entry = (id, entity, entityId, data = {}) => ({ id, data: { entity, entityId, ...data } });
assert.deepEqual(ENTITY_KINDS, ['person', 'event', 'place', 'source', 'evidence', 'media']);
assert.ok(SOURCE_TYPES.includes('rumor'));
assert.ok(MEDIA_TYPES.includes('video') && MEDIA_TYPES.includes('note'));

// Same display name can legitimately refer to multiple identities.
const records = [
  entry('people/alex/index', 'person', 'person:alex', {
    name: { display: 'Alex', aliases: ['King Alex'] },
    born: { date: { year: -320, circa: true }, place: 'place:city' },
    relations: [{ target: 'person:other-alex', kind: 'rival', sourceRefs: ['source:chronicle'] }],
    kinship: { parents: ['person:other-alex'], dynasty: 'Example dynasty' },
  }),
  entry('people/other/index', 'person', 'person:other-alex', { name: 'Alex' }),
  entry('ru/people/alex/index', 'person', 'person:alex', { name: 'Алекс' }),
  entry('places/city/index', 'place', 'place:city', {
    geo: { latitude: 44.8, longitude: 20.4, precision: 'city' },
  }),
  entry('events/congress/index', 'event', 'event:congress', {
    participants: [{ person: 'person:alex', role: 'attendee' }],
    places: ['place:city'],
    date: { year: 1918, month: 12 },
  }),
  entry('sources/chronicle/index', 'source', 'source:chronicle', {
    sourceType: 'publication',
  }),
  entry('evidence/claim/index', 'evidence', 'evidence:claim', {
    claim: 'Synthetic example', assessment: 'disputed',
    sourceRefs: ['source:chronicle'], mediaRefs: ['media:video'],
  }),
  entry('media/video/index', 'media', 'media:video', {
    mediaType: 'video', externalUrl: 'https://example.org/video',
    subjects: ['person:alex'], eventRefs: ['event:congress'],
    sourceRefs: ['source:chronicle'],
  }),
];
for (const record of records) {
  assert.ok(schema.safeParse(record.data).success, record.id + ' must pass metadata schema');
}
assert.deepEqual(validateEntityCatalog(records), { entities: 7, localizedRecords: 8 });
assert.throws(() => validateEntityCatalog([...records, entry('people/duplicate', 'person', 'person:alex')]), /duplicate person:alex in sr/);
assert.throws(() => validateEntityCatalog([...records, entry('events/bad', 'event', 'event:bad', { participants: ['person:missing'] })]), /unknown entity reference person:missing/);
assert.throws(() => validateEntityCatalog([...records, entry('people/self', 'person', 'person:self', { relations: [{ target: 'person:self', kind: 'ally' }] })]), /self-relation/);
assert.throws(() => validateEntityCatalog([...records, entry('media/bad', 'media', 'media:bad', { subjects: ['place:city'] })]), /expected person reference/);
assert.equal(schema.safeParse({ entity: 'place', entityId: 'place:invalid', geo: { latitude: 400, longitude: 20 } }).success, false);
assert.equal(schema.safeParse({ entity: 'media', entityId: 'media:scan', mediaType: 'scan' }).success, true);
for (const forbidden of [
  { entity: 'media', entityId: 'media:x', born: { date: 1930 } },
  { entity: 'media', entityId: 'media:x', relations: [{ target: 'person:alex', kind: 'rival' }] },
  { entity: 'evidence', entityId: 'evidence:x', participants: ['person:alex'] },
  { entity: 'person', entityId: 'person:x', geo: { latitude: 0, longitude: 0 } },
]) assert.equal(schema.safeParse(forbidden).success, false, 'entity-specific field must be rejected');
assert.equal(schema.safeParse({ entity: 'media', entityId: 'media:bad', file: '' }).success, false);
assert.equal(schema.safeParse({ entity: 'media', entityId: 'media:bad', file: '../escape.pdf' }).success, false);
assert.equal(schema.safeParse({ entity: 'media', entityId: 'media:ok', file: './evidence.pdf' }).success, true);
assert.throws(() => validateEntityCatalog([...records, entry('people/self-parent', 'person', 'person:self-parent', { kinship: { parents: ['person:self-parent'] } })]), /self kinship.parents/);
assert.throws(() => validateEntityCatalog([...records, entry('people/self-partner', 'person', 'person:self-partner', { kinship: { partners: ['person:self-partner'] } })]), /self kinship.partners/);
assert.throws(() => validateEntityCatalog([...records, entry('places/self', 'place', 'place:self', { parentPlace: 'place:self' })]), /self parentPlace/);
assert.throws(() => validateEntityCatalog([
  ...records,
  entry('people/a', 'person', 'person:a', { kinship: { parents: ['person:b'] } }),
  entry('people/b', 'person', 'person:b', { kinship: { parents: ['person:a'] } }),
]), /person hierarchy cycle/);
assert.throws(() => validateEntityCatalog([
  ...records,
  entry('places/a', 'place', 'place:a', { parentPlace: 'place:b' }),
  entry('places/b', 'place', 'place:b', { parentPlace: 'place:a' }),
]), /place hierarchy cycle/);
assert.throws(() => validateEntityCatalog([
  ...records,
  entry('people/rival', 'person', 'person:rival', { relations: [{ target: 'media:video', kind: 'rival' }] }),
]), /expected person reference/);

// Build-derived views and multilingual isolation, including a georeferenced event.
const derived = buildEntityProjections(records);
assert.equal(derived.locales.sr.records.length, 7);
assert.equal(derived.locales.ru.records.length, 1);
assert.deepEqual(derived.locales.sr.indexes.byPersonEvents['person:alex'], ['event:congress']);
assert.deepEqual(derived.locales.sr.indexes.byPlaceEvents['place:city'], ['event:congress']);
assert.deepEqual(derived.locales.sr.indexes.byPersonPlaces['person:alex'], ['place:city']);
assert.deepEqual(derived.locales.sr.indexes.byPersonRelations['person:alex'], ['person:other-alex']);
assert.equal(derived.locales.ru.indexes.byPersonEvents['person:alex'], undefined);
assert.equal(derived.geojson.features.length, 1);
assert.equal(derived.geojson.features[0].properties.locale, 'sr');
assert.equal(derived.geojson.features[0].properties.eventUrl, '/events/congress/');
assert.deepEqual(derived.geojson.features[0].geometry.coordinates, [20.4,44.8]);
const withEvidence = [...records, entry('evidence/other/index','evidence','evidence:other',{
  subjectRefs:['event:congress','person:alex'], sourceRefs:['source:chronicle'],
})];
const withEvidenceModel = buildEntityProjections(withEvidence);
assert.deepEqual(withEvidenceModel.locales.sr.indexes.bySubjectEvidence['event:congress'], ['evidence:other']);
assert.deepEqual(withEvidenceModel.locales.sr.indexes.bySourceEvidence['source:chronicle'], ['evidence:claim','evidence:other']);
assert.throws(() => validateEntityCatalog([...records,entry('evidence/bad-subject','evidence','evidence:bad-subject',{
  subjectRefs:['media:video'],
})]), /evidence subject must be person, event or place/);
assert.throws(() => validateEntityCatalog([...records,entry('people/bad-evidence','person','person:bad-evidence',{
  evidenceRefs:['evidence:missing'],
})]), /unknown entity reference evidence:missing/);

const dir = mkdtempSync(path.join(tmpdir(),'sves-entity-assets-'));
try {
  const recordDir = path.join(dir,'media');
  mkdirSync(recordDir);
  const document = path.join(recordDir,'fixture.md');
  writeFileSync(document,'fixture');
  writeFileSync(path.join(recordDir,'real.pdf'),'%PDF-1.0 fixture');
  checkMediaFile('./real.pdf',document,dir);
  assert.throws(() => checkMediaFile('./does-not-exist.pdf',document,dir), /does not exist/);
  assert.throws(() => checkMediaFile('./does-not-exist.png',document,dir), /does not exist/);
  assert.throws(() => checkMediaFile('../escape.pdf',document,dir), /must be a safe local/);
  assert.throws(() => checkMediaFile('https://example.org/remote.pdf',document,dir), /must be a safe local/);
  assert.throws(() => checkMediaFile('',document,dir), /must be a safe local/);
} finally {
  rmSync(dir,{recursive:true,force:true});
}
const work = mkdtempSync(path.join(tmpdir(),'sves-model-input-'));
try {
  const docsRoot = path.join(work,'docs');
  const outputRoot = path.join(work,'generated');
  const folder = path.join(docsRoot,'places');
  mkdirSync(folder,{recursive:true});
  writeFileSync(path.join(folder,'_snippet.md'),'not a Starlight document');
  const document = path.join(folder,'Example.markdown');
  const valid = [
    '---','title: Example','entity: place','entityId: place:example',
    'url: /alternative/','id: person:other',
    'geo:','  latitude: 44','  longitude: 20','---','Example body',
  ].join('\n');
  writeFileSync(document,valid);
  assert.deepEqual((await discoverDocs(docsRoot)).map(x=>path.basename(x)),['Example.markdown']);
  let projection = await buildEntityData({docsRoot,outputRoot});
  assert.equal(projection.locales.sr.records[0].url,'/places/example/');
  assert.equal(projection.locales.sr.records[0].id,'place:example');
  const saved = readFileSync(path.join(outputRoot,'entity-index.json'),'utf8');
  writeFileSync(document,valid.replace('latitude: 44','latitude: "oops"'));
  await assert.rejects(buildEntityData({docsRoot,outputRoot}), /geo.latitude/);
  assert.equal(readFileSync(path.join(outputRoot,'entity-index.json'),'utf8'),saved);
  writeFileSync(document,valid);
  writeFileSync(path.join(folder,'Second.mdown'),[
    '---','title: Second','entity: place','entityId: place:second','---','Content',
  ].join('\n'));
  projection = await buildEntityData({docsRoot,outputRoot});
  assert.equal(projection.locales.sr.records.length,2);
} finally {
  rmSync(work,{recursive:true,force:true});
}
console.log('Entity model, loader parity and raw metadata validation: OK');
