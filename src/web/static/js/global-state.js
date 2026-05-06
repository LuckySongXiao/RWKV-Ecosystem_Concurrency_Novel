class GlobalStateSync {
    constructor() {
        this._state = {};
        this._listeners = {};
        this._socket = null;
        this._connected = false;
    }

    connect() {
        if (this._socket) return;

        this._socket = io(location.host, {
            transports: ['websocket'],
            reconnection: true,
            reconnectionDelay: 2000,
        });

        this._socket.on('connect', () => {
            this._connected = true;
            this._socket.emit('request_state');
        });

        this._socket.on('disconnect', () => {
            this._connected = false;
        });

        this._socket.on('state_update', (changes) => {
            this._applyChanges(changes);
        });

        this._socket.on('progress_update', (data) => {
            this._emit('progress_update', data);
        });

        fetch('/api/global/state')
            .then(r => r.json())
            .then(state => {
                this._state = state;
                this._emit('init', state);
            })
            .catch(() => {});
    }

    _applyChanges(changes) {
        Object.assign(this._state, changes);
        this._emit('state_update', changes);
    }

    on(event, callback) {
        if (!this._listeners[event]) this._listeners[event] = [];
        this._listeners[event].push(callback);
    }

    off(event, callback) {
        if (!this._listeners[event]) return;
        this._listeners[event] = this._listeners[event].filter(cb => cb !== callback);
    }

    _emit(event, data) {
        if (!this._listeners[event]) return;
        this._listeners[event].forEach(cb => {
            try { cb(data); } catch(e) { console.error('[GlobalState]', e); }
        });
    }

    get(key, defaultValue) {
        if (key in this._state) return this._state[key];
        return defaultValue;
    }

    getState() {
        return { ...this._state };
    }

    update(changes) {
        this._applyChanges(changes);
        fetch('/api/global/state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(changes),
        }).catch(e => console.error('[GlobalState] update failed:', e));
    }

    syncFormInputs(mapping) {
        this.on('state_update', (changes) => {
            for (const [stateKey, inputId] of Object.entries(mapping)) {
                if (stateKey in changes) {
                    const el = document.getElementById(inputId);
                    if (el && document.activeElement !== el) {
                        const val = changes[stateKey];
                        if (Array.isArray(val)) {
                            el.value = val.join('，');
                        } else if (typeof val === 'object' && val !== null) {
                            // nested object like concurrency_config - skip
                        } else {
                            el.value = val;
                        }
                    }
                }
            }
        });
    }

    syncPipelineStatus(statusElId, stageElId, progressElId) {
        this.on('state_update', (changes) => {
            if ('pipeline_status' in changes) {
                const statusEl = document.getElementById(statusElId);
                if (statusEl) {
                    const s = changes.pipeline_status;
                    statusEl.textContent = s === 'running' ? '运行中' : s === 'completed' ? '已完成' : s === 'failed' ? '失败' : '空闲';
                    statusEl.style.color = s === 'running' ? '#ffa502' : s === 'completed' ? '#2ed573' : s === 'failed' ? '#ff4757' : '#888';
                }
            }
            if ('pipeline_stage' in changes) {
                const stageEl = document.getElementById(stageElId);
                if (stageEl) stageEl.textContent = changes.pipeline_stage || '-';
            }
            if ('pipeline_progress' in changes) {
                const progressEl = document.getElementById(progressElId);
                if (progressEl) progressEl.textContent = changes.pipeline_progress + '%';
            }
        });
    }
}

window.globalState = new GlobalStateSync();
