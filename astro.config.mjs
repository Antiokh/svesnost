import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { entityDataDevPlugin } from './src/lib/entity-dev-plugin.mjs';

export default defineConfig({
  vite: { plugins: [entityDataDevPlugin()] },
  integrations: [
    starlight({
      title: { sr: 'Свесност', ru: 'Свесност', en: 'Svesnost' },
      description: 'Енциклопедија политике и друштва.',
      locales: {
        root: { label: 'Српски (ћирилица)', lang: 'sr' },
        ru: { label: 'Русский', lang: 'ru' },
        en: { label: 'English', lang: 'en' },
      },
      routeMiddleware: './src/route-middleware.ts',
      components: {
        Header: './src/components/Header.astro',
        LanguageSelect: './src/components/LanguageSelect.astro',
        ThemeSelect: './src/components/ThemeSelect.astro',
        PageTitle: './src/components/EntityPageTitle.astro',
        MarkdownContent: './src/components/EntityMarkdownContent.astro',
      },
      customCss: ['./src/styles/theme.css'],
      logo: { src: './src/assets/logo.svg', alt: 'Свесност' },
      head: [
        { tag: 'link', attrs: { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' } },
      ],
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/Antiokh/svesnost' },
      ],
      editLink: { baseUrl: 'https://github.com/Antiokh/svesnost/edit/main/' },
      lastUpdated: true,
      // Each language has its own pages, navigation and Pagefind search index.
      sidebar: [{ autogenerate: { directory: '' } }],
    }),
  ],
});
