/*
 * Rebuild derived editorial views from canonical Markdown records.
 * No invented person-to-person edges from co-participation; no URL guesses for translations.
 */
import { validateEntityCatalog } from './entity-catalog.mjs';

export const localeFor = (id) => (id === 'ru' || id.startsWith('ru/')) ? 'ru' : (id === 'en' || id.startsWith('en/')) ? 'en' : 'sr';
export function pathForId(id) {
  // id is the canonical Astro/Starlight docsLoader entry ID, including custom slug.
  const parts = id.replace(/\/index$/, '').replace(/^index$/, '').replace(/^\/+|\/+$/g, '').normalize();
  return '/' + (parts ? parts + '/' : '');
}
export function dateSortKey(value) {
  if (typeof value === 'number') return value * 10000;
  if (typeof value === 'string') {
    const match = /^(-?\d{1,6})(?:-(\d{2}))?(?:-(\d{2}))?$/.exec(value);
    if (match) return Number(match[1]) * 10000 + Number(match[2] || 0) * 100 + Number(match[3] || 0);
    return Number.POSITIVE_INFINITY;
  }
  if (value && typeof value === 'object') {
    if (typeof value.year === 'number') return value.year * 10000 + (value.month || 0) * 100 + (value.day || 0);
    return Number.POSITIVE_INFINITY;
  }
  return Number.POSITIVE_INFINITY;
}
const uniq = (xs) => [...new Set(xs)].sort();
const participantRef = (p) => typeof p === 'string' ? p : p.person;
const refsOf = (data) => [
  ...(data.sourceRefs ?? []), ...(data.evidenceRefs ?? []), ...(data.mediaRefs ?? []),
];
function makeIndex(items) {
  const index = {};
  for (const [ref, value] of items) {
    if (!ref) continue;
    index[ref] ??= [];
    index[ref].push(value);
  }
  for (const key of Object.keys(index)) index[key] = uniq(index[key]);
  return Object.fromEntries(Object.entries(index).sort(([a],[b]) => a.localeCompare(b)));
}

export function buildEntityProjections(entries) {
  validateEntityCatalog(entries);
  const sorted = [...entries].filter((x) => x.data.entity && !x.data.draft)
    .sort((a,b) => a.id.localeCompare(b.id));
  const locales = {};
  const features = [];
  for (const locale of ['sr', 'ru', 'en']) {
    const rows = sorted.filter((entry) => localeFor(entry.id) === locale)
      .map(({ id, data }) => ({
        id: data.entityId,
        entity: data.entity,
        url: pathForId(id),
        title: data.title || data.name?.display || data.entityId,
        description: data.description || null,
        ...Object.fromEntries(Object.entries(data).filter(([key]) => key !== 'title' && key !== 'description' && key !== 'slug')),
      }));
    const byId = Object.fromEntries(rows.map((r)=>[r.id,r]));
    const perType = (type) => rows.filter((r)=>r.entity===type);
    const people = perType('person'), events = perType('event'), places = perType('place');
    const byPersonEvents = [], byPlaceEvents = [], byPersonRelations = [], byPersonPlaces = [];
    const byEventMedia = [], byPersonMedia = [], byPlaceMedia = [];
    const bySubjectEvidence = [], bySourceEvidence = [];

    for (const event of events) {
      for (const p of event.participants ?? []) {
        const ref = participantRef(p);
        if (byId[ref]) byPersonEvents.push([ref, event.id]);
      }
      for (const placeRef of event.places ?? []) {
        const place = byId[placeRef];
        if (!place) continue;
        byPlaceEvents.push([placeRef, event.id]);
        if (!place.geo) continue;
        features.push({
          type: 'Feature',
          id: `${locale}:${event.id}:${placeRef}`,
          geometry: {type: 'Point', coordinates: [place.geo.longitude, place.geo.latitude]},
          properties: {
            locale, eventId: event.id, eventUrl: event.url, eventTitle: event.title,
            placeId: placeRef, placeUrl: place.url, placeTitle: place.title,
            date: event.date ?? null, endDate: event.endDate ?? null,
            precision: place.geo.precision ?? null,
            participants: (event.participants ?? []).map(participantRef),
          },
        });
      }
    }
    for (const person of people) {
      for (const relation of person.relations ?? []) {
        if (byId[relation.target]) {
          byPersonRelations.push([person.id, relation.target]);
          byPersonRelations.push([relation.target, person.id]);
        }
      }
      for (const occurrence of [person.born, person.died]) {
        if (occurrence?.place && byId[occurrence.place]) byPersonPlaces.push([person.id, occurrence.place]);
      }
      for (const residence of person.residences ?? []) {
        if (byId[residence.place]) byPersonPlaces.push([person.id, residence.place]);
      }
    }
    for (const media of perType('media')) {
      for (const personRef of media.subjects ?? []) if (byId[personRef]) byPersonMedia.push([personRef, media.id]);
      for (const eventRef of media.eventRefs ?? []) if (byId[eventRef]) byEventMedia.push([eventRef, media.id]);
      for (const placeRef of media.placeRefs ?? []) if (byId[placeRef]) byPlaceMedia.push([placeRef, media.id]);
    }
    for (const evidence of perType('evidence')) {
      for (const subject of evidence.subjectRefs ?? []) if (byId[subject]) bySubjectEvidence.push([subject, evidence.id]);
      for (const source of evidence.sourceRefs ?? []) if (byId[source]) bySourceEvidence.push([source, evidence.id]);
    }
    const indexes = {
      byPersonEvents: makeIndex(byPersonEvents),
      byPlaceEvents: makeIndex(byPlaceEvents),
      byPersonRelations: makeIndex(byPersonRelations),
      byPersonPlaces: makeIndex(byPersonPlaces),
      byPersonMedia: makeIndex(byPersonMedia),
      byPlaceMedia: makeIndex(byPlaceMedia),
      byEventMedia: makeIndex(byEventMedia),
      bySubjectEvidence: makeIndex(bySubjectEvidence),
      bySourceEvidence: makeIndex(bySourceEvidence),
    };
    // Authored event-place edges become reverse person-place links, not arbitrary person-person edges.
    for (const [person, eventRefs] of Object.entries(indexes.byPersonEvents)) {
      for (const eventId of eventRefs) {
        const event = byId[eventId];
        for (const placeId of event?.places ?? []) {
          if (byId[placeId]) byPersonPlaces.push([person, placeId]);
        }
      }
    }
    indexes.byPersonPlaces = makeIndex(byPersonPlaces);
    locales[locale] = { records: rows, indexes };
  }
  features.sort((a,b) => a.id.localeCompare(b.id));
  return {
    schema:'svesnost-entity-index-1',
    locales,
    geojson: {type:'FeatureCollection',features},
  };
}
