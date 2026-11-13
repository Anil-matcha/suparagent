import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseRoom, roomHref, searchResultHref, mentionToken, queueText } from './rooms.ts';

test('room links round trip only valid positive ids and the creation view', () => {
  assert.equal(parseRoom('new'), 'new'); assert.equal(parseRoom('12'),12);
  for(const raw of [null,'0','-1','1.5','1e3','../1','9007199254740993']) assert.equal(parseRoom(raw),null);
  assert.equal(roomHref(12),'/chat?room=12');
});
test('search results open rooms as rooms and ordinary threads as conversations', () => {
  assert.equal(searchResultHref({thread_id:77, room_id:23}), '/chat?room=23');
  assert.equal(searchResultHref({thread_id:88, room_id:null}), '/chat?thread=88');
});
test('queue names each active participant and leaves completed work out', () => {
  const members=[{agent_id:1,name:'Research Agent'},{agent_id:2,name:'Reviewer'}];
  assert.equal(queueText(members,[{agent_id:1,status:'running'},{agent_id:2,status:'queued'},{agent_id:3,status:'completed'}]),'Research Agent is working · Reviewer is queued');
  assert.equal(mentionToken('Research Agent'),'@Research Agent ');
});
