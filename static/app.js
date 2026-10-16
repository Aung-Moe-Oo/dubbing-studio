/**
 * Myanmar Dubbing Studio — Frontend Application
 *
 * Handles file upload, SSE progress tracking, and UI state management.
 */

(function () {
    'use strict';

    // ========================================================================
    // DOM References
    // ========================================================================
    const DOM = {
        // Upload
        uploadSection: document.getElementById('upload-section'),
        startBtn: document.getElementById('start-btn'),
        inputCardTitle: document.getElementById('input-card-title'),

        // Input Mode Toggle
        modeTextTab: document.getElementById('mode-text-tab'),
        modeSrtTab: document.getElementById('mode-srt-tab'),
        modeTranscribeTab: document.getElementById('mode-transcribe-tab'),

        // Text Input
        textInputWrapper: document.getElementById('text-input-wrapper'),
        textInput: document.getElementById('text-input'),
        charCount: document.getElementById('char-count'),

        // SRT Upload
        srtInputWrapper: document.getElementById('srt-input-wrapper'),
        srtUploadZone: document.getElementById('srt-upload-zone'),
        srtInput: document.getElementById('srt-input'),
        srtFileInfo: document.getElementById('srt-file-info'),
        srtFileName: document.getElementById('srt-file-name'),
        srtFileSize: document.getElementById('srt-file-size'),
        removeSrt: document.getElementById('remove-srt'),
        srtTranslateToggle: document.getElementById('srt-translate-toggle'),
        optTranslate: document.getElementById('opt-translate'),
        optOriginal: document.getElementById('opt-original'),

        // Audio Transcription Upload
        transcribeInputWrapper: document.getElementById('transcribe-input-wrapper'),
        audioUploadZone: document.getElementById('audio-upload-zone'),
        audioInput: document.getElementById('audio-input'),
        audioFileInfo: document.getElementById('audio-file-info'),
        audioFileName: document.getElementById('audio-file-name'),
        audioFileSize: document.getElementById('audio-file-size'),
        removeAudio: document.getElementById('remove-audio'),

        // Voice Selector
        voiceSelectorWrapper: document.getElementById('voice-selector-wrapper'),
        voiceSearch: document.getElementById('voice-search'),
        voiceList: document.getElementById('voice-list'),
        voicePreviewSection: document.getElementById('voice-preview-section'),
        voicePreviewName: document.getElementById('voice-preview-name'),
        voicePreviewMeta: document.getElementById('voice-preview-meta'),
        voicePreviewTags: document.getElementById('voice-preview-tags'),
        previewBtn: document.getElementById('preview-btn'),
        previewIcon: document.getElementById('preview-icon'),
        previewAudio: document.getElementById('preview-audio'),

        // Progress
        progressSection: document.getElementById('progress-section'),
        progressStage: document.getElementById('progress-stage'),
        progressPct: document.getElementById('progress-pct'),
        progressBar: document.getElementById('progress-bar'),
        progressChunk: document.getElementById('progress-chunk'),
        progressTime: document.getElementById('progress-time'),
        statusMessage: document.getElementById('status-message'),
        statusSpinner: document.getElementById('status-spinner'),
        statusText: document.getElementById('status-text'),
        chunksGrid: document.getElementById('chunks-grid'),
        cancelBtn: document.getElementById('cancel-btn'),
        logToggle: document.getElementById('log-toggle'),
        logOutput: document.getElementById('log-output'),

        // Download
        downloadSection: document.getElementById('download-section'),
        downloadInfo: document.getElementById('download-info'),
        downloadBtn: document.getElementById('download-btn'),
        newJobBtn: document.getElementById('new-job-btn'),
        
        // Audio Adjustments
        audioPreview: document.getElementById('audio-preview'),
        speedSlider: document.getElementById('speed-slider'),
        speedValue: document.getElementById('speed-value'),
        pitchSlider: document.getElementById('pitch-slider'),
        pitchValue: document.getElementById('pitch-value'),
        applyAdjustmentsBtn: document.getElementById('apply-adjustments-btn'),
        adjustmentStatus: document.getElementById('adjustment-status'),

        // Transcription Result
        transcriptionResultSection: document.getElementById('transcription-result-section'),
        transcriptionInfo: document.getElementById('transcription-info'),
        srtSegmentsView: document.getElementById('srt-segments-view'),
        srtLoading: document.getElementById('srt-loading'),
        srtRawOutput: document.getElementById('srt-raw-output'),
        srtSegmentCount: document.getElementById('srt-segment-count'),
        toggleRawBtn: document.getElementById('toggle-raw-btn'),
        transcriptionStats: document.getElementById('transcription-stats'),
        statDuration: document.getElementById('stat-duration'),
        statSegments: document.getElementById('stat-segments'),
        statChars: document.getElementById('stat-chars'),
        copyTranscriptionBtn: document.getElementById('copy-transcription-btn'),
        downloadSrtBtn: document.getElementById('download-srt-btn'),
        newTranscribeBtn: document.getElementById('new-transcribe-btn'),

        // Error
        errorBanner: document.getElementById('error-banner'),
        errorMessage: document.getElementById('error-message'),
    };

    // ========================================================================
    // State
    // ========================================================================
    let state = {
        inputMode: 'text',      // 'text', 'srt', or 'transcribe'
        selectedSrtFile: null,
        srtTranslateMode: 'translate',  // 'translate' or 'original'
        selectedAudioFile: null,
        currentJobId: null,
        eventSource: null,
        elapsedTimer: null,
        startTime: null,
        outputMode: 'audio',    // 'audio' or 'transcription'
        voices: [],
        selectedVoice: null,
        previewPlaying: false,
        previewLoading: false,
    };

    // ========================================================================
    // Utility
    // ========================================================================
    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
        return (bytes / 1073741824).toFixed(2) + ' GB';
    }

    // Format time in seconds to human readable string
    function formatTime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        if (h > 0) return `${h}h ${m}m ${s}s`;
        if (m > 0) return `${m}m ${s}s`;
        return `${s}s`;
    }

    function stageLabel(stage) {
        const labels = {
            'pending': 'Pending',
            'initializing': 'Initializing',
            'splitting': 'Splitting Video',
            'extracting': 'Extracting Audio',
            'transcribing': 'Transcribing',
            'translating': 'Translating',
            'tts': 'Generating TTS',
            'merging': 'Merging Audio',
            'combining': 'Combining Chunks',
            'processing': 'Processing',
            'done': 'Complete',
            'error': 'Error',
            'cancelled': 'Cancelled',
        };
        return labels[stage] || stage;
    }

    function addLog(message, type = '') {
        const entry = document.createElement('div');
        entry.className = 'log-entry' + (type ? ' ' + type : '');
        const now = new Date().toLocaleTimeString();
        entry.innerHTML = `<span class="timestamp">[${now}]</span> ${message}`;
        DOM.logOutput.appendChild(entry);
        DOM.logOutput.scrollTop = DOM.logOutput.scrollHeight;
    }

    function showError(message) {
        DOM.errorMessage.textContent = message;
        DOM.errorBanner.classList.add('active');
        setTimeout(() => {
            DOM.errorBanner.classList.remove('active');
        }, 10000);
    }

    // ========================================================================
    // Input Mode Toggle
    // ========================================================================
    function setInputMode(mode) {
        state.inputMode = mode;

        // Update tabs
        DOM.modeTextTab.classList.toggle('active', mode === 'text');
        DOM.modeSrtTab.classList.toggle('active', mode === 'srt');
        DOM.modeTranscribeTab.classList.toggle('active', mode === 'transcribe');

        // Show/hide panels
        DOM.textInputWrapper.classList.toggle('hidden', mode !== 'text');
        DOM.srtInputWrapper.classList.toggle('hidden', mode !== 'srt');
        DOM.transcribeInputWrapper.classList.toggle('hidden', mode !== 'transcribe');

        // Show/hide voice selector (not needed for transcription)
        DOM.voiceSelectorWrapper.classList.toggle('hidden', mode === 'transcribe');

        // Update start button label
        if (mode === 'transcribe') {
            DOM.startBtn.innerHTML = '📝 Transcribe';
        } else {
            DOM.startBtn.innerHTML = '🎙️ Generate Audio';
        }

        // Update start button state
        updateStartButton();
    }

    DOM.modeTextTab.addEventListener('click', () => setInputMode('text'));
    DOM.modeSrtTab.addEventListener('click', () => setInputMode('srt'));
    DOM.modeTranscribeTab.addEventListener('click', () => setInputMode('transcribe'));

    /** Update the start button's enabled state based on current input */
    function updateStartButton() {
        if (state.inputMode === 'text') {
            const text = DOM.textInput.value.trim();
            DOM.startBtn.disabled = text.length === 0;
        } else if (state.inputMode === 'srt') {
            DOM.startBtn.disabled = !state.selectedSrtFile;
        } else if (state.inputMode === 'transcribe') {
            DOM.startBtn.disabled = !state.selectedAudioFile;
        }
    }

    // ========================================================================
    // Text Input
    // ========================================================================
    DOM.textInput.addEventListener('input', () => {
        const len = DOM.textInput.value.length;
        DOM.charCount.textContent = `${len} character${len !== 1 ? 's' : ''}`;
        updateStartButton();
    });

    // ========================================================================
    // SRT File Selection
    // ========================================================================
    function handleSrtSelect(file) {
        if (!file) return;

        const ext = file.name.split('.').pop().toLowerCase();
        if (ext !== 'srt') {
            showError('Invalid subtitle format. Only .srt files are supported.');
            return;
        }

        state.selectedSrtFile = file;
        DOM.srtFileName.textContent = file.name;
        DOM.srtFileSize.textContent = `(${formatFileSize(file.size)})`;
        DOM.srtFileInfo.classList.remove('hidden');
        DOM.srtUploadZone.classList.add('has-file');
        DOM.srtTranslateToggle.classList.remove('hidden');
        updateStartButton();
    }

    function clearSrt() {
        state.selectedSrtFile = null;
        DOM.srtInput.value = '';
        DOM.srtFileInfo.classList.add('hidden');
        DOM.srtUploadZone.classList.remove('has-file');
        DOM.srtTranslateToggle.classList.add('hidden');
        state.srtTranslateMode = 'translate';
        DOM.optTranslate.classList.add('selected');
        DOM.optOriginal.classList.remove('selected');
        DOM.optTranslate.querySelector('input').checked = true;
        updateStartButton();
    }

    // SRT upload zone click
    DOM.srtUploadZone.addEventListener('click', () => {
        if (!state.selectedSrtFile) {
            DOM.srtInput.click();
        }
    });

    DOM.srtInput.addEventListener('change', (e) => {
        handleSrtSelect(e.target.files[0]);
    });

    DOM.removeSrt.addEventListener('click', (e) => {
        e.stopPropagation();
        clearSrt();
    });

    // SRT Drag and drop
    DOM.srtUploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        DOM.srtUploadZone.classList.add('dragover');
    });

    DOM.srtUploadZone.addEventListener('dragleave', () => {
        DOM.srtUploadZone.classList.remove('dragover');
    });

    DOM.srtUploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        DOM.srtUploadZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleSrtSelect(e.dataTransfer.files[0]);
        }
    });

    // ========================================================================
    // SRT Translation Mode Toggle
    // ========================================================================
    document.querySelectorAll('input[name="srt_translate_mode"]').forEach((radio) => {
        radio.addEventListener('change', (e) => {
            state.srtTranslateMode = e.target.value;
            DOM.optTranslate.classList.toggle('selected', e.target.value === 'translate');
            DOM.optOriginal.classList.toggle('selected', e.target.value === 'original');
        });
    });

    // ========================================================================
    // Upload & Start Pipeline
    // ========================================================================
    DOM.startBtn.addEventListener('click', async () => {
        if (state.inputMode === 'text') {
            await submitText();
        } else if (state.inputMode === 'srt') {
            await submitSrt();
        } else if (state.inputMode === 'transcribe') {
            await submitTranscribe();
        }
    });

    /** Submit text input for TTS */
    async function submitText() {
        const text = DOM.textInput.value.trim();
        if (!text) return;

        DOM.startBtn.disabled = true;
        DOM.startBtn.innerHTML = '<div class="spinner" style="width:18px;height:18px;border:2px solid rgba(255,255,255,0.3);border-top:2px solid white;border-radius:50%;animation:spin 0.8s linear infinite"></div> Generating...';

        const formData = new FormData();
        formData.append('text', text);
        if (state.selectedVoice) {
            formData.append('tts_voice', state.selectedVoice.ShortName);
        }

        try {
            const response = await fetch('/api/text-to-speech', {
                method: 'POST',
                body: formData,
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Request failed');
            }

            state.currentJobId = data.job_id;
            addLog(`Job started: ${data.job_id} — Text input (${text.length} chars)`, 'success');

            showProgressView();
            connectSSE(data.job_id);

        } catch (err) {
            showError(err.message);
            DOM.startBtn.disabled = false;
            DOM.startBtn.innerHTML = '🎙️ Generate Audio';
        }
    }

    /** Submit SRT file for dubbing pipeline */
    async function submitSrt() {
        if (!state.selectedSrtFile) return;

        const sourceLang = 'auto';
        state.outputMode = 'audio';

        DOM.startBtn.disabled = true;
        DOM.startBtn.innerHTML = '<div class="spinner" style="width:18px;height:18px;border:2px solid rgba(255,255,255,0.3);border-top:2px solid white;border-radius:50%;animation:spin 0.8s linear infinite"></div> Uploading...';

        const formData = new FormData();
        formData.append('subtitle', state.selectedSrtFile);
        formData.append('source_lang', sourceLang);
        formData.append('output_mode', 'audio');
        if (state.srtTranslateMode === 'original') {
            formData.append('skip_translate', 'true');
        }
        if (state.selectedVoice) {
            formData.append('tts_voice', state.selectedVoice.ShortName);
        }

        try {
            const response = await fetch('/api/upload', {
                method: 'POST',
                body: formData,
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Upload failed');
            }

            state.currentJobId = data.job_id;
            addLog(`Job started: ${data.job_id} — ${data.filename}`, 'success');

            showProgressView();
            connectSSE(data.job_id);

        } catch (err) {
            showError(err.message);
            DOM.startBtn.disabled = false;
            DOM.startBtn.innerHTML = '🎙️ Generate Audio';
        }
    }

    // ========================================================================
    // Audio File Selection (Transcription)
    // ========================================================================
    function handleAudioSelect(file) {
        if (!file) return;

        const validExts = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'wma', 'aac', 'webm'];
        const ext = file.name.split('.').pop().toLowerCase();
        if (!validExts.includes(ext)) {
            showError(`Unsupported audio format '.${ext}'. Supported: ${validExts.join(', ')}`);
            return;
        }

        state.selectedAudioFile = file;
        DOM.audioFileName.textContent = file.name;
        DOM.audioFileSize.textContent = `(${formatFileSize(file.size)})`;
        DOM.audioFileInfo.classList.remove('hidden');
        DOM.audioUploadZone.classList.add('has-file');
        updateStartButton();
    }

    function clearAudio() {
        state.selectedAudioFile = null;
        DOM.audioInput.value = '';
        DOM.audioFileInfo.classList.add('hidden');
        DOM.audioUploadZone.classList.remove('has-file');
        updateStartButton();
    }

    // Audio upload zone click
    DOM.audioUploadZone.addEventListener('click', () => {
        if (!state.selectedAudioFile) {
            DOM.audioInput.click();
        }
    });

    DOM.audioInput.addEventListener('change', (e) => {
        handleAudioSelect(e.target.files[0]);
    });

    DOM.removeAudio.addEventListener('click', (e) => {
        e.stopPropagation();
        clearAudio();
    });

    // Audio drag and drop
    DOM.audioUploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        DOM.audioUploadZone.classList.add('dragover');
    });

    DOM.audioUploadZone.addEventListener('dragleave', () => {
        DOM.audioUploadZone.classList.remove('dragover');
    });

    DOM.audioUploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        DOM.audioUploadZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleAudioSelect(e.dataTransfer.files[0]);
        }
    });

    /** Submit audio file for transcription */
    async function submitTranscribe() {
        if (!state.selectedAudioFile) return;

        state.outputMode = 'transcription';

        DOM.startBtn.disabled = true;
        DOM.startBtn.innerHTML = '<div class="spinner" style="width:18px;height:18px;border:2px solid rgba(255,255,255,0.3);border-top:2px solid white;border-radius:50%;animation:spin 0.8s linear infinite"></div> Transcribing...';

        const formData = new FormData();
        formData.append('audio', state.selectedAudioFile);

        try {
            const response = await fetch('/api/transcribe', {
                method: 'POST',
                body: formData,
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Upload failed');
            }

            state.currentJobId = data.job_id;
            addLog(`Transcription job started: ${data.job_id} — ${data.filename}`, 'success');

            showProgressView();
            connectSSE(data.job_id);

        } catch (err) {
            showError(err.message);
            DOM.startBtn.disabled = false;
            DOM.startBtn.innerHTML = '📝 Transcribe';
        }
    }

    // ========================================================================
    // Progress View
    // ========================================================================
    function showProgressView() {
        DOM.uploadSection.style.display = 'none';
        DOM.progressSection.classList.add('active');
        DOM.downloadSection.classList.remove('active');

        // Start elapsed timer
        state.startTime = Date.now();
        state.elapsedTimer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - state.startTime) / 1000);
            DOM.progressTime.textContent = `Elapsed: ${formatTime(elapsed)}`;
        }, 1000);
    }

    function showUploadView() {
        DOM.uploadSection.style.display = 'block';
        DOM.progressSection.classList.remove('active');
        DOM.downloadSection.classList.remove('active');
        DOM.transcriptionResultSection.classList.remove('active');
        clearSrt();
        clearAudio();
        DOM.textInput.value = '';
        DOM.charCount.textContent = '0 characters';

        if (state.elapsedTimer) {
            clearInterval(state.elapsedTimer);
            state.elapsedTimer = null;
        }

        if (state.inputMode === 'transcribe') {
            DOM.startBtn.innerHTML = '📝 Transcribe';
        } else {
            DOM.startBtn.innerHTML = '🎙️ Generate Audio';
        }
        DOM.chunksGrid.innerHTML = '';
        DOM.logOutput.innerHTML = '';
        updateStartButton();
    }

    function showDownloadView(jobId, filename) {
        DOM.progressSection.classList.remove('active');
        DOM.downloadSection.classList.add('active');

        if (state.elapsedTimer) {
            clearInterval(state.elapsedTimer);
            state.elapsedTimer = null;
        }

        const elapsed = Math.floor((Date.now() - state.startTime) / 1000);
        DOM.downloadInfo.textContent = `Completed in ${formatTime(elapsed)}. Your Burmese dubbed audio is ready!`;

        const downloadUrl = `/api/download/${jobId}`;
        DOM.downloadBtn.href = downloadUrl + '?download=true';
        DOM.downloadBtn.setAttribute('download', '');
        DOM.downloadBtn.classList.remove('disabled');
        DOM.downloadBtn.style.pointerEvents = 'auto';
        DOM.downloadBtn.innerHTML = '⬇️ Download Dubbed Audio';

        // Setup audio preview
        DOM.audioPreview.src = `${downloadUrl}?t=${Date.now()}`;
        DOM.speedSlider.value = 1.0;
        DOM.speedValue.textContent = '1.0x';
        DOM.pitchSlider.value = 1.0;
        DOM.pitchValue.textContent = '1.0x';
        DOM.adjustmentStatus.style.display = 'none';
    }

    // ========================================================================
    // SSE Connection
    // ========================================================================
    function connectSSE(jobId) {
        if (state.eventSource) {
            state.eventSource.close();
        }

        state.eventSource = new EventSource(`/api/status/${jobId}`);

        state.eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.done) {
                    state.eventSource.close();
                    state.eventSource = null;
                    return;
                }

                updateProgress(data);
            } catch (err) {
                console.error('SSE parse error:', err);
            }
        };

        state.eventSource.onerror = () => {
            // SSE connection lost — try to reconnect after 3s
            state.eventSource.close();
            state.eventSource = null;
            setTimeout(() => {
                if (state.currentJobId) {
                    connectSSE(state.currentJobId);
                }
            }, 3000);
        };
    }

    // ========================================================================
    // Update Progress UI
    // ========================================================================
    function updateProgress(data) {
        // Overall progress
        DOM.progressPct.textContent = `${data.overall_progress}%`;
        DOM.progressBar.style.width = `${data.overall_progress}%`;
        DOM.progressStage.textContent = stageLabel(data.stage);
        DOM.progressChunk.textContent = `Chunk ${data.current_chunk + 1}/${data.total_chunks || '?'}`;

        // Status message
        DOM.statusText.textContent = data.message || 'Processing...';

        // Handle completion
        if (data.stage === 'done') {
            DOM.statusSpinner.style.display = 'none';
            if (state.outputMode === 'transcription') {
                addLog('✅ Transcription complete!', 'success');
                showTranscriptionResult(data.job_id);
            } else {
                addLog('✅ Dubbing complete!', 'success');
                showDownloadView(data.job_id, data.output_file);
            }
            return;
        }

        // Handle error
        if (data.stage === 'error') {
            DOM.statusSpinner.style.display = 'none';
            DOM.statusText.textContent = `Error: ${data.error}`;
            DOM.statusMessage.style.borderColor = 'var(--error)';
            addLog(`❌ Error: ${data.error}`, 'error');
            showError(data.error);
            return;
        }

        // Handle cancellation
        if (data.stage === 'cancelled') {
            DOM.statusSpinner.style.display = 'none';
            DOM.statusText.textContent = 'Job cancelled';
            addLog('🚫 Job cancelled', 'error');
            return;
        }

        // Update chunk cards
        if (data.chunks && data.chunks.length > 0) {
            renderChunks(data.chunks);
        }

        // Add log entry for significant updates
        if (data.message) {
            addLog(data.message);
        }
    }

    function renderChunks(chunks) {
        // Only rebuild if chunk count changed
        if (DOM.chunksGrid.children.length !== chunks.length) {
            DOM.chunksGrid.innerHTML = '';
            chunks.forEach((chunk) => {
                const card = document.createElement('div');
                card.className = 'chunk-card';
                card.id = `chunk-${chunk.index}`;
                card.innerHTML = `
                    <div class="chunk-card-header">
                        <span class="chunk-label">Chunk ${chunk.index + 1}</span>
                        <span class="chunk-badge ${chunk.stage}">${stageLabel(chunk.stage)}</span>
                    </div>
                    <div class="chunk-progress">
                        <div class="chunk-progress-fill" style="width: ${chunk.progress}%"></div>
                    </div>
                    <div class="chunk-message">${chunk.message || '—'}</div>
                `;
                DOM.chunksGrid.appendChild(card);
            });
        } else {
            // Update existing cards
            chunks.forEach((chunk) => {
                const card = document.getElementById(`chunk-${chunk.index}`);
                if (!card) return;

                card.className = 'chunk-card';
                if (chunk.stage === 'done') card.classList.add('done');
                else if (chunk.stage === 'error') card.classList.add('error');
                else if (chunk.stage !== 'pending') card.classList.add('active');

                const badge = card.querySelector('.chunk-badge');
                badge.className = `chunk-badge ${chunk.stage}`;
                badge.textContent = stageLabel(chunk.stage);

                const fill = card.querySelector('.chunk-progress-fill');
                fill.style.width = `${chunk.progress}%`;

                const msg = card.querySelector('.chunk-message');
                msg.textContent = chunk.message || '—';
            });
        }
    }

    // ========================================================================
    // Cancel
    // ========================================================================
    DOM.cancelBtn.addEventListener('click', async () => {
        if (!state.currentJobId) return;

        try {
            await fetch(`/api/cancel/${state.currentJobId}`, { method: 'POST' });
            addLog('Cancellation requested...', 'error');
        } catch (err) {
            showError('Failed to cancel job');
        }
    });

    // ========================================================================
    // Log Toggle
    // ========================================================================
    DOM.logToggle.addEventListener('click', () => {
        DOM.logToggle.classList.toggle('open');
        DOM.logOutput.classList.toggle('open');
    });

    // ========================================================================
    // New Job
    // ========================================================================
    DOM.newJobBtn.addEventListener('click', () => {
        cleanupAndReset();
    });

    function cleanupAndReset() {
        if (state.currentJobId) {
            fetch(`/api/cleanup/${state.currentJobId}`, { method: 'POST' })
                .catch(err => console.error('Cleanup failed:', err));
        }
        state.currentJobId = null;
        state.outputMode = 'audio';
        if (state.eventSource) {
            state.eventSource.close();
            state.eventSource = null;
        }
        // Reset transcription preview state
        _transcriptionData = { srt: '', text: '', segments: [] };
        _rawSrtVisible = false;
        if (DOM.srtSegmentsView) {
            DOM.srtSegmentsView.innerHTML = '';
            DOM.srtSegmentsView.classList.remove('hidden');
        }
        if (DOM.srtRawOutput) {
            DOM.srtRawOutput.value = '';
            DOM.srtRawOutput.classList.add('hidden');
        }
        if (DOM.toggleRawBtn) {
            DOM.toggleRawBtn.classList.remove('active');
            DOM.toggleRawBtn.innerHTML = '<span>&lt;/&gt;</span> Raw SRT';
        }
        if (DOM.transcriptionStats) DOM.transcriptionStats.style.display = 'none';
        showUploadView();
    }

    DOM.downloadBtn.addEventListener('click', () => {
        // Change text and appearance of button to show files are cleaned up
        DOM.downloadBtn.classList.add('disabled');
        DOM.downloadBtn.innerHTML = '✅ Downloaded (Files Cleaned Up)';
        DOM.downloadBtn.style.pointerEvents = 'none';
    });

    // ========================================================================
    // Voice Selector
    // ========================================================================

    /** Extract display name from FriendlyName (e.g. "Microsoft AndrewMultilingual Online (Natural)" -> "Andrew") */
    function extractVoiceName(voice) {
        if (voice.DisplayName) return voice.DisplayName;
        // Pull name between "Microsoft " and "Multilingual|Neural|Online"
        const match = voice.FriendlyName.match(/Microsoft\s+([\w]+?)(?:Multilingual|Neural|Online)/i);
        if (match) return match[1];
        // Fallback: use ShortName middle part
        const parts = voice.ShortName.split('-');
        return parts.length >= 3 ? parts[2].replace('Neural', '') : voice.ShortName;
    }

    /** Get a locale display label */
    function localeLabel(locale) {
        const map = {
            'en-US': '🇺🇸 English (US)',
            'en-AU': '🇦🇺 English (AU)',
            'fr-FR': '🇫🇷 French',
            'de-DE': '🇩🇪 German',
            'it-IT': '🇮🇹 Italian',
            'ko-KR': '🇰🇷 Korean',
            'pt-BR': '🇧🇷 Portuguese (BR)',
            'my-MM': '🇲🇲 Burmese',
        };
        return map[locale] || locale;
    }

    /** Load voices from API */
    async function loadVoices() {
        try {
            const res = await fetch('/api/voices');
            const data = await res.json();
            state.voices = data.voices || [];
            renderVoiceList(state.voices);
        } catch (err) {
            DOM.voiceList.innerHTML = '<div class="voice-empty">⚠️ Failed to load voices</div>';
        }
    }

    /** Render the voice list */
    function renderVoiceList(voices) {
        if (voices.length === 0) {
            DOM.voiceList.innerHTML = '<div class="voice-empty">No voices found</div>';
            return;
        }

        DOM.voiceList.innerHTML = '';
        voices.forEach((voice) => {
            const item = document.createElement('div');
            item.className = 'voice-item' + (state.selectedVoice && state.selectedVoice.ShortName === voice.ShortName ? ' selected' : '');
            item.dataset.shortName = voice.ShortName;

            const genderIcon = voice.Gender === 'Male' ? '👨' : '👩';
            const genderClass = voice.Gender.toLowerCase();
            const displayName = extractVoiceName(voice);

            item.innerHTML = `
                <div class="voice-item-icon ${genderClass}">${genderIcon}</div>
                <div class="voice-item-details">
                    <div class="voice-item-name">${displayName}</div>
                    </div>
                    <span class="voice-item-gender-badge ${genderClass}">${voice.Gender}</span>
                    `;

            // <div class="voice-item-locale">${localeLabel(voice.Locale)}</div>
            item.addEventListener('click', () => selectVoice(voice));
            DOM.voiceList.appendChild(item);
        });
    }

    /** Select a voice */
    function selectVoice(voice) {
        state.selectedVoice = voice;

        // Update list selection
        DOM.voiceList.querySelectorAll('.voice-item').forEach(el => {
            el.classList.toggle('selected', el.dataset.shortName === voice.ShortName);
        });

        // Show preview section
        const displayName = extractVoiceName(voice);
        DOM.voicePreviewName.textContent = displayName;
        DOM.voicePreviewMeta.textContent = `${voice.Gender}`;
        // DOM.voicePreviewMeta.textContent = `${localeLabel(voice.Locale)} · ${voice.Gender} · ${voice.ShortName}`;

        // Render personality tags
        const personalities = (voice.VoiceTag && voice.VoiceTag.VoicePersonalities) || [];
        DOM.voicePreviewTags.innerHTML = personalities
            .map(p => `<span class="voice-tag">${p}</span>`)
            .join('');

        DOM.voicePreviewSection.classList.remove('hidden');

        // Stop any playing preview
        stopPreview();
    }

    /** Search/filter voices */
    DOM.voiceSearch.addEventListener('input', () => {
        const q = DOM.voiceSearch.value.toLowerCase().trim();
        if (!q) {
            renderVoiceList(state.voices);
            return;
        }
        const filtered = state.voices.filter(v => {
            const name = extractVoiceName(v).toLowerCase();
            const locale = v.Locale.toLowerCase();
            const gender = v.Gender.toLowerCase();
            const shortName = v.ShortName.toLowerCase();
            const friendly = v.FriendlyName.toLowerCase();
            return name.includes(q) || locale.includes(q) || gender.includes(q) || shortName.includes(q) || friendly.includes(q);
        });
        renderVoiceList(filtered);
    });

    /** Preview voice */
    DOM.previewBtn.addEventListener('click', async () => {
        if (state.previewPlaying) {
            stopPreview();
            return;
        }
        if (!state.selectedVoice || state.previewLoading) return;

        state.previewLoading = true;
        DOM.previewBtn.classList.add('loading');
        DOM.previewIcon.textContent = '⏳';

        try {
            const res = await fetch('/api/preview-voice', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ voice: state.selectedVoice.ShortName }),
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.error || 'Preview failed');
            }

            const blob = await res.blob();
            const url = URL.createObjectURL(blob);

            DOM.previewAudio.src = url;
            DOM.previewAudio.play();

            state.previewPlaying = true;
            state.previewLoading = false;
            DOM.previewBtn.classList.remove('loading');
            DOM.previewBtn.classList.add('playing');
            DOM.previewIcon.textContent = '⏹';

            DOM.previewAudio.onended = () => {
                stopPreview();
                URL.revokeObjectURL(url);
            };
        } catch (err) {
            state.previewLoading = false;
            DOM.previewBtn.classList.remove('loading');
            DOM.previewIcon.textContent = '▶';
            showError('Voice preview failed: ' + err.message);
        }
    });

    function stopPreview() {
        DOM.previewAudio.pause();
        DOM.previewAudio.currentTime = 0;
        state.previewPlaying = false;
        state.previewLoading = false;
        DOM.previewBtn.classList.remove('playing', 'loading');
        DOM.previewIcon.textContent = '▶';
    }

    // ========================================================================
    // Audio Adjustments
    // ========================================================================
    DOM.speedSlider.addEventListener('input', (e) => {
        DOM.speedValue.textContent = parseFloat(e.target.value).toFixed(1) + 'x';
    });

    DOM.pitchSlider.addEventListener('input', (e) => {
        DOM.pitchValue.textContent = parseFloat(e.target.value).toFixed(1) + 'x';
    });

    DOM.applyAdjustmentsBtn.addEventListener('click', async () => {
        if (!state.currentJobId) return;

        const speed = parseFloat(DOM.speedSlider.value);
        const pitch = parseFloat(DOM.pitchSlider.value);
        
        DOM.applyAdjustmentsBtn.disabled = true;
        DOM.applyAdjustmentsBtn.textContent = '⚙️ Applying...';
        DOM.adjustmentStatus.style.display = 'block';
        DOM.adjustmentStatus.textContent = 'Processing audio with FFmpeg...';
        DOM.adjustmentStatus.style.color = 'var(--text-muted)';

        try {
            const res = await fetch(`/api/adjust/${state.currentJobId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ speed, pitch })
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to adjust audio');

            // Update audio player and download link with cache buster
            const newUrl = `/api/download/${state.currentJobId}?t=${Date.now()}`;
            DOM.audioPreview.src = newUrl;
            DOM.downloadBtn.href = newUrl + '&download=true';
            DOM.downloadBtn.classList.remove('disabled');
            DOM.downloadBtn.style.pointerEvents = 'auto';
            DOM.downloadBtn.innerHTML = '⬇️ Download Dubbed Audio';
            
            // Auto play the new audio
            DOM.audioPreview.play().catch(() => {});

            DOM.adjustmentStatus.textContent = '✅ Adjustments applied successfully!';
            DOM.adjustmentStatus.style.color = 'var(--success)';
        } catch (err) {
            console.error(err);
            DOM.adjustmentStatus.textContent = `❌ Error: ${err.message}`;
            DOM.adjustmentStatus.style.color = 'var(--error)';
        } finally {
            DOM.applyAdjustmentsBtn.disabled = false;
            DOM.applyAdjustmentsBtn.textContent = '⚙️ Apply Adjustments';
        }
    });

    // ========================================================================
    // Transcription Result
    // ========================================================================

    // Internal state for transcription data
    let _transcriptionData = { srt: '', text: '', segments: [] };
    let _rawSrtVisible = false;

    /** Convert SRT timestamp string to total seconds */
    function srtTimeToSeconds(ts) {
        // format: HH:MM:SS,mmm
        try {
            const [hms, ms] = ts.split(',');
            const [h, m, s] = hms.split(':').map(Number);
            return h * 3600 + m * 60 + s + (Number(ms) / 1000);
        } catch (e) { return 0; }
    }

    /** Format seconds as m:ss or h:mm:ss */
    function formatDuration(totalSec) {
        totalSec = Math.round(totalSec);
        const h = Math.floor(totalSec / 3600);
        const m = Math.floor((totalSec % 3600) / 60);
        const s = totalSec % 60;
        if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
        return `${m}:${String(s).padStart(2,'0')}`;
    }

    async function showTranscriptionResult(jobId) {
        DOM.progressSection.classList.remove('active');
        DOM.downloadSection.classList.remove('active');
        DOM.transcriptionResultSection.classList.add('active');

        if (state.elapsedTimer) {
            clearInterval(state.elapsedTimer);
            state.elapsedTimer = null;
        }

        const elapsed = Math.floor((Date.now() - state.startTime) / 1000);
        DOM.transcriptionInfo.textContent = `Completed in ${formatTime(elapsed)}. Your Burmese SRT file is ready to download.`;

        // Update SRT file label
        const srtFileLabel = document.getElementById('srt-file-label');
        if (srtFileLabel) {
            srtFileLabel.textContent = `transcription_${jobId}.srt`;
        }

        try {
            const res = await fetch(`/api/transcription/${jobId}`);
            const data = await res.json();

            if (!res.ok || data.error) throw new Error(data.error || 'Failed to load transcription');

            _transcriptionData = {
                srt: data.srt || '',
                text: data.text || '',
                segments: data.segments || [],
            };

            // Populate hidden raw output for JS compat
            if (DOM.srtRawOutput) DOM.srtRawOutput.value = _transcriptionData.srt;

            // Stats
            const segCount = _transcriptionData.segments.length;
            if (DOM.srtSegmentCount) DOM.srtSegmentCount.textContent = `${segCount} segment${segCount !== 1 ? 's' : ''}`;
            DOM.statSegments.textContent = segCount;
            DOM.statChars.textContent = _transcriptionData.text.length.toLocaleString();

            // Estimate duration from last segment
            if (segCount > 0) {
                const lastSeg = _transcriptionData.segments[segCount - 1];
                let lastEnd = 0;
                if (typeof lastSeg.end === 'number') {
                    lastEnd = lastSeg.end;
                } else if (lastSeg.end_ts) {
                    lastEnd = srtTimeToSeconds(lastSeg.end_ts);
                }
                DOM.statDuration.textContent = formatDuration(lastEnd);
            } else {
                DOM.statDuration.textContent = '—';
            }

            DOM.transcriptionStats.style.display = 'flex';

        } catch (err) {
            showError('Failed to fetch transcription: ' + err.message);
        }
    }

    /** Render the list of SRT segments as pretty cards */
    function _renderSrtSegments(segments, rawSrt) {
        DOM.srtSegmentsView.innerHTML = '';

        if (!segments || segments.length === 0) {
            // Fall back to raw display if no structured segments
            if (rawSrt) {
                DOM.srtSegmentsView.innerHTML = `<div class="srt-fallback-note">No structured segments available — showing raw SRT.</div>`;
                DOM.srtRawOutput.classList.remove('hidden');
                DOM.srtSegmentsView.classList.add('hidden');
            } else {
                DOM.srtSegmentsView.innerHTML = '<div class="srt-error">No transcription data.</div>';
            }
            return;
        }

        segments.forEach((seg) => {
            // Build timestamp string
            let startTs, endTs;
            if (seg.start_ts && seg.end_ts) {
                // Already formatted strings from disk parse
                startTs = seg.start_ts;
                endTs = seg.end_ts;
            } else {
                // Numeric seconds from live transcription
                startTs = secondsToSrtTime(seg.start);
                endTs = secondsToSrtTime(seg.end);
            }

            const card = document.createElement('div');
            card.className = 'srt-segment-card';
            card.innerHTML = `
                <div class="srt-seg-index">${seg.index}</div>
                <div class="srt-seg-body">
                    <div class="srt-seg-timestamps">
                        <span class="srt-ts-start">${startTs}</span>
                        <span class="srt-ts-arrow">→</span>
                        <span class="srt-ts-end">${endTs}</span>
                    </div>
                    <div class="srt-seg-text">${escapeHtml(seg.text)}</div>
                </div>
            `;
            DOM.srtSegmentsView.appendChild(card);
        });
    }

    /** Convert float seconds to SRT timestamp string HH:MM:SS,mmm */
    function secondsToSrtTime(s) {
        s = Math.max(0, s || 0);
        const millis = Math.round((s % 1) * 1000);
        const totalInt = Math.floor(s);
        const sec = totalInt % 60;
        const minute = Math.floor(totalInt / 60) % 60;
        const hour = Math.floor(totalInt / 3600);
        return `${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}:${String(sec).padStart(2,'0')},${String(millis).padStart(3,'0')}`;
    }

    function escapeHtml(str) {
        return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    // Copy plain text to clipboard
    DOM.copyTranscriptionBtn.addEventListener('click', async () => {
        const text = _transcriptionData.text || DOM.srtRawOutput.value;
        if (!text) return;

        try {
            await navigator.clipboard.writeText(text);
            DOM.copyTranscriptionBtn.innerHTML = '✅ Copied!';
            setTimeout(() => {
                DOM.copyTranscriptionBtn.innerHTML = '📋 Copy Text';
            }, 2000);
        } catch (err) {
            // Fallback for older browsers
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            DOM.copyTranscriptionBtn.innerHTML = '✅ Copied!';
            setTimeout(() => {
                DOM.copyTranscriptionBtn.innerHTML = '📋 Copy Text';
            }, 2000);
        }
    });

    // Download as .srt — prefer server file, fall back to client-side blob
    DOM.downloadSrtBtn.addEventListener('click', async () => {
        const jobId = state.currentJobId;
        const srtContent = _transcriptionData.srt;
        if (!srtContent && !jobId) return;

        // Try server download first
        if (jobId) {
            try {
                const res = await fetch(`/api/transcription/${jobId}/download`);
                if (res.ok) {
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `transcription_${jobId}.srt`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                    return;
                }
            } catch (e) { /* fall through to client-side */ }
        }

        // Client-side fallback
        if (srtContent) {
            const blob = new Blob([srtContent], { type: 'text/plain;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `transcription_${jobId || 'output'}.srt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
    });

    // Toggle raw SRT view
    DOM.toggleRawBtn.addEventListener('click', () => {
        _rawSrtVisible = !_rawSrtVisible;
        DOM.srtSegmentsView.classList.toggle('hidden', _rawSrtVisible);
        DOM.srtRawOutput.classList.toggle('hidden', !_rawSrtVisible);
        DOM.toggleRawBtn.classList.toggle('active', _rawSrtVisible);
        DOM.toggleRawBtn.innerHTML = _rawSrtVisible
            ? '<span>🎬</span> Preview'
            : '<span>&lt;/&gt;</span> Raw SRT';
    });

    // Transcribe another
    DOM.newTranscribeBtn.addEventListener('click', () => {
        cleanupAndReset();
        // Switch back to transcribe mode
        setInputMode('transcribe');
    });

    // Load voices on page load
    loadVoices();

})();
