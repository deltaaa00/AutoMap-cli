/* Shared browser storage for generated maps and selected video materials. */
window.StudioStorage = (() => {
    let connection;
    let clearing = false;
    function open() {
        if (!connection) {
            connection = new Promise((resolve, reject) => {
                const request = indexedDB.open('automap-studio', 2);
                request.onupgradeneeded = () => {
                    const db = request.result;
                    if (!db.objectStoreNames.contains('assets')) db.createObjectStore('assets');
                    const history = db.objectStoreNames.contains('history')
                        ? request.transaction.objectStore('history') : db.createObjectStore('history', {keyPath: 'id'});
                    if (!history.indexNames.contains('createdAt')) history.createIndex('createdAt', 'createdAt');
                };
                request.onsuccess = () => {
                    const db = request.result;
                    db.onversionchange = () => { db.close(); connection = null; };
                    resolve(db);
                };
                request.onerror = () => { connection = null; reject(request.error); };
                request.onblocked = () => { connection = null; reject(new Error('Browser storage is busy.')); };
            });
        }
        return connection;
    }

    async function getAsset(key) {
        const db = await open();
        return new Promise((resolve, reject) => {
            const request = db.transaction('assets').objectStore('assets').get(key);
            request.onsuccess = () => {
                const record = request.result;
                resolve(record ? {
                    updatedAt: record.updatedAt,
                    topic: typeof record.topic === 'string' ? record.topic : '',
                    file: record.blob ? new File([record.blob], record.name, {
                        type: record.type, lastModified: record.lastModified,
                    }) : null,
                } : null);
            };
            request.onerror = () => reject(request.error);
        });
    }

    async function setAsset(key, file, metadata = {}) {
        if (clearing) return;
        const db = await open();
        if (clearing) return;
        const record = {
            blob: file, name: file?.name, type: file?.type,
            lastModified: file?.lastModified, updatedAt: Date.now(),
            topic: typeof metadata.topic === 'string' ? metadata.topic.trim().slice(0, 1000) : '',
        };
        return new Promise((resolve, reject) => {
            const transaction = db.transaction('assets', 'readwrite');
            transaction.objectStore('assets').put(record, key);
            transaction.oncomplete = () => resolve(record.updatedAt);
            transaction.onabort = () => reject(transaction.error || new Error('File could not be saved.'));
            transaction.onerror = () => reject(transaction.error);
        });
    }

    const HISTORY_RETENTION_MS = 24 * 60 * 60 * 1000;

    /* Run a read/write history transaction, pruning entries older than 24 hours. */
    async function historyTransaction(action) {
        if (clearing) return [];
        const db = await open();
        if (clearing) return [];
        return new Promise((resolve, reject) => {
            const transaction = db.transaction('history', 'readwrite');
            const store = transaction.objectStore('history');
            let entries = [];
            if (action?.clear) {
                store.clear();
            } else {
                const cutoff = Date.now() - HISTORY_RETENTION_MS;
                const cursorRequest = store.index('createdAt').openCursor(IDBKeyRange.upperBound(cutoff, true));
                cursorRequest.onsuccess = () => {
                    const cursor = cursorRequest.result;
                    if (cursor) {
                        cursor.delete();
                        cursor.continue();
                    } else if (action?.entry) {
                        store.put(action.entry);
                    } else if (!action?.prune) {
                        const all = store.getAll();
                        all.onsuccess = () => {
                            entries = all.result.filter(entry => {
                                const valid = Number.isFinite(entry.createdAt) && entry.createdAt >= cutoff &&
                                    entry.state && typeof entry.state === 'object' && !Array.isArray(entry.state);
                                if (!valid) store.delete(entry.id);
                                return valid;
                            });
                        };
                    }
                };
            }
            transaction.oncomplete = () => resolve(entries.sort((a, b) => b.createdAt - a.createdAt));
            transaction.onabort = () => reject(transaction.error || new Error('History could not be saved.'));
            transaction.onerror = () => reject(transaction.error);
        });
    }

    async function clearAll() {
        clearing = true;
        try {
            const db = await open();
            await new Promise((resolve, reject) => {
                const transaction = db.transaction(['assets', 'history'], 'readwrite');
                transaction.objectStore('assets').clear();
                transaction.objectStore('history').clear();
                transaction.oncomplete = resolve;
                transaction.onabort = () => reject(transaction.error || new Error('Could not clear browser data.'));
                transaction.onerror = () => reject(transaction.error);
            });
        } catch (error) {
            clearing = false;
            throw error;
        }
    }

    return {getAsset, setAsset, clearAll, HISTORY_RETENTION_MS,
        getHistory: () => historyTransaction(),
        pruneHistory: () => historyTransaction({prune: true}),
        addHistory: entry => historyTransaction({entry}),
        clearHistory: () => historyTransaction({clear: true}),
    };
})();

/* Reset both studios together; cookies and server credentials are unaffected. */
async function clearAllUserData(button) {
    if (!window.confirm('Clear all data? This removes saved API keys, topics, settings, run history, maps, and selected video/audio materials from this browser. This cannot be undone.')) return;
    button.disabled = true;
    try {
        await StudioStorage.clearAll();
        for (const storage of [localStorage, sessionStorage]) {
            for (const key of Object.keys(storage)) {
                if (/^automap[-_]/.test(key)) storage.removeItem(key);
            }
        }
        // Navigate to a fresh document so inputs and in-memory previews reset too.
        window.location.replace(window.location.pathname);
    } catch (error) {
        button.disabled = false;
        window.alert('Could not clear all data. Please allow browser storage and try again.');
    }
}
