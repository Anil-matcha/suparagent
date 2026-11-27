import assert from "node:assert/strict";
import test from "node:test";
import {
  buildAnnotatedMessage,
  citationAttachment,
  createCitationTextSelector,
  findCitationText,
  makeReplyTarget,
  splitMessageAnnotations,
  withCitationComment,
} from "./citations.ts";

test("citation selector captures prefix/suffix and locates text cleanly", () => {
  const source = "I can help you manage tasks, work with files and data, browse the web.";
  const idx = source.indexOf("files");
  const selector = createCitationTextSelector(source, idx, idx + 5);
  assert.ok(selector);
  assert.equal(selector.text, "files");
  const found = findCitationText(source, selector);
  assert.deepEqual(found, { start: idx, end: idx + 5 });
});

test("buildAnnotatedMessage and splitMessageAnnotations round-trip reply and citations", () => {
  const selector = createCitationTextSelector("Alpha beta gamma", 6, 10);
  assert.ok(selector);
  const citation = withCitationComment(citationAttachment({ threadId: "7", itemKey: "m-1" }, selector), "Explain beta");
  const reply = makeReplyTarget("m-1", "assistant", "Alpha beta gamma");
  const wire = buildAnnotatedMessage("Please elaborate.", { replyTo: reply, citations: [citation] });

  assert.match(wire, /> Replying to:/);
  assert.match(wire, /> Quoted message:/);
  assert.match(wire, /Comment:\nExplain beta/);

  const parsed = splitMessageAnnotations(wire);
  assert.equal(parsed.display, "Please elaborate.");
  assert.equal(parsed.reply?.itemKey, "m-1");
  assert.equal(parsed.reply?.excerpt, "Alpha beta gamma");
  assert.equal(parsed.citations.length, 1);
  assert.equal(parsed.citations[0]?.quote, "beta");
  assert.equal(parsed.citations[0]?.comment, "Explain beta");
});
