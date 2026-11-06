import assert from 'node:assert/strict';
import { z } from 'astro/zod';
import { entityFields, ENTITY_KINDS, SOURCE_TYPES, MEDIA_TYPES } from '../src/lib/entity-schema.mjs';
import { validateEntityCatalog } from '../src/lib/entity-catalog.mjs';

const schema = entityFields(() => z.string());
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
console.log('Entity schema, canonical IDs, evidence/media and references: OK');
