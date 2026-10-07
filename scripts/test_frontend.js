// Exercise real frontend request handlers against failures and cache recovery.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../frontend-alternative/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);

function handler(name) {
  const start = script.indexOf(`    async function ${name}(`);
  const end = script.indexOf('\n    function ', start + 1);
  return script.slice(start, end);
}

function context(replies) {
  const elements = new Map();
  const calls = [];
  const ctx = {
    catalogStats: null, catalogEntries: [], currentAnswerSources: [], lastQuestion: '',
    idToken: 'test', currentLanguage: 'en', CATALOG_URL: 'catalog', API_URL: 'query',
    $: id => {
      if (!elements.has(id)) elements.set(id, {value: 'ark', textContent: '', classList: {contains: () => true}});
      return elements.get(id);
    },
    text: key => key, escapeHtml: value => value,
    renderArchiveStats() {}, renderArchiveInsights() {}, renderSources() {},
    setAnswerQuery() {}, setAnswerLoading() {},
    applyCatalogFilter() { throw Error('Failed request rendered as an empty archive'); },
    setAnswerText(value, error) { ctx.answerError = error; ctx.answer = value; },
    setStatus(value) { ctx.status = value; },
    setTimeout: callback => callback(),
    fetch: async (url, options) => {
      calls.push({url, body: options.body ? JSON.parse(options.body) : null});
      const next = replies.length > 1 ? replies.shift() : replies[0];
      return {status: next.status, ok: next.status >= 200 && next.status < 300, json: async () => next.data};
    },
    calls,
  };
  vm.createContext(ctx);
  vm.runInContext(handler('loadCatalog'), ctx);
  vm.runInContext(handler('doQuery'), ctx);
  return ctx;
}

(async () => {
  for (const status of [500, 504]) {
    const ctx = context([{status, data: {message: 'Endpoint request timed out'}}]);
    await ctx.loadCatalog();
    assert.equal(ctx.$('catalog-count').textContent, 'messages.catalogUnavailable');
    assert.equal(ctx.catalogStats, null);
    await ctx.doQuery();
    assert.equal(ctx.answerError, true);
    assert.equal(ctx.status, 'connectionIssue');
  }
  const ctx = context([
    {status: 504, data: {message: 'Endpoint request timed out'}},
    {status: 202, data: {processing: true}},
    {status: 200, data: {answer: 'Cited answer', sources: []}},
  ]);
  await ctx.doQuery();
  assert.equal(ctx.answer, 'Cited answer');
  assert.equal(ctx.status, 'archiveReady');
  assert.equal(ctx.calls.filter(call => !call.body.cacheOnly).length, 1);
  assert.equal(ctx.calls.filter(call => call.body.cacheOnly).length, 2);
  const partial = context([{status: 200, data: {answer: 'Answer service unavailable', sources: [], answer_generation_unavailable: true}}]);
  await partial.doQuery();
  assert.equal(partial.$('answer-state').textContent, 'Sources Available');
  console.log('Frontend syntax, API errors, and cache-only timeout recovery passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
