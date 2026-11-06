/*
 * Integration regression for astro dev: editing existing canonical records must
 * regenerate the graph and page sections without restarting the running server.
 * All synthetic sources and generated output are cleaned on exit.
 */
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve('src/content/docs');
const docs = [
  ['people/fixture-dev-actor/index.md', [
    '---','title: Dev test person','entity: person','entityId: person:fixture-dev-actor',
    '---','Synthetic fixture for dev watcher test.',
  ].join('\n')],
  ['places/fixture-dev-place/index.md', [
    '---','title: Dev test place','entity: place','entityId: place:fixture-dev-place',
    'geo:','  latitude: 44','  longitude: 20',
    '---','Synthetic fixture for dev watcher test.',
  ].join('\n')],
];
const eventFile = path.resolve(root,'events/fixture-dev-event/index.md');
const eventBase = [
  '---','title: Dev test event','entity: event','entityId: event:fixture-dev-event',
  'date: 1900','places:','  - place:fixture-dev-place',
  'participants: []','---','Synthetic fixture for dev watcher test.',
].join('\n');
docs.push(['events/fixture-dev-event/index.md',eventBase]);

for(const [rel] of docs) {
  const file = path.resolve(root,rel);
  assert.ok(file.startsWith(root+path.sep));
  assert.equal(existsSync(path.dirname(file)),false, 'Fixture would replace a real file: '+file);
}

const url = 'http://127.0.0.1:4387';
const delay = (ms) => new Promise((resolve)=>setTimeout(resolve,ms));
async function eventually(check,label) {
  let last;
  for(let attempt=0;attempt<100;attempt++) {
    try {
      const answer = await check();
      if(answer) return answer;
    } catch(error) {last=error;}
    await delay(200);
  }
  throw new Error('Dev reload failed: '+label+(last?' ('+last.message+')':''));
}
async function http(pathname) {
  const response=await fetch(url+pathname,{cache:'no-store'});
  if(!response.ok) throw new Error('HTTP '+response.status+' for '+pathname);
  return response.text();
}
function index() {
  return JSON.parse(readFileSync('public/generated/entity-index.json','utf8'));
}
let child;
let output = '';
try {
  for(const [rel,body] of docs) {
    const filename=path.resolve(root,rel);
    mkdirSync(path.dirname(filename),{recursive:true});
    writeFileSync(filename,body);
  }
  child=spawn(process.platform==='win32'?'npm.cmd':'npm',
    ['run','dev','--','--host','127.0.0.1','--port','4387','--strictPort'],
    {detached:process.platform!=='win32',stdio:['ignore','pipe','pipe'],env:process.env});
  child.stdout.on('data',(chunk)=>{output+=String(chunk); output=output.slice(-12000)});
  child.stderr.on('data',(chunk)=>{output+=String(chunk); output=output.slice(-12000)});
  child.on('error',(error)=>{output+='\n'+error.message});

  await eventually(async()=>{
    const html=await http('/people/fixture-dev-actor/');
    return html.includes('data-entity-id="person:fixture-dev-actor"') && html;
  },'astro dev initial person page');
  assert.equal((index().locales.sr.indexes.byPersonEvents['person:fixture-dev-actor']??[]).length,0);
  const original=await http('/people/fixture-dev-actor/');
  assert.ok(!original.includes('data-entity-section="events"'));

  // Change the authored EVENT while astro dev keeps running.
  writeFileSync(eventFile,eventBase.replace('participants: []',
    'participants:\n  - person: person:fixture-dev-actor'));
  await eventually(()=>index().locales.sr.indexes.byPersonEvents['person:fixture-dev-actor']
    ?.includes('event:fixture-dev-event'),'derived person-to-event index update');
  await eventually(async()=> (await http('/people/fixture-dev-actor/'))
    .includes('data-entity-section="events"'),'person HTML shows new event section');

  // Change the authored PLACE, not the event. GeoJSON should be regenerated
  // and served by the same development process.
  const placeFile=path.resolve(root,'places/fixture-dev-place/index.md');
  writeFileSync(placeFile,docs[1][1].replace('latitude: 44','latitude: 45'));
  await eventually(async()=>{
    const data=JSON.parse(await http('/generated/places.geojson'));
    return data.features.some((feature)=>feature.properties.eventId==='event:fixture-dev-event'
      && feature.geometry.coordinates[1]===45);
  },'map GeoJSON updated after place edit');

  console.log('Astro dev hot-edit: participants, backlinks, page HTML and map data updated without restart.');
} catch(error) {
  console.error(output.slice(-10000));
  throw error;
} finally {
  if(child && child.exitCode===null) {
    if(process.platform==='win32') child.kill('SIGTERM');
    else {
      try {process.kill(-child.pid,'SIGTERM')} catch {child.kill('SIGTERM')}
    }
  }
  await delay(350);
  for(const [rel] of docs) {
    rmSync(path.dirname(path.resolve(root,rel)),{recursive:true,force:true});
  }
  rmSync(path.resolve('dist'),{recursive:true,force:true});
  rmSync(path.resolve('public/generated'),{recursive:true,force:true});
  execFileSync('npm',['run','build'],{stdio:'inherit',env:process.env});
  const outputIndex=readFileSync('dist/generated/entity-index.json','utf8');
  assert.ok(!outputIndex.includes('fixture-dev-'));
  assert.equal(existsSync(path.resolve('dist/people/fixture-dev-actor')),false);
  console.log('Astro dev test fixtures removed and clean production output rebuilt.');
}
