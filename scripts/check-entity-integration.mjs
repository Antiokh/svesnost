/*
 * CI-only end-to-end fixture. Synthetic authored records are created temporarily,
 * never committed or deployed. Remove them in finally even on a build failure.
 */
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, rmSync, readFileSync, writeFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const root=path.resolve('src/content/docs');
const docs=[
  ['people/fixture-actor/index.md',`---
title: Фиктивна личност А
entity: person
entityId: person:fixture-a
born:
  date:
    year: 1890
  place: place:fixture-city
relations:
  - target: person:fixture-b
    kind: collaborator
    sourceRefs:
      - source:fixture-publication
  - target: person:fixture-b
    kind: rival
    assessment: disputed
    evidenceRefs:
      - evidence:fixture-claim
---
## Контекст
Синтетички тест, није стварна личност.
`],
  ['people/fixture-peer/index.md',`---
title: Фиктивна личност Б
entity: person
entityId: person:fixture-b
---
Синтетички тест.
`],
  ['places/Fixture-City/index.md',`---
title: Измишљено место
entity: place
entityId: place:fixture-city
geo:
  latitude: 44
  longitude: 20
  precision: representative
---
## Место
Тест мапирања.
`],
  ['events/fixture-conference/index.md',`---
title: Измишљена конференција
slug: events/fixture-renamed-conference
entity: event
entityId: event:fixture-conference
date:
  year: 1910
participants:
  - person: person:fixture-a
    role: participant
  - person: person:fixture-b
    role: participant
places:
  - place:fixture-city
sourceRefs:
  - source:fixture-publication
---
## Опис
Ово је само тест руте.
`],
  ['sources/fixture-publication/index.md',`---
title: Пробни извор
entity: source
entityId: source:fixture-publication
sourceType: publication
---
Пробна публикација.
`],
  ['evidence/fixture-claim/index.md',`---
title: Пробна тврдња
entity: evidence
entityId: evidence:fixture-claim
claim: Синтетичка тврдња
assessment: unverified
subjectRefs:
  - person:fixture-a
  - event:fixture-conference
sourceRefs:
  - source:fixture-publication
---
Проба.
`],
  ['media/fixture-note/index.mdx',`---
title: Пробна белешка
entity: media
entityId: media:fixture-note
mediaType: note
subjects:
  - person:fixture-a
eventRefs:
  - event:fixture-conference
---
import YouTube from '../../../../components/YouTube.astro';
import EmbedFrame from '../../../../components/EmbedFrame.astro';

Белешка без фотографије.

<YouTube id="AAAAAAAAAAA" title="Пробни видео" />

<EmbedFrame src="https://example.org/document.pdf" title="Пробни документ" />
`],
  ['ru/people/fixture-actor/index.md',`---
title: Фиктивная личность А
entity: person
entityId: person:fixture-a
relations:
  - target: person:fixture-b
    kind: collaborator
    sourceRefs:
      - source:fixture-publication
---
Русская версия без локализованных событий.
`],
  ['ru/people/fixture-peer/index.md',`---
title: Фиктивная личность Б
entity: person
entityId: person:fixture-b
---
Русская версия второй личности.
`],
];
const roots=docs.map(([p])=>path.resolve(root,p));
for (const filepath of roots) {
  assert.ok(filepath.startsWith(root+path.sep),'Fixture must stay inside the docs root');
  assert.equal(existsSync(path.dirname(filepath)),false,'Synthetic test must never replace existing content: '+filepath);
}
const read=(url)=>readFileSync(path.resolve('dist',url.replace(/^\//,''),'index.html'),'utf8');
try {
  for(const [rel,body] of docs) {
    const pathname=path.resolve(root,rel);
    mkdirSync(path.dirname(pathname),{recursive:true});
    writeFileSync(pathname,body);
  }
  execFileSync('npm',['run','build'],{stdio:'inherit',env:process.env});

  const person=read('/people/fixture-actor/');
  assert.match(person,/data-entity-layout="person"/);
  assert.match(person,/data-entity-section="events"/);
  assert.match(person,/data-entity-section="relations"/);
  assert.match(person,/data-entity-section="places"/);
  assert.doesNotMatch(person,/data-entity-section="sources"/); // sources exist only on the relation
  assert.match(person,/data-entity-section="evidence"/);
  assert.match(person,/data-entity-section="media"/);
  assert.match(person,/href="\/events\/fixture-renamed-conference\/"/);
  assert.doesNotMatch(person,/href="\/events\/fixture-conference\/"/);
  const outwardEdges = [...person.matchAll(/data-relation-edge="out"/g)];
  assert.equal(outwardEdges.length,2,'all authored edges to one person must be shown');
  assert.match(person,/data-relation-sources[\s\S]*?href="\/sources\/fixture-publication\/"/);
  assert.match(person,/data-relation-evidence[\s\S]*?href="\/evidence\/fixture-claim\/"/);
  assert.ok(!person.includes('events/map/?person='),'person link must lead to a global map');
  assert.match(person,/href="\/places\/fixture-city\/"/);
  assert.match(person,/id="entity-events"/);
  const place=read('/places/fixture-city/');
  assert.match(place,/data-entity-layout="place"/);
  assert.match(place,/data-entity-section="events"/);
  assert.match(place,/data-entity-section="people"/);
  const peer = read('/people/fixture-peer/');
  assert.match(peer,/data-relation-edge="in"/);
  assert.match(peer,/data-relation-sources[\s\S]*?href="\/sources\/fixture-publication\/"/);
  const event=read('/events/fixture-renamed-conference/');
  assert.equal(existsSync(path.resolve('dist/events/fixture-conference/index.html')),false,
    'the old filename must not become a published route');
  assert.match(event,/data-entity-layout="event"/);
  assert.match(event,/data-entity-section="people"/);
  assert.match(event,/data-entity-section="places"/);
  assert.match(event,/data-entity-section="evidence"/);
  const ru=read('/ru/people/fixture-actor/');
  assert.doesNotMatch(ru,/data-entity-section="events"/);
  assert.match(ru,/data-relation-sources[\s\S]*?source:fixture-publication/);
  assert.doesNotMatch(ru,/href="\/sources\/fixture-publication\/"/,
    'never invent a source URL in another locale');
  assert.doesNotMatch(ru,/href="\/events\/fixture-conference\/"/);
  const geojson=JSON.parse(readFileSync('dist/generated/places.geojson','utf8'));
  assert.equal(geojson.features.length,1);
  assert.equal(geojson.features[0].properties.eventUrl,'/events/fixture-renamed-conference/');
  assert.equal(geojson.features[0].properties.placeUrl,'/places/fixture-city/');
  const mapHtml = read('/events/map/');
  assert.match(mapHtml,/Измишљена конференција/);
  assert.match(mapHtml,/href="\/events\/fixture-renamed-conference\/"/);
  assert.ok(!mapHtml.includes('events/map/?person='));
  const media = read('/media/fixture-note/');
  assert.ok(media.includes('youtube-nocookie.com/embed/AAAAAAAAAAA'));
  assert.ok(media.includes('youtube.com/watch?v=AAAAAAAAAAA'));
  assert.ok(media.includes('https://example.org/document.pdf'));
  console.log('Synthetic entity pages, auto sections, backlinks and global map: OK');
 } finally {
  // The fixture build is NEVER the last site build. Remove its sources and
  // both output trees, then regenerate a clean production dist from real docs.
  for(const [relative] of docs) {
    const directory=path.dirname(path.resolve(root,relative));
    rmSync(directory,{recursive:true,force:true});
  }
  rmSync(path.resolve('dist'),{recursive:true,force:true});
  rmSync(path.resolve('public/generated'),{recursive:true,force:true});
  execFileSync('npm',['run','build'],{stdio:'inherit',env:process.env});

  const markers = ['person:fixture-a','person:fixture-b','event:fixture-conference',
    'place:fixture-city','source:fixture-publication','evidence:fixture-claim',
    'media:fixture-note','fixture-actor','fixture-peer','fixture-renamed-conference',
    'Измишљена конференција'];
  const checkOutput = (directory) => {
    for(const item of readdirSync(directory,{withFileTypes:true})) {
      const file = path.join(directory,item.name);
      if(item.isDirectory()) { checkOutput(file); continue; }
      if(!/\.(?:html|json|geojson|xml)$/i.test(item.name)) continue;
      const body = readFileSync(file,'utf8');
      for(const marker of markers) {
        assert.ok(!body.includes(marker),file+' retains synthetic marker '+marker);
      }
    }
  };
  checkOutput(path.resolve('dist'));
  checkOutput(path.resolve('public/generated'));
  assert.equal(existsSync(path.resolve('dist/people/fixture-actor')),false);
  assert.equal(existsSync(path.resolve('dist/events/fixture-renamed-conference')),false);
  console.log('Synthetic fixtures removed; clean dist and public/generated verified.');
}
