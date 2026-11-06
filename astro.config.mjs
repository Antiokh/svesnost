import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  integrations: [
    starlight({
      title: 'Svesnost',
      description: 'Материалы о политике и общественной жизни.',
      locales: {
        root: { label: 'Русский', lang: 'ru' },
      },
      customCss: ['./src/styles/theme.css'],
      logo: {
        src: './src/assets/logo.svg',
        alt: 'Svesnost',
      },
      head: [
        {
          tag: 'link',
          attrs: { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' },
        },
      ],
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/Antiokh/svesnost',
        },
      ],
      editLink: {
        baseUrl: 'https://github.com/Antiokh/svesnost/edit/main/',
      },
      lastUpdated: true,
      sidebar: [
        { label: 'Главная', link: '/' },
        {
          label: 'Материалы',
          items: [{ autogenerate: { directory: 'materials' } }],
        },
        { label: 'О проекте', link: '/about/' },
      ],
    }),
  ],
});
