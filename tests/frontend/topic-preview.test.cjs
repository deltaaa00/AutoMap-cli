/**
 * Fast Node tests for map-editor preview scheduling and badge updates.
 *
 * Run: node --test tests/frontend/*.test.cjs from the project root. A VM executes
 * selected script sections from static/map/form.js with fake timers and DOM
 * objects. Section markers are part of this test harness: update extraction anchors
 * when moving that controller. Full browser interactions live in tests/browser.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const form = fs.readFileSync('static/map/form.js', 'utf8');

function section(start, end) {
    const first = form.indexOf(start);
    const last = form.indexOf(end, first);
    assert.ok(first >= 0 && last > first, `Missing controller section: ${start}`);
    return form.slice(first, last);
}

test('typing coalesces preview work, composition waits, blur flushes the final draft', () => {
    let callback, updates = 0;
    const saved = [];
    const context = vm.createContext({
        setTimeout(fn) { callback = fn; return 1; },
        clearTimeout() { callback = undefined; },
        updateCommandPreview() { updates++; },
        saveBrowserValue(key, value) { saved.push([key, value]); },
        MAP_TOPIC_STORAGE_KEY: 'topic',
        document: {getElementById() { return {value: 'draft topic'}; }},
    });
    vm.runInContext(section('let topicPreviewTimer;', '// Flush a pending draft'), context);
    for (let i = 0; i < 50; i++) context.scheduleTopicPreview({isComposing: false});
    assert.equal(updates, 0);
    assert.deepEqual(saved.at(-1), ['topic', 'draft topic']);
    callback();
    assert.equal(updates, 1);
    context.scheduleTopicPreview({isComposing: true});
    assert.equal(callback, undefined);
    context.scheduleTopicPreview({isComposing: false});
    context.flushTopicPreview();
    assert.equal(callback, undefined);
    assert.equal(updates, 2);
});

test('topic edits preserve existing badge DOM; changed settings refresh it', () => {
    let writes = 0;
    const container = {set innerHTML(value) { writes++; }};
    const context = vm.createContext({
        document: {getElementById() { return container; }},
        escapeHtml: value => value,
    });
    vm.runInContext(section('let activeBadgesMarkup', '// --- Custom Model Addition'), context);
    const state = {mode: 'text', map_type: 'default', gsm: 15,
        extra_option: 'none', model: 'deepseek-chat', font: 'Arial', topic: 'a'};
    context.updateActiveBadges(state);
    context.updateActiveBadges({...state, topic: 'another topic'});
    assert.equal(writes, 1);
    context.updateActiveBadges({...state, mode: 'number'});
    assert.equal(writes, 2);
});
