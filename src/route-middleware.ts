import { getCollection } from 'astro:content';
import { defineRouteMiddleware } from '@astrojs/starlight/route-data';

/** Routes belonging to real authored pages, never Starlight's generated fallbacks. */
const published = new Set(
  (await getCollection('docs', ({ data }) => !data.draft)).map(({ id }) => {
    const path = id === 'index' ? '' : id.replace(/\\/g, '/');
    return path ? '/' + path.replace(/^\\/g, '').replace(/\\/$/, '') + '/' : '/';
  }),
);

const normalize = (url: string) => {
  const pathname = new URL(url, 'https://svesnost.invalid').pathname;
  return pathname === '/' ? '/' : pathname.replace(/\\/$/, '') + '/';
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
  if (route.isFallback) {
    // Keep Starlight's fallback URLs out of search engines and the Pagefind index.
    // No fallback page is linked from the menu or the language switch.
    route.entry.data.pagefind = false;
    route.head.push({ tag: 'meta', attrs: { name: 'robots', content: 'noindex, nofollow' } });
  }
});
