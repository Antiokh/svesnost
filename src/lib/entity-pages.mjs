/*
 * Server-only generated index reader. No network requests from article layouts.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { localeFor } from './entity-projections.mjs';
const dataPath = path.resolve('public/generated/entity-index.json');
let index;
export function getEntityPageData(route) {
  if (!index) index = JSON.parse(readFileSync(dataPath, 'utf8'));
  const locale = localeFor(route.id);
  const current = index.locales[locale];
  if (!current || !route.entry?.data?.entityId) return null;
  const byId = new Map(current.records.map((item)=>[item.id,item]));
  const item = byId.get(route.entry.data.entityId);
  if (!item) return null;
  const from = (references) => [...new Set(references ?? [])].map((id) => byId.get(id)).filter(Boolean);
  const i = current.indexes;
  const sectionRefs = item.entity === 'person'
    ? {
      events: i.byPersonEvents[item.id],
      relations: i.byPersonRelations[item.id],
      places: i.byPersonPlaces[item.id],
      evidence: [...(i.bySubjectEvidence[item.id] ?? []), ...(item.evidenceRefs ?? [])],
      media: [...(i.byPersonMedia[item.id] ?? []), ...(item.mediaRefs ?? [])],
    }
    : item.entity === 'place'
      ? {
        events: i.byPlaceEvents[item.id],
        people: current.records.filter((r) => r.entity === 'person' && (i.byPersonPlaces[r.id] ?? []).includes(item.id)).map((r)=>r.id),
        evidence: [...(i.bySubjectEvidence[item.id] ?? []), ...(item.evidenceRefs ?? [])],
        media: [...(i.byPlaceMedia[item.id] ?? []), ...(item.mediaRefs ?? [])],
      }
      : item.entity === 'event'
        ? {
          people: (item.participants ?? []).map((p)=>typeof p==='string'?p:p.person),
          places: item.places ?? [],
          evidence: [...(i.bySubjectEvidence[item.id] ?? []), ...(item.evidenceRefs ?? [])],
          media: [...(i.byEventMedia[item.id] ?? []), ...(item.mediaRefs ?? [])],
        }
        : {};
  // An explicit source list is available on any structured page.
  if (item.sourceRefs?.length) sectionRefs.sources = item.sourceRefs;
  const sections = Object.entries(sectionRefs).map(([key, refs])=>({key,items:from(refs)})).filter((s)=>s.items.length);
  return {item,locale,sections};
}
