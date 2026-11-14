/* Shared browser storage for generated maps and selected video materials. */
window.StudioStorage = (() => {
    let connection;
    function open() {
        if (!connection) {
            connection = new Promise((resolve, reject) => {
                const request = indexedDB.open('automap-studio', 1);
                request.onupgradeneeded = () => request.result.createObjectStore('assets');
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
                    file: record.blob ? new File([record.blob], record.name, {
                        type: record.type, lastModified: record.lastModified,
                    }) : null,
                } : null);
            };
            request.onerror = () => reject(request.error);
        });
    }

    async function setAsset(key, file) {
        const db = await open();
        const record = {
            blob: file, name: file?.name, type: file?.type,
            lastModified: file?.lastModified, updatedAt: Date.now(),
        };
        return new Promise((resolve, reject) => {
            const transaction = db.transaction('assets', 'readwrite');
            transaction.objectStore('assets').put(record, key);
            transaction.oncomplete = () => resolve(record.updatedAt);
            transaction.onabort = () => reject(transaction.error || new Error('File could not be saved.'));
            transaction.onerror = () => reject(transaction.error);
        });
    }

    return {getAsset, setAsset};
})();
