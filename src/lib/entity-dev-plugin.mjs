// Rebuild canonical derived data when authored Starlight docs change.
// No design/UI or permanent database. Production builds run build:data first.
import path from 'node:path';
import { buildEntityData } from '../../scripts/build-entities.mjs';

const docsRoot = path.resolve('src/content/docs');
const supported = /\.(?:markdown|mdown|mkdn|mkd|mdwn|md|mdx)$/i;
const insideDocs = (file) => {
  const relative = path.relative(docsRoot, path.resolve(file));
  return relative && !relative.startsWith('..') && !path.isAbsolute(relative) &&
    supported.test(relative) && !path.basename(relative).startsWith('_');
};

export function entityDataDevPlugin() {
  return {
    name: 'svesnost-entity-data-dev',
    apply: 'serve',
    configureServer(server) {
      server.watcher.add(docsRoot);
      let queue = Promise.resolve();
      let scheduled;
      const regenerate = (file) => {
        if (!insideDocs(file)) return;
        clearTimeout(scheduled);
        scheduled = setTimeout(() => {
          queue = queue.catch(() => {}).then(async () => {
            try {
              await buildEntityData();
              // Existing Starlight pages need to be refreshed to show changed
              // backlinks, participant lists and map coordinates.
              server.ws.send({type:'full-reload'});
            } catch (error) {
              server.config.logger.error('Entity projection update failed: ' + String(error));
              // Keep the previously validated projection rather than publishing
              // a partly edited index while the author fixes the frontmatter.
            }
          });
        }, 120);
      };
      server.watcher.on('add', regenerate);
      server.watcher.on('change', regenerate);
      server.watcher.on('unlink', regenerate);
      server.httpServer?.on('close', () => clearTimeout(scheduled));
    },
  };
}
