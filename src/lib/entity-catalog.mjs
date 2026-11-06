/*
 * Build-time validation for references in authored Starlight documents.
 * Entity identity is independent of name, path and language.
 * No visual graph is created here; records stay canonical in Markdown/MDX.
 */
import { ENTITY_KINDS } from './entity-schema.mjs';

const sorted = (items) => [...new Set(items)].sort();

const referencePattern = /^(person|event|place|source|evidence|media):[a-z0-9][a-z0-9._-]*$/;
const localeFor = (id) => id.startsWith('ru/') ? 'ru' : id.startsWith('en/') ? 'en' : 'sr';

export function validateEntityCatalog(entries) {
  const catalog = new Map();
  const records = [];
  const errors = [];
  for (const entry of entries) {
    const { id, data } = entry;
    if (data.draft) continue;
    if (!data.entity) {
      if (data.entityId) errors.push(`${id}: entityId requires an entity type`);
      continue;
    }
    if (!ENTITY_KINDS.includes(data.entity)) {
      errors.push(`${id}: unknown entity type ${data.entity}`);
      continue;
    }
    if (!data.entityId || !referencePattern.test(data.entityId)) {
      errors.push(`${id}: entity requires a stable kind:slug entityId`);
      continue;
    }
    if (!data.entityId.startsWith(data.entity + ':')) {
      errors.push(`${id}: entityId prefix must match entity=${data.entity}`);
      continue;
    }
    const locale = localeFor(id);
    const previous = catalog.get(data.entityId);
    if (previous?.locales.has(locale)) {
      errors.push(`${id}: duplicate ${data.entityId} in ${locale} (already ${previous.locales.get(locale)})`);
      continue;
    }
    if (previous && previous.kind !== data.entity) {
      errors.push(`${id}: entity kind changed across languages for ${data.entityId}`);
      continue;
    }
    if (previous) previous.locales.set(locale, id);
    else catalog.set(data.entityId, { kind: data.entity, locales: new Map([[locale, id]]) });
    records.push({ id, data });
  }

  const check = (owner, ref, expected) => {
    if (typeof ref !== 'string' || !referencePattern.test(ref)) {
      errors.push(`${owner}: invalid entity reference ${JSON.stringify(ref)}`);
      return;
    }
    if (expected && !ref.startsWith(expected + ':')) {
      errors.push(`${owner}: expected ${expected} reference, got ${ref}`);
    }
    if (!catalog.has(ref)) errors.push(`${owner}: unknown entity reference ${ref}`);
  };
  const checkMany = (owner, refs, kind) => {
    for (const ref of refs ?? []) check(owner, ref, kind);
  };

  for (const { id, data } of records) {
    checkMany(id, data.sourceRefs, 'source');
    checkMany(id, data.mediaRefs, 'media');
    checkMany(id, data.places, 'place');
    checkMany(id, data.eventRefs, 'event');
    checkMany(id, data.placeRefs, 'place');
    checkMany(id, data.subjects, 'person');
    if (data.parentPlace) {
      check(id, data.parentPlace, 'place');
      if (data.parentPlace === data.entityId) errors.push(`${id}: self parentPlace is not allowed`);
    }
    for (const event of [data.born, data.died]) {
      if (event?.place) check(id, event.place, 'place');
      checkMany(id, event?.sourceRefs, 'source');
    }
    for (const residence of data.residences ?? []) check(id, residence.place, 'place');
    for (const participant of data.participants ?? []) {
      check(id, typeof participant === 'string' ? participant : participant.person, 'person');
    }
    if (data.kinship) {
      if (data.entity !== 'person') errors.push(`${id}: kinship is only valid on person records`);
      checkMany(id, data.kinship.parents, 'person');
      checkMany(id, data.kinship.partners, 'person');
      for (const parent of data.kinship.parents ?? []) {
        if (parent === data.entityId) errors.push(`${id}: self kinship.parents is not allowed`);
      }
      for (const partner of data.kinship.partners ?? []) {
        if (partner === data.entityId) errors.push(`${id}: self kinship.partners is not allowed`);
      }
    }
    if (data.relations?.length && data.entity !== 'person') {
      errors.push(`${id}: relations allowed only on person records`);
    }
    for (const relation of data.relations ?? []) {
      check(id, relation.target, 'person');
      checkMany(id, relation.sourceRefs, 'source');
      if (relation.target === data.entityId) errors.push(`${id}: self-relation is not allowed`);
    }
  }

  const byLocale = new Map();
  for (const item of records) {
    const locale = localeFor(item.id);
    if (!byLocale.has(locale)) byLocale.set(locale, new Map());
    byLocale.get(locale).set(item.data.entityId, item.data);
  }
  for (const [locale, rows] of byLocale) {
    for (const [kind, nextRefs] of [
      ['person', (data) => data.kinship?.parents ?? []],
      ['place', (data) => data.parentPlace ? [data.parentPlace] : []],
    ]) {
      const visited = new Set();
      const active = new Set();
      const visit = (ref, stack) => {
        if (active.has(ref)) {
          errors.push(`${locale}: ${kind} hierarchy cycle: ${[...stack, ref].join(' -> ')}`);
          return;
        }
        if (visited.has(ref)) return;
        const data = rows.get(ref);
        if (!data || data.entity !== kind) return;
        active.add(ref);
        for (const next of sorted(nextRefs(data))) visit(next, [...stack, ref]);
        active.delete(ref);
        visited.add(ref);
      };
      for (const [ref, data] of rows) if (data.entity === kind) visit(ref, []);
    }
  }

  if (errors.length) throw new Error('Entity model validation failed:\n' + errors.map((x) => '- ' + x).join('\n'));
  return { entities: catalog.size, localizedRecords: records.length };
}
