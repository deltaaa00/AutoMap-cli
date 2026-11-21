const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/index.html', 'utf8');

test('typing coalesces preview work, composition waits, blur flushes the final draft', () => {
    let callback, updates = 0;
    const context = vm.createContext({
        setTimeout(fn) { callback = fn; return 1; },
        clearTimeout() { callback = undefined; },
        updateCommandPreview() { updates++; },
        saveBrowserValue() {},
        MAP_TOPIC_STORAGE_KEY: 'topic',
        document: {getElementById() { return {value: 'Topic'}; }},
    });
    vm.runInContext(html.slice(html.indexOf('        let topicPreviewTimer;'),
        html.indexOf('        // Flush a pending draft')), context);
    for (let i = 0; i < 50; i++) context.scheduleTopicPreview({isComposing: false});
    assert.equal(updates, 0);
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
    vm.runInContext(html.slice(html.indexOf('        let activeBadgesMarkup'),
        html.indexOf('        // --- Custom Model Addition')), context);
    const state = {mode: 'text', map_type: 'default', gsm: 15,
        extra_option: 'none', model: 'deepseek-chat', font: 'Arial', topic: 'a'};
    context.updateActiveBadges(state);
    context.updateActiveBadges({...state, topic: 'another topic'});
    assert.equal(writes, 1);
    context.updateActiveBadges({...state, mode: 'number'});
    assert.equal(writes, 2);
});
