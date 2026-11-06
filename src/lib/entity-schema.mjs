/*
 * Structured editorial content model, adapted from genealogy-engine.
 * No renderer, genealogy tree, image-centric person requirements or theme.
 */
import { z } from 'astro/zod';

export const ENTITY_KINDS = ['person', 'event', 'place', 'source', 'evidence', 'media'];
export const ASSESSMENTS = ['confirmed', 'limited', 'disputed', 'unverified'];
export const SOURCE_TYPES = ['document', 'publication', 'testimony', 'rumor'];
export const MEDIA_TYPES = ['note', 'photo', 'scan', 'document', 'clipping', 'audio', 'video', 'other'];

const ref = z.string().regex(
  /^(person|event|place|source|evidence|media):[a-z0-9][a-z0-9._-]*$/,
  'Use a stable kind:slug identifier, not a display name or localized URL',
);
const date = z.union([
  z.number().int(), // negative years support BCE history
  z.string().min(1),
  z.object({
    year: z.number().int().optional(),
    month: z.number().int().min(1).max(12).optional(),
    day: z.number().int().min(1).max(31).optional(),
    circa: z.boolean().optional(),
    before: z.boolean().optional(),
    after: z.boolean().optional(),
    text: z.string().optional(),
  }),
]);
const referenceList = z.array(ref);
const name = z.object({
  display: z.string().optional(),
  first: z.string().optional(),
  middle: z.string().optional(),
  last: z.string().optional(),
  patronymic: z.string().optional(),
  aliases: z.array(z.string()).optional(),
  titles: z.array(z.string()).optional(),
});
const occurrence = z.object({
  date: date.optional(),
  place: ref.optional(),
  sourceRefs: referenceList.optional(),
});
const period = z.object({ from: date.optional(), to: date.optional() });
const relation = z.object({
  target: ref,
  kind: z.string().min(1), // political ally, rival, party affiliation, kinship, etc.
  role: z.string().optional(),
  start: date.optional(),
  end: date.optional(),
  assessment: z.enum(ASSESSMENTS).optional(),
  sourceRefs: referenceList.optional(),
  note: z.string().optional(),
  evidenceRefs: referenceList.optional(),
});
const participant = z.union([
  ref,
  z.object({ person: ref, role: z.string().optional() }),
]);

// Local images are resolved by Astro image(); non-image files must be local
// relative paths and are checked on disk by the build-data pipeline.
const localDocument = z.string().regex(
  /^\.\/(?!.*(?:\/\.\.?\/|\\\\))[a-z0-9_./-]+\.(?:pdf|txt|csv|json|mp3|ogg|wav|mp4|webm|mov|zip)$/i,
  'Use an existing relative document/media file; external resources use externalUrl',
);
const SHARED = ['entity', 'entityId', 'sourceRefs', 'mediaRefs', 'evidenceRefs'];
const BY_KIND = {
  person: ['name', 'aliases', 'born', 'died', 'occupations', 'residences', 'relations', 'kinship'],
  event: ['kind', 'date', 'endDate', 'participants', 'places', 'summary'],
  place: ['historicalNames', 'geo', 'parentPlace', 'country'],
  source: ['sourceType', 'sourceUrl', 'author', 'published', 'locator'],
  evidence: ['claim', 'assessment', 'subjectRefs'],
  media: ['mediaType', 'file', 'externalUrl', 'alt', 'caption', 'subjects', 'eventRefs', 'placeRefs', 'credit', 'rights', 'license', 'recordedAt'],
};
const CUSTOM_FIELDS = new Set([...SHARED, ...Object.values(BY_KIND).flat()]);
// Pass Astro's image() helper from docsSchema to keep image validation.
export function entityFields(image) {
  return z.object({
    entity: z.enum(ENTITY_KINDS).optional(),
    entityId: ref.optional(),

    // People are identified by entityId. Same names are explicitly allowed.
    name: z.union([z.string(), name]).optional(),
    aliases: z.array(z.string()).optional(),
    born: occurrence.optional(),
    died: occurrence.optional(),
    occupations: z.array(z.string()).optional(),
    residences: z.array(z.object({ place: ref, ...period.shape })).optional(),
    relations: z.array(relation).optional(),

    // Optional dynasty/royal-family metadata, not the site's global graph.
    kinship: z.object({
      parents: referenceList.optional(),
      partners: referenceList.optional(),
      dynasty: z.string().optional(),
    }).optional(),

    // Historical events, which may include people, locations and sources.
    kind: z.string().optional(),
    date: date.optional(),
    endDate: date.optional(),
    participants: z.array(participant).optional(),
    places: referenceList.optional(),
    summary: z.string().optional(),

    // Historical places need not have a map coordinate.
    historicalNames: z.array(z.object({ name: z.string(), ...period.shape })).optional(),
    geo: z.object({
      latitude: z.number().min(-90).max(90),
      longitude: z.number().min(-180).max(180),
      precision: z.enum(['exact', 'building', 'city', 'region', 'representative']).optional(),
    }).optional(),
    parentPlace: ref.optional(),
    country: z.string().optional(),

    // A source's category never automatically establishes claim reliability.
    sourceType: z.enum(SOURCE_TYPES).optional(),
    sourceUrl: z.string().url().optional(),
    author: z.string().optional(),
    published: date.optional(),
    locator: z.string().optional(),

    // Evidence is a sourced assertion; media is a reusable artifact.
    claim: z.string().optional(),
    assessment: z.enum(ASSESSMENTS).optional(),
    sourceRefs: referenceList.optional(),
    mediaRefs: referenceList.optional(),
    evidenceRefs: referenceList.optional(),
    subjectRefs: referenceList.optional(),
    mediaType: z.enum(MEDIA_TYPES).optional(),
    file: z.union([image(), localDocument]).optional(),
    externalUrl: z.string().url().optional(),
    alt: z.string().optional(),
    caption: z.string().optional(),
    subjects: referenceList.optional(),
    eventRefs: referenceList.optional(),
    placeRefs: referenceList.optional(),
    credit: z.string().optional(),
    rights: z.string().optional(),
    license: z.string().optional(),
    recordedAt: date.optional(),
  }).superRefine((data, ctx) => {
    if (!data.entity) return; // ordinary editorial articles are unaffected
    const allowed = new Set([...SHARED, ...BY_KIND[data.entity]]);
    for (const key of CUSTOM_FIELDS) {
      if (data[key] !== undefined && !allowed.has(key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: [key],
          message: key + ' is not allowed for entity ' + data.entity,
        });
      }
    }
  });
}
