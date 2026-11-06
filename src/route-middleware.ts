import { getCollection } from 'astro:content';
import { defineRouteMiddleware } from '@astrojs/starlight/route-data';
import { validateEntityCatalog } from './lib/entity-catalog.mjs';
import { getEntityPageData } from './lib/entity-pages.mjs';
import { labels } from './lib/entity-display.mjs';

/** Routes belonging to real authored pages, never Starlight's generated fallbacks. */
const authoredDocs = await getCollection('docs', ({ data }) => !data.draft);
validateEntityCatalog(authoredDocs);
const published = new Set(
  authoredDocs.map(({ id }) => {
    const path = id === 'index' ? '' : id;
    return path ? '/' + path + '/' : '/';
  }),
);

const normalize = (url: string) => {
  const pathname = new URL(url, 'https://svesnost.invalid').pathname;
  return pathname.endsWith('/') ? pathname : pathname + '/';
};

type NavEntry = {
  type: 'link' | 'group';
  href?: string;
  entries?: NavEntry[];
};

function onlyPublished<T extends NavEntry>(entries: T[]): T[] {
  return entries.flatMap((entry) => {
    if (entry.type === 'link') return entry.href && published.has(normalize(entry.href)) ? [entry] : [];
    const children = onlyPublished(entry.entries ?? []);
    return children.length ? [{ ...entry, entries: children } as T] : [];
  });
}

export const onRequest = defineRouteMiddleware((context) => {
  const route = context.locals.starlightRoute;
  route.sidebar = onlyPublished(route.sidebar);
  if (route.pagination.prev && !published.has(normalize(route.pagination.prev.href))) {
    route.pagination.prev = undefined;
  }
  if (route.pagination.next && !published.has(normalize(route.pagination.next.href))) {
    route.pagination.next = undefined;
  }
  if (!route.isFallback && route.toc && route.entry.data.entityId) {
    const entityPage = getEntityPageData(route);
    if (entityPage?.sections.length) {
      route.toc = {
        ...route.toc,
        items: [
          ...route.toc.items,
          ...entityPage.sections.map(({ key }) => ({
            depth: 2,
            slug: 'entity-' + key,
            text: labels[entityPage.locale][key],
            children: [],
          })),
        ],
      };
    }
  }
  if (route.isFallback) {
    // Keep Starlight's fallback URLs out of search engines and the Pagefind index.
    // No fallback page is linked from the menu or the language switch.
    route.entry.data.pagefind = false;
    route.head.push({ tag: 'meta', attrs: { name: 'robots', content: 'noindex, nofollow' } });
  }
});
